# OCTA — Architecture Documentation

> **Status:** Phase 1 (Godot character prototype) and Phase 2 Milestones 2.1 & 2.2 (Python application foundation, configuration, and logging) are implemented. All other components remain **proposed**.
>
> **Last updated:** 2026-11-01

---

## Table of Contents

- [System Overview](#system-overview)
- [Component Architecture](#component-architecture)
- [Communication Protocol](#communication-protocol)
- [Data Flow](#data-flow)
- [Technology Choices and Rationale](#technology-choices-and-rationale)
- [Security Considerations](#security-considerations)
- [Open Questions](#open-questions)
- [Phase 1 — Implemented Scene Architecture](#phase-1--implemented-scene-architecture)
- [Phase 2 — Implemented Python Architecture (Milestones 2.1 & 2.2)](#phase-2--implemented-python-architecture-milestones-21--22)

---

## System Overview

OCTA is designed as a **dual-process desktop application** for Windows:

1. **Python Process** — Handles application logic, AI orchestration, screen capture, voice I/O, and the system tray UI.
2. **Godot Process** — Renders the 3D animated companion character in a transparent overlay window.

The two processes communicate over a **local WebSocket connection** (bound to `localhost` only). This separation allows each process to use the best technology for its role while remaining loosely coupled.

```
┌──────────────────────────────────────────────────────────────┐
│                     Windows Desktop                          │
│                                                              │
│  ┌────────────────────┐         ┌─────────────────────────┐  │
│  │   Python Process   │◄──WS──►│   Godot 4.x Process     │  │
│  │                    │         │                         │  │
│  │  ┌──────────────┐  │         │  ┌───────────────────┐  │  │
│  │  │ Task Engine  │  │         │  │ Character Scene   │  │  │
│  │  ├──────────────┤  │         │  │ (3D model, anims) │  │  │
│  │  │ AI Pipeline  │  │         │  ├───────────────────┤  │  │
│  │  ├──────────────┤  │         │  │ Overlay Renderer  │  │  │
│  │  │ Screen Cap   │  │         │  │ (arrows, highlights│  │  │
│  │  ├──────────────┤  │         │  │  tooltips)        │  │  │
│  │  │ Voice I/O    │  │         │  ├───────────────────┤  │  │
│  │  ├──────────────┤  │         │  │ WebSocket Client  │  │  │
│  │  │ PySide6 UI   │  │         │  │ (GDScript)        │  │  │
│  │  ├──────────────┤  │         │  └───────────────────┘  │  │
│  │  │ WS Server    │  │         │                         │  │
│  │  └──────────────┘  │         └─────────────────────────┘  │
│  └────────────────────┘                                      │
└──────────────────────────────────────────────────────────────┘
```

---

## Component Architecture

### Python Process

| Component | Responsibility | Planned Technology |
|---|---|---|
| **Task Engine** | Defines and executes tutorial steps; tracks user progress | Custom Python module |
| **AI Pipeline** | Sends prompts to LLM APIs; parses and applies responses | LLM client library (TBD) |
| **Screen Capture** | Captures screen regions for progress verification | Win32 APIs or `mss` library (TBD) |
| **Voice I/O** | Speech-to-text input and text-to-speech output | STT/TTS engines (TBD) |
| **PySide6 UI** | System tray icon, settings dialogs, notification overlays | PySide6 |
| **WebSocket Server** | Hosts the local WS endpoint for Godot communication | `websockets` or `aiohttp` (TBD) |

### Godot Process

| Component | Responsibility | Planned Technology |
|---|---|---|
| **Character Scene** | 3D model with skeletal animations (idle, talk, point, emote) | Godot 4.x scene + AnimationTree |
| **Overlay Renderer** | Draws highlights, arrows, and tooltips over the desktop | Godot 2D overlay layer |
| **WebSocket Client** | Connects to the Python WS server; sends/receives JSON messages | GDScript WebSocketPeer |
| **State Machine** | Manages character emotional and behavioral states | GDScript state machine |

---

## Communication Protocol

### Transport

- **Protocol:** WebSocket (RFC 6455)
- **Binding:** `ws://127.0.0.1:<port>` (localhost only — no network exposure)
- **Port:** Configurable via environment variable; default TBD
- **Message format:** JSON

### Message Structure (proposed)

```json
{
  "type": "command | event | response",
  "action": "string — the specific action name",
  "payload": { },
  "timestamp": "ISO 8601",
  "id": "uuid — for request/response correlation"
}
```

### Example Messages (proposed)

**Python → Godot: Play animation**
```json
{
  "type": "command",
  "action": "play_animation",
  "payload": { "animation": "point_right", "speed": 1.0 },
  "timestamp": "2026-10-28T12:00:00Z",
  "id": "a1b2c3d4"
}
```

**Godot → Python: Animation completed**
```json
{
  "type": "event",
  "action": "animation_complete",
  "payload": { "animation": "point_right" },
  "timestamp": "2026-10-28T12:00:01Z",
  "id": "a1b2c3d4"
}
```

---

## Data Flow

### Tutorial Execution Flow (proposed)

```
1. User requests a tutorial (via UI or voice)
       │
       ▼
2. Task Engine loads tutorial definition
       │
       ▼
3. Task Engine sends current step instruction to:
   ├── AI Pipeline → generates natural language instruction
   ├── Godot (via WS) → character speaks/points/animates
   └── Voice I/O → speaks the instruction aloud
       │
       ▼
4. User performs the action on their desktop
       │
       ▼
5. Screen Capture takes a screenshot of the relevant region
       │
       ▼
6. AI Pipeline analyzes the screenshot to verify completion
       │
       ▼
7. If step is verified:
   ├── Task Engine advances to next step → go to step 3
   └── Godot (via WS) → character celebrates / confirms
   
   If step is NOT verified:
   ├── AI Pipeline generates corrective guidance
   └── Godot (via WS) → character shows encouragement
```

---

## Technology Choices and Rationale

| Choice | Rationale |
|---|---|
| **Python** | Rich AI/ML ecosystem; mature libraries for screen capture, voice, and WebSocket; large contributor community. |
| **PySide6** | Official Qt for Python bindings; native-looking Windows UI; supports system tray and overlay windows; LGPL license. |
| **Godot 4.x** | Open-source game engine; excellent 3D rendering; supports transparent borderless windows; GDScript is approachable; active community. |
| **WebSocket (localhost)** | Low-latency bidirectional communication; well-supported in both Python and Godot; simple to implement; no external network exposure. |
| **Dual-process architecture** | Isolates rendering from logic; allows independent development and testing of each component; prevents a crash in one from taking down the other. |

---

## Security Considerations

- **Local-only communication:** The WebSocket server must bind exclusively to `127.0.0.1` to prevent external access.
- **No secrets in code:** API keys and tokens must be stored in `.env` files (excluded from Git via `.gitignore`).
- **Screen capture privacy:** The application captures screen content; users must be clearly informed and give consent.
- **LLM data handling:** Understand and document what data is sent to external LLM APIs.

---

## Open Questions

These decisions are deferred to future phases:

1. **Which LLM provider(s)?** OpenAI, Anthropic, local models, or a pluggable interface?
2. **Screen capture library?** `mss`, `pyautogui`, Win32 API directly, or a combination?
3. **Voice engines?** Which STT and TTS libraries or services to use?
4. **Godot export format?** Ship as an exported Godot binary, or embed Godot in the Python process?
5. **Tutorial format?** How should tutorials be defined — YAML, JSON, a custom DSL, or generated by AI?
6. **Distribution?** Installer (MSI, NSIS), portable zip, or Microsoft Store?
7. **Update mechanism?** Auto-update, manual download, or package manager?

---

## Phase 1 — Implemented Scene Architecture

The following describes the **actual** Godot project structure as implemented in Phase 1. This is a proof-of-concept prototype using placeholder geometry.

### Scene Tree

```
Main (Node3D)                          ← main_scene.gd
├── Camera3D                           ← Positioned at (0, 0.8, 2.2), FOV 40°, looking at robot
├── DirectionalLight3D (key)           ← Warm white, shadow enabled, -40°/30° rotation
├── DirectionalLight3D (fill)          ← Cool blue-white, no shadow, -20°/-60° rotation
├── WorldEnvironment                   ← Dark blue-grey background, filmic tonemapping
├── MeshInstance3D (floor)             ← PlaneMesh 6×6, dark matte surface
├── RobotCharacter (Node3D)            ← robot_character.gd (class_name RobotCharacter)
│   ├── MeshInstance3D (body)          ← CapsuleMesh, soft blue
│   ├── Node3D (head_pivot)            ← Rotation pivot for head sway
│   │   ├── MeshInstance3D (head)      ← SphereMesh, lighter blue
│   │   ├── MeshInstance3D (left_eye)  ← SphereMesh, emissive, changes colour per state
│   │   ├── MeshInstance3D (right_eye) ← SphereMesh, emissive, changes colour per state
│   │   ├── MeshInstance3D (antenna)   ← CylinderMesh, silver
│   │   └── MeshInstance3D (ant. tip)  ← SphereMesh, red, emissive pulse per state
│   ├── MeshInstance3D (left_arm)      ← CapsuleMesh, darker blue
│   ├── MeshInstance3D (right_arm)     ← CapsuleMesh, darker blue
│   ├── MeshInstance3D (left_leg)      ← CylinderMesh, darker blue
│   └── MeshInstance3D (right_leg)     ← CylinderMesh, darker blue
└── CanvasLayer (HUD, layer 10)
    ├── Label (state display)          ← Top-left, shows current state name
    └── Label (keyboard hints)         ← Bottom-left, shows control instructions
```

### State Machine

The `RobotCharacter` uses a simple enum-based state machine:

| State | Eye Colour | Eye Emission | Antenna Tip | Visual Cue |
|---|---|---|---|---|
| IDLE | Near-white | 0.4 | Off | Neutral, calm |
| LISTENING | Soft green | 0.8 | Slow pulse | Attentive |
| THINKING | Amber | 0.6 | Fast pulse | Processing |
| SUCCESS | Bright green | 1.2 | Solid glow | Celebration |

State transitions use `lerpf` / `Color.lerp` for smooth visual blending.

### Scripts

| Script | Class | Responsibility |
|---|---|---|
| `scripts/robot_character.gd` | `RobotCharacter` | Builds robot mesh hierarchy, runs idle animation, manages state machine and visual transitions |
| `scripts/main_scene.gd` | *(anonymous)* | Creates camera, lights, environment, floor, robot instance, and HUD; handles keyboard input for state switching |

### Design Decisions

- **Procedural construction:** All geometry is created in `_ready()` via code, not in the scene editor. This keeps the `.tscn` file minimal and the geometry easy to adjust parametrically.
- **No AnimationPlayer:** Idle motion uses `_process()` with `sin()` oscillators. This avoids the complexity of animation tracks for what is essentially placeholder geometry.
- **State via property setter:** The `current_state` property uses a setter to emit `state_changed` and reset `_state_time`, keeping the API simple.
- **GL Compatibility renderer:** Chosen for broad GPU compatibility in a prototype. Can be upgraded later.


---

## Phase 2 — Implemented Python Architecture (Milestones 2.1 & 2.2)

The following describes the **actual** Python application architecture implemented in Phase 2 Milestones 2.1 and 2.2.

### Package Layout (`src/octa/`)

OCTA uses a modern `src` layout compliant with PEP 621 / PEP 517 packaging standards:

```
src/octa/
├── __init__.py                # Package version (__version__ = "0.1.0") and exports
├── __main__.py                # CLI entry point: python -m octa [--version | -V]
└── core/                      # Core application services
    ├── __init__.py            # Central exports (AppConfig, setup_logging, etc.)
    ├── config.py              # Configuration dataclasses and environment parsing
    └── logging.py             # Console and rotating file logging infrastructure
```

### Configuration Subsystem (`octa.core.config`)

Configuration is managed via the strongly-typed `AppConfig` frozen dataclass:

- **Fields & Types:**
  - `app_name: str` (default: `"OCTA"`)
  - `env: str` (default: `"production"`)
  - `app_dir: Path` (default: `%LOCALAPPDATA%\OCTA`)
  - `log_dir: Path` (default: `{app_dir}\logs`)
  - `log_level: str` (default: `"INFO"`, validated against `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
  - `log_to_file: bool` (default: `True`)
  - `log_to_console: bool` (default: `True`)
  - `log_max_bytes: int` (default: `5242880` / 5 MB)
  - `log_backup_count: int` (default: `3`)
  - `godot_executable: Path | None` (default: `None`)
  - `godot_project_dir: Path` (default: `Path("godot")`)
  - `godot_auto_start: bool` (default: `False`)
  - `start_minimized: bool` (default: `False`)

#### Configuration Precedence

When loaded via `AppConfig.load(...)`, values are resolved with the following priority (lowest to highest):

1. **Dataclass Defaults:** Built-in default values.
2. **`.env` File:** Parsed using `python-dotenv` (`dotenv_values`) without polluting global process environment variables.
3. **Environment Variables:** Process environment variables prefixed with `OCTA_` (e.g. `OCTA_LOG_LEVEL`, `OCTA_APP_DIR`, `OCTA_GODOT_PATH`).
4. **Explicit Keyword Overrides:** Arguments passed directly to `AppConfig.load(...)`, enabling full isolation in automated tests.

#### Windows Path Resolution

- Application data defaults to `%LOCALAPPDATA%\OCTA` using `os.environ.get("LOCALAPPDATA")`.
- If `LOCALAPPDATA` is not defined, it falls back to `Path.home() / "AppData" / "Local" / "OCTA"`.
- Log files default to `{app_dir}\logs\octa.log`.
- No directories are created automatically during configuration loading; directories are created on demand when logging starts.

### Logging Subsystem (`octa.core.logging`)

- **Dual Handlers:**
  1. `StreamHandler` (Console): Human-readable format: `%(asctime)s [%(levelname)-7s] [%(name)s] %(message)s`.
  2. `RotatingFileHandler` (File): Detailed location format: `%(asctime)s [%(levelname)-7s] [%(name)s] (%(filename)s:%(lineno)d) %(message)s`. Rotates at `log_max_bytes` keeping `log_backup_count` backups.
- **Duplicate Prevention:**
  - `setup_logging()` removes and closes any existing octa-managed handlers prior to attaching new ones, ensuring idempotent calls without duplicate log output.
- **Windows File Safety (`shutdown_logging`):**
  - Explicitly flushes and closes file handles on the target logger. This avoids Windows file-locking issues when temporary directories are cleaned up in automated tests.

### Component Implementation Status

| Component | Status | Implementation Details |
|---|---|---|
| Godot Character Scene | ✅ Implemented (Phase 1) | `godot/` 3D character, idle animation, 4 states |
| Python Packaging & Entry Point | ✅ Implemented (Phase 2.1) | `pyproject.toml`, `src/octa/__main__.py` |
| Configuration Infrastructure | ✅ Implemented (Phase 2.2) | `octa.core.config.AppConfig` |
| Logging Infrastructure | ✅ Implemented (Phase 2.2) | `octa.core.logging.setup_logging` |
| Godot Process Manager | ⬜ Planned (Phase 2.3) | Launch, monitor, and graceful termination |
| PySide6 Desktop UI / Tray | ⬜ Planned (Phase 2.4) | System tray icon, diagnostic control window |
| WebSocket Communication Bridge | ⬜ Planned (Phase 3.0) | Localhost-only JSON RPC bridge |
| AI Pipeline & Screen Capture | ⬜ Planned (Phase 4.0) | Vision analysis, guidance generation |
| Voice Interaction (STT/TTS) | ⬜ Planned (Phase 5.0) | Speech synthesis and recognition |
