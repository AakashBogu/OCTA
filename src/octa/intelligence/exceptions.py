"""Exception hierarchy for the OCTA Phase 4 Intelligence Layer."""

from __future__ import annotations


class IntelligenceError(Exception):
    """Base exception for all screen capture, task, consent, LLM, and overlay errors."""


class ScreenCaptureError(IntelligenceError):
    """Base exception for screen and region capture errors."""


class UserActionRequiredError(ScreenCaptureError):
    """Raised when screen capture is invoked without an explicit user action."""


class CaptureCancelledError(ScreenCaptureError):
    """Raised when the user cancels an interactive screen region selection."""


class CaptureRegionError(ScreenCaptureError, ValueError):
    """Raised when capture region coordinates or dimensions are invalid or out of bounds."""


class TaskValidationError(IntelligenceError, ValueError):
    """Raised when task definitions, steps, expected outcomes, or progress results are invalid."""


class ConsentError(IntelligenceError):
    """Base exception for remote transmission consent errors."""


class ConsentDeclinedError(ConsentError):
    """Raised when the user cancels or declines sending captured screen content."""


class LLMError(IntelligenceError):
    """Base exception for OpenAI-compatible LLM adapter errors."""


class MissingCredentialsError(LLMError):
    """Raised when an LLM request is attempted without a configured API key."""


class LLMTransportError(LLMError):
    """Raised when an HTTP, network, or timeout failure occurs during an LLM request."""


class MalformedModelOutputError(LLMError):
    """Raised when the LLM returns malformed JSON or violates the expected response schema."""


class OverlayError(IntelligenceError):
    """Base exception for Qt visual overlay errors."""


class InvalidOverlayCoordinatesError(OverlayError, ValueError):
    """Raised when overlay highlight or arrow coordinates are invalid or out of screen bounds."""
