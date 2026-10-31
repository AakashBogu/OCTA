## robot_character.gd
## Builds a friendly robot from primitive meshes and animates it.
## States: IDLE, LISTENING, THINKING, SUCCESS.
##
## This is Phase 1 placeholder geometry — not final artwork.

class_name RobotCharacter
extends Node3D

# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────
enum State { IDLE, LISTENING, THINKING, SUCCESS }

# ──────────────────────────────────────────────
# Signals
# ──────────────────────────────────────────────
signal state_changed(new_state: State)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
## How fast the idle bob cycle runs (radians / sec).
@export var idle_bob_speed: float = 1.8
## Vertical amplitude of the idle bob (meters).
@export var idle_bob_amplitude: float = 0.04
## How fast the head sways side-to-side.
@export var head_sway_speed: float = 1.2
## Maximum head sway angle (degrees).
@export var head_sway_max_deg: float = 5.0
## How quickly the character transitions between states (lerp weight / sec).
@export var state_transition_speed: float = 3.0

# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────
var current_state: State = State.IDLE:
	set(value):
		if current_state != value:
			current_state = value
			_state_time = 0.0
			state_changed.emit(current_state)

var _state_time: float = 0.0      # seconds in current state
var _elapsed: float = 0.0         # total elapsed time (for oscillators)

# ──────────────────────────────────────────────
# Node references (built in _ready)
# ──────────────────────────────────────────────
var _body: MeshInstance3D
var _head_pivot: Node3D
var _head: MeshInstance3D
var _left_eye: MeshInstance3D
var _right_eye: MeshInstance3D
var _left_arm: MeshInstance3D
var _right_arm: MeshInstance3D
var _left_leg: MeshInstance3D
var _right_leg: MeshInstance3D
var _antenna: MeshInstance3D
var _antenna_tip: MeshInstance3D

# Base y-position for the body (used by bob animation).
var _body_base_y: float = 0.0

# ──────────────────────────────────────────────
# Colours
# ──────────────────────────────────────────────
# Warm, friendly palette.
var _color_body := Color(0.35, 0.55, 0.78)       # soft blue
var _color_head := Color(0.40, 0.62, 0.85)       # lighter blue
var _color_eye_idle := Color(0.95, 0.95, 0.95)   # near-white
var _color_eye_listen := Color(0.4, 0.85, 0.5)   # soft green
var _color_eye_think := Color(0.9, 0.75, 0.2)    # amber
var _color_eye_success := Color(0.2, 0.9, 0.5)   # bright green
var _color_limb := Color(0.28, 0.45, 0.65)       # darker blue
var _color_antenna := Color(0.7, 0.7, 0.72)      # silver
var _color_antenna_tip := Color(0.95, 0.3, 0.3)  # red


func _ready() -> void:
	_build_robot()
	_body_base_y = _body.position.y


func _process(delta: float) -> void:
	if not _body:
		return
	_elapsed += delta
	_state_time += delta
	_animate_idle(delta)
	_animate_state(delta)


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

## Cycle to the next state (useful for testing).
func cycle_state() -> void:
	var next_index: int = (current_state + 1) % State.size()
	current_state = next_index as State


## Set state by name string (case-insensitive).
func set_state_by_name(state_name: String) -> void:
	match state_name.to_upper():
		"IDLE":      current_state = State.IDLE
		"LISTENING": current_state = State.LISTENING
		"THINKING":  current_state = State.THINKING
		"SUCCESS":   current_state = State.SUCCESS


## Return the current state name as a string.
func get_state_name() -> String:
	match current_state:
		State.IDLE:      return "IDLE"
		State.LISTENING: return "LISTENING"
		State.THINKING:  return "THINKING"
		State.SUCCESS:   return "SUCCESS"
	return "UNKNOWN"


# ══════════════════════════════════════════════
# Construction helpers
# ══════════════════════════════════════════════

func _build_robot() -> void:
	# ── Body (rounded capsule) ───────────────
	_body = _make_mesh(CapsuleMesh.new(), _color_body)
	var body_mesh := _body.mesh as CapsuleMesh
	body_mesh.radius = 0.30
	body_mesh.height = 0.70
	_body.position = Vector3(0.0, 0.55, 0.0)
	add_child(_body)

	# ── Head pivot (allows sway without moving the body) ──
	_head_pivot = Node3D.new()
	_head_pivot.position = Vector3(0.0, 1.05, 0.0)
	add_child(_head_pivot)

	# ── Head (sphere) ────────────────────────
	_head = _make_mesh(SphereMesh.new(), _color_head)
	var head_mesh := _head.mesh as SphereMesh
	head_mesh.radius = 0.28
	head_mesh.height = 0.56
	_head.position = Vector3.ZERO
	_head_pivot.add_child(_head)

	# ── Eyes ─────────────────────────────────
	_left_eye = _make_eye(Vector3(-0.12, 0.06, 0.22))
	_right_eye = _make_eye(Vector3(0.12, 0.06, 0.22))
	_head_pivot.add_child(_left_eye)
	_head_pivot.add_child(_right_eye)

	# ── Antenna ──────────────────────────────
	_antenna = _make_mesh(CylinderMesh.new(), _color_antenna)
	var ant_mesh := _antenna.mesh as CylinderMesh
	ant_mesh.top_radius = 0.015
	ant_mesh.bottom_radius = 0.02
	ant_mesh.height = 0.18
	_antenna.position = Vector3(0.0, 0.30, 0.0)
	_head_pivot.add_child(_antenna)

	_antenna_tip = _make_mesh(SphereMesh.new(), _color_antenna_tip)
	var tip_mesh := _antenna_tip.mesh as SphereMesh
	tip_mesh.radius = 0.04
	tip_mesh.height = 0.08
	_antenna_tip.position = Vector3(0.0, 0.40, 0.0)
	_head_pivot.add_child(_antenna_tip)

	# ── Arms (small capsules) ────────────────
	_left_arm = _make_limb(Vector3(-0.40, 0.60, 0.0))
	_right_arm = _make_limb(Vector3(0.40, 0.60, 0.0))
	add_child(_left_arm)
	add_child(_right_arm)

	# ── Legs (cylinders) ─────────────────────
	_left_leg = _make_leg(Vector3(-0.12, 0.12, 0.0))
	_right_leg = _make_leg(Vector3(0.12, 0.12, 0.0))
	add_child(_left_leg)
	add_child(_right_leg)


