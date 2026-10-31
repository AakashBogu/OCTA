# OCTA — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> This project does not yet follow [Semantic Versioning](https://semver.org/) because no application code has been released.

---

## [Unreleased]

### Added

- **Phase 1.0 — Godot 3D Character Prototype (2026-10-29)**
  - `godot/scenes/main.tscn` — Main 3D scene with camera, lighting, environment, robot character, and HUD overlay.
  - `godot/scripts/robot_character.gd` — `RobotCharacter` class: builds a friendly robot from primitive meshes (spheres, capsules, cylinders), idle animation (body bob, head sway, arm swing), and a four-state state machine (IDLE, LISTENING, THINKING, SUCCESS) with smooth colour/emission transitions.
  - `godot/scripts/main_scene.gd` — Scene setup: camera, two-point lighting (warm key + cool fill), dark background, floor plane, and a CanvasLayer HUD for state display and keyboard hints.
  - `godot/project.godot` — Updated to set `run/main_scene` to `res://scenes/main.tscn`.

### Fixed

- **Phase 1.0 — Godot 4.7.2 Compatibility & Parser Fixes (2026-10-29)**
  - `godot/scripts/main_scene.gd` — Corrected invalid `Environment.TONE_MAP_FILMIC` reference to supported Godot 4.7.2 enum `Environment.TONE_MAPPER_FILMIC`.
  - `godot/scripts/main_scene.gd` — Added camera to scene tree before calling `look_at()` to avoid node-not-in-tree runtime error.
  - `godot/scripts/robot_character.gd` — Added guard in `_process()` to prevent execution before node initialization completes.

### Changed

- `README.md` — Updated to reflect Phase 1 prototype status, added "What Works Now" section, run instructions, and updated project structure.
- `ROADMAP.md` — Marked Phase 0.1, 0.2 (Godot install), and Phase 1.0 tasks as complete. Reorganised phases to match actual implementation order. Added Phase 7 for advanced character work.
- `docs/architecture.md` — Added Phase 1 scene/script architecture section documenting the actual implemented structure.
- `docs/PROGRESS.md` — Added Phase 1 progress entry with files changed, validation results, and next steps.

- **Phase 0.1 — Project Foundation (2026-10-28)**
  - `README.md` — Project overview, goals, proposed architecture, and roadmap summary.
  - `ROADMAP.md` — Full milestone plan from Phase 0 through Phase 6.
  - `CHANGELOG.md` — This changelog file.
  - `PROJECT_RULES.md` — Development rules and mandatory documentation workflow.
  - `docs/architecture.md` — Detailed architecture documentation for the proposed system.
  - `docs/PROGRESS.md` — Task-by-task progress log.
  - `.gitignore` — Git ignore rules for Python, Godot, IDE, and OS files.
  - `.env.example` — Template for environment variables.
