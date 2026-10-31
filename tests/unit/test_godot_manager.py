"""Unit tests for GodotProcessManager using PySide6 and pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QProcess
from pytestqt.qtbot import QtBot

from octa.process.exceptions import (
    GodotNotFoundError,
    ProcessAlreadyRunningError,
)
from octa.process.manager import GodotProcessManager
from octa.process.state import ProcessState


class MockQProcess(QProcess):
    """Test double for QProcess that simulates process events without running binaries."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.mock_pid: int = 4321
        self.mock_process_state: QProcess.ProcessState = QProcess.ProcessState.NotRunning
        self.last_program: str | None = None
        self.last_arguments: list[str] | None = None
        self.last_working_dir: str | None = None
        self.mock_stdout: bytes = b""
        self.mock_stderr: bytes = b""
        self.auto_start: bool = True
        self.fail_to_start: bool = False
        self.mock_error_string: str = ""
        self.terminate_called: bool = False
        self.kill_called: bool = False
        self.auto_exit_on_terminate: bool = False
        self.auto_exit_on_kill: bool = True
        self.auto_exit_code: int = 0
        self.auto_exit_status: QProcess.ExitStatus = QProcess.ExitStatus.NormalExit
        self.terminate_exception: Exception | None = None
        self.kill_exception: Exception | None = None
        self.start_call_count: int = 0
        self.finished.connect(self._on_mock_finished)

    def _on_mock_finished(self, *args: object) -> None:
        self.mock_process_state = QProcess.ProcessState.NotRunning

    def state(self) -> QProcess.ProcessState:
        return self.mock_process_state

    def start(
        self,
        program: str,
        arguments: list[str],
        mode: QProcess.OpenMode = QProcess.OpenModeFlag.ReadWrite,
    ) -> None:
        self.start_call_count += 1
        self.last_program = program
        self.last_arguments = list(arguments)
        self.last_working_dir = self.workingDirectory()
        if self.fail_to_start:
            self.mock_process_state = QProcess.ProcessState.NotRunning
            self.mock_error_string = "Failed to spawn executable"
            self.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
        elif self.auto_start:
            self.mock_process_state = QProcess.ProcessState.Running
            self.started.emit()
        else:
            self.mock_process_state = QProcess.ProcessState.Starting

    def terminate(self) -> None:
        self.terminate_called = True
        if self.terminate_exception:
            raise self.terminate_exception
        if self.auto_exit_on_terminate:
            self.mock_process_state = QProcess.ProcessState.NotRunning
            self.finished.emit(self.auto_exit_code, self.auto_exit_status)

    def kill(self) -> None:
        self.kill_called = True
        if self.kill_exception:
            raise self.kill_exception
        if self.auto_exit_on_kill:
            self.mock_process_state = QProcess.ProcessState.NotRunning
            self.finished.emit(1, QProcess.ExitStatus.CrashExit)

    def processId(self) -> int:
        return self.mock_pid

    def readAllStandardOutput(self) -> bytes:
        out = self.mock_stdout
        self.mock_stdout = b""
        return out

    def readAllStandardError(self) -> bytes:
        err = self.mock_stderr
        self.mock_stderr = b""
        return err

    def errorString(self) -> str:
        return self.mock_error_string or super().errorString()


@pytest.fixture
def fake_godot_env(tmp_path: Path) -> tuple[Path, Path]:
    """Provide a validated fake Godot executable and project directory."""
    fake_exe = tmp_path / "bin" / "Godot_v4.7.2.exe"
    fake_exe.parent.mkdir(parents=True, exist_ok=True)
    fake_exe.write_bytes(b"MZfakebinary")

    proj_dir = tmp_path / "godot_project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    return fake_exe, proj_dir


# =========================================================================
# 1. Milestone 2.3.2 Base Tests (Preserved)
# =========================================================================


