"""Unit and Qt interaction tests for Phase 11.2 — Speech Preview & Audio Timing Simulator."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from octa.desktop.speech_preview_dialog import SpeechPreviewDialog
from octa.desktop.tutorial_authoring_dialog import TutorialAuthoringDialog
from octa.intelligence.capture import CaptureRegion
from octa.intelligence.overlay import (
    GuidanceOverlayWidget,
    OverlayBounds,
    OverlayElementType,
    OverlayHint,
)
from octa.intelligence.tasks import (
    ExpectedOutcome,
    TaskDefinition,
    TaskStep,
)
from octa.tutorials.speech_timing import (
    DEFAULT_MIN_CUE_SECONDS,
    DEFAULT_MIN_SPEECH_SECONDS,
    estimate_speech_timing,
    format_speech_timing_summary,
)
from octa.voice.audio import encode_pcm16_to_wav_bytes
from octa.voice.consent import (
    VoiceConsentDecision,
    VoiceConsentManager,
)
from octa.voice.exceptions import VoiceConsentDeclinedError
from octa.voice.providers import (
    DisabledTTSProvider,
    MockAudioPlaybackBackend,
    MockTTSProvider,
    RemoteHTTPTTSProvider,
    RemoteVoiceConfig,
)


def _sample_task_and_step() -> tuple[TaskDefinition, TaskStep]:
    """Build a deterministic sample task and step with instructions, hints, and cues."""
    step = TaskStep(
        step_id="step-1",
        order=1,
        title="Open File Explorer",
        instruction="Press Windows Key plus E to open File Explorer on your desktop.",
        hints=("Look at your keyboard.", "Press Win+E simultaneously."),
        expected_outcome=ExpectedOutcome(
            description="File Explorer window is open",
            required_elements=("File Explorer", "Navigation Pane"),
            verification_hint="Check for explorer.exe window",
        ),
        overlay_cues=(
            OverlayHint(
                kind=OverlayElementType.HIGHLIGHT,
                label="Folder List",
                bounds=CaptureRegion(x=100, y=100, width=400, height=300),
            ),
        ),
    )
    task = TaskDefinition(
        task_id="test-explorer-task",
        version="1.0.0",
        title="Explorer Basics",
        application="Explorer",
        goal="Learn to navigate File Explorer",
        steps=(step,),
    )
    return task, step


# ─────────────────────────────────────────────────────────────────────────────
# 1. Speech Timing & Cue Duration Estimator Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_estimate_speech_timing_empty_and_whitespace() -> None:
    """Empty or whitespace text returns 0s speech duration and minimum cue duration floor."""
    est = estimate_speech_timing("")
    assert est.word_count == 0
    assert est.character_count == 0
    assert est.estimated_duration_seconds == 0.0
    assert est.recommended_cue_duration_seconds == DEFAULT_MIN_CUE_SECONDS
    assert "Words: 0" in format_speech_timing_summary(est)

    est_ws = estimate_speech_timing("   \n\t  ")
    assert est_ws.word_count == 0
    assert est_ws.estimated_duration_seconds == 0.0


def test_estimate_speech_timing_single_word_and_minimum_floors() -> None:
    """Short phrases respect minimum speech and cue duration floors."""
    est = estimate_speech_timing("Connect.")
    assert est.word_count == 1
    # Base duration for 1 word + 1 period pause is < 1.5s, so floor applies
    assert est.estimated_duration_seconds == DEFAULT_MIN_SPEECH_SECONDS
    assert est.recommended_cue_duration_seconds == DEFAULT_MIN_CUE_SECONDS
    summary = format_speech_timing_summary(est)
    assert "Words: 1" in summary
    assert "~1.5s" in summary


def test_estimate_speech_timing_various_cadences_and_punctuations() -> None:
    """Calculates speech durations accounting for words and conversational punctuation pauses."""
    text = (
        "First, click on the File menu in the upper-left corner. "
        "Next, select 'Save As' from the dropdown list!"
    )
    # Default 140 WPM
    est_default = estimate_speech_timing(text)
    assert est_default.word_count > 10
    assert est_default.estimated_duration_seconds > 4.0
    assert (
        est_default.recommended_cue_duration_seconds >= est_default.estimated_duration_seconds + 1.5
    )

    # Faster cadence: 200 WPM
    est_fast = estimate_speech_timing(text, words_per_minute=200)
    assert est_fast.estimated_duration_seconds < est_default.estimated_duration_seconds

    # Slower cadence: 90 WPM
    est_slow = estimate_speech_timing(text, words_per_minute=90)
    assert est_slow.estimated_duration_seconds > est_default.estimated_duration_seconds


def test_estimate_cue_duration_custom_padding() -> None:
    """Custom cue padding properly increases the recommended visual cue duration."""
    text = "Open the application and press Connect to start."
    est_pad1 = estimate_speech_timing(text, cue_padding_seconds=1.0)
    est_pad3 = estimate_speech_timing(text, cue_padding_seconds=3.0)
    assert est_pad3.recommended_cue_duration_seconds > est_pad1.recommended_cue_duration_seconds
    assert est_pad3.cue_padding_seconds == 3.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Dialog Initialization & UI Interaction Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_dialog_initialization_and_prepopulation(qtbot: QtBot) -> None:
    """SpeechPreviewDialog initializes with task metadata, sources combo, and timing readouts."""
    task, step = _sample_task_and_step()
    dlg = SpeechPreviewDialog(task, step)
    qtbot.addWidget(dlg)

    assert f"Step {step.order}: {step.title}" in dlg.windowTitle()
    assert dlg._cmb_source.count() >= 4  # Instruction, Hint 1, Hint 2, Full Narration, Custom
    assert dlg._cmb_source.currentIndex() == 0
    assert dlg._txt_preview_text.toPlainText() == step.instruction

    # Metric badges populated
    assert "Words:" in dlg._lbl_counts.text()
    assert "Est. Speech:" in dlg._lbl_speech_duration.text()
    assert "Recommended Cue:" in dlg._lbl_cue_duration.text()


def test_source_selection_changes_preview_text(qtbot: QtBot) -> None:
    """Selecting a different source in the combo box updates text and recalculates timing."""
    task, step = _sample_task_and_step()
    dlg = SpeechPreviewDialog(task, step)
    qtbot.addWidget(dlg)

    # Switch to Hint 1
    dlg._cmb_source.setCurrentIndex(1)
    assert dlg._txt_preview_text.toPlainText() == step.hints[0]

    # Switch to Hint 2
    dlg._cmb_source.setCurrentIndex(2)
    assert dlg._txt_preview_text.toPlainText() == step.hints[1]

    # Switch to Full Narration
    dlg._cmb_source.setCurrentIndex(3)
    assert step.title in dlg._txt_preview_text.toPlainText()
    assert step.instruction in dlg._txt_preview_text.toPlainText()


def test_cadence_controls_recalculate_timing(qtbot: QtBot) -> None:
    """Changing WPM or padding spinboxes immediately updates timing labels."""
    task, step = _sample_task_and_step()
    dlg = SpeechPreviewDialog(task, step)
    qtbot.addWidget(dlg)

    initial_speech = dlg._lbl_speech_duration.text()
    initial_cue = dlg._lbl_cue_duration.text()

    # Increase WPM (faster -> shorter duration)
    dlg._spin_wpm.setValue(220)
    assert dlg._lbl_speech_duration.text() != initial_speech

    # Increase padding -> longer cue duration
    dlg._spin_padding.setValue(4.5)
    assert dlg._lbl_cue_duration.text() != initial_cue


def test_apply_recommended_cue_duration(qtbot: QtBot) -> None:
    """Clicking Apply sets applied_cue_duration and displays confirmation."""
    task, step = _sample_task_and_step()
    dlg = SpeechPreviewDialog(task, step)
    qtbot.addWidget(dlg)

    with patch.object(QMessageBox, "information") as mock_info:
        dlg._btn_apply_cue_duration.click()
        assert dlg.applied_cue_duration is not None
        assert dlg.applied_cue_duration >= DEFAULT_MIN_CUE_SECONDS
        mock_info.assert_called_once()
        assert "Applied cue duration:" in dlg._lbl_footer_status.text()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Provider Handling & Safety Boundary Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_disabled_tts_provider_behavior(qtbot: QtBot) -> None:
    """Disabled TTS shows clear text-only notice, disables play, and forbids fake speech."""
    task, step = _sample_task_and_step()
    disabled_prov = DisabledTTSProvider()
    dlg = SpeechPreviewDialog(task, step, tts_provider=disabled_prov)
    qtbot.addWidget(dlg)

    assert dlg.is_tts_disabled is True
    assert "TTS: DISABLED" in dlg._lbl_provider_badge.text()
    assert not dlg._frame_disabled_notice.isHidden()
    assert dlg._btn_play.isEnabled() is False

    # Attempting to click play shows informative dialog and never substitutes a chime or mock
    with patch.object(QMessageBox, "information") as mock_info:
        dlg._on_play_clicked()
        mock_info.assert_called_once()
        assert "TTS Disabled" in mock_info.call_args[0][1]


def test_mock_tts_provider_playback_and_finished_callback(qtbot: QtBot) -> None:
    """MockTTSProvider synthesizes in-memory PCM audio and updates playback state machine."""
    task, step = _sample_task_and_step()
    mock_prov = MockTTSProvider(provider_name="mock_tts")
    mock_playback = MockAudioPlaybackBackend(auto_finish=False)

    dlg = SpeechPreviewDialog(
        task,
        step,
        tts_provider=mock_prov,
        playback_backend=mock_playback,
    )
    qtbot.addWidget(dlg)

    assert dlg.is_tts_disabled is False
    assert "MOCK" in dlg._lbl_provider_badge.text()
    assert dlg._btn_play.isEnabled() is True
    assert dlg._btn_stop.isEnabled() is False

    # Start playback
    dlg._btn_play.click()
    assert mock_prov.call_count == 1
    assert mock_playback.play_count == 1
    assert dlg._is_playing is True
    assert dlg._btn_play.isEnabled() is False
    assert dlg._btn_stop.isEnabled() is True
    assert dlg._active_speech is not None

    # Simulate playback naturally finishing
    mock_playback.finish_playback()
    assert dlg._is_playing is False
    assert dlg._active_speech is None  # Audio buffer released
    assert dlg._btn_play.isEnabled() is True
    assert dlg._btn_stop.isEnabled() is False
    assert "Speech playback finished." in dlg._lbl_footer_status.text()


def test_remote_tts_consent_accepted(qtbot: QtBot) -> None:
    """Remote TTS requests single-use user consent and synthesizes when approved."""
    task, step = _sample_task_and_step()
    remote_cfg = RemoteVoiceConfig(
        endpoint="https://speech.example.com/v1/audio/speech",
        model="tts-1",
        voice_name="alloy",
        api_key="sk-test-secret-key-12345",
    )
    dummy_wav = encode_pcm16_to_wav_bytes(b"\x00\x00" * 400, sample_rate=16000, channels=1)

    def mock_transport(req: Any, timeout: float = 5.0) -> bytes:
        return dummy_wav

    mock_consent_mgr = MagicMock(spec=VoiceConsentManager)

    def fake_require_consent(req: Any, text: str, consume: bool = True) -> VoiceConsentDecision:
        return VoiceConsentDecision.from_request(req, approved=True)

    mock_consent_mgr.require_text_consent.side_effect = fake_require_consent

    remote_prov = RemoteHTTPTTSProvider(
        remote_cfg,
        transport=mock_transport,
        consent_manager=mock_consent_mgr,
    )
    mock_playback = MockAudioPlaybackBackend(auto_finish=True)

    dlg = SpeechPreviewDialog(
        task,
        step,
        tts_provider=remote_prov,
        playback_backend=mock_playback,
        consent_manager=mock_consent_mgr,
    )
    qtbot.addWidget(dlg)

    assert "REMOTE HTTPS" in dlg._lbl_provider_badge.text()
    assert "speech.example.com" in dlg._lbl_provider_desc.text()

    dlg._btn_play.click()
    mock_consent_mgr.require_text_consent.assert_called_once()
    assert mock_playback.play_count == 1


def test_remote_tts_consent_declined(qtbot: QtBot) -> None:
    """Remote TTS cleanly aborts when single-use user consent is declined."""
    task, step = _sample_task_and_step()
    remote_cfg = RemoteVoiceConfig(
        endpoint="https://speech.example.com/v1/audio/speech",
        model="tts-1",
        voice_name="alloy",
        api_key="sk-test-secret-key-12345",
    )
    mock_transport = MagicMock()
    mock_consent_mgr = MagicMock(spec=VoiceConsentManager)
    mock_consent_mgr.require_text_consent.side_effect = VoiceConsentDeclinedError(
        "Consent declined"
    )

    remote_prov = RemoteHTTPTTSProvider(
        remote_cfg,
        transport=mock_transport,
        consent_manager=mock_consent_mgr,
    )
    mock_playback = MockAudioPlaybackBackend(auto_finish=True)

    dlg = SpeechPreviewDialog(
        task,
        step,
        tts_provider=remote_prov,
        playback_backend=mock_playback,
        consent_manager=mock_consent_mgr,
    )
    qtbot.addWidget(dlg)

    dlg._btn_play.click()
    mock_consent_mgr.require_text_consent.assert_called_once()
    mock_transport.assert_not_called()  # Never transmitted
    assert mock_playback.play_count == 0
    assert "consent declined by author" in dlg._lbl_footer_status.text()
    assert dlg._is_playing is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Stop / Cancel & Audio Preferences Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_stop_playback_immediately_cancels_and_flushes_buffer(qtbot: QtBot) -> None:
    """Clicking Stop immediately cancels provider, stops playback, and releases audio buffers."""
    task, step = _sample_task_and_step()
    mock_prov = MockTTSProvider()
    mock_playback = MockAudioPlaybackBackend(auto_finish=False)

    dlg = SpeechPreviewDialog(
        task,
        step,
        tts_provider=mock_prov,
        playback_backend=mock_playback,
    )
    qtbot.addWidget(dlg)

    dlg._btn_play.click()
    assert dlg._is_playing is True
    assert dlg._active_speech is not None

    # Click stop
    dlg._btn_stop.click()
    assert mock_prov.cancel_count == 1
    assert mock_playback.stop_count == 1
    assert dlg._is_playing is False
    assert dlg._active_speech is None  # Buffer released
    assert dlg._btn_play.isEnabled() is True
    assert dlg._btn_stop.isEnabled() is False


def test_dialog_close_and_reject_stops_playback(qtbot: QtBot) -> None:
    """Closing or rejecting the dialog stops playback and cleans up overlay widgets."""
    task, step = _sample_task_and_step()
    mock_prov = MockTTSProvider()
    mock_playback = MockAudioPlaybackBackend(auto_finish=False)

    dlg = SpeechPreviewDialog(
        task,
        step,
        tts_provider=mock_prov,
        playback_backend=mock_playback,
    )
    qtbot.addWidget(dlg)

    dlg._btn_play.click()
    assert dlg._is_playing is True

    dlg.close()
    assert mock_playback.stop_count == 1
    assert dlg._is_playing is False
    assert dlg._active_speech is None


def test_audio_preference_toggled(qtbot: QtBot) -> None:
    """Unchecking audio playback preference mutes playback and stops active sound."""
    task, step = _sample_task_and_step()
    mock_prov = MockTTSProvider()
    mock_playback = MockAudioPlaybackBackend(auto_finish=False)

    dlg = SpeechPreviewDialog(
        task,
        step,
        tts_provider=mock_prov,
        playback_backend=mock_playback,
    )
    qtbot.addWidget(dlg)

    dlg._btn_play.click()
    assert dlg._is_playing is True

    # Uncheck audio playback
    dlg._chk_enable_audio.setChecked(False)
    assert mock_playback.stop_count == 1
    assert "Audio playback muted" in dlg._lbl_footer_status.text()


def test_synchronized_cue_preview_with_and_without_cues(qtbot: QtBot) -> None:
    """Synchronized cue preview displays GuidanceOverlayWidget for the estimated duration."""
    task, step = _sample_task_and_step()
    dlg = SpeechPreviewDialog(
        task,
        step,
        display_bounds=OverlayBounds(x=0, y=0, width=800, height=600),
    )
    qtbot.addWidget(dlg)

    # 1. Step has cues -> creates and displays overlay
    dlg._btn_sync_cues.click()
    assert dlg._active_overlay is not None
    assert isinstance(dlg._active_overlay, GuidanceOverlayWidget)
    assert dlg._overlay_timer is not None
    assert dlg._btn_stop.isEnabled() is True

    # Stop cleans up overlay immediately
    dlg._btn_stop.click()
    assert dlg._active_overlay is None
    assert dlg._overlay_timer is None

    # 2. Step has no cues -> informs author without error
    step_no_cues = TaskStep(
        step_id="step-no-cues",
        order=2,
        title="Empty Cue Step",
        instruction="Do something without cues.",
        hints=(),
        expected_outcome=ExpectedOutcome(description="Done"),
        overlay_cues=(),
    )
    dlg_no_cues = SpeechPreviewDialog(task, step_no_cues)
    qtbot.addWidget(dlg_no_cues)

    with patch.object(QMessageBox, "information") as mock_info:
        dlg_no_cues._btn_sync_cues.click()
        mock_info.assert_called_once()
        assert dlg_no_cues._active_overlay is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. TutorialAuthoringDialog Integration Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_tutorial_authoring_dialog_speech_timing_readout_and_button_wiring(qtbot: QtBot) -> None:
    """TutorialAuthoringDialog updates inline speech timing and opens SpeechPreviewDialog."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)
    dlg.show()

    try:
        assert hasattr(dlg, "_lbl_speech_timing")
        assert hasattr(dlg, "_btn_speech_preview")
        assert "Speech" in dlg._lbl_speech_timing.text()

        # Edit instruction text and verify live update
        initial_text = dlg._lbl_speech_timing.text()
        dlg._txt_step_instruction.setPlainText(
            "Open the Settings app, click on Privacy & Security, then review Diagnostics."
        )
        assert dlg._lbl_speech_timing.text() != initial_text
        assert "words" in dlg._lbl_speech_timing.text()

        # Test opening SpeechPreviewDialog
        with patch.object(
            SpeechPreviewDialog, "exec", return_value=QDialog.DialogCode.Accepted
        ) as mock_exec:
            dlg._btn_speech_preview.click()
            mock_exec.assert_called_once()
    finally:
        dlg._is_dirty = False
        dlg.close()


def test_no_microphone_recording_and_no_learner_metrics_pollution(qtbot: QtBot) -> None:
    """Speech preview forbids microphone recording and never touches learner session metrics."""
    from octa.tutorials.feedback import FeedbackCollector
    from octa.voice.audio import PushToTalkRecorder

    task, step = _sample_task_and_step()
    mock_prov = MockTTSProvider()
    mock_playback = MockAudioPlaybackBackend(auto_finish=True)

    with (
        patch.object(PushToTalkRecorder, "start_recording") as mock_rec,
        patch.object(FeedbackCollector, "record_session_metrics") as mock_metrics,
    ):
        dlg = SpeechPreviewDialog(
            task,
            step,
            tts_provider=mock_prov,
            playback_backend=mock_playback,
        )
        qtbot.addWidget(dlg)

        dlg._btn_play.click()
        assert mock_playback.play_count == 1
        mock_rec.assert_not_called()
        mock_metrics.assert_not_called()
