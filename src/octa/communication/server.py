"""Qt event-loop WebSocket server for the authenticated OCTA protocol v1 bridge."""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketProtocol, QWebSocketServer

from octa import __version__
from octa.communication.exceptions import (
    CommunicationError,
    ProtocolError,
)
from octa.communication.messages import (
    Envelope,
    HelloPayload,
    MessageType,
    PingPayload,
    PongPayload,
    SetStateAckPayload,
    SetStatePayload,
    ShutdownAckPayload,
    ShutdownPayload,
    StateChangedPayload,
)
from octa.communication.protocol import MAX_MESSAGE_SIZE, ProtocolHandler
from octa.communication.state import ConnectionState

DEFAULT_PORT = 8765
DEFAULT_HANDSHAKE_TIMEOUT_MS = 5_000
DEFAULT_AUTH_TIMEOUT_MS = 5_000
DEFAULT_HEARTBEAT_INTERVAL_MS = 5_000
DEFAULT_SHUTDOWN_TIMEOUT_MS = 5_000
SERVER_NAME = "OCTA Python Server"
_LOOPBACK_ADDRESS = QHostAddress("127.0.0.1")


class WebSocketSession(QObject):
    """Own one accepted socket and enforce the protocol handshake and timeouts."""

    stateChanged = Signal(object)
    authenticated = Signal(object)
    messageReceived = Signal(object)
    errorOccurred = Signal(str)
    timeoutOccurred = Signal(str)
    closed = Signal()

    def __init__(
        self,
        socket: QWebSocket,
        auth_token: str,
        *,
        auth_timeout_ms: int = DEFAULT_AUTH_TIMEOUT_MS,
        heartbeat_interval_ms: int = DEFAULT_HEARTBEAT_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._socket = socket
        socket.setParent(self)
        socket.setMaxAllowedIncomingFrameSize(MAX_MESSAGE_SIZE)
        socket.setMaxAllowedIncomingMessageSize(MAX_MESSAGE_SIZE)

        self._expected_token = auth_token
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._state = ConnectionState.CONNECTED
        self._authenticated = False
        self._pending_ping_id: str | None = None
        self._closed_emitted = False

        self._auth_timer = QTimer(self)
        self._auth_timer.setSingleShot(True)
        self._auth_timer.timeout.connect(self._on_auth_timeout)
        self._auth_timer.start(auth_timeout_ms)

        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(heartbeat_interval_ms)
        self._heartbeat_timer.timeout.connect(self._on_heartbeat)

        socket.textMessageReceived.connect(self._on_text_message)
        socket.binaryMessageReceived.connect(self._on_binary_message)
        socket.disconnected.connect(self._on_disconnected)
        socket.errorOccurred.connect(self._on_socket_error)

    @property
    def state(self) -> ConnectionState:
        """Current state of this client session."""
        return self._state

    @property
    def is_authenticated(self) -> bool:
        """Whether this session completed its HELLO authentication."""
        return self._authenticated

    def send_envelope(self, envelope: Envelope) -> bool:
        """Encode and send one protocol envelope without logging its contents."""
        if self._socket.state() != QAbstractSocket.SocketState.ConnectedState:
            return False
        try:
            text = ProtocolHandler.encode(envelope).decode("utf-8")
        except ProtocolError:
            self.errorOccurred.emit("Could not encode an outbound protocol message.")
            return False
        return self._socket.sendTextMessage(text) >= 0

    def begin_shutdown(self) -> None:
        """Pause heartbeats while the peer acknowledges a shutdown request."""
        self._heartbeat_timer.stop()
        self._pending_ping_id = None
        self._set_state(ConnectionState.CLOSING)

    def resume_after_shutdown_rejection(self) -> None:
        """Return to the authenticated state when the peer rejects shutdown."""
        if (
            self._authenticated
            and self._socket.state() == QAbstractSocket.SocketState.ConnectedState
        ):
            self._set_state(ConnectionState.AUTHENTICATED)
            self._heartbeat_timer.start()

    def close(
        self,
        code: QWebSocketProtocol.CloseCode = QWebSocketProtocol.CloseCodeNormal,
        reason: str = "",
    ) -> None:
        """Close this session gracefully."""
        self._auth_timer.stop()
        self._heartbeat_timer.stop()
        if self._state != ConnectionState.ERROR:
            self._set_state(ConnectionState.CLOSING)
        if self._socket.state() == QAbstractSocket.SocketState.ConnectedState:
            self._socket.close(code, reason[:123])

    def fail_protocol(
        self,
        error_code: str,
        message: str,
        correlation_id: str | None = None,
    ) -> None:
        """Send a generic protocol error and close without echoing untrusted data."""
        response = ProtocolHandler.create_error_envelope(
            error_code,
            message,
            correlation_id=correlation_id,
            recoverable=False,
        )
        self.send_envelope(response)
        self.errorOccurred.emit(message)
        self.close(QWebSocketProtocol.CloseCodePolicyViolated, "Protocol error")

    def _set_state(self, state: ConnectionState) -> None:
        if self._state != state:
            self._state = state
            self.stateChanged.emit(state)

    def _on_auth_timeout(self) -> None:
        if self._authenticated:
            return
        message = "Client authentication timed out."
        self.timeoutOccurred.emit(message)
        self.errorOccurred.emit(message)
        self.close(QWebSocketProtocol.CloseCodePolicyViolated, "Authentication timeout")

    def _on_heartbeat(self) -> None:
        if not self._authenticated or self._state == ConnectionState.CLOSING:
            return
        if self._pending_ping_id is not None:
            message = "Client heartbeat timed out."
            self.timeoutOccurred.emit(message)
            self.errorOccurred.emit(message)
            self.close(QWebSocketProtocol.CloseCodePolicyViolated, "Heartbeat timeout")
            return

        ping = ProtocolHandler.create_envelope(MessageType.PING, PingPayload())
        if self.send_envelope(ping):
            self._pending_ping_id = ping.message_id

    def _on_text_message(self, text: str) -> None:
        try:
            envelope = ProtocolHandler.decode(text)
        except ProtocolError:
            self.fail_protocol("INVALID_MESSAGE", "Malformed or unsupported protocol message.")
            return

        if not self._authenticated:
            self._handle_hello(envelope)
            return

        try:
            payload = ProtocolHandler.validate_payload(envelope)
        except ProtocolError:
            self.fail_protocol(
                "INVALID_PAYLOAD",
                "Protocol message payload is invalid.",
                correlation_id=envelope.message_id,
            )
            return

        if envelope.message_type == MessageType.PING.value:
            pong = ProtocolHandler.create_envelope(
                MessageType.PONG,
                PongPayload(data=payload.data),
                correlation_id=envelope.message_id,
            )
            self.send_envelope(pong)
            return

        if envelope.message_type == MessageType.PONG.value:
            if envelope.correlation_id != self._pending_ping_id or self._pending_ping_id is None:
                self.fail_protocol(
                    "UNEXPECTED_PONG",
                    "Heartbeat response does not match an outstanding PING.",
                    correlation_id=envelope.message_id,
                )
                return
            self._pending_ping_id = None
            return

        if envelope.message_type == MessageType.HELLO.value:
            self.fail_protocol(
                "DUPLICATE_HELLO",
                "HELLO may only be sent once per connection.",
                correlation_id=envelope.message_id,
            )
            return

        self.messageReceived.emit(envelope)

    def _handle_hello(self, envelope: Envelope) -> None:
        if envelope.message_type != MessageType.HELLO.value:
            self.fail_protocol(
                "HELLO_REQUIRED",
                "The first protocol message must be HELLO.",
                correlation_id=envelope.message_id,
            )
            return
        try:
            payload = ProtocolHandler.validate_payload(envelope)
        except ProtocolError:
            self.fail_protocol(
                "INVALID_HELLO",
                "The HELLO message is invalid.",
                correlation_id=envelope.message_id,
            )
            return
        assert isinstance(payload, HelloPayload)

        if not hmac.compare_digest(payload.auth_token, self._expected_token):
            self.fail_protocol(
                "AUTHENTICATION_FAILED",
                "Handshake authentication failed.",
                correlation_id=envelope.message_id,
            )
            return

        self._authenticated = True
        self._auth_timer.stop()
        self._set_state(ConnectionState.AUTHENTICATED)
        welcome = ProtocolHandler.create_envelope(
            MessageType.WELCOME,
            {
                "server_name": SERVER_NAME,
                "server_version": __version__,
                "confirmed_state": payload.initial_state,
                "heartbeat_interval_ms": self._heartbeat_interval_ms,
            },
            correlation_id=envelope.message_id,
        )
        self.send_envelope(welcome)
        self._heartbeat_timer.start()
        self.authenticated.emit(payload)

    def _on_binary_message(self, _data: Any) -> None:
        self.fail_protocol("TEXT_REQUIRED", "Only UTF-8 JSON text messages are supported.")

    def _on_socket_error(self, _error: QAbstractSocket.SocketError) -> None:
        if self._socket.state() != QAbstractSocket.SocketState.ConnectedState:
            return
        self.errorOccurred.emit("WebSocket transport error.")

    def _on_disconnected(self) -> None:
        self._auth_timer.stop()
        self._heartbeat_timer.stop()
        if self._state not in (ConnectionState.ERROR, ConnectionState.STOPPED):
            self._set_state(ConnectionState.STOPPED)
        if not self._closed_emitted:
            self._closed_emitted = True
            self.closed.emit()


class WebSocketServer(QObject):
    """Single-client, loopback-only WebSocket server using the Qt event loop."""

    stateChanged = Signal(object)
    listening = Signal(str, int)
    fallbackUsed = Signal(int, int)
    clientAuthenticated = Signal(str, str)
    clientDisconnected = Signal()
    timeoutOccurred = Signal(str)
    robotStateChanged = Signal(str, str)
    stateCommandAcknowledged = Signal(object)
    shutdownAcknowledged = Signal(object)
    clientErrorReceived = Signal(object)
    errorOccurred = Signal(str)

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        *,
        handshake_timeout_ms: int = DEFAULT_HANDSHAKE_TIMEOUT_MS,
        auth_timeout_ms: int = DEFAULT_AUTH_TIMEOUT_MS,
        heartbeat_interval_ms: int = DEFAULT_HEARTBEAT_INTERVAL_MS,
        shutdown_timeout_ms: int = DEFAULT_SHUTDOWN_TIMEOUT_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._validate_int("port", port, minimum=0, maximum=65535)
        self._validate_int("handshake_timeout_ms", handshake_timeout_ms, minimum=1)
        self._validate_int("auth_timeout_ms", auth_timeout_ms, minimum=1)
        self._validate_int("heartbeat_interval_ms", heartbeat_interval_ms, minimum=1)
        self._validate_int("shutdown_timeout_ms", shutdown_timeout_ms, minimum=1)

        self._requested_port = port
        self._auth_timeout_ms = auth_timeout_ms
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._shutdown_timeout_ms = shutdown_timeout_ms
        self._state = ConnectionState.STOPPED
        self._auth_token: str | None = None
        self._session: WebSocketSession | None = None
        self._stop_requested = False
        self._robot_state = "IDLE"
        self._pending_state_changes: dict[str, str] = {}
        self._pending_shutdown_id: str | None = None

        self._server = QWebSocketServer(
            SERVER_NAME,
            QWebSocketServer.SslMode.NonSecureMode,
            self,
        )
        self._server.setMaxPendingConnections(1)
        self._server.setHandshakeTimeout(handshake_timeout_ms)
        self._server.newConnection.connect(self._on_new_connection)

        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.setInterval(shutdown_timeout_ms)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)

    @staticmethod
    def _validate_int(
        name: str,
        value: int,
        *,
        minimum: int,
        maximum: int | None = None,
    ) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{name} must be an integer")
        if value < minimum or (maximum is not None and value > maximum):
            bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
            raise ValueError(f"{name} must be {bound}")

    @property
    def state(self) -> ConnectionState:
        """Current server/session lifecycle state."""
        return self._state

    @property
    def host(self) -> str:
        """The server binds exclusively to IPv4 loopback."""
        return "127.0.0.1"

    @property
    def port(self) -> int:
        """Actual listening port, or zero when the server is stopped."""
        return self._server.serverPort()

    @property
    def requested_port(self) -> int:
        """Configured preferred port (8765 by default; zero requests an ephemeral port)."""
        return self._requested_port

    @property
    def url(self) -> str:
        """WebSocket URL for the active loopback listener."""
        if not self._server.isListening():
            return ""
        return f"ws://127.0.0.1:{self.port}"

    @property
    def is_authenticated(self) -> bool:
        """Whether the single active session has authenticated."""
        return self._session is not None and self._session.is_authenticated

    @property
    def robot_state(self) -> str:
        """Last robot state reported by the connected Godot client."""
        return self._robot_state

    @property
    def auth_token(self) -> str:
        """Return the in-memory bearer token for a local client handoff.

        Callers must not log the token or expose it through command-line arguments.
        """
        if self._auth_token is None:
            raise RuntimeError("Start the server before retrieving its authentication token.")
        return self._auth_token

    def start(self) -> int:
        """Listen on 127.0.0.1, preferring the requested port and falling back to port 0."""
        if QCoreApplication.instance() is None:
            raise RuntimeError("A Qt application and event loop must exist before start().")
        if self.thread() != QThread.currentThread():
            raise RuntimeError("WebSocketServer must be started on its Qt object's thread.")
        if self._server.isListening():
            return self.port
        if self._session is not None:
            raise CommunicationError("The prior WebSocket session is still closing.")

        self._stop_requested = False
        self._auth_token = secrets.token_urlsafe(32)
        if not self._server.listen(_LOOPBACK_ADDRESS, self._requested_port):
            if self._requested_port == 0:
                self._set_state(ConnectionState.ERROR)
                raise CommunicationError("Could not bind the WebSocket server to 127.0.0.1.")
            self._server.close()
            if not self._server.listen(_LOOPBACK_ADDRESS, 0):
                self._set_state(ConnectionState.ERROR)
                raise CommunicationError("Could not bind the WebSocket server to 127.0.0.1.")
            self.fallbackUsed.emit(self._requested_port, self.port)

        self._set_state(ConnectionState.LISTENING)
        self.listening.emit(self.host, self.port)
        return self.port

    def stop(self) -> None:
        """Stop accepting connections and gracefully close the active session."""
        self._stop_requested = True
        self._shutdown_timer.stop()
        self._pending_shutdown_id = None
        self._pending_state_changes.clear()
        self._server.close()
        if self._session is None:
            self._auth_token = None
            self._set_state(ConnectionState.STOPPED)
            return
        self._set_state(ConnectionState.CLOSING)
        self._session.close(QWebSocketProtocol.CloseCodeGoingAway, "Server stopping")

    def set_state(self, state: str, reason: str = "") -> str:
        """Send SET_STATE to the authenticated client and return its message ID."""
        if not self.is_authenticated or self._state != ConnectionState.AUTHENTICATED:
            raise CommunicationError("An authenticated client is required to send SET_STATE.")
        if not isinstance(state, str) or state not in {"IDLE", "LISTENING", "THINKING", "SUCCESS"}:
            raise ValueError("state must be a recognized robot state")
        if not isinstance(reason, str):
            raise TypeError("reason must be a string")

        envelope = ProtocolHandler.create_envelope(
            MessageType.SET_STATE,
            SetStatePayload(state=state, reason=reason),
        )
        self._pending_state_changes[envelope.message_id] = state
        if not self._session or not self._session.send_envelope(envelope):
            self._pending_state_changes.pop(envelope.message_id, None)
            raise CommunicationError("Could not send SET_STATE to the client.")
        return envelope.message_id

    def request_shutdown(self, reason: str = "") -> str:
        """Send SHUTDOWN and wait for a matching SHUTDOWN_ACK before closing the session."""
        if not self.is_authenticated or self._state != ConnectionState.AUTHENTICATED:
            raise CommunicationError("An authenticated client is required to request shutdown.")
        if not isinstance(reason, str):
            raise TypeError("reason must be a string")

        envelope = ProtocolHandler.create_envelope(
            MessageType.SHUTDOWN,
            ShutdownPayload(reason=reason),
        )
        if not self._session:
            raise CommunicationError("The client session is no longer available.")
        self._pending_shutdown_id = envelope.message_id
        self._session.begin_shutdown()
        if not self._session.send_envelope(envelope):
            self._pending_shutdown_id = None
            self._session.resume_after_shutdown_rejection()
            raise CommunicationError("Could not send SHUTDOWN to the client.")
        self._shutdown_timer.start()
        return envelope.message_id

    def _set_state(self, state: ConnectionState) -> None:
        if self._state != state:
            self._state = state
            self.stateChanged.emit(state)

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            if self._session is not None or self._stop_requested:
                socket.close(
                    QWebSocketProtocol.CloseCodePolicyViolated,
                    "Only one client is supported.",
                )
                socket.deleteLater()
                continue

            self._session = WebSocketSession(
                socket,
                self.auth_token,
                auth_timeout_ms=self._auth_timeout_ms,
                heartbeat_interval_ms=self._heartbeat_interval_ms,
                parent=self,
            )
            self._session.stateChanged.connect(self._on_session_state_changed)
            self._session.authenticated.connect(self._on_session_authenticated)
            self._session.messageReceived.connect(self._on_client_message)
            self._session.errorOccurred.connect(self.errorOccurred)
            self._session.timeoutOccurred.connect(self.timeoutOccurred)
            self._session.closed.connect(self._on_session_closed)
            self._set_state(ConnectionState.CONNECTED)

    def _on_session_state_changed(self, state: ConnectionState) -> None:
        if self._session is not None and state != ConnectionState.STOPPED:
            self._set_state(state)

    def _on_session_authenticated(self, payload: HelloPayload) -> None:
        previous_state = self._robot_state
        self._robot_state = payload.initial_state
        if previous_state != self._robot_state:
            self.robotStateChanged.emit(previous_state, self._robot_state)
        self.clientAuthenticated.emit(payload.client_name, payload.client_version)

    def _on_client_message(self, envelope: Envelope) -> None:
        if self._session is None:
            return
        message_type = envelope.message_type

        if message_type == MessageType.STATE_CHANGED.value:
            payload = ProtocolHandler.validate_payload(envelope)
            assert isinstance(payload, StateChangedPayload)
            previous = self._robot_state
            self._robot_state = payload.state
            if previous != payload.state:
                self.robotStateChanged.emit(previous, payload.state)
            return

        if message_type == MessageType.SET_STATE_ACK.value:
            payload = ProtocolHandler.validate_payload(envelope)
            assert isinstance(payload, SetStateAckPayload)
            request_id = envelope.correlation_id
            expected_state = self._pending_state_changes.get(request_id or "")
            if expected_state is None or expected_state != payload.requested_state:
                self._session.fail_protocol(
                    "UNMATCHED_RESPONSE",
                    "SET_STATE_ACK does not match a pending SET_STATE request.",
                    correlation_id=envelope.message_id,
                )
                return
            del self._pending_state_changes[request_id]
            previous = self._robot_state
            self._robot_state = payload.current_state
            if previous != payload.current_state:
                self.robotStateChanged.emit(previous, payload.current_state)
            self.stateCommandAcknowledged.emit(payload)
            return

        if message_type == MessageType.SHUTDOWN_ACK.value:
            payload = ProtocolHandler.validate_payload(envelope)
            assert isinstance(payload, ShutdownAckPayload)
            if (
                self._pending_shutdown_id is None
                or envelope.correlation_id != self._pending_shutdown_id
            ):
                self._session.fail_protocol(
                    "UNMATCHED_RESPONSE",
                    "SHUTDOWN_ACK does not match a pending SHUTDOWN request.",
                    correlation_id=envelope.message_id,
                )
                return
            self._shutdown_timer.stop()
            self._pending_shutdown_id = None
            self.shutdownAcknowledged.emit(payload)
            if payload.acknowledged:
                self._session.close()
            else:
                self._session.resume_after_shutdown_rejection()
            return

        if message_type == MessageType.ERROR.value:
            self.clientErrorReceived.emit(ProtocolHandler.validate_payload(envelope))
            return

        self._session.fail_protocol(
            "UNSUPPORTED_MESSAGE_DIRECTION",
            "The client sent a message that is only valid from the server.",
            correlation_id=envelope.message_id,
        )

    def _on_shutdown_timeout(self) -> None:
        if self._pending_shutdown_id is None:
            return
        self._pending_shutdown_id = None
        self.errorOccurred.emit("Client shutdown acknowledgement timed out.")
        if self._session is not None:
            self._session.close(
                QWebSocketProtocol.CloseCodePolicyViolated,
                "Shutdown acknowledgement timeout",
            )

    def _on_session_closed(self) -> None:
        session = self._session
        if session is None:
            return
        self._session = None
        self._pending_state_changes.clear()
        self._pending_shutdown_id = None
        self._shutdown_timer.stop()
        session.deleteLater()
        self.clientDisconnected.emit()
        if self._stop_requested:
            self._auth_token = None
            self._set_state(ConnectionState.STOPPED)
        elif self._server.isListening():
            self._set_state(ConnectionState.LISTENING)
        else:
            self._auth_token = None
            self._set_state(ConnectionState.STOPPED)
