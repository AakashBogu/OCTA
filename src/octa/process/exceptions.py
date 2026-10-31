"""Exception hierarchy for process management in OCTA."""

from __future__ import annotations


class ProcessError(Exception):
    """Base exception for all process management errors."""


class GodotNotFoundError(ProcessError):
    """Raised when the Godot executable cannot be found or is invalid."""


class GodotProjectNotFoundError(ProcessError):
    """Raised when the Godot project directory or project.godot file cannot be found."""


class ProcessAlreadyRunningError(ProcessError):
    """Raised when attempting to start a process that is already active."""


class ProcessLaunchError(ProcessError):
    """Raised when the process fails to launch at the operating system or runtime level."""
