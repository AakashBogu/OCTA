"""In-memory push-to-talk audio capture, WAV encoding, and privacy-safe voice data models.

Enforces explicit push-to-talk activation (never continuous or wake-word recording),
keeps recorded microphone audio, synthesized speech, and transcripts exclusively in
memory by default, and guarantees raw audio bytes and transcript text never appear
in repr, str, or logs.
"""

from __future__ import annotations

import hashlib
import io
import logging
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from octa.voice.exceptions import (
    ContinuousRecordingForbiddenError,
    MicrophoneCaptureError,
    PushToTalkRequiredError,
    VoiceCancelledError,
)

logger = logging.getLogger("octa.voice.audio")

VALID_RECORDING_SOURCES = frozenset({"push_to_talk"})
FORBIDDEN_RECORDING_SOURCES = frozenset({"continuous", "wake_word", "always_on", "background"})
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_SAMPLE_WIDTH_BYTES = 2  # 16-bit signed PCM


def _require_positive_int(value: Any, field_name: str) -> int:
    """Validate that value is a strictly positive integer (not bool)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MicrophoneCaptureError(
            f"Field '{field_name}' must be a positive integer, got {value!r}."
        )
    return value


def _require_non_negative_int(value: Any, field_name: str) -> int:
    """Validate that value is a non-negative integer (not bool)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MicrophoneCaptureError(
            f"Field '{field_name}' must be a non-negative integer, got {value!r}."
        )
    return value


@dataclass(frozen=True)
class AudioFormatSpec:
    """Validated PCM audio format specification for push-to-talk capture and playback."""

    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    sample_width_bytes: int = DEFAULT_SAMPLE_WIDTH_BYTES

    def __post_init__(self) -> None:
        _require_positive_int(self.sample_rate, "sample_rate")
        _require_positive_int(self.channels, "channels")
        if self.channels > 2:
            raise MicrophoneCaptureError("AudioFormatSpec channels must be 1 (mono) or 2 (stereo).")
        if self.sample_width_bytes not in {1, 2, 4}:
            raise MicrophoneCaptureError("AudioFormatSpec sample_width_bytes must be 1, 2, or 4.")

    @property
    def bytes_per_second(self) -> int:
        """Byte rate per second of uncompressed PCM audio."""
        return self.sample_rate * self.channels * self.sample_width_bytes


def encode_pcm16_to_wav_bytes(
    pcm_bytes: bytes,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    sample_width_bytes: int = DEFAULT_SAMPLE_WIDTH_BYTES,
) -> bytes:
    """Encode raw PCM frames into an in-memory RIFF WAV byte buffer without touching disk."""
    if not isinstance(pcm_bytes, (bytes, bytearray)):
        raise MicrophoneCaptureError("PCM audio data must be bytes.")
    raw = bytes(pcm_bytes)
    frame_size = channels * sample_width_bytes
    if frame_size <= 0:
        raise MicrophoneCaptureError("Invalid audio frame size.")
    if len(raw) == 0:
        raise MicrophoneCaptureError("Cannot encode empty PCM audio buffer.")
    # Trim any trailing partial sample frame
    usable_len = (len(raw) // frame_size) * frame_size
    if usable_len == 0:
        raise MicrophoneCaptureError("PCM buffer contains fewer bytes than a single audio frame.")
    raw = raw[:usable_len]

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width_bytes)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw)
    return buf.getvalue()


