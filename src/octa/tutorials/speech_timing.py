"""Speech timing estimator and visual cue duration calculator (Phase 11.2).

Calculates accurate speech narration duration estimates from linguistic cadence
models without requiring external network calls or cloud services, and derives
recommended display durations for passive on-screen accessibility visual cues.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Linguistic cadence defaults:
# Average spoken English instruction pace is 130-150 words per minute (WPM).
DEFAULT_WORDS_PER_MINUTE: int = 140
DEFAULT_MIN_SPEECH_SECONDS: float = 1.5
DEFAULT_CUE_PADDING_SECONDS: float = 1.5
DEFAULT_MIN_CUE_SECONDS: float = 3.5

# Punctuation pause durations (seconds)
PAUSE_MAJOR: float = 0.35  # ., !, ?, \n
PAUSE_MINOR: float = 0.15  # ,, ;, :, -, —


@dataclass(frozen=True)
class SpeechTimingEstimate:
    """Estimated timing metrics for spoken instructions and visual cue durations."""

    text: str
    word_count: int
    character_count: int
    estimated_duration_seconds: float
    recommended_cue_duration_seconds: float
    words_per_minute: int = DEFAULT_WORDS_PER_MINUTE
    cue_padding_seconds: float = DEFAULT_CUE_PADDING_SECONDS
    min_speech_seconds: float = DEFAULT_MIN_SPEECH_SECONDS
    min_cue_seconds: float = DEFAULT_MIN_CUE_SECONDS

    @property
    def estimated_duration_ms(self) -> int:
        """Estimated speech duration in milliseconds."""
        return int(round(self.estimated_duration_seconds * 1000.0))

    @property
    def recommended_cue_duration_ms(self) -> int:
        """Recommended visual cue duration in milliseconds."""
        return int(round(self.recommended_cue_duration_seconds * 1000.0))


def estimate_speech_timing(
    text: str,
    *,
    words_per_minute: int = DEFAULT_WORDS_PER_MINUTE,
    min_speech_seconds: float = DEFAULT_MIN_SPEECH_SECONDS,
    cue_padding_seconds: float = DEFAULT_CUE_PADDING_SECONDS,
    min_cue_seconds: float = DEFAULT_MIN_CUE_SECONDS,
) -> SpeechTimingEstimate:
    """Calculate estimated speech duration and recommended cue duration for narration text.

    Args:
        text: Spoken instruction or hint text.
        words_per_minute: Speech cadence in WPM (default 140 WPM).
        min_speech_seconds: Minimum speech duration floor for non-empty text (default 1.5s).
        cue_padding_seconds: Buffer added to speech duration for visual cues (default 1.5s).
        min_cue_seconds: Minimum cue duration floor (default 3.5s).

    Returns:
        SpeechTimingEstimate: Calculated timing metrics.
    """
    if not isinstance(text, str) or not text.strip():
        return SpeechTimingEstimate(
            text=text if isinstance(text, str) else "",
            word_count=0,
            character_count=len(text) if isinstance(text, str) else 0,
            estimated_duration_seconds=0.0,
            recommended_cue_duration_seconds=min_cue_seconds,
            words_per_minute=words_per_minute,
            cue_padding_seconds=cue_padding_seconds,
            min_speech_seconds=min_speech_seconds,
            min_cue_seconds=min_cue_seconds,
        )

    clean_text = text.strip()
    # Word count: words separated by whitespace or hyphens
    words = re.findall(r"\b[\w]+(?:['’\-][\w]+)*\b", clean_text)
    word_count = len(words)

    # Effective WPM clamp to sane bounds (50 to 300)
    effective_wpm = max(50, min(300, words_per_minute))
    words_per_second = effective_wpm / 60.0

    # Base duration from word count
    base_speech_sec = word_count / words_per_second

    # Natural conversational pauses at punctuation marks
    major_pauses = len(re.findall(r"[\.!\?\n]+", clean_text)) * PAUSE_MAJOR
    minor_pauses = len(re.findall(r"[,;:\-—]+", clean_text)) * PAUSE_MINOR

    total_est = base_speech_sec + major_pauses + minor_pauses
    speech_duration = max(min_speech_seconds, round(total_est, 1))

    # Recommended visual cue duration: speech duration + comprehension padding
    cue_duration = max(min_cue_seconds, round(speech_duration + cue_padding_seconds, 1))

    return SpeechTimingEstimate(
        text=clean_text,
        word_count=word_count,
        character_count=len(clean_text),
        estimated_duration_seconds=speech_duration,
        recommended_cue_duration_seconds=cue_duration,
        words_per_minute=effective_wpm,
        cue_padding_seconds=cue_padding_seconds,
        min_speech_seconds=min_speech_seconds,
        min_cue_seconds=min_cue_seconds,
    )


def format_speech_timing_summary(estimate: SpeechTimingEstimate) -> str:
    """Format a concise human-readable timing summary."""
    rec = estimate.recommended_cue_duration_seconds
    if estimate.word_count == 0:
        return f"Words: 0 | Est. Speech: 0.0s | Rec. Cue: {rec:.1f}s"
    return (
        f"Words: {estimate.word_count} | "
        f"Est. Speech: ~{estimate.estimated_duration_seconds:.1f}s | "
        f"Rec. Cue: ~{rec:.1f}s"
    )
