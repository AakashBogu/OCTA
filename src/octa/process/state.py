"""Process state enumeration for OCTA runtime management."""

from __future__ import annotations

from enum import Enum


class ProcessState(Enum):
    """Lifecycle states of the Godot character runtime process."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    CRASHED = "CRASHED"
    ERROR = "ERROR"

    def __str__(self) -> str:
        return self.value
