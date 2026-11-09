## main_scene.gd
## Sets up the 3D environment (camera, lights, background), the
## state-switching UI overlay, and the WebSocket client for Python orchestration.

extends Node3D

const OctaProtocol = preload("res://scripts/ws_protocol.gd")
const OctaWsClient = preload("res://scripts/ws_client.gd")

# ──────────────────────────────────────────────
# References
# ──────────────────────────────────────────────
var _robot: RobotCharacter
var _camera: Camera3D
var _state_label: Label
var _conn_label: Label
var _hint_label: Label
var _ws_client: OctaWsClient

# ──────────────────────────────────────────────
# Scene construction
# ──────────────────────────────────────────────

func _ready() -> void:
	_build_environment()
	_build_robot()
	_build_ui()
	_build_ws_client()
	_update_state_label()
	_update_conn_label()


func _unhandled_input(event: InputEvent) -> void:
	# Press Space or Enter to cycle through states.
	if event is InputEventKey and event.pressed and not event.echo:
		var key_event := event as InputEventKey
		if key_event.keycode == KEY_SPACE or key_event.keycode == KEY_ENTER:
			_robot.cycle_state()
		# Also allow 1-4 keys for direct state access.
		elif key_event.keycode == KEY_1:
			_robot.current_state = RobotCharacter.State.IDLE
		elif key_event.keycode == KEY_2:
			_robot.current_state = RobotCharacter.State.LISTENING
		elif key_event.keycode == KEY_3:
			_robot.current_state = RobotCharacter.State.THINKING
		elif key_event.keycode == KEY_4:
			_robot.current_state = RobotCharacter.State.SUCCESS


# ══════════════════════════════════════════════
# Environment
# ══════════════════════════════════════════════

func _build_environment() -> void:
	# ── Camera ───────────────────────────────
	_camera = Camera3D.new()
	add_child(_camera)
	_camera.position = Vector3(0.0, 0.8, 2.2)
	_camera.look_at(Vector3(0.0, 0.65, 0.0))
	_camera.fov = 40.0
	_camera.current = true

	# ── Key light (warm) ─────────────────────
	var key_light := DirectionalLight3D.new()
	key_light.rotation_degrees = Vector3(-40.0, 30.0, 0.0)
	key_light.light_color = Color(1.0, 0.96, 0.90)    # warm white
	key_light.light_energy = 1.0
	key_light.shadow_enabled = true
	add_child(key_light)

	# ── Fill light (cool, softer) ────────────
	var fill_light := DirectionalLight3D.new()
	fill_light.rotation_degrees = Vector3(-20.0, -60.0, 0.0)
	fill_light.light_color = Color(0.85, 0.90, 1.0)   # cool blue-white
	fill_light.light_energy = 0.4
	fill_light.shadow_enabled = false
	add_child(fill_light)

	# ── Ambient / sky ────────────────────────
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.18, 0.20, 0.28)    # dark blue-grey
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.30, 0.32, 0.40)
	env.ambient_light_energy = 0.5
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC

	var world_env := WorldEnvironment.new()
	world_env.environment = env
	add_child(world_env)

	# ── Floor (subtle, so the robot doesn't float in void) ──
	var floor_mesh := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(6.0, 6.0)
	floor_mesh.mesh = plane
	var floor_mat := StandardMaterial3D.new()
	floor_mat.albedo_color = Color(0.15, 0.17, 0.22)
	floor_mat.roughness = 0.9
	floor_mesh.material_override = floor_mat
	floor_mesh.position = Vector3(0.0, 0.0, 0.0)
	add_child(floor_mesh)


# ══════════════════════════════════════════════
# Robot
# ══════════════════════════════════════════════

func _build_robot() -> void:
	_robot = RobotCharacter.new()
	_robot.position = Vector3.ZERO
	_robot.state_changed.connect(_on_robot_state_changed)
	add_child(_robot)


# ══════════════════════════════════════════════
# WebSocket Client
# ══════════════════════════════════════════════

func _build_ws_client() -> void:
	_ws_client = OctaWsClient.new()
	_ws_client.name = "WsClient"
	_ws_client.current_robot_state = _robot.get_state_name()
	_ws_client.state_change_requested.connect(_on_ws_state_change_requested)
	_ws_client.shutdown_requested.connect(_on_ws_shutdown_requested)
	_ws_client.authenticated.connect(_on_ws_authenticated)
	_ws_client.connection_state_changed.connect(_on_ws_connection_state_changed)
	_ws_client.error_received.connect(_on_ws_error_received)
	add_child(_ws_client)


