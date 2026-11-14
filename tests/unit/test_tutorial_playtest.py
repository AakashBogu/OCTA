"""Unit and Qt interaction tests for Phase 11.3:
Interactive Author Step Runner & Playtest Sandbox.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QDialog, QLabel
from pytestqt.qtbot import QtBot

from octa.desktop.controller import DesktopController
from octa.desktop.plugin_project_dialog import PluginProjectDialog
from octa.desktop.tutorial_authoring_dialog import TutorialAuthoringDialog
from octa.desktop.tutorial_playtest_dialog import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PENDING,
    TutorialPlaytestDialog,
)
from octa.intelligence.capture import CaptureRegion
from octa.intelligence.overlay import (
    GuidanceOverlayWidget,
    OverlayBounds,
    OverlayElementType,
    OverlayHint,
)
from octa.intelligence.tasks import ExpectedOutcome, TaskDefinition, TaskStep
from octa.tutorials.feedback import FeedbackCollector


def _sample_multi_step_task() -> TaskDefinition:
    """Build a deterministic 3-step TaskDefinition for playtest verification."""
    step1 = TaskStep(
        step_id="step-1",
        order=1,
        title="Open Paint",
        instruction="Launch the Paint application from the Start menu.",
        hints=("Press the Windows key.", "Type 'Paint' and hit Enter."),
        expected_outcome=ExpectedOutcome(
            description="Paint window is visible",
            required_elements=("Paint Canvas", "Ribbon"),
            verification_hint="Check for mspaint.exe window",
        ),
        overlay_cues=(
            OverlayHint(
                kind=OverlayElementType.HIGHLIGHT,
                label="Start Menu Search",
                bounds=CaptureRegion(x=10, y=10, width=200, height=50),
            ),
        ),
    )
    step2 = TaskStep(
        step_id="step-2",
        order=2,
        title="Select Ellipse Tool",
        instruction="Click the Ellipse / Circle tool in the Shapes toolbar.",
        hints=("Look at the Shapes section in the Ribbon.",),
        expected_outcome=ExpectedOutcome(
            description="Ellipse tool is selected",
            required_elements=("Ellipse Tool Selected",),
            verification_hint="Look for shape selection highlight",
        ),
        overlay_cues=(
            OverlayHint(
                kind=OverlayElementType.ARROW,
                label="Shapes Section",
                start_x=300,
                start_y=80,
                end_x=300,
                end_y=150,
            ),
        ),
    )
    step3 = TaskStep(
        step_id="step-3",
        order=3,
        title="Draw Circle on Canvas",
        instruction="Click and drag on the canvas while holding Shift to draw a perfect circle.",
        hints=(),  # No hints on this step
        expected_outcome=ExpectedOutcome(
            description="Circle drawn on canvas",
            required_elements=("Circle Shape",),
            verification_hint="Check for circle on canvas",
        ),
        overlay_cues=(),  # No cues on this step
    )
    return TaskDefinition(
        task_id="test-paint-circle",
        version="1.0.0",
        title="Draw a Circle in Paint",
        application="Paint",
        goal="Learn shape drawing fundamentals in Paint",
        steps=(step1, step2, step3),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Dialog Initialization & Step Navigation Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_playtest_dialog_initialization_and_prepopulation(qtbot: QtBot) -> None:
    """TutorialPlaytestDialog initializes with task metadata and listening companion."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    assert f"Playtest Sandbox — {task.title}" in dlg.windowTitle()
    assert dlg.current_step_index == 0
    assert dlg.current_step.step_id == "step-1"
    assert "Step 1 of 3: Open Paint" in dlg._lbl_step_header.text()
    assert "Launch the Paint application" in dlg._txt_instruction_view.toPlainText()
    assert dlg._cmb_steps.count() == 3
    assert dlg._cmb_steps.currentIndex() == 0

    # Navigation buttons boundary state on step 0
    assert not dlg._btn_first.isEnabled()
    assert not dlg._btn_prev.isEnabled()
    assert dlg._btn_next.isEnabled()
    assert dlg._btn_last.isEnabled()

    # Initial companion state is LISTENING
    assert dlg.companion_state == "LISTENING"
    assert dlg._lbl_companion_badge.text() == "LISTENING"
    assert dlg._lbl_status_badge.text() == "PENDING / NOT COMPLETED"


