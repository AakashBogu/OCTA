"""Visual region & directional cue authoring overlay for OCTA (Phase 10.1).

Provides an interactive on-screen authoring overlay allowing tutorial authors to visually
select highlight bounding boxes (HighlightSpec) and draw directional guidance arrows (ArrowSpec).
Includes multi-monitor adaptation, negative virtual desktop coordinate support, logical-to-physical
DPI scaling conversions, fine-tuning controls, and learner-accurate accessibility previews.

Guaranteed privacy-safe:
- Never captures, records, or transmits screen pixels.
- Never synthesizes clicks, keystrokes, or remote automated input.
- Immediately dismisses and restores normal desktop interaction on confirmation, cancel, or Escape.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from octa.intelligence.display import DisplayInfo, DisplayManager
from octa.intelligence.exceptions import InvalidOverlayCoordinatesError
from octa.intelligence.overlay import (
    DEFAULT_ARROW_COLOR,
    DEFAULT_HIGHLIGHT_COLOR,
    ArrowSpec,
    HighlightSpec,
    OverlayBounds,
)


class AuthoringMode(enum.Enum):
    """Active visual authoring mode."""

    HIGHLIGHT = "highlight"
    ARROW = "arrow"


# ──────────────────────────────────────────
# Coordinate Conversion Helpers
# ──────────────────────────────────────────


def logical_to_physical(x: int, y: int, dpr: float) -> tuple[int, int]:
    """Convert logical pixel coordinates to physical device pixels."""
    ratio = max(0.1, float(dpr))
    return round(x * ratio), round(y * ratio)


def physical_to_logical(px: int, py: int, dpr: float) -> tuple[int, int]:
    """Convert physical device pixel coordinates to logical pixels."""
    ratio = max(0.1, float(dpr))
    return round(px / ratio), round(py / ratio)


def rect_logical_to_physical(
    rect: tuple[int, int, int, int], dpr: float
) -> tuple[int, int, int, int]:
    """Convert a logical rect (x, y, w, h) to physical device pixels."""
    x, y, w, h = rect
    ratio = max(0.1, float(dpr))
    return round(x * ratio), round(y * ratio), max(1, round(w * ratio)), max(1, round(h * ratio))


def rect_physical_to_logical(
    rect: tuple[int, int, int, int], dpr: float
) -> tuple[int, int, int, int]:
    """Convert a physical rect (px, py, pw, ph) to logical pixels."""
    px, py, pw, ph = rect
    ratio = max(0.1, float(dpr))
    return (
        round(px / ratio),
        round(py / ratio),
        max(1, round(pw / ratio)),
        max(1, round(ph / ratio)),
    )


@dataclass(frozen=True)
class AuthoringResult:
    """Strongly-typed outcome of an authoring selection session."""

    mode: AuthoringMode
    display: DisplayInfo
    highlight_spec: HighlightSpec | None = None
    arrow_spec: ArrowSpec | None = None
    is_cancelled: bool = False
    logical_bounds: tuple[int, int, int, int] | None = None
    physical_bounds: tuple[int, int, int, int] | None = None


def _resolve_default_display() -> DisplayInfo:
    """Resolve primary display information from Qt or fallback to 1920x1080 default."""
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        geom = screen.geometry()
        dpr = float(screen.devicePixelRatio())
        avail = screen.availableGeometry()
        return DisplayInfo(
            index=0,
            name=screen.name() or "Primary",
            x=geom.x(),
            y=geom.y(),
            width=max(1, geom.width()),
            height=max(1, geom.height()),
            device_pixel_ratio=max(0.5, dpr),
            is_primary=True,
            available_x=avail.x(),
            available_y=avail.y(),
            available_width=max(1, avail.width()),
            available_height=max(1, avail.height()),
        )
    return DisplayInfo(
        index=0,
        name="DefaultDisplay",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.0,
        is_primary=True,
    )


# ──────────────────────────────────────────
# Interactive Authoring Overlay Widget
# ──────────────────────────────────────────


class AuthoringOverlayWidget(QWidget):
    """Interactive Qt overlay surface for visually defining tutorial cues.

    Captures author pointer coordinates within its own canvas to construct
    HighlightSpec and ArrowSpec models without recording the screen or
    synthesizing external input.
    """

    highlight_selected = Signal(HighlightSpec)
    arrow_selected = Signal(ArrowSpec)
    confirmed = Signal(object)  # HighlightSpec or ArrowSpec
    cancelled = Signal()
    mode_changed = Signal(str)

    def __init__(
        self,
        display: DisplayInfo | None = None,
        *,
        mode: AuthoringMode = AuthoringMode.HIGHLIGHT,
        high_contrast: bool = False,
        text_scale: float = 1.0,
        label_text: str = "",
        color: str | None = None,
        stroke_width: int = 3,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._display = display if display is not None else _resolve_default_display()
        self._mode = mode
        self._high_contrast = bool(high_contrast)
        self._text_scale = max(0.5, min(3.0, float(text_scale)))
        self._label_text = label_text.strip()
        self._stroke_width = max(1, min(20, int(stroke_width)))
        self._color = color or (
            DEFAULT_HIGHLIGHT_COLOR
            if self._mode == AuthoringMode.HIGHLIGHT
            else DEFAULT_ARROW_COLOR
        )

        # Drawing state
        self._is_dragging = False
        self._drag_start_local: QPoint | None = None
        self._drag_current_local: QPoint | None = None
        self._committed_highlight: HighlightSpec | None = None
        self._committed_arrow: ArrowSpec | None = None
        self._is_cancelled = False

        # Configure window: frameless, stay on top, tool window, strong focus for Esc
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._apply_geometry()
        self._build_hud()

    def _apply_geometry(self) -> None:
        """Position overlay exactly on the target monitor's logical geometry."""
        self.setGeometry(
            self._display.x,
            self._display.y,
            self._display.width,
            self._display.height,
        )

    @property
    def display(self) -> DisplayInfo:
        """Currently targeted display."""
        return self._display

    def set_display(self, display: DisplayInfo) -> None:
        """Update targeted display and reposition overlay."""
        if not isinstance(display, DisplayInfo):
            raise ValueError(f"Expected DisplayInfo, got {type(display).__name__}.")
        self._display = display
        self._apply_geometry()
        self.update()

    @property
    def mode(self) -> AuthoringMode:
        """Current authoring mode (HIGHLIGHT or ARROW)."""
        return self._mode

    def set_mode(self, mode: AuthoringMode) -> None:
        """Switch authoring mode between Highlight and Arrow."""
        if not isinstance(mode, AuthoringMode):
            raise ValueError(f"Expected AuthoringMode enum, got {mode!r}.")
        if self._mode != mode:
            self._mode = mode
            if self._mode == AuthoringMode.HIGHLIGHT and self._color == DEFAULT_ARROW_COLOR:
                self._color = DEFAULT_HIGHLIGHT_COLOR
            elif self._mode == AuthoringMode.ARROW and self._color == DEFAULT_HIGHLIGHT_COLOR:
                self._color = DEFAULT_ARROW_COLOR
            self._sync_hud_to_mode()
            self.mode_changed.emit(self._mode.value)
            self.update()

    @property
    def high_contrast(self) -> bool:
        """Whether high-contrast accessibility preview is enabled."""
        return self._high_contrast

    def set_high_contrast(self, enabled: bool) -> None:
        """Toggle learner-matching high contrast rendering."""
        self._high_contrast = bool(enabled)
        if hasattr(self, "_chk_contrast"):
            self._chk_contrast.blockSignals(True)
            self._chk_contrast.setChecked(self._high_contrast)
            self._chk_contrast.blockSignals(False)
        self.update()

    @property
    def text_scale(self) -> float:
        """Current text scale multiplier."""
        return self._text_scale

    def set_text_scale(self, scale: float) -> None:
        """Set preview text scaling multiplier."""
        self._text_scale = max(0.5, min(3.0, float(scale)))
        self.update()

    @property
    def label_text(self) -> str:
        """Label attached to the authoring cue."""
        return self._label_text

    def set_label_text(self, text: str) -> None:
        """Update label text."""
        self._label_text = text.strip()
        if hasattr(self, "_txt_label"):
            self._txt_label.blockSignals(True)
            self._txt_label.setText(self._label_text)
            self._txt_label.blockSignals(False)
        self.update()

    @property
    def current_highlight_spec(self) -> HighlightSpec | None:
        """Currently drafted or committed HighlightSpec."""
        return self._committed_highlight

    @property
    def current_arrow_spec(self) -> ArrowSpec | None:
        """Currently drafted or committed ArrowSpec."""
        return self._committed_arrow

    @property
    def active_spec(self) -> HighlightSpec | ArrowSpec | None:
        """Active spec matching the current authoring mode."""
        if self._mode == AuthoringMode.HIGHLIGHT:
            return self._committed_highlight
        return self._committed_arrow

    # ──────────────────────────────────────────
    # Floating HUD Toolbar Construction
    # ──────────────────────────────────────────

    def _build_hud(self) -> None:
        """Build the non-intrusive floating control HUD."""
        self._hud_frame = QFrame(self)
        self._hud_frame.setObjectName("authoring_hud")
        self._hud_frame.setStyleSheet(
            "#authoring_hud {"
            "  background-color: rgba(15, 23, 42, 235);"
            "  border: 1px solid #0EA5E9;"
            "  border-radius: 8px;"
            "  color: #F8FAFC;"
            "}"
            "QLabel { color: #E2E8F0; font-size: 11px; font-weight: 500; }"
            "QSpinBox {"
            "  background: #1E293B; color: #FFFFFF; border: 1px solid #475569;"
            "  border-radius: 4px; padding: 2px 4px; min-width: 55px;"
            "}"
            "QLineEdit {"
            "  background: #1E293B; color: #FFFFFF; border: 1px solid #475569;"
            "  border-radius: 4px; padding: 2px 6px; min-width: 100px;"
            "}"
            "QPushButton {"
            "  background: #0284C7; color: #FFFFFF; border: none;"
            "  border-radius: 4px; padding: 4px 8px; font-weight: bold; font-size: 11px;"
            "}"
            "QPushButton:hover { background: #0369A1; }"
            "QPushButton#btn_cancel { background: #475569; }"
            "QPushButton#btn_cancel:hover { background: #334155; }"
        )

        hud_layout = QVBoxLayout(self._hud_frame)
        hud_layout.setContentsMargins(10, 6, 10, 6)
        hud_layout.setSpacing(4)

        # Top row: Mode selector, contrast toggle, help info
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        lbl_mode = QLabel("Mode:", self._hud_frame)
        self._combo_mode = QComboBox(self._hud_frame)
        self._combo_mode.addItems(["Highlight Box", "Directional Arrow"])
        self._combo_mode.setCurrentIndex(0 if self._mode == AuthoringMode.HIGHLIGHT else 1)
        self._combo_mode.currentIndexChanged.connect(self._on_hud_mode_changed)

        self._chk_contrast = QCheckBox("High Contrast", self._hud_frame)
        self._chk_contrast.setChecked(self._high_contrast)
        self._chk_contrast.toggled.connect(self.set_high_contrast)

        self._lbl_monitor_info = QLabel(
            f"Monitor {self._display.index}: {self._display.width}x{self._display.height} "
            f"({int(round(self._display.device_pixel_ratio * 100))}%)",
            self._hud_frame,
        )
        self._lbl_monitor_info.setStyleSheet("color: #94A3B8; font-size: 10px;")

        row1.addWidget(lbl_mode)
        row1.addWidget(self._combo_mode)
        row1.addWidget(self._chk_contrast)
        row1.addStretch()
        row1.addWidget(self._lbl_monitor_info)
        hud_layout.addLayout(row1)

        # Middle row: Coordinate spinboxes & label
        self._row_coords = QHBoxLayout()
        self._row_coords.setSpacing(6)

        self._lbl_c1 = QLabel("X:", self._hud_frame)
        self._spin_c1 = QSpinBox(self._hud_frame)
        self._lbl_c2 = QLabel("Y:", self._hud_frame)
        self._spin_c2 = QSpinBox(self._hud_frame)
        self._lbl_c3 = QLabel("W:", self._hud_frame)
        self._spin_c3 = QSpinBox(self._hud_frame)
        self._lbl_c4 = QLabel("H:", self._hud_frame)
        self._spin_c4 = QSpinBox(self._hud_frame)

        for sp in (self._spin_c1, self._spin_c2, self._spin_c3, self._spin_c4):
            sp.setRange(-10000, 20000)
            sp.valueChanged.connect(self._on_spinbox_adjusted)

        lbl_lbl = QLabel("Label:", self._hud_frame)
        self._txt_label = QLineEdit(self._label_text, self._hud_frame)
        self._txt_label.setPlaceholderText("Optional step cue label")
        self._txt_label.textChanged.connect(self._on_label_edited)

        self._row_coords.addWidget(self._lbl_c1)
        self._row_coords.addWidget(self._spin_c1)
        self._row_coords.addWidget(self._lbl_c2)
        self._row_coords.addWidget(self._spin_c2)
        self._row_coords.addWidget(self._lbl_c3)
        self._row_coords.addWidget(self._spin_c3)
        self._row_coords.addWidget(self._lbl_c4)
        self._row_coords.addWidget(self._spin_c4)
        self._row_coords.addWidget(lbl_lbl)
        self._row_coords.addWidget(self._txt_label)
        hud_layout.addLayout(self._row_coords)

        # Bottom row: Physical readout & Action buttons
        row3 = QHBoxLayout()
        row3.setSpacing(8)

        self._lbl_physical_readout = QLabel("Physical Pixels: --", self._hud_frame)
        self._lbl_physical_readout.setStyleSheet("color: #38BDF8; font-size: 10px;")

        self._btn_confirm = QPushButton("✔ Confirm (Enter)", self._hud_frame)
        self._btn_confirm.clicked.connect(self.confirm)

        self._btn_cancel = QPushButton("✖ Cancel (Esc)", self._hud_frame)
        self._btn_cancel.setObjectName("btn_cancel")
        self._btn_cancel.clicked.connect(self.cancel)

        row3.addWidget(self._lbl_physical_readout)
        row3.addStretch()
        row3.addWidget(self._btn_confirm)
        row3.addWidget(self._btn_cancel)
        hud_layout.addLayout(row3)

        self._sync_hud_to_mode()
        self._position_hud()

    def _position_hud(self) -> None:
        """Position HUD horizontally centered near top of display."""
        hud_width = 620
        hud_height = 88
        hud_x = max(10, (self._display.width - hud_width) // 2)
        hud_y = 12
        self._hud_frame.setGeometry(hud_x, hud_y, hud_width, hud_height)

    def _sync_hud_to_mode(self) -> None:
        """Update coordinate labels and limits to match the current mode."""
        self._combo_mode.blockSignals(True)
        self._combo_mode.setCurrentIndex(0 if self._mode == AuthoringMode.HIGHLIGHT else 1)
        self._combo_mode.blockSignals(False)

        if self._mode == AuthoringMode.HIGHLIGHT:
            self._lbl_c1.setText("X:")
            self._lbl_c2.setText("Y:")
            self._lbl_c3.setText("W:")
            self._lbl_c4.setText("H:")
            self._spin_c3.setMinimum(1)
            self._spin_c4.setMinimum(1)
            if self._committed_highlight:
                self._populate_spinboxes(
                    self._committed_highlight.x,
                    self._committed_highlight.y,
                    self._committed_highlight.width,
                    self._committed_highlight.height,
                )
        else:
            self._lbl_c1.setText("X1:")
            self._lbl_c2.setText("Y1:")
            self._lbl_c3.setText("X2:")
            self._lbl_c4.setText("Y2:")
            self._spin_c3.setMinimum(-10000)
            self._spin_c4.setMinimum(-10000)
            if self._committed_arrow:
                self._populate_spinboxes(
                    self._committed_arrow.start_x,
                    self._committed_arrow.start_y,
                    self._committed_arrow.end_x,
                    self._committed_arrow.end_y,
                )

    def _populate_spinboxes(self, v1: int, v2: int, v3: int, v4: int) -> None:
        for sp in (self._spin_c1, self._spin_c2, self._spin_c3, self._spin_c4):
            sp.blockSignals(True)
        self._spin_c1.setValue(v1)
        self._spin_c2.setValue(v2)
        self._spin_c3.setValue(v3)
        self._spin_c4.setValue(v4)
        for sp in (self._spin_c1, self._spin_c2, self._spin_c3, self._spin_c4):
            sp.blockSignals(False)
        self._update_physical_readout()

    def _update_physical_readout(self) -> None:
        dpr = self._display.device_pixel_ratio
        if self._mode == AuthoringMode.HIGHLIGHT:
            w = self._spin_c3.value()
            h = self._spin_c4.value()
            pw = max(1, round(w * dpr))
            ph = max(1, round(h * dpr))
            self._lbl_physical_readout.setText(f"Physical Size: {pw} × {ph} px (DPI {dpr:.2f}x)")
        else:
            x1 = self._spin_c1.value()
            y1 = self._spin_c2.value()
            x2 = self._spin_c3.value()
            y2 = self._spin_c4.value()
            length = math.hypot(x2 - x1, y2 - y1)
            plen = round(length * dpr)
            self._lbl_physical_readout.setText(f"Physical Length: {plen} px (DPI {dpr:.2f}x)")

    def _on_hud_mode_changed(self, index: int) -> None:
        self.set_mode(AuthoringMode.HIGHLIGHT if index == 0 else AuthoringMode.ARROW)

    def _on_label_edited(self, text: str) -> None:
        self._label_text = text.strip()
        self._rebuild_active_spec_from_spinboxes()
        self.update()

    def _on_spinbox_adjusted(self) -> None:
        self._rebuild_active_spec_from_spinboxes()
        self._update_physical_readout()
        self.update()

    def _rebuild_active_spec_from_spinboxes(self) -> None:
        allow_neg = self._display.x < 0 or self._display.y < 0
        try:
            if self._mode == AuthoringMode.HIGHLIGHT:
                x = self._spin_c1.value()
                y = self._spin_c2.value()
                w = max(1, self._spin_c3.value())
                h = max(1, self._spin_c4.value())
                self._committed_highlight = HighlightSpec(
                    x=x,
                    y=y,
                    width=w,
                    height=h,
                    label=self._label_text,
                    color=self._color,
                    stroke_width=self._stroke_width,
                    allow_negative=allow_neg or (x < 0 or y < 0),
                )
            else:
                x1 = self._spin_c1.value()
                y1 = self._spin_c2.value()
                x2 = self._spin_c3.value()
                y2 = self._spin_c4.value()
                if x1 != x2 or y1 != y2:
                    self._committed_arrow = ArrowSpec(
                        start_x=x1,
                        start_y=y1,
                        end_x=x2,
                        end_y=y2,
                        label=self._label_text,
                        color=self._color,
                        stroke_width=self._stroke_width,
                        allow_negative=allow_neg or (x1 < 0 or y1 < 0 or x2 < 0 or y2 < 0),
                    )
        except InvalidOverlayCoordinatesError:
            pass

    # ──────────────────────────────────────────
    # Mouse Interaction: Click-and-Drag
    # ──────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pt = event.position().toPoint()
            # Check if clicked inside HUD frame
            if self._hud_frame.geometry().contains(pt):
                super().mousePressEvent(event)
                return
            self._is_dragging = True
            self._drag_start_local = pt
            self._drag_current_local = pt
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_dragging and self._drag_start_local is not None:
            self._drag_current_local = event.position().toPoint()
            self._update_spec_from_drag()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self._is_dragging = False
            self._drag_current_local = event.position().toPoint()
            self._update_spec_from_drag()
            self.update()
        super().mouseReleaseEvent(event)

    def _update_spec_from_drag(self) -> None:
        """Convert current drag points from local monitor coordinates to global specs."""
        if self._drag_start_local is None or self._drag_current_local is None:
            return

        p1 = self._drag_start_local
        p2 = self._drag_current_local
        allow_neg = self._display.x < 0 or self._display.y < 0

        if self._mode == AuthoringMode.HIGHLIGHT:
            lx = min(p1.x(), p2.x())
            ly = min(p1.y(), p2.y())
            lw = abs(p2.x() - p1.x())
            lh = abs(p2.y() - p1.y())
            if lw < 2 and lh < 2:
                return

            gx, gy = self._display.local_to_global(lx, ly)
            try:
                spec = HighlightSpec(
                    x=gx,
                    y=gy,
                    width=max(1, lw),
                    height=max(1, lh),
                    label=self._label_text,
                    color=self._color,
                    stroke_width=self._stroke_width,
                    allow_negative=allow_neg or (gx < 0 or gy < 0),
                )
                self._committed_highlight = spec
                self._populate_spinboxes(spec.x, spec.y, spec.width, spec.height)
            except InvalidOverlayCoordinatesError:
                pass

        elif self._mode == AuthoringMode.ARROW:
            if p1.x() == p2.x() and p1.y() == p2.y():
                return

            sx, sy = self._display.local_to_global(p1.x(), p1.y())
            ex, ey = self._display.local_to_global(p2.x(), p2.y())
            try:
                spec_arr = ArrowSpec(
                    start_x=sx,
                    start_y=sy,
                    end_x=ex,
                    end_y=ey,
                    label=self._label_text,
                    color=self._color,
                    stroke_width=self._stroke_width,
                    allow_negative=allow_neg or (sx < 0 or sy < 0 or ex < 0 or ey < 0),
                )
                self._committed_arrow = spec_arr
                self._populate_spinboxes(
                    spec_arr.start_x,
                    spec_arr.start_y,
                    spec_arr.end_x,
                    spec_arr.end_y,
                )
            except InvalidOverlayCoordinatesError:
                pass

    # ──────────────────────────────────────────
    # Rendering & Learner-Facing Previews
    # ──────────────────────────────────────────

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            # 1. Semi-translucent dark scrim over the monitor
            painter.fillRect(self.rect(), QColor(15, 23, 42, 75))

            origin_x = self._display.x
            origin_y = self._display.y

            label_font = QFont()
            label_font.setPointSize(max(8, int(round(9.0 * self._text_scale))))
            label_font.setBold(True)
            painter.setFont(label_font)

            # 2. Render Highlight Preview
            if self._mode == AuthoringMode.HIGHLIGHT and self._committed_highlight is not None:
                h = self._committed_highlight
                rect = QRectF(
                    float(h.x - origin_x),
                    float(h.y - origin_y),
                    float(h.width),
                    float(h.height),
                )
                self._paint_highlight_cue(painter, rect, h)

            # 3. Render Arrow Preview
            elif self._mode == AuthoringMode.ARROW and self._committed_arrow is not None:
                a = self._committed_arrow
                self._paint_arrow_cue(painter, a, origin_x, origin_y)

        finally:
            painter.end()

    def _paint_highlight_cue(self, painter: QPainter, rect: QRectF, h: HighlightSpec) -> None:
        """Paint highlight matching GuidanceOverlayWidget rendering."""
        base_color = QColor(h.color)

        if not self._high_contrast:
            # Standard contrast
            fill_color = QColor(base_color.red(), base_color.green(), base_color.blue(), 38)
            painter.fillRect(rect, fill_color)

            pen = QPen(base_color, float(h.stroke_width))
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            if h.label:
                self._paint_label_pill(painter, rect.x() + 4, rect.y() - 22, h.label, base_color)
        else:
            # High-contrast dual-stroke: outer dark halo + inner vibrant yellow
            halo_color = QColor(0, 0, 0, 240)
            inner_color = QColor("#FFD600")

            halo_width = float(h.stroke_width + 4)
            halo_pen = QPen(halo_color, halo_width)
            halo_pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(halo_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            inner_pen = QPen(inner_color, float(h.stroke_width))
            inner_pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(inner_pen)
            painter.drawRect(rect)

            # Non-color shape indicator: L-shaped corner brackets
            bracket_pen = QPen(inner_color, max(2.0, float(h.stroke_width)))
            painter.setPen(bracket_pen)
            rx, ry, rw, rh = rect.x(), rect.y(), rect.width(), rect.height()
            c_len = min(14.0, min(rw, rh) / 3.0)

            # Top-left corner
            painter.drawLine(QPointF(rx, ry + c_len), QPointF(rx, ry))
            painter.drawLine(QPointF(rx, ry), QPointF(rx + c_len, ry))
            # Top-right corner
            painter.drawLine(QPointF(rx + rw - c_len, ry), QPointF(rx + rw, ry))
            painter.drawLine(QPointF(rx + rw, ry), QPointF(rx + rw, ry + c_len))
            # Bottom-left corner
            painter.drawLine(QPointF(rx, ry + rh - c_len), QPointF(rx, ry + rh))
            painter.drawLine(QPointF(rx, ry + rh), QPointF(rx + c_len, ry + rh))
            # Bottom-right corner
            painter.drawLine(QPointF(rx + rw - c_len, ry + rh), QPointF(rx + rw, ry + rh))
            painter.drawLine(QPointF(rx + rw, ry + rh), QPointF(rx + rw, ry + rh - c_len))

            tag = f"[■] {h.label}".strip() if h.label else "[■]"
            self._paint_label_pill(painter, rx + 4, ry - 24, tag, inner_color, is_hc=True)

    def _paint_arrow_cue(
        self, painter: QPainter, a: ArrowSpec, origin_x: int, origin_y: int
    ) -> None:
        """Paint directional arrow matching GuidanceOverlayWidget rendering."""
        p1 = QPointF(float(a.start_x - origin_x), float(a.start_y - origin_y))
        p2 = QPointF(float(a.end_x - origin_x), float(a.end_y - origin_y))
        base_color = QColor(a.color)

        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        angle = math.atan2(dy, dx)
        head_len = max(16.0, float(a.stroke_width * 4))
        head_angle = math.pi / 6.0

        p_left = QPointF(
            p2.x() - head_len * math.cos(angle - head_angle),
            p2.y() - head_len * math.sin(angle - head_angle),
        )
        p_right = QPointF(
            p2.x() - head_len * math.cos(angle + head_angle),
            p2.y() - head_len * math.sin(angle + head_angle),
        )
        arrowhead = QPolygonF([p2, p_left, p_right])

        if not self._high_contrast:
            pen = QPen(base_color, float(a.stroke_width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(p1, p2)

            painter.setBrush(QBrush(base_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(arrowhead)
            painter.drawEllipse(p1, 4.5, 4.5)

            if a.label:
                mid_x = (p1.x() + p2.x()) / 2.0
                mid_y = (p1.y() + p2.y()) / 2.0
                self._paint_label_pill(painter, mid_x + 6, mid_y - 12, a.label, base_color)
        else:
            halo_color = QColor(0, 0, 0, 240)
            inner_color = QColor("#FFD600")

            # Outer dark halo
            halo_pen = QPen(halo_color, float(a.stroke_width + 5))
            halo_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(halo_pen)
            painter.drawLine(p1, p2)

            # Inner vibrant shaft
            inner_pen = QPen(inner_color, float(a.stroke_width))
            inner_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(inner_pen)
            painter.drawLine(p1, p2)

            # Arrowhead dual stroke
            painter.setPen(QPen(halo_color, 2.0))
            painter.setBrush(QBrush(inner_color))
            painter.drawPolygon(arrowhead)

            # Non-color shape indicator: Target ring 'O' at start
            ring_pen = QPen(inner_color, 2.5)
            painter.setPen(ring_pen)
            painter.setBrush(QBrush(halo_color))
            painter.drawEllipse(p1, 7.0, 7.0)

            tag = f"[▶] {a.label}".strip() if a.label else "[▶]"
            mid_x = (p1.x() + p2.x()) / 2.0
            mid_y = (p1.y() + p2.y()) / 2.0
            self._paint_label_pill(painter, mid_x + 8, mid_y - 16, tag, inner_color, is_hc=True)

    def _paint_label_pill(
        self,
        painter: QPainter,
        x: float,
        y: float,
        text: str,
        accent: QColor,
        is_hc: bool = False,
    ) -> None:
        """Draw an accessible contrast pill behind annotation labels."""
        fm = painter.fontMetrics()
        pad_x = 8.0 * self._text_scale
        pad_y = 3.0 * self._text_scale
        tw = fm.horizontalAdvance(text)
        th = fm.height()

        pill_rect = QRectF(x, y, tw + pad_x * 2, th + pad_y * 2)

        bg_color = QColor(0, 0, 0, 230) if is_hc else QColor(15, 23, 42, 215)
        border_color = accent if is_hc else QColor(base=accent, a=180)

        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1.5 if is_hc else 1.0))
        painter.drawRoundedRect(pill_rect, 4.0, 4.0)

        painter.setPen(QColor("#FFFFFF") if not is_hc else QColor("#FFD600"))
        painter.drawText(
            QRectF(x + pad_x, y + pad_y, tw, th),
            Qt.AlignmentFlag.AlignCenter,
            text,
        )

    # ──────────────────────────────────────────
    # Keyboard & Dismissal Safety
    # ──────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
            event.accept()
        elif key == Qt.Key.Key_H and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.set_mode(AuthoringMode.HIGHLIGHT)
            event.accept()
        elif key == Qt.Key.Key_A and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.set_mode(AuthoringMode.ARROW)
            event.accept()
        elif key == Qt.Key.Key_C and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.set_high_contrast(not self._high_contrast)
            event.accept()
        else:
            super().keyPressEvent(event)

    def confirm(self) -> None:
        """Validate and commit the selected spec, then dismiss overlay."""
        bounds = OverlayBounds(
            x=self._display.x,
            y=self._display.y,
            width=self._display.width,
            height=self._display.height,
        )

        target_spec: HighlightSpec | ArrowSpec | None = None
        if self._mode == AuthoringMode.HIGHLIGHT:
            if self._committed_highlight is not None:
                self._committed_highlight.validate_against_screen(bounds)
                target_spec = self._committed_highlight
                self.highlight_selected.emit(self._committed_highlight)
        else:
            if self._committed_arrow is not None:
                self._committed_arrow.validate_against_screen(bounds)
                target_spec = self._committed_arrow
                self.arrow_selected.emit(self._committed_arrow)

        if target_spec is not None:
            self.confirmed.emit(target_spec)

        self._cleanup_and_close()

    def cancel(self) -> None:
        """Cancel selection session, emit cancelled signal, and close immediately."""
        self._is_cancelled = True
        self.cancelled.emit()
        self._cleanup_and_close()

    def _cleanup_and_close(self) -> None:
        """Immediately release cursor and hide window to prevent blocking desktop input."""
        self._is_dragging = False
        self.hide()
        self.close()

    def closeEvent(self, event: Any) -> None:
        """Ensure overlay is hidden and non-intercepting on close."""
        self._is_dragging = False
        self.hide()
        event.accept()


# ──────────────────────────────────────────
# Companion Visual Cue Authoring Dialog
# ──────────────────────────────────────────


class AuthoringCueDialog(QDialog):
    """Authoring modal for configuring and fine-tuning tutorial guidance cues."""

    def __init__(
        self,
        display_manager: DisplayManager | None = None,
        *,
        mode: AuthoringMode = AuthoringMode.HIGHLIGHT,
        high_contrast: bool = False,
        text_scale: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("OCTA Visual Cue Authoring Studio")
        self.resize(520, 360)

        self._display_manager = display_manager
        self._mode = mode
        self._high_contrast = high_contrast
        self._text_scale = text_scale
        self._overlay_widget: AuthoringOverlayWidget | None = None

        self._created_highlight: HighlightSpec | None = None
        self._created_arrow: ArrowSpec | None = None

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header description
        lbl_intro = QLabel(
            "Configure on-screen guidance cues (highlights or arrows) for tutorial steps. "
            "You can visually select the region on your desktop or fine-tune exact coordinates.",
            self,
        )
        lbl_intro.setWordWrap(True)
        layout.addWidget(lbl_intro)

        # Settings Group Frame
        frame = QFrame(self)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        f_layout = QVBoxLayout(frame)
        f_layout.setSpacing(8)

        # Monitor row
        row_mon = QHBoxLayout()
        row_mon.addWidget(QLabel("Target Monitor:", frame))
        self._combo_monitor = QComboBox(frame)
        self._populate_monitors()
        row_mon.addWidget(self._combo_monitor, stretch=1)
        f_layout.addLayout(row_mon)

        # Mode row
        row_mode = QHBoxLayout()
        row_mode.addWidget(QLabel("Cue Type:", frame))
        self._combo_type = QComboBox(frame)
        self._combo_type.addItems(["Highlight Rectangle", "Directional Arrow"])
        self._combo_type.setCurrentIndex(0 if self._mode == AuthoringMode.HIGHLIGHT else 1)
        self._combo_type.currentIndexChanged.connect(self._on_type_changed)
        row_mode.addWidget(self._combo_type, stretch=1)
        f_layout.addLayout(row_mode)

        # Visual selection button
        self._btn_select_on_screen = QPushButton("🎯 Select Region on Screen...", frame)
        self._btn_select_on_screen.setStyleSheet(
            "font-weight: bold; padding: 6px; background-color: #0284C7; color: white;"
        )
        self._btn_select_on_screen.clicked.connect(self._launch_screen_selector)
        f_layout.addWidget(self._btn_select_on_screen)

        # Coordinates row
        row_coords = QHBoxLayout()
        self._lbl_c1 = QLabel("X:", frame)
        self._sp_c1 = QSpinBox(frame)
        self._lbl_c2 = QLabel("Y:", frame)
        self._sp_c2 = QSpinBox(frame)
        self._lbl_c3 = QLabel("W:", frame)
        self._sp_c3 = QSpinBox(frame)
        self._lbl_c4 = QLabel("H:", frame)
        self._sp_c4 = QSpinBox(frame)

        for sp in (self._sp_c1, self._sp_c2, self._sp_c3, self._sp_c4):
            sp.setRange(-10000, 20000)
            sp.valueChanged.connect(self._update_readout)

        row_coords.addWidget(self._lbl_c1)
        row_coords.addWidget(self._sp_c1)
        row_coords.addWidget(self._lbl_c2)
        row_coords.addWidget(self._sp_c2)
        row_coords.addWidget(self._lbl_c3)
        row_coords.addWidget(self._sp_c3)
        row_coords.addWidget(self._lbl_c4)
        row_coords.addWidget(self._sp_c4)
        f_layout.addLayout(row_coords)

        # Label & High contrast
        row_meta = QHBoxLayout()
        row_meta.addWidget(QLabel("Label:", frame))
        self._txt_label = QLineEdit(frame)
        self._txt_label.setPlaceholderText("e.g. Save Button")
        row_meta.addWidget(self._txt_label, stretch=1)

        self._chk_hc = QCheckBox("High Contrast", frame)
        self._chk_hc.setChecked(self._high_contrast)
        row_meta.addWidget(self._chk_hc)
        f_layout.addLayout(row_meta)

        # Readout label
        self._lbl_info = QLabel("Physical Pixels: --", frame)
        self._lbl_info.setStyleSheet("color: #0369A1; font-size: 11px;")
        f_layout.addWidget(self._lbl_info)

        layout.addWidget(frame)

        # Dialog Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._sync_type_labels()

    def _populate_monitors(self) -> None:
        self._combo_monitor.clear()
        if self._display_manager:
            for d in self._display_manager.displays:
                self._combo_monitor.addItem(d.display_label, d.index)
        else:
            default_disp = _resolve_default_display()
            self._combo_monitor.addItem(default_disp.display_label, default_disp.index)

    def _get_active_display(self) -> DisplayInfo:
        idx = self._combo_monitor.currentData()
        if self._display_manager and idx is not None:
            disp = self._display_manager.get_display_by_index(int(idx))
            if disp is not None:
                return disp
            return self._display_manager.active_display
        return _resolve_default_display()

    def _on_type_changed(self, index: int) -> None:
        self._mode = AuthoringMode.HIGHLIGHT if index == 0 else AuthoringMode.ARROW
        self._sync_type_labels()

    def _sync_type_labels(self) -> None:
        if self._mode == AuthoringMode.HIGHLIGHT:
            self._lbl_c1.setText("X:")
            self._lbl_c2.setText("Y:")
            self._lbl_c3.setText("W:")
            self._lbl_c4.setText("H:")
            self._sp_c3.setMinimum(1)
            self._sp_c4.setMinimum(1)
        else:
            self._lbl_c1.setText("X1:")
            self._lbl_c2.setText("Y1:")
            self._lbl_c3.setText("X2:")
            self._lbl_c4.setText("Y2:")
            self._sp_c3.setMinimum(-10000)
            self._sp_c4.setMinimum(-10000)
        self._update_readout()

    def _update_readout(self) -> None:
        disp = self._get_active_display()
        dpr = disp.device_pixel_ratio
        if self._mode == AuthoringMode.HIGHLIGHT:
            w = self._sp_c3.value()
            h = self._sp_c4.value()
            pw = max(1, round(w * dpr))
            ph = max(1, round(h * dpr))
            self._lbl_info.setText(f"Physical Size: {pw} × {ph} px | DPI: {int(round(dpr * 100))}%")
        else:
            x1 = self._sp_c1.value()
            y1 = self._sp_c2.value()
            x2 = self._sp_c3.value()
            y2 = self._sp_c4.value()
            length = math.hypot(x2 - x1, y2 - y1)
            plen = round(length * dpr)
            self._lbl_info.setText(f"Physical Length: {plen} px | DPI: {int(round(dpr * 100))}%")

    def _launch_screen_selector(self) -> None:
        disp = self._get_active_display()
        self.hide()

        self._overlay_widget = AuthoringOverlayWidget(
            display=disp,
            mode=self._mode,
            high_contrast=self._chk_hc.isChecked(),
            text_scale=self._text_scale,
            label_text=self._txt_label.text(),
        )

        self._overlay_widget.confirmed.connect(self._on_overlay_confirmed)
        self._overlay_widget.cancelled.connect(self._on_overlay_cancelled)
        self._overlay_widget.show()

    def _on_overlay_confirmed(self, spec: HighlightSpec | ArrowSpec) -> None:
        self.show()
        if isinstance(spec, HighlightSpec):
            self._sp_c1.setValue(spec.x)
            self._sp_c2.setValue(spec.y)
            self._sp_c3.setValue(spec.width)
            self._sp_c4.setValue(spec.height)
            self._txt_label.setText(spec.label)
            self._created_highlight = spec
        elif isinstance(spec, ArrowSpec):
            self._sp_c1.setValue(spec.start_x)
            self._sp_c2.setValue(spec.start_y)
            self._sp_c3.setValue(spec.end_x)
            self._sp_c4.setValue(spec.end_y)
            self._txt_label.setText(spec.label)
            self._created_arrow = spec
        self._update_readout()

    def _on_overlay_cancelled(self) -> None:
        self.show()

    def _on_accept(self) -> None:
        disp = self._get_active_display()
        allow_neg = disp.x < 0 or disp.y < 0
        lbl = self._txt_label.text().strip()

        if self._mode == AuthoringMode.HIGHLIGHT:
            self._created_highlight = HighlightSpec(
                x=self._sp_c1.value(),
                y=self._sp_c2.value(),
                width=max(1, self._sp_c3.value()),
                height=max(1, self._sp_c4.value()),
                label=lbl,
                allow_negative=allow_neg,
            )
        else:
            self._created_arrow = ArrowSpec(
                start_x=self._sp_c1.value(),
                start_y=self._sp_c2.value(),
                end_x=self._sp_c3.value(),
                end_y=self._sp_c4.value(),
                label=lbl,
                allow_negative=allow_neg,
            )
        self.accept()

    @property
    def created_highlight(self) -> HighlightSpec | None:
        """Constructed HighlightSpec if in HIGHLIGHT mode."""
        return self._created_highlight

    @property
    def created_arrow(self) -> ArrowSpec | None:
        """Constructed ArrowSpec if in ARROW mode."""
        return self._created_arrow
