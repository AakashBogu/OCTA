"""Unit tests for push-to-talk audio capture, WAV encoding, and log/file privacy."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import pytest

from octa.voice.audio import (
    AudioFormatSpec,
    MockAudioCaptureBackend,
    PushToTalkRecorder,
    RecordedAudio,
    SynthesizedSpeech,
    TranscriptionResult,
    decode_wav_metadata,
    encode_pcm16_to_wav_bytes,
)
from octa.voice.exceptions import (
    ContinuousRecordingForbiddenError,
    MicrophoneCaptureError,
    PushToTalkRequiredError,
    VoiceCancelledError,
)


def test_wav_encode_and_decode_in_memory() -> None:
    """PCM16 bytes encode to RIFF WAV in memory and decode metadata accurately."""
    pcm_frames = b"\x20\x00" * 1600  # 1600 samples at 16 kHz mono = 100 ms
    wav_bytes = encode_pcm16_to_wav_bytes(
        pcm_frames,
        sample_rate=16000,
        channels=1,
        sample_width_bytes=2,
    )
    assert wav_bytes.startswith(b"RIFF")
    sr, ch, width, dur_ms, decoded_pcm = decode_wav_metadata(wav_bytes)
    assert sr == 16000
    assert ch == 1
    assert width == 2
    assert dur_ms == 100
    assert decoded_pcm == pcm_frames


def test_recorded_audio_transcription_and_speech_exclude_content_from_repr_and_str() -> None:
    """RecordedAudio, TranscriptionResult, and SynthesizedSpeech hide audio and transcripts."""
    secret_pcm = b"UNIQUE_PCM_AUDIO_PAYLOAD_BYTES__" * 20
    wav_bytes = encode_pcm16_to_wav_bytes(secret_pcm, sample_rate=16000, channels=1)
    b64_wav = base64.b64encode(wav_bytes).decode("ascii")

    audio = RecordedAudio(
        wav_bytes=wav_bytes,
        sample_rate=16000,
        channels=1,
        duration_ms=40,
        source="push_to_talk",
    )
    assert "UNIQUE_PCM_AUDIO" not in repr(audio)
    assert "UNIQUE_PCM_AUDIO" not in str(audio)
    assert b64_wav[:16] not in repr(audio)
    assert audio.sha256_digest[:8] not in repr(audio)

    sensitive_transcript = "My private spoken question with account 987654321"
    transcript = TranscriptionResult(
        text=sensitive_transcript,
        provider_name="mock_stt",
        language="en",
        confidence=0.95,
        duration_ms=40,
    )
    assert sensitive_transcript not in repr(transcript)
    assert sensitive_transcript not in str(transcript)
    assert "987654321" not in repr(transcript)
    assert transcript.text_sha256[:8] not in repr(transcript)
    assert f"char_length={len(sensitive_transcript)}" in repr(transcript)

    speech = SynthesizedSpeech(
        audio_bytes=wav_bytes,
        provider_name="mock_tts",
        media_format="wav",
        sample_rate=16000,
        channels=1,
        duration_ms=40,
    )
    assert "UNIQUE_PCM_AUDIO" not in repr(speech)
    assert "UNIQUE_PCM_AUDIO" not in str(speech)
    assert b64_wav[:16] not in repr(speech)
    assert speech.sha256_digest[:8] not in repr(speech)


def test_push_to_talk_requires_explicit_user_action_and_forbids_continuous_modes() -> None:
    """PushToTalkRecorder rejects non-user-initiated starts and forbids continuous/wake modes."""
    backend = MockAudioCaptureBackend()
    recorder = PushToTalkRecorder(backend=backend)

    # Without user_initiated=True -> PushToTalkRequiredError
    with pytest.raises(PushToTalkRequiredError, match="user_initiated=True"):
        recorder.start_recording(user_initiated=False)
    assert recorder.is_recording is False

    # Continuous or wake-word modes are strictly forbidden
    for forbidden_mode in ("continuous", "wake_word", "always_on", "background"):
        with pytest.raises(ContinuousRecordingForbiddenError, match="forbidden"):
            recorder.start_recording(user_initiated=True, mode=forbidden_mode)
        with pytest.raises(ContinuousRecordingForbiddenError, match="forbidden"):
            RecordedAudio(
                wav_bytes=encode_pcm16_to_wav_bytes(b"\x01\x00" * 100),
                source=forbidden_mode,
            )


def test_push_to_talk_records_in_memory_without_saving_files_or_logging_bytes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PushToTalkRecorder holds WAV audio in memory by default and never logs raw bytes."""
    marker_pcm = b"RAW_MIC_SAMPLE_MARKER_88" * 40
    backend = MockAudioCaptureBackend(
        format_spec=AudioFormatSpec(sample_rate=16000, channels=1, sample_width_bytes=2),
        canned_pcm_bytes=marker_pcm,
    )
    recorder = PushToTalkRecorder(
        backend=backend,
        save_audio=False,
        save_dir=tmp_path,
    )

    with caplog.at_level(logging.DEBUG, logger="octa.voice.audio"):
        recorder.start_recording(user_initiated=True)
        assert recorder.is_recording is True
        audio = recorder.stop_recording()
        assert recorder.is_recording is False

    assert audio.saved_path is None
    assert list(tmp_path.iterdir()) == []
    assert audio.byte_size > len(marker_pcm)

    b64_wav = base64.b64encode(audio.wav_bytes).decode("ascii")
    for record in caplog.records:
        msg = record.getMessage()
        assert "RAW_MIC_SAMPLE_MARKER_88" not in msg
        assert b64_wav[:16] not in msg


def test_push_to_talk_immediate_cancel_stops_backend_and_clears_buffer() -> None:
    """cancel_recording() immediately stops microphone capture and discards buffered bytes."""
    backend = MockAudioCaptureBackend()
    recorder = PushToTalkRecorder(backend=backend)

    recorder.start_recording(user_initiated=True)
    assert recorder.is_recording is True
    assert len(backend._buffer) > 0

    recorder.cancel_recording()
    assert recorder.is_recording is False
    assert backend.cancel_count == 1
    assert len(backend._buffer) == 0

    with pytest.raises(MicrophoneCaptureError, match="not active"):
        recorder.stop_recording()


def test_empty_capture_raises_cancelled_error() -> None:
    """Stopping a recording with zero captured bytes raises VoiceCancelledError."""
    backend = MockAudioCaptureBackend(canned_pcm_bytes=b"")
    recorder = PushToTalkRecorder(backend=backend)
    recorder.start_recording(user_initiated=True)
    with pytest.raises(VoiceCancelledError, match="no audio frames"):
        recorder.stop_recording()
