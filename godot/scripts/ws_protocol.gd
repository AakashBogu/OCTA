## ws_protocol.gd
## Protocol framing, SemVer validation, and JSON encoding/decoding for OCTA v1.
## Pure utility class containing protocol constants, schemas, and helper methods.

class_name OctaProtocol
extends RefCounted

const PROTOCOL_VERSION: String = "1.0.0"
const MAX_MESSAGE_SIZE: int = 65536  # 64 KB

const VALID_ROBOT_STATES: Array[String] = [
	"IDLE",
	"LISTENING",
	"THINKING",
	"SUCCESS",
]

const VALID_MESSAGE_TYPES: Array[String] = [
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
]

static var _semver_regex: RegEx = null


# ──────────────────────────────────────────────
# Version and Validation Helpers
# ──────────────────────────────────────────────

static func _get_semver_regex() -> RegEx:
	if _semver_regex == null:
		_semver_regex = RegEx.new()
		_semver_regex.compile(
			"^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-((?:0|[1-9]\\d*|\\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\\.(?:0|[1-9]\\d*|\\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\\+([0-9a-zA-Z-]+(?:\\.[0-9a-zA-Z-]+)*))?$"
		)
	return _semver_regex


static func is_version_compatible(version: String) -> bool:
	if version.is_empty():
		return false
	var regex := _get_semver_regex()
	var m := regex.search(version.strip_edges())
	if m == null:
		return false
	var major := m.get_string(1).to_int()
	return major == 1


static func is_valid_state(state: String) -> bool:
	return state in VALID_ROBOT_STATES


static func is_valid_message_type(msg_type: String) -> bool:
	return msg_type in VALID_MESSAGE_TYPES


static func generate_message_id() -> String:
	var crypto := Crypto.new()
	var bytes := crypto.generate_random_bytes(16)
	if bytes.size() == 16:
		return bytes.hex_encode().to_lower()
	# Fallback if Crypto returns empty (e.g. platform limitation)
	var rng := RandomNumberGenerator.new()
	rng.randomize()
	var fallback_bytes := PackedByteArray()
	for i in range(16):
		fallback_bytes.append(rng.randi() % 256)
	return fallback_bytes.hex_encode().to_lower()


static func get_iso_timestamp() -> String:
	var dt := Time.get_datetime_string_from_system(true, true)
	return dt.replace(" ", "T") + "Z"


# ──────────────────────────────────────────────
# Envelope Construction
# ──────────────────────────────────────────────

static func create_envelope(
	message_type: String,
	payload: Dictionary,
	correlation_id: Variant = null
) -> Dictionary:
	var envelope := {
		"protocol_version": PROTOCOL_VERSION,
		"message_id": generate_message_id(),
		"message_type": message_type,
		"timestamp": get_iso_timestamp(),
		"correlation_id": correlation_id if correlation_id != null else null,
		"payload": payload,
	}
	return envelope


static func create_hello(
	auth_token: String,
	client_name: String = "OCTA Godot Client",
	client_version: String = "0.1.0",
	initial_state: String = "IDLE"
) -> Dictionary:
	return create_envelope(
		"HELLO",
		{
			"auth_token": auth_token,
			"client_name": client_name,
			"client_version": client_version,
			"initial_state": initial_state,
		}
	)


static func create_pong(correlation_id: String, data: String = "") -> Dictionary:
	return create_envelope(
		"PONG",
		{"data": data},
		correlation_id
	)


static func create_set_state_ack(
	correlation_id: String,
	accepted: bool,
	requested_state: String,
	current_state: String,
	error_message: Variant = null
) -> Dictionary:
	var payload := {
		"accepted": accepted,
		"requested_state": requested_state,
		"current_state": current_state,
	}
	if error_message != null:
		payload["error_message"] = str(error_message)
	else:
		payload["error_message"] = null
	return create_envelope("SET_STATE_ACK", payload, correlation_id)


