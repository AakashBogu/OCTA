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
| **WebSocket Server** | Hosts the authenticated local endpoint for Godot communication | PySide6.QtWebSockets.QWebSocketServer |

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

- **Protocol:** WebSocket (RFC 6455), implemented with PySide6.QtWebSockets.QWebSocketServer.
- **Event loop:** The server and sessions are QObjects and rely on the application Qt event loop; they perform no blocking socket waits.
- **Binding:** ws://127.0.0.1:<port> only.
- **Port selection:** Prefer 8765; if unavailable, bind port 0 and report the selected OS-assigned port. Callers may request port 0 directly.
- **Client capacity:** One accepted client session at a time.
- **Authentication:** On each start, generate a 32-byte cryptographically random URL-safe bearer token. The client presents it in HELLO; compare with a constant-time comparison.
- **Timeouts:** Five-second WebSocket handshake and HELLO authentication deadlines; five-second heartbeat and shutdown acknowledgement intervals.
- **Size limits:** Qt rejects incoming frames/messages above 65,536 bytes, while ProtocolHandler independently limits encoded and decoded UTF-8 JSON messages to 65,536 bytes.

### Protocol v1 Envelope

Every message is a JSON object containing protocol_version, message_id, message_type, timestamp, correlation_id, and payload. The message_type selects one of the typed protocol payloads defined in octa.communication.messages.

- protocol_version must be strict SemVer and use supported major version 1.
- Each request receives a new message_id. A response sets correlation_id to the request's message_id; events without a matching request use no correlation_id.
- HELLO/WELCOME establish the session. PING/PONG check liveness. SET_STATE/SET_STATE_ACK command and acknowledge character state changes. STATE_CHANGED reports the client's state. SHUTDOWN/SHUTDOWN_ACK complete the graceful client shutdown handshake. ERROR carries a structured failure.
- The current milestone implements the Python server. The Godot client and end-to-end application wiring are not implemented yet.

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
| Godot Process Manager | ✅ Implemented (Phase 2.3) | `octa.process.manager.GodotProcessManager`, `QProcess` lifecycle, path validation |
| Python Qt WebSocket Server | ✅ Implemented (Phase 2.4.2) | Authenticated single-client protocol v1 server on IPv4 loopback |
| Godot WebSocket Client | ✅ Implemented (Phase 3.0) | GDScript `OctaWsClient` and `OctaProtocol` using `WebSocketPeer`, safe local handoff |
| PySide6 Desktop UI / Tray | ⬜ Planned (Phase 2.5) | System tray icon, diagnostic control window |
| AI Pipeline & Screen Capture | ⬜ Planned (Phase 4.0) | Vision analysis, guidance generation |
| Voice Interaction (STT/TTS) | ⬜ Planned (Phase 5.0) | Speech synthesis and recognition |

---

## Phase 2.3 — Implemented Godot Process Manager Architecture

The following describes the **actual** Godot process management architecture implemented in Phase 2 Milestone 2.3 under `src/octa/process/`.

### Subsystem Layout (`src/octa/process/`)

```
src/octa/process/
├── __init__.py                # Public exports (GodotProcessManager, ProcessState, exceptions)
├── exceptions.py              # Dedicated exception hierarchy
├── manager.py                 # GodotProcessManager (PySide6 QProcess lifecycle management)
├── state.py                   # ProcessState enum
└── validator.py               # Pre-flight executable and project path validation
```

### Core Responsibilities & Components

#### 1. Path Validation (`validator.py`)
- `validate_godot_environment(executable_path, project_dir)`:
  - Resolves executable path, checks file existence and executable permissions (`os.X_OK`).
  - Resolves project directory, confirms directory existence, and verifies `project.godot` is present.
  - Returns `tuple[Path, Path]` of fully resolved, normalized absolute paths.
  - Raises `GodotNotFoundError` or `GodotProjectNotFoundError` with descriptive error messages.

#### 2. Process Lifecycle State Machine (`state.py`)
Managed via the strongly-typed `ProcessState` enum:
- `STOPPED`: Process is not running and inactive. Initial state and target state after normal exit.
- `STARTING`: Launch initiated via `start()`, awaiting OS spawn and `QProcess.started` signal.
- `RUNNING`: Process is actively executing with a valid OS PID.
- `STOPPING`: Shutdown requested via `stop()`, awaiting process exit or timeout.
- `CRASHED`: Process terminated unexpectedly or exited with a non-zero code or `CrashExit`.
- `ERROR`: Startup validation failure or binary execution failure (`FailedToStart`).

State transitions emit the `state_changed(ProcessState)` Qt signal.

