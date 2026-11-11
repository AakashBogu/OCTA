## robot_character.gd
## High-quality articulated 3D character companion with AnimationTree state machine.
## States: IDLE, LISTENING, THINKING, SUCCESS, TALKING, POINTING, CELEBRATING.
##
## Asset Provenance & License:
## Created natively in GDScript for OCTA under the MIT License (see godot/ASSETS_LICENSE.md).
## 100% procedural geometry and materials; zero external copyright dependencies.

class_name RobotCharacter
extends Node3D

# ──────────────────────────────────────────────
# Enums & Constants
# ──────────────────────────────────────────────
enum State {
	IDLE,
	LISTENING,
	THINKING,
	SUCCESS,
	TALKING,
	POINTING,
	CELEBRATING,
}

# ──────────────────────────────────────────────
# Signals
# ──────────────────────────────────────────────
signal state_changed(new_state: State)
signal animation_triggered(anim_name: String)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
@export var transition_fade_time: float = 0.22
@export var emission_lerp_speed: float = 6.0

# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────
var current_state: State = State.IDLE:
	set(value):
		if current_state != value:
			var prev_state := current_state
			current_state = value
			_state_time = 0.0
			_apply_state_transition(prev_state, current_state)
			state_changed.emit(current_state)

var _state_time: float = 0.0
var _elapsed: float = 0.0

# ──────────────────────────────────────────────
# Node References
# ──────────────────────────────────────────────
var _body_pivot: Node3D
var _chassis: MeshInstance3D
var _chest_core: MeshInstance3D
var _neck: MeshInstance3D
var _head_pivot: Node3D
var _head: MeshInstance3D
var _visor: MeshInstance3D
var _left_eye: MeshInstance3D
var _right_eye: MeshInstance3D
var _mouth_grille: MeshInstance3D
var _left_antenna_pivot: Node3D
var _left_antenna: MeshInstance3D
var _left_antenna_tip: MeshInstance3D
var _right_antenna_pivot: Node3D
var _right_antenna: MeshInstance3D
var _right_antenna_tip: MeshInstance3D
var _left_arm_pivot: Node3D
var _left_upper_arm: MeshInstance3D
var _left_forearm_pivot: Node3D
var _left_forearm: MeshInstance3D
var _left_hand: MeshInstance3D
var _right_arm_pivot: Node3D
var _right_upper_arm: MeshInstance3D
var _right_forearm_pivot: Node3D
var _right_forearm: MeshInstance3D
var _right_hand: MeshInstance3D
var _right_pointer_finger: MeshInstance3D
var _thruster_ring: MeshInstance3D
var _ground_shadow: MeshInstance3D

var _anim_player: AnimationPlayer
var _anim_tree: AnimationTree
var _playback: AnimationNodeStateMachinePlayback

# ──────────────────────────────────────────────
# Material & Palette Definitions
# ──────────────────────────────────────────────
var _color_chassis_base := Color(0.90, 0.93, 0.97)       # Pearlescent metallic white
var _color_chassis_trim := Color(0.24, 0.38, 0.58)       # Deep slate blue
var _color_visor := Color(0.08, 0.10, 0.14)              # Glossy dark obsidian visor
var _color_joint := Color(0.18, 0.20, 0.25)              # Dark graphite joint
var _color_eye_idle := Color(0.30, 0.82, 0.98)          # Bright cyan
var _color_eye_listen := Color(0.25, 0.95, 0.55)        # Vibrant emerald green
var _color_eye_think := Color(0.98, 0.76, 0.20)         # Warm amber
var _color_eye_celebrate := Color(0.20, 0.95, 0.45)     # Joyous celebration green
var _color_antenna_tip_default := Color(0.98, 0.32, 0.32) # Coral red accent
var _color_mouth := Color(0.35, 0.85, 1.0)              # Cyan audio waveform

# Material references for dynamic emission
var _eye_mat: StandardMaterial3D
var _chest_core_mat: StandardMaterial3D
var _antenna_tip_mat: StandardMaterial3D
var _mouth_mat: StandardMaterial3D
var _thruster_mat: StandardMaterial3D


func _ready() -> void:
	if not _body_pivot:
		setup()


## Initializes character materials, hierarchy, animations, and state machine.
## Can be called directly or automatically via _ready().
func setup() -> void:
	if _body_pivot:
		return
	_build_materials()
	_build_character_hierarchy()
	_setup_animations()
	_apply_state_transition(State.IDLE, State.IDLE)


func _process(delta: float) -> void:
	_elapsed += delta
	_state_time += delta
	_update_dynamic_emissions(delta)


# ══════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════

## Cycle to the next standard state (useful for user testing / hotkeys).
func cycle_state() -> void:
	var next_idx := (int(current_state) + 1) % State.size()
	current_state = next_idx as State


