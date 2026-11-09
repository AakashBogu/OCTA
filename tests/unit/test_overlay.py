"""Unit tests for the passive Qt visual overlay (octa.intelligence.overlay)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from octa.intelligence.capture import CaptureRegion
from octa.intelligence.exceptions import InvalidOverlayCoordinatesError
from octa.intelligence.overlay import (
    ArrowSpec,
    GuidanceOverlayWidget,
    HighlightSpec,
    OverlayBounds,
    overlay_hint_to_spec,
)
from octa.intelligence.tasks import OverlayElementType, OverlayHint


def test_highlight_and_arrow_coordinate_validation() -> None:
    """HighlightSpec and ArrowSpec reject invalid coordinates, dimensions, and screen overflow."""
    screen = OverlayBounds(x=0, y=0, width=800, height=600)

    valid_h = HighlightSpec(x=20, y=30, width=120, height=40, label="Save Button")
    valid_h.validate_against_screen(screen)

    valid_a = ArrowSpec(start_x=200, start_y=150, end_x=80, end_y=50, label="Click")
    valid_a.validate_against_screen(screen)

    # Negative coordinates
    with pytest.raises(InvalidOverlayCoordinatesError, match="non-negative"):
        HighlightSpec(x=-5, y=10, width=50, height=50)

    with pytest.raises(InvalidOverlayCoordinatesError, match="non-negative"):
        ArrowSpec(start_x=10, start_y=-1, end_x=50, end_y=50)

    # Zero or negative dimensions
    with pytest.raises(InvalidOverlayCoordinatesError, match="positive"):
        HighlightSpec(x=0, y=0, width=0, height=40)

    # Zero-length arrow
    with pytest.raises(InvalidOverlayCoordinatesError, match="must not be identical"):
        ArrowSpec(start_x=100, start_y=100, end_x=100, end_y=100)

    # Boolean rejected as integer coordinate
    with pytest.raises(InvalidOverlayCoordinatesError, match="must be an integer"):
        HighlightSpec(x=True, y=0, width=20, height=20)  # type: ignore[arg-type]

    # Invalid color string
    with pytest.raises(InvalidOverlayCoordinatesError, match="not a valid color"):
        HighlightSpec(x=10, y=10, width=20, height=20, color="not-a-color-123")

    # Out of screen bounds
    overflow_h = HighlightSpec(x=750, y=100, width=100, height=50)
    with pytest.raises(InvalidOverlayCoordinatesError, match="exceeds screen bounds"):
        overflow_h.validate_against_screen(screen)

    overflow_a = ArrowSpec(start_x=100, start_y=100, end_x=850, end_y=200)
    with pytest.raises(InvalidOverlayCoordinatesError, match="exceeds screen bounds"):
        overflow_a.validate_against_screen(screen)


def test_guidance_overlay_is_passive_and_click_through(qtbot: QtBot) -> None:
    """GuidanceOverlayWidget is strictly non-interactive and transparent to mouse/focus."""
    bounds = OverlayBounds(x=0, y=0, width=640, height=480)
    overlay = GuidanceOverlayWidget(screen_bounds=bounds)
    qtbot.addWidget(overlay)

    assert overlay.is_interactive() is False
    assert overlay.is_click_through() is True
    assert bool(overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput)
    assert bool(overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    assert overlay.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_guidance_overlay_rejects_out_of_bounds_annotations_atomically(
    qtbot: QtBot,
) -> None:
    """Out-of-bounds coordinates raise InvalidOverlayCoordinatesError without corrupting state."""
    bounds = OverlayBounds(x=0, y=0, width=400, height=300)
    overlay = GuidanceOverlayWidget(screen_bounds=bounds)
    qtbot.addWidget(overlay)

    initial_h = HighlightSpec(x=10, y=10, width=80, height=40, label="File")
    overlay.add_highlight(initial_h)
    assert overlay.highlights == (initial_h,)

    # Direct add_highlight out of bounds
    with pytest.raises(InvalidOverlayCoordinatesError, match="exceeds screen bounds"):
        overlay.add_highlight(HighlightSpec(x=350, y=10, width=100, height=40))

    # Direct add_arrow out of bounds
    with pytest.raises(InvalidOverlayCoordinatesError, match="exceeds screen bounds"):
        overlay.add_arrow(ArrowSpec(start_x=50, start_y=50, end_x=450, end_y=100))

    # Atomic set_annotations rollback when one arrow is out of bounds
    with pytest.raises(InvalidOverlayCoordinatesError, match="exceeds screen bounds"):
        overlay.set_annotations(
            highlights=[HighlightSpec(x=20, y=20, width=50, height=50)],
            arrows=[ArrowSpec(start_x=10, start_y=10, end_x=500, end_y=500)],
        )

    # Initial highlight remains intact
    assert overlay.highlights == (initial_h,)
    assert overlay.arrows == ()


def test_guidance_overlay_apply_hints_and_renders_pixels(qtbot: QtBot) -> None:
    """Overlay converts OverlayHints, renders visible pixels, and clears cleanly."""
    bounds = OverlayBounds(x=0, y=0, width=320, height=240)
    overlay = GuidanceOverlayWidget(screen_bounds=bounds)
    qtbot.addWidget(overlay)

    hints = (
        OverlayHint(
            kind=OverlayElementType.HIGHLIGHT,
            label="Menu",
            color="#00E676",
            bounds=CaptureRegion(x=40, y=40, width=80, height=40),
        ),
        OverlayHint(
            kind=OverlayElementType.ARROW,
            label="Point",
            color="#FFAB00",
            start_x=200,
            start_y=160,
            end_x=125,
            end_y=60,
        ),
    )

    overlay.apply_hints(hints)
    assert len(overlay.highlights) == 1
    assert len(overlay.arrows) == 1

    rendered = overlay.render_to_image()
    assert rendered.width() == 320
    assert rendered.height() == 240

    # Far corner (2, 2) should remain transparent (alpha == 0)
    assert rendered.pixelColor(2, 2).alpha() == 0

    # Center of highlight interior (60, 60) has translucent fill (alpha > 0)
    assert rendered.pixelColor(60, 60).alpha() > 0

    # Midpoint of arrow shaft (162, 110) has visible stroke (alpha > 0)
    assert rendered.pixelColor(162, 110).alpha() > 0

    # Clearing annotations restores transparency
    overlay.clear_annotations()
    assert overlay.highlights == ()
    assert overlay.arrows == ()
    cleared_img = overlay.render_to_image()
    assert cleared_img.pixelColor(60, 60).alpha() == 0
    assert cleared_img.pixelColor(162, 110).alpha() == 0


def test_overlay_hint_to_spec_conversion() -> None:
    """overlay_hint_to_spec converts valid hints and validates against screen bounds."""
    bounds = OverlayBounds(x=0, y=0, width=500, height=400)
    h_hint = OverlayHint(
        kind=OverlayElementType.HIGHLIGHT,
        bounds=CaptureRegion(x=10, y=10, width=50, height=30),
    )
    spec = overlay_hint_to_spec(h_hint, screen=bounds)
    assert isinstance(spec, HighlightSpec)
    assert spec.width == 50


def test_overlay_bounds_update_revalidates_annotations(qtbot: QtBot) -> None:
    """Updating screen_bounds re-validates active annotations and rejects clipping bounds."""
    overlay = GuidanceOverlayWidget(screen_bounds=OverlayBounds(x=0, y=0, width=500, height=400))
    qtbot.addWidget(overlay)

    overlay.add_highlight(HighlightSpec(x=300, y=200, width=100, height=80))

    # Shrinking screen bounds so active highlight overflows raises InvalidOverlayCoordinatesError
    with pytest.raises(InvalidOverlayCoordinatesError, match="exceeds screen bounds"):
        overlay.set_screen_bounds(OverlayBounds(x=0, y=0, width=350, height=250))

    # Original bounds preserved
    assert overlay.screen_bounds == OverlayBounds(x=0, y=0, width=500, height=400)
