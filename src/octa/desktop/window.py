"""Companion diagnostic and monitor window for OCTA."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
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
        inst = self._controller.voice_controller.last_instruction
        self._lbl_instruction.setText(f"Current Instruction: {inst.instruction_text}")

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
