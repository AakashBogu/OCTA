"""Qt visual overlay for rendering validated highlights and arrows.

This widget is strictly passive and click-through: it sets WindowTransparentForInput,
WindowDoesNotAcceptFocus, and WA_TransparentForMouseEvents, and never clicks, types,
or controls any other application.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget

from octa.intelligence.exceptions import InvalidOverlayCoordinatesError
from octa.intelligence.tasks import OverlayElementType, OverlayHint

DEFAULT_HIGHLIGHT_COLOR = "#00E676"
DEFAULT_ARROW_COLOR = "#FFAB00"


def _require_coord_int(value: Any, field_name: str) -> int:
    """Validate that a coordinate or dimension is an integer and not a boolean."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidOverlayCoordinatesError(
            f"Overlay field '{field_name}' must be an integer, got {value!r}."
        )
    return value


def _validate_color(color_str: Any, field_name: str = "color") -> str:
    """Validate that color_str is a non-empty string recognized by QColor."""
    if not isinstance(color_str, str) or not color_str.strip():
        raise InvalidOverlayCoordinatesError(
            f"Overlay field '{field_name}' must be a non-empty color string."
        )
    cleaned = color_str.strip()
    if not QColor.isValidColorName(cleaned):
        raise InvalidOverlayCoordinatesError(
            f"Overlay field '{field_name}' is not a valid color: {cleaned!r}."
        )
    return cleaned


@dataclass(frozen=True)
class OverlayBounds:
    """Screen geometry bounds against which all overlay elements are validated."""

    x: int = 0
    y: int = 0
    width: int = 1920
    height: int = 1080

    def __post_init__(self) -> None:
        _require_coord_int(self.x, "x")
        _require_coord_int(self.y, "y")
        width = _require_coord_int(self.width, "width")
        height = _require_coord_int(self.height, "height")
        if width <= 0 or height <= 0:
            raise InvalidOverlayCoordinatesError(
                f"OverlayBounds width and height must be positive, got {width}x{height}."
            )


@dataclass(frozen=True)
class HighlightSpec:
    """Validated rectangular highlight box specification."""

    x: int
    y: int
    width: int
    height: int
    label: str = ""
    color: str = DEFAULT_HIGHLIGHT_COLOR
    stroke_width: int = 3

    def __post_init__(self) -> None:
        x = _require_coord_int(self.x, "x")
        y = _require_coord_int(self.y, "y")
        width = _require_coord_int(self.width, "width")
        height = _require_coord_int(self.height, "height")
        stroke = _require_coord_int(self.stroke_width, "stroke_width")

        if x < 0 or y < 0:
            raise InvalidOverlayCoordinatesError(
                f"Highlight coordinates must be non-negative, got ({x}, {y})."
            )
        if width <= 0 or height <= 0:
            raise InvalidOverlayCoordinatesError(
                f"Highlight dimensions must be positive, got {width}x{height}."
            )
        if stroke < 1 or stroke > 20:
            raise InvalidOverlayCoordinatesError(
                f"Highlight stroke_width must be between 1 and 20, got {stroke}."
            )
        if not isinstance(self.label, str):
            raise InvalidOverlayCoordinatesError("Highlight label must be a string.")
        object.__setattr__(self, "label", self.label.strip())
        object.__setattr__(self, "color", _validate_color(self.color))

    def validate_against_screen(self, screen: OverlayBounds) -> None:
        """Verify that this highlight box lies completely within the screen bounds."""
        if (
            self.x < screen.x
            or self.y < screen.y
            or (self.x + self.width) > (screen.x + screen.width)
            or (self.y + self.height) > (screen.y + screen.height)
        ):
            raise InvalidOverlayCoordinatesError(
                f"Highlight ({self.x}, {self.y}, {self.width}x{self.height}) exceeds "
                f"screen bounds ({screen.x}, {screen.y}, {screen.width}x{screen.height})."
            )


