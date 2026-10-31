"""Integration tests for GodotProcessManager against the real installed Godot binary."""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path

import psutil
import pytest
from PySide6.QtCore import QProcess
from pytestqt.qtbot import QtBot

from octa.process.exceptions import (
    GodotNotFoundError,
    GodotProjectNotFoundError,
    ProcessAlreadyRunningError,
)
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


# Mark all tests in this module as integration tests and skip if binary is unavailable
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not get_real_godot_console_path().is_file(),
        reason=f"Real Godot console executable not found at {get_real_godot_console_path()}",
    ),
]


@pytest.fixture
def real_godot_manager(qtbot: QtBot) -> Generator[GodotProcessManager, None, None]:
    """Provide a GodotProcessManager instance bound to the real Godot binary and project."""
    exe_path = get_real_godot_console_path()
    manager = GodotProcessManager(
        executable_path=exe_path,
        project_dir=REAL_GODOT_PROJECT_DIR,
    )
    try:
        yield manager
    finally:
        # Guarantee confirmed termination before QProcess destruction, even if an assertion failed
        is_active = (
            manager._process.state() != QProcess.ProcessState.NotRunning
            or manager.is_running
            or manager.state
            in (
                ProcessState.STARTING,
                ProcessState.RUNNING,
                ProcessState.STOPPING,
            )
        )
        if is_active:
            emergency_killed = False
            active_pid = manager.pid
            try:
                with qtbot.waitSignal(manager.finished, timeout=5000):
                    manager.cleanup()
            except Exception:
                # Stage 1: Graceful/manager cleanup timed out after 5000ms.
                # Initiate emergency OS-level termination via psutil.
                emergency_killed = True
                if active_pid and psutil.pid_exists(active_pid):
                    try:
                        p = psutil.Process(active_pid)
                        p.kill()
                    except Exception:
                        pass

                # Stage 2: Bounded second cleanup wait (3000ms) to pump Qt event loop
                # and guarantee QProcess internal state transitions to NotRunning.
                try:
                    qtbot.waitUntil(
                        lambda: manager._process.state() == QProcess.ProcessState.NotRunning,
                        timeout=3000,
                    )
                except Exception:
                    # Stage 3: If still not confirmed stopped, capture full diagnostic info
                    current_qstate = manager._process.state().name
                    os_alive = psutil.pid_exists(active_pid) if active_pid else False
                    pytest.fail(
                        f"Emergency teardown failed: Godot process (PID {active_pid}) could not be "
                        f"confirmed stopped (QProcess state: {current_qstate}, "
                        f"manager state: {manager.state.value}, OS alive: {os_alive})."
                    )

            if emergency_killed:
                pytest.fail(
                    f"Teardown timeout: Godot process (PID {active_pid}) failed to finish "
                    "within 5000ms and required emergency OS termination."
                )


def test_real_godot_headless_startup_and_exit(
    qtbot: QtBot, real_godot_manager: GodotProcessManager
) -> None:
    """Test A: Launch real Godot in headless mode with bounded frames and verify lifecycle."""
    state_transitions: list[ProcessState] = []
    real_godot_manager.state_changed.connect(state_transitions.append)

    with qtbot.waitSignal(real_godot_manager.started, timeout=5000) as start_blocker:
        real_godot_manager.start(headless=True, extra_args=["--quit-after", "10"])

    pid = start_blocker.args[0]
    assert pid is not None
    assert pid > 0
    assert real_godot_manager.pid == pid
    assert real_godot_manager.is_running is True
    assert real_godot_manager.state == ProcessState.RUNNING

    # Wait for Godot to quit automatically after 10 frames
    with qtbot.waitSignal(real_godot_manager.finished, timeout=10000) as finish_blocker:
        pass

    exit_code, exit_status = finish_blocker.args
    assert exit_code == 0
    assert exit_status == "NormalExit"
    assert real_godot_manager.state == ProcessState.STOPPED
    assert real_godot_manager.pid is None
    assert real_godot_manager.is_running is False
    assert state_transitions == [
        ProcessState.STARTING,
        ProcessState.RUNNING,
        ProcessState.STOPPED,
    ]


def test_real_godot_output_and_utf8_decoding(
    qtbot: QtBot, real_godot_manager: GodotProcessManager
) -> None:
    """Test B: Capture real stdout from Godot, verify UTF-8 decoding and banner headers."""
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    real_godot_manager.stdout_received.connect(stdout_chunks.append)
    real_godot_manager.stderr_received.connect(stderr_chunks.append)

    with qtbot.waitSignal(real_godot_manager.started, timeout=5000):
        real_godot_manager.start(headless=True, extra_args=["--quit-after", "5"])

    with qtbot.waitSignal(real_godot_manager.finished, timeout=10000) as finish_blocker:
        pass

    exit_code, exit_status = finish_blocker.args
    assert exit_code == 0
    assert exit_status == "NormalExit"

    full_stdout = "".join(stdout_chunks)
    assert "Godot Engine v4.7.2" in full_stdout
    assert "https://godotengine.org" in full_stdout
    # Verify no unhandled Unicode replacement artifacts
    assert "\ufffd" not in full_stdout


