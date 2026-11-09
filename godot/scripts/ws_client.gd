## ws_client.gd
## WebSocket client for the OCTA companion protocol v1.
## Implemented as a scene-tree Node using Godot 4's WebSocketPeer in the normal polling loop.

class_name OctaWsClient
extends Node

const OctaProtocol = preload("res://scripts/ws_protocol.gd")

# ──────────────────────────────────────────────
# Signals
# ──────────────────────────────────────────────
signal connection_state_changed(new_state: String)
signal connected()
signal disconnected(code: int, reason: String)
signal authenticated(server_name: String, server_version: String)
signal state_change_requested(requested_state: String, reason: String, correlation_id: String)
signal shutdown_requested(reason: String, correlation_id: String)
signal error_received(error_code: String, error_message: String, recoverable: bool)
signal error_occurred(message: String)

# ──────────────────────────────────────────────
# Configuration & Properties
# ──────────────────────────────────────────────
@export var client_name: String = "OCTA Godot Client"
@export var client_version: String = "0.1.0"
@export var host: String = "127.0.0.1"
@export var port: int = 8765
@export var auto_connect_on_ready: bool = true

var auth_token: String = ""
var current_robot_state: String = "IDLE"
var connection_state: String = "DISCONNECTED":
	set(val):
		if connection_state != val:
			connection_state = val
			connection_state_changed.emit(connection_state)

var is_authenticated: bool = false
var peer: WebSocketPeer = null
var pending_hello_id: String = ""
var server_heartbeat_interval_ms: int = 5000


# ──────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────

func _ready() -> void:
	if auto_connect_on_ready:
		if load_handoff():
			connect_to_server()
		else:
			# No handoff found; remain idle without spamming errors
			pass


func _process(_delta: float) -> void:
	poll_socket()


func poll_socket() -> void:
	if peer == null:
		return

	peer.poll()
	var state := peer.get_ready_state()

	match state:
		WebSocketPeer.STATE_OPEN:
			if connection_state == "CONNECTING":
				connection_state = "CONNECTED"
				connected.emit()
				_send_hello()

			while peer != null and peer.get_ready_state() == WebSocketPeer.STATE_OPEN and peer.get_available_packet_count() > 0:
				_read_packet()

		WebSocketPeer.STATE_CLOSING:
			if connection_state != "CLOSING":
				connection_state = "CLOSING"

		WebSocketPeer.STATE_CLOSED:
			if connection_state != "CLOSED" and connection_state != "DISCONNECTED":
				var code := peer.get_close_code()
				var reason := peer.get_close_reason()
				connection_state = "CLOSED"
				is_authenticated = false
				disconnected.emit(code, reason)
				peer = null


# ──────────────────────────────────────────────
# Handoff Loading (Environment & File)
# ──────────────────────────────────────────────

func load_handoff() -> bool:
	# 1. First, check for a secure one-time handoff file
	var handoff_file := OS.get_environment("OCTA_HANDOFF_FILE")
	if not handoff_file.is_empty() and FileAccess.file_exists(handoff_file):
		var fa := FileAccess.open(handoff_file, FileAccess.READ)
		if fa != null:
			var content := fa.get_as_text()
			fa.close()
			# Immediately delete the handoff file from disk to minimize exposure
			DirAccess.remove_absolute(handoff_file)

			var json := JSON.new()
			if json.parse(content) == OK and typeof(json.data) == TYPE_DICTIONARY:
				var data: Dictionary = json.data
				if data.has("port") and data.has("auth_token"):
					port = int(data.get("port"))
					auth_token = str(data.get("auth_token"))
					if data.has("host") and not str(data.get("host")).is_empty():
						host = str(data.get("host"))
					return true

	# 2. Check process environment variables
	var env_token := OS.get_environment("OCTA_AUTH_TOKEN")
	var env_port := OS.get_environment("OCTA_WS_PORT")
	var env_host := OS.get_environment("OCTA_WS_HOST")

	if not env_token.is_empty() and not env_port.is_empty():
		auth_token = env_token
		port = env_port.to_int()
		if not env_host.is_empty():
			host = env_host
		return true

	return false


# ──────────────────────────────────────────────
# Connection Management
# ──────────────────────────────────────────────

func connect_to_server(target_host: String = "", target_port: int = -1, target_token: String = "") -> Error:
	if not target_host.is_empty():
		host = target_host
	if target_port > 0:
		port = target_port
	if not target_token.is_empty():
		auth_token = target_token

	if auth_token.is_empty():
		var err_msg := "Cannot connect without an authentication token."
		error_occurred.emit(err_msg)
		return ERR_UNAUTHORIZED

	if peer != null:
		peer.close()
		peer = null

	peer = WebSocketPeer.new()
	peer.set_inbound_buffer_size(OctaProtocol.MAX_MESSAGE_SIZE)
	peer.set_outbound_buffer_size(OctaProtocol.MAX_MESSAGE_SIZE)

	var url := "ws://%s:%d" % [host, port]
	var err := peer.connect_to_url(url)
	if err != OK:
		peer = null
		connection_state = "ERROR"
		var msg := "Failed to initiate connection to %s (error code %d)" % [url, err]
		error_occurred.emit(msg)
		return err

	connection_state = "CONNECTING"
	is_authenticated = false
	return OK


func disconnect_from_server(code: int = 1000, reason: String = "Client disconnecting") -> void:
	if peer != null:
		peer.close(code, reason)
		poll_socket()
	connection_state = "DISCONNECTED"
	is_authenticated = false


# ──────────────────────────────────────────────
# Inbound Message Processing
# ──────────────────────────────────────────────

