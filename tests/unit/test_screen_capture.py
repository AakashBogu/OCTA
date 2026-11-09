"""Unit tests for Windows screen and region capture (octa.intelligence.capture)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap

from octa.intelligence.capture import (
    CapturedImage,
    CaptureRegion,
    ScreenCapturer,
    encode_qimage_to_png_bytes,
    grab_screen_with_qt,
)
from octa.intelligence.exceptions import (
    CaptureCancelledError,
    CaptureRegionError,
    ScreenCaptureError,
    UserActionRequiredError,
)


def _make_sample_png_bytes(width: int = 64, height: int = 48) -> bytes:
    """Create a small in-memory PNG image for deterministic tests."""
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(QColor(40, 90, 160))
    return encode_qimage_to_png_bytes(img)


def test_capture_region_validation_and_bounds() -> None:
    """CaptureRegion validates coordinates, dimensions, and screen containment."""
    region = CaptureRegion(x=10, y=20, width=300, height=200)
    assert region.to_dict() == {"x": 10, "y": 20, "width": 300, "height": 200}
    assert CaptureRegion.from_dict(region.to_dict()) == region

    region.validate_within_bounds(1920, 1080)

    with pytest.raises(CaptureRegionError, match="exceeds screen bounds"):
        region.validate_within_bounds(250, 200)

    with pytest.raises(CaptureRegionError, match="non-negative"):
        CaptureRegion(x=-1, y=0, width=100, height=100)

    with pytest.raises(CaptureRegionError, match="positive"):
        CaptureRegion(x=0, y=0, width=0, height=100)

    with pytest.raises(CaptureRegionError, match="must be an integer"):
        CaptureRegion(x=True, y=0, width=10, height=10)  # type: ignore[arg-type]

    with pytest.raises(CaptureRegionError, match="Unexpected fields"):
        CaptureRegion.from_dict({"x": 0, "y": 0, "width": 10, "height": 10, "extra": 1})

    with pytest.raises(CaptureRegionError, match="Missing required fields"):
        CaptureRegion.from_dict({"x": 0, "y": 0})


def test_encode_qimage_rejects_null_image() -> None:
    """encode_qimage_to_png_bytes raises ScreenCaptureError on null/empty QImage."""
    null_image = QImage()
    with pytest.raises(ScreenCaptureError, match="null or empty"):
        encode_qimage_to_png_bytes(null_image)


def test_screen_capturer_requires_explicit_user_action() -> None:
    """Capture methods refuse to run unless user_initiated=True is explicitly provided."""
    grabber = MagicMock()
    capturer = ScreenCapturer(grabber=grabber)
    region = CaptureRegion(x=0, y=0, width=100, height=100)

    with pytest.raises(UserActionRequiredError, match="explicit user action"):
        capturer.capture_full_screen()

    with pytest.raises(UserActionRequiredError, match="explicit user action"):
        capturer.capture_full_screen(user_initiated=False)

    with pytest.raises(UserActionRequiredError, match="explicit user action"):
        capturer.capture_region(region, user_initiated=False)

    with pytest.raises(UserActionRequiredError, match="explicit user action"):
        capturer.select_and_capture_region(user_initiated=False)

    grabber.assert_not_called()


def test_screen_capturer_does_not_save_or_log_screenshots_by_default(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Screenshots stay in memory by default and never leak bytes or base64 into logs."""
    png_bytes = _make_sample_png_bytes(80, 60)

    def mock_grabber(
        req_region: CaptureRegion | None,
    ) -> tuple[bytes, int, int, CaptureRegion]:
        actual = req_region or CaptureRegion(x=0, y=0, width=80, height=60)
        return png_bytes, actual.width, actual.height, actual

    capturer = ScreenCapturer(
        grabber=mock_grabber,
        save_screenshots=False,
        save_dir=tmp_path,
    )

    with caplog.at_level(logging.DEBUG, logger="octa.intelligence.capture"):
        full_img = capturer.capture_full_screen(user_initiated=True)
        sub_region = CaptureRegion(x=5, y=10, width=40, height=30)
        reg_img = capturer.capture_region(sub_region, user_initiated=True)

    # Not saved to disk by default
    assert full_img.saved_path is None
    assert reg_img.saved_path is None
    assert list(tmp_path.iterdir()) == []

    # Base64 and raw bytes are never present in repr, str, summary, or logs
    b64_str = full_img.to_base64()
    assert len(b64_str) > 20
    assert b64_str not in repr(full_img)
    assert b64_str not in str(full_img)
    assert b64_str not in str(full_img.summary())
    assert "iVBOR" not in repr(full_img)

    for record in caplog.records:
        msg = record.getMessage()
        assert b64_str not in msg
        assert "iVBOR" not in msg
        assert "data:image/png" not in msg


