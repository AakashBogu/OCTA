"""Unit and loopback integration tests for the Qt WebSocket server/session."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QTcpServer
from PySide6.QtTest import QSignalSpy
from PySide6.QtWebSockets import QWebSocket

from octa.communication.exceptions import CommunicationError
from octa.communication.messages import (
    Envelope,
    HelloPayload,
    MessageType,
    SetStateAckPayload,
    ShutdownAckPayload,
    StateChangedPayload,
)
from octa.communication.protocol import MAX_MESSAGE_SIZE, ProtocolHandler
from octa.communication.server import DEFAULT_PORT, WebSocketServer
from octa.communication.state import ConnectionState


def _connect(qtbot, server: WebSocketServer) -> QWebSocket:
    client = QWebSocket()
    with qtbot.waitSignal(client.connected, timeout=2_000):
        client.open(QUrl(server.url))
    return client


def _hello(server: WebSocketServer, *, token: str | None = None) -> Envelope:
    return ProtocolHandler.create_envelope(
        MessageType.HELLO,
        HelloPayload(
            auth_token=server.auth_token if token is None else token,
            client_name="GodotClient",
            client_version="4.7.2",
            initial_state="IDLE",
        ),
    )


def _authenticate(qtbot, server: WebSocketServer) -> tuple[QWebSocket, Envelope]:
    client = _connect(qtbot, server)
    with qtbot.waitSignal(client.textMessageReceived, timeout=2_000) as welcome_signal:
        client.sendTextMessage(ProtocolHandler.encode(_hello(server)).decode("utf-8"))
    return client, ProtocolHandler.decode(welcome_signal.args[0])


def test_default_port_is_8765_and_server_binds_only_to_loopback(qtbot) -> None:
    server = WebSocketServer()
    assert server.requested_port == DEFAULT_PORT == 8765
    assert server.host == "127.0.0.1"
    assert server.start() > 0
    assert server._server.serverAddress().toString() == "127.0.0.1"
    assert server.url == f"ws://127.0.0.1:{server.port}"
    server.stop()
    assert server.state == ConnectionState.STOPPED


def test_port_zero_asks_operating_system_for_available_port(qtbot) -> None:
    server = WebSocketServer(port=0)
    assert server.start() > 0
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_requested_port_conflict_falls_back_to_ephemeral_loopback_port(qtbot) -> None:
    reserved = QTcpServer()
    assert reserved.listen(QHostAddress("127.0.0.1"), 0)
    requested = reserved.serverPort()
    server = WebSocketServer(port=requested)
    fallback_events = []
    server.fallbackUsed.connect(
        lambda preferred, actual: fallback_events.append((preferred, actual))
    )

    actual = server.start()

    assert actual != requested
    assert server._server.serverAddress().toString() == "127.0.0.1"
    assert fallback_events == [(requested, actual)]
    server.stop()
    reserved.close()


def test_rejects_invalid_port_and_timeout_values(qtbot) -> None:
    for value in (-1, 65536, True):
        try:
            WebSocketServer(port=value)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"port {value!r} should be rejected")

    try:
        WebSocketServer(auth_timeout_ms=0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero authentication timeout should be rejected")


def test_token_is_random_rotated_and_not_in_server_repr(qtbot) -> None:
    server = WebSocketServer(port=0)
    server.start()
    token = server.auth_token
    assert len(token) >= 40
    assert token not in repr(server)
    server.stop()
    server.start()
    assert server.auth_token != token
    server.stop()


def test_authentication_sends_correlated_welcome(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client, welcome = _authenticate(qtbot, server)

    assert server.state == ConnectionState.AUTHENTICATED
    assert server.is_authenticated
    assert welcome.message_type == MessageType.WELCOME.value
    assert welcome.correlation_id is not None
    assert ProtocolHandler.validate_payload(welcome).confirmed_state == "IDLE"
    assert ProtocolHandler.validate_payload(welcome).heartbeat_interval_ms == 10_000

    client.close()
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_bad_auth_token_is_rejected_without_echoing_token(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    token = "sensitive-token-that-must-not-be-echoed"
    client = _connect(qtbot, server)
    errors = []
    server.errorOccurred.connect(errors.append)

    with qtbot.waitSignal(client.textMessageReceived, timeout=2_000) as response:
        client.sendTextMessage(ProtocolHandler.encode(_hello(server, token=token)).decode("utf-8"))

    error_envelope = ProtocolHandler.decode(response.args[0])
    assert error_envelope.message_type == MessageType.ERROR.value
    assert "AUTHENTICATION_FAILED" == error_envelope.payload["error_code"]
    assert token not in str(errors)
    qtbot.waitSignal(client.disconnected, timeout=2_000)
    server.stop()


def test_authentication_timeout_closes_unverified_peer(qtbot) -> None:
    server = WebSocketServer(port=0, auth_timeout_ms=50)
    server.start()
    client = _connect(qtbot, server)

    with qtbot.waitSignal(client.disconnected, timeout=2_000):
        pass

    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_only_one_client_is_active_at_a_time(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    first, _welcome = _authenticate(qtbot, server)
    second = QWebSocket()
    disconnect_spy = QSignalSpy(second.disconnected)
    with qtbot.waitSignal(second.connected, timeout=2_000):
        second.open(QUrl(server.url))
    qtbot.waitUntil(lambda: disconnect_spy.count() > 0, timeout=2_000)

    assert server.is_authenticated
    assert first.state() == QAbstractSocket.SocketState.ConnectedState
    first.close()
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_client_can_reconnect_with_same_server_token(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    first, _welcome = _authenticate(qtbot, server)
    token = server.auth_token

    first.close()
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    assert server.auth_token == token

    second, welcome = _authenticate(qtbot, server)
    assert welcome.message_type == MessageType.WELCOME.value
    assert server.is_authenticated
    second.close()
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_stop_closes_active_session_and_clears_token(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client, _welcome = _authenticate(qtbot, server)
    disconnect_spy = QSignalSpy(client.disconnected)

    server.stop()

    qtbot.waitUntil(lambda: disconnect_spy.count() > 0, timeout=2_000)
    qtbot.waitUntil(lambda: server.state == ConnectionState.STOPPED, timeout=2_000)
    with pytest.raises(RuntimeError):
        _ = server.auth_token


def test_ping_response_correlates_to_request_message_id(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client, _welcome = _authenticate(qtbot, server)
    ping = ProtocolHandler.create_envelope(MessageType.PING, {"data": "probe"})

    with qtbot.waitSignal(client.textMessageReceived, timeout=2_000) as response:
        client.sendTextMessage(ProtocolHandler.encode(ping).decode("utf-8"))

    pong = ProtocolHandler.decode(response.args[0])
    assert pong.message_type == MessageType.PONG.value
    assert pong.correlation_id == ping.message_id
    client.close()
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_missing_heartbeat_response_times_out(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=50)
    server.start()
    client, _welcome = _authenticate(qtbot, server)

    with qtbot.waitSignal(client.disconnected, timeout=2_000):
        pass

    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_set_state_command_and_ack_use_correlation_id(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client, _welcome = _authenticate(qtbot, server)
    with qtbot.waitSignal(client.textMessageReceived, timeout=2_000) as command_signal:
        request_id = server.set_state("THINKING", reason="processing")

    command = ProtocolHandler.decode(command_signal.args[0])
    assert command.message_type == MessageType.SET_STATE.value
    assert command.message_id == request_id

    ack = ProtocolHandler.create_envelope(
        MessageType.SET_STATE_ACK,
        SetStateAckPayload(
            accepted=True,
            requested_state="THINKING",
            current_state="THINKING",
        ),
        correlation_id=request_id,
    )
    with qtbot.waitSignal(server.stateCommandAcknowledged, timeout=2_000) as ack_signal:
        client.sendTextMessage(ProtocolHandler.encode(ack).decode("utf-8"))

    assert ack_signal.args[0].accepted is True
    assert server.robot_state == "THINKING"
    client.close()
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_state_changed_event_updates_server_state(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client, _welcome = _authenticate(qtbot, server)
    event = ProtocolHandler.create_envelope(
        MessageType.STATE_CHANGED,
        StateChangedPayload(state="SUCCESS", previous_state="IDLE"),
    )

    with qtbot.waitSignal(server.robotStateChanged, timeout=2_000):
        client.sendTextMessage(ProtocolHandler.encode(event).decode("utf-8"))

    assert server.robot_state == "SUCCESS"
    client.close()
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_shutdown_ack_must_correlate_before_session_closes(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client, _welcome = _authenticate(qtbot, server)
    with qtbot.waitSignal(client.textMessageReceived, timeout=2_000) as shutdown_signal:
        shutdown_id = server.request_shutdown("user requested exit")

    shutdown = ProtocolHandler.decode(shutdown_signal.args[0])
    assert shutdown.message_type == MessageType.SHUTDOWN.value
    assert shutdown.message_id == shutdown_id
    ack = ProtocolHandler.create_envelope(
        MessageType.SHUTDOWN_ACK,
        ShutdownAckPayload(acknowledged=True),
        correlation_id=shutdown_id,
    )
    with qtbot.waitSignal(server.shutdownAcknowledged, timeout=2_000):
        client.sendTextMessage(ProtocolHandler.encode(ack).decode("utf-8"))
    with qtbot.waitSignal(client.disconnected, timeout=2_000):
        pass
    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_shutdown_ack_timeout_closes_session(qtbot) -> None:
    server = WebSocketServer(
        port=0,
        heartbeat_interval_ms=10_000,
        shutdown_timeout_ms=50,
    )
    server.start()
    client, _welcome = _authenticate(qtbot, server)
    server.request_shutdown()

    with qtbot.waitSignal(client.disconnected, timeout=2_000):
        pass

    qtbot.waitUntil(lambda: server.state == ConnectionState.LISTENING, timeout=2_000)
    server.stop()


def test_server_rejects_server_only_message_from_client(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client, _welcome = _authenticate(qtbot, server)
    command = ProtocolHandler.create_envelope(
        MessageType.SET_STATE,
        {"state": "IDLE", "reason": ""},
    )

    with qtbot.waitSignal(client.disconnected, timeout=2_000):
        client.sendTextMessage(ProtocolHandler.encode(command).decode("utf-8"))

    server.stop()


def test_transport_rejects_message_larger_than_protocol_limit(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client = _connect(qtbot, server)

    with qtbot.waitSignal(client.disconnected, timeout=2_000):
        client.sendTextMessage("x" * (MAX_MESSAGE_SIZE + 1))

    server.stop()


def test_binary_messages_are_rejected(qtbot) -> None:
    server = WebSocketServer(port=0)
    server.start()
    client = _connect(qtbot, server)

    with qtbot.waitSignal(client.disconnected, timeout=2_000):
        client.sendBinaryMessage(b"{}")

    server.stop()


def test_second_hello_is_rejected(qtbot) -> None:
    server = WebSocketServer(port=0, heartbeat_interval_ms=10_000)
    server.start()
    client, _welcome = _authenticate(qtbot, server)

    with qtbot.waitSignal(client.disconnected, timeout=2_000):
        client.sendTextMessage(ProtocolHandler.encode(_hello(server)).decode("utf-8"))

    server.stop()


def test_state_command_requires_authenticated_client(qtbot) -> None:
    server = WebSocketServer(port=0)
    try:
        server.set_state("IDLE")
    except CommunicationError:
        pass
    else:
        raise AssertionError("SET_STATE should require an authenticated session")
    server.stop()
