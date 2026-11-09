"""Injectable Speech-to-Text (STT), Text-to-Speech (TTS), and audio playback providers.

Provides clean protocol interfaces, safe disabled defaults (never assuming a paid
cloud provider), deterministic mocked providers for tests and local push-to-talk
flows, QtMultimedia in-memory audio playback, and configurable remote HTTPS
speech adapters enforcing explicit single-use consent, no redirects, and strict
log/file privacy for credentials, audio, and transcripts.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from octa.core.config import (
    DEFAULT_VOICE_ALLOW_LOCAL_HTTP,
    DEFAULT_VOICE_STT_MODEL,
    DEFAULT_VOICE_TIMEOUT_SECONDS,
    DEFAULT_VOICE_TTS_VOICE,
    AppConfig,
    is_loopback_host,
)
from octa.intelligence.consent import sanitize_endpoint_for_display
from octa.intelligence.llm import REDIRECT_STATUS_CODES, redact_sensitive_text
from octa.voice.audio import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    RecordedAudio,
    SynthesizedSpeech,
    TranscriptionResult,
    decode_wav_metadata,
    encode_pcm16_to_wav_bytes,
)
from octa.voice.consent import (
    VoiceConsentDecision,
    VoiceConsentManager,
    VoiceConsentRequest,
)
from octa.voice.exceptions import (
    AudioPlaybackError,
    MalformedSpeechResponseError,
    VoiceCancelledError,
    VoiceConfigurationError,
    VoiceConsentDeclinedError,
    VoiceMissingCredentialsError,
    VoiceTransportError,
)

logger = logging.getLogger("octa.voice.providers")


@dataclass(frozen=True)
class VoiceProviderOptionSpec:
    """Metadata describing a supported or candidate voice provider option and its requirements."""

    option_id: str
    config_value: str | None  # OCTA_VOICE_*_PROVIDER value if implemented, else None
    implemented: bool
    category: str  # "built_in", "local_loopback", "remote_https", "candidate_future"
    supports_stt: bool
    supports_tts: bool
    requires_network: bool
    requires_api_key: bool
    extra_dependencies: tuple[str, ...]
    description: str


VOICE_PROVIDER_OPTIONS: tuple[VoiceProviderOptionSpec, ...] = (
    VoiceProviderOptionSpec(
        option_id="disabled",
        config_value="disabled",
        implemented=True,
        category="built_in",
        supports_stt=False,
        supports_tts=False,
        requires_network=False,
        requires_api_key=False,
        extra_dependencies=(),
        description=(
            "Default safe state (OCTA_VOICE_STT_PROVIDER=disabled, "
            "OCTA_VOICE_TTS_PROVIDER=disabled). Clearly indicates in the UI that speech "
            "providers are disabled and never records or pretends to speak."
        ),
    ),
    VoiceProviderOptionSpec(
        option_id="mock",
        config_value="mock",
        implemented=True,
        category="built_in",
        supports_stt=True,
        supports_tts=True,
        requires_network=False,
        requires_api_key=False,
        extra_dependencies=(),
        description=(
            "Built-in deterministic in-memory mock STT/TTS provider (OCTA_VOICE_*_PROVIDER=mock) "
            "for automated tests and offline push-to-talk flow verification."
        ),
    ),
    VoiceProviderOptionSpec(
        option_id="local_loopback_http_server",
        config_value="remote_http",
        implemented=True,
        category="local_loopback",
        supports_stt=True,
        supports_tts=True,
        requires_network=True,
        requires_api_key=False,
        extra_dependencies=(),
        description=(
            "Local OpenAI-compatible speech server on 127.0.0.1 / localhost (e.g., whisper.cpp "
            "server, LocalAI, OpenedAI Speech) configured via OCTA_VOICE_*_PROVIDER=remote_http "
            "and OCTA_VOICE_ALLOW_LOCAL_HTTP=true. Plays WAV via QtAudioPlaybackBackend."
        ),
    ),
    VoiceProviderOptionSpec(
        option_id="remote_https_openai_compatible",
        config_value="remote_http",
        implemented=True,
        category="remote_https",
        supports_stt=True,
        supports_tts=True,
        requires_network=True,
        requires_api_key=True,
        extra_dependencies=(),
        description=(
            "User-configured remote HTTPS STT/TTS endpoint (OCTA_VOICE_*_PROVIDER=remote_http). "
            "Requires HTTPS, blocks redirects, requires explicit per-request user consent and "
            "OCTA_VOICE_*_API_KEY, and plays synthesized WAV via QtAudioPlaybackBackend."
        ),
    ),
    VoiceProviderOptionSpec(
        option_id="faster_whisper_local",
        config_value=None,
        implemented=False,
        category="candidate_future",
        supports_stt=True,
        supports_tts=False,
        requires_network=False,
        requires_api_key=False,
        extra_dependencies=("faster-whisper",),
        description=(
            "Candidate offline local Whisper STT via CTranslate2 (not bundled). Would require "
            "installing 'faster-whisper' and downloading local model weights."
        ),
    ),
    VoiceProviderOptionSpec(
        option_id="vosk_local",
        config_value=None,
        implemented=False,
        category="candidate_future",
        supports_stt=True,
        supports_tts=False,
        requires_network=False,
        requires_api_key=False,
        extra_dependencies=("vosk",),
        description=(
            "Candidate offline CPU speech recognition (not bundled). Would require installing "
            "'vosk' and a local acoustic model directory."
        ),
    ),
)


def validate_voice_endpoint(endpoint: str, *, allow_local_http: bool = False) -> str:
    """Validate a speech provider endpoint URL and enforce HTTPS for all non-loopback hosts.

    Plain HTTP is permitted only when the target host is a loopback address
    (localhost, 127.0.0.0/8, ::1) AND allow_local_http is explicitly True.
    """
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise VoiceConfigurationError("Voice endpoint must be a non-empty URL string.")
    if not isinstance(allow_local_http, bool):
        raise VoiceConfigurationError("allow_local_http must be a boolean.")

    cleaned = endpoint.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise VoiceConfigurationError(
            f"Invalid voice endpoint URL '{redact_sensitive_text(cleaned)}'. "
            "Must use https (or loopback http for local development)."
        )

    if parsed.scheme == "http":
        if not is_loopback_host(parsed.hostname):
            raise VoiceConfigurationError(
                f"Insecure HTTP voice endpoint '{redact_sensitive_text(cleaned)}' is rejected: "
                "remote speech providers must use HTTPS."
            )
        if not allow_local_http:
            raise VoiceConfigurationError(
                f"Local HTTP voice endpoint '{redact_sensitive_text(cleaned)}' is disabled "
                "by default. Set allow_local_http=True (or OCTA_VOICE_ALLOW_LOCAL_HTTP=true) "
                "to permit loopback HTTP for local development."
            )
    return cleaned


class VoiceNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Explicitly reject all HTTP redirects for remote speech provider requests.

    Prevents urllib from forwarding Bearer credentials, microphone audio, or user
    text to a redirected destination without fresh user consent.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise VoiceTransportError(
            f"HTTP {code} redirect from speech provider endpoint is disabled by policy: "
            "refusing to forward credentials, microphone audio, or text to a redirected "
            "destination without fresh user consent."
        )


def build_voice_no_redirect_opener(
    *extra_handlers: urllib.request.BaseHandler,
) -> urllib.request.OpenerDirector:
    """Build a urllib OpenerDirector that blocks all automatic HTTP redirects."""
    return urllib.request.build_opener(*extra_handlers, VoiceNoRedirectHandler())


type VoiceHTTPTransportCallable = Callable[[urllib.request.Request, float], bytes]


def _default_voice_urllib_transport(request: urllib.request.Request, timeout: float) -> bytes:
    """Execute HTTP request via a no-redirect opener, blocking live calls under pytest."""
    if "pytest" in sys.modules:
        raise VoiceTransportError(
            "Live network calls are disabled during automated tests; inject a mock transport."
        )
    opener = build_voice_no_redirect_opener()
    with opener.open(request, timeout=timeout) as response:
        return bytes(response.read())


@dataclass(frozen=True)
class RemoteVoiceConfig:
    """Configuration for a remote or loopback HTTP speech provider.

    Endpoint and API key are excluded from default repr and sanitized in __repr__
    so credentials never leak into logs.
    """

    endpoint: str = field(repr=False)
    model: str = DEFAULT_VOICE_STT_MODEL
    api_key: str | None = field(default=None, repr=False)
    voice_name: str = DEFAULT_VOICE_TTS_VOICE
    timeout_seconds: float = DEFAULT_VOICE_TIMEOUT_SECONDS
    allow_local_http: bool = DEFAULT_VOICE_ALLOW_LOCAL_HTTP
    provider_name: str = "remote_http_voice"

    def __post_init__(self) -> None:
        validated_ep = validate_voice_endpoint(
            self.endpoint,
            allow_local_http=self.allow_local_http,
        )
        object.__setattr__(self, "endpoint", validated_ep)

        if not isinstance(self.model, str) or not self.model.strip():
            raise VoiceConfigurationError("RemoteVoiceConfig model must be a non-empty string.")
        object.__setattr__(self, "model", self.model.strip())

        if not isinstance(self.voice_name, str) or not self.voice_name.strip():
            raise VoiceConfigurationError(
                "RemoteVoiceConfig voice_name must be a non-empty string."
            )
        object.__setattr__(self, "voice_name", self.voice_name.strip())

        if not isinstance(self.provider_name, str) or not self.provider_name.strip():
            raise VoiceConfigurationError(
                "RemoteVoiceConfig provider_name must be a non-empty string."
            )
        object.__setattr__(self, "provider_name", self.provider_name.strip())

        if self.api_key is not None:
            if not isinstance(self.api_key, str):
                raise VoiceConfigurationError("RemoteVoiceConfig api_key must be a string or None.")
            cleaned_key = self.api_key.strip()
            object.__setattr__(self, "api_key", cleaned_key if cleaned_key else None)

        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            raise VoiceConfigurationError(
                "RemoteVoiceConfig timeout_seconds must be a positive number."
            )
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    def __repr__(self) -> str:
        safe_ep = sanitize_endpoint_for_display(self.endpoint)
        return (
            f"RemoteVoiceConfig(provider_name={self.provider_name!r}, "
            f"endpoint={safe_ep!r}, model={self.model!r}, voice_name={self.voice_name!r}, "
            f"has_api_key={self.has_credentials}, timeout_seconds={self.timeout_seconds}, "
            f"allow_local_http={self.allow_local_http})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def has_credentials(self) -> bool:
        """Return True if a non-empty API key is configured."""
        return bool(self.api_key and self.api_key.strip())

    def require_api_key(self) -> str:
        """Return the configured API key or raise VoiceMissingCredentialsError."""
        if not self.api_key or not self.api_key.strip():
            raise VoiceMissingCredentialsError(
                "No voice provider API key configured. Set OCTA_VOICE_STT_API_KEY / "
                "OCTA_VOICE_TTS_API_KEY in the environment or .env file."
            )
        return self.api_key.strip()


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Injectable interface for Speech-to-Text (STT) providers."""

    @property
    def provider_name(self) -> str:
        """Identifier of the STT provider."""

    @property
    def is_remote(self) -> bool:
        """Return True if this provider transmits audio to an external/remote endpoint."""

    def transcribe(
        self,
        audio: RecordedAudio,
        *,
        task_title: str = "Voice Interaction",
        step_title: str = "Push-to-Talk Request",
        consent_decision: VoiceConsentDecision | None = None,
    ) -> TranscriptionResult:
        """Transcribe push-to-talk RecordedAudio into a TranscriptionResult."""

    def cancel(self) -> None:
        """Immediately cancel any pending or active transcription operation."""


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Injectable interface for Text-to-Speech (TTS) providers."""

    @property
    def provider_name(self) -> str:
        """Identifier of the TTS provider."""

    @property
    def is_remote(self) -> bool:
        """Return True if this provider transmits text to an external/remote endpoint."""

    def synthesize(
        self,
        text: str,
        *,
        task_title: str = "Voice Interaction",
        step_title: str = "Read Instruction Aloud",
        consent_decision: VoiceConsentDecision | None = None,
    ) -> SynthesizedSpeech:
        """Synthesize spoken audio in memory from instruction text."""

    def cancel(self) -> None:
        """Immediately cancel any pending or active speech synthesis operation."""


@runtime_checkable
class AudioPlaybackBackend(Protocol):
    """Injectable interface for playing synthesized speech audio in memory."""

    @property
    def is_playing(self) -> bool:
        """Return True while audio is actively playing."""

    def play(self, speech: SynthesizedSpeech) -> None:
        """Play synthesized speech in memory."""

    def stop(self) -> None:
        """Immediately stop audio playback."""


class DisabledSTTProvider:
    """Safe default STT provider when no speech-to-text engine is configured."""

    @property
    def provider_name(self) -> str:
        return "disabled"

    @property
    def is_remote(self) -> bool:
        return False

    def transcribe(
        self,
        audio: RecordedAudio,
        *,
        task_title: str = "Voice Interaction",
        step_title: str = "Push-to-Talk Request",
        consent_decision: VoiceConsentDecision | None = None,
    ) -> TranscriptionResult:
        del audio, task_title, step_title, consent_decision
        raise VoiceConfigurationError(
            "No Speech-to-Text (STT) provider is configured (OCTA_VOICE_STT_PROVIDER=disabled). "
            "Configure a local or remote provider or inject a mock STT provider."
        )

    def cancel(self) -> None:
        pass


class DisabledTTSProvider:
    """Safe default TTS provider when no text-to-speech engine is configured."""

    @property
    def provider_name(self) -> str:
        return "disabled"

    @property
    def is_remote(self) -> bool:
        return False

    def synthesize(
        self,
        text: str,
        *,
        task_title: str = "Voice Interaction",
        step_title: str = "Read Instruction Aloud",
        consent_decision: VoiceConsentDecision | None = None,
    ) -> SynthesizedSpeech:
        del text, task_title, step_title, consent_decision
        raise VoiceConfigurationError(
            "No Text-to-Speech (TTS) provider is configured (OCTA_VOICE_TTS_PROVIDER=disabled). "
            "Configure a local or remote provider or inject a mock TTS provider."
        )

    def cancel(self) -> None:
        pass


class MockSTTProvider:
    """Deterministic in-memory mock STT provider for unit tests and offline verification.

    Never writes audio or transcripts to disk or logs.
    """

    def __init__(
        self,
        *,
        default_transcript: str = "What is the current step?",
        responses: Sequence[str] | None = None,
        responder: Callable[[RecordedAudio], str] | None = None,
        provider_name: str = "mock_stt",
        is_remote: bool = False,
    ) -> None:
        self._default_transcript = default_transcript
        self._responses = list(responses) if responses is not None else []
        self._responder = responder
        self._provider_name = provider_name
        self._is_remote = is_remote
        self._cancelled = False
        self.call_count = 0
        self.cancel_count = 0

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def is_remote(self) -> bool:
        return self._is_remote

    def queue_response(self, transcript: str) -> None:
        """Queue a transcript string to be returned on the next transcribe() call."""
        self._responses.append(transcript)

    def cancel(self) -> None:
        """Cancel pending transcription."""
        self._cancelled = True
        self.cancel_count += 1

    def transcribe(
        self,
        audio: RecordedAudio,
        *,
        task_title: str = "Voice Interaction",
        step_title: str = "Push-to-Talk Request",
        consent_decision: VoiceConsentDecision | None = None,
    ) -> TranscriptionResult:
        del task_title, step_title, consent_decision
        if not isinstance(audio, RecordedAudio):
            raise VoiceConfigurationError("MockSTTProvider requires a RecordedAudio instance.")
        if self._cancelled:
            self._cancelled = False
            raise VoiceCancelledError("Transcription was cancelled.")

        self.call_count += 1
        if self._responder is not None:
            text = self._responder(audio)
        elif self._responses:
            text = self._responses.pop(0)
        else:
            text = self._default_transcript

        if self._cancelled:
            self._cancelled = False
            raise VoiceCancelledError("Transcription was cancelled.")

        logger.info(
            "Completed STT transcription (provider=%s, audio_ms=%d, chars=%d).",
            self._provider_name,
            audio.duration_ms,
            len(text.strip()),
        )
        return TranscriptionResult(
            text=text,
            provider_name=self._provider_name,
            language="en",
            confidence=0.99,
            duration_ms=audio.duration_ms,
        )


class MockTTSProvider:
    """Deterministic in-memory mock TTS provider for unit tests and offline verification.

    Synthesizes valid in-memory WAV bytes without touching disk or logging spoken text.
    """

    def __init__(
        self,
        *,
        provider_name: str = "mock_tts",
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        is_remote: bool = False,
    ) -> None:
        self._provider_name = provider_name
        self._sample_rate = sample_rate
        self._channels = channels
        self._is_remote = is_remote
        self._cancelled = False
        self.call_count = 0
        self.cancel_count = 0
        self.last_char_length = 0

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def is_remote(self) -> bool:
        return self._is_remote

    def cancel(self) -> None:
        """Cancel pending speech synthesis."""
        self._cancelled = True
        self.cancel_count += 1

    def synthesize(
        self,
        text: str,
        *,
        task_title: str = "Voice Interaction",
        step_title: str = "Read Instruction Aloud",
        consent_decision: VoiceConsentDecision | None = None,
    ) -> SynthesizedSpeech:
        del task_title, step_title, consent_decision
        if not isinstance(text, str) or not text.strip():
            raise VoiceConfigurationError("TTS synthesize requires a non-empty text string.")
        if self._cancelled:
            self._cancelled = False
            raise VoiceCancelledError("Speech synthesis was cancelled.")

        cleaned = text.strip()
        self.call_count += 1
        self.last_char_length = len(cleaned)

        # Generate deterministic in-memory PCM16 frames proportional to text length
        duration_ms = max(100, min(2000, len(cleaned) * 15))
        num_frames = max(1, int((self._sample_rate * duration_ms) / 1000))
        pcm_bytes = b"\x08\x00" * (num_frames * self._channels)
        wav_bytes = encode_pcm16_to_wav_bytes(
            pcm_bytes,
            sample_rate=self._sample_rate,
            channels=self._channels,
            sample_width_bytes=2,
        )

        logger.info(
            "Synthesized TTS audio in memory (provider=%s, chars=%d, audio_bytes=%d).",
            self._provider_name,
            len(cleaned),
            len(wav_bytes),
        )
        return SynthesizedSpeech(
            audio_bytes=wav_bytes,
            provider_name=self._provider_name,
            media_format="wav",
            sample_rate=self._sample_rate,
            channels=self._channels,
            duration_ms=duration_ms,
        )


class MockAudioPlaybackBackend:
    """In-memory audio playback backend for tests and headless environments."""

    def __init__(
        self,
        *,
        auto_finish: bool = False,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self._is_playing = False
        self._auto_finish = auto_finish
        self._on_finished = on_finished
        self.play_count = 0
        self.stop_count = 0
        self.last_played_bytes_size = 0
        self.last_speech: SynthesizedSpeech | None = None

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def set_on_finished(self, callback: Callable[[], None] | None) -> None:
        """Register a callback invoked when playback completes naturally."""
        self._on_finished = callback

    def play(self, speech: SynthesizedSpeech) -> None:
        if not isinstance(speech, SynthesizedSpeech):
            raise AudioPlaybackError("Expected a SynthesizedSpeech instance for playback.")
        self.play_count += 1
        self.last_played_bytes_size = speech.byte_size
        self.last_speech = speech
        if self._auto_finish:
            self._is_playing = False
            if self._on_finished is not None:
                self._on_finished()
        else:
            self._is_playing = True

    def finish_playback(self) -> None:
        """Simulate natural completion of audio playback."""
        if self._is_playing:
            self._is_playing = False
            if self._on_finished is not None:
                self._on_finished()

    def stop(self) -> None:
        self._is_playing = False
        self.stop_count += 1


class QtAudioPlaybackBackend:
    """In-memory WAV audio playback backend using PySide6.QtMultimedia (QAudioSink + QBuffer)."""

    def __init__(
        self,
        *,
        device_provider: Callable[[], Any] | None = None,
        sink_factory: Callable[[Any, QAudioFormat], Any] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self._device_provider: Callable[[], Any] = (
            device_provider if device_provider is not None else QMediaDevices.defaultAudioOutput
        )
        self._sink_factory: Callable[[Any, QAudioFormat], Any] = (
            sink_factory if sink_factory is not None else QAudioSink
        )
        self._on_finished = on_finished
        self._audio_sink: Any = None
        self._byte_array: QByteArray | None = None
        self._qbuffer: QBuffer | None = None
        self._is_playing = False
        self._stopping = False

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def set_on_finished(self, callback: Callable[[], None] | None) -> None:
        """Register a callback invoked when WAV buffer playback finishes naturally."""
        self._on_finished = callback

    def play(self, speech: SynthesizedSpeech) -> None:
        if not isinstance(speech, SynthesizedSpeech):
            raise AudioPlaybackError("Expected a SynthesizedSpeech instance for playback.")
        self.stop()

        sample_rate, channels, sample_width, _dur_ms, pcm_frames = decode_wav_metadata(
            speech.audio_bytes
        )
        if sample_width != 2 or not pcm_frames:
            raise AudioPlaybackError("WAV payload must contain non-empty 16-bit PCM audio frames.")

        device = self._device_provider()
        if device is None or (hasattr(device, "isNull") and device.isNull()):
            raise AudioPlaybackError("No audio output device is available for TTS playback.")

        qt_format = QAudioFormat()
        qt_format.setSampleRate(sample_rate)
        qt_format.setChannelCount(channels)
        qt_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        if hasattr(device, "isFormatSupported") and not device.isFormatSupported(qt_format):
            raise AudioPlaybackError(
                f"Audio output device does not support WAV format "
                f"({sample_rate} Hz, {channels} channels, PCM16)."
            )

        self._byte_array = QByteArray(pcm_frames)
        self._qbuffer = QBuffer(self._byte_array)
        if not self._qbuffer.open(QIODevice.OpenModeFlag.ReadOnly):
            self._byte_array = None
            self._qbuffer = None
            raise AudioPlaybackError("Failed to open in-memory QBuffer for audio playback.")

        self._audio_sink = self._sink_factory(device, qt_format)
        state_changed_sig = getattr(self._audio_sink, "stateChanged", None)
        if state_changed_sig is not None and hasattr(state_changed_sig, "connect"):
            state_changed_sig.connect(self._on_sink_state_changed)
        self._is_playing = True
        self._audio_sink.start(self._qbuffer)

    def _on_sink_state_changed(self, state: Any) -> None:
        """Handle QAudioSink state transitions when playback finishes naturally."""
        if self._stopping or not self._is_playing:
            return
        if state in (QAudio.State.IdleState, QAudio.State.StoppedState):
            self.stop()
            if self._on_finished is not None:
                self._on_finished()

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._is_playing = False
        try:
            if self._audio_sink is not None:
                state_changed_sig = getattr(self._audio_sink, "stateChanged", None)
                if state_changed_sig is not None and hasattr(state_changed_sig, "disconnect"):
                    try:
                        state_changed_sig.disconnect(self._on_sink_state_changed)
                    except (RuntimeError, TypeError):
                        pass
                if hasattr(self._audio_sink, "reset"):
                    self._audio_sink.reset()
                if hasattr(self._audio_sink, "stop"):
                    self._audio_sink.stop()
            if self._qbuffer is not None and self._qbuffer.isOpen():
                self._qbuffer.close()
        finally:
            if self._byte_array is not None:
                self._byte_array.clear()
            self._audio_sink = None
            self._qbuffer = None
            self._byte_array = None
            self._stopping = False


class RemoteHTTPSTTProvider:
    """Isolated HTTPS Speech-to-Text provider enforcing consent, no redirects, and log privacy."""

    def __init__(
        self,
        config: RemoteVoiceConfig,
        *,
        transport: VoiceHTTPTransportCallable | None = None,
        consent_manager: VoiceConsentManager | None = None,
    ) -> None:
        if not isinstance(config, RemoteVoiceConfig):
            raise VoiceConfigurationError("RemoteHTTPSTTProvider requires a RemoteVoiceConfig.")
        self._config = config
        self._transport: VoiceHTTPTransportCallable = (
            transport if transport is not None else _default_voice_urllib_transport
        )
        self._consent_manager = consent_manager
        self._cancelled = False

    @property
    def config(self) -> RemoteVoiceConfig:
        return self._config

    @property
    def provider_name(self) -> str:
        return self._config.provider_name

    @property
    def is_remote(self) -> bool:
        return True

    def cancel(self) -> None:
        self._cancelled = True

    def build_consent_request(
        self,
        audio: RecordedAudio,
        *,
        purpose: str = "Transcribe push-to-talk microphone audio",
        task_title: str = "Voice Interaction",
        step_title: str = "Push-to-Talk Request",
    ) -> VoiceConsentRequest:
        """Build the canonical VoiceConsentRequest bound to the exact audio and endpoint."""
        return VoiceConsentRequest.from_audio(
            audio,
            purpose=purpose,
            provider_name=self._config.provider_name,
            endpoint_url=self._config.endpoint,
            model=self._config.model,
            task_title=task_title,
            step_title=step_title,
        )

    def transcribe(
        self,
        audio: RecordedAudio,
        *,
        task_title: str = "Voice Interaction",
        step_title: str = "Push-to-Talk Request",
        consent_decision: VoiceConsentDecision | None = None,
    ) -> TranscriptionResult:
        """Transcribe push-to-talk audio after verifying explicit single-use user consent."""
        if not isinstance(audio, RecordedAudio):
            raise VoiceConfigurationError("Expected a RecordedAudio instance for transcription.")
        if self._cancelled:
            self._cancelled = False
            raise VoiceCancelledError("Transcription was cancelled before transmission.")

        consent_request = self.build_consent_request(
            audio,
            task_title=task_title,
            step_title=step_title,
        )

        # 1. Enforce explicit single-use user consent BEFORE accessing API keys or network data
        if consent_decision is not None:
            consent_decision.validate_for_audio(consent_request, audio, consume=True)
        elif self._consent_manager is not None:
            self._consent_manager.require_audio_consent(consent_request, audio, consume=True)
        else:
            raise VoiceConsentDeclinedError(
                "Explicit user consent is required before sending microphone audio "
                "to a remote speech-to-text provider."
            )

        # 2. Enforce HTTPS for remote hosts (or explicit allow_local_http for loopback)
        validated_endpoint = validate_voice_endpoint(
            self._config.endpoint,
            allow_local_http=self._config.allow_local_http,
        )
        parsed = urlparse(validated_endpoint)
        if parsed.scheme != "https" and not is_loopback_host(parsed.hostname):
            raise VoiceConfigurationError(
                "Refusing to send bearer API key or microphone audio to a non-HTTPS remote host."
            )

        api_key = self._config.require_api_key()

        # 3. Build JSON payload in memory (audio base64 never logged)
        payload = {
            "model": self._config.model,
            "audio_format": "wav",
            "audio_base64": base64.b64encode(audio.wav_bytes).decode("ascii"),
        }
        body_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        req = urllib.request.Request(
            url=validated_endpoint,
            data=body_bytes,
            headers=headers,
            method="POST",
        )

        safe_endpoint = sanitize_endpoint_for_display(validated_endpoint)
        logger.info(
            "Sending STT request (provider=%s, endpoint=%s, model=%s, audio_bytes=%d).",
            self._config.provider_name,
            safe_endpoint,
            self._config.model,
            audio.byte_size,
        )

        raw_response = _execute_voice_http_request(
            self._transport,
            req,
            timeout=self._config.timeout_seconds,
            safe_endpoint=safe_endpoint,
            api_key=api_key,
        )

        if self._cancelled:
            self._cancelled = False
            raise VoiceCancelledError("Transcription was cancelled.")

        return self._parse_stt_response(raw_response, duration_ms=audio.duration_ms)

    def _parse_stt_response(self, raw_bytes: bytes, *, duration_ms: int) -> TranscriptionResult:
        if not isinstance(raw_bytes, (bytes, bytearray)) or not bytes(raw_bytes).strip():
            raise MalformedSpeechResponseError("STT endpoint returned an empty HTTP response.")
        try:
            data = json.loads(bytes(raw_bytes).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MalformedSpeechResponseError(
                "STT endpoint returned invalid JSON response."
            ) from exc

        if not isinstance(data, dict):
            raise MalformedSpeechResponseError("STT response root must be a JSON object.")

        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise MalformedSpeechResponseError("STT response is missing a non-empty 'text' field.")
        language = data.get("language", "en")
        if not isinstance(language, str) or not language.strip():
            language = "en"
        confidence = data.get("confidence", 1.0)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or confidence < 0.0
            or confidence > 1.0
        ):
            confidence = 1.0

        return TranscriptionResult(
            text=text.strip(),
            provider_name=self._config.provider_name,
            language=language.strip(),
            confidence=float(confidence),
            duration_ms=duration_ms,
        )


class RemoteHTTPTTSProvider:
    """Isolated HTTPS Text-to-Speech provider enforcing consent, no redirects, and log privacy."""

    def __init__(
        self,
        config: RemoteVoiceConfig,
        *,
        transport: VoiceHTTPTransportCallable | None = None,
        consent_manager: VoiceConsentManager | None = None,
    ) -> None:
        if not isinstance(config, RemoteVoiceConfig):
            raise VoiceConfigurationError("RemoteHTTPTTSProvider requires a RemoteVoiceConfig.")
        self._config = config
        self._transport: VoiceHTTPTransportCallable = (
            transport if transport is not None else _default_voice_urllib_transport
        )
        self._consent_manager = consent_manager
        self._cancelled = False

    @property
    def config(self) -> RemoteVoiceConfig:
        return self._config

    @property
    def provider_name(self) -> str:
        return self._config.provider_name

    @property
    def is_remote(self) -> bool:
        return True

    def cancel(self) -> None:
        self._cancelled = True

    def build_consent_request(
        self,
        text: str,
        *,
        purpose: str = "Synthesize spoken instruction audio",
        task_title: str = "Voice Interaction",
        step_title: str = "Read Instruction Aloud",
    ) -> VoiceConsentRequest:
        """Build the canonical VoiceConsentRequest bound to the exact text and endpoint."""
        return VoiceConsentRequest.from_text(
            text,
            purpose=purpose,
            provider_name=self._config.provider_name,
            endpoint_url=self._config.endpoint,
            model=self._config.model,
            voice_name=self._config.voice_name,
            task_title=task_title,
            step_title=step_title,
        )

    def synthesize(
        self,
        text: str,
        *,
        task_title: str = "Voice Interaction",
        step_title: str = "Read Instruction Aloud",
        consent_decision: VoiceConsentDecision | None = None,
    ) -> SynthesizedSpeech:
        """Synthesize spoken audio after verifying explicit single-use user consent."""
        if not isinstance(text, str) or not text.strip():
            raise VoiceConfigurationError("TTS synthesize requires a non-empty text string.")
        if self._cancelled:
            self._cancelled = False
            raise VoiceCancelledError("Speech synthesis was cancelled before transmission.")

        cleaned_text = text.strip()
        consent_request = self.build_consent_request(
            cleaned_text,
            task_title=task_title,
            step_title=step_title,
        )

        # 1. Enforce explicit single-use user consent BEFORE accessing API keys or network data
        if consent_decision is not None:
            consent_decision.validate_for_text(consent_request, cleaned_text, consume=True)
        elif self._consent_manager is not None:
            self._consent_manager.require_text_consent(consent_request, cleaned_text, consume=True)
        else:
            raise VoiceConsentDeclinedError(
                "Explicit user consent is required before sending text "
                "to a remote text-to-speech provider."
            )

        # 2. Enforce HTTPS for remote hosts
        validated_endpoint = validate_voice_endpoint(
            self._config.endpoint,
            allow_local_http=self._config.allow_local_http,
        )
        parsed = urlparse(validated_endpoint)
        if parsed.scheme != "https" and not is_loopback_host(parsed.hostname):
            raise VoiceConfigurationError(
                "Refusing to send bearer API key or text to a non-HTTPS remote host."
            )

        api_key = self._config.require_api_key()

        payload = {
            "model": self._config.model,
            "input": cleaned_text,
            "voice": self._config.voice_name,
            "response_format": "wav",
        }
        body_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "audio/wav, application/json",
            "Authorization": f"Bearer {api_key}",
        }
        req = urllib.request.Request(
            url=validated_endpoint,
            data=body_bytes,
            headers=headers,
            method="POST",
        )

        safe_endpoint = sanitize_endpoint_for_display(validated_endpoint)
        logger.info(
            "Sending TTS request (provider=%s, endpoint=%s, model=%s, voice=%s, chars=%d).",
            self._config.provider_name,
            safe_endpoint,
            self._config.model,
            self._config.voice_name,
            len(cleaned_text),
        )

        raw_response = _execute_voice_http_request(
            self._transport,
            req,
            timeout=self._config.timeout_seconds,
            safe_endpoint=safe_endpoint,
            api_key=api_key,
        )

        if self._cancelled:
            self._cancelled = False
            raise VoiceCancelledError("Speech synthesis was cancelled.")

        if not isinstance(raw_response, (bytes, bytearray)) or len(raw_response) == 0:
            raise MalformedSpeechResponseError("TTS endpoint returned an empty audio payload.")

        audio_bytes = bytes(raw_response)
        sample_rate, channels, _width, duration_ms, _pcm = decode_wav_metadata(audio_bytes)
        return SynthesizedSpeech(
            audio_bytes=audio_bytes,
            provider_name=self._config.provider_name,
            media_format="wav",
            sample_rate=sample_rate,
            channels=channels,
            duration_ms=duration_ms,
        )


def _execute_voice_http_request(
    transport: VoiceHTTPTransportCallable,
    request: urllib.request.Request,
    *,
    timeout: float,
    safe_endpoint: str,
    api_key: str,
) -> bytes:
    """Execute HTTP request via transport, blocking redirects and redacting sensitive errors."""
    try:
        return transport(request, timeout)
    except VoiceTransportError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in REDIRECT_STATUS_CODES:
            msg = (
                f"HTTP {exc.code} redirect from {safe_endpoint} is disabled by policy: "
                "refusing to forward credentials, microphone audio, or text to a "
                "redirected destination without fresh user consent."
            )
            logger.error(msg)
            raise VoiceTransportError(msg) from None
        safe_reason = redact_sensitive_text(str(exc.reason), api_key)
        msg = f"Speech provider HTTP error {exc.code} from {safe_endpoint}: {safe_reason}"
        logger.error(msg)
        raise VoiceTransportError(msg) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        safe_err = redact_sensitive_text(str(exc), api_key)
        msg = f"Speech provider transport failure communicating with {safe_endpoint}: {safe_err}"
        logger.error(msg)
        raise VoiceTransportError(msg) from None
    except Exception as exc:
        safe_err = redact_sensitive_text(str(exc), api_key)
        msg = f"Unexpected error during speech provider transport: {safe_err}"
        logger.error(msg)
        raise VoiceTransportError(msg) from None


def create_stt_provider(
    config: AppConfig | None = None,
    *,
    consent_manager: VoiceConsentManager | None = None,
    transport: VoiceHTTPTransportCallable | None = None,
) -> SpeechToTextProvider:
    """Instantiate the configured STT provider without assuming a paid cloud service."""
    resolved = config if config is not None else AppConfig.load()
    mode = resolved.voice_stt_provider
    if mode == "disabled":
        return DisabledSTTProvider()
    if mode == "mock":
        return MockSTTProvider(provider_name="mock_stt")
    if mode == "remote_http":
        if not resolved.voice_stt_endpoint:
            raise VoiceConfigurationError(
                "voice_stt_provider='remote_http' requires OCTA_VOICE_STT_ENDPOINT to be set."
            )
        remote_cfg = RemoteVoiceConfig(
            endpoint=resolved.voice_stt_endpoint,
            model=resolved.voice_stt_model,
            api_key=resolved.voice_stt_api_key,
            timeout_seconds=resolved.voice_timeout_seconds,
            allow_local_http=resolved.voice_allow_local_http,
            provider_name="remote_http_stt",
        )
        return RemoteHTTPSTTProvider(
            remote_cfg,
            transport=transport,
            consent_manager=consent_manager,
        )
    raise VoiceConfigurationError(f"Unsupported voice_stt_provider '{mode}'.")


def create_tts_provider(
    config: AppConfig | None = None,
    *,
    consent_manager: VoiceConsentManager | None = None,
    transport: VoiceHTTPTransportCallable | None = None,
) -> TextToSpeechProvider:
    """Instantiate the configured TTS provider without assuming a paid cloud service."""
    resolved = config if config is not None else AppConfig.load()
    mode = resolved.voice_tts_provider
    if mode == "disabled":
        return DisabledTTSProvider()
    if mode == "mock":
        return MockTTSProvider(provider_name="mock_tts")
    if mode == "remote_http":
        if not resolved.voice_tts_endpoint:
            raise VoiceConfigurationError(
                "voice_tts_provider='remote_http' requires OCTA_VOICE_TTS_ENDPOINT to be set."
            )
        remote_cfg = RemoteVoiceConfig(
            endpoint=resolved.voice_tts_endpoint,
            model=resolved.voice_tts_model,
            voice_name=resolved.voice_tts_voice,
            api_key=resolved.voice_tts_api_key,
            timeout_seconds=resolved.voice_timeout_seconds,
            allow_local_http=resolved.voice_allow_local_http,
            provider_name="remote_http_tts",
        )
        return RemoteHTTPTTSProvider(
            remote_cfg,
            transport=transport,
            consent_manager=consent_manager,
        )
    raise VoiceConfigurationError(f"Unsupported voice_tts_provider '{mode}'.")


def create_playback_backend(
    config: AppConfig | None = None,
    *,
    device_provider: Callable[[], Any] | None = None,
    sink_factory: Callable[[Any, QAudioFormat], Any] | None = None,
) -> AudioPlaybackBackend:
    """Create the production QtMultimedia WAV playback backend."""
    del config
    return QtAudioPlaybackBackend(
        device_provider=device_provider,
        sink_factory=sink_factory,
    )
