"""Protocol framing, SemVer validation, and JSON encoding/decoding."""

from __future__ import annotations

import json
import re
from typing import Any

from octa.communication.exceptions import (
    InvalidMessageError,
    MessageTooLargeError,
    PayloadValidationError,
    UnsupportedProtocolVersionError,
)
from octa.communication.messages import (
    VALID_MESSAGE_TYPES,
    AnyPayload,
    Envelope,
    ErrorPayload,
    HelloPayload,
    MessageType,
    PingPayload,
    PongPayload,
    SetStateAckPayload,
    SetStatePayload,
    ShutdownAckPayload,
    ShutdownPayload,
    StateChangedPayload,
    WelcomePayload,
)

PROTOCOL_VERSION: str = "1.0.0"
MAX_MESSAGE_SIZE: int = 65536  # 64 KB

SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

PAYLOAD_TYPE_MAP: dict[MessageType, type[AnyPayload]] = {
    MessageType.HELLO: HelloPayload,
    MessageType.WELCOME: WelcomePayload,
    MessageType.PING: PingPayload,
    MessageType.PONG: PongPayload,
    MessageType.SET_STATE: SetStatePayload,
    MessageType.SET_STATE_ACK: SetStateAckPayload,
    MessageType.STATE_CHANGED: StateChangedPayload,
    MessageType.ERROR: ErrorPayload,
    MessageType.SHUTDOWN: ShutdownPayload,
    MessageType.SHUTDOWN_ACK: ShutdownAckPayload,
}


def is_version_compatible(version: str) -> bool:
    """Check if the provided version string is SemVer-compatible with this server.

    Compatibility rules:
    - Must be a valid Semantic Versioning (SemVer 2.0.0) string.
    - Major version must match the major version of PROTOCOL_VERSION (1.x.x).
    """
    if not isinstance(version, str):
        return False
    match = SEMVER_REGEX.match(version.strip())
    if not match:
        return False
    major = int(match.group(1))
    return major == 1


class ProtocolHandler:
    """Encoder, decoder, and validator for OCTA communication protocol messages."""

    @staticmethod
    def encode(envelope: Envelope) -> bytes:
        """Encode an Envelope into UTF-8 JSON bytes, enforcing size bounds.

        Raises:
            MessageTooLargeError: If encoded message size exceeds MAX_MESSAGE_SIZE.
        """
        if not isinstance(envelope, Envelope):
            raise InvalidMessageError(f"Expected Envelope instance, got {type(envelope).__name__}")

        raw_dict = envelope.to_dict()
        encoded = json.dumps(raw_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_MESSAGE_SIZE:
            raise MessageTooLargeError(
                f"Encoded message size {len(encoded)} bytes exceeds maximum {MAX_MESSAGE_SIZE}"
            )
        return encoded

    @staticmethod
    def decode(raw_data: bytes | str) -> Envelope:
        """Decode raw JSON bytes or string into an Envelope, validating envelope schema.

        Size validation occurs BEFORE parsing JSON to prevent memory exhaustion attacks.

        Raises:
            MessageTooLargeError: If payload exceeds MAX_MESSAGE_SIZE.
            InvalidMessageError: If message is empty, not valid JSON, or missing required fields.
            UnsupportedProtocolVersionError: If protocol_version is incompatible.
        """
        if isinstance(raw_data, str):
            raw_bytes = raw_data.encode("utf-8")
        elif isinstance(raw_data, (bytes, bytearray)):
            raw_bytes = bytes(raw_data)
        else:
            raise InvalidMessageError(f"Expected bytes or str, got {type(raw_data).__name__}")

        if len(raw_bytes) > MAX_MESSAGE_SIZE:
            raise MessageTooLargeError(
                f"Message size {len(raw_bytes)} bytes exceeds maximum {MAX_MESSAGE_SIZE} bytes"
            )

        if not raw_bytes.strip():
            raise InvalidMessageError("Cannot decode empty or whitespace-only message")

        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise InvalidMessageError(f"Failed to parse JSON payload: {e}") from e

        if not isinstance(data, dict):
            raise InvalidMessageError("Message root must be a JSON object dictionary")

        version = data.get("protocol_version")
        if not isinstance(version, str) or not is_version_compatible(version):
            raise UnsupportedProtocolVersionError(
                f"Unsupported protocol version {version!r}; expected major version 1"
            )

        return Envelope.from_dict(data)

    @staticmethod
    def validate_payload(envelope: Envelope) -> AnyPayload:
        """Validate and return the strongly-typed payload from an Envelope.

        Raises:
            InvalidMessageError: If envelope message_type is unrecognized.
            PayloadValidationError: If payload content fails schema validation.
        """
        if not isinstance(envelope, Envelope):
            raise InvalidMessageError(f"Expected Envelope instance, got {type(envelope).__name__}")

        try:
            msg_type = MessageType(envelope.message_type)
        except ValueError:
            raise InvalidMessageError(
                f"Unrecognized message type: {envelope.message_type!r}"
            ) from None

        payload_cls = PAYLOAD_TYPE_MAP.get(msg_type)
        if payload_cls is None:
            raise InvalidMessageError(
                f"No payload schema registered for message type {msg_type.value!r}"
            )

        return payload_cls.from_dict(envelope.payload)

    @classmethod
    def create_envelope(
        cls,
        message_type: MessageType | str,
        payload: dict[str, Any] | AnyPayload,
        correlation_id: str | None = None,
    ) -> Envelope:
        """Create a new validated Envelope.

        Accepts either a MessageType enum or string, and either a payload dataclass
        or a raw dictionary.
        """
        if isinstance(message_type, MessageType):
            type_str = message_type.value
        elif isinstance(message_type, str) and message_type in VALID_MESSAGE_TYPES:
            type_str = message_type
        else:
            raise InvalidMessageError(f"Invalid message type: {message_type!r}")

        if hasattr(payload, "to_dict") and callable(payload.to_dict):
            payload_dict = payload.to_dict()
        elif isinstance(payload, dict):
            payload_dict = payload
        else:
            raise PayloadValidationError(
                f"Payload must be a dict or payload dataclass, got {type(payload).__name__}"
            )

        return Envelope(
            message_type=type_str,
            payload=payload_dict,
            correlation_id=correlation_id,
        )

    @classmethod
    def create_error_envelope(
        cls,
        error_code: str,
        error_message: str,
        correlation_id: str | None = None,
        recoverable: bool = True,
    ) -> Envelope:
        """Helper to create a standard ERROR Envelope."""
        payload = ErrorPayload(
            error_code=error_code,
            error_message=error_message,
            recoverable=recoverable,
        )
        return cls.create_envelope(
            message_type=MessageType.ERROR,
            payload=payload,
            correlation_id=correlation_id,
        )
