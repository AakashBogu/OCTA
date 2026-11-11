## main_scene.gd
## Sets up the transparent, borderless 3D desktop companion window,
## lighting, mouse dragging, state-switching hotkeys, and WebSocket client for Python orchestration.

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
# Window Dragging State
# ──────────────────────────────────────────────
var _is_dragging_window: bool = false
var _drag_mouse_origin: Vector2i = Vector2i.ZERO
var _drag_window_origin: Vector2i = Vector2i.ZERO


func _ready() -> void:
	setup()


## Initializes desktop window configuration, 3D environment, robot, UI, and WebSocket client.
func setup() -> void:
	if _robot:
		return
	_configure_desktop_window()
	_build_environment()
	_build_robot()
	_build_ui()
	_build_ws_client()
	_update_state_label()
	_update_conn_label()


func _configure_desktop_window() -> void:
	# Enable viewport background transparency
	var vp: Viewport = null
	if is_inside_tree():
		vp = get_viewport()
	if vp:
		vp.transparent_bg = true
		if vp is Window:
			(vp as Window).transparent = true

	# Set window flags on DisplayServer if supported
	DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_TRANSPARENT, true)
	DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_BORDERLESS, true)
	DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_ALWAYS_ON_TOP, true)

	# Position window in the bottom-right corner of the usable screen area
	var screen_rect := DisplayServer.screen_get_usable_rect()
	var win_size := DisplayServer.window_get_size()
	if screen_rect.size.x > 0 and screen_rect.size.y > 0 and win_size.x > 0:
		var target_x: int = screen_rect.position.x + screen_rect.size.x - win_size.x - 24
		var target_y: int = screen_rect.position.y + screen_rect.size.y - win_size.y - 48
		DisplayServer.window_set_position(Vector2i(target_x, target_y))


func _input(event: InputEvent) -> void:
	# Window dragging via left mouse button
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		if mb.button_index == MOUSE_BUTTON_LEFT:
			if mb.pressed:
				_is_dragging_window = true
				_drag_mouse_origin = DisplayServer.mouse_get_position()
				_drag_window_origin = DisplayServer.window_get_position()
			else:
				_is_dragging_window = false
	elif event is InputEventMouseMotion and _is_dragging_window:
		var current_mouse := DisplayServer.mouse_get_position()
		var offset := current_mouse - _drag_mouse_origin
		DisplayServer.window_set_position(_drag_window_origin + offset)


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		var key_event := event as InputEventKey
		if key_event.keycode == KEY_SPACE or key_event.keycode == KEY_ENTER:
			_robot.cycle_state()
		elif key_event.keycode == KEY_1:
			_robot.idle()
		elif key_event.keycode == KEY_2:
			_robot.listen()
		elif key_event.keycode == KEY_3:
			_robot.think()
		elif key_event.keycode == KEY_4:
			_robot.celebrate()
		elif key_event.keycode == KEY_5:
			_robot.talk()
		elif key_event.keycode == KEY_6:
			_robot.point()
		elif key_event.keycode == KEY_7:
			_robot.celebrate()


# ══════════════════════════════════════════════
# Environment & Lighting
# ══════════════════════════════════════════════

func _build_environment() -> void:
	# ── Camera ───────────────────────────────
	_camera = Camera3D.new()
	add_child(_camera)
	_camera.look_at_from_position(Vector3(0.0, 0.72, 1.95), Vector3(0.0, 0.58, 0.0))
	_camera.fov = 38.0
	_camera.current = true

	# ── Key light (warm, directional) ────────
	var key_light := DirectionalLight3D.new()
	key_light.rotation_degrees = Vector3(-35.0, 30.0, 0.0)
	key_light.light_color = Color(1.0, 0.96, 0.90)
	key_light.light_energy = 1.15
	key_light.shadow_enabled = true
	add_child(key_light)

	# ── Fill light (cool, softer) ────────────
	var fill_light := DirectionalLight3D.new()
	fill_light.rotation_degrees = Vector3(-20.0, -55.0, 0.0)
	fill_light.light_color = Color(0.82, 0.90, 1.0)
	fill_light.light_energy = 0.55
	fill_light.shadow_enabled = false
	add_child(fill_light)

	# ── Rim light (highlights character silhouette) ──
	var rim_light := DirectionalLight3D.new()
	rim_light.rotation_degrees = Vector3(30.0, 150.0, 0.0)
	rim_light.light_color = Color(0.9, 0.95, 1.0)
	rim_light.light_energy = 0.4
	rim_light.shadow_enabled = false
	add_child(rim_light)

	# ── Transparent Environment ──────────────
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.0, 0.0, 0.0, 0.0)  # Transparent clear
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.40, 0.45, 0.55)
	env.ambient_light_energy = 0.6
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC

	var world_env := WorldEnvironment.new()
	world_env.environment = env
	add_child(world_env)


# ══════════════════════════════════════════════
# Character
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
	var clean_state := requested_state.strip_edges().to_upper()
	if OctaProtocol.is_valid_state(clean_state) or clean_state in ["TALKING", "POINTING", "CELEBRATING"]:
		var prev_state: String = _robot.get_state_name()
		_robot.set_state_by_name(clean_state)
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
	_state_label.position = Vector2(16, 12)
	_state_label.add_theme_font_size_override("font_size", 16)
	_state_label.add_theme_color_override("font_color", Color(0.92, 0.94, 0.98))
	canvas.add_child(_state_label)

	# ── Connection status label (below state) ──
	_conn_label = Label.new()
	_conn_label.position = Vector2(16, 36)
	_conn_label.add_theme_font_size_override("font_size", 12)
	_conn_label.add_theme_color_override("font_color", Color(0.68, 0.74, 0.85))
	canvas.add_child(_conn_label)

	# ── Hint label (bottom) ──────────────────
	_hint_label = Label.new()
	_hint_label.text = "1-4: States | 5: Talk | 6: Point | 7: Celebrate | Drag to move"
	_hint_label.position = Vector2(12, 0)
	_hint_label.add_theme_font_size_override("font_size", 11)
	_hint_label.add_theme_color_override("font_color", Color(0.65, 0.70, 0.78, 0.85))
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
		_state_label.text = "OCTA: %s" % _robot.get_state_name()


func _update_conn_label() -> void:
	if _conn_label and _ws_client:
		var status_text: String = _ws_client.connection_state
		var color := Color(0.68, 0.74, 0.85)
		if _ws_client.is_authenticated:
			status_text = "WS: CONNECTED"
			color = Color(0.35, 0.90, 0.45)
		elif _ws_client.connection_state == "CONNECTING":
			status_text = "WS: CONNECTING..."
			color = Color(0.95, 0.82, 0.3)
		elif _ws_client.connection_state == "ERROR":
			status_text = "WS: ERROR"
			color = Color(0.95, 0.35, 0.35)
		_conn_label.text = status_text
		_conn_label.add_theme_color_override("font_color", color)


func _process(_delta: float) -> void:
	if _hint_label:
		var vp_size := get_viewport().get_visible_rect().size
		_hint_label.position.y = vp_size.y - 28