def test_initial_state(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify initial manager state before launch."""
    fake_exe, proj_dir = fake_godot_env
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir)

    assert manager.state == ProcessState.STOPPED
    assert manager.pid is None
    assert manager.is_running is False
    assert manager.executable_path == fake_exe
    assert manager.project_dir == proj_dir


def test_successful_asynchronous_launch(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify clean startup transitions and PID reporting."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)

    state_transitions: list[ProcessState] = []
    manager.state_changed.connect(state_transitions.append)

    with qtbot.waitSignal(manager.started) as blocker:
        manager.start()

    assert blocker.args == [4321]
    assert manager.pid == 4321
    assert manager.is_running is True
    assert manager.state == ProcessState.RUNNING
    assert state_transitions == [ProcessState.STARTING, ProcessState.RUNNING]

    assert mock_proc.last_program == str(fake_exe.resolve())
    assert mock_proc.last_arguments == ["--path", str(proj_dir.resolve())]
    assert mock_proc.last_working_dir == str(proj_dir.resolve())


def test_headless_argument_construction(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that headless=True appends --headless argument."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)

    manager.start(headless=True)
    assert mock_proc.last_arguments == ["--path", str(proj_dir.resolve()), "--headless"]


def test_duplicate_launch_prevention(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify ProcessAlreadyRunningError when starting while active."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)

    manager.start()
    assert manager.is_running is True

    with pytest.raises(ProcessAlreadyRunningError, match="already active"):
        manager.start()


def test_stdout_capture_and_decoding(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify asynchronous stdout streaming and signal emission."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    mock_proc.mock_stdout = b"Godot Engine v4.7.2.stable\nRobot character loaded\n"
    with qtbot.waitSignal(manager.stdout_received) as blocker:
        mock_proc.readyReadStandardOutput.emit()

    assert "Robot character loaded" in blocker.args[0]


def test_stderr_capture_and_decoding(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify asynchronous stderr streaming and signal emission."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    mock_proc.mock_stderr = b"WARNING: Font texture missing\n"
    with qtbot.waitSignal(manager.stderr_received) as blocker:
        mock_proc.readyReadStandardError.emit()

    assert "WARNING: Font texture missing" in blocker.args[0]


def test_normal_exit_code_zero(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify normal exit with code 0 transitions to STOPPED."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    with qtbot.waitSignal(manager.finished) as blocker:
        mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)

    assert blocker.args == [0, "NormalExit"]
    assert manager.state == ProcessState.STOPPED
    assert manager.pid is None
    assert manager.is_running is False


def test_unexpected_nonzero_exit_transitions_to_crashed(
    qtbot: QtBot, fake_godot_env: tuple[Path, Path]
) -> None:
    """Verify nonzero exit code transitions to CRASHED."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    with qtbot.waitSignal(manager.finished) as blocker:
        mock_proc.finished.emit(1, QProcess.ExitStatus.NormalExit)

    assert blocker.args == [1, "NormalExit"]
    assert manager.state == ProcessState.CRASHED
    assert manager.pid is None
    assert manager.is_running is False


def test_crash_exit_status_transitions_to_crashed(
    qtbot: QtBot, fake_godot_env: tuple[Path, Path]
) -> None:
    """Verify CrashExit status transitions to CRASHED even if exit code is 0."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    with qtbot.waitSignal(manager.finished) as blocker:
        mock_proc.finished.emit(0, QProcess.ExitStatus.CrashExit)

    assert blocker.args == [0, "CrashExit"]
    assert manager.state == ProcessState.CRASHED


def test_failed_startup_transitions_to_error(
    qtbot: QtBot, fake_godot_env: tuple[Path, Path]
) -> None:
    """Verify process failure during startup transitions to ERROR state."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.fail_to_start = True

    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)

    with qtbot.waitSignal(manager.error_occurred) as blocker:
        manager.start()

    assert manager.state == ProcessState.ERROR
    assert "Failed to spawn executable" in blocker.args[0]


def test_preflight_validation_errors(tmp_path: Path) -> None:
    """Verify pre-flight validation failures emit error_occurred and raise."""
    missing_exe = tmp_path / "missing.exe"
    missing_proj = tmp_path / "missing_proj"

    manager = GodotProcessManager()

    captured_errors: list[str] = []
    manager.error_occurred.connect(captured_errors.append)

    with pytest.raises(GodotNotFoundError):
        manager.start(executable_path=missing_exe, project_dir=missing_proj)

    assert manager.state == ProcessState.ERROR
    assert len(captured_errors) == 1
    assert "not found" in captured_errors[0]


# =========================================================================
# 2. Milestone 2.3.3 Shutdown, Timeout, Restart & Cleanup Tests
# =========================================================================