def test_free_step_navigation_first_prev_next_last(qtbot: QtBot) -> None:
    """Author can freely navigate forward, backward, to first and last steps."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    # Next -> Step 2
    dlg._btn_next.click()
    assert dlg.current_step_index == 1
    assert dlg.current_step.step_id == "step-2"
    assert dlg._btn_first.isEnabled()
    assert dlg._btn_prev.isEnabled()
    assert dlg._btn_next.isEnabled()
    assert dlg._btn_last.isEnabled()

    # Next -> Step 3 (Last step)
    dlg._btn_next.click()
    assert dlg.current_step_index == 2
    assert dlg.current_step.step_id == "step-3"
    assert dlg._btn_first.isEnabled()
    assert dlg._btn_prev.isEnabled()
    assert not dlg._btn_next.isEnabled()
    assert not dlg._btn_last.isEnabled()

    # Prev -> Step 2
    dlg._btn_prev.click()
    assert dlg.current_step_index == 1

    # First -> Step 1
    dlg._btn_first.click()
    assert dlg.current_step_index == 0

    # Last -> Step 3
    dlg._btn_last.click()
    assert dlg.current_step_index == 2


def test_step_direct_jumping_via_combo(qtbot: QtBot) -> None:
    """Selecting a step in the dropdown combo box jumps directly to that step."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    # Jump directly to step 2 (index 1)
    dlg._cmb_steps.setCurrentIndex(1)
    assert dlg.current_step_index == 1
    assert dlg.current_step.step_id == "step-2"
    assert "Step 2 of 3" in dlg._lbl_step_header.text()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Progressive Hint Disclosure Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_progressive_hint_disclosure(qtbot: QtBot) -> None:
    """Clicking 'Request Next Hint' reveals progressive hints sequentially."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)  # Step 1 has 2 hints
    qtbot.addWidget(dlg)

    assert "Hints: 0 / 2 revealed" in dlg._lbl_hint_count.text()
    assert dlg._btn_reveal_hint.isEnabled()

    # Reveal Hint 1
    dlg._btn_reveal_hint.click()
    assert dlg.revealed_hints[0] == 1
    assert "Hints: 1 / 2 revealed" in dlg._lbl_hint_count.text()
    labels = dlg._hints_container.findChildren(QLabel)
    assert any("Press the Windows key" in lbl.text() for lbl in labels)
    assert dlg.companion_state == "THINKING"
    assert dlg._btn_reveal_hint.isEnabled()

    # Reveal Hint 2
    dlg._btn_reveal_hint.click()
    assert dlg.revealed_hints[0] == 2
    assert "Hints: 2 / 2 revealed" in dlg._lbl_hint_count.text()
    assert not dlg._btn_reveal_hint.isEnabled()


def test_step_without_hints_displays_empty_state(qtbot: QtBot) -> None:
    """Step with no hints shows empty state notice and disables request hint button."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=2)  # Step 3 has 0 hints
    qtbot.addWidget(dlg)

    assert "Hints: 0 / 0 revealed" in dlg._lbl_hint_count.text()
    assert not dlg._btn_reveal_hint.isEnabled()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Reset Controls Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_reset_current_step(qtbot: QtBot) -> None:
    """Resetting the active step clears outcome and hints without affecting other steps."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    # Force complete and reveal hints on Step 1
    dlg._btn_reveal_hint.click()
    dlg._btn_force_complete.click()
    assert dlg.step_outcomes[0] == STATUS_COMPLETE
    assert dlg.revealed_hints[0] == 1

    # Jump to Step 2 and force complete
    dlg._btn_next.click()
    dlg._btn_force_complete.click()
    assert dlg.step_outcomes[1] == STATUS_COMPLETE

    # Jump back to Step 1 and reset step
    dlg._btn_prev.click()
    dlg._btn_reset_step.click()

    assert dlg.step_outcomes[0] == STATUS_PENDING
    assert dlg.revealed_hints[0] == 0
    assert dlg.step_outcomes[1] == STATUS_COMPLETE  # Step 2 untouched


def test_reset_all_steps(qtbot: QtBot) -> None:
    """Resetting all steps clears all outcomes, hints, and returns to Step 1."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    dlg._btn_force_complete.click()
    dlg._btn_next.click()
    dlg._btn_force_complete.click()
    dlg._btn_next.click()
    dlg._btn_force_complete.click()

    assert all(o == STATUS_COMPLETE for o in dlg.step_outcomes.values())

    dlg._btn_reset_all.click()

    assert all(o == STATUS_PENDING for o in dlg.step_outcomes.values())
    assert all(h == 0 for h in dlg.revealed_hints.values())
    assert dlg.current_step_index == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. Author Simulation Overrides Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_force_step_complete_override(qtbot: QtBot) -> None:
    """Clicking 'Force Step Complete' marks step complete and sets companion to SUCCESS."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    dlg._btn_force_complete.click()
    assert dlg.step_outcomes[0] == STATUS_COMPLETE
    assert "STEP COMPLETE" in dlg._lbl_status_badge.text()
    assert dlg.companion_state == "SUCCESS"


def test_force_all_steps_shows_tutorial_completed_banner(qtbot: QtBot) -> None:
    """When all steps are marked complete, the footer displays tutorial completion."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    dlg._btn_force_complete.click()
    dlg._btn_next.click()
    dlg._btn_force_complete.click()
    dlg._btn_next.click()
    dlg._btn_force_complete.click()

    assert "All tutorial steps marked complete" in dlg._lbl_footer_status.text()