@dataclass(frozen=True)
class ArrowSpec:
    """Validated directional arrow specification."""

    start_x: int
    start_y: int
    end_x: int
    end_y: int
    label: str = ""
    color: str = DEFAULT_ARROW_COLOR
    stroke_width: int = 3

    def __post_init__(self) -> None:
        sx = _require_coord_int(self.start_x, "start_x")
        sy = _require_coord_int(self.start_y, "start_y")
        ex = _require_coord_int(self.end_x, "end_x")
        ey = _require_coord_int(self.end_y, "end_y")
        stroke = _require_coord_int(self.stroke_width, "stroke_width")

        if sx < 0 or sy < 0 or ex < 0 or ey < 0:
            raise InvalidOverlayCoordinatesError(
                f"Arrow coordinates must be non-negative, got ({sx}, {sy}) -> ({ex}, {ey})."
            )
        if sx == ex and sy == ey:
            raise InvalidOverlayCoordinatesError(
                "Arrow start and end coordinates must not be identical."
            )
        if stroke < 1 or stroke > 20:
            raise InvalidOverlayCoordinatesError(
                f"Arrow stroke_width must be between 1 and 20, got {stroke}."
            )
        if not isinstance(self.label, str):
            raise InvalidOverlayCoordinatesError("Arrow label must be a string.")
        object.__setattr__(self, "label", self.label.strip())
        object.__setattr__(self, "color", _validate_color(self.color))

    def validate_against_screen(self, screen: OverlayBounds) -> None:
        """Verify that both arrow endpoints lie within the screen bounds."""
        max_x = screen.x + screen.width
        max_y = screen.y + screen.height
        if not (
            screen.x <= self.start_x <= max_x
            and screen.y <= self.start_y <= max_y
            and screen.x <= self.end_x <= max_x
            and screen.y <= self.end_y <= max_y
        ):
            raise InvalidOverlayCoordinatesError(
                f"Arrow ({self.start_x}, {self.start_y}) -> ({self.end_x}, {self.end_y}) "
                f"exceeds screen bounds ({screen.x}, {screen.y}, {screen.width}x{screen.height})."
            )


def overlay_hint_to_spec(
    hint: OverlayHint,
    screen: OverlayBounds | None = None,
) -> HighlightSpec | ArrowSpec:
    """Convert a task/verifier OverlayHint into a validated HighlightSpec or ArrowSpec."""
    if not isinstance(hint, OverlayHint):
        raise InvalidOverlayCoordinatesError("Expected an OverlayHint instance.")

    if hint.kind == OverlayElementType.HIGHLIGHT:
        if hint.bounds is None:
            raise InvalidOverlayCoordinatesError("Highlight OverlayHint is missing bounds.")
        spec: HighlightSpec | ArrowSpec = HighlightSpec(
            x=hint.bounds.x,
            y=hint.bounds.y,
            width=hint.bounds.width,
            height=hint.bounds.height,
            label=hint.label,
            color=hint.color or DEFAULT_HIGHLIGHT_COLOR,
        )
    elif hint.kind == OverlayElementType.ARROW:
        if hint.start_x is None or hint.start_y is None or hint.end_x is None or hint.end_y is None:
            raise InvalidOverlayCoordinatesError("Arrow OverlayHint is missing coordinates.")
        spec = ArrowSpec(
            start_x=hint.start_x,
            start_y=hint.start_y,
            end_x=hint.end_x,
            end_y=hint.end_y,
            label=hint.label,
            color=hint.color or DEFAULT_ARROW_COLOR,
        )
    else:
        raise InvalidOverlayCoordinatesError(f"Unsupported OverlayHint kind: {hint.kind!r}")

    if screen is not None:
        spec.validate_against_screen(screen)
    return spec


def _resolve_default_screen_bounds() -> OverlayBounds:
    """Resolve primary screen bounds from Qt, falling back to 1920x1080 if unavailable."""
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        geom = screen.geometry()
        if geom.width() > 0 and geom.height() > 0:
            return OverlayBounds(
                x=max(0, geom.x()),
                y=max(0, geom.y()),
                width=geom.width(),
                height=geom.height(),
            )
    return OverlayBounds(x=0, y=0, width=1920, height=1080)


