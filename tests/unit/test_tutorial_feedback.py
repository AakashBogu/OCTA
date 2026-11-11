"""Unit tests for the user testing and feedback loop (Phase 6.0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from octa.core.config import AppConfig
from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow, TutorialFeedbackDialog
from octa.intelligence.capture import CaptureRegion, ScreenCapturer
from octa.intelligence.exceptions import TaskValidationError
from octa.tutorials.catalog import build_notepad_save_tutorial
from octa.tutorials.engine import TutorialEngine, TutorialSessionState
from octa.tutorials.feedback import (
    FeedbackCollector,
    TutorialFeedback,
    TutorialSessionMetrics,
)


def _make_dummy_capturer() -> ScreenCapturer:
    def _grabber(region: CaptureRegion | None) -> tuple[bytes, int, int, CaptureRegion]:
        actual = region if region is not None else CaptureRegion(0, 0, 800, 600)
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, actual.width, actual.height, actual

    return ScreenCapturer(grabber=_grabber, save_screenshots=False)


def test_tutorial_feedback_validation_and_serialization() -> None:
    """TutorialFeedback validates fields, clamps ratings, and serializes cleanly."""
    fb = TutorialFeedback(
        task_id="octa-welcome-task",
        step_id="step-1",
        rating=5,
        difficulty="easy",
        comment="Very clear instruction!",
    )
    assert fb.task_id == "octa-welcome-task"
    assert fb.step_id == "step-1"
    assert fb.rating == 5
    assert fb.difficulty == "easy"
    assert fb.comment == "Very clear instruction!"

    # Serialization roundtrip
    d = fb.to_dict()
    restored = TutorialFeedback.from_dict(d)
    assert restored.task_id == fb.task_id
    assert restored.step_id == fb.step_id
    assert restored.rating == fb.rating
    assert restored.difficulty == fb.difficulty
    assert restored.comment == fb.comment

    # Invalid ratings (< 1 or > 5 or bool)
    with pytest.raises(TaskValidationError, match="between 1 and 5"):
        TutorialFeedback(task_id="t1", step_id="s1", rating=0)

    with pytest.raises(TaskValidationError, match="between 1 and 5"):
        TutorialFeedback(task_id="t1", step_id="s1", rating=6)

    with pytest.raises(TaskValidationError, match="between 1 and 5"):
        TutorialFeedback(task_id="t1", step_id="s1", rating=True)  # type: ignore[arg-type]

    # Invalid difficulty
    with pytest.raises(TaskValidationError, match="difficulty must be one of"):
        TutorialFeedback(task_id="t1", step_id="s1", rating=3, difficulty="impossible")

    # Empty task_id or step_id
    with pytest.raises(TaskValidationError, match="task_id must be a non-empty string"):
        TutorialFeedback(task_id="   ", step_id="s1", rating=4)

    with pytest.raises(TaskValidationError, match="step_id must be a non-empty string"):
        TutorialFeedback(task_id="t1", step_id="", rating=4)


def test_tutorial_session_metrics_tracking_and_serialization() -> None:
    """TutorialSessionMetrics accurately records progress counts and durations."""
    metrics = TutorialSessionMetrics(
        task_id="windows-notepad-save",
        start_time="2026-11-11T20:00:00Z",
        end_time="2026-11-11T20:02:30Z",
        duration_seconds=150.25,
        total_steps=3,
        completed_steps=3,
        hints_requested=2,
        ai_verifications_attempted=1,
        ai_verifications_passed=1,
        manual_completions=2,
        overlay_toggles=1,
        completed=True,
    )
    d = metrics.to_dict()
    assert d["duration_seconds"] == 150.25
    assert d["completed"] is True
    assert d["hints_requested"] == 2

    restored = TutorialSessionMetrics.from_dict(d)
    assert restored.task_id == metrics.task_id
    assert restored.duration_seconds == 150.25
    assert restored.completed is True


def test_feedback_collector_in_memory_and_persistence(tmp_path: Path) -> None:
    """FeedbackCollector records feedback and metrics and persists them to disk safely."""
    collector = FeedbackCollector(storage_dir=tmp_path)
    assert collector.storage_dir == tmp_path

    fb1 = TutorialFeedback(task_id="task-1", step_id="step-1", rating=4, difficulty="easy")
    fb2 = TutorialFeedback(
        task_id="task-1",
        step_id="step-2",
        rating=5,
        difficulty="just_right",
        comment="Great visual arrow!",
    )
    fb3 = TutorialFeedback(task_id="task-2", step_id="step-1", rating=2, difficulty="hard")

    collector.record_feedback(fb1)
    collector.record_feedback(fb2)
    collector.record_feedback(fb3)

    assert len(collector.get_feedback()) == 3
    assert len(collector.get_feedback(task_id="task-1")) == 2
    assert len(collector.get_feedback(task_id="task-2")) == 1

    # Verify persisted feedback file
    feedback_file = tmp_path / "tutorial_feedback.json"
    assert feedback_file.is_file()
    saved_data = json.loads(feedback_file.read_text(encoding="utf-8"))
    assert len(saved_data) == 3
    assert saved_data[1]["comment"] == "Great visual arrow!"

    # Session metrics
    m1 = TutorialSessionMetrics(
        task_id="task-1",
        start_time="2026-11-11T12:00:00Z",
        completed=True,
    )
    collector.record_session_metrics(m1)
    metrics_file = tmp_path / "tutorial_session_metrics.json"
    assert metrics_file.is_file()
    assert len(collector.get_session_metrics()) == 1

    # Summary export
    summary = collector.export_summary()
    assert summary["total_feedback_count"] == 3
    assert summary["average_rating"] == pytest.approx((4 + 5 + 2) / 3, rel=1e-2)
    assert summary["total_sessions_count"] == 1
    assert summary["completed_sessions_count"] == 1


def test_tutorial_engine_records_metrics_and_user_feedback(qapp: object) -> None:
    """TutorialEngine tracks hints, manual completions, and user feedback signals."""
    collector = FeedbackCollector()
    engine = TutorialEngine(
        capturer=_make_dummy_capturer(),
        feedback_collector=collector,
        tutorials=(build_notepad_save_tutorial(),),
    )

    feedback_emitted: list[TutorialFeedback] = []
    metrics_emitted: list[TutorialSessionMetrics] = []
    engine.feedback_recorded.connect(feedback_emitted.append)
    engine.session_metrics_updated.connect(metrics_emitted.append)

    try:
        # Request hints
        engine.request_hint()
        engine.request_hint()

        # Toggle overlay
        engine.set_overlay_enabled(True)
        engine.set_overlay_enabled(False)

        # Complete step 1 and 2 manually
        engine.complete_current_step_manually(user_initiated=True)
        engine.complete_current_step_manually(user_initiated=True)

        # Snapshot active session metrics before completion
        snapshot = engine.get_session_metrics()
        assert snapshot.hints_requested == 2
        assert snapshot.manual_completions == 2
        assert snapshot.completed_steps == 2
        assert snapshot.completed is False
        assert snapshot.overlay_toggles == 2

        # Record user feedback on current step
        fb = engine.record_feedback(
            rating=5,
            difficulty="just_right",
            comment="Step 2 instructions were clear.",
        )
        assert fb.rating == 5
        assert fb.step_id == "notepad-save-as"
        assert len(feedback_emitted) == 1

        # Complete final step -> tutorial reaches COMPLETED and finalizes metrics
        engine.complete_current_step_manually(user_initiated=True)
        assert engine.state == TutorialSessionState.COMPLETED
        assert len(metrics_emitted) == 1
        final_metrics = metrics_emitted[0]
        assert final_metrics.completed is True
        assert final_metrics.total_steps == 3
        assert final_metrics.completed_steps == 3
        assert final_metrics.manual_completions == 3
        assert final_metrics.hints_requested == 2
        assert final_metrics.duration_seconds >= 0.0

        # Verify collector has the records
        assert len(collector.get_feedback()) == 1
        assert len(collector.get_session_metrics()) == 1
    finally:
        engine.shutdown()


def test_tutorial_feedback_dialog_submission(qtbot: QtBot) -> None:
    """TutorialFeedbackDialog collects user rating, difficulty, and comments."""
    dialog = TutorialFeedbackDialog(
        task_title="Getting Started with OCTA",
        step_title="Step 1: Review Current Step",
    )
    qtbot.addWidget(dialog)

    try:
        assert dialog.get_rating() == 5  # Default "5 - Excellent"
        assert dialog.get_difficulty() == "just_right"
        assert dialog.get_comment() == ""

        # Change rating to 4 - Good (index 1)
        dialog._combo_rating.setCurrentIndex(1)
        assert dialog.get_rating() == 4

        # Change difficulty to Challenging (index 2)
        dialog._combo_difficulty.setCurrentIndex(2)
        assert dialog.get_difficulty() == "hard"

        # Enter comment
        dialog._txt_comment.setPlainText("The window target took a moment to locate.")
        assert dialog.get_comment() == "The window target took a moment to locate."

        # Submit via submit button
        qtbot.mouseClick(dialog._btn_submit, Qt.MouseButton.LeftButton)
        assert dialog.result() == TutorialFeedbackDialog.DialogCode.Accepted
    finally:
        dialog.close()


def test_desktop_controller_and_window_feedback_flow(qtbot: QtBot) -> None:
    """DiagnosticWindow Give Feedback button records feedback via DesktopController."""
    config = AppConfig(
        llm_api_key="",
        voice_stt_provider="disabled",
        voice_tts_provider="disabled",
    )
    controller = DesktopController(config=config, headless=True)
    window = DiagnosticWindow(controller, tray_enabled=False)
    qtbot.addWidget(window)

    feedback_received: list[TutorialFeedback] = []
    controller.feedback_recorded.connect(feedback_received.append)

    try:
        # Give feedback programmatically through controller
        fb = controller.record_tutorial_feedback(
            rating=4,
            difficulty="easy",
            comment="Automated feedback test.",
        )
        assert fb.rating == 4
        assert fb.difficulty == "easy"
        assert len(feedback_received) == 1
        assert feedback_received[0].comment == "Automated feedback test."

        # Verify button exists in UI
        assert window._btn_feedback is not None
        assert window._btn_feedback.text() == "Give Feedback"

        # Check session metrics property
        metrics = controller.tutorial_session_metrics
        assert metrics.task_id == "octa-welcome-task"
        assert metrics.completed_steps == 0
    finally:
        window.close()
        controller.stop(timeout_ms=500)
