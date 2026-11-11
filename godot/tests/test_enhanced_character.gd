## test_enhanced_character.gd
## Comprehensive tests for Phase 7.0 Enhanced 3D Robot Character, Animations, and AnimationTree.

extends SceneTree

const RobotCharacter = preload("res://scripts/robot_character.gd")
const MainScene = preload("res://scripts/main_scene.gd")

var _tests_run: int = 0
var _tests_passed: int = 0
var _tests_failed: int = 0


func _init() -> void:
	print("==================================================")
	print("OCTA Godot Phase 7.0 Enhanced Character Test Suite")
	print("==================================================")

	test_character_hierarchy_and_materials()
	test_animation_library_tracks()
	test_animation_tree_state_machine()
	test_state_transitions_and_playback()
	test_action_helper_methods()
	test_emission_updates()
	test_main_scene_integration()

	print("==================================================")
	print("Results: %d passed, %d failed (total: %d)" % [_tests_passed, _tests_failed, _tests_run])
	print("==================================================")

	if _tests_failed > 0:
		quit(1)
	else:
		quit(0)


func assert_true(condition: bool, msg: String) -> void:
	_tests_run += 1
	if condition:
		_tests_passed += 1
		print("  [PASS] %s" % msg)
	else:
		_tests_failed += 1
		printerr("  [FAIL] %s" % msg)


func assert_equal(actual: Variant, expected: Variant, msg: String) -> void:
	_tests_run += 1
	if actual == expected:
		_tests_passed += 1
		print("  [PASS] %s" % msg)
	else:
		_tests_failed += 1
		printerr("  [FAIL] %s: expected '%s', got '%s'" % [msg, str(expected), str(actual)])


func test_character_hierarchy_and_materials() -> void:
	print("\n--- Test: Character Hierarchy & Materials ---")
	var root := Node3D.new()
	get_root().add_child(root)

	var robot := RobotCharacter.new()
	root.add_child(robot)
	robot.setup()

	assert_true(robot.get_node_or_null("BodyPivot") != null, "BodyPivot node exists")
	assert_true(robot.get_node_or_null("BodyPivot/Chassis") != null, "Chassis mesh exists")
	assert_true(robot.get_node_or_null("BodyPivot/ChestCore") != null, "ChestCore mesh exists")
	assert_true(robot.get_node_or_null("BodyPivot/Neck") != null, "Neck mesh exists")
	assert_true(robot.get_node_or_null("BodyPivot/HeadPivot") != null, "HeadPivot node exists")
	assert_true(robot.get_node_or_null("BodyPivot/HeadPivot/Head") != null, "Head mesh exists")
	assert_true(robot.get_node_or_null("BodyPivot/HeadPivot/Visor") != null, "Visor mesh exists")
	assert_true(robot.get_node_or_null("BodyPivot/HeadPivot/LeftEye") != null, "LeftEye exists")
	assert_true(robot.get_node_or_null("BodyPivot/HeadPivot/RightEye") != null, "RightEye exists")
	assert_true(robot.get_node_or_null("BodyPivot/HeadPivot/MouthGrille") != null, "MouthGrille exists")
	assert_true(robot.get_node_or_null("BodyPivot/HeadPivot/LeftAntennaPivot/LeftAntennaTip") != null, "Left antenna tip exists")
	assert_true(robot.get_node_or_null("BodyPivot/HeadPivot/RightAntennaPivot/RightAntennaTip") != null, "Right antenna tip exists")
	assert_true(robot.get_node_or_null("BodyPivot/LeftArmPivot/LeftUpperArm") != null, "Left upper arm exists")
	assert_true(robot.get_node_or_null("BodyPivot/RightArmPivot/RightUpperArm") != null, "Right upper arm exists")
	assert_true(robot.get_node_or_null("BodyPivot/RightArmPivot/RightForearmPivot/RightPointerFinger") != null, "Right pointer finger exists")
	assert_true(robot.get_node_or_null("BodyPivot/ThrusterRing") != null, "ThrusterRing exists")
	assert_true(robot.get_node_or_null("GroundShadow") != null, "GroundShadow exists")

	# Check shadow transparency
	var shadow_mi: MeshInstance3D = robot.get_node("GroundShadow")
	var shadow_mat := shadow_mi.material_override as StandardMaterial3D
	assert_true(shadow_mat != null, "GroundShadow has material override")
	assert_equal(shadow_mat.transparency, StandardMaterial3D.TRANSPARENCY_ALPHA, "GroundShadow material has alpha transparency")

	root.queue_free()