def test_real_godot_path_validation_and_duplicate_launch_errors(
    qtbot: QtBot, real_godot_manager: GodotProcessManager
) -> None:
    """Test C: Validate error handling for invalid paths and duplicate start calls."""
    # 1. Invalid executable path
    mgr_bad_exe = GodotProcessManager(
        executable_path=Path("D:/invalid_dir/nonexistent_godot.exe"),
        project_dir=REAL_GODOT_PROJECT_DIR,
    )
    with pytest.raises(GodotNotFoundError):
        mgr_bad_exe.start()
    assert mgr_bad_exe.state == ProcessState.ERROR

    # 2. Invalid project directory
    mgr_bad_proj = GodotProcessManager(
        executable_path=get_real_godot_console_path(),
        project_dir=Path("D:/invalid_dir/nonexistent_project"),
    )
    with pytest.raises(GodotProjectNotFoundError):
        mgr_bad_proj.start()
    assert mgr_bad_proj.state == ProcessState.ERROR

    # 3. Duplicate start while active
    with qtbot.waitSignal(real_godot_manager.started, timeout=5000):
        real_godot_manager.start(headless=True, extra_args=["--quit-after", "1000"])

    with pytest.raises(ProcessAlreadyRunningError):
        real_godot_manager.start()

    # Confirmed cleanup: wait for finished signal so process is exited before test concludes
    with qtbot.waitSignal(real_godot_manager.finished, timeout=5000):
        real_godot_manager.cleanup()


def test_real_godot_shutdown_timeout_and_force_kill(
    qtbot: QtBot, real_godot_manager: GodotProcessManager
) -> None:
    """Test D: Observe real process stop behavior and force-kill fallback on Windows.

    Note on Windows Headless Console Behavior:
    In headless console mode on Windows, Godot does not create a Win32 GUI message
    loop. Consequently, WM_CLOSE dispatched by QProcess.terminate() is not processed.
    The manager's shutdown timer expires after timeout_ms, invoking QProcess.kill()
    (TerminateProcess on Windows) and transitioning the manager to CRASHED.
    """
    with qtbot.waitSignal(real_godot_manager.started, timeout=5000):
        real_godot_manager.start(headless=True, extra_args=["--quit-after", "2000"])

    pid = real_godot_manager.pid
    assert pid is not None
    assert pid > 0

    # Request stop with a fast 400ms timeout
    with qtbot.waitSignal(real_godot_manager.finished, timeout=6000) as blocker:
        real_godot_manager.stop(timeout_ms=400)
        assert real_godot_manager.state == ProcessState.STOPPING

    _exit_code, exit_status = blocker.args
    assert exit_status == "CrashExit"
    assert real_godot_manager.state == ProcessState.CRASHED
    assert real_godot_manager.pid is None
    assert real_godot_manager.is_running is False

    # Confirm direct Godot process PID is terminated
    # Note: Confirms termination of the direct process only; does not infer descendant containment
    if sys.platform == "win32":
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_DEAD


def test_real_godot_cleanup_terminates_active_process(
    qtbot: QtBot, real_godot_manager: GodotProcessManager
) -> None:
    """Verify cleanup() immediately force-terminates an active real Godot process.

    Important Scope Note:
    The integration test confirms termination of the direct Godot process.
    Descendant-process containment is not guaranteed by the current implementation.
    """
    with qtbot.waitSignal(real_godot_manager.started, timeout=5000):
        real_godot_manager.start(headless=True, extra_args=["--quit-after", "2000"])

    pid = real_godot_manager.pid
    assert pid is not None
    assert pid > 0

    with qtbot.waitSignal(real_godot_manager.finished, timeout=5000):
        real_godot_manager.cleanup()
        # Immediately upon cleanup(), termination is requested asynchronously:
        # It must NOT report STOPPED prematurely while termination is in flight.
        assert real_godot_manager.state == ProcessState.STOPPING
        assert real_godot_manager.pid == pid

    # After finished signal arrives from QProcess, state is finalized to STOPPED and PID is None:
    assert real_godot_manager.state == ProcessState.STOPPED
    assert real_godot_manager.pid is None
    assert real_godot_manager.is_running is False

    # Confirm direct Godot process PID is terminated
    if sys.platform == "win32":
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_DEAD
