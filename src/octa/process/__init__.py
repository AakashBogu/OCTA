"""OCTA process management subsystem."""

from octa.process.exceptions import (
    GodotNotFoundError,
    GodotProjectNotFoundError,
    ProcessAlreadyRunningError,
    ProcessError,
    ProcessLaunchError,
)
from octa.process.manager import GodotProcessManager
from octa.process.state import ProcessState
from octa.process.validator import (
    validate_executable_path,
    validate_godot_environment,
    validate_project_dir,
)

__all__ = [
    "GodotNotFoundError",
    "GodotProcessManager",
    "GodotProjectNotFoundError",
    "ProcessAlreadyRunningError",
    "ProcessError",
    "ProcessLaunchError",
    "ProcessState",
    "validate_executable_path",
    "validate_godot_environment",
    "validate_project_dir",
]
