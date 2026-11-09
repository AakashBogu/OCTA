"""Message schemas, payloads, and envelope definitions for OCTA protocol."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from octa.communication.exceptions import (
    InvalidMessageError,
    PayloadValidationError,
)


class RobotState(StrEnum):
    """Operational states of the Godot 3D companion robot."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SUCCESS = "SUCCESS"

    def __str__(self) -> str:
        return self.value


VALID_ROBOT_STATES: frozenset[str] = frozenset(s.value for s in RobotState)


def is_valid_robot_state(state: str) -> bool:
    """Check if the provided string is a recognized robot state."""
    return isinstance(state, str) and state in VALID_ROBOT_STATES


class MessageType(StrEnum):
    """Protocol message type identifiers."""

    HELLO = "HELLO"
    WELCOME = "WELCOME"
    PING = "PING"
    PONG = "PONG"
    SET_STATE = "SET_STATE"
    SET_STATE_ACK = "SET_STATE_ACK"
    STATE_CHANGED = "STATE_CHANGED"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"
    SHUTDOWN_ACK = "SHUTDOWN_ACK"

    def __str__(self) -> str:
        return self.value


VALID_MESSAGE_TYPES: frozenset[str] = frozenset(m.value for m in MessageType)


def is_valid_message_type(msg_type: str) -> bool:
    """Check if the provided string is a recognized message type."""
    return isinstance(msg_type, str) and msg_type in VALID_MESSAGE_TYPES


