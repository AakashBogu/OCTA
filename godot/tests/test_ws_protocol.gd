## test_ws_protocol.gd
## Headless test suite for Godot WebSocket protocol and client implementation.
## Run with: godot --headless --path godot -s res://tests/test_ws_protocol.gd

extends SceneTree

const OctaProtocol = preload("res://scripts/ws_protocol.gd")
const OctaWsClient = preload("res://scripts/ws_client.gd")

var _tests_run: int = 0
var _tests_passed: int = 0
var _tests_failed: int = 0


func _init() -> void:
	print("========================================")
	print("OCTA Godot WebSocket Protocol Test Suite")
	print("========================================")

	_run_test("test_semver_compatibility", test_semver_compatibility)
	_run_test("test_valid_robot_states", test_valid_robot_states)
	_run_test("test_valid_message_types", test_valid_message_types)
	_run_test("test_generate_message_id", test_generate_message_id)
	_run_test("test_get_iso_timestamp", test_get_iso_timestamp)
	_run_test("test_create_envelopes", test_create_envelopes)
	_run_test("test_encode_and_decode_roundtrip", test_encode_and_decode_roundtrip)
	_run_test("test_message_size_limit", test_message_size_limit)
	_run_test("test_decode_malformed_json", test_decode_malformed_json)
	_run_test("test_decode_missing_fields", test_decode_missing_fields)
	_run_test("test_decode_incompatible_version", test_decode_incompatible_version)
	_run_test("test_client_handoff_environment", test_client_handoff_environment)
	_run_test("test_client_handoff_file", test_client_handoff_file)

	print("========================================")
	print("Results: %d passed, %d failed (total: %d)" % [_tests_passed, _tests_failed, _tests_run])
	print("========================================")

	if _tests_failed > 0:
		quit(1)
	else:
		quit(0)


func _run_test(test_name: String, test_callable: Callable) -> void:
	_tests_run += 1
	var result: Variant = test_callable.call()
	if result == true or result == null:
		_tests_passed += 1
		print("[PASS] %s" % test_name)
	else:
		_tests_failed += 1
		printerr("[FAIL] %s: %s" % [test_name, result])


# ──────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────

func test_semver_compatibility() -> Variant:
	assert(OctaProtocol.is_version_compatible("1.0.0"), "1.0.0 should be compatible")
	assert(OctaProtocol.is_version_compatible("1.5.2"), "1.5.2 should be compatible")
	assert(OctaProtocol.is_version_compatible("1.0.0-alpha.1"), "1.0.0-alpha.1 should be compatible")
	assert(not OctaProtocol.is_version_compatible("2.0.0"), "2.0.0 must be incompatible")
	assert(not OctaProtocol.is_version_compatible("0.9.0"), "0.9.0 must be incompatible")
	assert(not OctaProtocol.is_version_compatible("invalid"), "invalid string must be incompatible")
	assert(not OctaProtocol.is_version_compatible(""), "empty string must be incompatible")
	return true


func test_valid_robot_states() -> Variant:
	for state in ["IDLE", "LISTENING", "THINKING", "SUCCESS"]:
		assert(OctaProtocol.is_valid_state(state), "%s should be valid" % state)
	assert(not OctaProtocol.is_valid_state("RUNNING"), "RUNNING should be invalid")
	assert(not OctaProtocol.is_valid_state(""), "empty should be invalid")
	assert(not OctaProtocol.is_valid_state("idle"), "lowercase should be invalid")
	return true


func test_valid_message_types() -> Variant:
	for m in ["HELLO", "WELCOME", "PING", "PONG", "SET_STATE", "SET_STATE_ACK", "STATE_CHANGED", "ERROR", "SHUTDOWN", "SHUTDOWN_ACK"]:
		assert(OctaProtocol.is_valid_message_type(m), "%s should be valid" % m)
	assert(not OctaProtocol.is_valid_message_type("UNKNOWN"), "UNKNOWN should be invalid")
	return true


func test_generate_message_id() -> Variant:
	var id1 := OctaProtocol.generate_message_id()
	var id2 := OctaProtocol.generate_message_id()
	assert(id1.length() == 32, "message_id should be 32 chars hex, got %d" % id1.length())
	assert(id2.length() == 32, "message_id should be 32 chars hex")
	assert(id1 != id2, "Generated IDs must be unique")
	return true


func test_get_iso_timestamp() -> Variant:
	var ts := OctaProtocol.get_iso_timestamp()
	assert(ts.ends_with("Z"), "Timestamp must end with Z")
	assert(ts.contains("T"), "Timestamp must contain T separator")
	return true