func _make_mesh(mesh: Mesh, color: Color) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.6
	mat.metallic = 0.1
	mi.material_override = mat
	return mi


func _make_eye(pos: Vector3) -> MeshInstance3D:
	var eye := _make_mesh(SphereMesh.new(), _color_eye_idle)
	var eye_mesh := eye.mesh as SphereMesh
	eye_mesh.radius = 0.07
	eye_mesh.height = 0.14
	eye.position = pos
	# Slight emission so eyes glow subtly.
	var mat := eye.material_override as StandardMaterial3D
	mat.emission_enabled = true
	mat.emission = _color_eye_idle
	mat.emission_energy_multiplier = 0.4
	return eye


func _make_limb(pos: Vector3) -> MeshInstance3D:
	var limb := _make_mesh(CapsuleMesh.new(), _color_limb)
	var limb_mesh := limb.mesh as CapsuleMesh
	limb_mesh.radius = 0.06
	limb_mesh.height = 0.28
	limb.position = pos
	return limb


func _make_leg(pos: Vector3) -> MeshInstance3D:
	var leg := _make_mesh(CylinderMesh.new(), _color_limb)
	var leg_mesh := leg.mesh as CylinderMesh
	leg_mesh.top_radius = 0.055
	leg_mesh.bottom_radius = 0.065
	leg_mesh.height = 0.22
	leg.position = pos
	return leg


# ══════════════════════════════════════════════
# Animation
# ══════════════════════════════════════════════

func _animate_idle(delta: float) -> void:
	# Gentle body bob.
	var bob_offset: float = sin(_elapsed * idle_bob_speed) * idle_bob_amplitude
	_body.position.y = _body_base_y + bob_offset

	# Head sway.
	var sway_angle: float = sin(_elapsed * head_sway_speed) * deg_to_rad(head_sway_max_deg)
	_head_pivot.rotation.z = sway_angle

	# Arm sway (opposite phase for natural feel).
	var arm_sway: float = sin(_elapsed * idle_bob_speed * 0.7) * deg_to_rad(6.0)
	_left_arm.rotation.z = arm_sway
	_right_arm.rotation.z = -arm_sway


func _animate_state(_delta: float) -> void:
	var target_eye_color: Color
	var target_emission: float = 0.4
	var target_tip_emission: float = 0.0

	match current_state:
		State.IDLE:
			target_eye_color = _color_eye_idle
			target_emission = 0.4
			target_tip_emission = 0.0
		State.LISTENING:
			target_eye_color = _color_eye_listen
			target_emission = 0.8
			# Antenna tip pulses gently.
			target_tip_emission = 0.5 + sin(_elapsed * 3.0) * 0.3
		State.THINKING:
			target_eye_color = _color_eye_think
			target_emission = 0.6
			# Antenna tip pulses faster while "thinking".
			target_tip_emission = 0.5 + sin(_elapsed * 6.0) * 0.4
		State.SUCCESS:
			target_eye_color = _color_eye_success
			target_emission = 1.2
			target_tip_emission = 1.0

	# Smoothly lerp eye colour and emission.
	var weight: float = clampf(_delta * state_transition_speed, 0.0, 1.0)
	_set_eye_state(_left_eye, target_eye_color, target_emission, weight)
	_set_eye_state(_right_eye, target_eye_color, target_emission, weight)

	# Antenna tip emission.
	var tip_mat := _antenna_tip.material_override as StandardMaterial3D
	if not tip_mat.emission_enabled:
		tip_mat.emission_enabled = true
		tip_mat.emission = _color_antenna_tip
	tip_mat.emission_energy_multiplier = lerpf(
		tip_mat.emission_energy_multiplier, target_tip_emission, weight
	)


func _set_eye_state(
	eye: MeshInstance3D,
	target_color: Color,
	target_energy: float,
	weight: float,
) -> void:
	var mat := eye.material_override as StandardMaterial3D
	mat.albedo_color = mat.albedo_color.lerp(target_color, weight)
	mat.emission = mat.emission.lerp(target_color, weight)
	mat.emission_energy_multiplier = lerpf(
		mat.emission_energy_multiplier, target_energy, weight
	)
