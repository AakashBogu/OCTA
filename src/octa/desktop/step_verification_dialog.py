"""In-app live step verification sandbox and prompt diagnostics workbench.

Enables tutorial authors to test and tune step verification criteria against
real or simulated screen captures, inspect multimodal model confidence and
checklist breakdowns, observe conservative evidence downgrades, and iterate
on prompt criteria in a temporary draft before committing changes.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QCloseEvent, QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from octa.intelligence.capture import CapturedImage, CaptureRegion, ScreenCapturer
from octa.intelligence.consent import ConsentManager
from octa.intelligence.exceptions import (
    ConsentDeclinedError,
    UserActionRequiredError,
)
from octa.intelligence.llm import redact_sensitive_text
from octa.intelligence.tasks import (
    ExpectedOutcome,
    ProgressResult,
    ProgressStatus,
    TaskDefinition,
    TaskStep,
)
from octa.intelligence.verifier import (
    DEFAULT_MIN_COMPLETE_CONFIDENCE,
    ProgressVerifier,
    parse_verification_output,
)

if TYPE_CHECKING:
    from octa.desktop.controller import DesktopController

logger = logging.getLogger("octa.desktop.step_verification")


class StepVerificationDialog(QDialog):
    """Authoring workbench for testing and tuning step verification criteria."""

    def __init__(
        self,
        task: TaskDefinition,
        step: TaskStep,
        *,
        controller: DesktopController | None = None,
        verifier: ProgressVerifier | None = None,
        capturer: ScreenCapturer | None = None,
        consent_manager: ConsentManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Step Verification Test Bench — Step {step.order}: {step.title}")
        self.setMinimumSize(920, 680)
        self.setModal(True)

        self._task = task
        self._step = step
        self._controller = controller

        # Resolve dependencies from controller if available
        if controller is not None and hasattr(controller, "tutorial_engine"):
            engine = controller.tutorial_engine
            self._capturer = capturer if capturer is not None else engine.capturer
            self._consent_manager = (
                consent_manager if consent_manager is not None else engine.consent_manager
            )
            self._verifier = verifier if verifier is not None else engine.verifier
        else:
            self._capturer = (
                capturer if capturer is not None else ScreenCapturer(save_screenshots=False)
            )
            self._consent_manager = consent_manager
            self._verifier = verifier

        # Outgoing result if applied
        self.applied_outcome: ExpectedOutcome | None = None

        # Internal state
        self._last_captured_image: CapturedImage | None = None
        self._last_verification_result: ProgressResult | None = None

        self._init_ui()
        self._populate_original_step_data()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 14)

        # ── 1. Header Banner ──────────────────────────────
        hdr_frame = QFrame(self)
        hdr_frame.setStyleSheet("background-color: #0f172a; border-radius: 6px; padding: 10px;")
        hdr_layout = QVBoxLayout(hdr_frame)
        hdr_layout.setSpacing(4)
        hdr_layout.setContentsMargins(8, 8, 8, 8)

        lbl_step_hdr = QLabel(
            f"Step {self._step.order} ({self._step.step_id}): {self._step.title}",
            hdr_frame,
        )
        lbl_step_hdr.setStyleSheet(
            "color: #f8fafc; font-size: 15px; font-weight: bold; background: transparent;"
        )
        hdr_layout.addWidget(lbl_step_hdr)

        lbl_task_hdr = QLabel(
            f"Tutorial: {self._task.title} (App: {self._task.application})",
            hdr_frame,
        )
        lbl_task_hdr.setStyleSheet("color: #94a3b8; font-size: 12px; background: transparent;")
        hdr_layout.addWidget(lbl_task_hdr)

        lbl_inst = QLabel(f"Instruction: {self._step.instruction}", hdr_frame)
        lbl_inst.setStyleSheet(
            "color: #cbd5e1; font-size: 12px; font-style: italic; background: transparent;"
        )
        lbl_inst.setWordWrap(True)
        hdr_layout.addWidget(lbl_inst)

        main_layout.addWidget(hdr_frame)

        # ── 2. Splitter: Left (Draft Criteria) / Right (Test & Diagnostics) ──
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # LEFT PANE: Draft Criteria Editor
        left_widget = QWidget(splitter)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        grp_draft = QGroupBox("Draft Verification Criteria (Temporary Working Copy)", left_widget)
        draft_layout = QVBoxLayout(grp_draft)
        draft_layout.setSpacing(8)

        draft_layout.addWidget(QLabel("Outcome Description:", grp_draft))
        self._txt_draft_desc = QTextEdit(grp_draft)
        self._txt_draft_desc.setMaximumHeight(80)
        self._txt_draft_desc.setPlaceholderText("Describe visual evidence of completion...")
        self._txt_draft_desc.setAccessibleName("Draft Outcome Description")
        draft_layout.addWidget(self._txt_draft_desc)

        draft_layout.addWidget(QLabel("Required Visual Elements (comma-separated):", grp_draft))
        self._txt_draft_elements = QLineEdit(grp_draft)
        self._txt_draft_elements.setPlaceholderText("e.g. Save As dialog, Cancel button")
        self._txt_draft_elements.setAccessibleName("Draft Required Elements")
        draft_layout.addWidget(self._txt_draft_elements)

        draft_layout.addWidget(QLabel("Verifier Prompt Hint (optional guidance):", grp_draft))
        self._txt_draft_hint = QLineEdit(grp_draft)
        self._txt_draft_hint.setPlaceholderText("e.g. Look for title bar text 'Save As'")
        self._txt_draft_hint.setAccessibleName("Draft Verifier Hint")
        draft_layout.addWidget(self._txt_draft_hint)

        # Region status
        self._lbl_region_info = QLabel("Target Region: Full Screen", grp_draft)
        self._lbl_region_info.setStyleSheet("color: #64748b; font-size: 11px;")
        draft_layout.addWidget(self._lbl_region_info)

        btn_reset_row = QHBoxLayout()
        self._btn_reset = QPushButton("↺ Reset to Step Original", grp_draft)
        self._btn_reset.setAccessibleName("Reset Criteria to Original Step Values")
        self._btn_reset.clicked.connect(self._populate_original_step_data)
        btn_reset_row.addWidget(self._btn_reset)
        btn_reset_row.addStretch()
        draft_layout.addLayout(btn_reset_row)

        draft_layout.addStretch()
        left_layout.addWidget(grp_draft)

        # Notice on left pane
        lbl_draft_notice = QLabel(
            "Changes in this test bench are temporary drafts. They will not be applied to the "
            "tutorial step unless you explicitly click 'Apply Changes to Step' below.",
            left_widget,
        )
        lbl_draft_notice.setWordWrap(True)
        lbl_draft_notice.setStyleSheet("color: #64748b; font-size: 11px; font-style: italic;")
        left_layout.addWidget(lbl_draft_notice)

        splitter.addWidget(left_widget)

        # RIGHT PANE: Test Execution & Diagnostics (Scrollable)
        right_scroll = QScrollArea(splitter)
        right_scroll.setWidgetResizable(True)
        right_widget = QWidget(right_scroll)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(10)

        # Test Execution Box
        grp_exec = QGroupBox("Verification Execution", right_widget)
        exec_layout = QVBoxLayout(grp_exec)
        exec_layout.setSpacing(8)

        # Mode Selection: Live vs Offline/Simulated
        mode_row = QHBoxLayout()
        self._rb_mode_live = QRadioButton("🌐 Live Multimodal AI", grp_exec)
        self._rb_mode_live.setChecked(True)
        self._rb_mode_offline = QRadioButton("🖥 Simulated / Offline Mode", grp_exec)
        self._mode_group = QButtonGroup(grp_exec)
        self._mode_group.addButton(self._rb_mode_live)
        self._mode_group.addButton(self._rb_mode_offline)
        self._mode_group.buttonClicked.connect(self._on_mode_changed)

        mode_row.addWidget(self._rb_mode_live)
        mode_row.addWidget(self._rb_mode_offline)
        mode_row.addStretch()
        exec_layout.addLayout(mode_row)

        # Offline preset row (hidden when Live mode active)
        self._frame_offline = QFrame(grp_exec)
        self._frame_offline.setVisible(False)
        self._frame_offline.setStyleSheet(
            "background-color: #f1f5f9; border-radius: 4px; padding: 6px;"
        )
        off_layout = QHBoxLayout(self._frame_offline)
        off_layout.setContentsMargins(4, 4, 4, 4)
        off_layout.addWidget(QLabel("Offline Test Preset:", self._frame_offline))
        self._cmb_offline_preset = QComboBox(self._frame_offline)
        self._cmb_offline_preset.addItem("Pass: Complete (All elements detected, conf 0.95)")
        self._cmb_offline_preset.addItem("Incomplete: Missing Elements (Not found, conf 0.85)")
        self._cmb_offline_preset.addItem("Uncertain: Low Confidence (0.60 < 0.75 threshold)")
        self._cmb_offline_preset.addItem("Uncertain: Ambiguous Evidence ('unclear or occluded')")
        self._cmb_offline_preset.setAccessibleName("Offline Test Preset Scenario")
        off_layout.addWidget(self._cmb_offline_preset, stretch=1)
        exec_layout.addWidget(self._frame_offline)

        # Action Button & Options
        act_row = QHBoxLayout()
        self._btn_verify = QPushButton("📸 Capture Screen & Test Verification", grp_exec)
        self._btn_verify.setStyleSheet(
            "background-color: #0284c7; color: white; font-weight: bold; "
            "font-size: 13px; padding: 6px 14px;"
        )
        self._btn_verify.setAccessibleName("Capture Screen and Test Step Verification")
        self._btn_verify.clicked.connect(self._on_test_verification_clicked)

        self._chk_capture_region = QCheckBox("Capture Target Region Only", grp_exec)
        self._chk_capture_region.setChecked(self._step.expected_outcome.target_region is not None)
        self._chk_capture_region.setEnabled(self._step.expected_outcome.target_region is not None)

        act_row.addWidget(self._btn_verify)
        act_row.addWidget(self._chk_capture_region)
        act_row.addStretch()
        exec_layout.addLayout(act_row)

        # Screenshot Thumbnail & In-Memory Status
        snap_box = QHBoxLayout()
        self._lbl_thumb = QLabel("No capture yet", grp_exec)
        self._lbl_thumb.setFixedSize(140, 85)
        self._lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_thumb.setStyleSheet(
            "background-color: #e2e8f0; border: 1px dashed #94a3b8; "
            "border-radius: 4px; color: #64748b;"
        )

        snap_info_layout = QVBoxLayout()
        self._lbl_image_info = QLabel("Image: None (Click Capture to test)", grp_exec)
        self._lbl_image_info.setStyleSheet("color: #334155; font-weight: 500;")
        lbl_privacy_note = QLabel(
            "🔒 Screen content is evaluated strictly in memory and will NOT be saved to disk.",
            grp_exec,
        )
        lbl_privacy_note.setStyleSheet("color: #047857; font-size: 11px;")
        snap_info_layout.addWidget(self._lbl_image_info)
        snap_info_layout.addWidget(lbl_privacy_note)
        snap_info_layout.addStretch()

        snap_box.addWidget(self._lbl_thumb)
        snap_box.addLayout(snap_info_layout, stretch=1)
        exec_layout.addLayout(snap_box)

        right_layout.addWidget(grp_exec)

        # Diagnostics & Evaluation Report Group
        grp_diag = QGroupBox("Verification Diagnostics & Evidence Analysis", right_widget)
        diag_layout = QVBoxLayout(grp_diag)
        diag_layout.setSpacing(8)

        # Status & Confidence Row
        stat_row = QHBoxLayout()
        self._lbl_status_badge = QLabel("READY", grp_diag)
        self._lbl_status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_status_badge.setStyleSheet(
            "background-color: #94a3b8; color: white; font-weight: bold; font-size: 13px; "
            "padding: 4px 12px; border-radius: 4px;"
        )
        self._lbl_status_badge.setAccessibleName("Verification Outcome Status")

        self._lbl_confidence_val = QLabel("Confidence: -- (Threshold: 0.75)", grp_diag)
        self._lbl_confidence_val.setStyleSheet("color: #334155; font-weight: bold;")

        stat_row.addWidget(self._lbl_status_badge)
        stat_row.addWidget(self._lbl_confidence_val)
        stat_row.addStretch()
        diag_layout.addLayout(stat_row)

        self._bar_confidence = QProgressBar(grp_diag)
        self._bar_confidence.setRange(0, 100)
        self._bar_confidence.setValue(0)
        self._bar_confidence.setTextVisible(False)
        self._bar_confidence.setFixedHeight(12)
        self._bar_confidence.setStyleSheet(
            "QProgressBar { border: 1px solid #cbd5e1; border-radius: 4px; background: #f1f5f9; }"
            "QProgressBar::chunk { background-color: #0284c7; border-radius: 3px; }"
        )
        diag_layout.addWidget(self._bar_confidence)

        # Required Elements Checklist
        diag_layout.addWidget(QLabel("Required Visual Elements Checklist:", grp_diag))
        self._list_checklist = QListWidget(grp_diag)
        self._list_checklist.setMaximumHeight(90)
        self._list_checklist.setAccessibleName("Required Elements Verification Checklist")
        diag_layout.addWidget(self._list_checklist)

        # Explanation / Downgrade Summary
        diag_layout.addWidget(QLabel("Outcome Explanation & Downgrade Analysis:", grp_diag))
        self._txt_explanation = QTextEdit(grp_diag)
        self._txt_explanation.setMaximumHeight(70)
        self._txt_explanation.setReadOnly(True)
        self._txt_explanation.setAccessibleName("Outcome Explanation and Downgrades")
        self._txt_explanation.setStyleSheet(
            "background-color: #f8fafc; font-family: monospace; font-size: 11px;"
        )
        diag_layout.addWidget(self._txt_explanation)

        # Model Reasoning Excerpt
        diag_layout.addWidget(QLabel("Model Reasoning Excerpt:", grp_diag))
        self._txt_reasoning = QTextEdit(grp_diag)
        self._txt_reasoning.setMaximumHeight(75)
        self._txt_reasoning.setReadOnly(True)
        self._txt_reasoning.setAccessibleName("Model Reasoning Excerpt")
        self._txt_reasoning.setStyleSheet("background-color: #f8fafc; font-size: 11px;")
        diag_layout.addWidget(self._txt_reasoning)

        right_layout.addWidget(grp_diag)
        right_layout.addStretch()
        right_scroll.setWidget(right_widget)

        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        main_layout.addWidget(splitter, stretch=1)

        # ── 3. Bottom Action Buttons ──────────────────────
        bot_bar = QHBoxLayout()
        self._lbl_footer_status = QLabel("Ready to test.", self)
        self._lbl_footer_status.setStyleSheet("color: #64748b; font-size: 11px;")
        bot_bar.addWidget(self._lbl_footer_status, stretch=1)

        self._btn_apply = QPushButton("✔ Apply Changes to Step", self)
        self._btn_apply.setStyleSheet(
            "background-color: #15803d; color: white; font-weight: bold; padding: 6px 14px;"
        )
        self._btn_apply.setAccessibleName("Apply Verification Changes to Tutorial Step")
        self._btn_apply.clicked.connect(self._on_apply_clicked)

        self._btn_discard = QPushButton("✖ Discard & Close", self)
        self._btn_discard.setAccessibleName("Discard Draft and Close Test Bench")
        self._btn_discard.clicked.connect(self.reject)

        bot_bar.addWidget(self._btn_apply)
        bot_bar.addWidget(self._btn_discard)
        main_layout.addLayout(bot_bar)

    def _populate_original_step_data(self) -> None:
        """Populate draft fields from original step's ExpectedOutcome."""
        outcome = self._step.expected_outcome
        self._txt_draft_desc.setPlainText(outcome.description)
        self._txt_draft_elements.setText(", ".join(outcome.required_elements))
        self._txt_draft_hint.setText(outcome.verification_hint)

        if outcome.target_region is not None:
            r = outcome.target_region
            self._lbl_region_info.setText(
                f"Target Region: X={r.x}, Y={r.y}, W={r.width}, H={r.height}"
            )
        else:
            self._lbl_region_info.setText("Target Region: None (Full Screen capture)")

        self._refresh_checklist_preview()

    def _refresh_checklist_preview(self) -> None:
        """Refresh checklist widget with current draft elements in pending state."""
        self._list_checklist.clear()
        raw_elements = [e.strip() for e in self._txt_draft_elements.text().split(",") if e.strip()]
        if not raw_elements:
            item = QListWidgetItem("No required visual elements specified.")
            item.setForeground(QColor("#64748b"))
            self._list_checklist.addItem(item)
            return

        for req in raw_elements:
            item = QListWidgetItem(f"⚪ {req} (Pending test)")
            item.setForeground(QColor("#475569"))
            self._list_checklist.addItem(item)

    def _build_draft_outcome(self) -> ExpectedOutcome:
        """Construct an ExpectedOutcome from current draft inputs."""
        desc = self._txt_draft_desc.toPlainText().strip()
        req_items = tuple(
            e.strip() for e in self._txt_draft_elements.text().split(",") if e.strip()
        )
        hint = self._txt_draft_hint.text().strip()

        return ExpectedOutcome(
            description=desc,
            required_elements=req_items,
            target_region=self._step.expected_outcome.target_region,
            verification_hint=hint,
        )

    def _build_draft_step(self) -> TaskStep:
        """Construct a TaskStep with draft outcome for verification."""
        draft_outcome = self._build_draft_outcome()
        return TaskStep(
            step_id=self._step.step_id,
            order=self._step.order,
            title=self._step.title,
            instruction=self._step.instruction,
            expected_outcome=draft_outcome,
            hints=self._step.hints,
            overlay_cues=self._step.overlay_cues,
        )

    @Slot()
    def _on_mode_changed(self) -> None:
        is_offline = self._rb_mode_offline.isChecked()
        self._frame_offline.setVisible(is_offline)
        if is_offline:
            self._btn_verify.setText("⚡ Run Simulated / Offline Verification")
            self._lbl_footer_status.setText("Offline mode: tests criteria without live AI calls.")
        else:
            self._btn_verify.setText("📸 Capture Screen & Test Verification")
            self._lbl_footer_status.setText("Live mode: captures screen and queries multimodal AI.")

    @Slot()
    def _on_test_verification_clicked(self) -> None:
        """Handle verification test execution in live or offline mode."""
        draft_step = self._build_draft_step()

        if self._rb_mode_offline.isChecked():
            self._run_offline_verification(draft_step)
        else:
            self._run_live_verification(draft_step)

    def _run_offline_verification(self, draft_step: TaskStep) -> None:
        """Run verification evaluation using mock/simulated scenario data."""
        preset = self._cmb_offline_preset.currentText()
        draft_outcome = draft_step.expected_outcome
        req_list = list(draft_outcome.required_elements)

        if "Pass: Complete" in preset:
            mock_data = {
                "status": "complete",
                "confidence": 0.95,
                "reasoning": (
                    f"All required elements for '{draft_step.title}' are clearly visible on screen."
                ),
                "observed_elements": req_list + ["Application Window", "Active Control"],
                "missing_elements": [],
                "next_instruction": "Proceed to next tutorial step.",
                "overlay_cues": [],
            }
        elif "Incomplete: Missing Elements" in preset:
            missing = req_list if req_list else ["Target Action"]
            mock_data = {
                "status": "incomplete",
                "confidence": 0.85,
                "reasoning": (
                    "The target application is displayed but expected actions/elements "
                    "have not occurred."
                ),
                "observed_elements": ["Desktop Window"],
                "missing_elements": missing,
                "next_instruction": f"Please perform step: {draft_step.instruction}",
                "overlay_cues": [],
            }
        elif "Uncertain: Low Confidence" in preset:
            mock_data = {
                "status": "complete",
                "confidence": 0.60,
                "reasoning": "Visual elements appear present but low contrast obscures details.",
                "observed_elements": req_list,
                "missing_elements": [],
                "next_instruction": "Check the screen carefully.",
                "overlay_cues": [],
            }
        else:  # Ambiguous Evidence
            mock_data = {
                "status": "complete",
                "confidence": 0.92,
                "reasoning": "The target element is unclear and occluded by another window.",
                "observed_elements": req_list,
                "missing_elements": [],
                "next_instruction": "Bring target window to the front.",
                "overlay_cues": [],
            }

        try:
            result = parse_verification_output(
                json.dumps(mock_data),
                task=self._task,
                step=draft_step,
            )
            self._render_verification_result(result, is_simulated=True)
            self._lbl_footer_status.setText(
                "Offline simulation complete (evaluated criteria policy without network inference)."
            )
        except Exception as exc:
            safe_err = redact_sensitive_text(str(exc))
            QMessageBox.critical(
                self,
                "Simulation Error",
                f"Failed to parse simulated output:\n\n{safe_err}",
            )

    def _run_live_verification(self, draft_step: TaskStep) -> None:
        """Capture screen/region with explicit user initiation and verify with AI."""
        if self._capturer is None:
            QMessageBox.critical(self, "No Capturer", "ScreenCapturer is not available.")
            return

        # Explicit user initiation
        try:
            target_region: CaptureRegion | None = None
            if self._chk_capture_region.isChecked():
                target_region = draft_step.expected_outcome.target_region

            if target_region is not None:
                image = self._capturer.capture_region(target_region, user_initiated=True)
            else:
                image = self._capturer.capture_full_screen(user_initiated=True)
        except UserActionRequiredError:
            self._lbl_footer_status.setText("Capture cancelled: explicit user initiation required.")
            return
        except Exception as exc:
            safe_err = redact_sensitive_text(str(exc))
            QMessageBox.critical(self, "Capture Error", f"Screen capture failed:\n\n{safe_err}")
            return

        if image is None:
            self._lbl_footer_status.setText("Capture cancelled.")
            return

        self._last_captured_image = image
        self._update_image_preview(image)

        if self._verifier is None:
            QMessageBox.warning(
                self,
                "No Verifier Configured",
                "No ProgressVerifier or multimodal LLM is configured.\n"
                "Switch to 'Simulated / Offline Mode' to test criteria schema without live AI.",
            )
            return

        self._lbl_footer_status.setText("Analyzing capture with multimodal verifier...")
        try:
            result = self._verifier.verify_step_with_image(
                task=self._task,
                step=draft_step,
                image=image,
            )
            self._render_verification_result(result, is_simulated=False)
            self._lbl_footer_status.setText("Live verification completed successfully.")
        except ConsentDeclinedError:
            self._lbl_status_badge.setText("CONSENT DECLINED")
            self._lbl_status_badge.setStyleSheet(
                "background-color: #64748b; color: white; font-weight: bold; font-size: 13px; "
                "padding: 4px 12px; border-radius: 4px;"
            )
            self._txt_explanation.setPlainText(
                "Screen-sharing consent was declined. No image or data was transmitted."
            )
            self._lbl_footer_status.setText("Verification cancelled: consent declined by author.")
        except Exception as exc:
            safe_err = redact_sensitive_text(str(exc))
            self._lbl_status_badge.setText("VERIFICATION ERROR")
            self._lbl_status_badge.setStyleSheet(
                "background-color: #b91c1c; color: white; font-weight: bold; font-size: 13px; "
                "padding: 4px 12px; border-radius: 4px;"
            )
            self._txt_explanation.setPlainText(f"Error during verification:\n{safe_err}")
            self._lbl_footer_status.setText("Verification failed.")

    def _update_image_preview(self, image: CapturedImage) -> None:
        """Update thumbnail and image metadata in memory."""
        w, h = image.width, image.height
        kb_size = image.byte_size / 1024
        self._lbl_image_info.setText(f"Image: {w}x{h} ({kb_size:.1f} KB, in-memory PNG)")

        qimg = QImage.fromData(image.png_bytes, "PNG")
        pix = QPixmap.fromImage(qimg).scaled(
            self._lbl_thumb.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._lbl_thumb.setPixmap(pix)

    def _render_verification_result(
        self,
        result: ProgressResult,
        *,
        is_simulated: bool = False,
    ) -> None:
        """Render ProgressResult diagnostics, confidence bar, and element checklist."""
        self._last_verification_result = result

        # 1. Status Badge
        prefix = "SIMULATED — " if is_simulated else ""
        if result.status == ProgressStatus.COMPLETE:
            badge_text = f"{prefix}PASS (COMPLETE)"
            badge_style = "background-color: #15803d; color: white;"
        elif result.status == ProgressStatus.INCOMPLETE:
            badge_text = f"{prefix}INCOMPLETE"
            badge_style = "background-color: #b45309; color: white;"
        else:
            badge_text = f"{prefix}UNCERTAIN"
            badge_style = "background-color: #6d28d9; color: white;"

        self._lbl_status_badge.setText(badge_text)
        self._lbl_status_badge.setStyleSheet(
            f"{badge_style} font-weight: bold; font-size: 13px; "
            "padding: 4px 12px; border-radius: 4px;"
        )

        # 2. Confidence Bar
        conf_pct = int(result.confidence * 100)
        self._bar_confidence.setValue(conf_pct)
        th_val = DEFAULT_MIN_COMPLETE_CONFIDENCE
        th_met = result.confidence >= th_val

        conf_color = "#15803d" if th_met else "#b45309"
        self._bar_confidence.setStyleSheet(
            "QProgressBar { border: 1px solid #cbd5e1; border-radius: 4px; background: #f1f5f9; }"
            f"QProgressBar::chunk {{ background-color: {conf_color}; border-radius: 3px; }}"
        )
        status_note = "meets threshold" if th_met else "below 0.75 threshold"
        self._lbl_confidence_val.setText(f"Confidence: {result.confidence:.2f} ({status_note})")

        # 3. Required Elements Checklist
        self._list_checklist.clear()
        draft_outcome = self._build_draft_outcome()
        observed_low = [o.lower() for o in result.observed_elements]

        if not draft_outcome.required_elements:
            item = QListWidgetItem("No required visual elements specified in criteria.")
            item.setForeground(QColor("#64748b"))
            self._list_checklist.addItem(item)
        else:
            for req in draft_outcome.required_elements:
                is_observed = any(req.lower() in obs for obs in observed_low)
                if is_observed:
                    item = QListWidgetItem(f"✔ {req} (Observed on screen)")
                    item.setForeground(QColor("#15803d"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                else:
                    item = QListWidgetItem(f"✖ {req} (MISSING / unconfirmed)")
                    item.setForeground(QColor("#b91c1c"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self._list_checklist.addItem(item)

        # 4. Explanation & Downgrade Analysis
        expl_lines: list[str] = []
        if is_simulated:
            expl_lines.append(
                "ℹ OFFLINE SIMULATION: Evaluates criteria schema without live AI inference."
            )

        if result.status == ProgressStatus.COMPLETE:
            expl_lines.append(
                f"• Step confirmed complete (confidence {result.confidence:.2f} >= {th_val:.2f}, "
                f"all required elements verified)."
            )
        elif result.status == ProgressStatus.INCOMPLETE:
            expl_lines.append("• Step is incomplete. Target action or result was not detected.")
            if result.missing_elements:
                expl_lines.append(f"  Missing elements: {', '.join(result.missing_elements)}")
        else:  # UNCERTAIN
            expl_lines.append(
                "• Step outcome is UNCERTAIN (evidence policy prevents false completion)."
            )
            if not th_met:
                expl_lines.append(
                    f"  - Confidence {result.confidence:.2f} is below required threshold "
                    f"{th_val:.2f}."
                )
            if result.missing_elements:
                expl_lines.append(
                    f"  - Missing elements reported: {', '.join(result.missing_elements)}"
                )

        self._txt_explanation.setPlainText(redact_sensitive_text("\n".join(expl_lines)))

        # 5. Model Reasoning (Redacted)
        self._txt_reasoning.setPlainText(redact_sensitive_text(result.reasoning))

    @Slot()
    def _on_apply_clicked(self) -> None:
        """Prompt author for confirmation and apply draft criteria to active step."""
        desc = self._txt_draft_desc.toPlainText().strip()
        if not desc:
            QMessageBox.warning(self, "Invalid Criteria", "Outcome description cannot be empty.")
            return

        draft_outcome = self._build_draft_outcome()

        res = QMessageBox.question(
            self,
            "Apply Changes to Step",
            f"Are you sure you want to apply the updated verification criteria to "
            f"Step {self._step.order} ('{self._step.title}')?\n\n"
            f"Description: {draft_outcome.description}\n"
            f"Required Elements: {', '.join(draft_outcome.required_elements) or 'None'}\n"
            f"Hint: {draft_outcome.verification_hint or 'None'}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            self.applied_outcome = draft_outcome
            self.accept()

    def _cleanup_retained_data(self) -> None:
        """Clear retained in-memory image and preview data on dismissal."""
        self._last_captured_image = None
        self._last_verification_result = None
        if hasattr(self, "_lbl_thumb"):
            self._lbl_thumb.clear()
            self._lbl_thumb.setText("No capture yet")
        if hasattr(self, "_lbl_image_info"):
            self._lbl_image_info.setText("Image: None (In-memory capture cleared)")

    def reject(self) -> None:
        """Discard draft criteria and clean up retained image data on reject."""
        self.applied_outcome = None
        self._cleanup_retained_data()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure closing the dialog discards drafts and cleans up image data."""
        self.applied_outcome = None
        self._cleanup_retained_data()
        super().closeEvent(event)

    def accept(self) -> None:
        """Clean up retained in-memory image on accept while keeping applied_outcome."""
        self._cleanup_retained_data()
        super().accept()
