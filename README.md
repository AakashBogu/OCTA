# OCTA — On-screen Companion Tutor & Assistant

**Status:** 🟢 Phase 2 — Python Foundation & Process Management (in progress)

OCTA is an AI-powered desktop tutor for Windows. It guides users through desktop applications with an animated 3D companion, conversational step-by-step instructions, visual screen guidance, and progress verification.

> **Note:** OCTA is in active early development. The Phase 1 Godot 3D character prototype is implemented and runnable. The Phase 2 Python desktop application foundation (packaging, configuration, logging, process management, and test infrastructure) is implemented and verified against the real Godot 4.7.2 binary. PySide6 desktop UI (system tray / monitor window) and WebSocket inter-process communication are scheduled for upcoming milestones.

---

## Table of Contents

- [Project Goals](#project-goals)
- [What Works Now](#what-works-now)
- [Proposed Architecture](#proposed-architecture)
- [Roadmap](#roadmap)
- [Development Status](#development-status)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

---

## Project Goals

1. **Animated 3D Companion** — A friendly, animated character rendered via Godot 4.x that lives on the user's desktop and reacts to context.
2. **Conversational Instructions** — Natural-language guidance that walks users through tasks in any desktop application.
3. **Visual Screen Guidance** — On-screen highlights, arrows, and overlays that direct attention to the relevant UI elements.
4. **Progress Verification** — Automated checks (via screen capture and AI analysis) to confirm the user completed each step correctly.
5. **Voice Interaction** — Planned speech-to-text and text-to-speech for hands-free operation.

---

## What Works Now

OCTA currently includes three verified foundational components:

### 1. Phase 1 Godot 3D Character Prototype
- A simple 3D robot character built programmatically from Godot primitive meshes (spheres, capsules, cylinders).
- Warm, friendly placeholder design with head, large eyes, compact body, arms, legs, and antenna.
- Gentle idle animation: body bobbing, head sway, and arm movement.
- Four character states with distinct visual feedback: **IDLE**, **LISTENING**, **THINKING**, **SUCCESS**.
- Keyboard controls for state switching (Space/Enter to cycle, 1–4 for direct selection).
- Camera, two-point lighting, and dark background environment.

### 2. Phase 2 Python Application Foundation (Milestones 2.1 & 2.2)
- Modern `src/octa` Python 3.12 package structure defined via `pyproject.toml` (PEP 621).
- Minimal application CLI entry point supporting `python -m octa --version`.
- Strongly-typed application configuration (`AppConfig`) supporting defaults, `.env` file loading, OS environment variable overrides (`OCTA_*`), and safe Windows `%LOCALAPPDATA%` path resolution.
- Structured logging (`setup_logging`, `shutdown_logging`) with formatted console output and rotating file logging (`RotatingFileHandler`).
- Automated testing infrastructure using pytest with 21 foundation and config/logging unit tests and Ruff linting/formatting.

### 3. Phase 2.3 Godot Process Manager
- Asynchronous process lifecycle management via PySide6 `QProcess` (`GodotProcessManager`).
- Environment and path validation for Godot executable and project directory (`validate_godot_environment`).
- Strict lifecycle state machine (`ProcessState`: `STOPPED`, `STARTING`, `RUNNING`, `STOPPING`, `CRASHED`, `ERROR`).
- Asynchronous standard output and standard error stream capture with UTF-8 decoding.
- Two-stage process termination: graceful shutdown request (`WM_CLOSE` on Windows) with single-shot non-blocking timeout fallback to force kill (`TerminateProcess`).
- Asynchronous restart sequencing (`restart()`) ensuring replacement processes never launch before the previous process finishes.
- Asynchronous, idempotent cleanup semantics (`cleanup()`) using QProcess signals for lifecycle finalization.
- Monotonically increasing process-generation token (`_generation`) preventing stale shutdown timeouts from terminating replacement processes across lifecycles.
- Comprehensive test coverage: 59 unit tests (mocked `QProcess`) and 5 integration tests against real Godot 4.7.2 binary (85 automated tests total).
- **Process Termination Scope:** The integration tests confirm termination of the direct Godot process. Descendant-process containment is not guaranteed by the current implementation (kernel Job Objects and process-tree traversal are deferred).

> **Component Coupling Note:** Python now possesses full process management capabilities to launch, monitor, restart, and terminate the Godot companion runtime. PySide6 desktop UI (system tray / monitor window) in Milestone 2.4 and localhost WebSocket communication in Phase 3 will link user controls and bidirectional interaction.

---

## Proposed Architecture

OCTA is designed around two cooperating processes connected by a local WebSocket bridge:

```
┌─────────────────────────┐       WebSocket        ┌─────────────────────────┐
│   Python Application    │◄──────────────────────►│   Godot 4.x Character   │
│                         │    (localhost only)     │                         │
│  • AI orchestration     │                        │  • 3D animated companion│
│  • Screen capture       │                        │  • Visual overlays      │
│  • Voice I/O            │                        │  • Emotion / animation  │
│  • Task engine          │                        │    state machine        │
│  • PySide6 system UI    │                        │  • QProcess managed     │
│  • GodotProcessManager  │                        │                         │
└─────────────────────────┘                        └─────────────────────────┘
```

| Layer | Planned Technology | Purpose |
|---|---|---|
| Application logic | Python 3.12+ | Core orchestration, configuration, logging, task engine |
| Process management | PySide6 (`QProcess`) | Launching, monitoring, restarting, and terminating Godot runtime |
| Desktop UI | PySide6 | System tray, settings dialogs, companion monitor window |
| 3D Character | Godot 4.7.x | Animated companion rendering and interaction |
| IPC | WebSocket (localhost) | Real-time bidirectional messaging between Python and Godot |
| AI | LLM APIs (TBD) | Instruction generation, progress analysis |
| Screen capture | Platform APIs (TBD) | Capturing and analyzing on-screen content |
| Voice | STT / TTS engines (TBD) | Speech recognition and synthesis |

> All technology choices above are subject to evolution as development progresses.

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full milestone plan.

| Phase | Description | Status |
|---|---|---|
| 0.1 | Project foundation & documentation | ✅ Complete |
| 0.2 | Development environment setup | ✅ Complete |
| 1.0 | Godot 3D character prototype | ✅ Complete |
| 2.1 | Python packaging & project foundation | ✅ Complete |
| 2.2 | Configuration & logging infrastructure | ✅ Complete |
| 2.3 | Godot process manager | ✅ Complete |
| 2.4 | PySide6 desktop interface | ⬜ Planned |
| 3.0 | WebSocket bridge | ⬜ Planned |
| 4.0 | Screen capture & AI integration | ⬜ Planned |
| 5.0 | Voice interaction | ⬜ Planned |
| 6.0 | End-to-end tutorial engine | ⬜ Planned |

---

## Development Status

- **Current phase:** 2.0 — Core Python Application (Milestones 2.1, 2.2, and 2.3 completed)
- **What exists:**
  - Runnable Godot 4.7 project with a 3D robot character, idle animations, and a four-state state machine.
  - Python 3.12 package foundation with CLI version flag, configuration system, structured logging, and Godot process manager.
  - Full automated test suite (85 tests passing: 80 unit tests and 5 real Godot integration tests).
- **What does not exist yet:** PySide6 desktop UI/system tray, WebSocket bridge, AI integration, voice, screen capture, or installers.

---

## Getting Started

### Prerequisites

- **Python 3.12+** — [python.org](https://www.python.org/downloads/)
- **Godot 4.7.x** (4.7.2 Stable recommended) — download from [godotengine.org](https://godotengine.org/download/windows/)
- **Git** — for cloning the repository

### Python Application Setup

1. Open PowerShell and navigate to the project directory:
   ```powershell
   cd C:\Users\Admin\Desktop\OCTA
   ```

2. Create and activate a local virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the package in editable mode with development dependencies:
   ```powershell
   pip install -e ".[dev]"
   ```

4. Verify the Python package installation and version:
   ```powershell
   python -m octa --version
   # Expected output: octa 0.1.0
   ```

5. Run the automated test suite:
   ```powershell
   pytest
   ```

### Running the Godot Character Prototype

1. Open Godot 4.7 and import the project at `godot/project.godot`.
2. Press **F5** (or the Play ▶ button) to run the main scene.
3. Use **Space** or **Enter** to cycle through character states, or press **1–4** to select a state directly:
   - `1` — IDLE
   - `2` — LISTENING
   - `3` — THINKING
   - `4` — SUCCESS

### Project Rules

Read [PROJECT_RULES.md](PROJECT_RULES.md) for the mandatory development and documentation workflow.

---

## Project Structure

```
OCTA/
├── docs/
│   ├── architecture.md           # Architecture documentation
│   └── PROGRESS.md               # Task-by-task progress log
├── godot/                        # Godot 4.x project (character runtime)
│   ├── scenes/
│   │   └── main.tscn             # Main scene (camera, lights, robot, UI)
│   ├── scripts/
│   │   ├── main_scene.gd         # Scene setup, environment, and UI overlay
│   │   └── robot_character.gd    # Robot construction, animation, state machine
│   ├── icon.svg                  # Godot default project icon
│   └── project.godot             # Godot project configuration
├── src/
│   └── octa/                     # Python 3.12 desktop package
│       ├── __init__.py           # Package version (__version__) and exports
│       ├── __main__.py           # CLI entry point (python -m octa)
│       ├── core/                 # Core application services
│       │   ├── __init__.py       # Core exports
│       │   ├── config.py         # AppConfig dataclass & environment parsing
│       │   └── logging.py        # Structured console & rotating file logging
│       └── process/              # Godot process lifecycle management
│           ├── __init__.py       # Process module exports
│           ├── exceptions.py     # Process management exception hierarchy
│           ├── manager.py        # GodotProcessManager (PySide6 QProcess lifecycle)
│           ├── state.py          # ProcessState enum
│           └── validator.py      # Executable and project path validation
├── tests/                        # Automated test suite (85 tests passing)
│   ├── conftest.py               # Shared test fixtures & custom markers
│   ├── integration/              # Real binary integration tests
│   │   └── test_godot_process_integration.py  # Real Godot headless integration tests
│   └── unit/                     # Isolated unit tests (mocked doubles)
│       ├── test_config.py        # Configuration & precedence tests
│       ├── test_foundation.py    # Package & version CLI tests
│       ├── test_godot_manager.py # GodotProcessManager lifecycle & timer tests
│       ├── test_logging.py       # Log creation, rotation & handler tests
│       └── test_process_validator.py # Path validation unit tests
├── .env.example                  # Template for environment variables
├── .gitignore                    # Git ignore rules
├── CHANGELOG.md                  # Version changelog
├── PROJECT_RULES.md              # Development rules and workflow requirements
├── pyproject.toml                # Build configuration and dependency declarations
├── README.md                     # This file
├── requirements.txt              # Production dependency reference (-e .)
├── requirements-dev.txt          # Development dependency reference (-e .[dev])
└── ROADMAP.md                    # Milestone roadmap
```

---

## Contributing

OCTA is in early development. Contributions are welcome:

1. Read [PROJECT_RULES.md](PROJECT_RULES.md) for the documentation and commit workflow.
2. Open an issue on [GitHub](https://github.com/AakashBogu/OCTA/issues) to discuss ideas.
3. All pull requests must follow the documentation update rules in `PROJECT_RULES.md`.

---

## License

License TBD. To be determined before the first code release.