func test_create_envelopes() -> Variant:
	var hello := OctaProtocol.create_hello("tok123", "TestClient", "0.1.0", "IDLE")
	assert(hello["message_type"] == "HELLO")
	assert(hello["protocol_version"] == "1.0.0")
	assert(hello["payload"]["auth_token"] == "tok123")
	assert(hello["payload"]["client_name"] == "TestClient")

	var pong := OctaProtocol.create_pong("corr_1", "pong_data")
	assert(pong["message_type"] == "PONG")
	assert(pong["correlation_id"] == "corr_1")
	assert(pong["payload"]["data"] == "pong_data")

	var ack := OctaProtocol.create_set_state_ack("corr_2", true, "THINKING", "THINKING")
	assert(ack["message_type"] == "SET_STATE_ACK")
	assert(ack["payload"]["accepted"] == true)
	assert(ack["payload"]["error_message"] == null)

	var changed := OctaProtocol.create_state_changed("THINKING", "IDLE")
	assert(changed["message_type"] == "STATE_CHANGED")
	assert(changed["payload"]["state"] == "THINKING")
	assert(changed["payload"]["previous_state"] == "IDLE")

	var shut_ack := OctaProtocol.create_shutdown_ack("corr_3", true)
	assert(shut_ack["message_type"] == "SHUTDOWN_ACK")
	assert(shut_ack["payload"]["acknowledged"] == true)

	var err := OctaProtocol.create_error("ERR_CODE", "Message", "corr_4", false)
	assert(err["message_type"] == "ERROR")
	assert(err["payload"]["error_code"] == "ERR_CODE")
	assert(err["payload"]["recoverable"] == false)
	return true


func test_encode_and_decode_roundtrip() -> Variant:
	var env := OctaProtocol.create_hello("tok_roundtrip", "Client", "1.0.0", "IDLE")
	var encoded := OctaProtocol.encode_envelope(env)
	assert(not encoded.is_empty(), "Encoded bytes must not be empty")

	var dec := OctaProtocol.decode_message(encoded)
	assert(dec["ok"] == true, "Decode must succeed")
	var decoded_env: Dictionary = dec["envelope"]
	assert(decoded_env["message_type"] == "HELLO")
	assert(decoded_env["payload"]["auth_token"] == "tok_roundtrip")
	assert(decoded_env["message_id"] == env["message_id"])
	return true


func test_message_size_limit() -> Variant:
	# Create a message exceeding 65536 bytes
	var huge_data := ""
	for i in range(66000):
		huge_data += "A"
	var big_bytes := huge_data.to_utf8_buffer()
	var dec := OctaProtocol.decode_message(big_bytes)
	assert(dec["ok"] == false, "Must reject > 65536 bytes")
	assert(dec["error_code"] == "MESSAGE_TOO_LARGE", "Error code must be MESSAGE_TOO_LARGE")
	return true


func test_decode_malformed_json() -> Variant:
	var bad_json := "{not valid json}".to_utf8_buffer()
	var dec := OctaProtocol.decode_message(bad_json)
	assert(dec["ok"] == false)
	assert(dec["error_code"] == "INVALID_MESSAGE")
	return true


func test_decode_missing_fields() -> Variant:
	var incomplete := JSON.stringify({"message_type": "HELLO"}).to_utf8_buffer()
	var dec := OctaProtocol.decode_message(incomplete)
	assert(dec["ok"] == false)
	assert(dec["error_code"] == "INVALID_MESSAGE")
	return true


func test_decode_incompatible_version() -> Variant:
	var env := OctaProtocol.create_hello("tok", "C", "1.0", "IDLE")
	env["protocol_version"] = "2.0.0"
	var raw := JSON.stringify(env).to_utf8_buffer()
	var dec := OctaProtocol.decode_message(raw)
	assert(dec["ok"] == false)
	assert(dec["error_code"] == "UNSUPPORTED_PROTOCOL_VERSION")
	return true


func test_client_handoff_environment() -> Variant:
	var client := OctaWsClient.new()
	client.auto_connect_on_ready = false

	# Test with no environment set
	OS.set_environment("OCTA_WS_PORT", "")
	OS.set_environment("OCTA_AUTH_TOKEN", "")
	OS.set_environment("OCTA_HANDOFF_FILE", "")
	assert(not client.load_handoff(), "load_handoff should return false when unset")

	# Test with environment variables set
	OS.set_environment("OCTA_WS_PORT", "9876")
	OS.set_environment("OCTA_AUTH_TOKEN", "env_tok_abc")
	OS.set_environment("OCTA_WS_HOST", "127.0.0.1")
	assert(client.load_handoff(), "load_handoff should return true when set")
	assert(client.port == 9876, "Port should be 9876")
	assert(client.auth_token == "env_tok_abc", "Token should match")
	assert(client.host == "127.0.0.1", "Host should be 127.0.0.1")

	# Clean up environment
	OS.set_environment("OCTA_WS_PORT", "")
	OS.set_environment("OCTA_AUTH_TOKEN", "")
	OS.set_environment("OCTA_WS_HOST", "")
	client.free()
	return true


func test_client_handoff_file() -> Variant:
	var client := OctaWsClient.new()
	client.auto_connect_on_ready = false

	# Create a temporary handoff file
	var temp_path := "user://test_handoff_temp.json"
	var fa := FileAccess.open(temp_path, FileAccess.WRITE)
	assert(fa != null, "Could not open temp file")
	fa.store_string(JSON.stringify({
		"host": "127.0.0.1",
		"port": 5432,
		"auth_token": "file_tok_xyz"
	}))
	fa.close()

	var abs_path := ProjectSettings.globalize_path(temp_path)
	OS.set_environment("OCTA_HANDOFF_FILE", abs_path)

	assert(client.load_handoff(), "load_handoff should succeed with file")
	assert(client.port == 5432, "Port should be 5432")
	assert(client.auth_token == "file_tok_xyz", "Token should match")
	# Verify file was deleted
	assert(not FileAccess.file_exists(temp_path), "Handoff file must be deleted after load")

	OS.set_environment("OCTA_HANDOFF_FILE", "")
	client.free()
	return true
