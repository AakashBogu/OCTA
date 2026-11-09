"""End-to-end integration tests connecting Python WebSocketServer and real Godot process."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import psutil
import pytest
from PySide6.QtCore import QProcess
from pytestqt.qtbot import QtBot

from octa.communication.handoff import (
    ENV_HANDOFF_FILE,
    prepare_process_environment,
    write_handoff_file,
)
from octa.communication.messages import SetStateAckPayload, ShutdownAckPayload
from octa.communication.server import WebSocketServer
from octa.communication.state import ConnectionState
from octa.process.manager import GodotProcessManager
from octa.process.state import ProcessState

DEFAULT_GODOT_CONSOLE_EXE = Path(
    r"D:\Downloads\Godot_v4.7.2-stable_win64.exe\Godot_v4.7.2-stable_win64_console.exe"
)
REAL_GODOT_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "godot"


def get_real_godot_console_path() -> Path:
    """Return the configured or default real Godot console executable path."""
    env_path = os.environ.get("OCTA_GODOT_EXE")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
    return DEFAULT_GODOT_CONSOLE_EXE


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not get_real_godot_console_path().is_file(),
        reason=f"Real Godot console executable not found at {get_real_godot_console_path()}",
    ),
]


@pytest.fixture
def test_server(qtbot: QtBot) -> Generator[WebSocketServer, None, None]:
    """Provide a started loopback WebSocket server on an ephemeral port."""
    server = WebSocketServer(port=0)
    server.start()
    yield server
    if server.state != ConnectionState.STOPPED:
        server.stop()
        qtbot.waitUntil(lambda: server.state == ConnectionState.STOPPED, timeout=3000)


@pytest.fixture
def e2e_godot_manager(qtbot: QtBot) -> Generator[GodotProcessManager, None, None]:
    """Provide a GodotProcessManager for real binary integration with robust teardown."""
    exe_path = get_real_godot_console_path()
    manager = GodotProcessManager(
        executable_path=exe_path,
        project_dir=REAL_GODOT_PROJECT_DIR,
    )
    try:
        yield manager
    finally:
        is_active = (
            manager._process.state() != QProcess.ProcessState.NotRunning
            or manager.is_running
            or manager.state in (ProcessState.STARTING, ProcessState.RUNNING, ProcessState.STOPPING)
        )
        if is_active:
            active_pid = manager.pid
            try:
                with qtbot.waitSignal(manager.finished, timeout=5000):
                    manager.stop(timeout_ms=1000)
            except Exception:
                if active_pid and psutil.pid_exists(active_pid):
                    try:
                        p = psutil.Process(active_pid)
                        p.kill()
                    except Exception:
                        pass
                try:
                    qtbot.waitUntil(
                        lambda: manager._process.state() == QProcess.ProcessState.NotRunning,
                        timeout=3000,
                    )
                except Exception:
                    pass


def test_e2e_successful_handshake_and_state_transitions(
    qtbot: QtBot,
    test_server: WebSocketServer,
    e2e_godot_manager: GodotProcessManager,
) -> None:
    """Full end-to-end integration: handshake, SET_STATE roundtrip, and graceful SHUTDOWN."""
    client_authenticated_signals: list[tuple[str, str]] = []
    test_server.clientAuthenticated.connect(
        lambda name, ver: client_authenticated_signals.append((name, ver))
    )

    state_ack_signals: list[SetStateAckPayload] = []
    test_server.stateCommandAcknowledged.connect(state_ack_signals.append)

    shutdown_ack_signals: list[ShutdownAckPayload] = []
    test_server.shutdownAcknowledged.connect(shutdown_ack_signals.append)

    # 1. Prepare safe process environment handoff
    env = prepare_process_environment(test_server.port, test_server.auth_token)

    # 2. Launch Godot process in headless mode
    with qtbot.waitSignal(e2e_godot_manager.started, timeout=5000):
        e2e_godot_manager.start(headless=True, env=env)

    # 3. Wait for client to connect and complete HELLO / WELCOME handshake
    qtbot.waitUntil(lambda: test_server.is_authenticated, timeout=8000)
    assert len(client_authenticated_signals) == 1
    client_name, client_version = client_authenticated_signals[0]
    assert client_name == "OCTA Godot Client"
    assert client_version == "0.1.0"
    assert test_server.robot_state == "IDLE"

    # 4. Command state transition to THINKING
    test_server.set_state("THINKING", "Testing transition")
    qtbot.waitUntil(lambda: len(state_ack_signals) == 1, timeout=5000)
    ack = state_ack_signals[0]
    assert ack.accepted is True
    assert ack.requested_state == "THINKING"
    assert ack.current_state == "THINKING"
    assert test_server.robot_state == "THINKING"

    # 5. Command state transition to SUCCESS
    test_server.set_state("SUCCESS", "Task completed")
    qtbot.waitUntil(lambda: len(state_ack_signals) == 2, timeout=5000)
    ack2 = state_ack_signals[1]
    assert ack2.accepted is True
    assert ack2.requested_state == "SUCCESS"
    assert ack2.current_state == "SUCCESS"
    assert test_server.robot_state == "SUCCESS"

    # 6. Graceful SHUTDOWN request
    with qtbot.waitSignal(e2e_godot_manager.finished, timeout=10000) as finish_blocker:
        test_server.request_shutdown("Integration test finished")

    assert len(shutdown_ack_signals) == 1
    assert shutdown_ack_signals[0].acknowledged is True

    exit_code, exit_status = finish_blocker.args
    assert exit_code == 0
    assert exit_status == "NormalExit"


def test_e2e_file_handoff_mechanism(
    qtbot: QtBot,
    test_server: WebSocketServer,
    e2e_godot_manager: GodotProcessManager,
    tmp_path: Path,
) -> None:
    """End-to-end integration using a secure one-time handoff file."""
    # Write handoff file in temp directory
    handoff_path = write_handoff_file(
        port=test_server.port,
        auth_token=test_server.auth_token,
        directory=tmp_path,
    )
    assert handoff_path.is_file()

    env = {ENV_HANDOFF_FILE: str(handoff_path)}

    with qtbot.waitSignal(e2e_godot_manager.started, timeout=5000):
        e2e_godot_manager.start(headless=True, env=env)

    # Client must authenticate and immediately delete the file
    qtbot.waitUntil(lambda: test_server.is_authenticated, timeout=8000)
    assert not handoff_path.exists()

    # Request graceful shutdown
    with qtbot.waitSignal(e2e_godot_manager.finished, timeout=10000) as finish_blocker:
        test_server.request_shutdown("File handoff test complete")

    exit_code, exit_status = finish_blocker.args
    assert exit_code == 0
    assert exit_status == "NormalExit"


def test_e2e_authentication_rejection(
    qtbot: QtBot,
    test_server: WebSocketServer,
    e2e_godot_manager: GodotProcessManager,
) -> None:
    """When client presents an invalid auth token, server rejects the handshake."""
    # Send incorrect bearer token
    env = prepare_process_environment(test_server.port, "invalid_unauthorized_token")

    with qtbot.waitSignal(e2e_godot_manager.started, timeout=5000):
        e2e_godot_manager.start(headless=True, env=env)

    # Server must never authenticate
    qtbot.wait(1500)
    assert test_server.is_authenticated is False


def test_e2e_heartbeat_ping_pong(
    qtbot: QtBot,
    e2e_godot_manager: GodotProcessManager,
) -> None:
    """Server sends periodic PINGs and Godot replies with valid correlated PONGs."""
    server = WebSocketServer(port=0, heartbeat_interval_ms=100)
    server.start()

    error_signals: list[str] = []
    server.errorOccurred.connect(error_signals.append)

    env = prepare_process_environment(server.port, server.auth_token)

    try:
        with qtbot.waitSignal(e2e_godot_manager.started, timeout=5000):
            e2e_godot_manager.start(headless=True, env=env)

        qtbot.waitUntil(lambda: server.is_authenticated, timeout=8000)

        # Allow multiple heartbeats (100ms interval) to exchange PING and PONG
        qtbot.wait(600)

        # Confirm the session remains alive and authenticated without any timeout/heartbeat errors
        assert server.is_authenticated is True
        assert server.state == ConnectionState.AUTHENTICATED
        assert len(error_signals) == 0

        # Graceful shutdown
        with qtbot.waitSignal(e2e_godot_manager.finished, timeout=10000) as finish_blocker:
            server.request_shutdown("Heartbeat test complete")

        exit_code, _ = finish_blocker.args
        assert exit_code == 0
    finally:
        if server.state != ConnectionState.STOPPED:
            server.stop()
            qtbot.waitUntil(lambda: server.state == ConnectionState.STOPPED, timeout=3000)
