"""Unit tests for multi-monitor geometry, display adaptation, and coordinate mapping (Phase 8.2)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow
from octa.intelligence.capture import (
    CapturedImage,
    CaptureRegion,
    ScreenCapturer,
)
from octa.intelligence.display import DisplayInfo, DisplayManager
from octa.intelligence.exceptions import CaptureRegionError
from octa.intelligence.overlay import (
    ArrowSpec,
    GuidanceOverlayWidget,
    HighlightSpec,
    OverlayBounds,
    overlay_hint_to_spec,
)
from octa.intelligence.tasks import OverlayElementType, OverlayHint
from octa.tutorials.catalog import build_octa_welcome_tutorial
from octa.tutorials.engine import TutorialEngine

# ══════════════════════════════════════════════
# DisplayInfo Tests
# ══════════════════════════════════════════════


def test_display_info_validation() -> None:
    """DisplayInfo validates geometry, index, DPR, and generates labels."""
    d = DisplayInfo(
        index=0,
        name=r"\\.\DISPLAY1",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.25,
        is_primary=True,
    )
    assert d.index == 0
    assert d.name == r"\\.\DISPLAY1"
    assert d.geometry == (0, 0, 1920, 1080)
    assert d.physical_width == 2400
    assert d.physical_height == 1350
    assert "Primary" in d.display_label
    assert "125%" in d.display_label

    # Invalid values
    with pytest.raises(ValueError, match="index must be a non-negative integer"):
        DisplayInfo(index=-1, name="D", x=0, y=0, width=1920, height=1080)

    with pytest.raises(ValueError, match="name must be a non-empty string"):
        DisplayInfo(index=0, name="", x=0, y=0, width=1920, height=1080)

    with pytest.raises(ValueError, match="width must be a positive integer"):
        DisplayInfo(index=0, name="D", x=0, y=0, width=0, height=1080)

    with pytest.raises(ValueError, match="device_pixel_ratio must be positive"):
        DisplayInfo(index=0, name="D", x=0, y=0, width=1920, height=1080, device_pixel_ratio=0.0)


def test_display_info_negative_coordinates() -> None:
    """DisplayInfo correctly represents displays placed left of or above primary."""
    left_display = DisplayInfo(
        index=1,
        name=r"\\.\DISPLAY2",
        x=-1920,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.0,
        is_primary=False,
    )
    assert left_display.x == -1920
    assert left_display.y == 0
    assert "-1920, +0" in left_display.display_label

    bounds = left_display.to_overlay_bounds()
    assert bounds.x == -1920
    assert bounds.width == 1920

    cap_reg = left_display.to_capture_region()
    assert cap_reg.x == -1920
    assert cap_reg.allow_negative is True

    top_display = DisplayInfo(
        index=2,
        name=r"\\.\DISPLAY3",
        x=0,
        y=-1440,
        width=2560,
        height=1440,
        device_pixel_ratio=1.5,
        is_primary=False,
    )
    assert top_display.y == -1440
    assert "+0, -1440" in top_display.display_label


def test_display_info_point_and_rect_mapping() -> None:
    """DisplayInfo maps coordinates between global virtual desktop and monitor-local space."""
    left_display = DisplayInfo(
        index=1,
        name="Left",
        x=-1920,
        y=100,
        width=1920,
        height=1080,
    )

    # Point containment
    assert left_display.contains_global_point(-1000, 500)
    assert not left_display.contains_global_point(100, 500)
    assert left_display.contains_local_point(920, 400)
    assert not left_display.contains_local_point(2000, 400)

    # Global <-> Local point mapping
    lx, ly = left_display.global_to_local(-1500, 300)
    assert lx == 420  # -1500 - (-1920)
    assert ly == 200  # 300 - 100

    gx, gy = left_display.local_to_global(420, 200)
    assert gx == -1500
    assert gy == 300

    # Global <-> Local rect mapping
    global_rect = CaptureRegion(x=-1500, y=300, width=400, height=200, allow_negative=True)
    local_rect = left_display.global_rect_to_local(global_rect)
    assert local_rect.x == 420
    assert local_rect.y == 200
    assert local_rect.width == 400
    assert local_rect.height == 200

    roundtrip_global = left_display.local_rect_to_global(local_rect)
    assert roundtrip_global.x == -1500
    assert roundtrip_global.y == 300
    assert roundtrip_global.width == 400
    assert roundtrip_global.height == 200


def test_display_info_scaling_transforms() -> None:
    """DisplayInfo scales between logical points and physical pixels."""
    d = DisplayInfo(
        index=0,
        name="HiDPI",
        x=0,
        y=0,
        width=1920,
        height=1080,
        device_pixel_ratio=1.5,
    )
    px, py = d.scale_logical_to_physical(100.0, 200.0)
    assert px == 150.0
    assert py == 300.0

    lx, ly = d.scale_physical_to_logical(150.0, 300.0)
    assert lx == 100.0
    assert ly == 200.0


# ══════════════════════════════════════════════
# DisplayManager Tests
# ══════════════════════════════════════════════


def _sample_triple_monitors() -> list[DisplayInfo]:
    """Helper returning a standard 3-monitor setup: Left, Primary, Right."""
    return [
        DisplayInfo(
            index=0,
            name="Primary Display",
            x=0,
            y=0,
            width=1920,
            height=1080,
            device_pixel_ratio=1.0,
            is_primary=True,
        ),
        DisplayInfo(
            index=1,
            name="Left Display",
            x=-1920,
            y=0,
            width=1920,
            height=1080,
            device_pixel_ratio=1.0,
            is_primary=False,
        ),
        DisplayInfo(
            index=2,
            name="Right 4K Display",
            x=1920,
            y=0,
            width=3840,
            height=2160,
            device_pixel_ratio=2.0,
            is_primary=False,
        ),
    ]


def test_display_manager_discovery_and_active_monitor() -> None:
    """DisplayManager discovers displays from provider and defaults to primary."""
    manager = DisplayManager(display_provider=_sample_triple_monitors)
    assert len(manager.displays) == 3
    assert manager.active_index == 0
    assert manager.active_display.name == "Primary Display"
    assert manager.primary_display.name == "Primary Display"


def test_display_manager_selection_and_signals() -> None:
    """DisplayManager selects monitor by index or name and emits active_display_changed."""
    manager = DisplayManager(display_provider=_sample_triple_monitors)

    changed_events: list[DisplayInfo] = []
    manager.active_display_changed.connect(changed_events.append)

    # Select left display by index
    assert manager.select_display(1) is True
    assert manager.active_index == 1
    assert manager.active_display.x == -1920
    assert len(changed_events) == 1
    assert changed_events[-1].index == 1

    # Select right display by name
    assert manager.select_display("Right 4K Display") is True
    assert manager.active_index == 2
    assert manager.active_display.width == 3840
    assert len(changed_events) == 2

    # Selecting current display is a no-op
    assert manager.select_display(2) is True
    assert len(changed_events) == 2


def test_display_manager_invalid_selection_fallback() -> None:
    """Selecting an invalid index or unknown name gracefully returns False without crashing."""
    manager = DisplayManager(display_provider=_sample_triple_monitors)
    initial_active = manager.active_display

    assert manager.select_display(999) is False
    assert manager.active_display == initial_active

    assert manager.select_display("Nonexistent Monitor") is False
    assert manager.active_display == initial_active


def test_display_manager_dynamic_disconnect_fallback() -> None:
    """When the active display is disconnected, refresh() automatically falls back to primary."""
    monitors = _sample_triple_monitors()
    manager = DisplayManager(display_provider=lambda: monitors)

    # Select display 2 (Right 4K)
    manager.select_display(2)
    assert manager.active_index == 2

    active_changed_events: list[DisplayInfo] = []
    displays_changed_events: list[tuple[DisplayInfo, ...]] = []
    manager.active_display_changed.connect(active_changed_events.append)
    manager.displays_changed.connect(displays_changed_events.append)

    # Disconnect displays 1 and 2, leaving only primary display 0
    monitors = [monitors[0]]
    manager.refresh()

    assert len(manager.displays) == 1
    assert manager.active_index == 0
    assert manager.active_display.is_primary is True
    assert len(active_changed_events) == 1
    assert active_changed_events[0].index == 0
    assert len(displays_changed_events) == 1


# ══════════════════════════════════════════════
# Capture & Negative Coordinate Region Tests
# ══════════════════════════════════════════════


def test_capture_region_allow_negative() -> None:
    """CaptureRegion supports negative coordinates when allow_negative=True."""
    # Negative disallowed by default
    with pytest.raises(CaptureRegionError, match="non-negative"):
        CaptureRegion(x=-50, y=0, width=100, height=100)

    # Negative allowed explicitly
    reg = CaptureRegion(x=-1800, y=50, width=500, height=400, allow_negative=True)
    assert reg.x == -1800
    assert reg.allow_negative is True

    # validate_within_bounds on left display (-1920, 0, 1920, 1080)
    reg.validate_within_bounds(1920, 1080, screen_x=-1920, screen_y=0)

    # Out of bounds on left display raises
    with pytest.raises(CaptureRegionError, match="exceeds screen bounds"):
        reg.validate_within_bounds(1920, 1080, screen_x=-1000, screen_y=0)

    # to_dict & from_dict serialization
    d = reg.to_dict()
    assert d["x"] == -1800
    assert d["allow_negative"] is True
    rebuilt = CaptureRegion.from_dict(d)
    assert rebuilt == reg


def test_screen_capturer_with_display_manager() -> None:
    """ScreenCapturer delegates to grabber with active monitor index."""
    mock_grabber = MagicMock(return_value=(b"pngdata", 1920, 1080, CaptureRegion(0, 0, 1920, 1080)))
    manager = DisplayManager(display_provider=_sample_triple_monitors)
    capturer = ScreenCapturer(grabber=mock_grabber, display_manager=manager)

    # By default, primary monitor (index 0)
    capturer.capture_full_screen(user_initiated=True)
    assert mock_grabber.call_args[1]["screen_index"] == 0

    # Switch to monitor 1
    manager.select_display(1)
    capturer.capture_full_screen(user_initiated=True)
    assert mock_grabber.call_args[1]["screen_index"] == 1

    # Explicit display_index override
    capturer.capture_full_screen(user_initiated=True, display_index=2)
    assert mock_grabber.call_args[1]["screen_index"] == 2


# ══════════════════════════════════════════════
# Overlay & Multi-Monitor Positioning Tests
# ══════════════════════════════════════════════


def test_overlay_on_negative_monitor() -> None:
    """GuidanceOverlayWidget and specs adapt to negative display coordinates."""
    left_bounds = OverlayBounds(x=-1920, y=0, width=1920, height=1080)

    # Highlight on negative monitor
    h = HighlightSpec(x=-1500, y=100, width=200, height=100, allow_negative=True)
    h.validate_against_screen(left_bounds)

    # Arrow on negative monitor
    a = ArrowSpec(start_x=-1500, start_y=100, end_x=-1400, end_y=100, allow_negative=True)
    a.validate_against_screen(left_bounds)

    # Converting OverlayHint with negative screen bounds automatically sets allow_negative
    hint = OverlayHint(
        kind=OverlayElementType.HIGHLIGHT,
        bounds=CaptureRegion(x=-1500, y=100, width=200, height=100, allow_negative=True),
        label="Target",
    )
    spec = overlay_hint_to_spec(hint, screen=left_bounds)
    assert isinstance(spec, HighlightSpec)
    assert spec.x == -1500
    assert spec.allow_negative is True


def test_guidance_overlay_set_display(qapp: object) -> None:
    """GuidanceOverlayWidget.set_display moves geometry to target monitor."""
    overlay = GuidanceOverlayWidget()
    left_display = DisplayInfo(
        index=1,
        name="Left",
        x=-1920,
        y=0,
        width=1920,
        height=1080,
    )
    overlay.set_display(left_display)
    assert overlay.screen_bounds.x == -1920
    assert overlay.screen_bounds.width == 1920
    assert overlay.geometry().x() == -1920


# ══════════════════════════════════════════════
# TutorialEngine & DesktopController Integration
# ══════════════════════════════════════════════


def test_tutorial_engine_display_coordination() -> None:
    """TutorialEngine coordinates display selection with overlay and capturer."""
    manager = DisplayManager(display_provider=_sample_triple_monitors)
    task = build_octa_welcome_tutorial()
    engine = TutorialEngine(tutorials=[task], display_manager=manager)

    assert engine.active_display.index == 0
    assert engine.overlay_widget.screen_bounds.x == 0

    # Select left display
    assert engine.select_display(1) is True
    assert engine.active_display.index == 1
    assert engine.overlay_widget.screen_bounds.x == -1920

    # Verify capture uses active display
    mock_capturer = MagicMock()
    mock_capturer.display_manager = manager
    sample_img = CapturedImage(
        png_bytes=b"png",
        width=1920,
        height=1080,
        region=CaptureRegion(x=-1920, y=0, width=1920, height=1080, allow_negative=True),
    )
    mock_capturer.capture_region.return_value = sample_img
    mock_capturer.capture_full_screen.return_value = sample_img
    engine._capturer = mock_capturer

    engine._capture_for_step(task.steps[0])
    assert mock_capturer.capture_region.call_args[1]["display_index"] == 1


def test_diagnostic_window_monitor_combo(qapp: object) -> None:
    """DiagnosticWindow provides monitor selection combo and syncs with display changes."""
    manager = DisplayManager(display_provider=_sample_triple_monitors)
    task = build_octa_welcome_tutorial()
    engine = TutorialEngine(tutorials=[task], display_manager=manager)
    controller = DesktopController(tutorial_engine=engine)
    window = DiagnosticWindow(controller)

    # Verify combo populated with 3 monitors
    items = window.get_monitor_combo_items()
    assert len(items) == 3
    assert "Display 0:" in items[0]
    assert "Primary" in items[0]
    assert "Display 1:" in items[1]
    assert "Display 2:" in items[2]
    assert window.get_selected_monitor_index() == 0

    # Change monitor in UI
    window._combo_monitor.setCurrentIndex(1)
    assert controller.active_display.index == 1
    assert engine.active_display.index == 1

    window.close()
    if controller.is_active:
        controller.stop(timeout_ms=100)