## Set state by name string (case-insensitive).
## Compatible with protocol v1.0.0 ("IDLE", "LISTENING", "THINKING", "SUCCESS")
## and extended states ("TALKING", "POINTING", "CELEBRATING").
func set_state_by_name(state_name: String) -> void:
	match state_name.strip_edges().to_upper():
		"IDLE":
			current_state = State.IDLE
		"LISTENING":
			current_state = State.LISTENING
		"THINKING":
			current_state = State.THINKING
		"SUCCESS":
			current_state = State.SUCCESS
		"TALKING":
			current_state = State.TALKING
		"POINTING":
			current_state = State.POINTING
		"CELEBRATING":
			current_state = State.CELEBRATING


## Return the current state name string.
## For protocol v1.0.0 compatibility, SUCCESS is preserved.
func get_state_name() -> String:
	match current_state:
		State.IDLE:
			return "IDLE"
		State.LISTENING:
			return "LISTENING"
		State.THINKING:
			return "THINKING"
		State.SUCCESS:
			return "SUCCESS"
		State.TALKING:
			return "TALKING"
		State.POINTING:
			return "POINTING"
		State.CELEBRATING:
			return "CELEBRATING"
	return "UNKNOWN"


## Return active animation node from the AnimationTree state machine.
func get_active_animation() -> String:
	if _playback:
		return _playback.get_current_node()
	return ""


## Convenience action triggers
func idle() -> void:
	current_state = State.IDLE


func listen() -> void:
	current_state = State.LISTENING


func think() -> void:
	current_state = State.THINKING


func celebrate() -> void:
	current_state = State.CELEBRATING


func talk() -> void:
	current_state = State.TALKING


func point() -> void:
	current_state = State.POINTING


# ══════════════════════════════════════════════
# State Machine & Animation Transitions
# ══════════════════════════════════════════════

func _apply_state_transition(_from_state: State, to_state: State) -> void:
	var target_node := "idle"
	match to_state:
		State.IDLE:
			target_node = "idle"
		State.LISTENING:
			target_node = "listening"
		State.THINKING:
			target_node = "thinking"
		State.SUCCESS, State.CELEBRATING:
			target_node = "celebrating"
		State.TALKING:
			target_node = "talking"
		State.POINTING:
			target_node = "pointing"

	if _playback:
		_playback.travel(target_node)
	animation_triggered.emit(target_node)


func _update_dynamic_emissions(delta: float) -> void:
	var target_eye_color: Color = _color_eye_idle
	var target_eye_energy: float = 1.0
	var target_tip_energy: float = 0.4
	var target_mouth_energy: float = 0.0
	var target_core_energy: float = 0.8

	match current_state:
		State.IDLE:
			target_eye_color = _color_eye_idle
			target_eye_energy = 0.9 + sin(_elapsed * 1.5) * 0.15
			target_tip_energy = 0.3
			target_mouth_energy = 0.05
			target_core_energy = 0.7 + sin(_elapsed * 2.0) * 0.2

		State.LISTENING:
			target_eye_color = _color_eye_listen
			target_eye_energy = 1.4 + sin(_elapsed * 3.5) * 0.3
			target_tip_energy = 0.8 + sin(_elapsed * 4.0) * 0.4
			target_mouth_energy = 0.2
			target_core_energy = 1.2 + sin(_elapsed * 3.0) * 0.3

		State.THINKING:
			target_eye_color = _color_eye_think
			target_eye_energy = 1.1 + sin(_elapsed * 6.0) * 0.4
			target_tip_energy = 1.2 + sin(_elapsed * 8.0) * 0.6
			target_mouth_energy = 0.1
			target_core_energy = 0.9 + sin(_elapsed * 5.0) * 0.4

		State.TALKING:
			target_eye_color = _color_eye_idle
			target_eye_energy = 1.2 + sin(_elapsed * 4.0) * 0.2
			# Mouth LED flickers rapidly with simulated speech cadence
			var speech_flicker := absf(sin(_elapsed * 14.0) * cos(_elapsed * 7.5))
			target_mouth_energy = 0.6 + speech_flicker * 1.6
			target_tip_energy = 0.5 + sin(_elapsed * 3.0) * 0.2
			target_core_energy = 1.0 + speech_flicker * 0.5

		State.POINTING:
			target_eye_color = _color_eye_idle
			target_eye_energy = 1.5
			target_tip_energy = 0.8
			target_mouth_energy = 0.3
			target_core_energy = 1.4

		State.SUCCESS, State.CELEBRATING:
			target_eye_color = _color_eye_celebrate
			target_eye_energy = 1.8 + sin(_elapsed * 9.0) * 0.5
			target_tip_energy = 1.6 + sin(_elapsed * 9.0) * 0.6
			target_mouth_energy = 1.2 + sin(_elapsed * 8.0) * 0.6
			target_core_energy = 1.8 + sin(_elapsed * 7.0) * 0.5

	var lerp_w := clampf(delta * emission_lerp_speed, 0.0, 1.0)

	if _eye_mat:
		_eye_mat.albedo_color = _eye_mat.albedo_color.lerp(target_eye_color, lerp_w)
		_eye_mat.emission = _eye_mat.emission.lerp(target_eye_color, lerp_w)
		_eye_mat.emission_energy_multiplier = lerpf(
			_eye_mat.emission_energy_multiplier, target_eye_energy, lerp_w
		)

	if _antenna_tip_mat:
		_antenna_tip_mat.emission_energy_multiplier = lerpf(
			_antenna_tip_mat.emission_energy_multiplier, target_tip_energy, lerp_w
		)

	if _mouth_mat:
		_mouth_mat.emission_energy_multiplier = lerpf(
			_mouth_mat.emission_energy_multiplier, target_mouth_energy, lerp_w
		)

	if _chest_core_mat:
		_chest_core_mat.emission_energy_multiplier = lerpf(
			_chest_core_mat.emission_energy_multiplier, target_core_energy, lerp_w
		)

	if _thruster_mat:
		var thruster_pulse := 0.8 + sin(_elapsed * 4.0) * 0.25
		_thruster_mat.emission_energy_multiplier = thruster_pulse