def test_simulate_failure_override(qtbot: QtBot) -> None:
    """Clicking 'Simulate Failure' marks step failed and sets companion to THINKING."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    dlg._btn_simulate_fail.click()
    assert dlg.step_outcomes[0] == STATUS_FAILED
    assert "CRITERIA UNMET" in dlg._lbl_status_badge.text()
    assert dlg.companion_state == "THINKING"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Visual Overlay & Display Management Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_visual_overlay_rendering_and_step_switching(qtbot: QtBot) -> None:
    """Visual overlay is displayed for steps with cues and cleared for steps without cues."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(
        task,
        initial_step_index=0,
        display_bounds=OverlayBounds(x=0, y=0, width=1920, height=1080),
    )
    qtbot.addWidget(dlg)

    # Step 1 has cues -> overlay active
    assert dlg._active_overlay is not None
    assert isinstance(dlg._active_overlay, GuidanceOverlayWidget)
    assert len(dlg._active_overlay._highlights) == 1

    # Jump to Step 2 -> cues updated to arrow
    dlg._btn_next.click()
    assert len(dlg._active_overlay._arrows) == 1

    # Jump to Step 3 (no cues) -> overlay annotations cleared
    dlg._btn_next.click()
    assert len(dlg._active_overlay._highlights) == 0
    assert len(dlg._active_overlay._arrows) == 0


def test_visual_overlay_toggle_checkbox(qtbot: QtBot) -> None:
    """Unchecking the overlay checkbox clears active overlay annotations."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    assert len(dlg._active_overlay._highlights) == 1

    # Uncheck overlay
    dlg._chk_overlay.setChecked(False)
    assert len(dlg._active_overlay._highlights) == 0

    # Re-check overlay
    dlg._chk_overlay.setChecked(True)
    assert len(dlg._active_overlay._highlights) == 1


def test_overlay_cleanup_on_dialog_exit(qtbot: QtBot) -> None:
    """Exiting or closing the dialog closes the active overlay widget."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    overlay = dlg._active_overlay
    assert overlay is not None

    dlg.close()
    assert dlg._active_overlay is None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Companion Robot State Synchronization Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_companion_state_synchronization_with_controller(qtbot: QtBot) -> None:
    """Controller receives companion state requests and resets to IDLE on dialog close."""
    task = _sample_multi_step_task()
    mock_ctrl = MagicMock(spec=DesktopController)
    mock_ctrl.accessibility_high_contrast = False
    mock_ctrl.accessibility_text_scale = 1.0

    dlg = TutorialPlaytestDialog(task, controller=mock_ctrl, initial_step_index=0)
    qtbot.addWidget(dlg)

    mock_ctrl.request_companion_state.assert_called_with("LISTENING", reason="playtest_init")

    # Step complete -> SUCCESS
    dlg._btn_force_complete.click()
    mock_ctrl.request_companion_state.assert_called_with("SUCCESS", reason="force_step_complete")

    # Simulate fail -> THINKING
    dlg._btn_simulate_fail.click()
    mock_ctrl.request_companion_state.assert_called_with("THINKING", reason="simulate_failure")

    # Close dialog -> IDLE
    dlg.close()
    mock_ctrl.request_companion_state.assert_called_with("IDLE", reason="playtest_exit")


