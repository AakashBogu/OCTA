"""Multi-monitor geometry and display adaptation for OCTA (Phase 8.2).

Provides reliable monitor discovery, coordinate mapping across displays with different
resolutions and DPI scaling, and safe handling of monitors placed left of or above the
primary display (negative virtual desktop coordinates).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication, QScreen

from octa.intelligence.capture import CaptureRegion
from octa.intelligence.overlay import OverlayBounds

logger = logging.getLogger("octa.intelligence.display")


@dataclass(frozen=True)
class DisplayInfo:
    """Strongly-typed representation of a connected display monitor.

    Coordinates (x, y) represent virtual desktop position in logical pixels and may
    be negative when monitors are positioned to the left of or above the primary display.
    """

    index: int
    name: str
    x: int
    y: int
    width: int
    height: int
    device_pixel_ratio: float = 1.0
    is_primary: bool = False
    available_x: int = 0
    available_y: int = 0
    available_width: int = 0
    available_height: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise ValueError(
                f"DisplayInfo index must be a non-negative integer, got {self.index!r}."
            )
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("DisplayInfo name must be a non-empty string.")
        if not isinstance(self.x, int) or isinstance(self.x, bool):
            raise ValueError(f"DisplayInfo x coordinate must be an integer, got {self.x!r}.")
        if not isinstance(self.y, int) or isinstance(self.y, bool):
            raise ValueError(f"DisplayInfo y coordinate must be an integer, got {self.y!r}.")
        if not isinstance(self.width, int) or isinstance(self.width, bool) or self.width <= 0:
            raise ValueError(f"DisplayInfo width must be a positive integer, got {self.width!r}.")
        if not isinstance(self.height, int) or isinstance(self.height, bool) or self.height <= 0:
            raise ValueError(f"DisplayInfo height must be a positive integer, got {self.height!r}.")
        if not isinstance(self.device_pixel_ratio, (int, float)) or self.device_pixel_ratio <= 0.0:
            raise ValueError(
                f"DisplayInfo device_pixel_ratio must be positive, got {self.device_pixel_ratio!r}."
            )

        # Default available geometry to full geometry if not provided
        if self.available_width <= 0 or self.available_height <= 0:
            object.__setattr__(self, "available_x", self.x)
            object.__setattr__(self, "available_y", self.y)
            object.__setattr__(self, "available_width", self.width)
            object.__setattr__(self, "available_height", self.height)

    @property
    def physical_width(self) -> int:
        """Physical device pixels width."""
        return max(1, round(self.width * self.device_pixel_ratio))

    @property
    def physical_height(self) -> int:
        """Physical device pixels height."""
        return max(1, round(self.height * self.device_pixel_ratio))

    @property
    def geometry(self) -> tuple[int, int, int, int]:
        """Virtual desktop geometry tuple (x, y, width, height)."""
        return self.x, self.y, self.width, self.height

    @property
    def available_geometry(self) -> tuple[int, int, int, int]:
        """Usable geometry tuple (available_x, available_y, available_width, available_height)."""
        return self.available_x, self.available_y, self.available_width, self.available_height

    @property
    def display_label(self) -> str:
        """Formatted human-readable monitor descriptor."""
        primary_tag = " (Primary)" if self.is_primary else ""
        dpr_tag = f" @ {int(round(self.device_pixel_ratio * 100))}%"
        pos_tag = f" [{self.x:+d}, {self.y:+d}]" if (self.x != 0 or self.y != 0) else ""
        lbl = f"Display {self.index}: {self.name} — {self.width}x{self.height}"
        return f"{lbl}{dpr_tag}{pos_tag}{primary_tag}"

    def contains_global_point(self, gx: int, gy: int) -> bool:
        """Check whether global virtual desktop point (gx, gy) falls inside this display."""
        return self.x <= gx < (self.x + self.width) and self.y <= gy < (self.y + self.height)

    def contains_local_point(self, lx: int, ly: int) -> bool:
        """Check whether monitor-local point (lx, ly) falls inside this display."""
        return 0 <= lx < self.width and 0 <= ly < self.height

    def global_to_local(self, gx: int, gy: int) -> tuple[int, int]:
        """Transform virtual desktop coordinates to monitor-local coordinates."""
        return gx - self.x, gy - self.y

    def local_to_global(self, lx: int, ly: int) -> tuple[int, int]:
        """Transform monitor-local coordinates to virtual desktop coordinates."""
        return lx + self.x, ly + self.y

    def global_rect_to_local(self, region: CaptureRegion) -> CaptureRegion:
        """Transform a global virtual desktop CaptureRegion to monitor-local coordinates."""
        return CaptureRegion(
            x=region.x - self.x,
            y=region.y - self.y,
            width=region.width,
            height=region.height,
            allow_negative=False,
        )

    def local_rect_to_global(self, region: CaptureRegion) -> CaptureRegion:
        """Transform a monitor-local CaptureRegion to global virtual desktop coordinates."""
        return CaptureRegion(
            x=region.x + self.x,
            y=region.y + self.y,
            width=region.width,
            height=region.height,
            allow_negative=(self.x < 0 or self.y < 0),
        )

    def scale_logical_to_physical(self, x: float, y: float) -> tuple[float, float]:
        """Scale logical coordinate values to physical device pixel coordinates."""
        return x * self.device_pixel_ratio, y * self.device_pixel_ratio

    def scale_physical_to_logical(self, x: float, y: float) -> tuple[float, float]:
        """Scale physical device pixel values to logical coordinate values."""
        return x / self.device_pixel_ratio, y / self.device_pixel_ratio

    def to_overlay_bounds(self) -> OverlayBounds:
        """Construct matching OverlayBounds for positioning the visual guidance overlay."""
        return OverlayBounds(x=self.x, y=self.y, width=self.width, height=self.height)

    def to_capture_region(self) -> CaptureRegion:
        """Construct matching CaptureRegion covering this entire monitor."""
        return CaptureRegion(
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
            allow_negative=(self.x < 0 or self.y < 0),
        )

    @classmethod
    def from_qscreen(cls, screen: QScreen, index: int, is_primary: bool = False) -> DisplayInfo:
        """Create a DisplayInfo from a live PySide6 QScreen object."""
        geom = screen.geometry()
        avail = screen.availableGeometry()
        return cls(
            index=index,
            name=screen.name() or f"Display {index}",
            x=geom.x(),
            y=geom.y(),
            width=max(1, geom.width()),
            height=max(1, geom.height()),
            device_pixel_ratio=float(screen.devicePixelRatio()),
            is_primary=is_primary,
            available_x=avail.x(),
            available_y=avail.y(),
            available_width=max(1, avail.width()),
            available_height=max(1, avail.height()),
        )


type DisplayProvider = Callable[[], Sequence[DisplayInfo]]


class DisplayManager(QObject):
    """Discovers, tracks, and manages displays across single and multi-monitor setups.

    Supports custom display providers for deterministic testing of arbitrary topologies
    (e.g., negative offsets, different DPR, disconnects, resolution changes) without
    requiring physical multi-monitor hardware.
    """

    displays_changed = Signal(tuple)  # tuple[DisplayInfo, ...]
    active_display_changed = Signal(object)  # DisplayInfo

    def __init__(
        self,
        *,
        display_provider: DisplayProvider | None = None,
        initial_index: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = display_provider
        self._displays: tuple[DisplayInfo, ...] = ()
        self._active_display: DisplayInfo | None = None
        self._hooked_qt = False

        self._query_and_update(initial_index=initial_index, emit_signals=False)
        self._connect_qt_signals_if_live()

    def _connect_qt_signals_if_live(self) -> None:
        if self._provider is not None:
            return
        app = QGuiApplication.instance()
        if app is not None and not self._hooked_qt:
            try:
                app.screenAdded.connect(self._on_qt_screens_changed)
                app.screenRemoved.connect(self._on_qt_screens_changed)
                app.primaryScreenChanged.connect(self._on_qt_screens_changed)
                self._hooked_qt = True
            except (AttributeError, RuntimeError):
                pass

    def _on_qt_screens_changed(self, *_args: Any) -> None:
        self.refresh()

    def _discover_from_qt(self) -> tuple[DisplayInfo, ...]:
        screens = QGuiApplication.screens()
        if not screens:
            primary = QGuiApplication.primaryScreen()
            if primary is not None:
                return (DisplayInfo.from_qscreen(primary, index=0, is_primary=True),)
            # Safe offline fallback if Qt has no screen yet
            return (
                DisplayInfo(
                    index=0,
                    name="Default Screen",
                    x=0,
                    y=0,
                    width=1920,
                    height=1080,
                    device_pixel_ratio=1.0,
                    is_primary=True,
                ),
            )

        primary_screen = QGuiApplication.primaryScreen()
        result: list[DisplayInfo] = []
        for idx, screen in enumerate(screens):
            is_pri = screen is primary_screen or (primary_screen is None and idx == 0)
            result.append(DisplayInfo.from_qscreen(screen, index=idx, is_primary=is_pri))

        # Ensure at least one display is marked primary
        if not any(d.is_primary for d in result) and result:
            first = result[0]
            result[0] = DisplayInfo(
                index=first.index,
                name=first.name,
                x=first.x,
                y=first.y,
                width=first.width,
                height=first.height,
                device_pixel_ratio=first.device_pixel_ratio,
                is_primary=True,
                available_x=first.available_x,
                available_y=first.available_y,
                available_width=first.available_width,
                available_height=first.available_height,
            )
        return tuple(result)

    def _query_and_update(
        self,
        *,
        initial_index: int | None = None,
        emit_signals: bool = True,
    ) -> bool:
        """Query displays, validate active display presence, and update internal state."""
        if self._provider is not None:
            raw_displays = tuple(self._provider())
            if not raw_displays:
                raw_displays = (
                    DisplayInfo(
                        index=0,
                        name="Fallback Display",
                        x=0,
                        y=0,
                        width=1920,
                        height=1080,
                        device_pixel_ratio=1.0,
                        is_primary=True,
                    ),
                )
            displays = raw_displays
        else:
            displays = self._discover_from_qt()

        old_displays = self._displays
        old_active = self._active_display
        self._displays = displays

        # Determine target active display
        new_active: DisplayInfo | None = None
        if initial_index is not None:
            new_active = self.get_display_by_index(initial_index)

        if new_active is None and old_active is not None:
            # Try to retain current active display by matching name or index
            for d in displays:
                if d.name == old_active.name or d.index == old_active.index:
                    new_active = d
                    break

        if new_active is None:
            # Fallback to primary display, or first display
            new_active = self.primary_display

        self._active_display = new_active

        changed = (old_displays != self._displays) or (old_active != self._active_display)
        if changed and emit_signals:
            if old_displays != self._displays:
                self.displays_changed.emit(self._displays)
            if old_active != self._active_display:
                self.active_display_changed.emit(self._active_display)
        return changed

    @property
    def displays(self) -> tuple[DisplayInfo, ...]:
        """Tuple of all currently discovered displays."""
        return self._displays

    @property
    def active_display(self) -> DisplayInfo:
        """Currently active display monitor for capture and overlay operations."""
        if self._active_display is None:
            self._query_and_update(emit_signals=False)
        assert self._active_display is not None
        return self._active_display

    @property
    def active_index(self) -> int:
        """Index of the currently active display."""
        return self.active_display.index

    @property
    def primary_display(self) -> DisplayInfo:
        """Primary system display."""
        for d in self._displays:
            if d.is_primary:
                return d
        if self._displays:
            return self._displays[0]
        return DisplayInfo(
            index=0,
            name="Default Screen",
            x=0,
            y=0,
            width=1920,
            height=1080,
            is_primary=True,
        )

    def get_display_by_index(self, index: int) -> DisplayInfo | None:
        """Return DisplayInfo matching index, or None if out of range."""
        for d in self._displays:
            if d.index == index:
                return d
        return None

    def get_display_by_name(self, name: str) -> DisplayInfo | None:
        """Return DisplayInfo matching monitor name, or None if not found."""
        for d in self._displays:
            if d.name.lower() == name.lower():
                return d
        return None

    def get_display_for_point(self, gx: int, gy: int) -> DisplayInfo | None:
        """Return the display containing virtual desktop coordinate (gx, gy), or None."""
        for d in self._displays:
            if d.contains_global_point(gx, gy):
                return d
        return None

    def select_display(self, index_or_name: int | str) -> bool:
        """Select active display by index or monitor name.

        If target does not exist, logs a warning, retains current selection or falls back
        gracefully to primary without raising an exception.
        """
        target: DisplayInfo | None = None
        if isinstance(index_or_name, int) and not isinstance(index_or_name, bool):
            target = self.get_display_by_index(index_or_name)
        elif isinstance(index_or_name, str):
            target = self.get_display_by_name(index_or_name)

        if target is None:
            logger.warning(
                "Requested display %r not found among %d available displays. Fallback to primary.",
                index_or_name,
                len(self._displays),
            )
            return False

        if target != self._active_display:
            self._active_display = target
            logger.info("Active display switched to: %s", target.display_label)
            self.active_display_changed.emit(target)
        return True

    def refresh(self) -> tuple[DisplayInfo, ...]:
        """Rescan display devices, update active display if disconnected, and notify listeners."""
        self._query_and_update(emit_signals=True)
        return self._displays
