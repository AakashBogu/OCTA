"""Interactive Author Step Runner & Playtest Sandbox (Phase 11.3).

Provides a dedicated, non-destructive author playtest workbench launched directly
from TutorialAuthoringDialog and PluginProjectDialog.

Features:
- Free step jumping (forward/back/direct selection) and step/tutorial reset.
- Author simulation overrides: "Force Step Complete" and "Simulate Failure".
- Learner-facing instruction and progressive hint disclosure simulation.
- Real-time visual guidance overlay rendering (HighlightSpec, ArrowSpec) on the active display.
- Godot 3D companion character state synchronization (IDLE, LISTENING, THINKING, SUCCESS).
- Strict isolation from normal learner behavior: zero feedback/metrics recording,
  zero screen capture, zero remote AI calls, zero voice recording, and zero input synthesis.
- Automatic overlay dismissal and companion return to IDLE on playtest exit.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from octa.intelligence.overlay import (
    GuidanceOverlayWidget,
    OverlayBounds,
)
from octa.intelligence.tasks import TaskDefinition, TaskStep

if TYPE_CHECKING:
    from octa.desktop.controller import DesktopController

logger = logging.getLogger("octa.desktop.tutorial_playtest")

STATUS_PENDING: str = "PENDING"
STATUS_COMPLETE: str = "SIMULATED_COMPLETE"
STATUS_FAILED: str = "SIMULATED_FAILED"


class TutorialPlaytestDialog(QDialog):
    """Author playtest workbench for step navigation, hint testing, and companion simulation."""

    def __init__(
        self,
        task: TaskDefinition,
        *,
        initial_step_index: int = 0,
        controller: DesktopController | None = None,
        display_bounds: OverlayBounds | None = None,
        high_contrast: bool | None = None,
        text_scale: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not task.steps:
            raise ValueError("TaskDefinition must contain at least one step for playtesting.")

        self._task = task
        self._controller = controller
        self._current_step_index = max(0, min(initial_step_index, len(task.steps) - 1))

        # Visual overlay parameters
        self._display_bounds = display_bounds
        if self._display_bounds is None and controller is not None:
            if hasattr(controller, "display_manager") and controller.display_manager:
                active_disp = getattr(controller.display_manager, "active_display", None)
                if (
                    active_disp is not None
                    and isinstance(getattr(active_disp, "x", None), int)
                    and not isinstance(getattr(active_disp, "x", None), bool)
                ):
                    self._display_bounds = OverlayBounds(
                        x=active_disp.x,
                        y=active_disp.y,
                        width=active_disp.width,
                        height=active_disp.height,
                    )

        self._high_contrast = (
            high_contrast
            if high_contrast is not None
            else (controller.accessibility_high_contrast if controller else False)
        )
        self._text_scale = (
            text_scale
            if text_scale is not None
            else (controller.accessibility_text_scale if controller else 1.0)
        )

        # Simulation state
        self._step_outcomes: dict[int, str] = {i: STATUS_PENDING for i in range(len(task.steps))}
        self._revealed_hints: dict[int, int] = {i: 0 for i in range(len(task.steps))}
        self._companion_state: str = "LISTENING"
        self._active_overlay: GuidanceOverlayWidget | None = None
        self._overlay_enabled: bool = True

        self.setWindowTitle(f"Playtest Sandbox — {task.title} ({task.task_id})")
        self.resize(920, 700)
        self.setMinimumSize(820, 580)
        self.setModal(True)

        self._init_ui()
        self._update_step_view()
        self._set_companion_state("LISTENING", reason="playtest_init")

    @property
    def current_step_index(self) -> int:
        """Currently active step index in playtest."""
        return self._current_step_index

    @property
    def current_step(self) -> TaskStep:
        """Currently active TaskStep."""
        return self._task.steps[self._current_step_index]

    @property
    def step_outcomes(self) -> dict[int, str]:
        """Dictionary of step index to simulated outcome status."""
        return dict(self._step_outcomes)

    @property
    def revealed_hints(self) -> dict[int, int]:
        """Dictionary of step index to count of revealed hints."""
        return dict(self._revealed_hints)

    @property
    def companion_state(self) -> str:
        """Current companion character state string."""
        return self._companion_state

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 14)

        # ── 1. Top Header Banner ──────────────────────────
        hdr_frame = QFrame(self)
        hdr_frame.setStyleSheet(
            "background-color: #f8fafc; border: 1px solid #cbd5e1; "
            "border-radius: 6px; padding: 4px;"
        )
        hdr_layout = QVBoxLayout(hdr_frame)
        hdr_layout.setSpacing(4)
        hdr_layout.setContentsMargins(8, 8, 8, 8)

        row_meta = QHBoxLayout()
        lbl_badge = QLabel("🛠️ AUTHOR PLAYTEST SANDBOX", hdr_frame)
        lbl_badge.setStyleSheet(
            "background: #475569; color: white; font-weight: bold; font-size: 11px; "
            "padding: 4px 10px; border-radius: 4px;"
        )
        lbl_badge.setAccessibleName("Author Playtest Sandbox Mode Badge")
        row_meta.addWidget(lbl_badge)

        lbl_task_info = QLabel(
            f"<b>{self._task.title}</b> <code>({self._task.task_id})</code> v{self._task.version} "
            f"· App: <i>{self._task.application}</i>",
            hdr_frame,
        )
        lbl_task_info.setStyleSheet("color: #1e293b; font-size: 13px;")
        row_meta.addWidget(lbl_task_info, stretch=1)
        hdr_layout.addLayout(row_meta)

        lbl_disclaimer = QLabel(
            "ℹ️ <b>Sandbox Isolation:</b> Free step jumping and simulated outcomes. "
            "Does <b>not</b> record learner feedback or session metrics, capture screen pixels, "
            "make remote AI calls, record microphone audio, or synthesize user input.",
            hdr_frame,
        )
        lbl_disclaimer.setStyleSheet("color: #64748b; font-size: 11px;")
        lbl_disclaimer.setWordWrap(True)
        hdr_layout.addWidget(lbl_disclaimer)
        main_layout.addWidget(hdr_frame)

        # ── 2. Step Navigation & Reset Bar ────────────────
        nav_box = QGroupBox("Step Navigation & Controls", self)
        nav_layout = QHBoxLayout(nav_box)
        nav_layout.setSpacing(8)

        self._btn_first = QPushButton("⏮ First", nav_box)
        self._btn_first.setAccessibleName("Go to First Step")
        self._btn_first.clicked.connect(self._on_first_clicked)
        nav_layout.addWidget(self._btn_first)

        self._btn_prev = QPushButton("◀ Prev", nav_box)
        self._btn_prev.setAccessibleName("Go to Previous Step")
        self._btn_prev.clicked.connect(self._on_prev_clicked)
        nav_layout.addWidget(self._btn_prev)

        self._cmb_steps = QComboBox(nav_box)
        self._cmb_steps.setAccessibleName("Select Tutorial Step")
        total_s = len(self._task.steps)
        for s in self._task.steps:
            self._cmb_steps.addItem(f"Step {s.order}/{total_s}: {s.title} ({s.step_id})")
        self._cmb_steps.currentIndexChanged.connect(self._on_step_combo_changed)
        nav_layout.addWidget(self._cmb_steps, stretch=1)

        self._btn_next = QPushButton("Next ▶", nav_box)
        self._btn_next.setAccessibleName("Go to Next Step")
        self._btn_next.clicked.connect(self._on_next_clicked)
        nav_layout.addWidget(self._btn_next)

        self._btn_last = QPushButton("Last ⏭", nav_box)
        self._btn_last.setAccessibleName("Go to Last Step")
        self._btn_last.clicked.connect(self._on_last_clicked)
        nav_layout.addWidget(self._btn_last)

        # Separator
        line = QFrame(nav_box)
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        nav_layout.addWidget(line)

        self._btn_reset_step = QPushButton("🔄 Reset Step", nav_box)
        self._btn_reset_step.setToolTip("Reset hints and outcome for current step.")
        self._btn_reset_step.setAccessibleName("Reset Current Step")
        self._btn_reset_step.clicked.connect(self._on_reset_step_clicked)
        nav_layout.addWidget(self._btn_reset_step)

        self._btn_reset_all = QPushButton("⏪ Reset All Steps", nav_box)
        self._btn_reset_all.setToolTip("Reset all step outcomes and return to Step 1.")
        self._btn_reset_all.setAccessibleName("Reset Entire Tutorial Playtest")
        self._btn_reset_all.clicked.connect(self._on_reset_all_clicked)
        nav_layout.addWidget(self._btn_reset_all)

        main_layout.addWidget(nav_box)

        # ── 3. Main Workspace Splitter (Learner View & Overrides) ──
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ── Left Column: Learner-Facing Simulation View ───
        left_widget = QWidget(splitter)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        grp_learner = QGroupBox("Learner View Simulation", left_widget)
        learner_layout = QVBoxLayout(grp_learner)
        learner_layout.setSpacing(8)

        self._lbl_step_header = QLabel("", grp_learner)
        self._lbl_step_header.setStyleSheet("color: #0f172a; font-size: 14px; font-weight: bold;")
        self._lbl_step_header.setAccessibleName("Current Step Title Header")
        learner_layout.addWidget(self._lbl_step_header)

        # Instruction display
        learner_layout.addWidget(QLabel("Instruction:", grp_learner))
        self._txt_instruction_view = QPlainTextEdit(grp_learner)
        self._txt_instruction_view.setReadOnly(True)
        self._txt_instruction_view.setMaximumHeight(85)
        self._txt_instruction_view.setStyleSheet(
            "background-color: #f8fafc; color: #1e293b; font-size: 12px; "
            "border: 1px solid #cbd5e1; border-radius: 4px;"
        )
        self._txt_instruction_view.setAccessibleName("Learner Instruction Text")
        learner_layout.addWidget(self._txt_instruction_view)

        # Progressive Hints Container
        grp_hints = QGroupBox("Progressive Hint Disclosure", grp_learner)
        hints_layout = QVBoxLayout(grp_hints)
        hints_layout.setSpacing(6)

        hint_ctrl_row = QHBoxLayout()
        self._lbl_hint_count = QLabel("Hints: 0 / 0 revealed", grp_hints)
        self._lbl_hint_count.setStyleSheet("color: #475569; font-weight: bold; font-size: 11px;")
        hint_ctrl_row.addWidget(self._lbl_hint_count, stretch=1)

        self._btn_reveal_hint = QPushButton("💡 Request Next Hint", grp_hints)
        self._btn_reveal_hint.setStyleSheet(
            "background-color: #f59e0b; color: white; font-weight: bold; "
            "padding: 4px 10px; border-radius: 4px;"
        )
        self._btn_reveal_hint.setAccessibleName("Reveal Next Hint")
        self._btn_reveal_hint.clicked.connect(self._on_reveal_hint_clicked)
        hint_ctrl_row.addWidget(self._btn_reveal_hint)
        hints_layout.addLayout(hint_ctrl_row)

        scroll_hints = QScrollArea(grp_hints)
        scroll_hints.setWidgetResizable(True)
        scroll_hints.setMaximumHeight(130)
        self._hints_container = QWidget()
        self._hints_container_layout = QVBoxLayout(self._hints_container)
        self._hints_container_layout.setContentsMargins(4, 4, 4, 4)
        self._hints_container_layout.setSpacing(4)
        scroll_hints.setWidget(self._hints_container)
        hints_layout.addWidget(scroll_hints)

        learner_layout.addWidget(grp_hints)

        # Guidance Overlay Toggle & Summary
        overlay_box = QFrame(grp_learner)
        overlay_box.setStyleSheet(
            "background-color: #f1f5f9; border: 1px solid #e2e8f0; "
            "border-radius: 4px; padding: 4px;"
        )
        overlay_box_layout = QHBoxLayout(overlay_box)
        overlay_box_layout.setContentsMargins(6, 4, 6, 4)

        self._chk_overlay = QCheckBox("👁️ Show Guidance Overlay on Screen", overlay_box)
        self._chk_overlay.setChecked(True)
        self._chk_overlay.setAccessibleName("Show Guidance Overlay Toggle")
        self._chk_overlay.toggled.connect(self._on_overlay_toggled)
        overlay_box_layout.addWidget(self._chk_overlay)

        self._lbl_cue_count = QLabel("0 visual cues", overlay_box)
        self._lbl_cue_count.setStyleSheet("color: #64748b; font-size: 11px;")
        overlay_box_layout.addWidget(self._lbl_cue_count, stretch=1)

        self._btn_refresh_overlay = QPushButton("🔄 Refresh Overlay", overlay_box)
        self._btn_refresh_overlay.setAccessibleName("Refresh On-Screen Visual Overlay")
        self._btn_refresh_overlay.clicked.connect(self._update_overlay)
        overlay_box_layout.addWidget(self._btn_refresh_overlay)
        learner_layout.addWidget(overlay_box)

        left_layout.addWidget(grp_learner)
        splitter.addWidget(left_widget)

        # ── Right Column: Author Overrides & Diagnostics ──
        right_widget = QWidget(splitter)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(8)

        grp_author = QGroupBox("Author Simulation Overrides & Criteria", right_widget)
        author_layout = QVBoxLayout(grp_author)
        author_layout.setSpacing(8)

        # Criteria Inspection Box
        author_layout.addWidget(QLabel("Verification Criteria (Expected Outcome):", grp_author))
        self._txt_outcome_view = QPlainTextEdit(grp_author)
        self._txt_outcome_view.setReadOnly(True)
        self._txt_outcome_view.setMaximumHeight(85)
        self._txt_outcome_view.setStyleSheet(
            "background-color: #f8fafc; color: #334155; font-size: 11px; "
            "border: 1px solid #cbd5e1; border-radius: 4px;"
        )
        self._txt_outcome_view.setAccessibleName("Expected Outcome Criteria")
        author_layout.addWidget(self._txt_outcome_view)

        # Status Badge
        badge_row = QHBoxLayout()
        badge_row.addWidget(QLabel("Simulated Status:", grp_author))
        self._lbl_status_badge = QLabel("PENDING / NOT COMPLETED", grp_author)
        self._lbl_status_badge.setStyleSheet(
            "background: #64748b; color: white; font-weight: bold; "
            "font-size: 11px; padding: 4px 10px; border-radius: 4px;"
        )
        self._lbl_status_badge.setAccessibleName("Simulated Step Outcome Status Badge")
        badge_row.addWidget(self._lbl_status_badge, stretch=1)
        author_layout.addLayout(badge_row)

        # Author Overrides Action Row
        override_row = QHBoxLayout()
        override_row.setSpacing(8)

        self._btn_force_complete = QPushButton("✔ Force Step Complete", grp_author)
        self._btn_force_complete.setStyleSheet(
            "background-color: #16a34a; color: white; font-weight: bold; "
            "padding: 6px 12px; border-radius: 4px;"
        )
        self._btn_force_complete.setToolTip(
            "Mark this step as complete and transition companion to SUCCESS."
        )
        self._btn_force_complete.setAccessibleName("Force Step Complete Author Override")
        self._btn_force_complete.clicked.connect(self._on_force_complete_clicked)
        override_row.addWidget(self._btn_force_complete)

        self._btn_simulate_fail = QPushButton("✖ Simulate Failure", grp_author)
        self._btn_simulate_fail.setStyleSheet(
            "background-color: #dc2626; color: white; font-weight: bold; "
            "padding: 6px 12px; border-radius: 4px;"
        )
        self._btn_simulate_fail.setToolTip(
            "Mark this step as incomplete/failed and transition companion to THINKING."
        )
        self._btn_simulate_fail.setAccessibleName("Simulate Failure Author Override")
        self._btn_simulate_fail.clicked.connect(self._on_simulate_fail_clicked)
        override_row.addWidget(self._btn_simulate_fail)
        author_layout.addLayout(override_row)

        lbl_sim_note = QLabel(
            "ℹ️ Outcomes are strictly simulated for author testing and cannot be mistaken "
            "for real AI verification.",
            grp_author,
        )
        lbl_sim_note.setStyleSheet("color: #64748b; font-size: 11px;")
        lbl_sim_note.setWordWrap(True)
        author_layout.addWidget(lbl_sim_note)

        # Companion Character State Row
        grp_companion = QGroupBox("Companion Character State (Godot 3D Robot)", grp_author)
        comp_layout = QVBoxLayout(grp_companion)
        comp_layout.setSpacing(6)

        comp_badge_row = QHBoxLayout()
        comp_badge_row.addWidget(QLabel("Current State:", grp_companion))
        self._lbl_companion_badge = QLabel("LISTENING", grp_companion)
        self._lbl_companion_badge.setStyleSheet(
            "background: #0284c7; color: white; font-weight: bold; "
            "font-size: 11px; padding: 3px 8px; border-radius: 4px;"
        )
        self._lbl_companion_badge.setAccessibleName("Companion Robot State Badge")
        comp_badge_row.addWidget(self._lbl_companion_badge, stretch=1)
        comp_layout.addLayout(comp_badge_row)

        comp_btn_row = QHBoxLayout()
        comp_btn_row.setSpacing(4)
        self._btn_comp_idle = QPushButton("IDLE", grp_companion)
        self._btn_comp_idle.clicked.connect(
            lambda: self._set_companion_state("IDLE", "manual_test")
        )
        self._btn_comp_listening = QPushButton("LISTENING", grp_companion)
        self._btn_comp_listening.clicked.connect(
            lambda: self._set_companion_state("LISTENING", "manual_test")
        )
        self._btn_comp_thinking = QPushButton("THINKING", grp_companion)
        self._btn_comp_thinking.clicked.connect(
            lambda: self._set_companion_state("THINKING", "manual_test")
        )
        self._btn_comp_success = QPushButton("SUCCESS", grp_companion)
        self._btn_comp_success.clicked.connect(
            lambda: self._set_companion_state("SUCCESS", "manual_test")
        )
        comp_btn_row.addWidget(self._btn_comp_idle)
        comp_btn_row.addWidget(self._btn_comp_listening)
        comp_btn_row.addWidget(self._btn_comp_thinking)
        comp_btn_row.addWidget(self._btn_comp_success)
        comp_layout.addLayout(comp_btn_row)

        author_layout.addWidget(grp_companion)
        author_layout.addStretch()
        right_layout.addWidget(grp_author)
        splitter.addWidget(right_widget)

        main_layout.addWidget(splitter, stretch=1)

        # ── 4. Bottom Footer Bar ──────────────────────────
        bot_bar = QHBoxLayout()
        self._lbl_footer_status = QLabel("Ready.", self)
        self._lbl_footer_status.setStyleSheet("color: #64748b; font-size: 11px;")
        bot_bar.addWidget(self._lbl_footer_status, stretch=1)

        self._btn_exit = QPushButton("&Exit Playtest", self)
        self._btn_exit.setAccessibleName("Exit Playtest Sandbox")
        self._btn_exit.clicked.connect(self.accept)
        bot_bar.addWidget(self._btn_exit)
        main_layout.addLayout(bot_bar)

    # ──────────────────────────────────────────────────────
    # State Synchronization & UI Updating
    # ──────────────────────────────────────────────────────

    def _update_step_view(self) -> None:
        """Update all widgets to reflect the currently selected step."""
        step = self.current_step
        total = len(self._task.steps)
        idx = self._current_step_index

        # Update Navigation buttons state
        self._btn_first.setEnabled(idx > 0)
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < total - 1)
        self._btn_last.setEnabled(idx < total - 1)

        # Synchronize combo box without recursive signals
        if self._cmb_steps.currentIndex() != idx:
            self._cmb_steps.blockSignals(True)
            self._cmb_steps.setCurrentIndex(idx)
            self._cmb_steps.blockSignals(False)

        # Update Step Header & Instruction
        self._lbl_step_header.setText(
            f"Step {step.order} of {total}: {step.title} <code>({step.step_id})</code>"
        )
        self._txt_instruction_view.setPlainText(step.instruction)

        # Update Hints
        self._update_hints_view()

        # Update Verification Criteria
        reqs = ", ".join(step.expected_outcome.required_elements) or "(None)"
        hint = step.expected_outcome.verification_hint or "(None)"
        criteria_text = (
            f"Description: {step.expected_outcome.description}\n"
            f"Required Elements: {reqs}\n"
            f"Verification Hint: {hint}"
        )
        self._txt_outcome_view.setPlainText(criteria_text)

        # Update Status Badge
        outcome = self._step_outcomes.get(idx, STATUS_PENDING)
        if outcome == STATUS_COMPLETE:
            self._lbl_status_badge.setText("✔ SIMULATED: STEP COMPLETE (Author Override)")
            self._lbl_status_badge.setStyleSheet(
                "background: #16a34a; color: white; font-weight: bold; "
                "font-size: 11px; padding: 4px 10px; border-radius: 4px;"
            )
        elif outcome == STATUS_FAILED:
            self._lbl_status_badge.setText("✖ SIMULATED: CRITERIA UNMET / FAILED")
            self._lbl_status_badge.setStyleSheet(
                "background: #dc2626; color: white; font-weight: bold; "
                "font-size: 11px; padding: 4px 10px; border-radius: 4px;"
            )
        else:
            self._lbl_status_badge.setText("PENDING / NOT COMPLETED")
            self._lbl_status_badge.setStyleSheet(
                "background: #64748b; color: white; font-weight: bold; "
                "font-size: 11px; padding: 4px 10px; border-radius: 4px;"
            )

        # Update Cues Count & Overlay
        cue_cnt = len(step.overlay_cues)
        self._lbl_cue_count.setText(f"{cue_cnt} visual cue(s) defined")
        self._update_overlay()

        self._lbl_footer_status.setText(f"Viewing Step {step.order}/{total}: {step.title}.")

    def _update_hints_view(self) -> None:
        """Render progressive hints revealed so far for the active step."""
        step = self.current_step
        idx = self._current_step_index
        total_hints = len(step.hints)
        revealed_count = self._revealed_hints.get(idx, 0)

        self._lbl_hint_count.setText(f"Hints: {revealed_count} / {total_hints} revealed")
        self._btn_reveal_hint.setEnabled(revealed_count < total_hints)

        # Clear hints container layout
        while self._hints_container_layout.count():
            item = self._hints_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if total_hints == 0:
            lbl_none = QLabel("No progressive hints defined for this step.", self._hints_container)
            lbl_none.setStyleSheet("color: #94a3b8; font-style: italic; font-size: 11px;")
            self._hints_container_layout.addWidget(lbl_none)
        elif revealed_count == 0:
            lbl_click = QLabel(
                "Click 'Request Next Hint' to simulate learner hint progression.",
                self._hints_container,
            )
            lbl_click.setStyleSheet("color: #64748b; font-size: 11px;")
            self._hints_container_layout.addWidget(lbl_click)
        else:
            for i in range(revealed_count):
                h_text = step.hints[i]
                h_frame = QFrame(self._hints_container)
                h_frame.setStyleSheet(
                    "background: #fffbeb; border: 1px solid #fef3c7; "
                    "border-radius: 4px; padding: 4px;"
                )
                h_layout = QVBoxLayout(h_frame)
                h_layout.setContentsMargins(6, 4, 6, 4)
                lbl_h = QLabel(f"<b>Hint {i + 1}:</b> {h_text}", h_frame)
                lbl_h.setStyleSheet("color: #92400e; font-size: 11px;")
                lbl_h.setWordWrap(True)
                h_layout.addWidget(lbl_h)
                self._hints_container_layout.addWidget(h_frame)

    def _update_overlay(self) -> None:
        """Update passive click-through overlay guidance cues on the active screen."""
        if not self._overlay_enabled or not self._chk_overlay.isChecked():
            if self._active_overlay is not None:
                self._active_overlay.clear_annotations()
            return

        step = self.current_step
        cues = list(step.overlay_cues)
        if not cues:
            if self._active_overlay is not None:
                self._active_overlay.clear_annotations()
            return

        if self._active_overlay is None:
            bounds = self._display_bounds or OverlayBounds(x=0, y=0, width=1920, height=1080)
            self._active_overlay = GuidanceOverlayWidget(
                screen_bounds=bounds,
                high_contrast=self._high_contrast,
                text_scale=self._text_scale,
            )

        self._active_overlay.apply_hints(cues)
        self._active_overlay.show()

    def _set_companion_state(self, state: str, reason: str = "") -> None:
        """Update companion robot state in UI and request Godot state transition via controller."""
        if state not in {"IDLE", "LISTENING", "THINKING", "SUCCESS"}:
            return
        self._companion_state = state

        # Color coding for state badges
        bg_colors = {
            "IDLE": "#64748b",
            "LISTENING": "#0284c7",
            "THINKING": "#d97706",
            "SUCCESS": "#16a34a",
        }
        self._lbl_companion_badge.setText(state)
        self._lbl_companion_badge.setStyleSheet(
            f"background: {bg_colors.get(state, '#64748b')}; color: white; "
            "font-weight: bold; font-size: 11px; padding: 3px 8px; border-radius: 4px;"
        )

        if self._controller is not None and hasattr(self._controller, "request_companion_state"):
            self._controller.request_companion_state(state, reason=reason)

    # ──────────────────────────────────────────────────────
    # Action Slots
    # ──────────────────────────────────────────────────────

    @Slot()
    def _on_first_clicked(self) -> None:
        if self._current_step_index != 0:
            self._current_step_index = 0
            self._set_companion_state("LISTENING", reason="nav_first")
            self._update_step_view()

    @Slot()
    def _on_prev_clicked(self) -> None:
        if self._current_step_index > 0:
            self._current_step_index -= 1
            self._set_companion_state("LISTENING", reason="nav_prev")
            self._update_step_view()

    @Slot()
    def _on_next_clicked(self) -> None:
        if self._current_step_index < len(self._task.steps) - 1:
            self._current_step_index += 1
            self._set_companion_state("LISTENING", reason="nav_next")
            self._update_step_view()

    @Slot()
    def _on_last_clicked(self) -> None:
        last_idx = len(self._task.steps) - 1
        if self._current_step_index != last_idx:
            self._current_step_index = last_idx
            self._set_companion_state("LISTENING", reason="nav_last")
            self._update_step_view()

    @Slot(int)
    def _on_step_combo_changed(self, index: int) -> None:
        if 0 <= index < len(self._task.steps) and index != self._current_step_index:
            self._current_step_index = index
            self._set_companion_state("LISTENING", reason="nav_jump")
            self._update_step_view()

    @Slot()
    def _on_reset_step_clicked(self) -> None:
        """Reset outcomes and hints for current step."""
        idx = self._current_step_index
        self._step_outcomes[idx] = STATUS_PENDING
        self._revealed_hints[idx] = 0
        self._set_companion_state("LISTENING", reason="step_reset")
        self._update_step_view()
        self._lbl_footer_status.setText(f"Reset Step {idx + 1} to initial uncompleted state.")

    @Slot()
    def _on_reset_all_clicked(self) -> None:
        """Reset all steps in the tutorial playtest and jump to Step 1."""
        for i in range(len(self._task.steps)):
            self._step_outcomes[i] = STATUS_PENDING
            self._revealed_hints[i] = 0
        self._current_step_index = 0
        self._set_companion_state("LISTENING", reason="all_reset")
        self._update_step_view()
        self._lbl_footer_status.setText("All steps reset. Returned to Step 1.")

    @Slot()
    def _on_force_complete_clicked(self) -> None:
        """Mark current step as complete and transition companion to SUCCESS."""
        idx = self._current_step_index
        self._step_outcomes[idx] = STATUS_COMPLETE
        self._set_companion_state("SUCCESS", reason="force_step_complete")
        self._update_step_view()

        # Check if all steps are completed
        all_done = all(s == STATUS_COMPLETE for s in self._step_outcomes.values())
        if all_done:
            self._lbl_footer_status.setText(
                "🎉 Simulated: All tutorial steps marked complete in author sandbox!"
            )
        else:
            self._lbl_footer_status.setText(
                f"Simulated: Step {idx + 1} marked complete. Companion transitioned to SUCCESS."
            )

    @Slot()
    def _on_simulate_fail_clicked(self) -> None:
        """Mark current step as failed/criteria unmet and transition companion to THINKING."""
        idx = self._current_step_index
        self._step_outcomes[idx] = STATUS_FAILED
        self._set_companion_state("THINKING", reason="simulate_failure")
        self._update_step_view()
        self._lbl_footer_status.setText(
            f"Simulated: Step {idx + 1} criteria unmet. Companion transitioned to THINKING."
        )

    @Slot()
    def _on_reveal_hint_clicked(self) -> None:
        """Progressive hint disclosure: reveal next hint for current step."""
        idx = self._current_step_index
        step = self.current_step
        total = len(step.hints)
        revealed = self._revealed_hints.get(idx, 0)
        if revealed < total:
            self._revealed_hints[idx] = revealed + 1
            self._set_companion_state("THINKING", reason="hint_revealed")
            self._update_hints_view()
            self._lbl_footer_status.setText(
                f"Revealed Hint {revealed + 1} of {total} for Step {idx + 1}."
            )

    @Slot(bool)
    def _on_overlay_toggled(self, checked: bool) -> None:
        """Toggle on-screen guidance overlay visibility."""
        self._overlay_enabled = checked
        if not checked and self._active_overlay is not None:
            self._active_overlay.clear_annotations()
            self._lbl_footer_status.setText("Guidance overlay hidden.")
        else:
            self._update_overlay()
            self._lbl_footer_status.setText("Guidance overlay enabled.")

    # ──────────────────────────────────────────────────────
    # Cleanup & Teardown
    # ──────────────────────────────────────────────────────

    def _cleanup_playtest(self) -> None:
        """Dismiss on-screen overlay cues and return companion character to IDLE."""
        if self._active_overlay is not None:
            self._active_overlay.close()
            self._active_overlay = None
        self._set_companion_state("IDLE", reason="playtest_exit")

    def reject(self) -> None:
        """Ensure clean teardown on cancel / escape."""
        self._cleanup_playtest()
        super().reject()

    def accept(self) -> None:
        """Ensure clean teardown on exit / accept."""
        self._cleanup_playtest()
        super().accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Ensure clean teardown on window close."""
        self._cleanup_playtest()
        super().closeEvent(event)
