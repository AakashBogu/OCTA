## main_scene.gd
## Sets up the 3D environment (camera, lights, background) and the
## state-switching UI overlay for testing.  Attached to the scene root.

extends Node3D

# ──────────────────────────────────────────────
# References
# ──────────────────────────────────────────────
var _robot: RobotCharacter
var _camera: Camera3D
var _state_label: Label
var _hint_label: Label

# ──────────────────────────────────────────────
# Scene construction
# ──────────────────────────────────────────────

func _ready() -> void:
	_build_environment()
	_build_robot()
	_build_ui()
	_update_state_label()


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

	# ── Hint label (bottom-left) ─────────────
	_hint_label = Label.new()
	_hint_label.text = "Space / Enter : cycle state  |  1-4 : select state directly"
	_hint_label.position = Vector2(20, 0)  # positioned in _process to stick to bottom
	_hint_label.add_theme_font_size_override("font_size", 14)
	_hint_label.add_theme_color_override("font_color", Color(0.6, 0.62, 0.65))
	canvas.add_child(_hint_label)


func _on_robot_state_changed(_new_state: RobotCharacter.State) -> void:
	_update_state_label()


func _update_state_label() -> void:
	if _state_label and _robot:
		_state_label.text = "State: %s" % _robot.get_state_name()


func _process(_delta: float) -> void:
	# Keep the hint label pinned near the bottom of the viewport.
	if _hint_label:
		var vp_size := get_viewport().get_visible_rect().size
		_hint_label.position.y = vp_size.y - 40
