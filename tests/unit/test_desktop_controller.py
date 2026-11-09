"""Unit tests for DesktopController lifecycle orchestration and signal routing."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QObject, QProcess, QTimer
from pytestqt.qtbot import QtBot

from octa.communication.messages import ShutdownAckPayload
from octa.communication.server import WebSocketServer
from octa.communication.state import ConnectionState
from octa.core.config import AppConfig
from octa.desktop.controller import DesktopController
from octa.process.manager import GodotProcessManager
from octa.process.state import ProcessState


class ControllableMockProcess(QProcess):
    """Test double for QProcess supporting delayed exit and force-kill fallback."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.mock_pid: int = 4321
        self.mock_state: QProcess.ProcessState = QProcess.ProcessState.NotRunning
        self.terminate_called: bool = False
        self.kill_called: bool = False
        self.auto_exit_on_terminate: bool = False
        self.terminate_exit_delay_ms: int = 0
        self.auto_exit_on_kill: bool = True
        self.events: list[str] = []
        self.finished.connect(self._on_finished_internal)

    def _on_finished_internal(self, *_args: object) -> None:
        self.mock_state = QProcess.ProcessState.NotRunning

    def state(self) -> QProcess.ProcessState:
        return self.mock_state

    def processId(self) -> int:
        return self.mock_pid

    def simulate_running(self, manager: GodotProcessManager, pid: int = 4321) -> None:
        self.mock_pid = pid
        self.mock_state = QProcess.ProcessState.Running
        manager._generation += 1
        manager._pid = pid
        manager._set_state(ProcessState.RUNNING)

    def terminate(self) -> None:
        self.terminate_called = True
        self.events.append("process_terminate")
        if self.auto_exit_on_terminate:
            if self.terminate_exit_delay_ms > 0:

                def _delayed_finish() -> None:
                    if self.mock_state != QProcess.ProcessState.NotRunning:
                        self.mock_state = QProcess.ProcessState.NotRunning
                        self.events.append("process_finished")
                        self.finished.emit(0, QProcess.ExitStatus.NormalExit)

                QTimer.singleShot(self.terminate_exit_delay_ms, _delayed_finish)
            else:
                self.mock_state = QProcess.ProcessState.NotRunning
                self.events.append("process_finished")
                self.finished.emit(0, QProcess.ExitStatus.NormalExit)

    def kill(self) -> None:
        self.kill_called = True
        self.events.append("process_kill")
        if self.auto_exit_on_kill:
            self.mock_state = QProcess.ProcessState.NotRunning
            self.events.append("process_finished")
            self.finished.emit(1, QProcess.ExitStatus.CrashExit)


def test_controller_initial_state(qtbot: QtBot) -> None:
    """Controller initializes with stopped states and default properties."""
    del qtbot
    config = AppConfig(godot_auto_start=False, start_minimized=False)
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)

    controller = DesktopController(
        config=config,
        process_manager=process_mgr,
        server=server,
    )

    assert controller.process_state == ProcessState.STOPPED
    assert controller.server_state == ConnectionState.STOPPED
    assert controller.robot_state == "IDLE"
    assert controller.server_url == ""
    assert controller.server_port == 0
    assert controller.is_client_authenticated is False
    assert controller.is_active is False