# ══════════════════════════════════════════════
# Materials Construction
# ══════════════════════════════════════════════

func _build_materials() -> void:
	_eye_mat = StandardMaterial3D.new()
	_eye_mat.albedo_color = _color_eye_idle
	_eye_mat.roughness = 0.15
	_eye_mat.metallic = 0.1
	_eye_mat.emission_enabled = true
	_eye_mat.emission = _color_eye_idle
	_eye_mat.emission_energy_multiplier = 1.0

	_chest_core_mat = StandardMaterial3D.new()
	_chest_core_mat.albedo_color = Color(0.2, 0.75, 1.0)
	_chest_core_mat.roughness = 0.2
	_chest_core_mat.emission_enabled = true
	_chest_core_mat.emission = Color(0.2, 0.8, 1.0)
	_chest_core_mat.emission_energy_multiplier = 0.8

	_antenna_tip_mat = StandardMaterial3D.new()
	_antenna_tip_mat.albedo_color = _color_antenna_tip_default
	_antenna_tip_mat.roughness = 0.25
	_antenna_tip_mat.emission_enabled = true
	_antenna_tip_mat.emission = _color_antenna_tip_default
	_antenna_tip_mat.emission_energy_multiplier = 0.5

	_mouth_mat = StandardMaterial3D.new()
	_mouth_mat.albedo_color = _color_mouth
	_mouth_mat.roughness = 0.2
	_mouth_mat.emission_enabled = true
	_mouth_mat.emission = _color_mouth
	_mouth_mat.emission_energy_multiplier = 0.1

	_thruster_mat = StandardMaterial3D.new()
	_thruster_mat.albedo_color = Color(0.3, 0.85, 1.0)
	_thruster_mat.roughness = 0.3
	_thruster_mat.emission_enabled = true
	_thruster_mat.emission = Color(0.3, 0.85, 1.0)
	_thruster_mat.emission_energy_multiplier = 0.8


func _make_chassis_mat() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = _color_chassis_base
	mat.roughness = 0.25
	mat.metallic = 0.15
	return mat


func _make_trim_mat() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = _color_chassis_trim
	mat.roughness = 0.35
	mat.metallic = 0.2
	return mat


func _make_joint_mat() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = _color_joint
	mat.roughness = 0.45
	mat.metallic = 0.6
	return mat


# ══════════════════════════════════════════════
# Character Geometry Hierarchy
# ══════════════════════════════════════════════

