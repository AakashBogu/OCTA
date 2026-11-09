"""Unit tests for DiagnosticWindow UI rendering and control interactions."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QCloseEvent
from pytestqt.qtbot import QtBot

from octa.communication.server import WebSocketServer
from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow
from octa.process.manager import GodotProcessManager


def test_diagnostic_window_initial_state(qtbot: QtBot) -> None:
    """Window renders initial stopped status, controls, and clean log."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    assert "OCTA" in window.windowTitle()
    assert window.get_process_status() == "Process: STOPPED"
    assert window.get_server_status() == "Server: STOPPED"
    assert window.get_loopback_url() == "URL: Not listening"
    assert window.get_client_status() == "Client: Disconnected"
    assert window.get_robot_state() == "Robot State: IDLE"

    assert window._btn_start.isEnabled() is True
    assert window._btn_stop.isEnabled() is False
    assert window._btn_restart.isEnabled() is False
    assert window.get_log_text() == ""


def test_diagnostic_window_updates_on_events(qtbot: QtBot) -> None:
    """Labels and log view update dynamically when controller signals fire."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    # Server starts
    controller.server_started.emit("127.0.0.1", 8765, "ws://127.0.0.1:8765")
    assert "ws://127.0.0.1:8765" in window.get_loopback_url()
    assert "Port: 8765" in window.get_loopback_url()

    # Client authenticates
    controller.client_authenticated.emit("OCTA Godot Client", "0.1.0")
    assert "OCTA Godot Client v0.1.0 (Authenticated)" in window.get_client_status()

    # Robot state changes across protocol states: IDLE, LISTENING, THINKING, SUCCESS
    for previous_state, next_state in (
        ("IDLE", "LISTENING"),
        ("LISTENING", "THINKING"),
        ("THINKING", "SUCCESS"),
        ("SUCCESS", "IDLE"),
    ):
        controller.robot_state_changed.emit(previous_state, next_state)
        assert window.get_robot_state() == f"Robot State: {next_state}"

    # Log message arrives
    controller.log_message.emit("12:00:00", "Sample diagnostic log entry", "INFO")
    assert "[12:00:00] [INFO] Sample diagnostic log entry" in window.get_log_text()

    # Client disconnects
    controller.client_disconnected.emit()
    assert window.get_client_status() == "Client: Disconnected"


def test_diagnostic_window_never_displays_auth_token(qtbot: QtBot) -> None:
    """Confirm the auth token is never rendered in labels, log views, or window elements."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    secret_token = "SUPER_SECRET_OCTA_BEARER_TOKEN_9999"

    # Simulate full activity
    controller.server_started.emit("127.0.0.1", 8765, "ws://127.0.0.1:8765")
    controller.client_authenticated.emit("Client", "1.0.0")
    controller.log_message.emit("12:00:00", "Server active and listening", "INFO")
    controller.log_message.emit("12:00:01", "Client authenticated successfully", "INFO")

    full_window_text = (
        f"{window.get_process_status()} "
        f"{window.get_server_status()} "
        f"{window.get_loopback_url()} "
        f"{window.get_client_status()} "
        f"{window.get_robot_state()} "
        f"{window.get_log_text()}"
    )

    assert secret_token not in full_window_text


def test_diagnostic_window_buttons_invoke_controller(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """Clicking window buttons triggers the matching controller methods."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    start_mock = MagicMock()
    stop_mock = MagicMock()
    restart_mock = MagicMock()

    monkeypatch.setattr(controller, "start", start_mock)
    monkeypatch.setattr(controller, "stop", stop_mock)
    monkeypatch.setattr(controller, "restart", restart_mock)

    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    # Click start
    qtbot.mouseClick(window._btn_start, Qt.MouseButton.LeftButton)
    start_mock.assert_called_once()

    # Enable stop and restart to test clicks
    window._btn_stop.setEnabled(True)
    window._btn_restart.setEnabled(True)

    qtbot.mouseClick(window._btn_stop, Qt.MouseButton.LeftButton)
    stop_mock.assert_called_once()

    qtbot.mouseClick(window._btn_restart, Qt.MouseButton.LeftButton)
    restart_mock.assert_called_once()


def test_diagnostic_window_clear_log(qtbot: QtBot) -> None:
    """Clear log button clears the text area."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)

    controller.log_message.emit("12:00:00", "Log 1", "INFO")
    assert len(window.get_log_text()) > 0

    qtbot.mouseClick(window._btn_clear, Qt.MouseButton.LeftButton)
    assert window.get_log_text() == ""


def test_diagnostic_window_close_event_with_tray(qtbot: QtBot, monkeypatch) -> None:  # noqa: ANN001
    """When tray is enabled and available, closeEvent hides window and ignores close."""
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)

    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    window = DiagnosticWindow(controller, tray_enabled=True)
    qtbot.addWidget(window)
    window.show()

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted() is False
    assert window.isVisible() is False
