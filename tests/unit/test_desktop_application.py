"""Unit tests for OctaApplication coordinator."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QObject, QProcess, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from pytestqt.qtbot import QtBot

from octa.communication.server import WebSocketServer
from octa.communication.state import ConnectionState
from octa.core.config import AppConfig
from octa.desktop.application import OctaApplication
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


def test_application_initialization_defaults(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """Application sets up window, tray, and honors auto-start."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    start_mock = MagicMock()
    monkeypatch.setattr(controller, "start", start_mock)

    config = AppConfig(godot_auto_start=True, start_minimized=False)
    app_coord = OctaApplication(config=config, controller=controller)
    qtbot.addWidget(app_coord.window)

    assert app_coord.controller is controller
    assert app_coord.window is not None
    assert start_mock.called


def test_application_no_auto_start(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """When godot_auto_start is False, controller.start is not called."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    start_mock = MagicMock()
    monkeypatch.setattr(controller, "start", start_mock)

    config = AppConfig(godot_auto_start=False, start_minimized=False)
    app_coord = OctaApplication(config=config, controller=controller)
    qtbot.addWidget(app_coord.window)

    assert not start_mock.called


def test_application_tray_unavailable_fallback(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """When system tray is unavailable, application falls back cleanly to windowed mode."""
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)

    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    config = AppConfig(godot_auto_start=False, start_minimized=True)
    app_coord = OctaApplication(config=config, controller=controller)
    qtbot.addWidget(app_coord.window)

    assert app_coord.tray is None
    assert app_coord.is_tray_available is False
    # Window must remain visible even if start_minimized was requested
    assert app_coord.window.isVisible()
    qapp = QApplication.instance()
    assert qapp.quitOnLastWindowClosed() is True


def test_application_about_to_quit_waits_for_delayed_process(
    qtbot: QtBot,
    monkeypatch,  # noqa: ANN001
) -> None:
    """aboutToQuit waits for delayed Godot exit before stopping the WebSocket server."""
    mock_process = ControllableMockProcess()
    mock_process.auto_exit_on_terminate = True
    mock_process.terminate_exit_delay_ms = 50
    process_mgr = GodotProcessManager(process=mock_process)
    mock_process.simulate_running(process_mgr, pid=5555)

    server = WebSocketServer(port=0)
    server.start()

    orig_server_stop = server.stop

    def spy_server_stop() -> None:
        mock_process.events.append("server_stop")
        orig_server_stop()

    monkeypatch.setattr(server, "stop", spy_server_stop)

    controller = DesktopController(process_manager=process_mgr, server=server)
    config = AppConfig(godot_auto_start=False, start_minimized=False)
    app_coord = OctaApplication(
        config=config,
        controller=controller,
        shutdown_timeout_ms=500,
    )
    qtbot.addWidget(app_coord.window)

    app_coord._on_about_to_quit()

    assert mock_process.events == [
        "process_terminate",
        "process_finished",
        "server_stop",
    ]
    assert controller.process_state == ProcessState.STOPPED
    assert controller.server_state == ConnectionState.STOPPED


def test_application_about_to_quit_runs_force_kill_fallback(
    qtbot: QtBot,
    monkeypatch,  # noqa: ANN001
) -> None:
    """aboutToQuit keeps event loop active so process manager's force-kill fallback completes."""
    mock_process = ControllableMockProcess()
    mock_process.auto_exit_on_terminate = False
    mock_process.auto_exit_on_kill = True
    process_mgr = GodotProcessManager(process=mock_process)
    mock_process.simulate_running(process_mgr, pid=6666)

    server = WebSocketServer(port=0)
    server.start()

    orig_server_stop = server.stop

    def spy_server_stop() -> None:
        mock_process.events.append("server_stop")
        orig_server_stop()

    monkeypatch.setattr(server, "stop", spy_server_stop)

    controller = DesktopController(process_manager=process_mgr, server=server)
    config = AppConfig(godot_auto_start=False, start_minimized=False)
    app_coord = OctaApplication(
        config=config,
        controller=controller,
        shutdown_timeout_ms=80,
    )
    qtbot.addWidget(app_coord.window)

    app_coord._on_about_to_quit()

    assert mock_process.terminate_called is True
    assert mock_process.kill_called is True
    assert mock_process.events == [
        "process_terminate",
        "process_kill",
        "process_finished",
        "server_stop",
    ]
    assert controller.process_state == ProcessState.CRASHED
    assert controller.server_state == ConnectionState.STOPPED