func _build_character_hierarchy() -> void:
	# ── Root Body Pivot ──
	_body_pivot = Node3D.new()
	_body_pivot.name = "BodyPivot"
	add_child(_body_pivot)

	# ── Chassis / Torso ──
	var chassis_mesh := CapsuleMesh.new()
	chassis_mesh.radius = 0.30
	chassis_mesh.height = 0.68
	chassis_mesh.radial_segments = 32
	chassis_mesh.rings = 8

	_chassis = MeshInstance3D.new()
	_chassis.name = "Chassis"
	_chassis.mesh = chassis_mesh
	_chassis.material_override = _make_chassis_mat()
	_chassis.position = Vector3(0.0, 0.54, 0.0)
	_body_pivot.add_child(_chassis)

	# ── Chest Core Indicator ──
	var core_mesh := CylinderMesh.new()
	core_mesh.top_radius = 0.08
	core_mesh.bottom_radius = 0.08
	core_mesh.height = 0.04
	core_mesh.radial_segments = 24

	_chest_core = MeshInstance3D.new()
	_chest_core.name = "ChestCore"
	_chest_core.mesh = core_mesh
	_chest_core.material_override = _chest_core_mat
	_chest_core.rotation_degrees = Vector3(90.0, 0.0, 0.0)
	_chest_core.position = Vector3(0.0, 0.58, 0.28)
	_body_pivot.add_child(_chest_core)

	# ── Neck Joint ──
	var neck_mesh := CylinderMesh.new()
	neck_mesh.top_radius = 0.12
	neck_mesh.bottom_radius = 0.14
	neck_mesh.height = 0.12
	neck_mesh.radial_segments = 24

	_neck = MeshInstance3D.new()
	_neck.name = "Neck"
	_neck.mesh = neck_mesh
	_neck.material_override = _make_joint_mat()
	_neck.position = Vector3(0.0, 0.90, 0.0)
	_body_pivot.add_child(_neck)

	# ── Head Assembly (Pivot at neck center) ──
	_head_pivot = Node3D.new()
	_head_pivot.name = "HeadPivot"
	_head_pivot.position = Vector3(0.0, 1.02, 0.0)
	_body_pivot.add_child(_head_pivot)

	var head_mesh := SphereMesh.new()
	head_mesh.radius = 0.28
	head_mesh.height = 0.50
	head_mesh.radial_segments = 32
	head_mesh.rings = 16

	_head = MeshInstance3D.new()
	_head.name = "Head"
	_head.mesh = head_mesh
	_head.material_override = _make_chassis_mat()
	_head.position = Vector3.ZERO
	_head_pivot.add_child(_head)

	# ── Curved Visor Faceplate ──
	var visor_mesh := SphereMesh.new()
	visor_mesh.radius = 0.26
	visor_mesh.height = 0.38
	visor_mesh.radial_segments = 32
	visor_mesh.rings = 16

	var visor_mat := StandardMaterial3D.new()
	visor_mat.albedo_color = _color_visor
	visor_mat.roughness = 0.08
	visor_mat.metallic = 0.85

	_visor = MeshInstance3D.new()
	_visor.name = "Visor"
	_visor.mesh = visor_mesh
	_visor.material_override = visor_mat
	_visor.position = Vector3(0.0, 0.01, 0.07)
	_visor.scale = Vector3(0.96, 0.92, 0.85)
	_head_pivot.add_child(_visor)

	# ── Digital Eyes ──
	var eye_mesh := SphereMesh.new()
	eye_mesh.radius = 0.055
	eye_mesh.height = 0.06
	eye_mesh.radial_segments = 24

	_left_eye = MeshInstance3D.new()
	_left_eye.name = "LeftEye"
	_left_eye.mesh = eye_mesh
	_left_eye.material_override = _eye_mat
	_left_eye.position = Vector3(-0.11, 0.04, 0.24)
	_head_pivot.add_child(_left_eye)

	_right_eye = MeshInstance3D.new()
	_right_eye.name = "RightEye"
	_right_eye.mesh = eye_mesh
	_right_eye.material_override = _eye_mat
	_right_eye.position = Vector3(0.11, 0.04, 0.24)
	_head_pivot.add_child(_right_eye)

	# ── Digital Mouth / Waveform Grille ──
	var mouth_mesh := BoxMesh.new()
	mouth_mesh.size = Vector3(0.12, 0.022, 0.03)

	_mouth_grille = MeshInstance3D.new()
	_mouth_grille.name = "MouthGrille"
	_mouth_grille.mesh = mouth_mesh
	_mouth_grille.material_override = _mouth_mat
	_mouth_grille.position = Vector3(0.0, -0.065, 0.24)
	_head_pivot.add_child(_mouth_grille)

	# ── Dual Articulated Antennae ──
	_left_antenna_pivot = Node3D.new()
	_left_antenna_pivot.name = "LeftAntennaPivot"
	_left_antenna_pivot.position = Vector3(-0.14, 0.22, 0.0)
	_left_antenna_pivot.rotation_degrees = Vector3(0.0, 0.0, 18.0)
	_head_pivot.add_child(_left_antenna_pivot)

	var ant_stalk_mesh := CylinderMesh.new()
	ant_stalk_mesh.top_radius = 0.012
	ant_stalk_mesh.bottom_radius = 0.02
	ant_stalk_mesh.height = 0.16

	_left_antenna = MeshInstance3D.new()
	_left_antenna.name = "LeftAntenna"
	_left_antenna.mesh = ant_stalk_mesh
	_left_antenna.material_override = _make_trim_mat()
	_left_antenna.position = Vector3(0.0, 0.08, 0.0)
	_left_antenna_pivot.add_child(_left_antenna)

	var ant_tip_mesh := SphereMesh.new()
	ant_tip_mesh.radius = 0.035
	ant_tip_mesh.height = 0.07

	_left_antenna_tip = MeshInstance3D.new()
	_left_antenna_tip.name = "LeftAntennaTip"
	_left_antenna_tip.mesh = ant_tip_mesh
	_left_antenna_tip.material_override = _antenna_tip_mat
	_left_antenna_tip.position = Vector3(0.0, 0.17, 0.0)
	_left_antenna_pivot.add_child(_left_antenna_tip)

	_right_antenna_pivot = Node3D.new()
	_right_antenna_pivot.name = "RightAntennaPivot"
	_right_antenna_pivot.position = Vector3(0.14, 0.22, 0.0)
	_right_antenna_pivot.rotation_degrees = Vector3(0.0, 0.0, -18.0)
	_head_pivot.add_child(_right_antenna_pivot)

	_right_antenna = MeshInstance3D.new()
	_right_antenna.name = "RightAntenna"
	_right_antenna.mesh = ant_stalk_mesh
	_right_antenna.material_override = _make_trim_mat()
	_right_antenna.position = Vector3(0.0, 0.08, 0.0)
	_right_antenna_pivot.add_child(_right_antenna)

	_right_antenna_tip = MeshInstance3D.new()
	_right_antenna_tip.name = "RightAntennaTip"
	_right_antenna_tip.mesh = ant_tip_mesh
	_right_antenna_tip.material_override = _antenna_tip_mat
	_right_antenna_tip.position = Vector3(0.0, 0.17, 0.0)
	_right_antenna_pivot.add_child(_right_antenna_tip)

	# ── Left Arm Hierarchy ──
	_left_arm_pivot = Node3D.new()
	_left_arm_pivot.name = "LeftArmPivot"
	_left_arm_pivot.position = Vector3(-0.36, 0.68, 0.0)
	_body_pivot.add_child(_left_arm_pivot)

	var shoulder_mesh := SphereMesh.new()
	shoulder_mesh.radius = 0.07
	shoulder_mesh.height = 0.14

	var left_shoulder := MeshInstance3D.new()
	left_shoulder.name = "LeftShoulder"
	left_shoulder.mesh = shoulder_mesh
	left_shoulder.material_override = _make_trim_mat()
	_left_arm_pivot.add_child(left_shoulder)

	var upper_arm_mesh := CapsuleMesh.new()
	upper_arm_mesh.radius = 0.048
	upper_arm_mesh.height = 0.18

	_left_upper_arm = MeshInstance3D.new()
	_left_upper_arm.name = "LeftUpperArm"
	_left_upper_arm.mesh = upper_arm_mesh
	_left_upper_arm.material_override = _make_chassis_mat()
	_left_upper_arm.position = Vector3(0.0, -0.11, 0.0)
	_left_arm_pivot.add_child(_left_upper_arm)

	_left_forearm_pivot = Node3D.new()
	_left_forearm_pivot.name = "LeftForearmPivot"
	_left_forearm_pivot.position = Vector3(0.0, -0.21, 0.0)
	_left_arm_pivot.add_child(_left_forearm_pivot)

	var forearm_mesh := CapsuleMesh.new()
	forearm_mesh.radius = 0.042
	forearm_mesh.height = 0.16

	_left_forearm = MeshInstance3D.new()
	_left_forearm.name = "LeftForearm"
	_left_forearm.mesh = forearm_mesh
	_left_forearm.material_override = _make_trim_mat()
	_left_forearm.position = Vector3(0.0, -0.09, 0.0)
	_left_forearm_pivot.add_child(_left_forearm)

	var hand_mesh := SphereMesh.new()
	hand_mesh.radius = 0.05
	hand_mesh.height = 0.10

	_left_hand = MeshInstance3D.new()
	_left_hand.name = "LeftHand"
	_left_hand.mesh = hand_mesh
	_left_hand.material_override = _make_chassis_mat()
	_left_hand.position = Vector3(0.0, -0.18, 0.0)
	_left_forearm_pivot.add_child(_left_hand)

	# ── Right Arm Hierarchy (with Expressive Pointer Finger) ──
	_right_arm_pivot = Node3D.new()
	_right_arm_pivot.name = "RightArmPivot"
	_right_arm_pivot.position = Vector3(0.36, 0.68, 0.0)
	_body_pivot.add_child(_right_arm_pivot)

	var right_shoulder := MeshInstance3D.new()
	right_shoulder.name = "RightShoulder"
	right_shoulder.mesh = shoulder_mesh
	right_shoulder.material_override = _make_trim_mat()
	_right_arm_pivot.add_child(right_shoulder)

	_right_upper_arm = MeshInstance3D.new()
	_right_upper_arm.name = "RightUpperArm"
	_right_upper_arm.mesh = upper_arm_mesh
	_right_upper_arm.material_override = _make_chassis_mat()
	_right_upper_arm.position = Vector3(0.0, -0.11, 0.0)
	_right_arm_pivot.add_child(_right_upper_arm)

	_right_forearm_pivot = Node3D.new()
	_right_forearm_pivot.name = "RightForearmPivot"
	_right_forearm_pivot.position = Vector3(0.0, -0.21, 0.0)
	_right_arm_pivot.add_child(_right_forearm_pivot)

	_right_forearm = MeshInstance3D.new()
	_right_forearm.name = "RightForearm"
	_right_forearm.mesh = forearm_mesh
	_right_forearm.material_override = _make_trim_mat()
	_right_forearm.position = Vector3(0.0, -0.09, 0.0)
	_right_forearm_pivot.add_child(_right_forearm)

	_right_hand = MeshInstance3D.new()
	_right_hand.name = "RightHand"
	_right_hand.mesh = hand_mesh
	_right_hand.material_override = _make_chassis_mat()
	_right_hand.position = Vector3(0.0, -0.18, 0.0)
	_right_forearm_pivot.add_child(_right_hand)

	# Distinct Pointer Finger extending forward from right hand
	var finger_mesh := CapsuleMesh.new()
	finger_mesh.radius = 0.016
	finger_mesh.height = 0.08

	_right_pointer_finger = MeshInstance3D.new()
	_right_pointer_finger.name = "RightPointerFinger"
	_right_pointer_finger.mesh = finger_mesh
	_right_pointer_finger.material_override = _make_trim_mat()
	_right_pointer_finger.position = Vector3(0.0, -0.22, 0.03)
	_right_pointer_finger.rotation_degrees = Vector3(40.0, 0.0, 0.0)
	_right_forearm_pivot.add_child(_right_pointer_finger)

	# ── Anti-gravity Thruster Ring / Lower Base ──
	var thruster_mesh := TorusMesh.new()
	thruster_mesh.inner_radius = 0.12
	thruster_mesh.outer_radius = 0.22
	thruster_mesh.rings = 24
	thruster_mesh.ring_segments = 16

	_thruster_ring = MeshInstance3D.new()
	_thruster_ring.name = "ThrusterRing"
	_thruster_ring.mesh = thruster_mesh
	_thruster_ring.material_override = _thruster_mat
	_thruster_ring.position = Vector3(0.0, 0.18, 0.0)
	_body_pivot.add_child(_thruster_ring)

	# ── Ground Contact Shadow (Soft Transparent Disc) ──
	var shadow_mesh := CylinderMesh.new()
	shadow_mesh.top_radius = 0.38
	shadow_mesh.bottom_radius = 0.38
	shadow_mesh.height = 0.002
	shadow_mesh.radial_segments = 32

	var shadow_mat := StandardMaterial3D.new()
	shadow_mat.transparency = StandardMaterial3D.TRANSPARENCY_ALPHA
	shadow_mat.albedo_color = Color(0.0, 0.0, 0.0, 0.32)
	shadow_mat.roughness = 1.0

	_ground_shadow = MeshInstance3D.new()
	_ground_shadow.name = "GroundShadow"
	_ground_shadow.mesh = shadow_mesh
	_ground_shadow.material_override = shadow_mat
	_ground_shadow.position = Vector3(0.0, 0.001, 0.0)
	add_child(_ground_shadow)


