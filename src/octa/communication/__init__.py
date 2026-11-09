"""Communication package for OCTA Python-Godot bridge."""

from __future__ import annotations

from octa.communication.exceptions import (
    AuthenticationError,
    CommunicationError,
    ConnectionTimeoutError,
    InvalidMessageError,
    MessageTooLargeError,
    PayloadValidationError,
    ProtocolError,
    UnsupportedProtocolVersionError,
)
from octa.communication.messages import (
    VALID_MESSAGE_TYPES,
    VALID_ROBOT_STATES,
    AnyPayload,
    Envelope,
    ErrorPayload,
    HelloPayload,
    MessageType,
    PingPayload,
    PongPayload,
    RobotState,
    SetStateAckPayload,
    SetStatePayload,
    ShutdownAckPayload,
    ShutdownPayload,
    StateChangedPayload,
    WelcomePayload,
    is_valid_message_type,
    is_valid_robot_state,
)
from octa.communication.protocol import (
    MAX_MESSAGE_SIZE,
    PROTOCOL_VERSION,
    ProtocolHandler,
    is_version_compatible,
)
from octa.communication.server import (
    DEFAULT_PORT,
    WebSocketServer,
    WebSocketSession,
)
from octa.communication.state import ConnectionState

__all__ = [
    "MAX_MESSAGE_SIZE",
    "PROTOCOL_VERSION",
    "DEFAULT_PORT",
    "VALID_MESSAGE_TYPES",
    "VALID_ROBOT_STATES",
    "AnyPayload",
    "AuthenticationError",
    "CommunicationError",
    "ConnectionState",
    "ConnectionTimeoutError",
    "Envelope",
    "ErrorPayload",
    "HelloPayload",
    "InvalidMessageError",
    "MessageTooLargeError",
    "MessageType",
    "PayloadValidationError",
    "PingPayload",
    "PongPayload",
    "ProtocolError",
    "ProtocolHandler",
    "RobotState",
    "SetStateAckPayload",
    "SetStatePayload",
    "ShutdownAckPayload",
    "ShutdownPayload",
    "StateChangedPayload",
    "UnsupportedProtocolVersionError",
    "WebSocketServer",
    "WebSocketSession",
    "WelcomePayload",
    "is_valid_message_type",
    "is_valid_robot_state",
    "is_version_compatible",
]