#### 3. QProcess Encapsulation & Asynchronous Execution (`manager.py`)
`GodotProcessManager` inherits from `QObject` and owns an internal `QProcess` instance:
- **Executable Selection:** Supports both GUI (`Godot_v4.7.2-stable_win64.exe`) and console (`Godot_v4.7.2-stable_win64_console.exe`) binaries. Headless execution is enabled via `headless=True` (appends `--headless`).
- **Explicit Arguments:** Prevents Windows command-line injection and quoting bugs by passing argument lists directly:
  `["--path", str(valid_proj)] + (["--headless"] if headless else []) + extra_args`.
- **Working Directory:** Set to the resolved project directory (`QProcess.setWorkingDirectory(str(valid_proj))`).
- **Asynchronous Standard I/O:** Listens to `readyReadStandardOutput` and `readyReadStandardError` signals, decodes bytes using UTF-8 (with error replacement), logs lines, and emits `stdout_received(str)` and `stderr_received(str)` non-blocking signals.
- **PID Reporting:** Records integer PID on `_on_started()` and emits `started(int)`. Resets PID to `None` upon confirmed process exit.

#### 4. Two-Stage Shutdown & Timeout Fallback
- **Stage 1 (Graceful Request):** `stop(timeout_ms=3000)` transitions state to `STOPPING` and calls `QProcess.terminate()`. On Windows, Qt attempts to post `WM_CLOSE` to the process.
- **Stage 2 (Force Kill Fallback):** `stop()` arms a single-shot non-blocking `QTimer` (`_shutdown_timer`). If the process does not finish within `timeout_ms`, `_on_shutdown_timeout` calls `QProcess.kill()` (`TerminateProcess` on Windows) and sets `_force_killed = True`.
- **Signal Finalization:** `_on_finished` captures the exit code and `ExitStatus`, cancels timers, clears the PID, emits `finished(exit_code, status_name)`, and sets final state (`STOPPED` if normal exit 0; `CRASHED` if non-zero exit or `CrashExit`).

#### 5. Process-Generation Token Protection
- A monotonically increasing counter (`self._generation: int = 0`) is incremented on every `start()`.
- When `stop()` arms the shutdown timer, it captures `_shutdown_timer_generation = self._generation`.
- `_on_shutdown_timeout(generation=None)` strictly verifies that the timer generation matches the active process generation. Stale timeouts from a prior process lifecycle (e.g. Process A stopped, finished, Process B started and entered STOPPING) are discarded without killing Process B.

#### 6. Asynchronous Restart Sequencing
- Calling `restart()` while `RUNNING` or `STARTING` sets `_pending_restart = True` and requests shutdown via `stop()`.
- The replacement process is **never** launched while the previous process remains active.
- When `_on_finished` fires, the manager resets `_pending_restart = False` and launches the replacement process with a new generation token.
- Multiple duplicate `restart()` calls while `STOPPING` are de-duplicated to exactly one replacement launch.
- If a startup error occurs (`FailedToStart`), `_pending_restart` is dropped immediately to prevent infinite error loops.

#### 7. Idempotent Cleanup Semantics
- `cleanup()` safely releases process and timer resources.
- Cancels active shutdown timers and clears pending restart flags.
- If a process is active, transitions to `STOPPING` and initiates `kill()`, preserving `pid` and postponing `STOPPED` transition until `_on_finished` confirms termination from the OS.
- If already inactive, immediately transitions to `STOPPED`.

---

## Windows Process Management Limitations & Findings

The following technical findings and limitations are documented from implementation and testing on Windows:

1. **`QProcess.terminate()` in Headless Mode:**
   - On Windows, `QProcess.terminate()` posts `WM_CLOSE` to top-level Win32 windows belonging to the target process.
   - Headless console processes (such as Godot running with `--headless`) do not create a Win32 GUI message loop. Consequently, `terminate()` does not cause headless console processes to exit gracefully.
   - The two-stage shutdown mechanism handles this by allowing the single-shot timeout timer to expire and issuing `kill()` (`TerminateProcess`), cleanly terminating the process.

