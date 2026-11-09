"""Connection state enumeration for OCTA WebSocket communication."""

from __future__ import annotations

from enum import StrEnum


class ConnectionState(StrEnum):
    """Lifecycle states of the WebSocket communication channel.

    Note: This is strictly separate from process lifecycle states
    (octa.process.state.ProcessState) and robot companion states
    (octa.communication.messages.RobotState).
    """

    STOPPED = "STOPPED"
    LISTENING = "LISTENING"
    CONNECTED = "CONNECTED"
    AUTHENTICATED = "AUTHENTICATED"
    CLOSING = "CLOSING"
    ERROR = "ERROR"

    def __str__(self) -> str:
        return self.value
