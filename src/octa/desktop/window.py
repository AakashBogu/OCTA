"""Companion diagnostic and monitor window for OCTA."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from octa.desktop.controller import DesktopController
from octa.desktop.plugin_manager_dialog import PluginManagerDialog
from octa.process.state import ProcessState


class TutorialFeedbackDialog(QDialog):
    """User feedback dialog for rating tutorials and recording qualitative observations."""

    def __init__(
        self,
        task_title: str,
        step_title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("OCTA — Tutorial Feedback")
        self.resize(380, 320)
        self.setModal(True)
        self._task_title = task_title
        self._step_title = step_title

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel(f"Feedback for: {self._task_title}\nStep: {self._step_title}", self)
        header.setWordWrap(True)
        layout.addWidget(header)

        # Rating
        rating_layout = QHBoxLayout()
        rating_label = QLabel("Rating:", self)
        self._combo_rating = QComboBox(self)
        self._combo_rating.addItems(
            ["5 - Excellent", "4 - Good", "3 - Average", "2 - Needs Work", "1 - Confusing"]
        )
        rating_layout.addWidget(rating_label)
        rating_layout.addWidget(self._combo_rating, stretch=1)
        layout.addLayout(rating_layout)

        # Difficulty
        diff_layout = QHBoxLayout()
        diff_label = QLabel("Difficulty:", self)
        self._combo_difficulty = QComboBox(self)
        self._combo_difficulty.addItem("Just Right", "just_right")
        self._combo_difficulty.addItem("Easy", "easy")
        self._combo_difficulty.addItem("Challenging", "hard")
        diff_layout.addWidget(diff_label)
        diff_layout.addWidget(self._combo_difficulty, stretch=1)
        layout.addLayout(diff_layout)

        # Comment
        comment_label = QLabel("Comments or Suggestions:", self)
        self._txt_comment = QPlainTextEdit(self)
        self._txt_comment.setPlaceholderText("What worked well or what was confusing? (optional)")
        layout.addWidget(comment_label)
        layout.addWidget(self._txt_comment, stretch=1)

        # Buttons
        btn_layout = QHBoxLayout()
        self._btn_submit = QPushButton("&Submit Feedback", self)
        self._btn_cancel = QPushButton("&Cancel", self)
        self._btn_submit.clicked.connect(self.accept)
        self._btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self._btn_cancel)
        btn_layout.addWidget(self._btn_submit)
        layout.addLayout(btn_layout)

        # Accessibility and keyboard navigation
        self._combo_rating.setAccessibleName("Tutorial Rating")
        self._combo_rating.setAccessibleDescription(
            "Select a score from 1 (Confusing) to 5 (Excellent)"
        )
        self._combo_difficulty.setAccessibleName("Step Difficulty")
        self._combo_difficulty.setAccessibleDescription(
            "Select perceived difficulty: Just Right, Easy, or Challenging"
        )
        self._txt_comment.setAccessibleName("Feedback Comments")
        self._txt_comment.setAccessibleDescription(
            "Optional user comments or suggestions about the tutorial step"
        )
        self._btn_submit.setAccessibleName("Submit Feedback")
        self._btn_cancel.setAccessibleName("Cancel Feedback")

        self.setTabOrder(self._combo_rating, self._combo_difficulty)
        self.setTabOrder(self._combo_difficulty, self._txt_comment)
        self.setTabOrder(self._txt_comment, self._btn_submit)
        self.setTabOrder(self._btn_submit, self._btn_cancel)

    def get_rating(self) -> int:
        """Return selected rating from 1 to 5."""
        idx = self._combo_rating.currentIndex()
        return 5 - idx

    def get_difficulty(self) -> str:
        """Return selected difficulty slug ('easy', 'just_right', 'hard')."""
        return str(self._combo_difficulty.currentData())

    def get_comment(self) -> str:
        """Return free-form user comment string."""
        return self._txt_comment.toPlainText().strip()


class DiagnosticWindow(QWidget):
    """Diagnostic monitor window displaying real-time process, server, and robot state."""

    def __init__(
        self,
        controller: DesktopController,
        *,
        tray_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._tray_enabled = tray_enabled

        self.setWindowTitle("OCTA — Companion Diagnostic Monitor")
        self.resize(600, 560)
        self.setMinimumSize(480, 420)
        self._current_text_scale = float(self._controller.accessibility_text_scale)

        self._build_ui()
        self._connect_signals()
        self._refresh_all()
        self.apply_text_scale(self._current_text_scale)

    def _build_ui(self) -> None:
        """Construct window layout and child widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ── Status Group ─────────────────────────────────────
        self._status_group = QGroupBox("Status & Diagnostics", self)
        status_layout = QVBoxLayout(self._status_group)
        status_layout.setSpacing(6)

        self._lbl_process = QLabel("Process: STOPPED", self)
        self._lbl_server = QLabel("Server: STOPPED", self)
        self._lbl_url = QLabel("URL: Not listening", self)
        self._lbl_client = QLabel("Client: Disconnected", self)
        self._lbl_robot = QLabel("Robot State: IDLE", self)

        # Styling
        label_font = QFont()
        label_font.setPointSize(10)
        for lbl in (
            self._lbl_process,
            self._lbl_server,
            self._lbl_url,
            self._lbl_client,
            self._lbl_robot,
        ):
            lbl.setFont(label_font)
            status_layout.addWidget(lbl)

        main_layout.addWidget(self._status_group)

        # ── Action Buttons ───────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._btn_start = QPushButton("&Start Companion", self)
        self._btn_stop = QPushButton("S&top Companion", self)
        self._btn_restart = QPushButton("&Restart", self)
        self._btn_clear = QPushButton("&Clear Log", self)

        self._btn_start.clicked.connect(self._controller.start)
        self._btn_stop.clicked.connect(self._controller.stop)
        self._btn_restart.clicked.connect(self._controller.restart)
        self._btn_clear.clicked.connect(self._clear_log)

        btn_layout.addWidget(self._btn_start)
        btn_layout.addWidget(self._btn_stop)
        btn_layout.addWidget(self._btn_restart)
        btn_layout.addWidget(self._btn_clear)
        main_layout.addLayout(btn_layout)

        # ── Voice Interaction (Push-to-Talk) ─────────────────
        self._voice_group = QGroupBox("Voice Interaction (Push-to-Talk Only)", self)
        voice_layout = QVBoxLayout(self._voice_group)
        voice_layout.setSpacing(6)

        self._lbl_voice_providers = QLabel(
            self._controller.voice_controller.get_provider_status_summary(),
            self,
        )
        self._lbl_mic_status = QLabel("Microphone: INACTIVE (Push-to-Talk Only)", self)
        self._lbl_voice_state = QLabel("Voice State: IDLE", self)
        initial_instruction = self._controller.voice_controller.last_instruction.instruction_text
        self._lbl_instruction = QLabel(
            f"Current Instruction: {initial_instruction}",
            self,
        )
        self._lbl_instruction.setWordWrap(True)

        for v_lbl in (
            self._lbl_voice_providers,
            self._lbl_mic_status,
            self._lbl_voice_state,
            self._lbl_instruction,
        ):
            v_lbl.setFont(label_font)
            voice_layout.addWidget(v_lbl)

        voice_btn_layout = QHBoxLayout()
        voice_btn_layout.setSpacing(8)

        self._btn_ptt = QPushButton("&Push to Talk", self)
        self._btn_read_aloud = QPushButton("&Read Instruction Aloud", self)
        self._btn_voice_cancel = QPushButton("Stop / &Cancel Voice", self)

        self._btn_ptt.clicked.connect(self._on_ptt_clicked)
        self._btn_read_aloud.clicked.connect(self._on_read_aloud_clicked)
        self._btn_voice_cancel.clicked.connect(self._controller.cancel_voice)

        voice_btn_layout.addWidget(self._btn_ptt)
        voice_btn_layout.addWidget(self._btn_read_aloud)
        voice_btn_layout.addWidget(self._btn_voice_cancel)
        voice_layout.addLayout(voice_btn_layout)

        main_layout.addWidget(self._voice_group)

        # ── Tutorial Guide & Progress Verification (Phase 6.0) ─
        self._tutorial_group = QGroupBox("Tutorial Guide & Progress Verification", self)
        tutorial_layout = QVBoxLayout(self._tutorial_group)
        tutorial_layout.setSpacing(6)

        selector_row = QHBoxLayout()
        selector_row.setSpacing(8)
        selector_label = QLabel("Tutorial:", self)
        selector_label.setFont(label_font)
        self._combo_tutorial = QComboBox(self)
        self._populate_tutorial_combo()
        self._combo_tutorial.currentIndexChanged.connect(self._on_tutorial_combo_changed)
        self._btn_reset_tutorial = QPushButton("&Reset Tutorial", self)
        self._btn_reset_tutorial.clicked.connect(self._on_reset_tutorial_clicked)
        self._btn_manage_plugins = QPushButton("&Plugins...", self)
        self._btn_manage_plugins.clicked.connect(self._on_manage_plugins_clicked)

        selector_row.addWidget(selector_label)
        selector_row.addWidget(self._combo_tutorial, stretch=1)
        selector_row.addWidget(self._btn_reset_tutorial)
        selector_row.addWidget(self._btn_manage_plugins)
        tutorial_layout.addLayout(selector_row)

        monitor_row = QHBoxLayout()
        monitor_row.setSpacing(8)
        monitor_label = QLabel("Monitor:", self)
        monitor_label.setFont(label_font)
        self._combo_monitor = QComboBox(self)
        self._populate_monitor_combo()
        self._combo_monitor.currentIndexChanged.connect(self._on_monitor_combo_changed)
        self._btn_cue_selector = QPushButton("&Cue Selector...", self)
        self._btn_cue_selector.clicked.connect(self._on_cue_selector_clicked)
        self._btn_tutorial_builder = QPushButton("Tutorial &Builder...", self)
        self._btn_tutorial_builder.clicked.connect(self._on_tutorial_builder_clicked)
        monitor_row.addWidget(monitor_label)
        monitor_row.addWidget(self._combo_monitor, stretch=1)
        monitor_row.addWidget(self._btn_cue_selector)
        monitor_row.addWidget(self._btn_tutorial_builder)
        tutorial_layout.addLayout(monitor_row)

        self._lbl_tutorial_status = QLabel(
            self._controller.tutorial_engine.get_tutorial_status_summary(),
            self,
        )
        self._lbl_tutorial_status.setWordWrap(True)
        self._lbl_verification_status = QLabel(
            self._controller.tutorial_engine.get_verification_mode_summary(),
            self,
        )
        self._lbl_verification_status.setWordWrap(True)

        for t_lbl in (self._lbl_tutorial_status, self._lbl_verification_status):
            t_lbl.setFont(label_font)
            tutorial_layout.addWidget(t_lbl)

        step_nav_layout = QHBoxLayout()
        step_nav_layout.setSpacing(8)
        self._btn_prev_step = QPushButton("&Previous Step", self)
        self._btn_next_step = QPushButton("&Next Step", self)
        self._btn_hint = QPushButton("Show &Hint", self)
        self._btn_complete_manual = QPushButton("&Complete Step (Text Flow)", self)

        self._btn_prev_step.clicked.connect(self._on_prev_step_clicked)
        self._btn_next_step.clicked.connect(self._on_next_step_clicked)
        self._btn_hint.clicked.connect(self._on_hint_clicked)
        self._btn_complete_manual.clicked.connect(self._on_complete_manual_clicked)

        step_nav_layout.addWidget(self._btn_prev_step)
        step_nav_layout.addWidget(self._btn_next_step)
        step_nav_layout.addWidget(self._btn_hint)
        step_nav_layout.addWidget(self._btn_complete_manual)
        tutorial_layout.addLayout(step_nav_layout)

        verify_btn_layout = QHBoxLayout()
        verify_btn_layout.setSpacing(8)
        self._btn_verify_ai = QPushButton("Verify Step (Capture & AI)", self)
        self._btn_toggle_overlay = QPushButton("Enable Overlay", self)
        self._btn_cancel_verify = QPushButton("Cancel Verification", self)
        self._btn_feedback = QPushButton("Give Feedback", self)

        self._btn_verify_ai.clicked.connect(self._on_verify_ai_clicked)
        self._btn_toggle_overlay.clicked.connect(self._on_toggle_overlay_clicked)
        self._btn_cancel_verify.clicked.connect(self._on_cancel_verify_clicked)
        self._btn_feedback.clicked.connect(self._on_feedback_clicked)

        verify_btn_layout.addWidget(self._btn_verify_ai)
        verify_btn_layout.addWidget(self._btn_toggle_overlay)
        verify_btn_layout.addWidget(self._btn_cancel_verify)
        verify_btn_layout.addWidget(self._btn_feedback)
        tutorial_layout.addLayout(verify_btn_layout)

        main_layout.addWidget(self._tutorial_group)

        # ── Accessibility Adaptations (Phase 8.3) ────────────
        self._a11y_group = QGroupBox("Accessibility & Display Adaptations", self)
        a11y_layout = QVBoxLayout(self._a11y_group)
        a11y_layout.setSpacing(6)

        a11y_row1 = QHBoxLayout()
        a11y_row1.setSpacing(12)

        self._chk_high_contrast = QCheckBox("&High-Contrast Overlay Cues", self)
        self._chk_high_contrast.setChecked(self._controller.accessibility_high_contrast)
        self._chk_high_contrast.toggled.connect(self._on_high_contrast_toggled)

        self._chk_sound_cues = QCheckBox("Tutorial Completion &Audio Chime", self)
        self._chk_sound_cues.setChecked(self._controller.accessibility_sound_cues)
        self._chk_sound_cues.toggled.connect(self._on_sound_cues_toggled)

        self._btn_test_sound = QPushButton("&Test Audio Cue", self)
        self._btn_test_sound.clicked.connect(self._on_test_sound_clicked)

        a11y_row1.addWidget(self._chk_high_contrast)
        a11y_row1.addWidget(self._chk_sound_cues)
        a11y_row1.addWidget(self._btn_test_sound)
        a11y_row1.addStretch(1)
        a11y_layout.addLayout(a11y_row1)

        a11y_row2 = QHBoxLayout()
        a11y_row2.setSpacing(8)
        self._lbl_text_size = QLabel("Text Size:", self)
        self._lbl_text_size.setFont(label_font)
        self._combo_text_size = QComboBox(self)
        self._combo_text_size.addItem("Normal (100%)", 1.0)
        self._combo_text_size.addItem("Medium (125%)", 1.25)
        self._combo_text_size.addItem("Large (150%)", 1.5)
        self._combo_text_size.addItem("Extra Large (200%)", 2.0)
        self._sync_text_size_combo()
        self._combo_text_size.currentIndexChanged.connect(self._on_text_size_combo_changed)

        a11y_row2.addWidget(self._lbl_text_size)
        a11y_row2.addWidget(self._combo_text_size, stretch=1)
        a11y_layout.addLayout(a11y_row2)

        main_layout.addWidget(self._a11y_group)

        # ── Activity & Error Log ─────────────────────────────
        self._log_group = QGroupBox("Activity & Diagnostics Log", self)
        log_layout = QVBoxLayout(self._log_group)
        log_layout.setContentsMargins(8, 8, 8, 8)

        self._log_view = QPlainTextEdit(self)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(1000)
        log_font = QFont("Consolas", 9)
        log_font.setStyleHint(QFont.StyleHint.Monospace)
        self._log_view.setFont(log_font)

        log_layout.addWidget(self._log_view)
        main_layout.addWidget(self._log_group, stretch=1)

        self._setup_accessibility()

    def _connect_signals(self) -> None:
        """Connect to controller signals for live updates."""
        self._controller.status_changed.connect(self._on_status_changed)
        self._controller.server_started.connect(self._on_server_started)
        self._controller.server_stopped.connect(self._on_server_stopped)
        self._controller.client_authenticated.connect(self._on_client_authenticated)
        self._controller.client_disconnected.connect(self._on_client_disconnected)
        self._controller.robot_state_changed.connect(self._on_robot_state_changed)
        self._controller.log_message.connect(self._append_log)
        self._controller.voice_state_changed.connect(self._on_voice_state_changed)
        self._controller.microphone_active_changed.connect(self._on_microphone_active_changed)
        self._controller.speaking_active_changed.connect(self._on_speaking_active_changed)
        self._controller.instruction_updated.connect(self._on_instruction_updated)
        self._controller.tutorial_state_changed.connect(self._on_tutorial_state_changed)
        self._controller.tutorial_step_changed.connect(self._on_tutorial_step_changed)
        self._controller.verification_completed.connect(self._on_verification_completed)
        self._controller.tutorials_catalog_reloaded.connect(self._populate_tutorial_combo)
        self._controller.displays_changed.connect(self._on_displays_changed)
        self._controller.active_display_changed.connect(self._on_active_display_changed)
        self._controller.accessibility_changed.connect(self._on_accessibility_changed)
        self._controller.tutorial_completed.connect(self._on_tutorial_completed)

    def _setup_accessibility(self) -> None:
        """Configure accessible names, descriptions, focus policies, and tab navigation."""
        # Accessible names & descriptions
        self._btn_start.setAccessibleName("Start Companion")
        self._btn_start.setAccessibleDescription("Launch the Godot desktop companion window")
        self._btn_stop.setAccessibleName("Stop Companion")
        self._btn_stop.setAccessibleDescription("Shut down the Godot companion process")
        self._btn_restart.setAccessibleName("Restart Companion")
        self._btn_restart.setAccessibleDescription("Stop and restart the Godot companion process")
        self._btn_clear.setAccessibleName("Clear Log")
        self._btn_clear.setAccessibleDescription("Clear the diagnostic activity log")

        self._btn_ptt.setAccessibleName("Push to Talk")
        self._btn_ptt.setAccessibleDescription("Activate microphone recording while pressed")
        self._btn_read_aloud.setAccessibleName("Read Instruction Aloud")
        self._btn_read_aloud.setAccessibleDescription(
            "Synthesize and speak the current tutorial instruction"
        )
        self._btn_voice_cancel.setAccessibleName("Stop Voice")
        self._btn_voice_cancel.setAccessibleDescription(
            "Cancel active speech recognition or speech output"
        )

        self._combo_tutorial.setAccessibleName("Tutorial Selector")
        self._combo_tutorial.setAccessibleDescription(
            "Choose active tutorial from built-in or plugin tutorials"
        )
        self._btn_reset_tutorial.setAccessibleName("Reset Tutorial")
        self._btn_reset_tutorial.setAccessibleDescription(
            "Reset the active tutorial back to step 1"
        )
        self._btn_manage_plugins.setAccessibleName("Manage Plugins")
        self._btn_manage_plugins.setAccessibleDescription(
            "Open the Tutorial Plugin Manager to inspect, install, or uninstall packages"
        )
        self._combo_monitor.setAccessibleName("Monitor Selector")
        self._combo_monitor.setAccessibleDescription(
            "Select which display monitor is targeted for captures and overlay cues"
        )
        self._btn_cue_selector.setAccessibleName("Open Visual Cue Authoring Studio")
        self._btn_cue_selector.setAccessibleDescription(
            "Visually select or fine-tune highlight and arrow cues on screen"
        )
        self._btn_tutorial_builder.setAccessibleName("Tutorial Builder")
        self._btn_tutorial_builder.setAccessibleDescription(
            "Create, inspect, and author sequential interactive tutorials and visual cues"
        )

        self._btn_prev_step.setAccessibleName("Previous Step")
        self._btn_prev_step.setAccessibleDescription("Navigate back to the previous tutorial step")
        self._btn_next_step.setAccessibleName("Next Step")
        self._btn_next_step.setAccessibleDescription("Navigate forward to the next tutorial step")
        self._btn_hint.setAccessibleName("Show Hint")
        self._btn_hint.setAccessibleDescription(
            "Display hint and visual overlay for current tutorial step"
        )
        self._btn_complete_manual.setAccessibleName("Complete Step Manually")
        self._btn_complete_manual.setAccessibleDescription(
            "Mark current step as completed without AI verification"
        )
        self._btn_verify_ai.setAccessibleName("Verify Step with AI")
        self._btn_verify_ai.setAccessibleDescription(
            "Capture screen and verify completion using multimodal AI"
        )
        self._btn_toggle_overlay.setAccessibleName("Toggle Visual Overlay")
        self._btn_toggle_overlay.setAccessibleDescription(
            "Show or hide the transparent desktop guidance overlay"
        )
        self._btn_cancel_verify.setAccessibleName("Cancel Verification")
        self._btn_cancel_verify.setAccessibleDescription("Cancel in-flight background verification")
        self._btn_feedback.setAccessibleName("Give Tutorial Feedback")
        self._btn_feedback.setAccessibleDescription(
            "Open rating and feedback dialog for active step"
        )
        self._btn_feedback.setShortcut("Alt+F")
        self._btn_verify_ai.setShortcut("Alt+V")

        self._chk_high_contrast.setAccessibleName("High-Contrast Overlay Cues")
        self._chk_high_contrast.setAccessibleDescription(
            "Enable dual-stroke outlines and shape indicators on visual overlay cues"
        )
        self._chk_sound_cues.setAccessibleName("Tutorial Completion Sound Cues")
        self._chk_sound_cues.setAccessibleDescription(
            "Play an ascending audio chime when a tutorial is completed"
        )
        self._btn_test_sound.setAccessibleName("Test Audio Chime")
        self._btn_test_sound.setAccessibleDescription("Play a test audio completion chime")
        self._combo_text_size.setAccessibleName("Text Size Scaling")
        self._combo_text_size.setAccessibleDescription(
            "Adjust font size scaling across the interface and overlay"
        )
        self._log_view.setAccessibleName("Activity and Diagnostics Log")
        self._log_view.setAccessibleDescription(
            "Live log output of companion, server, and tutorial events"
        )

        # Focus policies
        for interactive_widget in (
            self._btn_start,
            self._btn_stop,
            self._btn_restart,
            self._btn_clear,
            self._btn_ptt,
            self._btn_read_aloud,
            self._btn_voice_cancel,
            self._combo_tutorial,
            self._btn_reset_tutorial,
            self._btn_manage_plugins,
            self._combo_monitor,
            self._btn_cue_selector,
            self._btn_tutorial_builder,
            self._btn_prev_step,
            self._btn_next_step,
            self._btn_hint,
            self._btn_complete_manual,
            self._btn_verify_ai,
            self._btn_toggle_overlay,
            self._btn_cancel_verify,
            self._btn_feedback,
            self._chk_high_contrast,
            self._chk_sound_cues,
            self._btn_test_sound,
            self._combo_text_size,
            self._log_view,
        ):
            interactive_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Tab order
        self.setTabOrder(self._btn_start, self._btn_stop)
        self.setTabOrder(self._btn_stop, self._btn_restart)
        self.setTabOrder(self._btn_restart, self._btn_clear)
        self.setTabOrder(self._btn_clear, self._btn_ptt)
        self.setTabOrder(self._btn_ptt, self._btn_read_aloud)
        self.setTabOrder(self._btn_read_aloud, self._btn_voice_cancel)
        self.setTabOrder(self._btn_voice_cancel, self._combo_tutorial)
        self.setTabOrder(self._combo_tutorial, self._btn_reset_tutorial)
        self.setTabOrder(self._btn_reset_tutorial, self._btn_manage_plugins)
        self.setTabOrder(self._btn_manage_plugins, self._combo_monitor)
        self.setTabOrder(self._combo_monitor, self._btn_cue_selector)
        self.setTabOrder(self._btn_cue_selector, self._btn_tutorial_builder)
        self.setTabOrder(self._btn_tutorial_builder, self._btn_prev_step)
        self.setTabOrder(self._btn_prev_step, self._btn_next_step)
        self.setTabOrder(self._btn_next_step, self._btn_hint)
        self.setTabOrder(self._btn_hint, self._btn_complete_manual)
        self.setTabOrder(self._btn_complete_manual, self._btn_verify_ai)
        self.setTabOrder(self._btn_verify_ai, self._btn_toggle_overlay)
        self.setTabOrder(self._btn_toggle_overlay, self._btn_cancel_verify)
        self.setTabOrder(self._btn_cancel_verify, self._btn_feedback)
        self.setTabOrder(self._btn_feedback, self._chk_high_contrast)
        self.setTabOrder(self._chk_high_contrast, self._chk_sound_cues)
        self.setTabOrder(self._chk_sound_cues, self._btn_test_sound)
        self.setTabOrder(self._btn_test_sound, self._combo_text_size)
        self.setTabOrder(self._combo_text_size, self._log_view)

    def apply_text_scale(self, scale: float) -> None:
        """Scale font sizes across diagnostic window elements dynamically."""
        clamped = max(0.5, min(3.0, float(scale)))
        self._current_text_scale = clamped

        base_pt = max(8, int(round(10.0 * clamped)))
        log_pt = max(7, int(round(9.0 * clamped)))
        header_pt = max(9, int(round(11.0 * clamped)))

        label_font = QFont()
        label_font.setPointSize(base_pt)

        header_font = QFont()
        header_font.setPointSize(header_pt)
        header_font.setBold(True)

        for group in (
            self._status_group,
            self._voice_group,
            self._tutorial_group,
            self._a11y_group,
            self._log_group,
        ):
            group.setFont(header_font)

        for lbl in (
            self._lbl_process,
            self._lbl_server,
            self._lbl_url,
            self._lbl_client,
            self._lbl_robot,
            self._lbl_voice_providers,
            self._lbl_mic_status,
            self._lbl_voice_state,
            self._lbl_instruction,
            self._lbl_tutorial_status,
            self._lbl_verification_status,
            self._lbl_text_size,
        ):
            lbl.setFont(label_font)

        for btn in (
            self._btn_start,
            self._btn_stop,
            self._btn_restart,
            self._btn_clear,
            self._btn_ptt,
            self._btn_read_aloud,
            self._btn_voice_cancel,
            self._btn_reset_tutorial,
            self._btn_manage_plugins,
            self._btn_cue_selector,
            self._btn_tutorial_builder,
            self._btn_prev_step,
            self._btn_next_step,
            self._btn_hint,
            self._btn_complete_manual,
            self._btn_verify_ai,
            self._btn_toggle_overlay,
            self._btn_cancel_verify,
            self._btn_feedback,
            self._btn_test_sound,
        ):
            btn.setFont(label_font)

        for combo in (self._combo_tutorial, self._combo_monitor, self._combo_text_size):
            combo.setFont(label_font)

        self._chk_high_contrast.setFont(label_font)
        self._chk_sound_cues.setFont(label_font)

        log_font = QFont("Consolas", log_pt)
        log_font.setStyleHint(QFont.StyleHint.Monospace)
        self._log_view.setFont(log_font)

        min_w = int(round(480 * max(1.0, clamped * 0.85)))
        min_h = int(round(420 * max(1.0, clamped * 0.85)))
        self.setMinimumSize(min_w, min_h)

    # ──────────────────────────────────────────
    # UI Update Slots
    # ──────────────────────────────────────────

    def _refresh_all(self) -> None:
        """Refresh all diagnostic labels and control buttons."""
        self._update_process_display()
        self._update_server_display()
        self._update_url_display()
        self._update_client_display()
        self._update_robot_display()
        self._update_voice_display()
        self._update_tutorial_display()
        self._update_buttons()

    def _update_process_display(self) -> None:
        state = self._controller.process_state.value
        pid = self._controller.process_manager.pid
        pid_suffix = f" (PID: {pid})" if pid else ""
        self._lbl_process.setText(f"Process: {state}{pid_suffix}")

    def _update_server_display(self) -> None:
        state = self._controller.server_state.value
        self._lbl_server.setText(f"Server: {state}")

    def _update_url_display(self) -> None:
        url = self._controller.server_url
        if url:
            port = self._controller.server_port
            self._lbl_url.setText(f"URL: {url} (Port: {port})")
        else:
            self._lbl_url.setText("URL: Not listening")

    def _update_client_display(self) -> None:
        if self._controller.is_client_authenticated:
            self._lbl_client.setText("Client: Authenticated (Godot Client)")
        else:
            self._lbl_client.setText("Client: Disconnected")

    def _update_robot_display(self) -> None:
        state = self._controller.robot_state
        self._lbl_robot.setText(f"Robot State: {state}")

    def _update_voice_display(self) -> None:
        self._lbl_voice_providers.setText(
            self._controller.voice_controller.get_provider_status_summary()
        )
        mic_active = self._controller.is_microphone_active
        self._on_microphone_active_changed(mic_active)
        self._on_voice_state_changed(self._controller.voice_state)
        inst = self._controller.tutorial_engine.current_instruction
        self._lbl_instruction.setText(f"Current Instruction: {inst.instruction_text}")

    def _update_tutorial_display(self) -> None:
        engine = self._controller.tutorial_engine
        self._lbl_tutorial_status.setText(engine.get_tutorial_status_summary())
        last_err = engine.last_error
        if last_err:
            self._lbl_verification_status.setText(f"Verification: {last_err}")
        else:
            self._lbl_verification_status.setText(engine.get_verification_mode_summary())

        self._btn_toggle_overlay.setText(
            "Disable Overlay" if engine.is_overlay_enabled else "Enable Overlay"
        )
        self._btn_cancel_verify.setEnabled(engine.is_busy)
        self._btn_verify_ai.setEnabled(not engine.is_busy)

    def _update_buttons(self) -> None:
        is_running = self._controller.process_manager.is_running
        is_starting = self._controller.process_state == ProcessState.STARTING
        is_stopping = self._controller.process_state == ProcessState.STOPPING

        if is_stopping:
            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(False)
            self._btn_restart.setEnabled(False)
        elif is_running or is_starting or self._controller.server_url:
            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(True)
            self._btn_restart.setEnabled(True)
        else:
            self._btn_start.setEnabled(True)
            self._btn_stop.setEnabled(False)
            self._btn_restart.setEnabled(False)

    @Slot(str, str, str)
    def _on_status_changed(self, proc: str, srv: str, rbt: str) -> None:
        pid = self._controller.process_manager.pid
        pid_suffix = f" (PID: {pid})" if pid else ""
        self._lbl_process.setText(f"Process: {proc}{pid_suffix}")
        self._lbl_server.setText(f"Server: {srv}")
        self._lbl_robot.setText(f"Robot State: {rbt}")
        self._update_url_display()
        self._update_buttons()

    @Slot(str, int, str)
    def _on_server_started(self, _host: str, port: int, url: str) -> None:
        self._lbl_url.setText(f"URL: {url} (Port: {port})")
        self._lbl_server.setText("Server: LISTENING")
        self._update_buttons()

    @Slot()
    def _on_server_stopped(self) -> None:
        self._lbl_url.setText("URL: Not listening")
        self._lbl_server.setText("Server: STOPPED")
        self._update_buttons()

    @Slot(str, str)
    def _on_client_authenticated(self, name: str, version: str) -> None:
        self._lbl_client.setText(f"Client: {name} v{version} (Authenticated)")
        self._update_buttons()

    @Slot()
    def _on_client_disconnected(self) -> None:
        self._lbl_client.setText("Client: Disconnected")
        self._update_buttons()

    @Slot(str, str)
    def _on_robot_state_changed(self, _prev: str, new_state: str) -> None:
        self._lbl_robot.setText(f"Robot State: {new_state}")

    @Slot(str)
    def _on_voice_state_changed(self, new_state: str) -> None:
        self._lbl_voice_providers.setText(
            self._controller.voice_controller.get_provider_status_summary()
        )
        last_err = self._controller.voice_controller.last_error
        if new_state == "ERROR" and last_err:
            self._lbl_voice_state.setText(f"Voice State: ERROR — {last_err}")
        else:
            self._lbl_voice_state.setText(f"Voice State: {new_state}")

    @Slot(bool)
    def _on_microphone_active_changed(self, active: bool) -> None:
        if active:
            self._lbl_mic_status.setText("Microphone: RECORDING (LIVE — Click Stop or Release)")
            self._lbl_mic_status.setStyleSheet("color: #dc2626; font-weight: bold;")
            self._btn_ptt.setText("Release / Finish Push to Talk")
        else:
            self._lbl_mic_status.setText("Microphone: INACTIVE (Push-to-Talk Only)")
            self._lbl_mic_status.setStyleSheet("")
            self._btn_ptt.setText("Push to Talk")

    @Slot(bool)
    def _on_speaking_active_changed(self, _speaking: bool) -> None:
        self._on_voice_state_changed(self._controller.voice_state)

    @Slot(str, str)
    def _on_instruction_updated(self, _step_id: str, instruction_text: str) -> None:
        self._lbl_instruction.setText(f"Current Instruction: {instruction_text}")

    @Slot(str)
    def _on_tutorial_state_changed(self, _new_state: str) -> None:
        self._update_tutorial_display()

    @Slot(int, int, str, str)
    def _on_tutorial_step_changed(
        self,
        _step_num: int,
        _total_steps: int,
        _step_id: str,
        instruction_text: str,
    ) -> None:
        self._lbl_instruction.setText(f"Current Instruction: {instruction_text}")
        self._update_tutorial_display()

    @Slot(object)
    def _on_verification_completed(self, _result: object) -> None:
        self._update_tutorial_display()

    @Slot()
    def _populate_tutorial_combo(self) -> None:
        """Populate or refresh the tutorial combo box items, indicating plugin provenance."""
        current_data = (
            self._combo_tutorial.currentData() if self._combo_tutorial.count() > 0 else None
        )
        self._combo_tutorial.blockSignals(True)
        self._combo_tutorial.clear()
        engine = self._controller.tutorial_engine
        for task_def in engine.available_tutorials:
            provenance = engine.get_tutorial_provenance(task_def.task_id)
            prov_prefix = f"[Plugin: {provenance}] " if provenance else ""
            self._combo_tutorial.addItem(
                f"{prov_prefix}{task_def.title} ({task_def.application})",
                userData=task_def.task_id,
            )
        if current_data is not None:
            idx = self._combo_tutorial.findData(current_data)
            if idx >= 0:
                self._combo_tutorial.setCurrentIndex(idx)
        self._combo_tutorial.blockSignals(False)

    @Slot()
    def _populate_monitor_combo(self) -> None:
        """Populate or refresh the monitor selector dropdown."""
        self._combo_monitor.blockSignals(True)
        self._combo_monitor.clear()
        displays = self._controller.display_manager.displays
        active_idx = self._controller.display_manager.active_index
        selected_row = 0
        for idx, display in enumerate(displays):
            self._combo_monitor.addItem(display.display_label, userData=display.index)
            if display.index == active_idx:
                selected_row = idx
        if self._combo_monitor.count() > 0:
            self._combo_monitor.setCurrentIndex(selected_row)
        self._combo_monitor.blockSignals(False)

    @Slot(int)
    def _on_monitor_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        disp_idx = self._combo_monitor.itemData(index)
        if isinstance(disp_idx, int):
            self._controller.select_display(disp_idx)

    @Slot(tuple)
    def _on_displays_changed(self, _displays: tuple[Any, ...]) -> None:
        self._populate_monitor_combo()

    @Slot(object)
    def _on_active_display_changed(self, _display: Any) -> None:
        self._populate_monitor_combo()

    @Slot(int)
    def _on_tutorial_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        task_id = self._combo_tutorial.itemData(index)
        if isinstance(task_id, str) and task_id:
            try:
                self._controller.select_tutorial(task_id)
            except Exception:
                pass
            self._update_tutorial_display()

    @Slot()
    def _on_reset_tutorial_clicked(self) -> None:
        try:
            self._controller.reset_tutorial()
        finally:
            self._update_tutorial_display()

    @Slot()
    def _on_manage_plugins_clicked(self) -> None:
        dlg = PluginManagerDialog(self._controller, parent=self)
        dlg.exec()

    @Slot()
    def _on_cue_selector_clicked(self) -> None:
        """Open the Visual Cue Authoring Studio dialog."""
        from octa.intelligence.authoring_overlay import AuthoringCueDialog

        dlg = AuthoringCueDialog(
            display_manager=self._controller.display_manager,
            high_contrast=self._controller.accessibility_high_contrast,
            text_scale=self._controller.accessibility_text_scale,
            parent=self,
        )
        dlg.exec()

    @Slot()
    def _on_tutorial_builder_clicked(self) -> None:
        """Open the Interactive Step & Tutorial Builder dialog."""
        from octa.desktop.tutorial_authoring_dialog import TutorialAuthoringDialog

        dlg = TutorialAuthoringDialog(
            controller=self._controller,
            parent=self,
        )
        dlg.exec()

    @Slot()
    def _on_prev_step_clicked(self) -> None:
        try:
            self._controller.previous_tutorial_step()
        finally:
            self._update_tutorial_display()

    @Slot()
    def _on_next_step_clicked(self) -> None:
        try:
            self._controller.next_tutorial_step()
        finally:
            self._update_tutorial_display()

    @Slot()
    def _on_hint_clicked(self) -> None:
        try:
            self._controller.show_tutorial_hint()
        finally:
            self._update_tutorial_display()

    @Slot()
    def _on_complete_manual_clicked(self) -> None:
        try:
            self._controller.complete_step_manually(user_initiated=True)
        finally:
            self._update_tutorial_display()

    @Slot()
    def _on_verify_ai_clicked(self) -> None:
        try:
            self._controller.verify_current_step_with_ai(user_initiated=True)
        except Exception:
            pass
        finally:
            self._update_tutorial_display()

    @Slot()
    def _on_toggle_overlay_clicked(self) -> None:
        engine = self._controller.tutorial_engine
        self._controller.set_overlay_enabled(not engine.is_overlay_enabled)
        self._update_tutorial_display()

    @Slot()
    def _on_cancel_verify_clicked(self) -> None:
        self._controller.cancel_tutorial_operation()
        self._update_tutorial_display()

    @Slot()
    def _on_feedback_clicked(self) -> None:
        """Open the feedback dialog to record qualitative rating and comments."""
        engine = self._controller.tutorial_engine
        step = engine.current_step
        dialog = TutorialFeedbackDialog(
            task_title=engine.active_task.title,
            step_title=f"Step {step.order}: {step.title}",
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._controller.record_tutorial_feedback(
                rating=dialog.get_rating(),
                difficulty=dialog.get_difficulty(),
                comment=dialog.get_comment(),
                step_id=step.step_id,
            )
            self._append_log(
                f"Recorded user feedback ({dialog.get_rating()} stars, {dialog.get_difficulty()}).",
                "INFO",
            )

    @Slot(bool)
    def _on_high_contrast_toggled(self, checked: bool) -> None:
        self._controller.set_high_contrast(checked)

    @Slot(bool)
    def _on_sound_cues_toggled(self, checked: bool) -> None:
        self._controller.set_sound_cues_enabled(checked)

    @Slot()
    def _on_test_sound_clicked(self) -> None:
        played = self._controller.play_completion_sound_cue()
        if not played:
            self._append_log(
                "Test audio chime skipped (sound cues disabled or unavailable).",
                "INFO",
            )

    @Slot(int)
    def _on_text_size_combo_changed(self, index: int) -> None:
        if index < 0:
            return
        scale_val = self._combo_text_size.itemData(index)
        if isinstance(scale_val, (int, float)):
            self.apply_text_scale(float(scale_val))
            self._controller.set_text_scale(float(scale_val))

    def _sync_text_size_combo(self) -> None:
        current_scale = self._controller.accessibility_text_scale
        best_idx = 0
        min_diff = 999.0
        for i in range(self._combo_text_size.count()):
            val = float(self._combo_text_size.itemData(i))
            diff = abs(val - current_scale)
            if diff < min_diff:
                min_diff = diff
                best_idx = i
        self._combo_text_size.blockSignals(True)
        self._combo_text_size.setCurrentIndex(best_idx)
        self._combo_text_size.blockSignals(False)

    @Slot(bool, float, bool)
    def _on_accessibility_changed(
        self,
        high_contrast: bool,
        text_scale: float,
        sound_cues: bool,
    ) -> None:
        if self._chk_high_contrast.isChecked() != high_contrast:
            self._chk_high_contrast.blockSignals(True)
            self._chk_high_contrast.setChecked(high_contrast)
            self._chk_high_contrast.blockSignals(False)

        if self._chk_sound_cues.isChecked() != sound_cues:
            self._chk_sound_cues.blockSignals(True)
            self._chk_sound_cues.setChecked(sound_cues)
            self._chk_sound_cues.blockSignals(False)

        if abs(self._current_text_scale - text_scale) > 1e-4:
            self._sync_text_size_combo()
            self.apply_text_scale(text_scale)

    @Slot(str, str)
    def _on_tutorial_completed(self, task_id: str, title: str) -> None:
        self._append_log(f"Tutorial '{title}' ({task_id}) marked complete!", "INFO")

    @Slot()
    def _on_ptt_clicked(self) -> None:
        """Toggle push-to-talk recording from an explicit user button click."""
        try:
            if self._controller.is_microphone_active:
                self._controller.stop_push_to_talk()
            else:
                self._controller.start_push_to_talk(user_initiated=True)
        except Exception:
            self._update_voice_display()

    @Slot()
    def _on_read_aloud_clicked(self) -> None:
        """Trigger reading the current instruction aloud from an explicit user click."""
        try:
            self._controller.read_current_instruction_aloud(user_initiated=True)
        except Exception:
            self._update_voice_display()

    @Slot(str, str, str)
    def _append_log(
        self,
        timestamp_or_msg: str,
        message: str = "",
        level: str = "INFO",
    ) -> None:
        if not message:
            ts = datetime.now().strftime("%H:%M:%S")
            msg = timestamp_or_msg
            lvl = level
        elif level == "INFO" and message in {"INFO", "WARNING", "ERROR", "DEBUG"}:
            ts = datetime.now().strftime("%H:%M:%S")
            msg = timestamp_or_msg
            lvl = message
        else:
            ts = timestamp_or_msg
            msg = message
            lvl = level
        entry = f"[{ts}] [{lvl}] {msg}"
        self._log_view.appendPlainText(entry)

    def _clear_log(self) -> None:
        self._log_view.clear()

    # ──────────────────────────────────────────
    # Window Behavior
    # ──────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide window if system tray is available, else accept close."""
        if self._tray_enabled and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
        else:
            event.accept()

    # ──────────────────────────────────────────
    # Diagnostic Inspection Helpers for Tests
    # ──────────────────────────────────────────

    def get_process_status(self) -> str:
        """Return text of the process label."""
        return self._lbl_process.text()

    def get_server_status(self) -> str:
        """Return text of the server label."""
        return self._lbl_server.text()

    def get_loopback_url(self) -> str:
        """Return text of the URL label."""
        return self._lbl_url.text()

    def get_client_status(self) -> str:
        """Return text of the client label."""
        return self._lbl_client.text()

    def get_robot_state(self) -> str:
        """Return text of the robot state label."""
        return self._lbl_robot.text()

    def get_log_text(self) -> str:
        """Return the complete content of the log view."""
        return self._log_view.toPlainText()

    def get_voice_provider_status(self) -> str:
        """Return text of the voice providers status label."""
        return self._lbl_voice_providers.text()

    def get_microphone_status(self) -> str:
        """Return text of the microphone activity indicator label."""
        return self._lbl_mic_status.text()

    def get_voice_state(self) -> str:
        """Return text of the voice state label."""
        return self._lbl_voice_state.text()

    def get_current_instruction_text(self) -> str:
        """Return text of the current instruction label."""
        return self._lbl_instruction.text()

    def is_microphone_indicator_active(self) -> bool:
        """Return True when the UI microphone indicator shows live recording."""
        return "RECORDING" in self._lbl_mic_status.text()

    def get_tutorial_status(self) -> str:
        """Return text of the tutorial progress & status label."""
        return self._lbl_tutorial_status.text()

    def get_verification_status(self) -> str:
        """Return text of the verification mode & status label."""
        return self._lbl_verification_status.text()

    def get_selected_tutorial_id(self) -> str:
        """Return the task_id of the currently selected tutorial in the combo box."""
        data = self._combo_tutorial.currentData()
        return str(data) if data is not None else ""

    def get_selected_monitor_index(self) -> int:
        """Return the display index of the currently selected monitor in the combo box."""
        data = self._combo_monitor.currentData()
        return int(data) if data is not None else 0

    def get_monitor_combo_items(self) -> list[str]:
        """Return list of text labels in the monitor selection combo box."""
        return [self._combo_monitor.itemText(i) for i in range(self._combo_monitor.count())]

    def is_high_contrast_checked(self) -> bool:
        """Return True if high-contrast checkbox is checked."""
        return self._chk_high_contrast.isChecked()

    def is_sound_cues_checked(self) -> bool:
        """Return True if tutorial completion audio chime checkbox is checked."""
        return self._chk_sound_cues.isChecked()

    def get_selected_text_scale(self) -> float:
        """Return current text scaling factor from the UI selector."""
        data = self._combo_text_size.currentData()
        return float(data) if data is not None else 1.0