2. **Process Tree & Descendant Management:**
   - The integration test confirms termination of the direct Godot process. Descendant-process containment is not guaranteed by the current implementation.
   - `QProcess` operates directly on the spawned process PID.
   - On Windows, terminating a parent process via `QProcess` does not inherently guarantee the termination of grandchild processes spawned by that child.
   - Kernel-level Windows Job Objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` provide native orphan prevention, but integrating them with PySide6 presents fundamental race conditions:
     1. PySide6 executes `CreateProcessW` internally in Qt C++ and only returns the integer PID, not the native process `HANDLE`.
     2. Calling `OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, ...)` in Python occurs asynchronously after the process has already begun executing, creating a window where child processes can spawn before assignment.
     3. If the process is launched within an existing Job Object (e.g. within certain IDEs, CI harnesses, or build tools) without nested job support or breakaway privileges, assignment fails with `ERROR_ACCESS_DENIED`.
   - Kernel Job Object binding is therefore deferred. Future multi-process management should leverage `psutil` process-tree traversal when needed.

3. **No Automatic Crash-Restart Loop:**
   - The process manager detects abnormal termination and marks the state as `CRASHED`.
   - It intentionally does **not** implement an unthrottled automatic restart loop. Restarts must be initiated by higher-level supervisory logic or user intervention, preventing runaway CPU thrashing if a corrupt asset or missing dependency causes persistent crashes.

---

## Testing Strategy & Results

The test suite enforces strict separation between unit tests and real-binary integration tests:

1. **Unit Tests (59 tests):**
   - `tests/unit/test_process_validator.py` (22 tests): Verifies path validation, missing binaries, missing project files, non-executable files, and directory checks.
   - `tests/unit/test_godot_manager.py` (37 tests): Uses `MockQProcess` to deterministically verify state transitions, signal dispatch, argument building, timeout expiration, stale timer rejection across generations, restart sequencing, cleanup idempotency, and exception handling without running binaries.
2. **Real Godot Integration Tests (5 tests):**
   - `tests/integration/test_godot_process_integration.py`: Validates headless startup, PID assignment, stdout streaming, UTF-8 decoding, banner validation, path errors, stop timeout force-kill on Windows, and cleanup against the actual installed Godot 4.7.2 console binary (`4.7.2.stable.official.ed1daf0bf`).
   - Integration tests are tagged with the `integration` marker and skipped automatically with a documented reason if the Godot binary is not found on the host machine.
3. **Total Test Suite:**
   - **85 tests passing** (80 unit tests + 5 real Godot integration tests) in ~2.4 seconds with zero warnings or failures.

## Phase 2.4.1 & 2.4.2 — Implemented Communication Server

The protocol schemas and Python server live under src/octa/communication/. The server exposes start(), stop(), set_state(), and request_shutdown() for higher-level application code. Construct it on the Qt application thread and keep the Qt event loop running.

### Session lifecycle

1. start() rotates the in-memory authentication token and listens on 127.0.0.1. Port 8765 is preferred; an occupied port triggers a port-0 fallback.
2. The first WebSocket client receives a five-second authentication deadline. Only a valid HELLO with a constant-time token match advances the session to AUTHENTICATED; WELCOME correlates to the HELLO message_id.
3. Additional clients are closed while one session is active. After disconnect or failure, the server returns to LISTENING if its listener remains open.
4. The session sends periodic PING messages and requires matching PONG correlation IDs. Heartbeat failure closes the session.
5. set_state() sends SET_STATE and tracks its message_id. SET_STATE_ACK must correlate to that ID and acknowledge the requested state.
6. request_shutdown() sends SHUTDOWN, pauses heartbeat traffic, and waits up to five seconds for a matching SHUTDOWN_ACK. A positive acknowledgement closes the session; a negative acknowledgement resumes the authenticated session.

### Security and operational limits

- Qt enforces the 65,536-byte incoming frame and message cap before the application receives the complete message. ProtocolHandler applies the same byte limit to incoming and outgoing JSON.
- The random token is available through the in-memory auth_token property for a future local handoff. It is not emitted in signals or logs. Passing it on a command line is unsafe because local process inspection can reveal arguments; the Godot launch handoff remains to be designed.
- The server does not launch Godot or create a client. This milestone only implements the server endpoint and protocol session handling.
- tests/unit/test_communication_server.py exercises loopback binding, fallback, authentication, client limits, protocol direction, correlation, timeouts, and transport size enforcement.

## Phase 3.0 — Implemented Godot WebSocket Client & End-to-End Bridge

The Godot client implementation lives under `godot/scripts/` (`ws_protocol.gd`, `ws_client.gd`, and integration in `main_scene.gd`). It connects the 3D character companion to the Python server across the authenticated protocol v1 bridge.

### Godot Architecture

1. **`OctaProtocol` (`godot/scripts/ws_protocol.gd`):**
   - Pure utility class for SemVer validation, 65,536-byte framing bounds, envelope formatting, and typed payload builders.
   - Enforces SemVer 2.0.0 major version 1 matching and UTF-8 JSON message limits.
   - Generates cryptographically secure 32-character hex message IDs via Godot's `Crypto.new().generate_random_bytes(16).hex_encode()`.
2. **`OctaWsClient` (`godot/scripts/ws_client.gd`):**
   - Scene-tree `Node` utilizing Godot 4's `WebSocketPeer` in the standard non-blocking `_process(delta)` polling loop.
   - Manages connection lifecycle (`CONNECTING`, `CONNECTED`, `AUTHENTICATED`, `CLOSING`, `CLOSED`).
   - Initiates handshake via `HELLO` upon socket connection and verifies correlated `WELCOME`.
   - Replies to server `PING` with matching `PONG` correlated to the `PING` `message_id`.
   - Emits signals for `state_change_requested`, `shutdown_requested`, `error_received`, and `connection_state_changed`.
3. **Character & Scene Integration (`godot/scripts/main_scene.gd`):**
   - Connects `OctaWsClient` to the 3D `RobotCharacter` and HUD overlay.
   - Dispatches incoming `SET_STATE` commands to the robot character, returns correlated `SET_STATE_ACK` and `STATE_CHANGED` events.
   - Notifies server of autonomous or manual user state changes made via the HUD or keyboard shortcuts.
   - Handles `SHUTDOWN` requests by sending `SHUTDOWN_ACK`, flushing the socket, and cleanly terminating the process.

### Safe Local Credential Handoff (`src/octa/communication/handoff.py`)

Passing authentication credentials in command-line arguments on Windows exposes secrets through:
- Task Manager and Sysinternals Process Explorer command-line columns.
- Windows Event Log: Security Audit Event 4688 (Process Creation with Command Line Logging).
- Local unprivileged process query tools (`wmic process get CommandLine`).

To prevent secret exposure, OCTA implements two secure in-memory and filesystem handoff paths:

1. **Process Environment Block (`prepare_process_environment`):**
   - The primary handoff mechanism.
   - Python sets `OCTA_WS_HOST`, `OCTA_WS_PORT`, and `OCTA_AUTH_TOKEN` directly on the child process using `QProcess.setProcessEnvironment(env)`.
   - On Windows, the environment block is passed directly to `CreateProcessW` in the child's virtual address space (PEB) and is inaccessible to unprivileged other users.
   - Environment variables are never logged and do not appear in command-line logs.
2. **Secure One-Time Temporary File (`write_handoff_file`, `read_handoff_file`):**
   - Created in the user's private `%TEMP%` directory with restricted file permissions (`0o600`).
   - Pointed to by `OCTA_HANDOFF_FILE`.
   - Godot reads the file once on initialization and immediately unlinks it from disk (`DirAccess.remove_absolute(...)`), minimizing lifetime on disk to milliseconds.

### Validation and Test Coverage

1. **Godot Protocol Test Suite (`godot/tests/test_ws_protocol.gd`):**
   - 13 headless tests covering SemVer validation, state checking, envelope generation, UTF-8 roundtrip encode/decode, 64 KiB boundary enforcement, malformed JSON handling, and handoff loading.
2. **Python Handoff Tests (`tests/unit/test_communication_handoff.py`):**
   - 19 unit tests validating parameter validation, environment preparation, handoff file creation/deletion, permission restrictions, and manager environment propagation.
3. **End-to-End Integration Tests (`tests/integration/test_end_to_end_bridge.py`):**
   - 4 real-binary tests against installed Godot 4.7.2 in headless mode verifying the full handshake, `SET_STATE` roundtrip state transitions, PING/PONG heartbeats, graceful shutdown coordination, file-based handoff, and unauthorized token rejection.
4. **Total Test Suite:**
   - 180 Python automated tests + 13 Godot protocol tests passing with zero failures.

## Phase 2.5 — Implemented PySide6 Desktop Interface

The desktop interface subsystem lives in `src/octa/desktop/` and orchestrates the application presentation, user controls, background services, and system tray integration on the Qt event loop.

```
┌────────────────────────────────────────────────────────────────────────┐
│                             OctaApplication                            │
│  • QApplication lifecycle                                              │
│  • Start minimized / Auto-start configuration                          │
│  • System tray availability detection & fallback                       │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
       ┌────────────────────────┐       ┌────────────────────────┐
       │     OctaSystemTray     │       │    DiagnosticWindow    │
       │  • Procedural icon     │       │  • Live status labels  │
       │  • Dynamic tooltip     │◄─────►│  • Control buttons     │
       │  • Context menu actions│       │  • Diagnostics console │
       └────────────┬───────────┘       └────────────┬───────────┘
                    │                                │
                    └───────────────┬────────────────┘
                                    │
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │                    DesktopController                    │
       │  • Server-before-Godot startup sequencing               │
       │  • Credential rotation & child environment handoff      │
       │  • Bounded graceful SHUTDOWN handshake (SHUTDOWN_ACK)   │
       │  • Process wait & force-kill fallback before server stop│
       └────────────────────┬───────────────────┬────────────────┘
                            │                   │
                            ▼                   ▼
                 ┌────────────────────┐┌────────────────────┐
                 │  WebSocketServer   ││ GodotProcessManager│
                 │  (127.0.0.1:port)  ││ (QProcess runtime) │
                 └────────────────────┘└────────────────────┘
