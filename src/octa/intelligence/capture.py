"""Windows screen and region capture for OCTA.

Enforces explicit user initiation, keeps captured screenshots in memory by default,
and ensures raw image bytes or base64 payloads are never written to logs.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication, QImage, QPixmap

from octa.intelligence.exceptions import (
    CaptureCancelledError,
    CaptureRegionError,
    ScreenCaptureError,
    UserActionRequiredError,
)

logger = logging.getLogger("octa.intelligence.capture")


def _require_int(value: Any, field_name: str) -> int:
    """Validate that value is an integer and not a boolean."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise CaptureRegionError(f"Field '{field_name}' must be an integer, got {value!r}.")
    return value


@dataclass(frozen=True)
class CaptureRegion:
    """Validated rectangular screen region in pixel coordinates."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        x = _require_int(self.x, "x")
        y = _require_int(self.y, "y")
        width = _require_int(self.width, "width")
        height = _require_int(self.height, "height")

        if x < 0 or y < 0:
            raise CaptureRegionError(
                f"CaptureRegion coordinates must be non-negative, got ({x}, {y})."
            )
        if width <= 0 or height <= 0:
            raise CaptureRegionError(
                f"CaptureRegion dimensions must be positive, got {width}x{height}."
            )

    def validate_within_bounds(
        self,
        screen_width: int,
        screen_height: int,
        *,
        screen_x: int = 0,
        screen_y: int = 0,
    ) -> None:
        """Ensure this region fits completely within the given screen geometry."""
        sw = _require_int(screen_width, "screen_width")
        sh = _require_int(screen_height, "screen_height")
        sx = _require_int(screen_x, "screen_x")
        sy = _require_int(screen_y, "screen_y")

        if sw <= 0 or sh <= 0:
            raise CaptureRegionError(f"Screen bounds must have positive dimensions, got {sw}x{sh}.")
        if (
            self.x < sx
            or self.y < sy
            or (self.x + self.width) > (sx + sw)
            or (self.y + self.height) > (sy + sh)
        ):
            raise CaptureRegionError(
                f"CaptureRegion ({self.x}, {self.y}, {self.width}x{self.height}) exceeds "
                f"screen bounds ({sx}, {sy}, {sw}x{sh})."
            )

    def to_dict(self) -> dict[str, int]:
        """Serialize region to a JSON-compatible dictionary."""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct and validate a CaptureRegion from a dictionary."""
        if not isinstance(data, dict):
            raise CaptureRegionError("CaptureRegion must be a dictionary.")

        allowed_keys = {"x", "y", "width", "height"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise CaptureRegionError(f"Unexpected fields in CaptureRegion: {sorted(extra)}")

        missing = allowed_keys - set(data.keys())
        if missing:
            raise CaptureRegionError(f"Missing required fields in CaptureRegion: {sorted(missing)}")

        return cls(
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"],
        )


VALID_CAPTURE_SOURCES = frozenset({"full_screen", "region"})


@dataclass(frozen=True)
class CapturedImage:
    """In-memory captured screen image with secret-safe string representations.

    Raw PNG bytes are excluded from repr/str so screenshots are never logged.
    """

    png_bytes: bytes = field(repr=False)
    width: int
    height: int
    region: CaptureRegion
    source: str = "full_screen"
    captured_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    saved_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.png_bytes, bytes) or len(self.png_bytes) == 0:
            raise ScreenCaptureError("CapturedImage requires non-empty PNG bytes.")
        if not isinstance(self.width, int) or isinstance(self.width, bool) or self.width <= 0:
            raise ScreenCaptureError("CapturedImage width must be a positive integer.")
        if not isinstance(self.height, int) or isinstance(self.height, bool) or self.height <= 0:
            raise ScreenCaptureError("CapturedImage height must be a positive integer.")
        if not isinstance(self.region, CaptureRegion):
            raise ScreenCaptureError("CapturedImage region must be a CaptureRegion instance.")
        if self.source not in VALID_CAPTURE_SOURCES:
            raise ScreenCaptureError(
                f"CapturedImage source must be one of {sorted(VALID_CAPTURE_SOURCES)}, "
                f"got {self.source!r}."
            )
        if not isinstance(self.captured_at, str) or not self.captured_at.strip():
            raise ScreenCaptureError("CapturedImage captured_at must be a non-empty string.")

    def __repr__(self) -> str:
        saved = str(self.saved_path) if self.saved_path is not None else None
        return (
            f"CapturedImage(source={self.source!r}, width={self.width}, height={self.height}, "
            f"byte_size={len(self.png_bytes)}, sha256_prefix={self.sha256_digest[:12]!r}, "
            f"saved_path={saved!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def byte_size(self) -> int:
        """Size of the in-memory PNG payload in bytes."""
        return len(self.png_bytes)

    @property
    def sha256_digest(self) -> str:
        """SHA-256 hex digest of the PNG payload for integrity and consent binding."""
        return hashlib.sha256(self.png_bytes).hexdigest()

    def to_base64(self) -> str:
        """Return base64-encoded ASCII string of the PNG bytes."""
        return base64.b64encode(self.png_bytes).decode("ascii")

    def to_data_url(self) -> str:
        """Return a data URL suitable for multimodal OpenAI-compatible vision payloads."""
        return f"data:image/png;base64,{self.to_base64()}"

    def summary(self) -> dict[str, Any]:
        """Return safe metadata describing the capture without any pixel/byte content."""
        return {
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "byte_size": self.byte_size,
            "region": self.region.to_dict(),
            "captured_at": self.captured_at,
            "sha256": self.sha256_digest,
            "saved_path": str(self.saved_path) if self.saved_path is not None else None,
        }


def encode_qimage_to_png_bytes(image: QImage | QPixmap) -> bytes:
    """Encode a QImage or QPixmap to in-memory PNG bytes without touching disk."""
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise ScreenCaptureError("Cannot encode a null or empty Qt image.")

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ScreenCaptureError("Could not open in-memory QBuffer for PNG encoding.")
    try:
        saved = image.save(buffer, "PNG")
    finally:
        buffer.close()

    if not saved or byte_array.isEmpty():
        raise ScreenCaptureError("Failed to encode captured screen image to PNG.")
    return bytes(byte_array.data())


type ScreenGrabCallable = Callable[
    [CaptureRegion | None],
    tuple[bytes, int, int, CaptureRegion],
]


def grab_screen_with_qt(
    region: CaptureRegion | None = None,
) -> tuple[bytes, int, int, CaptureRegion]:
    """Capture the primary Windows screen or a rectangular region in memory using PySide6."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        raise ScreenCaptureError(
            "No primary screen is available. A Qt GUI application must be active."
        )

    geometry = screen.geometry()
    screen_w = geometry.width()
    screen_h = geometry.height()
    screen_x = max(0, geometry.x())
    screen_y = max(0, geometry.y())

    if region is not None:
        region.validate_within_bounds(
            screen_w,
            screen_h,
            screen_x=screen_x,
            screen_y=screen_y,
        )
        pixmap = screen.grabWindow(0, region.x, region.y, region.width, region.height)
        actual_region = region
    else:
        pixmap = screen.grabWindow(0)
        actual_region = CaptureRegion(
            x=screen_x,
            y=screen_y,
            width=max(1, pixmap.width()),
            height=max(1, pixmap.height()),
        )

    if pixmap.isNull() or pixmap.width() <= 0 or pixmap.height() <= 0:
        raise ScreenCaptureError("Qt screen grab returned an empty or null pixmap.")

    png_bytes = encode_qimage_to_png_bytes(pixmap)
    return png_bytes, pixmap.width(), pixmap.height(), actual_region


class ScreenCapturer:
    """Windows screen and region capturer gated on explicit user actions.

    Screenshots are held exclusively in memory by default and are never logged.
    """

    def __init__(
        self,
        *,
        grabber: ScreenGrabCallable | None = None,
        region_selector: Callable[[], CaptureRegion | None] | None = None,
        save_screenshots: bool = False,
        save_dir: Path | None = None,
    ) -> None:
        self._grabber: ScreenGrabCallable = grabber if grabber is not None else grab_screen_with_qt
        self._region_selector = region_selector
        self._save_screenshots = bool(save_screenshots)
        self._save_dir = Path(save_dir) if save_dir is not None else None

    @property
    def save_screenshots(self) -> bool:
        """Whether screenshots are persisted to disk when save_dir is configured."""
        return self._save_screenshots

    @property
    def save_dir(self) -> Path | None:
        """Optional directory where screenshots may be saved if explicitly enabled."""
        return self._save_dir

    @staticmethod
    def _require_user_action(user_initiated: bool) -> None:
        """Ensure capture starts only from an explicit user action."""
        if not isinstance(user_initiated, bool) or not user_initiated:
            raise UserActionRequiredError(
                "Screen capture starts only from an explicit user action (user_initiated=True)."
            )

    def _maybe_persist(self, png_bytes: bytes, *, persist: bool | None) -> Path | None:
        """Persist screenshot to disk only when explicitly enabled and configured."""
        should_save = self._save_screenshots if persist is None else bool(persist)
        if not should_save:
            return None
        if self._save_dir is None:
            raise ScreenCaptureError(
                "Cannot save screenshot to disk because no save_dir is configured."
            )
        self._save_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        digest_prefix = hashlib.sha256(png_bytes).hexdigest()[:8]
        target_path = self._save_dir / f"octa_capture_{timestamp}_{digest_prefix}.png"
        target_path.write_bytes(png_bytes)
        return target_path

    def capture_full_screen(
        self,
        *,
        user_initiated: bool = False,
        persist: bool | None = None,
    ) -> CapturedImage:
        """Capture the full primary screen after verifying explicit user action."""
        self._require_user_action(user_initiated)
        png_bytes, width, height, actual_region = self._grabber(None)
        saved_path = self._maybe_persist(png_bytes, persist=persist)

        image = CapturedImage(
            png_bytes=png_bytes,
            width=width,
            height=height,
            region=actual_region,
            source="full_screen",
            saved_path=saved_path,
        )
        logger.info(
            "Captured full screen (width=%d, height=%d, bytes=%d, saved=%s).",
            image.width,
            image.height,
            image.byte_size,
            saved_path is not None,
        )
        return image

    def capture_region(
        self,
        region: CaptureRegion,
        *,
        user_initiated: bool = False,
        persist: bool | None = None,
    ) -> CapturedImage:
        """Capture a specific validated screen region after verifying explicit user action."""
        self._require_user_action(user_initiated)
        if not isinstance(region, CaptureRegion):
            raise CaptureRegionError(
                f"Expected CaptureRegion instance, got {type(region).__name__}."
            )

        png_bytes, width, height, actual_region = self._grabber(region)
        saved_path = self._maybe_persist(png_bytes, persist=persist)

        image = CapturedImage(
            png_bytes=png_bytes,
            width=width,
            height=height,
            region=actual_region,
            source="region",
            saved_path=saved_path,
        )
        logger.info(
            "Captured screen region (x=%d, y=%d, width=%d, height=%d, bytes=%d, saved=%s).",
            actual_region.x,
            actual_region.y,
            image.width,
            image.height,
            image.byte_size,
            saved_path is not None,
        )
        return image

    def select_and_capture_region(
        self,
        *,
        user_initiated: bool = False,
        persist: bool | None = None,
    ) -> CapturedImage:
        """Run an interactive region selection and capture the selected region.

        Raises:
            UserActionRequiredError: If user_initiated is not True.
            CaptureCancelledError: If the user cancels region selection.
        """
        self._require_user_action(user_initiated)
        if self._region_selector is None:
            raise CaptureCancelledError("No interactive region selector is configured.")

        selected_region = self._region_selector()
        if selected_region is None:
            logger.info("User cancelled screen region selection.")
            raise CaptureCancelledError("Screen region capture was cancelled by the user.")

        return self.capture_region(
            selected_region,
            user_initiated=True,
            persist=persist,
        )
