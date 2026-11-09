"""Unit tests for OctaSystemTray icon and context menu."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QProcess
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from pytestqt.qtbot import QtBot

from octa.communication.server import WebSocketServer
from octa.desktop.controller import DesktopController
from octa.desktop.tray import OctaSystemTray, create_tray_icon
from octa.desktop.window import DiagnosticWindow
from octa.process.manager import GodotProcessManager


def test_create_tray_icon(qtbot: QtBot) -> None:
    """Procedural icon generator creates a valid non-null QIcon."""
    del qtbot
    icon = create_tray_icon()
    assert isinstance(icon, QIcon)
    assert not icon.isNull()


def test_tray_initial_state(qtbot: QtBot) -> None:
    """Tray initializes with default actions, states, and tooltip."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)
    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    tray = OctaSystemTray(controller=controller, window=window)

    assert tray.action_start.isEnabled() is True
    assert tray.action_stop.isEnabled() is False
    assert tray.action_restart.isEnabled() is False
    assert tray.action_show.isEnabled() is True
    assert tray.action_exit.isEnabled() is True

    assert "Server: Stopped" in tray.toolTip()


def test_tray_actions_trigger_controller_and_window(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """Triggering tray actions invokes matching controller and window methods."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)
    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    tray = OctaSystemTray(controller=controller, window=window)

    start_mock = MagicMock()
    stop_mock = MagicMock()
    restart_mock = MagicMock()
    quit_mock = MagicMock()

    monkeypatch.setattr(controller, "start", start_mock)
    monkeypatch.setattr(controller, "stop", stop_mock)
    monkeypatch.setattr(controller, "restart", restart_mock)
    monkeypatch.setattr(QApplication, "quit", quit_mock)

    # Start
    tray.action_start.trigger()
    assert start_mock.called

    # Enable running actions
    tray.action_stop.setEnabled(True)
    tray.action_restart.setEnabled(True)

    # Stop
    tray.action_stop.trigger()
    assert stop_mock.called

    # Restart
    tray.action_restart.trigger()
    assert restart_mock.called

    # Show monitor
    window.hide()
    assert not window.isVisible()
    tray.action_show.trigger()
    assert window.isVisible()

    # Exit
    fake_app = MagicMock()
    monkeypatch.setattr(QApplication, "instance", lambda: fake_app)
    tray.action_exit.trigger()
    assert fake_app.quit.called


def test_tray_click_toggles_window_visibility(qtbot: QtBot) -> None:
    """Clicking or double-clicking the tray icon toggles the monitor window."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)
    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    tray = OctaSystemTray(controller=controller, window=window)

    # Initially hidden, activate -> shown
    window.hide()
    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert window.isVisible()

    # Shown, activate -> hidden
    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert not window.isVisible()


def test_tray_tooltip_never_contains_secret(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """Verify tray tooltip never renders bearer auth tokens."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)
    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    tray = OctaSystemTray(controller=controller, window=window)

    secret_token = "BEARER_SECRET_TOKEN_DO_NOT_LEAK"
    monkeypatch.setattr(type(server), "url", property(lambda _self: "ws://127.0.0.1:8765"))
    monkeypatch.setattr(type(server), "robot_state", property(lambda _self: "LISTENING"))

    controller.server_started.emit("127.0.0.1", 8765, "ws://127.0.0.1:8765")
    tooltip = tray.toolTip()

    assert "ws://127.0.0.1:8765" in tooltip
    assert "LISTENING" in tooltip
    assert secret_token not in tooltip