```

### Components

1. **`DesktopController` (`src/octa/desktop/controller.py`):**
   - Coordinates `WebSocketServer` and `GodotProcessManager` lifecycle.
   - **Startup Sequencing:** Always starts `WebSocketServer` first to obtain the dynamically selected loopback port (`server_port`) and a freshly rotated bearer token (`secrets.token_urlsafe(32)`).
   - **Safe Credential Passing:** Injects loopback port and bearer token into Godot's child environment using `prepare_process_environment` via `QProcessEnvironment`. No credentials appear in command-line arguments.
   - **Bounded Graceful Shutdown & Process Wait:**
     1. If client is authenticated and process is running, emits `SHUTDOWN` request over WebSocket and waits (bounded up to `DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT_MS`) for `shutdownAcknowledged`, disconnect, or process completion while keeping the WebSocket server available.
     2. Calls `GodotProcessManager.stop()` and spins a bounded `QEventLoop` waiting for the Godot process to finish, allowing `GodotProcessManager`'s force-kill fallback timer (`_shutdown_timer` -> `kill()`) to execute while Qt's event loop is still active and while keeping the WebSocket server available until the client/process has finished or the bounded fallback completes.
     3. Stops `WebSocketServer` and waits in a bounded loop for any closing WebSocket session to reach `ConnectionState.STOPPED`.
     4. Always disconnects temporary signal handlers in `finally` blocks so repeated stops never accumulate callbacks.
   - **Restart Sequencing:** Waits for both the old Godot process and the old WebSocket session to finish closing before relaunching the server and replacement process.

2. **`DiagnosticWindow` (`src/octa/desktop/window.py`):**
   - Renders companion diagnostic monitor with live indicators:
     - Process state (`STOPPED`, `STARTING`, `RUNNING`, `STOPPING`, `CRASHED`, `ERROR`) + PID
     - Server state (`STOPPED`, `LISTENING`, `CONNECTED`, `AUTHENTICATED`, `CLOSING`, `ERROR`)
     - Loopback URL + Port
     - Client authentication state (`Authenticated` / `Disconnected`)
     - Robot companion state (`IDLE`, `LISTENING`, `THINKING`, `SUCCESS`)
   - Interactive control buttons (`Start Companion`, `Stop Companion`, `Restart`, `Clear Log`).
   - Read-only monospace activity log with timestamped entries.
   - **Security Guarantee:** Never displays, formats, or logs the authentication token in any window element, label, tooltip, or log view.
   - **Tray-Aware Close Handling:** When system tray is available, closing the window ignores the close event and hides the window (`hide()`), keeping the application running in the background.

3. **`OctaSystemTray` (`src/octa/desktop/tray.py`):**
   - Generates a 32x32 robot companion icon programmatically with `QPainter` (`create_tray_icon()`).
   - Dynamic tooltip showing connection URL and robot state without exposing tokens.
   - Left-click or double-click toggles the diagnostic monitor window.
   - Context menu provides `Show Monitor`, `Start Companion`, `Stop Companion`, `Restart Companion`, and `Exit OCTA`.

4. **`OctaApplication` (`src/octa/desktop/application.py`):**
   - Sets up `QApplication`, `DesktopController`, `DiagnosticWindow`, and `OctaSystemTray`.
   - Checks `QSystemTrayIcon.isSystemTrayAvailable()`:
     - When available: initializes system tray, sets `app.setQuitOnLastWindowClosed(False)`, honors `start_minimized`.
     - When unavailable: logs a warning, falls back cleanly to windowed mode (`app.setQuitOnLastWindowClosed(True)`), and ensures the monitor window is shown.
   - Automatically starts services if `config.godot_auto_start` is enabled.
   - Hooks `QApplication.aboutToQuit` to run `controller.stop(timeout_ms=...)`, ensuring application exit waits for Godot to finish (including the force-kill fallback while Qt's event loop is active) before shutting down the WebSocket server.
