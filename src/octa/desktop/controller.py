"""Desktop controller orchestrating GodotProcessManager and WebSocketServer."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QProcess, QTimer, Signal, Slot

from octa.communication.handoff import prepare_process_environment
from octa.communication.server import WebSocketServer
from octa.communication.state import ConnectionState
from octa.core.config import AppConfig
from octa.intelligence.display import DisplayInfo, DisplayManager
from octa.intelligence.tasks import GuidanceInstruction
from octa.process.exceptions import ProcessError
from octa.process.manager import GodotProcessManager
from octa.process.state import ProcessState
from octa.tutorials.engine import TutorialEngine
from octa.tutorials.feedback import TutorialFeedback, TutorialSessionMetrics
from octa.voice.controller import VoiceInteractionController

DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_MS = 1500
DEFAULT_STOP_TIMEOUT_MS = 3000
DEFAULT_SERVER_CLOSE_TIMEOUT_MS = 1000


class DesktopController(QObject):
    """Central controller coordinating the Godot process and WebSocket server."""

    # UI notification signals
    status_changed = Signal(str, str, str)  # process_state, server_state, robot_state
    server_started = Signal(str, int, str)  # host, port, url
    server_stopped = Signal()
    client_authenticated = Signal(str, str)  # client_name, client_version
    client_disconnected = Signal()
    robot_state_changed = Signal(str, str)  # previous_state, new_state
    log_message = Signal(str, str, str)  # timestamp, message, level
    error_occurred = Signal(str)
    voice_state_changed = Signal(str)  # IDLE, RECORDING, TRANSCRIBING, PROCESSING, SPEAKING, ERROR
    microphone_active_changed = Signal(bool)  # True when push-to-talk mic is recording
    speaking_active_changed = Signal(bool)  # True when TTS playback is active
    instruction_updated = Signal(str, str)  # step_id, instruction_text
    tutorial_state_changed = Signal(
        str
    )  # IDLE, ACTIVE, VERIFYING, GENERATING_GUIDANCE, COMPLETED, ERROR
    tutorial_step_changed = Signal(
        int, int, str, str
    )  # step_num, total_steps, step_id, instruction
    verification_completed = Signal(object)  # ProgressResult
    feedback_recorded = Signal(object)  # TutorialFeedback
    tutorials_catalog_reloaded = Signal()  # Emitted when tutorial plugins or catalog are reloaded
    displays_changed = Signal(tuple)  # tuple[DisplayInfo, ...]
    active_display_changed = Signal(object)  # DisplayInfo
    tutorial_completed = Signal(str, str)  # task_id, task_title
    accessibility_changed = Signal(bool, float, bool)  # high_contrast, text_scale, sound_cues

    def __init__(
        self,
        config: AppConfig | None = None,
        process_manager: GodotProcessManager | None = None,
        server: WebSocketServer | None = None,
        voice_controller: VoiceInteractionController | None = None,
        tutorial_engine: TutorialEngine | None = None,
        *,
        headless: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config if config is not None else AppConfig.load()
        self._logger = logging.getLogger("octa.desktop")
        self._headless = headless

        # Dependency injection for testability
        if process_manager is not None:
            self._process_manager = process_manager
            if process_manager.parent() is None:
                process_manager.setParent(self)
        else:
            self._process_manager = GodotProcessManager(
                executable_path=self._config.godot_executable,
                project_dir=self._config.godot_project_dir,
                parent=self,
            )

        if server is not None:
            self._server = server
            if server.parent() is None:
                server.setParent(self)
        else:
            self._server = WebSocketServer(parent=self)

        if voice_controller is not None:
            self._voice_controller = voice_controller
            if voice_controller.parent() is None:
                voice_controller.setParent(self)
        else:
            self._voice_controller = VoiceInteractionController.from_app_config(
                self._config,
                parent=self,
            )

        if tutorial_engine is not None:
            self._tutorial_engine = tutorial_engine
            if tutorial_engine.parent() is None:
                tutorial_engine.setParent(self)
        else:
            self._tutorial_engine = TutorialEngine.from_app_config(
                self._config,
                voice_controller=self._voice_controller,
                parent=self,
            )

        self._stopping = False
        self._restarting = False
        self._connect_signals()

    def _connect_signals(self) -> None:
        """Connect internal signals from server and process manager."""
        # Process manager signals
        self._process_manager.state_changed.connect(self._on_process_state_changed)
        self._process_manager.started.connect(self._on_process_started)
        self._process_manager.finished.connect(self._on_process_finished)
        self._process_manager.error_occurred.connect(self._on_process_error)
        self._process_manager.stdout_received.connect(self._on_process_stdout)
        self._process_manager.stderr_received.connect(self._on_process_stderr)

        # Server signals
        self._server.stateChanged.connect(self._on_server_state_changed)
        self._server.listening.connect(self._on_server_listening)
        self._server.clientAuthenticated.connect(self._on_client_authenticated)
        self._server.clientDisconnected.connect(self._on_client_disconnected)
        self._server.robotStateChanged.connect(self._on_robot_state_changed)
        self._server.errorOccurred.connect(self._on_server_error)

        # Voice controller signals
        self._voice_controller.voice_state_changed.connect(self._on_voice_state_changed)
        self._voice_controller.microphone_active_changed.connect(
            self.microphone_active_changed.emit
        )
        self._voice_controller.speaking_active_changed.connect(self.speaking_active_changed.emit)
        self._voice_controller.instruction_ready.connect(self.instruction_updated.emit)
        self._voice_controller.voice_activity_event.connect(self._log)
        self._voice_controller.voice_error.connect(self.error_occurred.emit)

        # Tutorial engine signals
        self._tutorial_engine.session_state_changed.connect(self.tutorial_state_changed.emit)
        self._tutorial_engine.step_changed.connect(self._on_tutorial_step_changed)
        self._tutorial_engine.verification_completed.connect(self.verification_completed.emit)
        self._tutorial_engine.companion_state_requested.connect(self._on_companion_state_requested)
        self._tutorial_engine.activity_logged.connect(self._log)
        self._tutorial_engine.error_occurred.connect(self.error_occurred.emit)
        self._tutorial_engine.feedback_recorded.connect(self.feedback_recorded.emit)
        self._tutorial_engine.tutorials_catalog_reloaded.connect(
            self.tutorials_catalog_reloaded.emit
        )
        self._tutorial_engine.display_manager.displays_changed.connect(self.displays_changed.emit)
        self._tutorial_engine.display_manager.active_display_changed.connect(
            self.active_display_changed.emit
        )
        self._tutorial_engine.tutorial_completed.connect(self.tutorial_completed.emit)

    # ──────────────────────────────────────────
    # Properties
    # ──────────────────────────────────────────

    @property
    def config(self) -> AppConfig:
        """Application configuration."""
        return self._config

    @property
    def process_manager(self) -> GodotProcessManager:
        """Godot process lifecycle manager."""
        return self._process_manager

    @property
    def server(self) -> WebSocketServer:
        """Loopback WebSocket server."""
        return self._server

    @property
    def process_state(self) -> ProcessState:
        """Current Godot process state."""
        return self._process_manager.state

    @property
    def server_state(self) -> ConnectionState:
        """Current WebSocket server state."""
        return self._server.state

    @property
    def robot_state(self) -> str:
        """Current companion robot state reported by client."""
        return self._server.robot_state

    @property
    def server_url(self) -> str:
        """Loopback WebSocket URL, or empty if stopped."""
        return self._server.url

    @property
    def server_port(self) -> int:
        """Listening server port, or 0 if stopped."""
        return self._server.port

    @property
    def is_client_authenticated(self) -> bool:
        """Whether a companion client is connected and authenticated."""
        return self._server.is_authenticated

    @property
    def voice_controller(self) -> VoiceInteractionController:
        """Push-to-talk voice interaction controller."""
        return self._voice_controller

    @property
    def voice_state(self) -> str:
        """Current push-to-talk voice state string."""
        return self._voice_controller.state.value

    @property
    def is_microphone_active(self) -> bool:
        """Whether the push-to-talk microphone is actively recording."""
        return self._voice_controller.is_microphone_active

    @property
    def is_speaking(self) -> bool:
        """Whether instruction audio is actively playing."""
        return self._voice_controller.is_speaking

    @property
    def tutorial_engine(self) -> TutorialEngine:
        """End-to-end tutorial engine coordinating tasks, capture, consent, AI, and overlay."""
        return self._tutorial_engine

    @property
    def tutorial_state(self) -> str:
        """Current tutorial session state string."""
        return self._tutorial_engine.state.value

    @property
    def tutorial_session_metrics(self) -> TutorialSessionMetrics:
        """Active tutorial session metrics."""
        return self._tutorial_engine.get_session_metrics()

    @property
    def is_active(self) -> bool:
        """Whether the companion process or server is active."""
        return (
            self._process_manager.is_running
            or self._is_process_active()
            or self._server.state
            not in (
                ConnectionState.STOPPED,
                ConnectionState.ERROR,
            )
        )

    # ──────────────────────────────────────────
    # Public Control API
    # ──────────────────────────────────────────

    @Slot()
    def start(self) -> None:
        """Start the WebSocket server, then launch Godot with safe credential handoff."""
        if self._process_manager.is_running or self._process_manager.state == ProcessState.STARTING:
            self._log("Process is already running or starting.", "WARNING")
            return

        # Wait if a prior process or WebSocket session is still finishing its close
        if self._is_process_active():
            self._wait_until(
                condition=lambda: not self._is_process_active(),
                timeout_ms=DEFAULT_STOP_TIMEOUT_MS,
                signals=[
                    self._process_manager.finished,
                    self._process_manager.state_changed,
                ],
            )

        if self._server.state == ConnectionState.CLOSING or (
            self._server.state == ConnectionState.STOPPED
            and getattr(self._server, "_session", None) is not None
        ):
            self._wait_until(
                condition=self._is_server_stopped,
                timeout_ms=DEFAULT_SERVER_CLOSE_TIMEOUT_MS,
                signals=[
                    self._server.stateChanged,
                    self._server.clientDisconnected,
                ],
            )

        self._stopping = False
        self._log("Starting companion services...", "INFO")

        # 1. Start WebSocket server first to obtain port and rotated auth token
        try:
            if self._server.state == ConnectionState.STOPPED:
                self._server.start()
        except Exception as exc:
            msg = f"Failed to start WebSocket server: {exc}"
            self._log(msg, "ERROR")
            self.error_occurred.emit(msg)
            self._emit_status()
            return

        # 2. Prepare safe process environment (never puts token in CLI args)
        try:
            env = prepare_process_environment(
                port=self._server.port,
                auth_token=self._server.auth_token,
                host=self._server.host,
            )
        except Exception as exc:
            msg = f"Failed to prepare credential handoff: {exc}"
            self._log(msg, "ERROR")
            self.error_occurred.emit(msg)
            self._stop_server(timeout_ms=DEFAULT_SERVER_CLOSE_TIMEOUT_MS)
            self._emit_status()
            return

        # 3. Asynchronously launch Godot runtime with QProcessEnvironment
        try:
            target_exe = self._config.godot_executable
            target_proj = self._config.godot_project_dir
            self._process_manager.start(
                executable_path=target_exe,
                project_dir=target_proj,
                headless=self._headless,
                env=env,
            )
        except ProcessError as exc:
            msg = f"Failed to start Godot character runtime: {exc}"
            self._log(msg, "ERROR")
            self.error_occurred.emit(msg)
            self._emit_status()
        except Exception as exc:
            msg = f"Unexpected error launching runtime: {exc}"
            self._log(msg, "ERROR")
            self.error_occurred.emit(msg)
            self._emit_status()

    @Slot()
    def stop(self, timeout_ms: int = DEFAULT_STOP_TIMEOUT_MS) -> None:
        """Gracefully stop services, waiting for Godot to exit before stopping the server."""
        self._restarting = False
        if self._stopping:
            return
        self._execute_stop(timeout_ms=timeout_ms)

    @Slot()
    def restart(self, timeout_ms: int = DEFAULT_STOP_TIMEOUT_MS) -> None:
        """Restart companion services after the old process and WebSocket session finish closing."""
        if self._restarting:
            return
        self._log("Restarting companion services...", "INFO")
        self._restarting = True
        if self._stopping:
            return
        self._execute_stop(timeout_ms=timeout_ms)
        if self._restarting:
            self._restarting = False
            self.start()

    @Slot()
    def start_push_to_talk(self, *, user_initiated: bool = True) -> None:
        """Start push-to-talk microphone capture from an explicit user action."""
        self._voice_controller.start_push_to_talk(user_initiated=user_initiated)

    @Slot()
    def stop_push_to_talk(
        self,
        *,
        auto_speak_response: bool = False,
    ) -> GuidanceInstruction | None:
        """Stop push-to-talk capture, transcribe, and route spoken request to task pipeline."""
        return self._voice_controller.stop_push_to_talk_and_process(
            auto_speak_response=auto_speak_response,
        )

    @Slot()
    def read_current_instruction_aloud(self, *, user_initiated: bool = True) -> None:
        """Read the current task step instruction aloud via TTS."""
        self._voice_controller.speak_current_instruction(user_initiated=user_initiated)

    @Slot()
    def cancel_voice(self) -> None:
        """Immediately cancel/stop any active push-to-talk recording, STT, or TTS playback."""
        self._voice_controller.cancel_or_stop()

    @Slot(str)
    def select_tutorial(self, task_id: str) -> GuidanceInstruction:
        """Select a sample tutorial by task_id and reset progress to Step 1."""
        return self._tutorial_engine.select_tutorial(task_id)

    @Slot()
    def start_tutorial(self) -> GuidanceInstruction:
        """Start or restart the active tutorial from Step 1."""
        return self._tutorial_engine.start_tutorial()

    @Slot()
    def reset_tutorial(self) -> GuidanceInstruction:
        """Reset the active tutorial to Step 1."""
        return self._tutorial_engine.reset_tutorial()

    @Slot()
    def next_tutorial_step(self) -> GuidanceInstruction:
        """Advance to the next step in the active tutorial (text-based flow)."""
        return self._tutorial_engine.next_step()

    @Slot()
    def previous_tutorial_step(self) -> GuidanceInstruction:
        """Return to the previous step in the active tutorial (text-based flow)."""
        return self._tutorial_engine.previous_step()

    @Slot()
    def show_tutorial_hint(self) -> GuidanceInstruction:
        """Display the hint for the current tutorial step."""
        return self._tutorial_engine.request_hint()

    @Slot()
    def complete_step_manually(self, *, user_initiated: bool = True) -> GuidanceInstruction:
        """Mark the current step complete via the text-based flow and advance."""
        return self._tutorial_engine.complete_current_step_manually(user_initiated=user_initiated)

    @Slot()
    def verify_current_step_with_ai(self, *, user_initiated: bool = True) -> int:
        """Capture screen, request single-use consent, and verify current step with AI."""
        return self._tutorial_engine.verify_current_step(user_initiated=user_initiated)

    @Slot()
    def cancel_tutorial_operation(self) -> bool:
        """Cancel any in-flight background tutorial verification or guidance operation."""
        return self._tutorial_engine.cancel_active_operation()

    @Slot(bool)
    def set_overlay_enabled(self, enabled: bool) -> None:
        """Enable or disable the passive visual guidance overlay."""
        self._tutorial_engine.set_overlay_enabled(enabled)

    @Slot(int, str, str, str)
    def record_tutorial_feedback(
        self,
        rating: int,
        difficulty: str = "just_right",
        comment: str = "",
        step_id: str | None = None,
    ) -> TutorialFeedback:
        """Record structured user feedback for the current tutorial step."""
        return self._tutorial_engine.record_feedback(
            rating=rating,
            difficulty=difficulty,
            comment=comment,
            step_id=step_id,
        )

    def reload_tutorial_plugins(self, plugins_dir: Any = None) -> Any:
        """Reload external tutorial plugins from disk and refresh catalog."""
        return self._tutorial_engine.reload_plugins(plugins_dir)

    @property
    def display_manager(self) -> DisplayManager:
        """DisplayManager managing monitor geometry and selection."""
        return self._tutorial_engine.display_manager

    @property
    def active_display(self) -> DisplayInfo:
        """Currently active display monitor."""
        return self._tutorial_engine.active_display

    def select_display(self, index_or_name: int | str) -> bool:
        """Select active display monitor by index or name."""
        return self._tutorial_engine.select_display(index_or_name)

    # ──────────────────────────────────────────
    # Accessibility Adaptations (Phase 8.3)
    # ──────────────────────────────────────────

    @property
    def accessibility_high_contrast(self) -> bool:
        """Whether high-contrast overlay cues are enabled."""
        return self._tutorial_engine.overlay_high_contrast

    @property
    def accessibility_text_scale(self) -> float:
        """Current UI and overlay text scale factor."""
        return self._tutorial_engine.overlay_text_scale

    @property
    def accessibility_sound_cues(self) -> bool:
        """Whether tutorial completion audio cues are enabled."""
        return self._tutorial_engine.sound_cues_enabled

    def set_high_contrast(self, enabled: bool) -> None:
        """Toggle high-contrast cue rendering on the overlay and notify listeners."""
        self._tutorial_engine.set_overlay_high_contrast(enabled)
        self._log(
            f"Accessibility: high-contrast cues {'enabled' if enabled else 'disabled'}.",
            "INFO",
        )
        self.accessibility_changed.emit(
            self.accessibility_high_contrast,
            self.accessibility_text_scale,
            self.accessibility_sound_cues,
        )

    def set_text_scale(self, scale: float) -> None:
        """Update text scaling factor and notify listeners."""
        self._tutorial_engine.set_overlay_text_scale(scale)
        self._log(f"Accessibility: text scale set to {self.accessibility_text_scale:.2f}x.", "INFO")
        self.accessibility_changed.emit(
            self.accessibility_high_contrast,
            self.accessibility_text_scale,
            self.accessibility_sound_cues,
        )

    def set_sound_cues_enabled(self, enabled: bool) -> None:
        """Toggle tutorial completion audio chime cues and notify listeners."""
        self._tutorial_engine.set_sound_cues_enabled(enabled)
        self._log(
            f"Accessibility: completion audio cues {'enabled' if enabled else 'disabled'}.",
            "INFO",
        )
        self.accessibility_changed.emit(
            self.accessibility_high_contrast,
            self.accessibility_text_scale,
            self.accessibility_sound_cues,
        )

    def set_accessibility_preferences(
        self,
        *,
        high_contrast: bool | None = None,
        text_scale: float | None = None,
        sound_cues: bool | None = None,
    ) -> None:
        """Atomically update accessibility preferences and emit accessibility_changed."""
        if high_contrast is not None:
            self._tutorial_engine.set_overlay_high_contrast(high_contrast)
        if text_scale is not None:
            self._tutorial_engine.set_overlay_text_scale(text_scale)
        if sound_cues is not None:
            self._tutorial_engine.set_sound_cues_enabled(sound_cues)
        self.accessibility_changed.emit(
            self.accessibility_high_contrast,
            self.accessibility_text_scale,
            self.accessibility_sound_cues,
        )

    def play_completion_sound_cue(self) -> bool:
        """Explicitly test or trigger the tutorial completion sound cue."""
        return self._tutorial_engine.sound_player.play_completion_cue()

    # ──────────────────────────────────────────
    # Internal Lifecycle Sequencing & Helpers
    # ──────────────────────────────────────────

    def _is_process_active(self) -> bool:
        """Return True when the Godot process has not yet reached a terminal state."""
        if self._process_manager.state in (
            ProcessState.STARTING,
            ProcessState.RUNNING,
            ProcessState.STOPPING,
        ):
            return True
        proc = getattr(self._process_manager, "_process", None)
        if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
            return True
        return False

    def _is_server_stopped(self) -> bool:
        """Return True when the WebSocket server is stopped and no session is still closing."""
        return (
            self._server.state == ConnectionState.STOPPED
            and getattr(self._server, "_session", None) is None
        )

    @staticmethod
    def _safe_disconnect(signal: Any, slot: Callable[..., Any]) -> None:
        """Safely disconnect a temporary signal handler."""
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass

    def _wait_until(
        self,
        condition: Callable[[], bool],
        timeout_ms: int,
        signals: Sequence[Any],
    ) -> bool:
        """Run a bounded event loop until condition() is True or timeout_ms elapses.

        Always disconnects temporary signal handlers in a finally block so
        repeated stops never accumulate callbacks.
        """
        if condition():
            return True

        bounded_ms = max(1, int(timeout_ms))
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(bounded_ms)
        timed_out = False

        def _on_signal(*_args: object) -> None:
            if condition() and loop.isRunning():
                loop.quit()

        def _on_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            if loop.isRunning():
                loop.quit()

        timer.timeout.connect(_on_timeout)
        for sig in signals:
            sig.connect(_on_signal)

        try:
            if not condition():
                timer.start()
                rc = loop.exec()
                if rc == -1 and not condition() and not timed_out:
                    deadline = time.monotonic() + (bounded_ms / 1000.0)
                    while not condition() and not timed_out and time.monotonic() < deadline:
                        QCoreApplication.processEvents(
                            QEventLoop.ProcessEventsFlag.AllEvents,
                            10,
                        )
            return condition()
        finally:
            timer.stop()
            self._safe_disconnect(timer.timeout, _on_timeout)
            for sig in signals:
                self._safe_disconnect(sig, _on_signal)

    def _stop_server(self, timeout_ms: int) -> None:
        """Stop the WebSocket server and wait for any active session to finish closing."""
        if self._server.state != ConnectionState.STOPPED:
            self._server.stop()
        if not self._is_server_stopped():
            server_wait_ms = max(50, min(int(timeout_ms), DEFAULT_SERVER_CLOSE_TIMEOUT_MS))
            self._wait_until(
                condition=self._is_server_stopped,
                timeout_ms=server_wait_ms,
                signals=[
                    self._server.stateChanged,
                    self._server.clientDisconnected,
                ],
            )
            if not self._is_server_stopped():
                session = getattr(self._server, "_session", None)
                if session is not None:
                    socket = getattr(session, "_socket", None)
                    if socket is not None and hasattr(socket, "abort"):
                        socket.abort()
                if not self._is_server_stopped() and hasattr(self._server, "_on_session_closed"):
                    self._server._on_session_closed()

    def _execute_stop(self, timeout_ms: int = DEFAULT_STOP_TIMEOUT_MS) -> None:
        """Execute bounded graceful shutdown, process wait/force-kill fallback, and server stop."""
        self._stopping = True
        self._log("Stopping companion services...", "INFO")
        try:
            # Immediately cancel/stop any active push-to-talk recording, speech, or tutorial worker
            self._voice_controller.cancel_or_stop()
            self._tutorial_engine.shutdown(timeout_ms=min(1000, max(100, int(timeout_ms))))
            total_timeout_ms = max(1, int(timeout_ms))
            started_at = time.monotonic()

            # Step 1: Bounded graceful SHUTDOWN_ACK handshake while client is authenticated.
            # Keep the WebSocket server available during handshake and process termination.
            has_running_process = self._process_manager.is_running or self._is_process_active()
            if self._server.is_authenticated and has_running_process:
                self._log("Requesting graceful client shutdown...", "DEBUG")
                handshake_timeout_ms = min(
                    DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_MS,
                    max(1, total_timeout_ms // 2),
                )
                ack_received = False

                def _on_ack(_payload: object = None) -> None:
                    nonlocal ack_received
                    ack_received = True

                self._server.shutdownAcknowledged.connect(_on_ack)
                try:
                    self._server.request_shutdown("User requested stop")
                    self._wait_until(
                        condition=lambda: (
                            ack_received
                            or not self._server.is_authenticated
                            or not (self._process_manager.is_running or self._is_process_active())
                        ),
                        timeout_ms=handshake_timeout_ms,
                        signals=[
                            self._server.shutdownAcknowledged,
                            self._server.clientDisconnected,
                            self._process_manager.finished,
                            self._process_manager.state_changed,
                        ],
                    )
                except Exception as exc:
                    self._logger.debug("SHUTDOWN request failed: %s", exc)
                finally:
                    self._safe_disconnect(self._server.shutdownAcknowledged, _on_ack)

            # Step 2: Stop Godot process and wait for completion (allowing process manager's
            # force-kill fallback timer to run while Qt's event loop is active).
            # The WebSocket server remains available until process exit or fallback completion.
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            remaining_ms = max(50, total_timeout_ms - elapsed_ms)

            if self._process_manager.is_running or self._process_manager.state in (
                ProcessState.STARTING,
                ProcessState.RUNNING,
            ):
                self._process_manager.stop(timeout_ms=remaining_ms)

            if self._is_process_active():
                shutdown_timer = getattr(self._process_manager, "_shutdown_timer", None)
                timer_interval_ms = remaining_ms
                if shutdown_timer is not None and shutdown_timer.isActive():
                    timer_interval_ms = max(remaining_ms, int(shutdown_timer.interval()))
                firm_wait_ms = timer_interval_ms + max(50, min(500, timer_interval_ms))

                finished_in_time = self._wait_until(
                    condition=lambda: not self._is_process_active(),
                    timeout_ms=firm_wait_ms,
                    signals=[
                        self._process_manager.finished,
                        self._process_manager.state_changed,
                    ],
                )
                if not finished_in_time and self._is_process_active():
                    self._logger.warning(
                        "Process still active after firm shutdown wait (%d ms); running cleanup.",
                        firm_wait_ms,
                    )
                    self._process_manager.cleanup()
                    if self._is_process_active():
                        self._wait_until(
                            condition=lambda: not self._is_process_active(),
                            timeout_ms=max(50, min(500, remaining_ms)),
                            signals=[
                                self._process_manager.finished,
                                self._process_manager.state_changed,
                            ],
                        )

            # Step 3: Stop WebSocket server and wait for session closure only after
            # the client/process has finished or the bounded fallback has completed.
            if not self._is_server_stopped():
                self._stop_server(timeout_ms=total_timeout_ms)
        finally:
            self._stopping = False
            self._log("Companion services stopped.", "INFO")
            self._emit_status()

        if self._restarting:
            self._restarting = False
            self.start()

    # ──────────────────────────────────────────
    # Internal Slots and Signal Handlers
    # ──────────────────────────────────────────

    def _log(self, message: str, level: str = "INFO") -> None:
        """Emit a timestamped log message without exposing tokens."""
        now = datetime.now().strftime("%H:%M:%S")
        self._logger.log(getattr(logging, level, logging.INFO), message)
        self.log_message.emit(now, message, level)

    def _emit_status(self) -> None:
        """Emit current aggregate status for UI consumers."""
        proc_str = self._process_manager.state.value
        srv_str = self._server.state.value
        rbt_str = self._server.robot_state
        self.status_changed.emit(proc_str, srv_str, rbt_str)

    def _on_process_state_changed(self, state: ProcessState) -> None:
        self._emit_status()

    def _on_process_started(self, pid: int) -> None:
        self._log(f"Godot runtime active (PID: {pid}).", "INFO")
        self._emit_status()

    def _on_process_finished(self, exit_code: int, exit_status: str) -> None:
        self._log(f"Godot runtime finished (code: {exit_code}, status: {exit_status}).", "INFO")
        self._emit_status()

    def _on_process_error(self, message: str) -> None:
        self._log(f"Process error: {message}", "ERROR")
        self.error_occurred.emit(message)
        self._emit_status()

    def _on_process_stdout(self, line: str) -> None:
        clean = line.strip()
        if clean:
            self._log(f"[Godot] {clean}", "DEBUG")

    def _on_process_stderr(self, line: str) -> None:
        clean = line.strip()
        if clean:
            self._log(f"[Godot Err] {clean}", "WARNING")

    def _on_server_state_changed(self, state: ConnectionState) -> None:
        self._emit_status()
        if state == ConnectionState.STOPPED:
            self.server_stopped.emit()

    def _on_server_listening(self, host: str, port: int) -> None:
        url = f"ws://{host}:{port}"
        self._log(f"Server listening on {url}.", "INFO")
        self.server_started.emit(host, port, url)
        self._emit_status()

    def _on_client_authenticated(self, name: str, version: str) -> None:
        self._log(f"Client authenticated: {name} v{version}.", "INFO")
        self.client_authenticated.emit(name, version)
        self._emit_status()

    def _on_client_disconnected(self) -> None:
        self._log("Client disconnected.", "INFO")
        self.client_disconnected.emit()
        self._emit_status()

    def _on_robot_state_changed(self, previous: str, new_state: str) -> None:
        self._log(f"Robot state: {previous} -> {new_state}", "INFO")
        self.robot_state_changed.emit(previous, new_state)
        self._emit_status()

    def _on_server_error(self, message: str) -> None:
        self._log(f"Server error: {message}", "ERROR")
        self.error_occurred.emit(message)
        self._emit_status()

    def _on_voice_state_changed(self, new_voice_state: str) -> None:
        self.voice_state_changed.emit(new_voice_state)
        if self._server.is_authenticated:
            target_robot_state: str | None = None
            if new_voice_state == "RECORDING":
                target_robot_state = "LISTENING"
            elif new_voice_state in {"TRANSCRIBING", "PROCESSING"}:
                target_robot_state = "THINKING"
            elif new_voice_state == "IDLE":
                target_robot_state = "IDLE"
            if target_robot_state is not None:
                try:
                    self._server.set_state(target_robot_state)
                except Exception:
                    pass

    def _on_tutorial_step_changed(
        self,
        step_num: int,
        total_steps: int,
        step_id: str,
        instruction_text: str,
    ) -> None:
        self.tutorial_step_changed.emit(step_num, total_steps, step_id, instruction_text)
        self.instruction_updated.emit(step_id, instruction_text)

    def _on_companion_state_requested(self, target_robot_state: str) -> None:
        if self._server.is_authenticated and target_robot_state:
            try:
                self._server.set_state(target_robot_state)
            except Exception:
                pass
