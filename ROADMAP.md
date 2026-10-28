# OCTA — Roadmap

> **Last updated:** 2026-10-28
>
> All milestones below are **planned**. None have been completed unless explicitly marked.

---

## Phase 0 — Foundation

### Phase 0.1 — Project Foundation & Documentation *(current)*

- [x] Initialize Git repository
- [x] Create README.md with project overview
- [x] Create ROADMAP.md (this file)
- [x] Create CHANGELOG.md
- [x] Create PROJECT_RULES.md with documentation workflow
- [x] Create docs/architecture.md
- [x] Create docs/PROGRESS.md
- [x] Create .gitignore and .env.example

### Phase 0.2 — Development Environment Setup *(planned)*

- [ ] Define Python version and dependency management strategy (e.g., `pyproject.toml`, `requirements.txt`)
- [ ] Set up virtual environment tooling
- [ ] Install PySide6 and verify a minimal window launches
- [ ] Install Godot 4.x and verify editor runs
- [ ] Establish project linting and formatting (e.g., `ruff`, `black`)
- [ ] Add pre-commit hooks
- [ ] Verify development environment on a clean Windows machine

---

## Phase 1 — Core Python Application

### Phase 1.0 — Application Skeleton *(planned)*

- [ ] Create Python package structure
- [ ] Implement application entry point
- [ ] Build system tray integration with PySide6
- [ ] Create basic settings/configuration framework
- [ ] Add logging infrastructure
- [ ] Write initial unit tests

---

## Phase 2 — 3D Character

### Phase 2.0 — Godot Character Prototype *(planned)*

- [ ] Set up Godot 4.x project structure
- [ ] Create or integrate a 3D character model
- [ ] Implement basic idle, talking, and pointing animations
- [ ] Build transparent borderless window for desktop overlay
- [ ] Define character emotion/state machine

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

## Future Ideas *(not scheduled)*

- Plugin system for community-contributed tutorials
- Multi-monitor support
- Accessibility features (high contrast, screen reader compatibility)
- Localization / multi-language support
- Cloud sync for progress and preferences