static func create_state_changed(state: String, previous_state: String) -> Dictionary:
	return create_envelope(
		"STATE_CHANGED",
		{
			"state": state,
			"previous_state": previous_state,
		}
	)


static func create_shutdown_ack(correlation_id: String, acknowledged: bool = true) -> Dictionary:
	return create_envelope(
		"SHUTDOWN_ACK",
		{"acknowledged": acknowledged},
		correlation_id
	)


static func create_error(
	error_code: String,
	error_message: String,
	correlation_id: Variant = null,
	recoverable: bool = true
) -> Dictionary:
	return create_envelope(
		"ERROR",
		{
			"error_code": error_code,
			"error_message": error_message,
			"recoverable": recoverable,
		},
		correlation_id
	)


# ──────────────────────────────────────────────
# Encoding & Decoding
# ──────────────────────────────────────────────

static func encode_envelope(envelope: Dictionary) -> PackedByteArray:
	var json_str := JSON.stringify(envelope)
	var utf8 := json_str.to_utf8_buffer()
	if utf8.size() > MAX_MESSAGE_SIZE:
		push_error("Encoded message size %d exceeds limit of %d bytes" % [utf8.size(), MAX_MESSAGE_SIZE])
		return PackedByteArray()
	return utf8


static func decode_message(raw_bytes: PackedByteArray) -> Dictionary:
	# Returns a dictionary with either:
	# {"ok": true, "envelope": Dictionary}
	# or {"ok": false, "error_code": String, "error_message": String}

	if raw_bytes.size() > MAX_MESSAGE_SIZE:
		return {
			"ok": false,
			"error_code": "MESSAGE_TOO_LARGE",
			"error_message": "Message size %d exceeds limit of %d bytes" % [raw_bytes.size(), MAX_MESSAGE_SIZE],
		}

	if raw_bytes.is_empty():
		return {
			"ok": false,
			"error_code": "INVALID_MESSAGE",
			"error_message": "Cannot decode empty message",
		}

	var json_str := raw_bytes.get_string_from_utf8()
	if json_str.is_empty() and not raw_bytes.is_empty():
		return {
			"ok": false,
			"error_code": "INVALID_MESSAGE",
			"error_message": "Invalid UTF-8 payload",
		}

	var json := JSON.new()
	var parse_err := json.parse(json_str)
	if parse_err != OK:
		return {
			"ok": false,
			"error_code": "INVALID_MESSAGE",
			"error_message": "Failed to parse JSON: %s (line %d)" % [json.get_error_message(), json.get_error_line()],
		}

	if typeof(json.data) != TYPE_DICTIONARY:
		return {
			"ok": false,
			"error_code": "INVALID_MESSAGE",
			"error_message": "Message root must be a JSON dictionary object",
		}

	var data: Dictionary = json.data
	for req_field in ["protocol_version", "message_id", "message_type", "timestamp", "payload"]:
		if not data.has(req_field):
			return {
				"ok": false,
				"error_code": "INVALID_MESSAGE",
				"error_message": "Missing required envelope field: %s" % req_field,
			}

	var ver: String = str(data.get("protocol_version", ""))
	if not is_version_compatible(ver):
		return {
			"ok": false,
			"error_code": "UNSUPPORTED_PROTOCOL_VERSION",
			"error_message": "Incompatible protocol version '%s'; expected major version 1" % ver,
		}

	var msg_type: String = str(data.get("message_type", ""))
	if not is_valid_message_type(msg_type):
		return {
			"ok": false,
			"error_code": "INVALID_MESSAGE",
			"error_message": "Unrecognized message type: %s" % msg_type,
		}

	if typeof(data.get("payload")) != TYPE_DICTIONARY:
		return {
			"ok": false,
			"error_code": "INVALID_MESSAGE",
			"error_message": "Envelope field 'payload' must be a dictionary",
		}

	return {
		"ok": true,
		"envelope": data,
	}
