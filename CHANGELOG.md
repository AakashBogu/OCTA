# OCTA — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> This project does not yet follow [Semantic Versioning](https://semver.org/) because no application code has been released.

---

## [Unreleased]

### Added

- **Phase 2.3 — Godot Process Manager (2026-11-01)**
  - `src/octa/process/manager.py` — `GodotProcessManager` QObject subclass managing Godot runtime lifecycle via PySide6 `QProcess`. Supports pre-flight path validation, asynchronous launch with explicit arguments (`--path <dir>`, `--headless`, `extra_args`), stdout/stderr streaming via Qt signals, two-stage shutdown (graceful terminate request + single-shot timeout force-kill), asynchronous restart sequencing (`_pending_restart`), signal-finalized cleanup (`cleanup()`), and process-generation token tracking (`_generation`) to prevent stale shutdown timeouts from affecting newer process instances across lifecycles.
  - `src/octa/process/state.py` — Strongly-typed `ProcessState` enum defining `STOPPED`, `STARTING`, `RUNNING`, `STOPPING`, `CRASHED`, and `ERROR` lifecycle states.
  - `src/octa/process/exceptions.py` — Dedicated exception hierarchy: `ProcessError`, `GodotNotFoundError`, `GodotProjectNotFoundError`, `ProcessAlreadyRunningError`, and `ProcessLaunchError`.
  - `src/octa/process/validator.py` — `validate_godot_environment` verifying that Godot binary exists and is executable, and validating project directory and `project.godot` presence.
  - `src/octa/process/__init__.py` — Clean public exports for process management subsystem.
  - `tests/unit/test_godot_manager.py` — 37 unit tests covering initial state, asynchronous launch, headless flag, argument construction, stdout/stderr signal dispatch, normal/crash exit handling, graceful stop, force-kill timeout expiration, stale timeout protection across generations, restart sequencing, cleanup idempotency, and exception handling using `MockQProcess`.
  - `tests/unit/test_process_validator.py` — 22 unit tests validating executable and project path scenarios.
  - `tests/integration/test_godot_process_integration.py` — 5 real-binary integration tests validating headless startup, stdout capture, UTF-8 decoding, path validation errors, stop timeout and force kill on Windows, and cleanup against actual installed Godot 4.7.2 console binary (`4.7.2.stable.official.ed1daf0bf`).

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

- `README.md` — Updated to reflect Phase 2.3 completion, documented Godot process manager, updated project tree, and verified total test count (85 tests).
- `ROADMAP.md` — Marked Milestone 2.3 completed with all sub-tasks checked.
- `CHANGELOG.md` — Documented Phase 2.3 implementation and testing under `[Unreleased]`.
- `docs/PROGRESS.md` — Appended task log for Milestone 2.3 (Godot Process Manager implementation, lifecycle audit, and real Godot integration tests).
- `docs/architecture.md` — Added detailed architecture documentation for the Godot process manager subsystem, state machine, lifecycle semantics, Windows limitations, and testing results.

- **Phase 0.1 — Project Foundation (2026-10-28)**
  - `README.md` — Project overview, goals, proposed architecture, and roadmap summary.
  - `ROADMAP.md` — Full milestone plan from Phase 0 through Phase 6.
  - `CHANGELOG.md` — This changelog file.
  - `PROJECT_RULES.md` — Development rules and mandatory documentation workflow.
  - `docs/architecture.md` — Detailed architecture documentation for the proposed system.
  - `docs/PROGRESS.md` — Task-by-task progress log.
  - `.gitignore` — Git ignore rules for Python, Godot, IDE, and OS files.
  - `.env.example` — Template for environment variables.