def test_screen_capturer_optional_persistence_when_enabled(tmp_path: Path) -> None:
    """Screenshots are written to save_dir only when explicitly enabled."""
    png_bytes = _make_sample_png_bytes(32, 32)
    capturer = ScreenCapturer(
        grabber=lambda _r: (png_bytes, 32, 32, CaptureRegion(x=0, y=0, width=32, height=32)),
        save_screenshots=True,
        save_dir=tmp_path,
    )

    captured = capturer.capture_full_screen(user_initiated=True)
    assert captured.saved_path is not None
    assert captured.saved_path.exists()
    assert captured.saved_path.read_bytes() == png_bytes


def test_select_and_capture_region_cancellation() -> None:
    """Cancelling interactive region selection raises CaptureCancelledError without grabbing."""
    grabber = MagicMock()
    capturer = ScreenCapturer(
        grabber=grabber,
        region_selector=lambda: None,
    )

    with pytest.raises(CaptureCancelledError, match="cancelled by the user"):
        capturer.select_and_capture_region(user_initiated=True)

    grabber.assert_not_called()


def test_select_and_capture_region_success() -> None:
    """Interactive region selector passes chosen region to capture_region."""
    png_bytes = _make_sample_png_bytes(50, 40)
    chosen = CaptureRegion(x=100, y=120, width=50, height=40)

    capturer = ScreenCapturer(
        grabber=lambda r: (png_bytes, 50, 40, r or chosen),
        region_selector=lambda: chosen,
    )

    captured = capturer.select_and_capture_region(user_initiated=True)
    assert isinstance(captured, CapturedImage)
    assert captured.source == "region"
    assert captured.region == chosen
    assert captured.width == 50
    assert captured.height == 40


def test_grab_screen_with_qt_mocked_primary_screen(
    qtbot: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grab_screen_with_qt validates region against primary screen and encodes QPixmap in memory."""
    del qtbot
    img = QImage(100, 80, QImage.Format.Format_ARGB32)
    img.fill(QColor(20, 120, 80))
    pixmap = QPixmap.fromImage(img)

    fake_screen = MagicMock()
    fake_screen.geometry.return_value = QRect(0, 0, 100, 80)
    fake_screen.grabWindow.return_value = pixmap

    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: fake_screen))

    region = CaptureRegion(x=10, y=10, width=50, height=40)
    png_bytes, w, h, actual_region = grab_screen_with_qt(region)
    assert len(png_bytes) > 0
    assert (w, h) == (100, 80)
    assert actual_region == region
    fake_screen.grabWindow.assert_called_once_with(0, 10, 10, 50, 40)

    # Out-of-bounds region is rejected before grabWindow
    out_of_bounds = CaptureRegion(x=80, y=50, width=50, height=50)
    with pytest.raises(CaptureRegionError, match="exceeds screen bounds"):
        grab_screen_with_qt(out_of_bounds)


def test_grab_screen_with_qt_error_conditions(
    qtbot: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grab_screen_with_qt raises ScreenCaptureError when screen is missing or pixmap is null."""
    del qtbot
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: None))
    with pytest.raises(ScreenCaptureError, match="No primary screen"):
        grab_screen_with_qt()

    fake_screen = MagicMock()
    fake_screen.geometry.return_value = QRect(0, 0, 100, 80)
    fake_screen.grabWindow.return_value = QPixmap()
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: fake_screen))

    with pytest.raises(ScreenCaptureError, match="empty or null pixmap"):
        grab_screen_with_qt()


def test_captured_image_and_capturer_edge_validation() -> None:
    """CapturedImage and ScreenCapturer validate invalid inputs and missing save_dir."""
    region = CaptureRegion(x=0, y=0, width=10, height=10)
    png_bytes = _make_sample_png_bytes(10, 10)

    with pytest.raises(ScreenCaptureError, match="non-empty PNG bytes"):
        CapturedImage(png_bytes=b"", width=10, height=10, region=region)

    with pytest.raises(ScreenCaptureError, match="source must be one of"):
        CapturedImage(png_bytes=png_bytes, width=10, height=10, region=region, source="invalid")

    capturer_no_dir = ScreenCapturer(
        grabber=lambda _r: (png_bytes, 10, 10, region),
        save_screenshots=True,
        save_dir=None,
    )
    with pytest.raises(ScreenCaptureError, match="no save_dir is configured"):
        capturer_no_dir.capture_full_screen(user_initiated=True)

    with pytest.raises(CaptureCancelledError, match="No interactive region selector"):
        capturer_no_dir.select_and_capture_region(user_initiated=True)
