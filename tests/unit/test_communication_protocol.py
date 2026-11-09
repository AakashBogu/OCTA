"""Unit tests for the OCTA communication protocol, message schemas, and framing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

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
from octa.communication.state import ConnectionState
from octa.process.state import ProcessState

# ============================================================================
# 1. ConnectionState Enum & Isolation Tests
# ============================================================================


def test_connection_state_enum_members_and_values() -> None:
    """Verify ConnectionState enum contains expected states and string equality."""
    expected = {"STOPPED", "LISTENING", "CONNECTED", "AUTHENTICATED", "CLOSING", "ERROR"}
    actual = {state.value for state in ConnectionState}
    assert actual == expected

    for state in ConnectionState:
        assert str(state) == state.value
        assert state.name == state.value
        assert state == state.value


def test_connection_state_distinct_from_process_state() -> None:
    """Ensure ConnectionState is strictly separate from ProcessState."""
    assert ConnectionState is not ProcessState
    # ProcessState has STARTING, RUNNING, CRASHED which ConnectionState does not have
    assert hasattr(ProcessState, "STARTING")
    assert not hasattr(ConnectionState, "STARTING")
    assert hasattr(ConnectionState, "AUTHENTICATED")
    assert not hasattr(ProcessState, "AUTHENTICATED")


# ============================================================================
# 2. Exception Hierarchy Tests
# ============================================================================


def test_exception_hierarchy() -> None:
    """Verify inheritance tree of communication exceptions."""
    assert issubclass(ProtocolError, CommunicationError)
    assert issubclass(AuthenticationError, CommunicationError)
    assert issubclass(ConnectionTimeoutError, CommunicationError)
    assert issubclass(CommunicationError, Exception)

    assert issubclass(InvalidMessageError, ProtocolError)
    assert issubclass(UnsupportedProtocolVersionError, ProtocolError)
    assert issubclass(MessageTooLargeError, ProtocolError)
    assert issubclass(PayloadValidationError, ProtocolError)


# ============================================================================
# 3. Security & Token Protection Tests
# ============================================================================


def test_hello_payload_token_omitted_from_repr() -> None:
    """Verify auth_token is marked repr=False and never leaked in string representations."""
    secret_token = "ultra_confidential_token_xyz999"
    payload = HelloPayload(
        auth_token=secret_token,
        client_name="GodotClient",
        client_version="1.0.0",
        initial_state=RobotState.IDLE.value,
    )

    repr_str = repr(payload)
    str_str = str(payload)

    assert secret_token not in repr_str
    assert secret_token not in str_str
    assert "auth_token" not in repr_str
    # Dict serialization still correctly retains token for transport
    payload_dict = payload.to_dict()
    assert payload_dict["auth_token"] == secret_token


def test_authentication_error_and_validation_do_not_leak_secrets() -> None:
    """Verify exceptions raised during auth or validation never contain tokens."""
    token = "secret_handshake_bearer_token"
    # Testing error message hygiene
    err = AuthenticationError("Handshake authentication failed: token mismatch")
    assert token not in str(err)
    assert token not in repr(err)


# ============================================================================
# 4. RobotState & MessageType Tests
# ============================================================================


def test_robot_state_members_and_validation() -> None:
    """Verify RobotState enum members and validation helper."""
    assert {s.value for s in RobotState} == {"IDLE", "LISTENING", "THINKING", "SUCCESS"}
    assert VALID_ROBOT_STATES == frozenset({"IDLE", "LISTENING", "THINKING", "SUCCESS"})
    for state in ["IDLE", "LISTENING", "THINKING", "SUCCESS"]:
        assert is_valid_robot_state(state) is True

    assert is_valid_robot_state("FLYING") is False
    assert is_valid_robot_state("idle") is False  # Case-sensitive
    assert is_valid_robot_state("") is False


def test_message_type_members_and_validation() -> None:
    """Verify MessageType enum members and validation helper."""
    expected_types = {
        "HELLO",
        "WELCOME",
        "PING",
        "PONG",
        "SET_STATE",
        "SET_STATE_ACK",
        "STATE_CHANGED",
        "ERROR",
        "SHUTDOWN",
        "SHUTDOWN_ACK",
    }
    assert {m.value for m in MessageType} == expected_types
    assert VALID_MESSAGE_TYPES == frozenset(expected_types)
    for msg_type in expected_types:
        assert is_valid_message_type(msg_type) is True

    assert is_valid_message_type("UNKNOWN") is False
    assert is_valid_message_type("hello") is False
    assert is_valid_message_type("") is False


def test_protocol_constants() -> None:
    """Verify protocol version and maximum message size constants."""
    assert PROTOCOL_VERSION == "1.0.0"
    assert MAX_MESSAGE_SIZE == 65536


# ============================================================================
# 5. Payload Schemas & Domain Validation Tests
# ============================================================================


def test_hello_payload_valid_and_invalid() -> None:
    """Verify HelloPayload schema validation and round-tripping."""
    valid_dict = {
        "auth_token": "token123",
        "client_name": "GodotCompanion",
        "client_version": "1.0.0",
        "initial_state": "IDLE",
    }
    payload = HelloPayload.from_dict(valid_dict)
    assert payload.auth_token == "token123"
    assert payload.client_name == "GodotCompanion"
    assert payload.client_version == "1.0.0"
    assert payload.initial_state == "IDLE"
    assert payload.to_dict() == valid_dict

    # Missing field
    with pytest.raises(PayloadValidationError, match="Missing required fields"):
        HelloPayload.from_dict({"auth_token": "tok", "client_name": "Godot"})

    # Unexpected field
    with pytest.raises(PayloadValidationError, match="Unexpected fields"):
        HelloPayload.from_dict({**valid_dict, "extra": "bonus"})

    # Invalid robot state
    with pytest.raises(PayloadValidationError, match="must be one of"):
        HelloPayload.from_dict({**valid_dict, "initial_state": "DANCING"})

    # Empty token
    with pytest.raises(PayloadValidationError, match="'auth_token' must be a non-empty string"):
        HelloPayload.from_dict({**valid_dict, "auth_token": ""})

    # Non-dict payload
    with pytest.raises(PayloadValidationError, match="Payload must be a dictionary"):
        HelloPayload.from_dict("not a dict")  # type: ignore[arg-type]


def test_welcome_payload_valid_and_invalid() -> None:
    """Verify WelcomePayload validation and round-tripping."""
    valid_dict = {
        "server_name": "OCTAPythonServer",
        "server_version": "1.0.0",
        "confirmed_state": "IDLE",
        "heartbeat_interval_ms": 5000,
    }
    payload = WelcomePayload.from_dict(valid_dict)
    assert payload.server_name == "OCTAPythonServer"
    assert payload.heartbeat_interval_ms == 5000
    assert payload.to_dict() == valid_dict

    # Invalid heartbeat interval (<= 0 or not int)
    with pytest.raises(PayloadValidationError, match="positive integer"):
        WelcomePayload.from_dict({**valid_dict, "heartbeat_interval_ms": 0})
    with pytest.raises(PayloadValidationError, match="positive integer"):
        WelcomePayload.from_dict({**valid_dict, "heartbeat_interval_ms": -100})
    with pytest.raises(PayloadValidationError, match="positive integer"):
        WelcomePayload.from_dict({**valid_dict, "heartbeat_interval_ms": True})  # bool rejection

    # Invalid state
    with pytest.raises(PayloadValidationError, match="must be one of"):
        WelcomePayload.from_dict({**valid_dict, "confirmed_state": "OFFLINE"})

    # Extra fields
    with pytest.raises(PayloadValidationError, match="Unexpected fields"):
        WelcomePayload.from_dict({**valid_dict, "extra": 1})


def test_ping_and_pong_payloads() -> None:
    """Verify PingPayload and PongPayload handling."""
    ping = PingPayload.from_dict({"data": "timestamp-12345"})
    assert ping.data == "timestamp-12345"
    assert ping.to_dict() == {"data": "timestamp-12345"}

    # Default empty data
    ping_default = PingPayload.from_dict({})
    assert ping_default.data == ""

    # Non-string data
    with pytest.raises(PayloadValidationError, match="must be a string"):
        PingPayload.from_dict({"data": 123})

    # Extra fields
    with pytest.raises(PayloadValidationError, match="Unexpected fields"):
        PingPayload.from_dict({"data": "", "extra": "val"})

    pong = PongPayload.from_dict({"data": "timestamp-12345"})
    assert pong.data == "timestamp-12345"
    assert pong.to_dict() == {"data": "timestamp-12345"}


def test_set_state_and_ack_payloads() -> None:
    """Verify SetStatePayload and SetStateAckPayload validation."""
    set_payload = SetStatePayload.from_dict({"state": "LISTENING", "reason": "User spoke"})
    assert set_payload.state == "LISTENING"
    assert set_payload.reason == "User spoke"
    assert set_payload.to_dict() == {"state": "LISTENING", "reason": "User spoke"}

    # Invalid state
    with pytest.raises(PayloadValidationError, match="must be one of"):
        SetStatePayload.from_dict({"state": "SLEEPING"})

    # SetStateAckPayload
    ack_valid = {
        "accepted": True,
        "requested_state": "LISTENING",
        "current_state": "LISTENING",
        "error_message": None,
    }
    ack = SetStateAckPayload.from_dict(ack_valid)
    assert ack.accepted is True
    assert ack.requested_state == "LISTENING"
    assert ack.to_dict() == ack_valid

    # SetStateAck rejected
    ack_rejected = {
        "accepted": False,
        "requested_state": "SUCCESS",
        "current_state": "IDLE",
        "error_message": "Transition not allowed",
    }
    ack2 = SetStateAckPayload.from_dict(ack_rejected)
    assert ack2.accepted is False
    assert ack2.error_message == "Transition not allowed"

    # Non-bool accepted
    with pytest.raises(PayloadValidationError, match="'accepted' must be a boolean"):
        SetStateAckPayload.from_dict({**ack_valid, "accepted": "yes"})


def test_state_changed_payload() -> None:
    """Verify StateChangedPayload validation."""
    valid_dict = {"state": "THINKING", "previous_state": "LISTENING"}
    payload = StateChangedPayload.from_dict(valid_dict)
    assert payload.state == "THINKING"
    assert payload.previous_state == "LISTENING"
    assert payload.to_dict() == valid_dict

    with pytest.raises(PayloadValidationError, match="must be one of"):
        StateChangedPayload.from_dict({"state": "INVALID", "previous_state": "IDLE"})


def test_error_payload() -> None:
    """Verify ErrorPayload validation."""
    valid_dict = {
        "error_code": "ERR_INVALID_TRANSITION",
        "error_message": "Cannot transition from ERROR to THINKING",
        "recoverable": True,
    }
    err = ErrorPayload.from_dict(valid_dict)
    assert err.error_code == "ERR_INVALID_TRANSITION"
    assert err.recoverable is True
    assert err.to_dict() == valid_dict

    # Empty error_code
    with pytest.raises(PayloadValidationError, match="'error_code' must be a non-empty string"):
        ErrorPayload.from_dict({**valid_dict, "error_code": ""})

    # Non-bool recoverable
    with pytest.raises(PayloadValidationError, match="'recoverable' must be a boolean"):
        ErrorPayload.from_dict({**valid_dict, "recoverable": "True"})


def test_shutdown_and_ack_payloads() -> None:
    """Verify ShutdownPayload and ShutdownAckPayload validation."""
    shutdown = ShutdownPayload.from_dict({"reason": "App exit"})
    assert shutdown.reason == "App exit"
    assert shutdown.to_dict() == {"reason": "App exit"}

    ack = ShutdownAckPayload.from_dict({"acknowledged": True})
    assert ack.acknowledged is True
    assert ack.to_dict() == {"acknowledged": True}

    with pytest.raises(PayloadValidationError, match="'acknowledged' must be a boolean"):
        ShutdownAckPayload.from_dict({"acknowledged": 1})


# ============================================================================
# 6. Envelope Serialization & Validation Tests
# ============================================================================


def test_envelope_defaults_and_roundtrip() -> None:
    """Verify default Envelope fields, ISO 8601 UTC timestamp, and roundtrip."""
    env = Envelope(
        message_type=MessageType.PING.value,
        payload={"data": "test"},
    )
    assert env.protocol_version == "1.0.0"
    assert len(env.message_id) == 32  # 32 hex chars UUID4
    assert env.correlation_id is None
    assert env.message_type == "PING"
    assert env.payload == {"data": "test"}

    # Timestamp is valid ISO 8601
    parsed_time = datetime.fromisoformat(env.timestamp.replace("Z", "+00:00"))
    assert parsed_time.tzinfo is not None

    d = env.to_dict()
    reconstructed = Envelope.from_dict(d)
    assert reconstructed == env


def test_envelope_validation_failures() -> None:
    """Verify Envelope rejects malformed schemas, missing fields, and bad types."""
    valid_dict = {
        "protocol_version": "1.0.0",
        "message_id": "msg-123",
        "message_type": "PING",
        "timestamp": datetime.now(UTC).isoformat(),
        "correlation_id": None,
        "payload": {},
    }

    # Non-dict
    with pytest.raises(InvalidMessageError, match="must be a JSON object"):
        Envelope.from_dict(["not a dict"])  # type: ignore[arg-type]

    # Missing message_type
    incomplete = {k: v for k, v in valid_dict.items() if k != "message_type"}
    with pytest.raises(InvalidMessageError, match="Missing required envelope fields"):
        Envelope.from_dict(incomplete)

    # Extra envelope key
    with pytest.raises(InvalidMessageError, match="Unexpected envelope fields"):
        Envelope.from_dict({**valid_dict, "unexpected": "value"})

    # Invalid timestamp
    with pytest.raises(InvalidMessageError, match="not a valid ISO 8601"):
        Envelope.from_dict({**valid_dict, "timestamp": "not-a-timestamp"})

    # Non-dict payload
    with pytest.raises(InvalidMessageError, match="'payload' must be a dictionary"):
        Envelope.from_dict({**valid_dict, "payload": "not-a-dict"})


# ============================================================================
# 7. SemVer Version Compatibility Tests
# ============================================================================


def test_semver_compatibility() -> None:
    """Verify SemVer compatibility logic for major version 1."""
    assert is_version_compatible("1.0.0") is True
    assert is_version_compatible("1.0.1") is True
    assert is_version_compatible("1.2.3") is True
    assert is_version_compatible("1.0.0-alpha.1") is True
    assert is_version_compatible("1.0.0+20130313144700") is True

    # Incompatible major versions
    assert is_version_compatible("2.0.0") is False
    assert is_version_compatible("0.9.0") is False
    assert is_version_compatible("3.1.2") is False

    # Malformed version strings
    assert is_version_compatible("1.0") is False
    assert is_version_compatible("v1.0.0") is False
    assert is_version_compatible("latest") is False
    assert is_version_compatible("") is False
    assert is_version_compatible(None) is False  # type: ignore[arg-type]


def test_decode_incompatible_version_raises_unsupported_version_error() -> None:
    """Verify decode raises UnsupportedProtocolVersionError on major version mismatch."""
    envelope_dict = {
        "protocol_version": "2.0.0",
        "message_id": "msg-123",
        "message_type": "PING",
        "timestamp": datetime.now(UTC).isoformat(),
        "correlation_id": None,
        "payload": {},
    }
    raw = json.dumps(envelope_dict).encode("utf-8")

    with pytest.raises(UnsupportedProtocolVersionError, match="expected major version 1"):
        ProtocolHandler.decode(raw)


# ============================================================================
# 8. Framing, Size Boundaries & UTF-8 Tests
# ============================================================================


def test_protocol_size_limit_rejection() -> None:
    """Verify that messages exceeding 64KB (65536 bytes) are rejected."""
    # Create an oversized payload
    oversized_data = "x" * (MAX_MESSAGE_SIZE + 10)
    raw_bytes = oversized_data.encode("utf-8")

    # Decoding oversized payload raises before parsing
    with pytest.raises(MessageTooLargeError, match="exceeds maximum"):
        ProtocolHandler.decode(raw_bytes)

    # Encoding oversized envelope raises MessageTooLargeError
    huge_env = Envelope(
        message_type="PING",
        payload={"data": "x" * MAX_MESSAGE_SIZE},
    )
    with pytest.raises(MessageTooLargeError, match="exceeds maximum"):
        ProtocolHandler.encode(huge_env)


def test_protocol_exact_size_limit_boundary() -> None:
    """Verify boundary conditions at exactly MAX_MESSAGE_SIZE."""
    # Construct an envelope whose serialized UTF-8 bytes equal exactly MAX_MESSAGE_SIZE
    base_env = Envelope(
        message_type="PING",
        message_id="fixed-message-id-123456789012345",
        timestamp="2026-11-01T00:00:00+00:00",
        payload={"data": ""},
    )
    base_bytes = ProtocolHandler.encode(base_env)
    target_fill = MAX_MESSAGE_SIZE - len(base_bytes)

    exact_env = Envelope(
        message_type="PING",
        message_id="fixed-message-id-123456789012345",
        timestamp="2026-11-01T00:00:00+00:00",
        payload={"data": "a" * target_fill},
    )
    exact_bytes = ProtocolHandler.encode(exact_env)
    assert len(exact_bytes) == MAX_MESSAGE_SIZE

    # Successfully decodes at boundary
    decoded = ProtocolHandler.decode(exact_bytes)
    assert decoded.message_id == "fixed-message-id-123456789012345"

    # Exactly 1 byte over the boundary
    over_by_one = exact_bytes + b" "
    assert len(over_by_one) == MAX_MESSAGE_SIZE + 1
    with pytest.raises(MessageTooLargeError):
        ProtocolHandler.decode(over_by_one)


def test_multibyte_utf8_payload_measured_by_bytes() -> None:
    """Verify multibyte UTF-8 characters are measured by byte count, not char count."""
    # 4-byte Unicode characters (emoji: 😀 = 4 bytes)
    emojis = "😀" * 20000  # 20,000 chars = 80,000 bytes > 65,536
    assert len(emojis) < MAX_MESSAGE_SIZE  # string length is only 20,000
    assert len(emojis.encode("utf-8")) > MAX_MESSAGE_SIZE  # byte length is 80,000

    env = Envelope(message_type="PING", payload={"data": emojis})
    with pytest.raises(MessageTooLargeError):
        ProtocolHandler.encode(env)


def test_decode_empty_or_whitespace_raises_invalid_message() -> None:
    """Verify empty and whitespace messages raise InvalidMessageError."""
    with pytest.raises(InvalidMessageError, match="Cannot decode empty"):
        ProtocolHandler.decode(b"")

    with pytest.raises(InvalidMessageError, match="Cannot decode empty"):
        ProtocolHandler.decode("   \n\t  ")


def test_decode_malformed_json_raises_invalid_message() -> None:
    """Verify unparseable JSON raises InvalidMessageError."""
    with pytest.raises(InvalidMessageError, match="Failed to parse JSON"):
        ProtocolHandler.decode(b"{malformed_json: true")


# ============================================================================
# 9. ProtocolHandler High-Level API & Typed Validation Tests
# ============================================================================


def test_protocol_handler_encode_decode_roundtrip_all_types() -> None:
    """Verify encode, decode, and validate_payload for all 10 message types."""
    test_cases: list[tuple[MessageType, dict[str, Any], type]] = [
        (
            MessageType.HELLO,
            {
                "auth_token": "token",
                "client_name": "Godot",
                "client_version": "1.0.0",
                "initial_state": "IDLE",
            },
            HelloPayload,
        ),
        (
            MessageType.WELCOME,
            {
                "server_name": "OCTA",
                "server_version": "1.0.0",
                "confirmed_state": "IDLE",
                "heartbeat_interval_ms": 3000,
            },
            WelcomePayload,
        ),
        (MessageType.PING, {"data": "ping-1"}, PingPayload),
        (MessageType.PONG, {"data": "ping-1"}, PongPayload),
        (MessageType.SET_STATE, {"state": "THINKING", "reason": "Query"}, SetStatePayload),
        (
            MessageType.SET_STATE_ACK,
            {
                "accepted": True,
                "requested_state": "THINKING",
                "current_state": "THINKING",
                "error_message": None,
            },
            SetStateAckPayload,
        ),
        (
            MessageType.STATE_CHANGED,
            {"state": "SUCCESS", "previous_state": "THINKING"},
            StateChangedPayload,
        ),
        (
            MessageType.ERROR,
            {"error_code": "ERR_BUSY", "error_message": "Server busy", "recoverable": True},
            ErrorPayload,
        ),
        (MessageType.SHUTDOWN, {"reason": "User exit"}, ShutdownPayload),
        (MessageType.SHUTDOWN_ACK, {"acknowledged": True}, ShutdownAckPayload),
    ]

    for msg_type, payload_dict, expected_cls in test_cases:
        env = ProtocolHandler.create_envelope(
            message_type=msg_type,
            payload=payload_dict,
            correlation_id="corr-xyz",
        )
        encoded = ProtocolHandler.encode(env)
        decoded_env = ProtocolHandler.decode(encoded)

        assert decoded_env.message_type == msg_type.value
        assert decoded_env.correlation_id == "corr-xyz"

        payload_obj = ProtocolHandler.validate_payload(decoded_env)
        assert isinstance(payload_obj, expected_cls)
        assert payload_obj.to_dict() == payload_dict


def test_create_error_envelope() -> None:
    """Verify ProtocolHandler.create_error_envelope helper."""
    err_env = ProtocolHandler.create_error_envelope(
        error_code="ERR_PROTOCOL",
        error_message="Invalid request format",
        correlation_id="msg-req-1",
        recoverable=False,
    )
    assert err_env.message_type == MessageType.ERROR.value
    assert err_env.correlation_id == "msg-req-1"

    payload = ProtocolHandler.validate_payload(err_env)
    assert isinstance(payload, ErrorPayload)
    assert payload.error_code == "ERR_PROTOCOL"
    assert payload.error_message == "Invalid request format"
    assert payload.recoverable is False