func _read_packet() -> void:
	var packet := peer.get_packet()
	if packet.size() > OctaProtocol.MAX_MESSAGE_SIZE:
		send_error("MESSAGE_TOO_LARGE", "Message size exceeds 65536 bytes limit", null, false)
		peer.close(1008, "Message size exceeded")
		connection_state = "ERROR"
		return

	var res := OctaProtocol.decode_message(packet)
	if not res.get("ok", false):
		var err_code: String = res.get("error_code", "INVALID_MESSAGE")
		var err_msg: String = res.get("error_message", "Failed to parse message")
		send_error(err_code, err_msg, null, false)
		if err_code == "UNSUPPORTED_PROTOCOL_VERSION":
			peer.close(1002, "Unsupported protocol version")
			connection_state = "ERROR"
		return

	var envelope: Dictionary = res.get("envelope", {})
	var msg_type: String = envelope.get("message_type", "")
	var payload: Dictionary = envelope.get("payload", {})
	var msg_id: String = envelope.get("message_id", "")
	var correlation_id: Variant = envelope.get("correlation_id", null)

	if not is_authenticated:
		if msg_type == "WELCOME":
			_handle_welcome(envelope, payload, correlation_id)
		elif msg_type == "ERROR":
			_handle_error(payload)
			peer.close(1008, "Server rejected handshake")
			connection_state = "ERROR"
		else:
			send_error("HANDSHAKE_REQUIRED", "Expected WELCOME message first", msg_id, false)
			peer.close(1008, "Handshake violation")
			connection_state = "ERROR"
		return

	# Authenticated message dispatch
	match msg_type:
		"PING":
			_handle_ping(msg_id, payload)
		"SET_STATE":
			_handle_set_state(msg_id, payload)
		"SHUTDOWN":
			_handle_shutdown(msg_id, payload)
		"ERROR":
			_handle_error(payload)
		_:
			send_error("UNSUPPORTED_MESSAGE_DIRECTION", "Unexpected message type '%s' from server" % msg_type, msg_id, false)


func _handle_welcome(envelope: Dictionary, payload: Dictionary, correlation_id: Variant) -> void:
	if correlation_id == null or str(correlation_id) != pending_hello_id:
		send_error("UNMATCHED_RESPONSE", "WELCOME does not match pending HELLO correlation ID", envelope.get("message_id"), false)
		peer.close(1008, "Correlation ID mismatch")
		connection_state = "ERROR"
		return

	var server_name: String = str(payload.get("server_name", ""))
	var server_version: String = str(payload.get("server_version", ""))
	if server_name.is_empty() or server_version.is_empty():
		send_error("INVALID_PAYLOAD", "WELCOME missing required server info", envelope.get("message_id"), false)
		peer.close(1008, "Invalid WELCOME payload")
		connection_state = "ERROR"
		return

	if payload.has("heartbeat_interval_ms"):
		server_heartbeat_interval_ms = int(payload.get("heartbeat_interval_ms"))

	is_authenticated = true
	connection_state = "AUTHENTICATED"
	authenticated.emit(server_name, server_version)


func _handle_ping(msg_id: String, payload: Dictionary) -> void:
	var ping_data: String = str(payload.get("data", ""))
	var pong := OctaProtocol.create_pong(msg_id, ping_data)
	send_envelope(pong)


func _handle_set_state(msg_id: String, payload: Dictionary) -> void:
	var state_name: String = str(payload.get("state", ""))
	var reason: String = str(payload.get("reason", ""))
	state_change_requested.emit(state_name, reason, msg_id)


func _handle_shutdown(msg_id: String, payload: Dictionary) -> void:
	var reason: String = str(payload.get("reason", ""))
	shutdown_requested.emit(reason, msg_id)


func _handle_error(payload: Dictionary) -> void:
	var err_code: String = str(payload.get("error_code", ""))
	var err_msg: String = str(payload.get("error_message", ""))
	var rec: bool = bool(payload.get("recoverable", true))
	error_received.emit(err_code, err_msg, rec)


# ──────────────────────────────────────────────
# Outbound Message Sending
# ──────────────────────────────────────────────

func send_envelope(envelope: Dictionary) -> bool:
	if peer == null or peer.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return false

	var json_str := JSON.stringify(envelope)
	var utf8 := json_str.to_utf8_buffer()
	if utf8.size() > OctaProtocol.MAX_MESSAGE_SIZE:
		error_occurred.emit("Failed to encode outbound message: size exceeds limit")
		return false

	var err := peer.send_text(json_str)
	return err == OK


func _send_hello() -> bool:
	var hello := OctaProtocol.create_hello(
		auth_token,
		client_name,
		client_version,
		current_robot_state
	)
	pending_hello_id = hello.message_id
	return send_envelope(hello)


func send_set_state_ack(
	correlation_id: String,
	accepted: bool,
	requested_state: String,
	current_state: String,
	error_message: Variant = null
) -> bool:
	current_robot_state = current_state
	var ack := OctaProtocol.create_set_state_ack(
		correlation_id,
		accepted,
		requested_state,
		current_state,
		error_message
	)
	return send_envelope(ack)


func notify_state_changed(new_state: String, previous_state: String) -> bool:
	current_robot_state = new_state
	var evt := OctaProtocol.create_state_changed(new_state, previous_state)
	return send_envelope(evt)


func send_shutdown_ack(correlation_id: String, acknowledged: bool = true) -> bool:
	var ack := OctaProtocol.create_shutdown_ack(correlation_id, acknowledged)
	var sent := send_envelope(ack)
	if sent:
		poll_socket()
	return sent


func send_error(
	error_code: String,
	error_message: String,
	correlation_id: Variant = null,
	recoverable: bool = true
) -> bool:
	var err_env := OctaProtocol.create_error(
		error_code,
		error_message,
		correlation_id,
		recoverable
	)
	return send_envelope(err_env)