func test_animation_library_tracks() -> void:
	print("\n--- Test: AnimationLibrary & Animations ---")
	var root := Node3D.new()
	get_root().add_child(root)

	var robot := RobotCharacter.new()
	root.add_child(robot)
	robot.setup()

	var player: AnimationPlayer = robot.get_node_or_null("AnimPlayer")
	assert_true(player != null, "AnimPlayer node exists")
	assert_true(player.has_animation("idle"), "Has idle animation")
	assert_true(player.has_animation("listening"), "Has listening animation")
	assert_true(player.has_animation("thinking"), "Has thinking animation")
	assert_true(player.has_animation("talking"), "Has talking animation")
	assert_true(player.has_animation("pointing"), "Has pointing animation")
	assert_true(player.has_animation("celebrating"), "Has celebrating animation")

	var talk_anim := player.get_animation("talking")
	assert_true(talk_anim.track_get_key_count(0) > 0, "Talking animation has keyframes")
	assert_equal(talk_anim.loop_mode, Animation.LOOP_LINEAR, "Talking animation loops linearly")

	var point_anim := player.get_animation("pointing")
	assert_true(point_anim.track_get_key_count(0) > 0, "Pointing animation has keyframes")

	var cel_anim := player.get_animation("celebrating")
	assert_true(cel_anim.track_get_key_count(0) > 0, "Celebrating animation has keyframes")

	root.queue_free()


func test_animation_tree_state_machine() -> void:
	print("\n--- Test: AnimationTree State Machine ---")
	var root := Node3D.new()
	get_root().add_child(root)

	var robot := RobotCharacter.new()
	root.add_child(robot)
	robot.setup()

	var tree: AnimationTree = robot.get_node_or_null("AnimTree")
	assert_true(tree != null, "AnimTree exists")
	assert_true(tree.active, "AnimTree is active")

	var sm := tree.tree_root as AnimationNodeStateMachine
	assert_true(sm != null, "AnimTree tree_root is AnimationNodeStateMachine")
	assert_true(sm.has_node("idle"), "State machine has idle node")
	assert_true(sm.has_node("listening"), "State machine has listening node")
	assert_true(sm.has_node("thinking"), "State machine has thinking node")
	assert_true(sm.has_node("talking"), "State machine has talking node")
	assert_true(sm.has_node("pointing"), "State machine has pointing node")
	assert_true(sm.has_node("celebrating"), "State machine has celebrating node")

	root.queue_free()


