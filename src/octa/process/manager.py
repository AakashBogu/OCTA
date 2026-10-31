"""Godot process lifecycle management using PySide6 QProcess."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, Slot

from octa.process.exceptions import (
    GodotNotFoundError,
    GodotProjectNotFoundError,
    ProcessAlreadyRunningError,
)
from octa.process.state import ProcessState
from octa.process.validator import validate_godot_environment

DEFAULT_SHUTDOWN_TIMEOUT_MS = 3000

# NOTE ON WINDOWS PROCESS TREE MANAGEMENT & JOB OBJECTS:
# On Windows, terminating a parent process via QProcess does not inherently guarantee
# termination of descendant child processes spawned by the child.
# While Windows Job Objects with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE provide kernel-level
# orphan prevention, assigning a QProcess to a Job Object in PySide6 presents fundamental
# race conditions:
#   1. QProcess executes CreateProcessW internally in Qt C++ and only exposes the integer PID,
#      not the native process HANDLE.
#   2. Calling OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, ...) in Python happens
#      asynchronously after the process has already begun executing, creating a window where
#      child processes can spawn before assignment.
#   3. If the process is launched within an existing Job Object (e.g., within certain IDEs,
#      CI harnesses, or terminals) without nested job support or breakaway privileges,
#      assignment fails with ERROR_ACCESS_DENIED.
# Therefore, kernel Job Object binding cannot be safely guaranteed within the QProcess
# abstraction and is deferred. Process monitoring and tree cleanup should leverage psutil
# process-tree traversal when needed.


class GodotProcessManager(QObject):
    """Manages the asynchronous execution, lifecycle, and I/O of the Godot character runtime."""

    # Qt Signals
    state_changed = Signal(ProcessState)
    started = Signal(int)
    finished = Signal(int, str)
    stdout_received = Signal(str)
    stderr_received = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        executable_path: str | Path | None = None,
        project_dir: str | Path | None = None,
        parent: QObject | None = None,
        process: QProcess | None = None,
    ) -> None:
        super().__init__(parent)
        self._executable_path = Path(executable_path) if executable_path else None
        self._project_dir = Path(project_dir) if project_dir else None
        self._state = ProcessState.STOPPED
        self._pid: int | None = None
        self._logger = logging.getLogger("octa.process")

        # Headless and restart sequencing flags
        self._headless = False
        self._extra_args: list[str] = []
        self._pending_restart = False
        self._force_killed = False
        self._cleaning_up = False

        # Monotonically increasing process generation token & timer ownership
        self._generation = 0
        self._shutdown_timer_generation: int | None = None

        # Shutdown timeout timer (single-shot, non-blocking)
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)

        # Own and manage a QProcess instance (supports dependency injection for testing)
        self._process = process if process is not None else QProcess(self)
        self._connect_process_signals()

    def _connect_process_signals(self) -> None:
        """Connect QProcess internal signals to manager handlers."""
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error_occurred)
        self._process.readyReadStandardOutput.connect(self._on_ready_read_stdout)
        self._process.readyReadStandardError.connect(self._on_ready_read_stderr)

    @property
    def state(self) -> ProcessState:
        """Current process lifecycle state."""
        return self._state

    @property
    def pid(self) -> int | None:
        """Operating system process ID if running, or None."""
        return self._pid

    @property
    def is_running(self) -> bool:
        """Whether the process is in RUNNING state."""
        return self._state == ProcessState.RUNNING

    @property
    def executable_path(self) -> Path | None:
        """Current Godot executable path."""
        return self._executable_path

    @property
    def project_dir(self) -> Path | None:
        """Current Godot project directory."""
        return self._project_dir

    @property
    def generation(self) -> int:
        """Monotonically increasing process lifecycle generation token."""
        return self._generation

    def _set_state(self, new_state: ProcessState) -> None:
        """Update internal state and emit state_changed signal if changed."""
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            self._logger.debug("State transition: %s -> %s", old_state.value, new_state.value)
            self.state_changed.emit(new_state)

    def start(
        self,
        executable_path: str | Path | None = None,
        project_dir: str | Path | None = None,
        headless: bool = False,
        extra_args: list[str] | None = None,
    ) -> None:
        """Asynchronously launch the Godot character runtime process.

        Args:
            executable_path: Path to Godot executable (defaults to configured path).
            project_dir: Path to Godot project directory (defaults to configured path).
            headless: If True, appends '--headless' argument (useful for testing).
            extra_args: Optional additional command-line arguments to pass to Godot.

        Raises:
            ProcessAlreadyRunningError: If a process is already STARTING or RUNNING.
            GodotNotFoundError: If the executable path is invalid or missing.
            GodotProjectNotFoundError: If the project path is invalid or missing.
        """
        if self._state in (ProcessState.STARTING, ProcessState.RUNNING):
            msg = f"Godot process is already active (PID: {self._pid}, State: {self._state.value})."
            self._logger.warning(msg)
            raise ProcessAlreadyRunningError(msg)

        target_exe = executable_path if executable_path is not None else self._executable_path
        target_proj = project_dir if project_dir is not None else self._project_dir

        try:
            valid_exe, valid_proj = validate_godot_environment(target_exe, target_proj)
        except (GodotNotFoundError, GodotProjectNotFoundError) as exc:
            self._set_state(ProcessState.ERROR)
            self.error_occurred.emit(str(exc))
            self._logger.error("Pre-flight validation failed: %s", exc)
            raise

        self._executable_path = valid_exe
        self._project_dir = valid_proj
        self._headless = headless
        self._extra_args = list(extra_args) if extra_args else []
        self._force_killed = False
        self._cleaning_up = False
        self._generation += 1

        # Explicit argument list (prevents command injection and quoting bugs on Windows)
        arguments = ["--path", str(valid_proj)]
        if headless:
            arguments.append("--headless")
        if self._extra_args:
            arguments.extend(self._extra_args)

        self._set_state(ProcessState.STARTING)
        self._process.setWorkingDirectory(str(valid_proj))

        self._logger.info(
            "Launching Godot process: %s with args %s in working directory %s (gen: %d)",
            valid_exe,
            arguments,
            valid_proj,
            self._generation,
        )
        self._process.start(str(valid_exe), arguments)

    def stop(self, timeout_ms: int = DEFAULT_SHUTDOWN_TIMEOUT_MS) -> None:
        """Request graceful process termination with a fallback force-kill timeout.

        Args:
            timeout_ms: Milliseconds to wait for graceful exit before issuing a force kill.
                Defaults to 3000 ms.
        """
        # If already stopped or in an inactive state, do nothing
        if self._state in (ProcessState.STOPPED, ProcessState.ERROR, ProcessState.CRASHED):
            self._logger.debug(
                "Stop requested but process is not active (State: %s).", self._state.value
            )
            return

        # If already stopping, do not duplicate shutdown requests or timers
        if self._state == ProcessState.STOPPING:
            self._logger.debug("Stop already in progress. Ignoring duplicate request.")
            return

        self._set_state(ProcessState.STOPPING)
        self._force_killed = False

        # Bind timeout timer to current process generation token
        self._shutdown_timer.stop()
        self._shutdown_timer_generation = self._generation
        self._shutdown_timer.setInterval(timeout_ms)
        self._shutdown_timer.start()

        self._logger.info(
            "Requesting graceful process termination (timeout: %d ms, gen: %d)...",
            timeout_ms,
            self._generation,
        )
        try:
            self._process.terminate()
        except Exception as exc:
            self._logger.error("Error invoking process terminate(): %s", exc)
            self.error_occurred.emit(f"Failed to terminate process: {exc}")
            # Immediately attempt force kill if terminate() raises an exception
            self._on_shutdown_timeout(generation=self._generation)

    def restart(self, timeout_ms: int = DEFAULT_SHUTDOWN_TIMEOUT_MS) -> None:
        """Restart the Godot character runtime process.

        If currently running or starting, requests shutdown and launches the replacement
        only after the previous process has completely exited.
        If currently stopping, queues restart upon completion.
        If currently stopped, error, or crashed, starts the replacement immediately.

        Args:
            timeout_ms: Milliseconds to wait for graceful exit before issuing a force kill.
        """
        is_active = self._process.state() != QProcess.ProcessState.NotRunning or self._state in (
            ProcessState.RUNNING,
            ProcessState.STARTING,
            ProcessState.STOPPING,
        )

        if is_active:
            self._pending_restart = True
            if self._state != ProcessState.STOPPING:
                self.stop(timeout_ms=timeout_ms)
        else:
            self._pending_restart = False
            self.start(
                executable_path=self._executable_path,
                project_dir=self._project_dir,
                headless=self._headless,
                extra_args=self._extra_args,
            )

    def cleanup(self) -> None:
        """Safely release and clean up process and timer resources.

        Cancels any active shutdown timers, drops pending restart requests,
        and terminates any active process asynchronously. The manager state
        transitions to STOPPED once process exit is confirmed by QProcess.
        This method is idempotent.
        """
        self._shutdown_timer.stop()
        self._shutdown_timer_generation = None
        self._pending_restart = False
        self._cleaning_up = True

        is_active = self._process.state() != QProcess.ProcessState.NotRunning or self._state in (
            ProcessState.STARTING,
            ProcessState.RUNNING,
            ProcessState.STOPPING,
        )

        if is_active:
            self._logger.warning("Cleanup terminating active process (PID: %s)...", self._pid)
            if self._state != ProcessState.STOPPING:
                self._set_state(ProcessState.STOPPING)
            try:
                self._process.kill()
            except Exception as exc:
                self._logger.error("Error during cleanup kill(): %s", exc)
                self.error_occurred.emit(f"Failed to kill process during cleanup: {exc}")
        else:
            self._cleaning_up = False
            self._pid = None
            self._force_killed = False
            if self._state not in (ProcessState.STOPPED, ProcessState.ERROR):
                self._set_state(ProcessState.STOPPED)

    @Slot()
    def _on_shutdown_timeout(self, generation: int | None = None) -> None:
        """Handle expiration of graceful shutdown timeout by force-killing the process.

        Args:
            generation: Optional process generation to validate against. If None,
                uses the generation recorded when the timer was armed.
        """
        self._shutdown_timer.stop()
        target_gen = self._shutdown_timer_generation if generation is None else generation

        # Discard stale timeout from a prior process lifecycle
        if target_gen != self._generation:
            self._logger.warning(
                "Stale shutdown timeout ignored (timer gen: %s, current gen: %d)",
                target_gen,
                self._generation,
            )
            return

        if self._state == ProcessState.STOPPING:
            self._logger.warning(
                "Graceful shutdown timed out for gen %d. Force-killing Godot process (PID: %s)...",
                self._generation,
                self._pid,
            )
            self._force_killed = True
            try:
                self._process.kill()
            except Exception as exc:
                self._logger.error("Error invoking process kill(): %s", exc)
                self.error_occurred.emit(f"Failed to kill process: {exc}")

    @Slot()
    def _on_started(self) -> None:
        """Handle QProcess started event."""
        self._pid = int(self._process.processId())
        if self._state == ProcessState.STOPPING:
            self._logger.info(
                "Godot process started during shutdown sequence (PID: %d). Continuing termination.",
                self._pid,
            )
        else:
            self._set_state(ProcessState.RUNNING)
            self._logger.info("Godot process started successfully (PID: %d)", self._pid)
        self.started.emit(self._pid)

    @Slot(int, QProcess.ExitStatus)
    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        """Handle QProcess finished event."""
        # Cancel shutdown timer immediately to prevent stale callbacks
        self._shutdown_timer.stop()
        self._shutdown_timer_generation = None

        status_name = "NormalExit" if exit_status == QProcess.ExitStatus.NormalExit else "CrashExit"
        self._logger.info("Godot process finished with exit code %d (%s)", exit_code, status_name)

        self._pid = None

        if self._cleaning_up:
            self._cleaning_up = False
            self._set_state(ProcessState.STOPPED)
            self._logger.info("Cleanup completed; process terminated.")
        elif exit_status == QProcess.ExitStatus.CrashExit or exit_code != 0:
            self._set_state(ProcessState.CRASHED)
            self._logger.error(
                "Godot process terminated abnormally (code: %d, status: %s)",
                exit_code,
                status_name,
            )
        else:
            self._set_state(ProcessState.STOPPED)
            self._logger.info("Godot process exited normally.")

        self._force_killed = False
        self.finished.emit(exit_code, status_name)

        # Handle pending restart sequence if queued (cannot restart if cleaning up)
        if self._pending_restart:
            self._pending_restart = False
            self._logger.info("Executing pending restart sequence...")
            try:
                self.start(
                    executable_path=self._executable_path,
                    project_dir=self._project_dir,
                    headless=self._headless,
                    extra_args=self._extra_args,
                )
            except Exception as exc:
                self._logger.error("Restart execution failed: %s", exc)

    @Slot(QProcess.ProcessError)
    def _on_error_occurred(self, error: QProcess.ProcessError) -> None:
        """Handle QProcess errorOccurred event."""
        error_msg = self._process.errorString() or f"Process error ({error.name})"
        if self._state == ProcessState.STARTING or error == QProcess.ProcessError.FailedToStart:
            self._shutdown_timer.stop()
            self._shutdown_timer_generation = None
            self._pending_restart = False
            self._pid = None
            self._set_state(ProcessState.ERROR)
            self._logger.error("Process startup failed: %s", error_msg)
        else:
            self._logger.warning("Process runtime error: %s", error_msg)

        self.error_occurred.emit(error_msg)

    @Slot()
    def _on_ready_read_stdout(self) -> None:
        """Capture and decode standard output asynchronously."""
        raw_bytes = bytes(self._process.readAllStandardOutput())
        text = raw_bytes.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line_str = line.strip()
            if line_str:
                self._logger.debug("Godot [stdout]: %s", line_str)
        self.stdout_received.emit(text)

    @Slot()
    def _on_ready_read_stderr(self) -> None:
        """Capture and decode standard error asynchronously."""
        raw_bytes = bytes(self._process.readAllStandardError())
        text = raw_bytes.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line_str = line.strip()
            if line_str:
                self._logger.warning("Godot [stderr]: %s", line_str)
        self.stderr_received.emit(text)
