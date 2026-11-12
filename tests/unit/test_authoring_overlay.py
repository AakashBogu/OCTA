"""Unit and Qt interaction tests for Phase 10.1 — Visual Region & Directional Cue Selector."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QPushButton

from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow
from octa.intelligence.authoring_overlay import (
    AuthoringCueDialog,
    AuthoringMode,
    AuthoringOverlayWidget,
    logical_to_physical,
    physical_to_logical,
    rect_logical_to_physical,
    rect_physical_to_logical,
)
from octa.intelligence.display import DisplayInfo, DisplayManager
from octa.intelligence.overlay import ArrowSpec, HighlightSpec, OverlayBounds


def _sample_triple_monitors() -> tuple[DisplayInfo, ...]:
    """Mock display provider returning Primary, LeftSecondary, and TopSecondary displays."""
    return (
        DisplayInfo(
            index=0,
            name="Primary",
            x=0,
            y=0,
            width=1920,
            height=1080,
            device_pixel_ratio=1.0,
            is_primary=True,
        ),
        DisplayInfo(
            index=1,
            name="LeftSecondary",
            x=-1920,
            y=0,
            width=1920,
            height=1080,
            device_pixel_ratio=1.5,
            is_primary=False,
        ),
        DisplayInfo(
            index=2,
            name="TopSecondary",
            x=0,
            y=-1080,
            width=1920,
            height=1080,
            device_pixel_ratio=2.0,
            is_primary=False,
        ),
    )


def _send_mouse_click_and_drag(
    widget: AuthoringOverlayWidget,
    start: QPoint,
    end: QPoint,
) -> None:
    """Helper to simulate click-and-drag mouse events on the authoring overlay."""
    start_f = QPointF(float(start.x()), float(start.y()))
    end_f = QPointF(float(end.x()), float(end.y()))
    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        start_f,
        start_f,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(press_ev)

    move_ev = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        end_f,
        end_f,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseMoveEvent(move_ev)

    release_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        end_f,
        end_f,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseReleaseEvent(release_ev)


def test_coordinate_conversion_helpers() -> None:
    """Logical and physical coordinate conversion helpers scale accurately."""
    # 1.0 scale
    assert logical_to_physical(100, 200, 1.0) == (100, 200)
    assert physical_to_logical(100, 200, 1.0) == (100, 200)
    assert rect_logical_to_physical((10, 20, 100, 50), 1.0) == (10, 20, 100, 50)
    assert rect_physical_to_logical((10, 20, 100, 50), 1.0) == (10, 20, 100, 50)

    # 1.5 scale (High-DPI)
    assert logical_to_physical(100, 200, 1.5) == (150, 300)
    assert physical_to_logical(150, 300, 1.5) == (100, 200)
    assert rect_logical_to_physical((10, 20, 100, 50), 1.5) == (15, 30, 150, 75)
    assert rect_physical_to_logical((15, 30, 150, 75), 1.5) == (10, 20, 100, 50)

    # 2.0 scale (4K UHD)
    assert logical_to_physical(50, 80, 2.0) == (100, 160)
    assert physical_to_logical(100, 160, 2.0) == (50, 80)


def test_authoring_overlay_window_flags_and_properties(qapp: object) -> None:
    """Authoring overlay initializes with active focus, mouse tracking, and crosshair cursor."""
    disp = DisplayInfo(
        index=0,
        name="TestDisplay",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.0,
        is_primary=True,
    )
    overlay = AuthoringOverlayWidget(display=disp)

    try:
        flags = overlay.windowFlags()
        assert bool(flags & Qt.WindowType.FramelessWindowHint)
        assert bool(flags & Qt.WindowType.WindowStaysOnTopHint)
        assert bool(flags & Qt.WindowType.Tool)

        # Unlike passive learner guidance, authoring overlay must accept pointer coordinates
        assert not bool(flags & Qt.WindowType.WindowTransparentForInput)
        assert overlay.hasMouseTracking()
        assert overlay.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert overlay.cursor().shape() == Qt.CursorShape.CrossCursor
        assert overlay.display == disp
        assert overlay.mode == AuthoringMode.HIGHLIGHT
    finally:
        overlay.close()


def test_authoring_overlay_drag_highlight_selection(qapp: object) -> None:
    """Click and drag selection on primary monitor produces valid HighlightSpec."""
    disp = DisplayInfo(
        index=0,
        name="Primary",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.0,
        is_primary=True,
    )
    overlay = AuthoringOverlayWidget(display=disp, label_text="OK Button")

    try:
        # Simulate dragging from local (100, 150) to local (350, 250)
        _send_mouse_click_and_drag(overlay, QPoint(100, 150), QPoint(350, 250))

        spec = overlay.current_highlight_spec
        assert spec is not None
        assert spec.x == 100
        assert spec.y == 150
        assert spec.width == 250
        assert spec.height == 100
        assert spec.label == "OK Button"

        # Validate against screen bounds
        bounds = OverlayBounds(x=0, y=0, width=1920, height=1080)
        spec.validate_against_screen(bounds)
    finally:
        overlay.close()


def test_authoring_overlay_negative_coordinate_adaptation(qapp: object) -> None:
    """Dragging on secondary monitor placed left of primary maps to negative virtual coords."""
    # Display positioned at x = -1920, y = 0
    disp_left = DisplayInfo(
        index=1,
        name="LeftDisplay",
        x=-1920,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.5,
        is_primary=False,
    )
    overlay = AuthoringOverlayWidget(display=disp_left, label_text="Side Panel")

    try:
        # Drag locally inside the left display from (200, 100) to (500, 400)
        _send_mouse_click_and_drag(overlay, QPoint(200, 100), QPoint(500, 400))

        spec = overlay.current_highlight_spec
        assert spec is not None
        # -1920 + 200 = -1720
        assert spec.x == -1720
        assert spec.y == 100
        assert spec.width == 300
        assert spec.height == 300
        assert spec.allow_negative is True

        # Validates against display screen bounds
        bounds = OverlayBounds(x=-1920, y=0, width=1920, height=1080)
        spec.validate_against_screen(bounds)
    finally:
        overlay.close()


def test_authoring_overlay_arrow_selection(qapp: object) -> None:
    """Arrow mode drag produces valid ArrowSpec with correct start and end points."""
    disp = DisplayInfo(
        index=0,
        name="Primary",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.0,
        is_primary=True,
    )
    overlay = AuthoringOverlayWidget(
        display=disp,
        mode=AuthoringMode.ARROW,
        label_text="Click here",
    )

    try:
        assert overlay.mode == AuthoringMode.ARROW
        _send_mouse_click_and_drag(overlay, QPoint(200, 300), QPoint(450, 500))

        spec = overlay.current_arrow_spec
        assert spec is not None
        assert spec.start_x == 200
        assert spec.start_y == 300
        assert spec.end_x == 450
        assert spec.end_y == 500
        assert spec.label == "Click here"

        bounds = OverlayBounds(x=0, y=0, width=1920, height=1080)
        spec.validate_against_screen(bounds)
    finally:
        overlay.close()


def test_authoring_overlay_fine_tuning_spinboxes(qapp: object) -> None:
    """Modifying HUD spinboxes dynamically updates active HighlightSpec and ArrowSpec."""
    disp = DisplayInfo(
        index=0,
        name="Primary",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.25,
        is_primary=True,
    )
    overlay = AuthoringOverlayWidget(display=disp)

    try:
        # Set highlight coordinates via spinboxes
        overlay._spin_c1.setValue(120)
        overlay._spin_c2.setValue(180)
        overlay._spin_c3.setValue(220)
        overlay._spin_c4.setValue(140)
        overlay._txt_label.setText("Save File")

        spec = overlay.current_highlight_spec
        assert spec is not None
        assert spec.x == 120
        assert spec.y == 180
        assert spec.width == 220
        assert spec.height == 140
        assert spec.label == "Save File"

        # Switch to Arrow mode and adjust spinboxes
        overlay.set_mode(AuthoringMode.ARROW)
        overlay._spin_c1.setValue(50)
        overlay._spin_c2.setValue(60)
        overlay._spin_c3.setValue(300)
        overlay._spin_c4.setValue(400)

        arr_spec = overlay.current_arrow_spec
        assert arr_spec is not None
        assert arr_spec.start_x == 50
        assert arr_spec.start_y == 60
        assert arr_spec.end_x == 300
        assert arr_spec.end_y == 400
    finally:
        overlay.close()


def test_authoring_overlay_high_contrast_preview(qapp: object) -> None:
    """Toggling high contrast updates rendering mode without error."""
    disp = DisplayInfo(
        index=0,
        name="Primary",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.0,
        is_primary=True,
    )
    overlay = AuthoringOverlayWidget(display=disp, high_contrast=False)

    try:
        assert not overlay.high_contrast
        overlay.set_high_contrast(True)
        assert overlay.high_contrast

        # Set a highlight and arrow, then trigger paintEvent directly to verify no rendering crash
        overlay._spin_c1.setValue(100)
        overlay._spin_c2.setValue(100)
        overlay._spin_c3.setValue(200)
        overlay._spin_c4.setValue(150)
        overlay.repaint()

        overlay.set_mode(AuthoringMode.ARROW)
        overlay._spin_c1.setValue(100)
        overlay._spin_c2.setValue(100)
        overlay._spin_c3.setValue(300)
        overlay._spin_c4.setValue(300)
        overlay.repaint()
    finally:
        overlay.close()


def test_authoring_overlay_cancel_dismissal_and_cleanup(qapp: object) -> None:
    """Escape key or cancel() immediately hides the overlay and emits cancelled signal."""
    disp = DisplayInfo(
        index=0,
        name="Primary",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.0,
        is_primary=True,
    )
    overlay = AuthoringOverlayWidget(display=disp)
    cancelled_emitted = False

    def on_cancelled() -> None:
        nonlocal cancelled_emitted
        cancelled_emitted = True

    overlay.cancelled.connect(on_cancelled)
    overlay.show()
    assert overlay.isVisible()

    # Simulate pressing Escape key
    esc_ev = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )
    overlay.keyPressEvent(esc_ev)

    assert cancelled_emitted
    assert not overlay.isVisible()


def test_authoring_overlay_confirm_dismissal_and_signal(qapp: object) -> None:
    """Enter key or confirm() emits confirmed spec and closes overlay cleanly."""
    disp = DisplayInfo(
        index=0,
        name="Primary",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.0,
        is_primary=True,
    )
    overlay = AuthoringOverlayWidget(display=disp)
    confirmed_spec: HighlightSpec | ArrowSpec | None = None

    def on_confirmed(spec: object) -> None:
        nonlocal confirmed_spec
        confirmed_spec = spec  # type: ignore

    overlay.confirmed.connect(on_confirmed)
    overlay.show()

    # Create a spec via spinboxes
    overlay._spin_c1.setValue(50)
    overlay._spin_c2.setValue(50)
    overlay._spin_c3.setValue(100)
    overlay._spin_c4.setValue(80)

    # Simulate pressing Enter
    enter_ev = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
    )
    overlay.keyPressEvent(enter_ev)

    assert confirmed_spec is not None
    assert isinstance(confirmed_spec, HighlightSpec)
    assert confirmed_spec.width == 100
    assert not overlay.isVisible()


def test_authoring_cue_dialog_integration(qapp: object) -> None:
    """AuthoringCueDialog manages multi-display selection and returns valid specs."""
    dm = DisplayManager(display_provider=_sample_triple_monitors)

    dlg = AuthoringCueDialog(display_manager=dm, mode=AuthoringMode.HIGHLIGHT)

    try:
        assert dlg._combo_monitor.count() == 3
        # Switch monitor to LeftSecondary (index 1)
        dlg._combo_monitor.setCurrentIndex(1)
        active_disp = dlg._get_active_display()
        assert active_disp.index == 1
        assert active_disp.x == -1920

        # Set coordinates and accept
        dlg._sp_c1.setValue(-1500)
        dlg._sp_c2.setValue(100)
        dlg._sp_c3.setValue(200)
        dlg._sp_c4.setValue(120)
        dlg._txt_label.setText("Left Screen Target")

        dlg._on_accept()
        spec = dlg.created_highlight
        assert spec is not None
        assert spec.x == -1500
        assert spec.y == 100
        assert spec.width == 200
        assert spec.height == 120
        assert spec.label == "Left Screen Target"
        assert spec.allow_negative is True
    finally:
        dlg.close()


def test_diagnostic_window_visual_cue_selector_button(qapp: object) -> None:
    """DiagnosticWindow has a Cue Selector button wired to open AuthoringCueDialog."""
    ctrl = DesktopController()
    win = DiagnosticWindow(ctrl)

    try:
        assert hasattr(win, "_btn_cue_selector")
        btn = win._btn_cue_selector
        assert isinstance(btn, QPushButton)
        assert "Cue Selector" in btn.text()
        assert btn.accessibleName() == "Open Visual Cue Authoring Studio"
    finally:
        win.close()