def test_graceful_stop_request(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that calling stop() invokes QProcess.terminate()."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.stop()
    assert mock_proc.terminate_called is True


def test_stop_transitions_to_stopping(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that calling stop() immediately transitions state to STOPPING."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    states: list[ProcessState] = []
    manager.state_changed.connect(states.append)

    manager.stop()
    assert manager.state == ProcessState.STOPPING
    assert states == [ProcessState.STOPPING]


def test_stop_when_already_stopped(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that stop() does nothing when process is already stopped."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)

    manager.stop()
    assert manager.state == ProcessState.STOPPED
    assert mock_proc.terminate_called is False


def test_repeated_stop_requests_idempotent(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that repeated stop requests while STOPPING do not create duplicate timers."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.stop(timeout_ms=5000)
    assert manager.state == ProcessState.STOPPING

    # Second stop call
    manager.stop(timeout_ms=5000)
    assert manager.state == ProcessState.STOPPING


def test_graceful_exit_before_timeout_cancels_timer(
    qtbot: QtBot, fake_godot_env: tuple[Path, Path]
) -> None:
    """Verify that graceful exit cancels the timeout timer and reaches STOPPED."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.stop(timeout_ms=5000)
    assert manager.state == ProcessState.STOPPING
    assert manager._shutdown_timer.isActive() is True

    # Process exits cleanly before timeout
    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)

    assert manager.state == ProcessState.STOPPED
    assert manager._shutdown_timer.isActive() is False
    assert mock_proc.kill_called is False


def test_timeout_triggers_force_kill(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that expiration of shutdown timeout invokes QProcess.kill()."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    # Stop with a tiny timeout (20 ms)
    manager.stop(timeout_ms=20)
    assert manager.state == ProcessState.STOPPING

    # Wait for the timer to expire and trigger force-kill
    with qtbot.waitSignal(manager.finished) as blocker:
        pass

    assert mock_proc.kill_called is True
    assert blocker.args == [1, "CrashExit"]
    assert manager.state == ProcessState.CRASHED


def test_timer_cancelled_on_exit(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that timeout timer is stopped immediately on process exit."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.stop(timeout_ms=10000)
    assert manager._shutdown_timer.isActive() is True

    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert manager._shutdown_timer.isActive() is False


def test_stale_timeout_cannot_kill_replacement_process(
    qtbot: QtBot, fake_godot_env: tuple[Path, Path]
) -> None:
    """Verify that a stale timeout callback cannot kill a replacement process."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.stop(timeout_ms=10000)
    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert manager.state == ProcessState.STOPPED

    # Start replacement process
    manager.start()
    assert manager.state == ProcessState.RUNNING

    # Manually invoke the timeout slot as if an orphaned timer fired
    manager._on_shutdown_timeout()
    assert mock_proc.kill_called is False
    assert manager.state == ProcessState.RUNNING


def test_restart_waits_for_old_process_to_finish(
    qtbot: QtBot, fake_godot_env: tuple[Path, Path]
) -> None:
    """Verify that restart requests stop and does not start replacement until finished."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()
    assert mock_proc.start_call_count == 1

    manager.restart(timeout_ms=5000)
    assert manager.state == ProcessState.STOPPING
    assert mock_proc.terminate_called is True
    assert mock_proc.start_call_count == 1  # Not started yet!

    # Old process finishes
    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)

    # Replacement process launched now
    assert mock_proc.start_call_count == 2
    assert manager.state == ProcessState.RUNNING


def test_restart_starts_exactly_one_replacement(
    qtbot: QtBot, fake_godot_env: tuple[Path, Path]
) -> None:
    """Verify that restart launches exactly one replacement process."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.restart()
    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)

    assert mock_proc.start_call_count == 2
    assert manager.is_running is True


def test_restart_from_stopped(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that restart when STOPPED starts the process immediately."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    assert manager.state == ProcessState.STOPPED

    manager.restart()
    assert manager.state == ProcessState.RUNNING
    assert mock_proc.start_call_count == 1


def test_restart_after_crash(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that restart from CRASHED state starts the process immediately."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    # Process crashes
    mock_proc.finished.emit(1, QProcess.ExitStatus.NormalExit)
    assert manager.state == ProcessState.CRASHED

    # Restart
    manager.restart()
    assert manager.state == ProcessState.RUNNING
    assert mock_proc.start_call_count == 2


def test_restart_during_starting(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify restart behavior when invoked during STARTING state."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.auto_start = False  # Stay in STARTING state

    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()
    assert manager.state == ProcessState.STARTING

    manager.restart()
    assert manager.state == ProcessState.STOPPING

    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert mock_proc.start_call_count == 2


def test_restart_during_stopping(qtbot: QtBot, fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that multiple restart calls while STOPPING only launch one process."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.stop()
    assert manager.state == ProcessState.STOPPING

    # Multiple restart calls while STOPPING
    manager.restart()
    manager.restart()

    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert mock_proc.start_call_count == 2  # 1 initial + 1 replacement


def test_cleanup_is_idempotent(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that calling cleanup() multiple times is safe and idempotent."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.cleanup()
    assert manager.state == ProcessState.STOPPED
    assert manager.pid is None
    assert manager._shutdown_timer.isActive() is False

    # Second call should not raise or change state
    manager.cleanup()
    assert manager.state == ProcessState.STOPPED


def test_pid_reset_on_stop_and_cleanup(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that PID is reset when process stops or is cleaned up."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()
    assert manager.pid == 4321

    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert manager.pid is None

    manager.start()
    assert manager.pid == 4321
    manager.cleanup()
    assert manager.pid is None


def test_failure_during_terminate_handled_safely(
    qtbot: QtBot, fake_godot_env: tuple[Path, Path]
) -> None:
    """Verify that an exception raised by terminate() emits error_occurred and force-kills."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.terminate_exception = OSError("Access denied on terminate")

    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    errors: list[str] = []
    manager.error_occurred.connect(errors.append)

    manager.stop()
    assert mock_proc.kill_called is True
    assert len(errors) >= 1
    assert "Failed to terminate" in errors[0]


# =========================================================================
# 5. Targeted Lifecycle Audit Tests (Milestone 2.3.3 Audit)
# =========================================================================


def test_failure_during_kill_handled_safely(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that exceptions raised during kill() in cleanup and timeout are caught safely."""
    fake_exe, proj_dir = fake_godot_env

    # 1. Test kill exception during cleanup()
    mock_proc1 = MockQProcess()
    mock_proc1.kill_exception = OSError("Access denied during cleanup kill")
    manager1 = GodotProcessManager(
        executable_path=fake_exe, project_dir=proj_dir, process=mock_proc1
    )
    manager1.start()

    errors1: list[str] = []
    manager1.error_occurred.connect(errors1.append)
    manager1.cleanup()

    assert mock_proc1.kill_called is True
    assert len(errors1) >= 1
    assert "Failed to kill process during cleanup" in errors1[0]

    # 2. Test kill exception during _on_shutdown_timeout()
    mock_proc2 = MockQProcess()
    mock_proc2.kill_exception = OSError("Access denied during timeout kill")
    manager2 = GodotProcessManager(
        executable_path=fake_exe, project_dir=proj_dir, process=mock_proc2
    )
    manager2.start()
    manager2.stop()
    assert manager2.state == ProcessState.STOPPING

    errors2: list[str] = []
    manager2.error_occurred.connect(errors2.append)
    manager2._on_shutdown_timeout()

    assert mock_proc2.kill_called is True
    assert len(errors2) >= 1
    assert "Failed to kill process" in errors2[0]


def test_cleanup_while_running(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify cleanup() while in RUNNING state terminates process and cancels timers."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()
    assert manager.state == ProcessState.RUNNING
    assert manager.pid == 4321

    manager.cleanup()

    assert mock_proc.kill_called is True
    assert manager.state == ProcessState.STOPPED
    assert manager.pid is None
    assert manager.is_running is False
    assert manager._shutdown_timer.isActive() is False


def test_cleanup_while_starting(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify cleanup() while in STARTING state terminates process and cancels timers."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.auto_start = False
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)

    manager.start()
    assert manager.state == ProcessState.STARTING

    manager.cleanup()
    assert mock_proc.kill_called is True
    assert manager.state == ProcessState.STOPPED
    assert manager.pid is None


def test_cleanup_while_stopping(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify cleanup() while in STOPPING state safely forces termination and cancels timers."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.auto_exit_on_terminate = False
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    manager.stop()
    assert manager.state == ProcessState.STOPPING
    assert manager._shutdown_timer.isActive() is True

    manager.cleanup()
    assert manager._shutdown_timer.isActive() is False
    assert mock_proc.kill_called is True
    assert manager.state == ProcessState.STOPPED
    assert manager.pid is None


def test_stale_timeout_from_process_a_does_not_kill_process_b_in_stopping(
    fake_godot_env: tuple[Path, Path],
) -> None:
    """Verify that an expired timeout from an earlier process cannot kill a newer process.

    Scenario:
      1. Process A enters STOPPING.
      2. Process A finishes.
      3. Process B starts.
      4. Process B enters STOPPING.
      5. An artificially triggered stale timeout from Process A fires.
      6. Process B must not receive kill() from that stale callback.
    """
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.auto_exit_on_terminate = False
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)

    # 1. Process A starts and enters STOPPING
    manager.start()
    gen_a = manager.generation
    manager.stop()
    assert manager.state == ProcessState.STOPPING

    # 2. Process A finishes
    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert manager.state == ProcessState.STOPPED

    # 3. Process B starts
    manager.start()
    gen_b = manager.generation
    assert gen_b > gen_a
    assert manager.state == ProcessState.RUNNING

    # 4. Process B enters STOPPING
    manager.stop()
    assert manager.state == ProcessState.STOPPING
    mock_proc.kill_called = False

    # 5. Artificially triggered stale timeout from Process A fires
    manager._on_shutdown_timeout(generation=gen_a)

    # 6. Process B must NOT receive kill() from the stale callback
    assert mock_proc.kill_called is False
    assert manager.state == ProcessState.STOPPING

    # Verify that Process B's legitimate timeout DOES trigger kill()
    manager._on_shutdown_timeout(generation=gen_b)
    assert mock_proc.kill_called is True


def test_restart_after_failed_startup(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify that restart recovery handles initial startup failures and pending restarts."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.fail_to_start = True
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)

    manager.start()
    assert manager.state == ProcessState.ERROR

    # Recover by clearing the failure and restarting
    mock_proc.fail_to_start = False
    manager.restart()
    assert manager.state == ProcessState.RUNNING
    assert mock_proc.start_call_count == 2

    # Also verify: restart queued while STARTING clears pending flag if launch fails
    mock_proc2 = MockQProcess()
    mock_proc2.auto_start = False
    manager2 = GodotProcessManager(
        executable_path=fake_exe, project_dir=proj_dir, process=mock_proc2
    )
    manager2.start()
    assert manager2.state == ProcessState.STARTING

    # Call restart while starting
    manager2.restart()
    assert manager2._pending_restart is True
    assert manager2.state == ProcessState.STOPPING

    # Process fails to start
    mock_proc2.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
    assert manager2.state == ProcessState.ERROR
    assert manager2._pending_restart is False


def test_restart_after_cleanup(fake_godot_env: tuple[Path, Path]) -> None:
    """Verify restart() works cleanly after cleanup and cleanup cancels pending restarts."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()

    # Case A: Cleanup, then restart cleanly launches new process
    manager.cleanup()
    assert manager.state == ProcessState.STOPPED

    manager.restart()
    assert manager.state == ProcessState.RUNNING
    assert mock_proc.start_call_count == 2

    # Case B: Cleanup cancels pending restart requests
    mock_proc2 = MockQProcess()
    mock_proc2.auto_exit_on_terminate = False
    mock_proc2.auto_exit_on_kill = False
    manager2 = GodotProcessManager(
        executable_path=fake_exe, project_dir=proj_dir, process=mock_proc2
    )
    manager2.start()

    manager2.restart()
    assert manager2._pending_restart is True
    assert manager2.state == ProcessState.STOPPING

    # Cleanup cancels the pending restart
    manager2.cleanup()
    assert manager2._pending_restart is False

    # Process finally terminates
    mock_proc2.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert manager2.state == ProcessState.STOPPED
    assert mock_proc2.start_call_count == 1  # No replacement was spawned


def test_process_exits_naturally_during_restart_sequencing(
    fake_godot_env: tuple[Path, Path],
) -> None:
    """Verify that a normal natural process exit triggers pending restart replacement."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.auto_exit_on_terminate = False
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()
    assert manager.generation == 1

    manager.restart()
    assert manager.state == ProcessState.STOPPING
    assert manager._pending_restart is True

    # Process exits naturally with code 0 NormalExit
    mock_proc.finished.emit(0, QProcess.ExitStatus.NormalExit)

    # Replacement process should have started immediately
    assert manager.state == ProcessState.RUNNING
    assert manager.generation == 2
    assert mock_proc.start_call_count == 2


def test_cleanup_does_not_report_stopped_before_finished(
    fake_godot_env: tuple[Path, Path],
) -> None:
    """Verify that cleanup() does not report STOPPED or reset PID until process finishes."""
    fake_exe, proj_dir = fake_godot_env
    mock_proc = MockQProcess()
    mock_proc.auto_exit_on_kill = False
    manager = GodotProcessManager(executable_path=fake_exe, project_dir=proj_dir, process=mock_proc)
    manager.start()
    assert manager.state == ProcessState.RUNNING
    assert manager.pid == 4321

    # Cleanup called, kill requested asynchronously
    manager.cleanup()
    assert mock_proc.kill_called is True

    # Must NOT report STOPPED or reset PID yet
    assert manager.state == ProcessState.STOPPING
    assert manager.pid == 4321
    assert manager.is_running is False

    # Process finally emits finished
    mock_proc.finished.emit(1, QProcess.ExitStatus.CrashExit)

    # Now state is finalized to STOPPED and PID is reset
    assert manager.state == ProcessState.STOPPED
    assert manager.pid is None