def test_controller_start_starts_server_before_godot(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """Controller starts WebSocket server first, prepares environment, and launches Godot."""
    del qtbot
    config = AppConfig(godot_auto_start=False)
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)

    call_order: list[str] = []
    orig_server_start = server.start

    def spy_server_start() -> int:
        call_order.append("server")
        return orig_server_start()

    def spy_proc_start(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        call_order.append("process")
        process_mgr._set_state(ProcessState.RUNNING)
        process_mgr._pid = 12345
        process_mgr.started.emit(12345)

    monkeypatch.setattr(server, "start", spy_server_start)
    monkeypatch.setattr(process_mgr, "start", spy_proc_start)

    controller = DesktopController(
        config=config,
        process_manager=process_mgr,
        server=server,
    )

    status_events: list[tuple[str, str, str]] = []
    controller.status_changed.connect(lambda p, s, r: status_events.append((p, s, r)))

    log_messages: list[str] = []
    controller.log_message.connect(lambda _ts, msg, _lvl: log_messages.append(msg))

    controller.start()

    # Verify server started before Godot
    assert call_order == ["server", "process"]
    assert server.state == ConnectionState.LISTENING
    assert server.port > 0
    assert controller.server_url.startswith("ws://127.0.0.1:")
    assert controller.process_state == ProcessState.RUNNING

    # Verify token is NEVER logged in any emitted message
    token = server.auth_token
    assert token is not None and len(token) > 0
    for log_entry in log_messages:
        assert token not in log_entry, f"Auth token leaked in log message: {log_entry}"

    # Clean up
    server.stop()


def test_controller_stop_graceful_shutdown_handshake(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """Controller requests graceful shutdown when authenticated before stopping services."""
    del qtbot
    config = AppConfig()
    mock_process = ControllableMockProcess()
    mock_process.auto_exit_on_terminate = True
    process_mgr = GodotProcessManager(process=mock_process)
    mock_process.simulate_running(process_mgr)

    server = WebSocketServer(port=0)
    server.start()

    controller = DesktopController(
        config=config,
        process_manager=process_mgr,
        server=server,
    )

    # Mock server state as authenticated
    server._state = ConnectionState.AUTHENTICATED
    monkeypatch.setattr(type(server), "is_authenticated", property(lambda _self: True))

    events: list[str] = mock_process.events

    def fake_request_shutdown(reason: str) -> str:
        events.append(f"request_shutdown:{reason}")
        QTimer.singleShot(
            10,
            lambda: server.shutdownAcknowledged.emit(ShutdownAckPayload(acknowledged=True)),
        )
        return "msg-1"

    orig_server_stop = server.stop

    def spy_server_stop() -> None:
        events.append("server_stop")
        orig_server_stop()

    monkeypatch.setattr(server, "request_shutdown", fake_request_shutdown)
    monkeypatch.setattr(server, "stop", spy_server_stop)

    controller.stop(timeout_ms=500)

    assert events == [
        "request_shutdown:User requested stop",
        "process_terminate",
        "process_finished",
        "server_stop",
    ]
    assert controller.process_state == ProcessState.STOPPED
    assert controller.server_state == ConnectionState.STOPPED


def test_controller_stop_waits_for_delayed_process_completion_before_server_stop(
    qtbot: QtBot,
    monkeypatch,  # noqa: ANN001
) -> None:
    """stop() waits for delayed Godot exit before stopping the WebSocket server."""
    del qtbot
    mock_process = ControllableMockProcess()
    mock_process.auto_exit_on_terminate = True
    mock_process.terminate_exit_delay_ms = 60
    process_mgr = GodotProcessManager(process=mock_process)
    mock_process.simulate_running(process_mgr)

    server = WebSocketServer(port=0)
    server.start()

    controller = DesktopController(
        process_manager=process_mgr,
        server=server,
    )

    orig_server_stop = server.stop
    server_was_listening_when_proc_finished = False

    def on_proc_finished(*_args: object) -> None:
        nonlocal server_was_listening_when_proc_finished
        server_was_listening_when_proc_finished = server.state == ConnectionState.LISTENING

    process_mgr.finished.connect(on_proc_finished)

    def spy_server_stop() -> None:
        mock_process.events.append("server_stop")
        orig_server_stop()

    monkeypatch.setattr(server, "stop", spy_server_stop)

    controller.stop(timeout_ms=600)

    assert server_was_listening_when_proc_finished is True
    assert mock_process.events == [
        "process_terminate",
        "process_finished",
        "server_stop",
    ]
    assert controller.process_state == ProcessState.STOPPED
    assert controller.server_state == ConnectionState.STOPPED


def test_controller_stop_timeout_triggers_force_kill_while_server_available(
    qtbot: QtBot,
    monkeypatch,  # noqa: ANN001
) -> None:
    """When terminate() hangs, stop() runs force-kill fallback before stopping server."""
    del qtbot
    mock_process = ControllableMockProcess()
    mock_process.auto_exit_on_terminate = False
    mock_process.auto_exit_on_kill = True
    process_mgr = GodotProcessManager(process=mock_process)
    mock_process.simulate_running(process_mgr)

    server = WebSocketServer(port=0)
    server.start()

    controller = DesktopController(
        process_manager=process_mgr,
        server=server,
    )

    orig_server_stop = server.stop
    server_listening_at_kill = False

    orig_kill = mock_process.kill

    def spy_kill() -> None:
        nonlocal server_listening_at_kill
        server_listening_at_kill = server.state == ConnectionState.LISTENING
        orig_kill()

    def spy_server_stop() -> None:
        mock_process.events.append("server_stop")
        orig_server_stop()

    monkeypatch.setattr(mock_process, "kill", spy_kill)
    monkeypatch.setattr(server, "stop", spy_server_stop)

    controller.stop(timeout_ms=80)

    assert mock_process.terminate_called is True
    assert mock_process.kill_called is True
    assert server_listening_at_kill is True
    assert mock_process.events == [
        "process_terminate",
        "process_kill",
        "process_finished",
        "server_stop",
    ]
    assert controller.process_state == ProcessState.CRASHED
    assert controller.server_state == ConnectionState.STOPPED


def test_controller_restart_waits_for_old_process_and_session_to_close_before_relaunch(
    qtbot: QtBot,
    monkeypatch,  # noqa: ANN001
) -> None:
    """restart() waits for old process exit and WebSocket session close before relaunch."""
    del qtbot
    mock_process = ControllableMockProcess()
    mock_process.auto_exit_on_terminate = True
    mock_process.terminate_exit_delay_ms = 40
    process_mgr = GodotProcessManager(process=mock_process)
    mock_process.simulate_running(process_mgr, pid=1111)

    server = WebSocketServer(port=0)
    server.start()
    first_token = server.auth_token

    controller = DesktopController(
        process_manager=process_mgr,
        server=server,
    )

    events: list[str] = mock_process.events

    # Simulate an active session that closes asynchronously 40ms after server.stop()
    fake_session = SimpleNamespace(is_authenticated=False)
    server._session = fake_session  # type: ignore[assignment]
    server._state = ConnectionState.CONNECTED

    orig_server_stop = server.stop
    orig_server_start = server.start

    def delayed_server_stop() -> None:
        events.append("old_server_stop")
        server._server.close()
        server._set_state(ConnectionState.CLOSING)

        def _finish_session_close() -> None:
            server._session = None
            server._auth_token = None
            events.append("old_session_closed")
            server._set_state(ConnectionState.STOPPED)
            server.clientDisconnected.emit()

        QTimer.singleShot(40, _finish_session_close)

    def spy_server_start() -> int:
        # Must not be called while old session is still present or old process active
        assert server._session is None
        assert server.state == ConnectionState.STOPPED
        assert process_mgr.state == ProcessState.STOPPED
        events.append("new_server_start")
        return orig_server_start()

    def spy_proc_start(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        assert server.state == ConnectionState.LISTENING
        events.append("new_process_start")
        mock_process.simulate_running(process_mgr, pid=2222)
        process_mgr.started.emit(2222)

    monkeypatch.setattr(server, "stop", delayed_server_stop)
    monkeypatch.setattr(server, "start", spy_server_start)
    monkeypatch.setattr(process_mgr, "start", spy_proc_start)

    controller.restart(timeout_ms=600)

    assert events == [
        "process_terminate",
        "process_finished",
        "old_server_stop",
        "old_session_closed",
        "new_server_start",
        "new_process_start",
    ]
    assert controller.process_state == ProcessState.RUNNING
    assert controller.process_manager.pid == 2222
    assert controller.server_state == ConnectionState.LISTENING
    assert server.auth_token != first_token

    # Cleanup
    monkeypatch.setattr(server, "stop", orig_server_stop)
    server.stop()


def test_controller_repeated_stops_do_not_accumulate_signal_handlers(
    qtbot: QtBot,
    monkeypatch,  # noqa: ANN001
) -> None:
    """Temporary signal handlers connected during stop() are disconnected after waiting."""
    del qtbot
    mock_process = ControllableMockProcess()
    mock_process.auto_exit_on_terminate = True
    mock_process.terminate_exit_delay_ms = 10
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)

    controller = DesktopController(
        process_manager=process_mgr,
        server=server,
    )

    monkeypatch.setattr(
        server,
        "request_shutdown",
        lambda _reason: QTimer.singleShot(
            5,
            lambda: server.shutdownAcknowledged.emit(ShutdownAckPayload(acknowledged=True)),
        ),
    )

    for i in range(4):
        server.start()
        server._state = ConnectionState.AUTHENTICATED
        monkeypatch.setattr(type(server), "is_authenticated", property(lambda _self: True))
        mock_process.simulate_running(process_mgr, pid=1000 + i)
        controller.stop(timeout_ms=300)
        assert controller.process_state == ProcessState.STOPPED
        assert controller.server_state == ConnectionState.STOPPED

    # Emit signals after repeated stops to verify no stale loop/callback errors occur
    server.shutdownAcknowledged.emit(ShutdownAckPayload(acknowledged=True))
    server.clientDisconnected.emit()
    process_mgr.finished.emit(0, "NormalExit")


def test_controller_signal_routing(qtbot: QtBot) -> None:
    """Controller routes signals from server and process manager for all protocol states."""
    del qtbot
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)

    controller = DesktopController(
        process_manager=process_mgr,
        server=server,
    )

    logs: list[str] = []
    controller.log_message.connect(lambda _ts, msg, _lvl: logs.append(msg))

    robot_states: list[tuple[str, str]] = []
    controller.robot_state_changed.connect(lambda prev, new_st: robot_states.append((prev, new_st)))

    errors: list[str] = []
    controller.error_occurred.connect(errors.append)

    # Emit server events across all valid protocol v1 states: IDLE, LISTENING, THINKING, SUCCESS
    server.robotStateChanged.emit("IDLE", "LISTENING")
    server.robotStateChanged.emit("LISTENING", "THINKING")
    server.robotStateChanged.emit("THINKING", "SUCCESS")
    server.robotStateChanged.emit("SUCCESS", "IDLE")
    assert robot_states == [
        ("IDLE", "LISTENING"),
        ("LISTENING", "THINKING"),
        ("THINKING", "SUCCESS"),
        ("SUCCESS", "IDLE"),
    ]

    server.errorOccurred.emit("Simulated server error")
    assert "Simulated server error" in errors

    # Emit process events
    process_mgr.stdout_received.emit("Godot init line")
    assert any("Godot init line" in log for log in logs)

    process_mgr.error_occurred.emit("Simulated process error")
    assert "Simulated process error" in errors