def decode_wav_metadata(wav_bytes: bytes) -> tuple[int, int, int, int, bytes]:
    """Decode WAV metadata and PCM frames in memory.

    Returns:
        tuple of (sample_rate, channels, sample_width_bytes, duration_ms, pcm_frames)
    """
    if not isinstance(wav_bytes, (bytes, bytearray)) or len(wav_bytes) == 0:
        raise MicrophoneCaptureError("WAV byte buffer must be non-empty.")
    try:
        with wave.open(io.BytesIO(bytes(wav_bytes)), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            n_frames = wav_file.getnframes()
            pcm_frames = wav_file.readframes(n_frames)
    except (wave.Error, EOFError) as exc:
        raise MicrophoneCaptureError(f"Invalid WAV audio data: {exc}") from exc

    if sample_rate <= 0 or channels <= 0 or sample_width <= 0:
        raise MicrophoneCaptureError("WAV header contains non-positive audio format parameters.")
    duration_ms = max(1, int(round((n_frames / sample_rate) * 1000.0))) if n_frames > 0 else 0
    return sample_rate, channels, sample_width, duration_ms, pcm_frames


@dataclass(frozen=True)
class RecordedAudio:
    """In-memory recorded push-to-talk audio with privacy-safe string representations.

    Raw WAV bytes are excluded from repr and str so audio payloads never leak into logs.
    """

    wav_bytes: bytes = field(repr=False)
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    duration_ms: int = 0
    source: str = "push_to_talk"
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    saved_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.wav_bytes, bytes) or len(self.wav_bytes) == 0:
            raise MicrophoneCaptureError("RecordedAudio requires non-empty WAV bytes.")
        _require_positive_int(self.sample_rate, "sample_rate")
        _require_positive_int(self.channels, "channels")
        _require_non_negative_int(self.duration_ms, "duration_ms")

        if not isinstance(self.source, str) or not self.source.strip():
            raise MicrophoneCaptureError("RecordedAudio source must be a non-empty string.")
        normalized_source = self.source.strip().lower()
        if normalized_source in FORBIDDEN_RECORDING_SOURCES:
            raise ContinuousRecordingForbiddenError(
                f"Recording source '{self.source}' is forbidden by policy. "
                "OCTA allows only explicit 'push_to_talk' microphone recording."
            )
        if normalized_source not in VALID_RECORDING_SOURCES:
            raise MicrophoneCaptureError(
                f"RecordedAudio source must be 'push_to_talk', got {self.source!r}."
            )
        object.__setattr__(self, "source", normalized_source)

        if not isinstance(self.recorded_at, str) or not self.recorded_at.strip():
            raise MicrophoneCaptureError("RecordedAudio recorded_at must be a non-empty string.")

    def __repr__(self) -> str:
        saved = str(self.saved_path) if self.saved_path is not None else None
        return (
            f"RecordedAudio(source={self.source!r}, sample_rate={self.sample_rate}, "
            f"channels={self.channels}, duration_ms={self.duration_ms}, "
            f"byte_size={len(self.wav_bytes)}, saved_path={saved!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def byte_size(self) -> int:
        """Size of the in-memory WAV audio payload in bytes."""
        return len(self.wav_bytes)

    @property
    def sha256_digest(self) -> str:
        """Internal SHA-256 hex digest of the WAV bytes for integrity and consent binding."""
        return hashlib.sha256(self.wav_bytes).hexdigest()


@dataclass(frozen=True)
class TranscriptionResult:
    """In-memory speech-to-text transcription result.

    The transcript text is excluded from repr and str so user speech transcripts
    are never written to logs or error traces.
    """

    text: str = field(repr=False)
    provider_name: str = "mock_stt"
    language: str = "en"
    confidence: float = 1.0
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise MicrophoneCaptureError("TranscriptionResult text must be a non-empty string.")
        object.__setattr__(self, "text", self.text.strip())

        if not isinstance(self.provider_name, str) or not self.provider_name.strip():
            raise MicrophoneCaptureError("TranscriptionResult provider_name must be non-empty.")
        object.__setattr__(self, "provider_name", self.provider_name.strip())

        if not isinstance(self.language, str) or not self.language.strip():
            raise MicrophoneCaptureError("TranscriptionResult language must be non-empty.")
        object.__setattr__(self, "language", self.language.strip())

        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or self.confidence < 0.0
            or self.confidence > 1.0
        ):
            raise MicrophoneCaptureError(
                "TranscriptionResult confidence must be between 0.0 and 1.0."
            )
        object.__setattr__(self, "confidence", float(self.confidence))
        _require_non_negative_int(self.duration_ms, "duration_ms")

    def __repr__(self) -> str:
        return (
            f"TranscriptionResult(provider_name={self.provider_name!r}, "
            f"language={self.language!r}, confidence={self.confidence:.2f}, "
            f"char_length={self.char_length}, duration_ms={self.duration_ms})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def char_length(self) -> int:
        """Number of characters in the transcript without exposing the text."""
        return len(self.text)

    @property
    def text_sha256(self) -> str:
        """Internal SHA-256 hex digest of the UTF-8 transcript text."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SynthesizedSpeech:
    """In-memory synthesized text-to-speech audio buffer.

    Raw audio bytes are excluded from repr and str so audio payloads are never logged.
    """

    audio_bytes: bytes = field(repr=False)
    provider_name: str = "mock_tts"
    media_format: str = "wav"
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    duration_ms: int = 0
    saved_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.audio_bytes, bytes) or len(self.audio_bytes) == 0:
            raise MicrophoneCaptureError("SynthesizedSpeech requires non-empty audio_bytes.")
        if not isinstance(self.provider_name, str) or not self.provider_name.strip():
            raise MicrophoneCaptureError("SynthesizedSpeech provider_name must be non-empty.")
        object.__setattr__(self, "provider_name", self.provider_name.strip())
        if not isinstance(self.media_format, str) or not self.media_format.strip():
            raise MicrophoneCaptureError("SynthesizedSpeech media_format must be non-empty.")
        object.__setattr__(self, "media_format", self.media_format.strip().lower())
        _require_positive_int(self.sample_rate, "sample_rate")
        _require_positive_int(self.channels, "channels")
        _require_non_negative_int(self.duration_ms, "duration_ms")

    def __repr__(self) -> str:
        saved = str(self.saved_path) if self.saved_path is not None else None
        return (
            f"SynthesizedSpeech(provider_name={self.provider_name!r}, "
            f"media_format={self.media_format!r}, sample_rate={self.sample_rate}, "
            f"channels={self.channels}, duration_ms={self.duration_ms}, "
            f"byte_size={len(self.audio_bytes)}, saved_path={saved!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @property
    def byte_size(self) -> int:
        """Size of the synthesized audio payload in bytes."""
        return len(self.audio_bytes)

    @property
    def sha256_digest(self) -> str:
        """Internal SHA-256 hex digest of the synthesized audio bytes."""
        return hashlib.sha256(self.audio_bytes).hexdigest()


@runtime_checkable
class AudioCaptureBackend(Protocol):
    """Injectable low-level microphone capture backend interface."""

    @property
    def is_active(self) -> bool:
        """Return True while microphone capture is actively recording."""

    @property
    def format_spec(self) -> AudioFormatSpec:
        """Audio format produced by this capture backend."""

    def start(self) -> None:
        """Begin capturing PCM audio into an in-memory buffer."""

    def stop(self) -> bytes:
        """Stop capturing immediately and return captured raw PCM bytes."""

    def cancel(self) -> None:
        """Immediately stop capturing and discard any buffered audio bytes."""


class MockAudioCaptureBackend:
    """Deterministic in-memory audio capture backend for tests and offline verification."""

    def __init__(
        self,
        *,
        format_spec: AudioFormatSpec | None = None,
        canned_pcm_bytes: bytes | None = None,
        simulated_duration_ms: int = 250,
    ) -> None:
        self._format_spec = format_spec if format_spec is not None else AudioFormatSpec()
        self._is_active = False
        self.start_count = 0
        self.stop_count = 0
        self.cancel_count = 0

        if canned_pcm_bytes is not None:
            self._canned_pcm = bytes(canned_pcm_bytes)
        else:
            num_frames = max(1, int((self._format_spec.sample_rate * simulated_duration_ms) / 1000))
            frame_bytes = self._format_spec.channels * self._format_spec.sample_width_bytes
            self._canned_pcm = b"\x10\x00" * (num_frames * (frame_bytes // 2 or 1))
        self._buffer = bytearray()

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def format_spec(self) -> AudioFormatSpec:
        return self._format_spec

    def start(self) -> None:
        if self._is_active:
            raise MicrophoneCaptureError("Microphone capture backend is already active.")
        self._is_active = True
        self.start_count += 1
        self._buffer = bytearray(self._canned_pcm)

    def stop(self) -> bytes:
        if not self._is_active:
            raise MicrophoneCaptureError("Microphone capture backend is not active.")
        self._is_active = False
        self.stop_count += 1
        captured = bytes(self._buffer)
        self._buffer.clear()
        return captured

    def cancel(self) -> None:
        self._is_active = False
        self.cancel_count += 1
        self._buffer.clear()


class QtAudioCaptureBackend:
    """In-memory microphone capture backend using PySide6.QtMultimedia (QAudioSource + QBuffer).

    Captures PCM16 audio strictly into a QBuffer in RAM without touching disk.
    """

    def __init__(
        self,
        *,
        format_spec: AudioFormatSpec | None = None,
    ) -> None:
        self._format_spec = format_spec if format_spec is not None else AudioFormatSpec()
        self._audio_source: QAudioSource | None = None
        self._byte_array: QByteArray | None = None
        self._qbuffer: QBuffer | None = None
        self._is_active = False

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def format_spec(self) -> AudioFormatSpec:
        return self._format_spec

    def start(self) -> None:
        if self._is_active:
            raise MicrophoneCaptureError("Qt microphone capture is already active.")

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise MicrophoneCaptureError("No audio input (microphone) device is available.")

        qt_format = QAudioFormat()
        qt_format.setSampleRate(self._format_spec.sample_rate)
        qt_format.setChannelCount(self._format_spec.channels)
        qt_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        if not device.isFormatSupported(qt_format):
            preferred = device.preferredFormat()
            preferred.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if preferred.sampleRate() > 0 and preferred.channelCount() in {1, 2}:
                qt_format = preferred
                self._format_spec = AudioFormatSpec(
                    sample_rate=qt_format.sampleRate(),
                    channels=qt_format.channelCount(),
                    sample_width_bytes=2,
                )

        self._byte_array = QByteArray()
        self._qbuffer = QBuffer(self._byte_array)
        if not self._qbuffer.open(QIODevice.OpenModeFlag.WriteOnly):
            self._byte_array = None
            self._qbuffer = None
            raise MicrophoneCaptureError("Failed to open in-memory QBuffer for microphone capture.")

        self._audio_source = QAudioSource(device, qt_format)
        self._audio_source.start(self._qbuffer)
        self._is_active = True

    def stop(self) -> bytes:
        if not self._is_active:
            raise MicrophoneCaptureError("Qt microphone capture is not active.")
        self._is_active = False
        try:
            if self._audio_source is not None:
                self._audio_source.stop()
            if self._qbuffer is not None and self._qbuffer.isOpen():
                self._qbuffer.close()
            pcm_bytes = bytes(self._byte_array.data()) if self._byte_array is not None else b""
        finally:
            if self._byte_array is not None:
                self._byte_array.clear()
            self._audio_source = None
            self._qbuffer = None
            self._byte_array = None
        return pcm_bytes

    def cancel(self) -> None:
        self._is_active = False
        try:
            if self._audio_source is not None:
                self._audio_source.reset()
                self._audio_source.stop()
            if self._qbuffer is not None and self._qbuffer.isOpen():
                self._qbuffer.close()
        finally:
            if self._byte_array is not None:
                self._byte_array.clear()
            self._audio_source = None
            self._qbuffer = None
            self._byte_array = None


class PushToTalkRecorder:
    """Safe push-to-talk microphone recorder that never records continuously or saves audio.

    Requires explicit user action (user_initiated=True), supports immediate cancel/stop,
    rejects continuous or wake-word modes, and holds WAV audio in memory by default.
    """

    def __init__(
        self,
        *,
        backend: AudioCaptureBackend | None = None,
        save_audio: bool = False,
        save_dir: Path | None = None,
        max_recording_seconds: float = 30.0,
    ) -> None:
        if (
            isinstance(max_recording_seconds, bool)
            or not isinstance(max_recording_seconds, (int, float))
            or max_recording_seconds <= 0
        ):
            raise MicrophoneCaptureError("max_recording_seconds must be a positive number.")
        self._backend: AudioCaptureBackend = (
            backend if backend is not None else MockAudioCaptureBackend()
        )
        self._save_audio = bool(save_audio)
        self._save_dir = Path(save_dir) if save_dir is not None else None
        self._max_recording_seconds = float(max_recording_seconds)

    @property
    def is_recording(self) -> bool:
        """Return True while push-to-talk recording is active."""
        return self._backend.is_active

    @property
    def save_audio(self) -> bool:
        """Whether recorded audio is persisted to disk (False by default)."""
        return self._save_audio

    @property
    def max_recording_seconds(self) -> float:
        """Maximum allowed push-to-talk recording duration in seconds."""
        return self._max_recording_seconds

    @staticmethod
    def _require_push_to_talk(user_initiated: bool, mode: str) -> None:
        """Verify explicit push-to-talk user initiation and forbid continuous/wake-word modes."""
        if not isinstance(mode, str) or mode.strip().lower() != "push_to_talk":
            raise ContinuousRecordingForbiddenError(
                f"Recording mode '{mode}' is forbidden. Continuous recording and wake-word "
                "detection are disabled; only explicit 'push_to_talk' is permitted."
            )
        if not isinstance(user_initiated, bool) or not user_initiated:
            raise PushToTalkRequiredError(
                "Microphone recording starts only from an explicit push-to-talk user action "
                "(user_initiated=True)."
            )

    def start_recording(
        self,
        *,
        user_initiated: bool = False,
        mode: str = "push_to_talk",
    ) -> None:
        """Start push-to-talk microphone capture in memory."""
        self._require_push_to_talk(user_initiated, mode)
        if self._backend.is_active:
            raise MicrophoneCaptureError("Push-to-talk recording is already active.")
        self._backend.start()
        logger.info("Push-to-talk microphone recording started (in-memory only).")

    def stop_recording(self) -> RecordedAudio:
        """Stop push-to-talk capture immediately and encode in-memory WAV bytes."""
        if not self._backend.is_active:
            raise MicrophoneCaptureError("Cannot stop recording: microphone is not active.")

        spec = self._backend.format_spec
        pcm_bytes = self._backend.stop()
        if not pcm_bytes:
            raise VoiceCancelledError("Microphone capture produced no audio frames.")

        # Enforce max_recording_seconds cap on buffered frames
        max_bytes = int(spec.bytes_per_second * self._max_recording_seconds)
        frame_size = spec.channels * spec.sample_width_bytes
        max_aligned_bytes = max(frame_size, (max_bytes // frame_size) * frame_size)
        if len(pcm_bytes) > max_aligned_bytes:
            pcm_bytes = pcm_bytes[:max_aligned_bytes]

        wav_bytes = encode_pcm16_to_wav_bytes(
            pcm_bytes,
            sample_rate=spec.sample_rate,
            channels=spec.channels,
            sample_width_bytes=spec.sample_width_bytes,
        )
        duration_ms = max(1, int(round((len(pcm_bytes) / spec.bytes_per_second) * 1000.0)))

        # Never save audio by default (even if save_audio=False)
        saved_path: Path | None = None
        if self._save_audio:
            if self._save_dir is None:
                raise MicrophoneCaptureError(
                    "Cannot persist audio because no save_dir is configured."
                )
            self._save_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
            target = self._save_dir / f"octa_ptt_{timestamp}.wav"
            target.write_bytes(wav_bytes)
            saved_path = target

        recorded = RecordedAudio(
            wav_bytes=wav_bytes,
            sample_rate=spec.sample_rate,
            channels=spec.channels,
            duration_ms=duration_ms,
            source="push_to_talk",
            saved_path=saved_path,
        )
        logger.info(
            "Push-to-talk recording stopped (duration_ms=%d, bytes=%d, saved=%s).",
            recorded.duration_ms,
            recorded.byte_size,
            saved_path is not None,
        )
        return recorded

    def cancel_recording(self) -> None:
        """Immediately stop microphone capture and discard all buffered audio in memory."""
        was_active = self._backend.is_active
        self._backend.cancel()
        if was_active:
            logger.info("Push-to-talk microphone recording cancelled and in-memory buffer cleared.")
