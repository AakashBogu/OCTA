# OCTA — Roadmap

> **Last updated:** 2026-11-01
>
> Milestones marked with [x] have been completed and verified.

---

## Phase 0 — Foundation

### Phase 0.1 — Project Foundation & Documentation *(complete)*

- [x] Initialize Git repository
- [x] Create README.md with project overview
- [x] Create ROADMAP.md (this file)
- [x] Create CHANGELOG.md
- [x] Create PROJECT_RULES.md with documentation workflow
- [x] Create docs/architecture.md
- [x] Create docs/PROGRESS.md
- [x] Create .gitignore and .env.example

### Phase 0.2 — Development Environment Setup *(complete)*

- [x] Install Godot 4.x and verify editor runs
- [x] Define Python version and dependency management strategy (`pyproject.toml`, `requirements.txt`)
- [x] Set up virtual environment tooling (`.venv`)
- [ ] Install PySide6 and verify a minimal window launches *(deferred to Phase 2.4)*
- [x] Establish project linting and formatting (`ruff`)
- [ ] Add pre-commit hooks
- [x] Verify development environment on a clean Windows machine

---

## Phase 1 — Godot 3D Character Prototype *(complete)*

### Phase 1.0 — Minimal Character Prototype

- [x] Set up Godot 4.x project structure (`godot/` directory with `project.godot`)
- [x] Create 3D robot character from built-in primitive meshes
- [x] Implement idle animation (body bob, head sway, arm movement)
- [x] Define character state machine (IDLE, LISTENING, THINKING, SUCCESS)
- [x] Add visual feedback per state (eye colour, emission, antenna pulse)
- [x] Add camera, lighting, and background environment
- [x] Add keyboard controls for state switching (Space/Enter to cycle, 1–4 direct)
- [x] Set main scene in project.godot

---

## Phase 2 — Core Python Application *(in progress)*

### Milestone 2.1 — Python Packaging & Project Foundation *(complete)*

- [x] Create modern `pyproject.toml` with Python 3.12-compatible packaging standards (PEP 621)
- [x] Establish `src/octa/` package structure and minimal entry point (`__init__.py`, `__main__.py`)
- [x] Add dependency declarations and requirements files
- [x] Configure pytest and Ruff in `pyproject.toml`
- [x] Add foundation tests and verify `python -m octa --version`

### Milestone 2.2 — Configuration & Logging Infrastructure *(complete)*

- [x] Implement strongly-typed `AppConfig` dataclass with defaults
- [x] Implement `.env` file loading and `OCTA_*` environment variable overrides
- [x] Implement Windows application-data and log directory resolution
- [x] Implement structured console and rotating file logging
- [x] Prevent duplicate handlers and provide clean Windows shutdown hooks
- [x] Write unit tests for configuration and logging (21 tests total)

### Milestone 2.3 — Godot Process Manager *(complete)*

- [x] Implement Godot executable discovery and path validation (`validate_godot_environment`)
- [x] Implement asynchronous Godot process launching and monitoring via PySide6 `QProcess` (`GodotProcessManager`)
- [x] Implement two-stage graceful shutdown with force-kill fallback and process-generation token protection
- [x] Implement stdout/stderr stream forwarding to logging and Qt signals
- [x] Implement asynchronous restart sequencing, cleanup semantics, and crash/error detection
- [x] Validate against real Godot 4.7.2 binary in headless mode with integration tests (85 tests passing total)

### Milestone 2.4 — PySide6 Desktop Interface *(planned)*

- [ ] Build system tray integration with PySide6 (`QSystemTrayIcon`)
- [ ] Create companion diagnostic and monitor window
- [ ] Connect UI controls to process manager

### Milestone 2.5 — Documentation & Phase 2 Wrap-up *(planned)*

- [ ] Complete Phase 2 verification and documentation sync

---

## Phase 3 — Communication Bridge

### Phase 3.0 — WebSocket Bridge *(planned)*

- [ ] Implement WebSocket server in Python
- [ ] Implement WebSocket client in Godot (GDScript)
- [ ] Define message protocol (JSON schema)
- [ ] Handle connection lifecycle (connect, reconnect, disconnect)
- [ ] End-to-end test: Python sends command → Godot plays animation

---

## Phase 4 — Intelligence Layer

### Phase 4.0 — Screen Capture & AI Integration *(planned)*

- [ ] Implement screen region capture
- [ ] Integrate LLM API for instruction generation
- [ ] Build task definition format (steps, expected outcomes)
- [ ] Implement progress verification via screen analysis
- [ ] Create visual overlay system (highlights, arrows)

---

## Phase 5 — Voice

### Phase 5.0 — Voice Interaction *(planned)*

- [ ] Integrate text-to-speech engine
- [ ] Integrate speech-to-text engine
- [ ] Connect voice pipeline to task engine
- [ ] Handle wake-word or push-to-talk activation

---

## Phase 6 — Integration

### Phase 6.0 — End-to-End Tutorial Engine *(planned)*

- [ ] Combine all subsystems into a cohesive tutorial flow
- [ ] Create sample tutorials for common applications
- [ ] User testing and feedback loop
- [ ] Performance optimization
- [ ] Installer / distribution packaging

---

## Phase 7 — Advanced Character *(planned)*

### Phase 7.0 — Enhanced Godot Character

- [ ] Build transparent borderless window for desktop overlay
- [ ] Create or integrate a higher-quality 3D character model
- [ ] Implement advanced animations (talking, pointing, celebrating)
- [ ] Add AnimationTree-based state transitions

---

## Future Ideas *(not scheduled)*

- Plugin system for community-contributed tutorials
- Multi-monitor support
- Accessibility features (high contrast, screen reader compatibility)
- Localization / multi-language support
- Cloud sync for progress and preferences