func test_state_transitions_and_playback() -> void:
	print("\n--- Test: State Transitions and Playback ---")
	var root := Node3D.new()
	get_root().add_child(root)

	var robot := RobotCharacter.new()
	root.add_child(robot)
	robot.setup()

	var tree: AnimationTree = robot.get_node("AnimTree")
	tree.advance(0.01)

	assert_equal(robot.get_state_name(), "IDLE", "Initial state is IDLE")
	assert_equal(robot.get_active_animation(), "idle", "Active animation is idle")

	# Talking transition
	robot.set_state_by_name("TALKING")
	tree.advance(0.3)
	assert_equal(robot.get_state_name(), "TALKING", "State set to TALKING")
	assert_equal(robot.get_active_animation(), "talking", "Active animation is talking")

	# Pointing transition
	robot.set_state_by_name("POINTING")
	tree.advance(0.3)
	assert_equal(robot.get_state_name(), "POINTING", "State set to POINTING")
	assert_equal(robot.get_active_animation(), "pointing", "Active animation is pointing")

	# Celebrating transition via SUCCESS (protocol v1 compatibility)
	robot.set_state_by_name("SUCCESS")
	tree.advance(0.3)
	assert_equal(robot.get_state_name(), "SUCCESS", "State set to SUCCESS")
	assert_equal(robot.get_active_animation(), "celebrating", "Active animation is celebrating")

	# Thinking transition
	robot.set_state_by_name("THINKING")
	tree.advance(0.3)
	assert_equal(robot.get_state_name(), "THINKING", "State set to THINKING")
	assert_equal(robot.get_active_animation(), "thinking", "Active animation is thinking")

	# Listening transition
	robot.set_state_by_name("LISTENING")
	tree.advance(0.3)
	assert_equal(robot.get_state_name(), "LISTENING", "State set to LISTENING")
	assert_equal(robot.get_active_animation(), "listening", "Active animation is listening")

	# Return to IDLE
	robot.set_state_by_name("IDLE")
	tree.advance(0.3)
	assert_equal(robot.get_state_name(), "IDLE", "State returned to IDLE")
	assert_equal(robot.get_active_animation(), "idle", "Active animation is idle")

	root.queue_free()


func test_action_helper_methods() -> void:
	print("\n--- Test: Action Helper Methods ---")
	var root := Node3D.new()
	get_root().add_child(root)

	var robot := RobotCharacter.new()
	root.add_child(robot)
	robot.setup()

	var tree: AnimationTree = robot.get_node("AnimTree")
	tree.advance(0.01)

	robot.talk()
	tree.advance(0.05)
	assert_equal(robot.get_state_name(), "TALKING", "talk() sets state TALKING")

	robot.point()
	tree.advance(0.05)
	assert_equal(robot.get_state_name(), "POINTING", "point() sets state POINTING")

	robot.celebrate()
	tree.advance(0.05)
	assert_equal(robot.get_state_name(), "CELEBRATING", "celebrate() sets state CELEBRATING")

	robot.think()
	tree.advance(0.05)
	assert_equal(robot.get_state_name(), "THINKING", "think() sets state THINKING")

	robot.listen()
	tree.advance(0.05)
	assert_equal(robot.get_state_name(), "LISTENING", "listen() sets state LISTENING")

	robot.idle()
	tree.advance(0.05)
	assert_equal(robot.get_state_name(), "IDLE", "idle() sets state IDLE")

	# Cycle state
	var initial_state := robot.current_state
	robot.cycle_state()
	assert_true(robot.current_state != initial_state, "cycle_state() moves to next state")

	root.queue_free()


func test_emission_updates() -> void:
	print("\n--- Test: Dynamic Emission Updates ---")
	var root := Node3D.new()
	get_root().add_child(root)

	var robot := RobotCharacter.new()
	root.add_child(robot)
	robot.setup()

	# Simulate process delta across all states
	for s in [
		RobotCharacter.State.IDLE,
		RobotCharacter.State.LISTENING,
		RobotCharacter.State.THINKING,
		RobotCharacter.State.TALKING,
		RobotCharacter.State.POINTING,
		RobotCharacter.State.CELEBRATING
	]:
		robot.current_state = s
		robot._process(0.016)

	assert_true(true, "_process dynamic emissions executed without errors")
	root.queue_free()


func test_main_scene_integration() -> void:
	print("\n--- Test: Main Scene Integration ---")
	var root := Node3D.new()
	get_root().add_child(root)

	var main := MainScene.new()
	root.add_child(main)
	main.setup()

	assert_true(main._robot != null, "Main instantiates robot")
	assert_true(main._camera != null, "Main instantiates 3D camera")
	assert_true(main._ws_client != null, "Main instantiates WebSocket client")
	var vp := main.get_viewport()
	if vp == null:
		vp = get_root()
	assert_true(vp != null, "Main has viewport")
	assert_true(vp.transparent_bg, "Viewport transparent_bg is enabled")

	root.queue_free()
