# OCTA — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> This project does not yet follow [Semantic Versioning](https://semver.org/) because no application code has been released.

---

## [Unreleased]

### Added

- **Phase 2.2 — Configuration & Logging Infrastructure (2026-11-01)**
  - `src/octa/core/config.py` — Strongly-typed `AppConfig` dataclass with multi-tier precedence (Defaults < .env < Environment Variables < Keyword Overrides), safe Windows `%LOCALAPPDATA%` path resolution, and `ConfigError` validation.
  - `src/octa/core/logging.py` — Structured console and rotating file logging (`setup_logging`), idempotent setup avoiding duplicate handlers, and Windows file lock release hooks (`shutdown_logging`).
  - `src/octa/core/__init__.py` — Central core exports.
  - `tests/unit/test_config.py` — 9 unit tests verifying configuration loading, precedence, and path resolution.
  - `tests/unit/test_logging.py` — 6 unit tests verifying log file creation, log levels, rotation, duplicate prevention, and error handling.

- **Phase 2.1 — Python Packaging & Project Foundation (2026-11-01)**
  - `pyproject.toml` — Modern PEP 621 packaging metadata, runtime and development dependencies, setuptools package discovery, pytest configuration, and Ruff linter/formatter rules.
  - `requirements.txt`, `requirements-dev.txt` — Authoritative references to `-e .` and `-e .[dev]`.
  - `src/octa/__init__.py` — Package root defining version (`0.1.0`) and exports.
  - `src/octa/__main__.py` — Application entry point supporting `python -m octa --version` and CLI execution.
  - `tests/conftest.py` — Shared pytest fixtures for temporary workspace isolation and logging handler cleanup.
  - `tests/unit/test_foundation.py` — Foundation tests for package structure, versioning, and CLI execution.

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

- `README.md` — Updated to reflect Phase 2 progress, added Python setup instructions, CLI usage, and updated project structure.
- `ROADMAP.md` — Marked Phase 2.1 and 2.2 completed; broken down Phase 2 into discrete milestones.
- `docs/PROGRESS.md` — Appended task logs for Milestones 2.1 and 2.2.
- `docs/architecture.md` — Documented Python application package structure, configuration precedence, and logging subsystem.

- **Phase 0.1 — Project Foundation (2026-10-28)**
  - `README.md` — Project overview, goals, proposed architecture, and roadmap summary.
  - `ROADMAP.md` — Full milestone plan from Phase 0 through Phase 6.
  - `CHANGELOG.md` — This changelog file.
  - `PROJECT_RULES.md` — Development rules and mandatory documentation workflow.
  - `docs/architecture.md` — Detailed architecture documentation for the proposed system.
  - `docs/PROGRESS.md` — Task-by-task progress log.
  - `.gitignore` — Git ignore rules for Python, Godot, IDE, and OS files.
  - `.env.example` — Template for environment variables.