# ══════════════════════════════════════════════
# Animation Player & AnimationTree Setup
# ══════════════════════════════════════════════

func _setup_animations() -> void:
	_anim_player = AnimationPlayer.new()
	_anim_player.name = "AnimPlayer"
	add_child(_anim_player)

	var lib := AnimationLibrary.new()

	# ── 1. Idle Animation ──
	var idle_anim := Animation.new()
	idle_anim.length = 2.0
	idle_anim.loop_mode = Animation.LOOP_LINEAR
	_add_vec3_track(idle_anim, "BodyPivot:position", [
		[0.0, Vector3(0.0, 0.0, 0.0)],
		[1.0, Vector3(0.0, 0.035, 0.0)],
		[2.0, Vector3(0.0, 0.0, 0.0)],
	])
	_add_vec3_track(idle_anim, "BodyPivot/HeadPivot:rotation", [
		[0.0, Vector3(0.0, 0.0, -0.04)],
		[1.0, Vector3(0.0, 0.0, 0.04)],
		[2.0, Vector3(0.0, 0.0, -0.04)],
	])
	_add_vec3_track(idle_anim, "BodyPivot/LeftArmPivot:rotation", [
		[0.0, Vector3(0.0, 0.0, 0.08)],
		[1.0, Vector3(0.0, 0.0, 0.02)],
		[2.0, Vector3(0.0, 0.0, 0.08)],
	])
	_add_vec3_track(idle_anim, "BodyPivot/RightArmPivot:rotation", [
		[0.0, Vector3(0.0, 0.0, -0.08)],
		[1.0, Vector3(0.0, 0.0, -0.02)],
		[2.0, Vector3(0.0, 0.0, -0.08)],
	])
	_add_vec3_track(idle_anim, "BodyPivot/RightArmPivot/RightForearmPivot:rotation", [
		[0.0, Vector3.ZERO],
		[2.0, Vector3.ZERO],
	])
	lib.add_animation("idle", idle_anim)

	# ── 2. Listening Animation ──
	var listen_anim := Animation.new()
	listen_anim.length = 1.6
	listen_anim.loop_mode = Animation.LOOP_LINEAR
	_add_vec3_track(listen_anim, "BodyPivot:position", [
		[0.0, Vector3(0.0, 0.01, 0.0)],
		[0.8, Vector3(0.0, 0.03, 0.0)],
		[1.6, Vector3(0.0, 0.01, 0.0)],
	])
	_add_vec3_track(listen_anim, "BodyPivot/HeadPivot:rotation", [
		[0.0, Vector3(0.12, 0.0, 0.06)],
		[0.8, Vector3(0.15, 0.0, 0.02)],
		[1.6, Vector3(0.12, 0.0, 0.06)],
	])
	_add_vec3_track(listen_anim, "BodyPivot/LeftArmPivot:rotation", [
		[0.0, Vector3(0.12, 0.0, 0.12)],
		[0.8, Vector3(0.16, 0.0, 0.14)],
		[1.6, Vector3(0.12, 0.0, 0.12)],
	])
	_add_vec3_track(listen_anim, "BodyPivot/RightArmPivot:rotation", [
		[0.0, Vector3(0.12, 0.0, -0.12)],
		[0.8, Vector3(0.16, 0.0, -0.14)],
		[1.6, Vector3(0.12, 0.0, -0.12)],
	])
	_add_vec3_track(listen_anim, "BodyPivot/RightArmPivot/RightForearmPivot:rotation", [
		[0.0, Vector3.ZERO],
		[1.6, Vector3.ZERO],
	])
	lib.add_animation("listening", listen_anim)

	# ── 3. Thinking Animation (Hand to Chin / Pondering Pose) ──
	var think_anim := Animation.new()
	think_anim.length = 1.8
	think_anim.loop_mode = Animation.LOOP_LINEAR
	_add_vec3_track(think_anim, "BodyPivot:position", [
		[0.0, Vector3(0.0, 0.0, 0.0)],
		[0.9, Vector3(0.0, 0.02, 0.0)],
		[1.8, Vector3(0.0, 0.0, 0.0)],
	])
	_add_vec3_track(think_anim, "BodyPivot/HeadPivot:rotation", [
		[0.0, Vector3(-0.04, 0.14, 0.22)],
		[0.9, Vector3(-0.06, 0.16, 0.25)],
		[1.8, Vector3(-0.04, 0.14, 0.22)],
	])
	_add_vec3_track(think_anim, "BodyPivot/LeftArmPivot:rotation", [
		[0.0, Vector3(0.1, 0.0, 0.15)],
		[0.9, Vector3(0.12, 0.0, 0.18)],
		[1.8, Vector3(0.1, 0.0, 0.15)],
	])
	_add_vec3_track(think_anim, "BodyPivot/RightArmPivot:rotation", [
		[0.0, Vector3(1.35, -0.25, -0.35)],
		[0.9, Vector3(1.40, -0.28, -0.32)],
		[1.8, Vector3(1.35, -0.25, -0.35)],
	])
	_add_vec3_track(think_anim, "BodyPivot/RightArmPivot/RightForearmPivot:rotation", [
		[0.0, Vector3(0.65, 0.0, 0.0)],
		[0.9, Vector3(0.70, 0.0, 0.0)],
		[1.8, Vector3(0.65, 0.0, 0.0)],
	])
	lib.add_animation("thinking", think_anim)

	# ── 4. Talking Animation (Rhythmic Gestures & Speech Cadence) ──
	var talk_anim := Animation.new()
	talk_anim.length = 1.2
	talk_anim.loop_mode = Animation.LOOP_LINEAR
	_add_vec3_track(talk_anim, "BodyPivot:position", [
		[0.0, Vector3(0.0, 0.01, 0.0)],
		[0.3, Vector3(0.0, 0.03, 0.0)],
		[0.6, Vector3(0.0, 0.015, 0.0)],
		[0.9, Vector3(0.0, 0.035, 0.0)],
		[1.2, Vector3(0.0, 0.01, 0.0)],
	])
	_add_vec3_track(talk_anim, "BodyPivot/HeadPivot:rotation", [
		[0.0, Vector3(0.08, 0.04, 0.0)],
		[0.3, Vector3(-0.04, -0.02, 0.02)],
		[0.6, Vector3(0.06, 0.02, -0.02)],
		[0.9, Vector3(-0.02, -0.04, 0.0)],
		[1.2, Vector3(0.08, 0.04, 0.0)],
	])
	_add_vec3_track(talk_anim, "BodyPivot/LeftArmPivot:rotation", [
		[0.0, Vector3(0.35, 0.15, 0.20)],
		[0.4, Vector3(0.15, 0.05, 0.10)],
		[0.8, Vector3(0.40, 0.20, 0.22)],
		[1.2, Vector3(0.35, 0.15, 0.20)],
	])
	_add_vec3_track(talk_anim, "BodyPivot/RightArmPivot:rotation", [
		[0.0, Vector3(0.45, -0.15, -0.22)],
		[0.5, Vector3(0.20, -0.05, -0.12)],
		[0.9, Vector3(0.50, -0.20, -0.25)],
		[1.2, Vector3(0.45, -0.15, -0.22)],
	])
	_add_vec3_track(talk_anim, "BodyPivot/RightArmPivot/RightForearmPivot:rotation", [
		[0.0, Vector3(0.3, 0.0, 0.0)],
		[0.6, Vector3(0.15, 0.0, 0.0)],
		[1.2, Vector3(0.3, 0.0, 0.0)],
	])
	lib.add_animation("talking", talk_anim)

	# ── 5. Pointing Animation (Arm Outstretched with Pointer Finger) ──
	var point_anim := Animation.new()
	point_anim.length = 1.6
	point_anim.loop_mode = Animation.LOOP_LINEAR
	_add_vec3_track(point_anim, "BodyPivot:position", [
		[0.0, Vector3(0.0, 0.015, 0.0)],
		[0.8, Vector3(0.0, 0.025, 0.0)],
		[1.6, Vector3(0.0, 0.015, 0.0)],
	])
	_add_vec3_track(point_anim, "BodyPivot/HeadPivot:rotation", [
		[0.0, Vector3(-0.02, -0.18, 0.02)],
		[0.8, Vector3(-0.04, -0.22, 0.0)],
		[1.6, Vector3(-0.02, -0.18, 0.02)],
	])
	_add_vec3_track(point_anim, "BodyPivot/LeftArmPivot:rotation", [
		[0.0, Vector3(-0.08, 0.05, 0.14)],
		[0.8, Vector3(-0.12, 0.05, 0.16)],
		[1.6, Vector3(-0.08, 0.05, 0.14)],
	])
	_add_vec3_track(point_anim, "BodyPivot/RightArmPivot:rotation", [
		[0.0, Vector3(1.30, -0.32, -0.18)],
		[0.8, Vector3(1.35, -0.35, -0.15)],
		[1.6, Vector3(1.30, -0.32, -0.18)],
	])
	_add_vec3_track(point_anim, "BodyPivot/RightArmPivot/RightForearmPivot:rotation", [
		[0.0, Vector3(0.05, 0.0, 0.0)],
		[0.8, Vector3(0.08, 0.0, 0.0)],
		[1.6, Vector3(0.05, 0.0, 0.0)],
	])
	lib.add_animation("pointing", point_anim)

	# ── 6. Celebrating Animation (Arms Overhead Victory \o/) ──
	var celebrate_anim := Animation.new()
	celebrate_anim.length = 0.9
	celebrate_anim.loop_mode = Animation.LOOP_LINEAR
	_add_vec3_track(celebrate_anim, "BodyPivot:position", [
		[0.0, Vector3(0.0, 0.0, 0.0)],
		[0.25, Vector3(0.0, 0.10, 0.0)],
		[0.5, Vector3(0.0, 0.02, 0.0)],
		[0.75, Vector3(0.0, 0.09, 0.0)],
		[0.9, Vector3(0.0, 0.0, 0.0)],
	])
	_add_vec3_track(celebrate_anim, "BodyPivot/HeadPivot:rotation", [
		[0.0, Vector3(0.12, 0.0, -0.14)],
		[0.45, Vector3(0.12, 0.0, 0.14)],
		[0.9, Vector3(0.12, 0.0, -0.14)],
	])
	_add_vec3_track(celebrate_anim, "BodyPivot/LeftArmPivot:rotation", [
		[0.0, Vector3(2.55, 0.20, 0.45)],
		[0.45, Vector3(2.65, 0.25, 0.55)],
		[0.9, Vector3(2.55, 0.20, 0.45)],
	])
	_add_vec3_track(celebrate_anim, "BodyPivot/RightArmPivot:rotation", [
		[0.0, Vector3(2.55, -0.20, -0.45)],
		[0.45, Vector3(2.65, -0.25, -0.55)],
		[0.9, Vector3(2.55, -0.20, -0.45)],
	])
	_add_vec3_track(celebrate_anim, "BodyPivot/RightArmPivot/RightForearmPivot:rotation", [
		[0.0, Vector3(0.35, 0.0, 0.0)],
		[0.45, Vector3(0.45, 0.0, 0.0)],
		[0.9, Vector3(0.35, 0.0, 0.0)],
	])
	lib.add_animation("celebrating", celebrate_anim)

	_anim_player.add_animation_library("", lib)

	# ── AnimationTree with State Machine ──
	_anim_tree = AnimationTree.new()
	_anim_tree.name = "AnimTree"
	_anim_tree.anim_player = NodePath("../AnimPlayer")

	var sm := AnimationNodeStateMachine.new()
	var state_names := ["idle", "listening", "thinking", "talking", "pointing", "celebrating"]

	for s_name in state_names:
		var anim_node := AnimationNodeAnimation.new()
		anim_node.animation = s_name
		sm.add_node(s_name, anim_node)

	# Auto start transition to idle
	var start_tr := AnimationNodeStateMachineTransition.new()
	start_tr.advance_mode = AnimationNodeStateMachineTransition.ADVANCE_MODE_AUTO
	sm.add_transition("Start", "idle", start_tr)

	# Connect all state pairs with smooth crossfade
	for from_state in state_names:
		for to_state in state_names:
			if from_state != to_state:
				var tr := AnimationNodeStateMachineTransition.new()
				tr.xfade_time = transition_fade_time
				sm.add_transition(from_state, to_state, tr)

	_anim_tree.tree_root = sm
	add_child(_anim_tree)
	_anim_tree.active = true

	_playback = _anim_tree.get("parameters/playback")


func _add_vec3_track(anim: Animation, track_path: String, keyframes: Array) -> void:
	var idx := anim.add_track(Animation.TYPE_VALUE)
	anim.track_set_path(idx, track_path)
	for kf in keyframes:
		anim.track_insert_key(idx, float(kf[0]), kf[1])
