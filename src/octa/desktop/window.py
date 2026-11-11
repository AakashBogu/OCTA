"""Companion diagnostic and monitor window for OCTA."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
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
        self._btn_submit = QPushButton("Submit Feedback", self)
        self._btn_cancel = QPushButton("Cancel", self)
        self._btn_submit.clicked.connect(self.accept)
        self._btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self._btn_cancel)
        btn_layout.addWidget(self._btn_submit)
        layout.addLayout(btn_layout)

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
        self.resize(580, 480)
        self.setMinimumSize(450, 360)

        self._build_ui()
        self._connect_signals()
        self._refresh_all()

    def _build_ui(self) -> None:
        """Construct window layout and child widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ── Status Group ─────────────────────────────────────
        status_group = QGroupBox("Status & Diagnostics", self)
        status_layout = QVBoxLayout(status_group)
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

        main_layout.addWidget(status_group)

        # ── Action Buttons ───────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._btn_start = QPushButton("Start Companion", self)
        self._btn_stop = QPushButton("Stop Companion", self)
        self._btn_restart = QPushButton("Restart", self)
        self._btn_clear = QPushButton("Clear Log", self)

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
        voice_group = QGroupBox("Voice Interaction (Push-to-Talk Only)", self)
        voice_layout = QVBoxLayout(voice_group)
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

        self._btn_ptt = QPushButton("Push to Talk", self)
        self._btn_read_aloud = QPushButton("Read Instruction Aloud", self)
        self._btn_voice_cancel = QPushButton("Stop / Cancel Voice", self)

        self._btn_ptt.clicked.connect(self._on_ptt_clicked)
        self._btn_read_aloud.clicked.connect(self._on_read_aloud_clicked)
        self._btn_voice_cancel.clicked.connect(self._controller.cancel_voice)

        voice_btn_layout.addWidget(self._btn_ptt)
        voice_btn_layout.addWidget(self._btn_read_aloud)
        voice_btn_layout.addWidget(self._btn_voice_cancel)
        voice_layout.addLayout(voice_btn_layout)

        main_layout.addWidget(voice_group)

        # ── Tutorial Guide & Progress Verification (Phase 6.0) ─
        tutorial_group = QGroupBox("Tutorial Guide & Progress Verification", self)
        tutorial_layout = QVBoxLayout(tutorial_group)
        tutorial_layout.setSpacing(6)

        selector_row = QHBoxLayout()
        selector_row.setSpacing(8)
        selector_label = QLabel("Tutorial:", self)
        selector_label.setFont(label_font)
        self._combo_tutorial = QComboBox(self)
        for task_def in self._controller.tutorial_engine.available_tutorials:
            self._combo_tutorial.addItem(
                f"{task_def.title} ({task_def.application})",
                userData=task_def.task_id,
            )
        self._combo_tutorial.currentIndexChanged.connect(self._on_tutorial_combo_changed)
        self._btn_reset_tutorial = QPushButton("Reset Tutorial", self)
        self._btn_reset_tutorial.clicked.connect(self._on_reset_tutorial_clicked)

        selector_row.addWidget(selector_label)
        selector_row.addWidget(self._combo_tutorial, stretch=1)
        selector_row.addWidget(self._btn_reset_tutorial)
        tutorial_layout.addLayout(selector_row)

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
        self._btn_prev_step = QPushButton("Previous Step", self)
        self._btn_next_step = QPushButton("Next Step", self)
        self._btn_hint = QPushButton("Show Hint", self)
        self._btn_complete_manual = QPushButton("Complete Step (Text Flow)", self)

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

        main_layout.addWidget(tutorial_group)

        # ── Activity & Error Log ─────────────────────────────
        log_group = QGroupBox("Activity & Diagnostics Log", self)
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 8, 8, 8)

        self._log_view = QPlainTextEdit(self)
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(1000)
        log_font = QFont("Consolas", 9)
        log_font.setStyleHint(QFont.StyleHint.Monospace)
        self._log_view.setFont(log_font)

        log_layout.addWidget(self._log_view)
        main_layout.addWidget(log_group, stretch=1)

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
    def _append_log(self, timestamp: str, message: str, level: str) -> None:
        # Guarantee auth token is never rendered
        entry = f"[{timestamp}] [{level}] {message}"
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
