"""Exception hierarchy for the OCTA Phase 5.0 Voice Interaction subsystem."""

from __future__ import annotations


class VoiceError(Exception):
    """Base exception for all voice interaction, microphone, consent, and speech provider errors."""


class MicrophoneCaptureError(VoiceError):
    """Base exception for push-to-talk microphone capture errors."""


class PushToTalkRequiredError(MicrophoneCaptureError):
    """Raised when microphone recording is invoked without an explicit push-to-talk user action."""


class ContinuousRecordingForbiddenError(MicrophoneCaptureError):
    """Raised when continuous recording or wake-word activation is attempted."""


class VoiceCancelledError(VoiceError):
    """Raised when push-to-talk capture, transcription, or speech synthesis is cancelled/stopped."""


class VoiceConsentError(VoiceError):
    """Base exception for remote speech provider consent errors."""


class VoiceConsentDeclinedError(VoiceConsentError):
    """Raised when the user declines sending audio or text to a remote speech provider."""


class SpeechProviderError(VoiceError):
    """Base exception for speech-to-text (STT) and text-to-speech (TTS) provider errors."""


class VoiceConfigurationError(SpeechProviderError, ValueError):
    """Raised when voice provider settings, endpoints, or audio parameters are invalid."""


class VoiceMissingCredentialsError(SpeechProviderError):
    """Raised when a remote speech provider request is attempted without a configured API key."""


class VoiceTransportError(SpeechProviderError):
    """Raised when an HTTP, redirect, network, or timeout failure occurs in a speech provider."""


class MalformedSpeechResponseError(SpeechProviderError):
    """Raised when a remote speech provider returns an empty or malformed response."""


class AudioPlaybackError(VoiceError):
    """Raised when in-memory audio playback fails."""