class GuidanceOverlayWidget(QWidget):
    """Non-interactive, click-through Qt visual overlay for highlights and arrows.

    Guaranteed passive: never synthesizes clicks, keystrokes, or window focus changes.
    """

    def __init__(
        self,
        screen_bounds: OverlayBounds | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._screen_bounds = (
            screen_bounds if screen_bounds is not None else _resolve_default_screen_bounds()
        )
        self._highlights: list[HighlightSpec] = []
        self._arrows: list[ArrowSpec] = []

        # Configure click-through, non-focusable, transparent window flags and attributes
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._apply_geometry()

    def _apply_geometry(self) -> None:
        self.setGeometry(
            self._screen_bounds.x,
            self._screen_bounds.y,
            self._screen_bounds.width,
            self._screen_bounds.height,
        )

    @property
    def screen_bounds(self) -> OverlayBounds:
        """Current screen bounds enforced for coordinate validation."""
        return self._screen_bounds

    def set_screen_bounds(self, screen_bounds: OverlayBounds) -> None:
        """Update screen bounds and re-validate all active annotations."""
        if not isinstance(screen_bounds, OverlayBounds):
            raise InvalidOverlayCoordinatesError("Expected an OverlayBounds instance.")
        for h in self._highlights:
            h.validate_against_screen(screen_bounds)
        for a in self._arrows:
            a.validate_against_screen(screen_bounds)
        self._screen_bounds = screen_bounds
        self._apply_geometry()
        self.update()

    @property
    def highlights(self) -> tuple[HighlightSpec, ...]:
        """Active validated highlight boxes."""
        return tuple(self._highlights)

    @property
    def arrows(self) -> tuple[ArrowSpec, ...]:
        """Active validated directional arrows."""
        return tuple(self._arrows)

    def is_interactive(self) -> bool:
        """Always False: this overlay never clicks, types, or accepts user input."""
        return False

    def is_click_through(self) -> bool:
        """Verify that mouse-transparent and non-focusable attributes remain active."""
        flags = self.windowFlags()
        has_transparent_flag = bool(flags & Qt.WindowType.WindowTransparentForInput)
        has_no_focus_flag = bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus)
        has_mouse_attr = self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return (
            has_transparent_flag
            and has_no_focus_flag
            and has_mouse_attr
            and self.focusPolicy() == Qt.FocusPolicy.NoFocus
        )

    def add_highlight(self, spec: HighlightSpec) -> None:
        """Validate and add a highlight box to the overlay."""
        if not isinstance(spec, HighlightSpec):
            raise InvalidOverlayCoordinatesError("Expected a HighlightSpec instance.")
        spec.validate_against_screen(self._screen_bounds)
        self._highlights.append(spec)
        self.update()

    def add_arrow(self, spec: ArrowSpec) -> None:
        """Validate and add a directional arrow to the overlay."""
        if not isinstance(spec, ArrowSpec):
            raise InvalidOverlayCoordinatesError("Expected an ArrowSpec instance.")
        spec.validate_against_screen(self._screen_bounds)
        self._arrows.append(spec)
        self.update()

    def set_annotations(
        self,
        highlights: Sequence[HighlightSpec] = (),
        arrows: Sequence[ArrowSpec] = (),
    ) -> None:
        """Atomically validate and replace all highlights and arrows."""
        validated_highlights: list[HighlightSpec] = []
        for h in highlights:
            if not isinstance(h, HighlightSpec):
                raise InvalidOverlayCoordinatesError("All highlights must be HighlightSpec.")
            h.validate_against_screen(self._screen_bounds)
            validated_highlights.append(h)

        validated_arrows: list[ArrowSpec] = []
        for a in arrows:
            if not isinstance(a, ArrowSpec):
                raise InvalidOverlayCoordinatesError("All arrows must be ArrowSpec.")
            a.validate_against_screen(self._screen_bounds)
            validated_arrows.append(a)

        if self._highlights == validated_highlights and self._arrows == validated_arrows:
            return

        self._highlights = validated_highlights
        self._arrows = validated_arrows
        self.update()

    def apply_hints(self, hints: Sequence[OverlayHint]) -> None:
        """Convert and atomically apply a sequence of OverlayHint items."""
        highlights: list[HighlightSpec] = []
        arrows: list[ArrowSpec] = []
        for hint in hints:
            spec = overlay_hint_to_spec(hint, screen=self._screen_bounds)
            if isinstance(spec, HighlightSpec):
                highlights.append(spec)
            else:
                arrows.append(spec)
        if self._highlights == highlights and self._arrows == arrows:
            return
        self._highlights = highlights
        self._arrows = arrows
        self.update()

    def clear_annotations(self) -> None:
        """Remove all highlights and arrows from the overlay."""
        if not self._highlights and not self._arrows:
            return
        self._highlights.clear()
        self._arrows.clear()
        self.update()

    def render_to_image(self) -> QImage:
        """Render the current overlay annotations onto a transparent ARGB32 QImage."""
        image = QImage(
            self._screen_bounds.width,
            self._screen_bounds.height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            self._paint_annotations(painter)
        finally:
            painter.end()
        return image

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Paint active highlights and arrows onto the translucent overlay surface."""
        painter = QPainter(self)
        try:
            self._paint_annotations(painter)
        finally:
            painter.end()

    def _paint_annotations(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        origin_x = self._screen_bounds.x
        origin_y = self._screen_bounds.y

        label_font = QFont()
        label_font.setPointSize(9)
        label_font.setBold(True)
        painter.setFont(label_font)

        # 1. Draw highlights
        for h in self._highlights:
            base_color = QColor(h.color)
            fill_color = QColor(base_color)
            fill_color.setAlpha(40)

            pen = QPen(base_color, float(h.stroke_width))
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QBrush(fill_color))

            rect = QRectF(
                float(h.x - origin_x),
                float(h.y - origin_y),
                float(h.width),
                float(h.height),
            )
            painter.drawRoundedRect(rect, 6.0, 6.0)

            if h.label:
                self._draw_label(
                    painter,
                    h.label,
                    float(h.x - origin_x),
                    max(4.0, float(h.y - origin_y) - 22.0),
                    base_color,
                )

        # 2. Draw arrows
        for a in self._arrows:
            base_color = QColor(a.color)
            pen = QPen(base_color, float(a.stroke_width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QBrush(base_color))

            sx = float(a.start_x - origin_x)
            sy = float(a.start_y - origin_y)
            ex = float(a.end_x - origin_x)
            ey = float(a.end_y - origin_y)

            start_pt = QPointF(sx, sy)
            end_pt = QPointF(ex, ey)
            painter.drawLine(start_pt, end_pt)

            # Calculate arrowhead triangle
            angle = math.atan2(ey - sy, ex - sx)
            head_len = max(10.0, float(a.stroke_width) * 4.0)
            head_angle = math.pi / 6.0  # 30 degrees

            p1 = QPointF(
                ex - head_len * math.cos(angle - head_angle),
                ey - head_len * math.sin(angle - head_angle),
            )
            p2 = QPointF(
                ex - head_len * math.cos(angle + head_angle),
                ey - head_len * math.sin(angle + head_angle),
            )
            arrowhead = QPolygonF([end_pt, p1, p2])
            painter.drawPolygon(arrowhead)

            if a.label:
                self._draw_label(painter, a.label, sx, max(4.0, sy - 22.0), base_color)

    @staticmethod
    def _draw_label(
        painter: QPainter,
        text: str,
        x: float,
        y: float,
        accent_color: QColor,
    ) -> None:
        metrics = painter.fontMetrics()
        text_w = float(metrics.horizontalAdvance(text)) + 12.0
        text_h = float(metrics.height()) + 6.0
        bg_rect = QRectF(x, y, text_w, text_h)

        bg_color = QColor(20, 24, 32, 220)
        painter.save()
        painter.setPen(QPen(accent_color, 1.5))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(bg_rect, 4.0, 4.0)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
