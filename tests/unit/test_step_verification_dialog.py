"""Unit tests for Phase 11.1 Live Step Verification Sandbox & Prompt Diagnostics."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QDialog, QMessageBox

from octa.desktop.step_verification_dialog import StepVerificationDialog
from octa.desktop.tutorial_authoring_dialog import TutorialAuthoringDialog
from octa.intelligence.capture import (
    CapturedImage,
    CaptureRegion,
    ScreenCapturer,
    encode_qimage_to_png_bytes,
)
from octa.intelligence.exceptions import (
    ConsentDeclinedError,
    UserActionRequiredError,
)
from octa.intelligence.tasks import (
    ExpectedOutcome,
    ProgressResult,
    ProgressStatus,
    TaskDefinition,
    TaskStep,
)
from octa.intelligence.verifier import ProgressVerifier


def _sample_task_and_step() -> tuple[TaskDefinition, TaskStep]:
    step = TaskStep(
        step_id="step-save",
        order=1,
        title="Open Save Dialog",
        instruction="Click File then select Save As in the menu.",
        expected_outcome=ExpectedOutcome(
            description="Save As file dialog is displayed on screen.",
            required_elements=("Save As", "File Name field"),
            target_region=CaptureRegion(x=10, y=20, width=400, height=300),
            verification_hint="Verify the dialog title says 'Save As'",
        ),
        hints=("Use Ctrl+S as shortcut.",),
    )
    task = TaskDefinition(
        task_id="notepad-save",
        title="Save Document in Notepad",
        application="Notepad",
        goal="Save text document to disk.",
        steps=(step,),
    )
    return task, step


def _create_dummy_captured_image(width: int = 100, height: int = 80) -> CapturedImage:
    qimg = QImage(width, height, QImage.Format.Format_RGB32)
    qimg.fill(QColor("blue"))
    png_bytes = encode_qimage_to_png_bytes(qimg)
    return CapturedImage(
        png_bytes=png_bytes,
        width=width,
        height=height,
        region=CaptureRegion(x=0, y=0, width=width, height=height),
    )


def test_dialog_initialization_and_prepopulation(qtbot: Any) -> None:
    """Dialog pre-populates draft fields from original step ExpectedOutcome."""
    task, step = _sample_task_and_step()
    dlg = StepVerificationDialog(task, step)
    qtbot.addWidget(dlg)

    assert dlg._txt_draft_desc.toPlainText() == "Save As file dialog is displayed on screen."
    assert dlg._txt_draft_elements.text() == "Save As, File Name field"
    assert dlg._txt_draft_hint.text() == "Verify the dialog title says 'Save As'"
    assert "Target Region: X=10, Y=20, W=400, H=300" in dlg._lbl_region_info.text()
    assert dlg._list_checklist.count() == 2
    assert "Save As" in dlg._list_checklist.item(0).text()
    assert dlg._rb_mode_live.isChecked() is True
    assert dlg.applied_outcome is None


def test_reset_to_step_original(qtbot: Any) -> None:
    """Clicking Reset restores draft inputs to the original step values."""
    task, step = _sample_task_and_step()
    dlg = StepVerificationDialog(task, step)
    qtbot.addWidget(dlg)

    dlg._txt_draft_desc.setPlainText("Modified draft description")
    dlg._txt_draft_elements.setText("Different element")
    dlg._txt_draft_hint.setText("Different hint")

    dlg._btn_reset.click()

    assert dlg._txt_draft_desc.toPlainText() == "Save As file dialog is displayed on screen."
    assert dlg._txt_draft_elements.text() == "Save As, File Name field"
    assert dlg._txt_draft_hint.text() == "Verify the dialog title says 'Save As'"


def test_discard_leaves_step_untouched(qtbot: Any) -> None:
    """Discarding or rejecting the dialog leaves applied_outcome as None and cleans up data."""
    task, step = _sample_task_and_step()
    dlg = StepVerificationDialog(task, step)
    qtbot.addWidget(dlg)

    dlg._last_captured_image = _create_dummy_captured_image(100, 100)
    dlg._txt_draft_desc.setPlainText("Unsaved edit")
    dlg._btn_discard.click()

    assert dlg.result() == QDialog.DialogCode.Rejected
    assert dlg.applied_outcome is None
    assert dlg._last_captured_image is None


def test_close_event_cleans_up_retained_data_and_discards_draft(qtbot: Any) -> None:
    """Closing the dialog window cleans up retained image data and leaves applied_outcome None."""
    task, step = _sample_task_and_step()
    dlg = StepVerificationDialog(task, step)
    qtbot.addWidget(dlg)

    dlg._last_captured_image = _create_dummy_captured_image(120, 90)
    dlg._txt_draft_desc.setPlainText("Another unsaved edit")
    dlg.close()

    assert dlg.applied_outcome is None
    assert dlg._last_captured_image is None


def test_apply_changes_confirmation(qtbot: Any) -> None:
    """Applying changes updates applied_outcome only after user confirms."""
    task, step = _sample_task_and_step()
    dlg = StepVerificationDialog(task, step)
    qtbot.addWidget(dlg)

    dlg._txt_draft_desc.setPlainText("Updated description for Save As dialog")
    dlg._txt_draft_elements.setText("Save As, File Name, Confirm Button")
    dlg._txt_draft_hint.setText("Check window header")

    # 1. User says No to confirmation prompt
    with patch(
        "octa.desktop.step_verification_dialog.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ):
        dlg._btn_apply.click()
        assert dlg.applied_outcome is None

    # 2. User says Yes to confirmation prompt
    with patch(
        "octa.desktop.step_verification_dialog.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        dlg._btn_apply.click()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.applied_outcome is not None
        assert dlg.applied_outcome.description == "Updated description for Save As dialog"
        assert dlg.applied_outcome.required_elements == (
            "Save As",
            "File Name",
            "Confirm Button",
        )
        assert dlg.applied_outcome.verification_hint == "Check window header"


def test_apply_empty_description_rejected(qtbot: Any) -> None:
    """Empty description shows a warning and refuses to apply."""
    task, step = _sample_task_and_step()
    dlg = StepVerificationDialog(task, step)
    qtbot.addWidget(dlg)

    dlg._txt_draft_desc.setPlainText("")
    with patch("octa.desktop.step_verification_dialog.QMessageBox.warning") as mock_warn:
        dlg._btn_apply.click()
        mock_warn.assert_called_once()
        assert dlg.applied_outcome is None


def test_offline_simulation_scenarios(qtbot: Any) -> None:
    """Offline mode accurately simulates Complete, Incomplete, and Uncertain scenarios."""
    task, step = _sample_task_and_step()
    dlg = StepVerificationDialog(task, step)
    qtbot.addWidget(dlg)

    # Switch to offline mode
    dlg._rb_mode_offline.setChecked(True)
    dlg._on_mode_changed()
    assert not dlg._frame_offline.isHidden()

    # 1. Pass: Complete scenario
    dlg._cmb_offline_preset.setCurrentIndex(0)  # Pass: Complete
    dlg._btn_verify.click()
    assert "PASS (COMPLETE)" in dlg._lbl_status_badge.text()
    assert dlg._bar_confidence.value() == 95
    assert "meets threshold" in dlg._lbl_confidence_val.text()
    assert dlg._list_checklist.count() == 2
    assert "✔ Save As" in dlg._list_checklist.item(0).text()
    assert "✔ File Name field" in dlg._list_checklist.item(1).text()
    assert "OFFLINE SIMULATION" in dlg._txt_explanation.toPlainText()

    # 2. Incomplete: Missing Elements scenario
    dlg._cmb_offline_preset.setCurrentIndex(1)  # Incomplete: Missing Elements
    dlg._btn_verify.click()
    assert "INCOMPLETE" in dlg._lbl_status_badge.text()
    assert dlg._bar_confidence.value() == 85
    assert "✖ Save As" in dlg._list_checklist.item(0).text()
    assert "✖ File Name field" in dlg._list_checklist.item(1).text()

    # 3. Uncertain: Low Confidence scenario (0.60 < 0.75 threshold)
    dlg._cmb_offline_preset.setCurrentIndex(2)  # Uncertain: Low Confidence
    dlg._btn_verify.click()
    assert "UNCERTAIN" in dlg._lbl_status_badge.text()
    assert dlg._bar_confidence.value() == 60
    assert "below 0.75 threshold" in dlg._lbl_confidence_val.text()
    assert "below required threshold 0.75" in dlg._txt_explanation.toPlainText()

    # 4. Uncertain: Ambiguous Evidence scenario
    dlg._cmb_offline_preset.setCurrentIndex(3)  # Uncertain: Ambiguous Evidence
    dlg._btn_verify.click()
    assert "UNCERTAIN" in dlg._lbl_status_badge.text()
    assert "unclear and occluded" in dlg._txt_reasoning.toPlainText()


def test_capture_explicit_initiation_and_no_disk_persistence(qtbot: Any) -> None:
    """ScreenCapturer is called with user_initiated=True and never writes to disk."""
    task, step = _sample_task_and_step()
    capturer = ScreenCapturer(save_screenshots=False)
    dummy_img = _create_dummy_captured_image(200, 150)
    mock_verifier = MagicMock(spec=ProgressVerifier)

    with patch.object(capturer, "capture_region", return_value=dummy_img) as mock_cap:
        dlg = StepVerificationDialog(task, step, capturer=capturer, verifier=mock_verifier)
        qtbot.addWidget(dlg)

        dlg._chk_capture_region.setChecked(True)
        dlg._run_live_verification(dlg._build_draft_step())

        mock_cap.assert_called_once_with(step.expected_outcome.target_region, user_initiated=True)
        assert dlg._last_captured_image is dummy_img
        assert "Image: 200x150" in dlg._lbl_image_info.text()
        assert capturer._save_screenshots is False


def test_capture_full_screen_when_region_unchecked(qtbot: Any) -> None:
    """Captures full screen when target region checkbox is unchecked."""
    task, step = _sample_task_and_step()
    capturer = ScreenCapturer(save_screenshots=False)
    dummy_img = _create_dummy_captured_image(300, 200)
    mock_verifier = MagicMock(spec=ProgressVerifier)

    with patch.object(capturer, "capture_full_screen", return_value=dummy_img) as mock_cap:
        dlg = StepVerificationDialog(task, step, capturer=capturer, verifier=mock_verifier)
        qtbot.addWidget(dlg)

        dlg._chk_capture_region.setChecked(False)
        dlg._run_live_verification(dlg._build_draft_step())

        mock_cap.assert_called_once_with(user_initiated=True)
        assert dlg._last_captured_image is dummy_img


def test_capture_cancelled_clean_handling(qtbot: Any) -> None:
    """When capture returns None or raises UserActionRequiredError, handles cleanly."""
    task, step = _sample_task_and_step()
    capturer = ScreenCapturer(save_screenshots=False)

    with patch.object(capturer, "capture_region", return_value=None):
        dlg = StepVerificationDialog(task, step, capturer=capturer)
        qtbot.addWidget(dlg)

        dlg._run_live_verification(dlg._build_draft_step())
        assert "Capture cancelled." in dlg._lbl_footer_status.text()

    with patch.object(capturer, "capture_region", side_effect=UserActionRequiredError("No user")):
        dlg = StepVerificationDialog(task, step, capturer=capturer)
        qtbot.addWidget(dlg)

        dlg._run_live_verification(dlg._build_draft_step())
        assert "explicit user initiation required" in dlg._lbl_footer_status.text()


def test_live_verification_with_mock_verifier(qtbot: Any) -> None:
    """Live verification calls ProgressVerifier and renders live result."""
    task, step = _sample_task_and_step()
    capturer = ScreenCapturer(save_screenshots=False)
    dummy_img = _create_dummy_captured_image()

    mock_result = ProgressResult(
        task_id=task.task_id,
        step_id=step.step_id,
        status=ProgressStatus.COMPLETE,
        confidence=0.91,
        reasoning="Save As dialog confirmed visible.",
        next_instruction="Proceed to enter filename.",
        observed_elements=("Save As", "File Name field"),
        missing_elements=(),
    )
    mock_verifier = MagicMock(spec=ProgressVerifier)
    mock_verifier.verify_step_with_image.return_value = mock_result

    with patch.object(capturer, "capture_region", return_value=dummy_img):
        dlg = StepVerificationDialog(task, step, capturer=capturer, verifier=mock_verifier)
        qtbot.addWidget(dlg)

        dlg._run_live_verification(dlg._build_draft_step())

        mock_verifier.verify_step_with_image.assert_called_once()
        assert "PASS (COMPLETE)" in dlg._lbl_status_badge.text()
        assert dlg._bar_confidence.value() == 91
        assert "Save As dialog confirmed visible." in dlg._txt_reasoning.toPlainText()


def test_live_verification_consent_declined(qtbot: Any) -> None:
    """When user declines screen-sharing consent, handles gracefully without crashing."""
    task, step = _sample_task_and_step()
    capturer = ScreenCapturer(save_screenshots=False)
    dummy_img = _create_dummy_captured_image()

    mock_verifier = MagicMock(spec=ProgressVerifier)
    mock_verifier.verify_step_with_image.side_effect = ConsentDeclinedError("Consent was declined")

    with patch.object(capturer, "capture_region", return_value=dummy_img):
        dlg = StepVerificationDialog(task, step, capturer=capturer, verifier=mock_verifier)
        qtbot.addWidget(dlg)

        dlg._run_live_verification(dlg._build_draft_step())

        assert "CONSENT DECLINED" in dlg._lbl_status_badge.text()
        assert "Screen-sharing consent was declined" in dlg._txt_explanation.toPlainText()
        assert "consent declined" in dlg._lbl_footer_status.text()


def test_sensitive_secret_redaction_in_diagnostics(qtbot: Any) -> None:
    """API keys and secrets are redacted from model reasoning and errors."""
    task, step = _sample_task_and_step()
    capturer = ScreenCapturer(save_screenshots=False)
    dummy_img = _create_dummy_captured_image()

    # Result containing sensitive token
    mock_result = ProgressResult(
        task_id=task.task_id,
        step_id=step.step_id,
        status=ProgressStatus.INCOMPLETE,
        confidence=0.45,
        reasoning="Failed call with Bearer super-secret-token-12345 in header.",
        next_instruction="Retry step.",
        observed_elements=(),
        missing_elements=("Save As",),
    )
    mock_verifier = MagicMock(spec=ProgressVerifier)
    mock_verifier.verify_step_with_image.return_value = mock_result

    with patch.object(capturer, "capture_region", return_value=dummy_img):
        dlg = StepVerificationDialog(task, step, capturer=capturer, verifier=mock_verifier)
        qtbot.addWidget(dlg)

        dlg._run_live_verification(dlg._build_draft_step())

        reasoning_text = dlg._txt_reasoning.toPlainText()
        assert "super-secret-token-12345" not in reasoning_text
        assert "[REDACTED]" in reasoning_text


def test_tutorial_authoring_dialog_test_verification_wiring(qtbot: Any) -> None:
    """Clicking '🧪 Test Verification...' in builder opens dialog and applies changes."""
    task_def, step = _sample_task_and_step()

    authoring_dlg = TutorialAuthoringDialog()
    qtbot.addWidget(authoring_dlg)

    authoring_dlg.load_task(task_def)
    assert authoring_dlg._btn_test_verification.isEnabled() is True

    # Simulate StepVerificationDialog accepted with applied outcome
    new_outcome = ExpectedOutcome(
        description="Newly tested outcome description",
        required_elements=("Element A", "Element B"),
        verification_hint="New Hint",
    )

    mock_dialog_instance = MagicMock(spec=StepVerificationDialog)
    mock_dialog_instance.exec.return_value = QDialog.DialogCode.Accepted
    mock_dialog_instance.applied_outcome = new_outcome

    with patch(
        "octa.desktop.step_verification_dialog.StepVerificationDialog",
        return_value=mock_dialog_instance,
    ):
        authoring_dlg._on_test_verification_clicked()

        # Check that active step fields were updated
        assert authoring_dlg._txt_outcome_desc.text() == "Newly tested outcome description"
        assert authoring_dlg._txt_outcome_req.text() == "Element A, Element B"
        assert authoring_dlg._txt_outcome_hint.text() == "New Hint"
        assert authoring_dlg._is_dirty is True

        # Clear dirty flag to prevent modal discard prompt during fixture teardown
        authoring_dlg._is_dirty = False