def test_companion_manual_state_test_buttons(qtbot: QtBot) -> None:
    """Manual state buttons directly set companion robot state."""
    task = _sample_multi_step_task()
    dlg = TutorialPlaytestDialog(task, initial_step_index=0)
    qtbot.addWidget(dlg)

    dlg._btn_comp_idle.click()
    assert dlg.companion_state == "IDLE"

    dlg._btn_comp_thinking.click()
    assert dlg.companion_state == "THINKING"

    dlg._btn_comp_success.click()
    assert dlg.companion_state == "SUCCESS"

    dlg._btn_comp_listening.click()
    assert dlg.companion_state == "LISTENING"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Strict Privacy & Learner Metric Isolation Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_strict_isolation_from_learner_metrics_and_feedback(qtbot: QtBot) -> None:
    """Playtest actions must never record feedback, mutate metrics, or record audio."""
    from octa.intelligence.capture import ScreenCapturer
    from octa.intelligence.verifier import ProgressVerifier
    from octa.voice.audio import PushToTalkRecorder

    task = _sample_multi_step_task()

    with (
        patch.object(FeedbackCollector, "record_feedback") as mock_fb,
        patch.object(FeedbackCollector, "record_session_metrics") as mock_metrics,
        patch.object(ScreenCapturer, "capture_full_screen") as mock_cap_screen,
        patch.object(ScreenCapturer, "capture_region") as mock_cap_region,
        patch.object(ProgressVerifier, "verify_step_with_image") as mock_verify,
        patch.object(PushToTalkRecorder, "start_recording") as mock_voice,
    ):
        dlg = TutorialPlaytestDialog(task, initial_step_index=0)
        qtbot.addWidget(dlg)

        # Execute all playtest actions
        dlg._btn_reveal_hint.click()
        dlg._btn_force_complete.click()
        dlg._btn_next.click()
        dlg._btn_simulate_fail.click()
        dlg._btn_reset_step.click()
        dlg._btn_reset_all.click()
        dlg.close()

        # Invariant: Zero recording, capture, or external calls
        mock_fb.assert_not_called()
        mock_metrics.assert_not_called()
        mock_cap_screen.assert_not_called()
        mock_cap_region.assert_not_called()
        mock_verify.assert_not_called()
        mock_voice.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 8. UI Integration with Authoring & Project Studio Dialogs
# ─────────────────────────────────────────────────────────────────────────────


def test_tutorial_authoring_dialog_playtest_button_wiring(qtbot: QtBot) -> None:
    """TutorialAuthoringDialog has 'Playtest...' button wired to launch TutorialPlaytestDialog."""
    dlg = TutorialAuthoringDialog()
    qtbot.addWidget(dlg)
    dlg.show()

    try:
        assert hasattr(dlg, "_btn_playtest")
        assert "Playtest" in dlg._btn_playtest.text()

        with patch.object(
            TutorialPlaytestDialog, "exec", return_value=QDialog.DialogCode.Accepted
        ) as mock_exec:
            dlg._btn_playtest.click()
            mock_exec.assert_called_once()
            assert "Playtest sandbox closed." in dlg._lbl_status.text()
    finally:
        dlg._is_dirty = False
        dlg.close()


def test_plugin_project_dialog_playtest_button_wiring(qtbot: QtBot, tmp_path: any) -> None:
    """PluginProjectDialog has 'Playtest...' button wired to launch playtest sandbox."""
    from octa.intelligence.tasks import save_task_definition_to_file
    from octa.tutorials.project import TutorialPluginProject

    task = _sample_multi_step_task()
    proj_dir = tmp_path / "test_plugin_proj"
    proj = TutorialPluginProject.create(
        proj_dir,
        plugin_id="octa-testplaytest",
        name="Test Playtest Plugin",
        version="1.0.0",
        author="Author",
    )
    # Save a tutorial in project
    tut_file = proj_dir / "tutorials" / "test_task.json"
    save_task_definition_to_file(task, tut_file)
    proj.add_tutorial_file(tut_file, copy_into_project=False)

    dlg = PluginProjectDialog(project=proj)
    qtbot.addWidget(dlg)
    dlg.show()

    try:
        assert hasattr(dlg, "_btn_playtest_tut")
        assert "Playtest" in dlg._btn_playtest_tut.text()

        # Select the tutorial
        dlg._list_tutorials.setCurrentRow(0)

        with patch.object(
            TutorialPlaytestDialog, "exec", return_value=QDialog.DialogCode.Accepted
        ) as mock_exec:
            dlg._btn_playtest_tut.click()
            mock_exec.assert_called_once()
    finally:
        dlg.close()