@dataclass(frozen=True)
class HelloPayload:
    """Handshake initiation payload sent by the Godot client."""

    auth_token: str = field(repr=False)
    client_name: str
    client_version: str
    initial_state: str

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {
            "auth_token": self.auth_token,
            "client_name": self.client_name,
            "client_version": self.client_version,
            "initial_state": self.initial_state,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create HelloPayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"auth_token", "client_name", "client_version", "initial_state"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(f"Unexpected fields in HelloPayload: {sorted(extra)}")

        missing = allowed_keys - set(data.keys())
        if missing:
            raise PayloadValidationError(
                f"Missing required fields in HelloPayload: {sorted(missing)}"
            )

        auth_token = data["auth_token"]
        if not isinstance(auth_token, str) or not auth_token:
            raise PayloadValidationError("Field 'auth_token' must be a non-empty string")

        client_name = data["client_name"]
        if not isinstance(client_name, str) or not client_name:
            raise PayloadValidationError("Field 'client_name' must be a non-empty string")

        client_version = data["client_version"]
        if not isinstance(client_version, str) or not client_version:
            raise PayloadValidationError("Field 'client_version' must be a non-empty string")

        initial_state = data["initial_state"]
        if not is_valid_robot_state(initial_state):
            valid_list = sorted(VALID_ROBOT_STATES)
            raise PayloadValidationError(
                f"Field 'initial_state' must be one of {valid_list}, got {initial_state!r}"
            )

        return cls(
            auth_token=auth_token,
            client_name=client_name,
            client_version=client_version,
            initial_state=initial_state,
        )


@dataclass(frozen=True)
class WelcomePayload:
    """Handshake acknowledgement payload sent by the Python server."""

    server_name: str
    server_version: str
    confirmed_state: str
    heartbeat_interval_ms: int

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {
            "server_name": self.server_name,
            "server_version": self.server_version,
            "confirmed_state": self.confirmed_state,
            "heartbeat_interval_ms": self.heartbeat_interval_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create WelcomePayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {
            "server_name",
            "server_version",
            "confirmed_state",
            "heartbeat_interval_ms",
        }
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(f"Unexpected fields in WelcomePayload: {sorted(extra)}")

        missing = allowed_keys - set(data.keys())
        if missing:
            raise PayloadValidationError(
                f"Missing required fields in WelcomePayload: {sorted(missing)}"
            )

        server_name = data["server_name"]
        if not isinstance(server_name, str) or not server_name:
            raise PayloadValidationError("Field 'server_name' must be a non-empty string")

        server_version = data["server_version"]
        if not isinstance(server_version, str) or not server_version:
            raise PayloadValidationError("Field 'server_version' must be a non-empty string")

        confirmed_state = data["confirmed_state"]
        if not is_valid_robot_state(confirmed_state):
            valid_list = sorted(VALID_ROBOT_STATES)
            raise PayloadValidationError(
                f"Field 'confirmed_state' must be one of {valid_list}, got {confirmed_state!r}"
            )

        heartbeat = data["heartbeat_interval_ms"]
        if not isinstance(heartbeat, int) or isinstance(heartbeat, bool) or heartbeat <= 0:
            raise PayloadValidationError("Field 'heartbeat_interval_ms' must be a positive integer")

        return cls(
            server_name=server_name,
            server_version=server_version,
            confirmed_state=confirmed_state,
            heartbeat_interval_ms=heartbeat,
        )


@dataclass(frozen=True)
class PingPayload:
    """Heartbeat ping payload."""

    data: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {"data": self.data}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create PingPayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"data"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(f"Unexpected fields in PingPayload: {sorted(extra)}")

        payload_data = data.get("data", "")
        if not isinstance(payload_data, str):
            raise PayloadValidationError("Field 'data' must be a string")

        return cls(data=payload_data)


@dataclass(frozen=True)
class PongPayload:
    """Heartbeat pong payload."""

    data: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {"data": self.data}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create PongPayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"data"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(f"Unexpected fields in PongPayload: {sorted(extra)}")

        payload_data = data.get("data", "")
        if not isinstance(payload_data, str):
            raise PayloadValidationError("Field 'data' must be a string")

        return cls(data=payload_data)


@dataclass(frozen=True)
class SetStatePayload:
    """Command requesting robot companion state transition."""

    state: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {"state": self.state, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create SetStatePayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"state", "reason"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(f"Unexpected fields in SetStatePayload: {sorted(extra)}")

        if "state" not in data:
            raise PayloadValidationError("Missing required field 'state' in SetStatePayload")

        state = data["state"]
        if not is_valid_robot_state(state):
            valid_list = sorted(VALID_ROBOT_STATES)
            raise PayloadValidationError(
                f"Field 'state' must be one of {valid_list}, got {state!r}"
            )

        reason = data.get("reason", "")
        if not isinstance(reason, str):
            raise PayloadValidationError("Field 'reason' must be a string")

        return cls(state=state, reason=reason)


@dataclass(frozen=True)
class SetStateAckPayload:
    """Response acknowledging or rejecting a state change request."""

    accepted: bool
    requested_state: str
    current_state: str
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {
            "accepted": self.accepted,
            "requested_state": self.requested_state,
            "current_state": self.current_state,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create SetStateAckPayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"accepted", "requested_state", "current_state", "error_message"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(
                f"Unexpected fields in SetStateAckPayload: {sorted(extra)}"
            )

        required = {"accepted", "requested_state", "current_state"}
        missing = required - set(data.keys())
        if missing:
            raise PayloadValidationError(
                f"Missing required fields in SetStateAckPayload: {sorted(missing)}"
            )

        accepted = data["accepted"]
        if not isinstance(accepted, bool):
            raise PayloadValidationError("Field 'accepted' must be a boolean")

        req_state = data["requested_state"]
        if not is_valid_robot_state(req_state):
            valid_list = sorted(VALID_ROBOT_STATES)
            raise PayloadValidationError(
                f"Field 'requested_state' must be one of {valid_list}, got {req_state!r}"
            )

        curr_state = data["current_state"]
        if not is_valid_robot_state(curr_state):
            valid_list = sorted(VALID_ROBOT_STATES)
            raise PayloadValidationError(
                f"Field 'current_state' must be one of {valid_list}, got {curr_state!r}"
            )

        err_msg = data.get("error_message")
        if err_msg is not None and not isinstance(err_msg, str):
            raise PayloadValidationError("Field 'error_message' must be a string or None")

        return cls(
            accepted=accepted,
            requested_state=req_state,
            current_state=curr_state,
            error_message=err_msg,
        )


@dataclass(frozen=True)
class StateChangedPayload:
    """Notification of autonomous or completed state transition."""

    state: str
    previous_state: str

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {
            "state": self.state,
            "previous_state": self.previous_state,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create StateChangedPayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"state", "previous_state"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(
                f"Unexpected fields in StateChangedPayload: {sorted(extra)}"
            )

        missing = allowed_keys - set(data.keys())
        if missing:
            raise PayloadValidationError(
                f"Missing required fields in StateChangedPayload: {sorted(missing)}"
            )

        state = data["state"]
        if not is_valid_robot_state(state):
            valid_list = sorted(VALID_ROBOT_STATES)
            raise PayloadValidationError(
                f"Field 'state' must be one of {valid_list}, got {state!r}"
            )

        prev_state = data["previous_state"]
        if not is_valid_robot_state(prev_state):
            valid_list = sorted(VALID_ROBOT_STATES)
            raise PayloadValidationError(
                f"Field 'previous_state' must be one of {valid_list}, got {prev_state!r}"
            )

        return cls(state=state, previous_state=prev_state)


@dataclass(frozen=True)
class ErrorPayload:
    """Structured error payload for protocol and application errors."""

    error_code: str
    error_message: str
    recoverable: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {
            "error_code": self.error_code,
            "error_message": self.error_message,
            "recoverable": self.recoverable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create ErrorPayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"error_code", "error_message", "recoverable"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(f"Unexpected fields in ErrorPayload: {sorted(extra)}")

        required = {"error_code", "error_message"}
        missing = required - set(data.keys())
        if missing:
            raise PayloadValidationError(
                f"Missing required fields in ErrorPayload: {sorted(missing)}"
            )

        err_code = data["error_code"]
        if not isinstance(err_code, str) or not err_code:
            raise PayloadValidationError("Field 'error_code' must be a non-empty string")

        err_msg = data["error_message"]
        if not isinstance(err_msg, str):
            raise PayloadValidationError("Field 'error_message' must be a string")

        rec = data.get("recoverable", True)
        if not isinstance(rec, bool):
            raise PayloadValidationError("Field 'recoverable' must be a boolean")

        return cls(error_code=err_code, error_message=err_msg, recoverable=rec)


@dataclass(frozen=True)
class ShutdownPayload:
    """Shutdown notification or request payload."""

    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {"reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create ShutdownPayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"reason"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(f"Unexpected fields in ShutdownPayload: {sorted(extra)}")

        reason = data.get("reason", "")
        if not isinstance(reason, str):
            raise PayloadValidationError("Field 'reason' must be a string")

        return cls(reason=reason)


@dataclass(frozen=True)
class ShutdownAckPayload:
    """Shutdown acknowledgement payload."""

    acknowledged: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary representation."""
        return {"acknowledged": self.acknowledged}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create ShutdownAckPayload from dictionary with strict validation."""
        if not isinstance(data, dict):
            raise PayloadValidationError("Payload must be a dictionary")

        allowed_keys = {"acknowledged"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise PayloadValidationError(
                f"Unexpected fields in ShutdownAckPayload: {sorted(extra)}"
            )

        ack = data.get("acknowledged", True)
        if not isinstance(ack, bool):
            raise PayloadValidationError("Field 'acknowledged' must be a boolean")

        return cls(acknowledged=ack)


type AnyPayload = (
    HelloPayload
    | WelcomePayload
    | PingPayload
    | PongPayload
    | SetStatePayload
    | SetStateAckPayload
    | StateChangedPayload
    | ErrorPayload
    | ShutdownPayload
    | ShutdownAckPayload
)


@dataclass(frozen=True)
class Envelope:
    """Top-level message envelope framing all OCTA communication messages."""

    message_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    protocol_version: str = "1.0.0"
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize envelope to standard JSON-compatible dictionary."""
        return {
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "message_type": self.message_type,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Construct and strictly validate Envelope from a dictionary."""
        if not isinstance(data, dict):
            raise InvalidMessageError("Envelope must be a JSON object dictionary")

        allowed_keys = {
            "protocol_version",
            "message_id",
            "message_type",
            "timestamp",
            "correlation_id",
            "payload",
        }
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise InvalidMessageError(f"Unexpected envelope fields: {sorted(extra)}")

        required_keys = {
            "protocol_version",
            "message_id",
            "message_type",
            "timestamp",
            "payload",
        }
        missing = required_keys - set(data.keys())
        if missing:
            raise InvalidMessageError(f"Missing required envelope fields: {sorted(missing)}")

        protocol_version = data["protocol_version"]
        if not isinstance(protocol_version, str) or not protocol_version.strip():
            raise InvalidMessageError("Field 'protocol_version' must be a non-empty string")

        message_id = data["message_id"]
        if not isinstance(message_id, str) or not message_id.strip():
            raise InvalidMessageError("Field 'message_id' must be a non-empty string")

        message_type = data["message_type"]
        if not is_valid_message_type(message_type):
            valid_list = sorted(VALID_MESSAGE_TYPES)
            raise InvalidMessageError(
                f"Field 'message_type' must be one of {valid_list}, got {message_type!r}"
            )

        timestamp = data["timestamp"]
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise InvalidMessageError("Field 'timestamp' must be a non-empty string")

        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as e:
            raise InvalidMessageError(
                f"Field 'timestamp' is not a valid ISO 8601 string: {e}"
            ) from e

        correlation_id = data.get("correlation_id")
        if correlation_id is not None and (
            not isinstance(correlation_id, str) or not correlation_id.strip()
        ):
            raise InvalidMessageError("Field 'correlation_id' must be None or a non-empty string")

        payload = data["payload"]
        if not isinstance(payload, dict):
            raise InvalidMessageError("Field 'payload' must be a dictionary")

        return cls(
            protocol_version=protocol_version,
            message_id=message_id,
            message_type=message_type,
            timestamp=timestamp,
            correlation_id=correlation_id,
            payload=payload,
        )
