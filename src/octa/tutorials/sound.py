"""Optional tutorial completion audio cues for accessibility (Phase 8.3).

Synthesizes a pleasant, in-memory harmonic completion chime using pure Python PCM
audio math and plays it via injectable AudioPlaybackBackend (QtAudioPlaybackBackend
or MockAudioPlaybackBackend). Respects user audio preferences and requires zero
external audio files or external dependencies.
"""

from __future__ import annotations

import logging
import math
import struct

from octa.voice.audio import (
    DEFAULT_SAMPLE_RATE,
    SynthesizedSpeech,
    encode_pcm16_to_wav_bytes,
)
from octa.voice.providers import (
    AudioPlaybackBackend,
    create_playback_backend,
)

logger = logging.getLogger("octa.tutorials.sound")

# Harmonic chime tones: C5 (523.25 Hz), E5 (659.25 Hz), G5 (783.99 Hz)
DEFAULT_CHIME_NOTES: tuple[tuple[float, float], ...] = (
    (523.25, 0.10),  # C5, 100ms
    (659.25, 0.10),  # E5, 100ms
    (783.99, 0.22),  # G5, 220ms
)


def synthesize_completion_chime_wav(
    notes: tuple[tuple[float, float], ...] = DEFAULT_CHIME_NOTES,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    amplitude: float = 0.45,
) -> bytes:
    """Synthesize an ascending harmonic chime into in-memory 16-bit PCM WAV bytes."""
    pcm_chunks: list[bytes] = []

    for freq, duration in notes:
        num_samples = max(1, int(sample_rate * duration))
        samples: list[int] = []
        for i in range(num_samples):
            t = i / sample_rate
            # Smooth cosine attack and exponential decay envelope
            attack_samples = min(num_samples // 6, int(sample_rate * 0.015))
            if i < attack_samples and attack_samples > 0:
                env = 0.5 * (1.0 - math.cos(math.pi * i / attack_samples))
            else:
                decay_pos = (i - attack_samples) / max(1, num_samples - attack_samples)
                env = math.exp(-3.0 * decay_pos)

            val = math.sin(2.0 * math.pi * freq * t) * amplitude * env
            sample_val = int(max(-32767, min(32767, round(val * 32767))))
            samples.append(sample_val)

        raw_pcm = struct.pack(f"<{len(samples)}h", *samples)
        pcm_chunks.append(raw_pcm)

    all_pcm = b"".join(pcm_chunks)
    return encode_pcm16_to_wav_bytes(
        all_pcm,
        sample_rate=sample_rate,
        channels=1,
        sample_width_bytes=2,
    )


def create_completion_speech_cue(
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> SynthesizedSpeech:
    """Create a SynthesizedSpeech cue wrapping the in-memory completion chime."""
    wav_bytes = synthesize_completion_chime_wav(sample_rate=sample_rate)
    duration_ms = int(round(sum(dur for _, dur in DEFAULT_CHIME_NOTES) * 1000.0))
    return SynthesizedSpeech(
        audio_bytes=wav_bytes,
        provider_name="accessibility_completion_chime",
        media_format="wav",
        sample_rate=sample_rate,
        channels=1,
        duration_ms=duration_ms,
    )


class TutorialSoundPlayer:
    """Manages optional audio cues on tutorial completion, respecting user preferences."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        backend: AudioPlaybackBackend | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._backend = backend
        self._cached_cue: SynthesizedSpeech | None = None
        self.play_count = 0

    @property
    def enabled(self) -> bool:
        """Whether audio cues are enabled."""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable completion audio cues."""
        self._enabled = bool(enabled)

    @property
    def backend(self) -> AudioPlaybackBackend | None:
        """Active audio playback backend."""
        return self._backend

    def set_backend(self, backend: AudioPlaybackBackend | None) -> None:
        """Inject or replace audio playback backend."""
        self._backend = backend

    def play_completion_cue(self) -> bool:
        """Play the tutorial completion chime if enabled.

        Returns:
            bool: True if the audio cue was dispatched for playback, False if disabled or skipped.
        """
        if not self._enabled:
            return False

        if self._cached_cue is None:
            self._cached_cue = create_completion_speech_cue()

        backend = self._backend
        if backend is None:
            try:
                backend = create_playback_backend()
                self._backend = backend
            except Exception as exc:
                logger.warning("Could not initialize audio playback backend for sound cue: %s", exc)
                return False

        try:
            backend.play(self._cached_cue)
            self.play_count += 1
            logger.info("Played accessibility tutorial completion sound cue.")
            return True
        except Exception as exc:
            logger.warning("Failed to play tutorial completion audio cue: %s", exc)
            return False

    def stop(self) -> None:
        """Stop any active audio cue playback."""
        if self._backend is not None and hasattr(self._backend, "stop"):
            try:
                self._backend.stop()
            except Exception:
                pass