func _on_ws_state_change_requested(requested_state: String, _reason: String, correlation_id: String) -> void:
	if OctaProtocol.is_valid_state(requested_state):
		var prev_state: String = _robot.get_state_name()
		_robot.set_state_by_name(requested_state)
		var curr_state: String = _robot.get_state_name()
		_ws_client.send_set_state_ack(correlation_id, true, requested_state, curr_state)
		_ws_client.notify_state_changed(curr_state, prev_state)
	else:
		_ws_client.send_set_state_ack(
			correlation_id,
			false,
			requested_state,
			_robot.get_state_name(),
			"Unrecognized robot state: %s" % requested_state
		)


func _on_ws_shutdown_requested(_reason: String, correlation_id: String) -> void:
	_ws_client.send_shutdown_ack(correlation_id, true)
	_ws_client.poll_socket()
	# Schedule clean process exit after socket flush
	get_tree().create_timer(0.05).timeout.connect(func(): get_tree().quit(0))


func _on_ws_authenticated(_server_name: String, _server_version: String) -> void:
	_update_conn_label()


func _on_ws_connection_state_changed(_new_state: String) -> void:
	_update_conn_label()


func _on_ws_error_received(_error_code: String, _error_message: String, _recoverable: bool) -> void:
	_update_conn_label()


# ══════════════════════════════════════════════
# UI Overlay (CanvasLayer)
# ══════════════════════════════════════════════

func _build_ui() -> void:
	var canvas := CanvasLayer.new()
	canvas.layer = 10
	add_child(canvas)

	# ── State label (top-left) ───────────────
	_state_label = Label.new()
	_state_label.position = Vector2(20, 16)
	_state_label.add_theme_font_size_override("font_size", 22)
	_state_label.add_theme_color_override("font_color", Color(0.9, 0.92, 0.95))
	canvas.add_child(_state_label)

	# ── Connection status label (below state) ──
	_conn_label = Label.new()
	_conn_label.position = Vector2(20, 48)
	_conn_label.add_theme_font_size_override("font_size", 14)
	_conn_label.add_theme_color_override("font_color", Color(0.7, 0.75, 0.85))
	canvas.add_child(_conn_label)

	# ── Hint label (bottom-left) ─────────────
	_hint_label = Label.new()
	_hint_label.text = "Space / Enter : cycle state  |  1-4 : select state directly"
	_hint_label.position = Vector2(20, 0)  # positioned in _process to stick to bottom
	_hint_label.add_theme_font_size_override("font_size", 14)
	_hint_label.add_theme_color_override("font_color", Color(0.6, 0.62, 0.65))
	canvas.add_child(_hint_label)


func _on_robot_state_changed(_new_state: RobotCharacter.State) -> void:
	_update_state_label()
	if _ws_client and _ws_client.is_authenticated:
		var current_name: String = _robot.get_state_name()
		if current_name != _ws_client.current_robot_state:
			var prev_name: String = _ws_client.current_robot_state
			_ws_client.notify_state_changed(current_name, prev_name)


func _update_state_label() -> void:
	if _state_label and _robot:
		_state_label.text = "State: %s" % _robot.get_state_name()


func _update_conn_label() -> void:
	if _conn_label and _ws_client:
		var status_text: String = _ws_client.connection_state
		var color := Color(0.7, 0.75, 0.85)
		if _ws_client.is_authenticated:
			status_text = "CONNECTED (Authenticated)"
			color = Color(0.3, 0.85, 0.4)
		elif _ws_client.connection_state == "CONNECTING":
			status_text = "CONNECTING..."
			color = Color(0.9, 0.8, 0.3)
		elif _ws_client.connection_state == "ERROR":
			status_text = "ERROR"
			color = Color(0.95, 0.3, 0.3)
		_conn_label.text = "WS: %s" % status_text
		_conn_label.add_theme_color_override("font_color", color)


func _process(_delta: float) -> void:
	# Keep the hint label pinned near the bottom of the viewport.
	if _hint_label:
		var vp_size := get_viewport().get_visible_rect().size
		_hint_label.position.y = vp_size.y - 40
