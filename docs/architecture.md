# OCTA — Architecture Documentation

> **Status:** Phase 1 (Godot character prototype), Phase 2 (Python foundation, process manager, protocol v1, WebSocket server, PySide6 desktop interface), Phase 3 (Godot WebSocket client & end-to-end bridge), Phase 4.0 (Screen capture, task validation, consent workflow, OpenAI-compatible LLM adapter, progress verification, and Qt visual overlay), and Phase 5.0 (Push-to-talk voice interaction, injectable STT/TTS providers, remote speech consent, and instruction pipeline routing) are implemented. Full tutorial loop orchestration (Phase 6.0) remains **proposed**.
>
> **Last updated:** 2026-11-09

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
  - `llm_endpoint: str` (default: `"https://api.openai.com/v1/chat/completions"`, requires `https://` for remote hosts)
  - `llm_model: str` (default: `"gpt-4o-mini"`)
  - `llm_api_key: str | None` (default: `None`, `repr=False` to prevent secret exposure)
  - `llm_timeout_seconds: float` (default: `30.0`, validated `> 0`)
  - `llm_allow_local_http: bool` (default: `False`; when `True`, allows `http://` only for loopback hosts `localhost`, `127.0.0.0/8`, `::1`)
  - `capture_save_screenshots: bool` (default: `False`)
  - `voice_stt_provider: str` (default: `"disabled"`, validated against `"disabled"`, `"mock"`, `"remote_http"`)
  - `voice_tts_provider: str` (default: `"disabled"`, validated against `"disabled"`, `"mock"`, `"remote_http"`)
  - `voice_stt_endpoint: str | None` (default: `None`, `repr=False`, validated for `https://` or opt-in loopback `http://`)
  - `voice_stt_model: str` (default: `"whisper-1"`)
  - `voice_stt_api_key: str | None` (default: `None`, `repr=False`)
  - `voice_tts_endpoint: str | None` (default: `None`, `repr=False`, validated for `https://` or opt-in loopback `http://`)
  - `voice_tts_model: str` (default: `"tts-1"`)
  - `voice_tts_voice: str` (default: `"alloy"`)
  - `voice_tts_api_key: str | None` (default: `None`, `repr=False`)
  - `voice_timeout_seconds: float` (default: `20.0`, validated `> 0`)
  - `voice_allow_local_http: bool` (default: `False`; when `True`, allows `http://` only for loopback hosts)
  - `voice_save_audio: bool` (default: `False`)
  - `voice_max_recording_seconds: float` (default: `30.0`, validated `> 0`)

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
| PySide6 Desktop UI / Tray | ✅ Implemented (Phase 2.5) | `OctaSystemTray`, `DiagnosticWindow`, `DesktopController`, `OctaApplication` |
| Screen Capture & AI Integration | ✅ Implemented (Phase 4.0) | `octa.intelligence` (`ScreenCapturer`, `TaskDefinition`, `ConsentManager`, `OpenAICompatibleAdapter`, `ProgressVerifier`, `GuidanceOverlayWidget`) |
| Voice Interaction (STT/TTS) | ✅ Implemented (Phase 5.0) | `octa.voice` (`PushToTalkRecorder`, `SpeechToTextProvider`, `TextToSpeechProvider`, `VoiceConsentManager`, `VoiceInteractionController`) |

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

---

## Phase 4.0 — Implemented Screen Capture, AI Integration & Visual Overlay

The Phase 4.0 Intelligence Layer lives under `src/octa/intelligence/` and provides modular, testable components for Windows screen capture, task validation, user consent, LLM communication, progress verification, and passive visual overlays.

```
src/octa/intelligence/
├── __init__.py       # Public exports for the Intelligence Layer
├── capture.py        # User-initiated in-memory Windows screen & region capture
├── consent.py        # Remote transmission disclosure, dialog & SHA-256 consent binding
├── exceptions.py     # Dedicated exception hierarchy
├── llm.py            # Isolated OpenAI-compatible Chat Completions adapter
├── overlay.py        # Passive, click-through Qt visual overlay for highlights & arrows
├── tasks.py          # TaskDefinition, TaskStep, ExpectedOutcome, ProgressResult schemas
└── verifier.py       # Conservative ProgressVerifier & step instruction generator
```

### Architectural Decisions & Rationale

1. **Zero New Runtime Dependencies (`PySide6` + Python Standard Library):**
   - **Screen Capture:** Uses `QGuiApplication.primaryScreen().grabWindow(...)` and encodes `QPixmap` / `QImage` into an in-memory `QBuffer` (`PNG` format). This avoids adding native screenshot libraries (`mss`, `pillow`, `pyautogui`) while keeping all pixel data strictly in memory.
   - **LLM Transport:** Uses `urllib.request` from the Python standard library against the standard OpenAI-compatible `/v1/chat/completions` JSON schema. Users can point `OCTA_LLM_ENDPOINT` to OpenAI, Azure/compatible gateways, or local OpenAI-compatible servers (vLLM, Ollama, LM Studio) without vendor SDK lock-in.

2. **Explicit User Action & Screenshot Privacy (`capture.py`):**
   - `ScreenCapturer.capture_full_screen`, `capture_region`, and `select_and_capture_region` require `user_initiated=True` and raise `UserActionRequiredError` otherwise, preventing background or unprompted screen recording.
   - Screenshots are never written to disk by default (`save_screenshots=False`).
   - `CapturedImage` marks `png_bytes` with `repr=False` and overrides `__repr__`/`__str__` to include only safe metadata (`source`, `width`, `height`, `byte_size`, `sha256_prefix`), ensuring screenshots or base64 strings never leak into log files.

3. **Explicit User Consent Bound to Complete Outbound Request & Exact Endpoint (`consent.py`):**
   - Before any `CapturedImage` is transmitted to an LLM endpoint, `OpenAICompatibleAdapter.complete_with_image` constructs a canonical `ConsentRequest` containing the image SHA-256 digest (`image_sha256`), image dimensions and byte size, capture source/region, internal exact raw endpoint URL SHA-256 digest (`endpoint_sha256`, `repr=False`), sanitized `endpoint_url`, `endpoint_disclosure`, `model`, `system_prompt`, `user_prompt`, `task_title`/`step_title` context, and `purpose`, computing an internal deterministic SHA-256 fingerprint (`request_sha256`) over all fields.
   - `format_endpoint_for_disclosure` and `ConsentRequest.to_display_summary()` show the sanitized endpoint (`scheme://host[:port]/path`) and indicate whether URL credentials, query parameters, or a fragment are present with their values redacted (`[REDACTED]`), while never displaying endpoint, request, or URL-component hash fingerprints in `ScreenShareConsentDialog`, `repr`, `str`, logs, or errors.
   - `ConsentDecision.validate_for_request(..., consume=True)` verifies that the user approved the exact outbound request (including `endpoint_sha256`) and marks the decision as consumed (`single-use`). Any attempt to reuse an approval or change even a single query parameter, URL credential, fragment, prompt, endpoint, model, task/step context, purpose, or image raises `ConsentDeclinedError` before any network request is made.
   - The `Cancel` button is the default dialog action so accidental Enter/Escape keystrokes cancel transmission (`ConsentDeclinedError`) before any network payload is sent.

4. **HTTPS Transport Enforcement, Explicit No-Redirect Policy, Credential Isolation & Test Network Guard (`llm.py`):**
   - Remote LLM endpoints must use `https://`. Plain `http://` is rejected unconditionally for all non-loopback hosts, and is permitted for loopback hosts (`localhost`, `127.0.0.1`, `::1`) only when `allow_local_http=True` (`OCTA_LLM_ALLOW_LOCAL_HTTP=true`) is explicitly enabled.
   - `OpenAICompatibleAdapter._send_chat_completion` re-validates the target endpoint before reading `require_api_key()` or constructing the `Authorization: Bearer ...` header, guaranteeing a real bearer API key is never sent to a non-HTTPS remote endpoint.
   - `NoRedirectHandler` and `build_no_redirect_opener` disable automatic HTTP redirects (`301`, `302`, `303`, `307`, `308`) and raise `LLMTransportError`, ensuring that neither the bearer API key nor captured screen content is ever forwarded to a redirected destination without fresh user consent.
   - `LLMConfig` and `AppConfig` mark `api_key` / `llm_api_key` as `repr=False` and redact `Bearer <token>`, URL userinfo credentials, and configured key values from all transport errors and log entries (`redact_sensitive_text`).
   - When running under `pytest` (`"pytest" in sys.modules`), the default `urllib` transport immediately raises `LLMTransportError`, guaranteeing that unit and integration tests never make accidental live API calls.

5. **Conservative Progress Verification (`verifier.py`):**
   - `ProgressVerifier` and `parse_verification_output` return structured `ProgressResult` instances with `ProgressStatus` (`complete`, `incomplete`, `uncertain`).
   - To prevent false-positive completion claims when visual evidence is ambiguous, any model output claiming `status="complete"` is automatically downgraded to `ProgressStatus.UNCERTAIN` if:
     - `confidence < min_complete_confidence` (`0.75` by default),
     - `missing_elements` is non-empty,
     - any item in `step.expected_outcome.required_elements` is not confirmed in `observed_elements`, or
     - `reasoning` contains ambiguity indicators (`"unclear"`, `"not sure"`, `"cannot confirm"`, `"blurry"`, etc.).

6. **Passive, Non-Interactive Qt Visual Overlay (`overlay.py`):**
   - `GuidanceOverlayWidget` validates `HighlightSpec` and `ArrowSpec` coordinates against `OverlayBounds` (rejecting negative coordinates, zero/negative dimensions, zero-length arrows, and out-of-screen bounds with `InvalidOverlayCoordinatesError`).
   - Configured with `Qt.WindowType.WindowTransparentForInput`, `Qt.WindowType.WindowDoesNotAcceptFocus`, `Qt.WindowType.FramelessWindowHint`, `Qt.WindowType.WindowStaysOnTopHint`, `Qt.WidgetAttribute.WA_TransparentForMouseEvents`, and `Qt.FocusPolicy.NoFocus`.
   - Strictly passive: contains no input synthesis or automation routines and never clicks, types, or controls other applications.

---

## Phase 5.0 — Implemented Voice Interaction (Push-to-Talk)

The Phase 5.0 Voice Interaction subsystem lives under `src/octa/voice/` and integrates with the existing `octa.intelligence` task/instruction pipeline (`TaskDefinition`, `TaskStep`, `GuidanceInstruction`, `ProgressVerifier`) and the PySide6 desktop monitor (`DesktopController` and `DiagnosticWindow`).

```
src/octa/voice/
├── __init__.py       # Public exports for the Voice Interaction subsystem
├── audio.py          # Push-to-talk audio models, WAV codec & QAudioSource capture
├── consent.py        # Remote speech consent disclosure, dialog & single-use SHA-256 binding
├── controller.py     # VoiceInteractionController & instruction pipeline routing
├── exceptions.py     # Voice, microphone, consent, and provider exception hierarchy
└── providers.py      # Injectable STT/TTS protocols, mocks, remote HTTP providers & catalog
```

### Architectural Decisions & Rationale

1. **Zero New Runtime Dependencies (`PySide6.QtMultimedia` + Python Standard Library):**
   - Verified that `PySide6.QtMultimedia` (`QAudioFormat`, `QAudioSource`, `QAudioSink`, `QMediaDevices`, `QBuffer`) and `PySide6.QtTextToSpeech` are included with the project's existing `PySide6>=6.7.0` installation.
   - Added zero new packages to `pyproject.toml`.
   - Uses Python's standard `wave` and `io.BytesIO` modules for in-memory 16-bit mono PCM WAV encoding (`encode_pcm16_to_wav_bytes`) and metadata inspection (`decode_wav_metadata`).

2. **Push-to-Talk Only, No Continuous Recording, and No Default Audio Persistence (`audio.py`):**
   - `PushToTalkRecorder.start_push_to_talk(...)` requires `user_initiated=True` (raising `PushToTalkRequiredError` otherwise) and rejects `continuous=True` or `wake_word=True` (`ContinuousRecordingForbiddenError`).
   - Wake-word detection (`enable_wake_word_detection`, `start_continuous_listening`) is explicitly deferred and blocked at runtime.
   - Recorded audio is held solely in memory (`RecordedAudio.wav_bytes`, `repr=False`) and is never written to disk by default (`voice_save_audio=False`).
   - `RecordedAudio`, `TranscriptionResult`, and `SynthesizedSpeech` mark raw audio bytes and transcript text with `repr=False` and expose only safe metadata (`duration_ms`, `byte_size`, `sha256_prefix`, `char_count`, `text_sha256_prefix`) in `__repr__` and `__str__`, keeping microphone audio and spoken transcripts out of logs.

3. **Explicit Destination Disclosure & Single-Use Consent Before Remote Speech Calls (`consent.py`):**
   - Before microphone audio (`VoiceTransmissionKind.SPEECH_TO_TEXT`) or user/instruction text (`VoiceTransmissionKind.TEXT_TO_SPEECH`) is sent to any remote speech provider, `RemoteHTTPSTTProvider` and `RemoteHTTPTTSProvider` construct a `VoiceConsentRequest`.
   - `VoiceConsentRequest` discloses the sanitized destination endpoint (`format_endpoint_for_disclosure` with URL credentials, query parameters, and fragments redacted as `[REDACTED]`), provider name, model, voice name, payload byte size, audio duration or character count, task/step context, and purpose.
   - `VoiceConsentDialog` displays the destination and metadata summary (without logging or exposing raw audio bytes, transcript text, or hash fingerprints) and sets **Cancel** as the default action so pressing Enter or Escape cancels transmission (`VoiceConsentDeclinedError`).
   - `VoiceConsentDecision` is single-use and cryptographically bound (`request_sha256` and `endpoint_sha256`) to the exact audio/text payload, destination endpoint, model, voice, and task context. Any attempt to reuse an approval or alter the audio, text, endpoint, query string, URL credentials, or model raises `VoiceConsentDeclinedError` before reading the API key or opening a network connection.

4. **HTTPS Enforcement, Redirect Blocking & No Assumed Paid Cloud Provider (`providers.py`):**
   - `AppConfig` defaults `voice_stt_provider` and `voice_tts_provider` to `"disabled"` (`DisabledSTTProvider` and `DisabledTTSProvider`). OCTA never assumes or contacts a paid cloud speech provider unless explicitly configured.
   - `validate_voice_endpoint` requires `https://` for all remote speech endpoints; plain `http://` is permitted only for loopback hosts (`localhost`, `127.0.0.1`, `::1`) when `allow_local_http=True` (`OCTA_VOICE_ALLOW_LOCAL_HTTP=true`) is explicitly enabled.
   - `VoiceNoRedirectHandler` and `build_voice_no_redirect_opener` reject HTTP redirects (`301`, `302`, `303`, `307`, `308`) with `VoiceTransportError`, preventing credentials, microphone audio, or user text from being forwarded to an unconsented redirect target.
   - Consent is verified and consumed **before** `require_stt_api_key()` / `require_tts_api_key()` is read, ensuring credentials are never accessed or transmitted if consent is declined or mismatched.
   - When running under `pytest`, default live network calls are blocked (`VoiceTransportError`).

5. **Connection to the Task & Instruction Pipeline (`controller.py`), Real WAV Playback (`QtAudioPlaybackBackend`), and Visible UI Indicators (`window.py`):**
   - `VoiceInteractionController` and `VoiceInteractionController.from_app_config` wire `QtAudioPlaybackBackend` (`create_playback_backend`) by default so synthesized WAV speech is decoded and played through `QAudioSink` and an in-memory `QBuffer`, transitioning `VoiceState.SPEAKING -> VoiceState.IDLE` when `QAudio.State.IdleState` fires.
   - `VoiceInteractionController` connects push-to-talk transcripts to `TaskDefinition` and `TaskStep` navigation (`current`, `next`, `previous`, `hint`, `goal`) and routes spoken requests through `ProgressVerifier.generate_step_instruction(...)` when an LLM adapter is injected, or falls back to deterministic local step guidance.
   - When STT or TTS providers are `"disabled"` (`DisabledSTTProvider`, `DisabledTTSProvider`), `DiagnosticWindow` clearly displays `Voice Providers: STT=disabled (Disabled) | TTS=disabled (Disabled)`, and attempting to record or speak explains the disabled status in the UI and log without activating the microphone or pretending to speak.
   - `cancel_or_stop()` immediately aborts active microphone capture (`PushToTalkRecorder.cancel_recording`), cancels in-flight STT/TTS provider operations, stops audio playback (`AudioPlaybackBackend.stop`), and returns the state to `VoiceState.IDLE`.

### Voice Provider Options & Requirements (`VOICE_PROVIDER_OPTIONS`)

Supported `OCTA_VOICE_STT_PROVIDER` and `OCTA_VOICE_TTS_PROVIDER` configuration modes are `"disabled"`, `"mock"`, and `"remote_http"`. Before making any provider-specific network calls, `VOICE_PROVIDER_OPTIONS` documents both implemented modes and candidate future options:

| Option ID | Config Value | Implemented | Kind | Network Access | Extra Dependencies | Credentials / Configuration Required |
|---|---|---|---|---|---|---|
| `disabled` *(default)* | `"disabled"` | ✅ Yes | STT & TTS | None | None | Default safe setting (`OCTA_VOICE_STT_PROVIDER=disabled`, `OCTA_VOICE_TTS_PROVIDER=disabled`). Clearly indicated as disabled in UI. |
| `mock` | `"mock"` | ✅ Yes | STT & TTS | None (in-memory) | None | Deterministic mock provider (`OCTA_VOICE_STT_PROVIDER=mock`, `OCTA_VOICE_TTS_PROVIDER=mock`) for offline testing and flow verification. |
| `local_loopback_http_server` | `"remote_http"` | ✅ Yes | STT & TTS | Loopback only (`127.0.0.1` / `localhost`) | External local server (e.g., `whisper.cpp`, LocalAI, OpenedAI Speech) | Requires `OCTA_VOICE_*_PROVIDER=remote_http`, `http://127.0.0.1:<port>/...` endpoint, `OCTA_VOICE_ALLOW_LOCAL_HTTP=true`, and explicit per-request consent. Plays WAV via `QtAudioPlaybackBackend`. |
| `remote_https_openai_compatible` | `"remote_http"` | ✅ Yes | STT & TTS | Outbound HTTPS (`https://`) | None (`urllib.request` stdlib) | Requires `OCTA_VOICE_*_PROVIDER=remote_http`, `https://` endpoint, `OCTA_VOICE_*_API_KEY`, and explicit per-request user consent. Plays WAV via `QtAudioPlaybackBackend`. |
| `faster_whisper_local` | `None` | ⬜ Candidate | STT | None (offline) | `faster-whisper` + local model weights | Not bundled; would require adding `faster-whisper` and local CTranslate2 Whisper weights. |
| `vosk_local` | `None` | ⬜ Candidate | STT | None (offline) | `vosk` + local acoustic model | Not bundled; would require adding `vosk` and a local Vosk model directory. |

---

## Phase 6 — Distribution & Packaging Architecture

### Implemented Distribution Model

OCTA is packaged as a standalone, portable Windows application bundle using PyInstaller 6.x and a checked-in specification file (`octa.spec`) automated by `scripts/build_windows_dist.py`.

```
dist/OCTA/
├── OCTA.exe                    ← Windowed GUI executable (subsystem: WINDOWS_GUI, no terminal window)
├── octa-cli.exe                ← Console CLI executable (subsystem: WINDOWS_CUI, prints stdout to terminal)
├── godot/                      ← Bundled Godot 4 companion runtime project
│   ├── project.godot
│   ├── icon.svg
│   ├── scenes/
│   └── scripts/
└── _internal/                  ← PyInstaller shared runtime directory
    ├── PySide6/                ← PySide6 libraries & plugins (platforms/qwindows.dll, tls, multimedia)
    ├── python312.dll           ← Python 3.12 embedded runtime
    └── [dependency DLLs]       ← psutil, Shiboken, and C-runtime libraries
```

### Key Packaging Architectural Decisions

1. **Directory Bundle (`--onedir`) vs Single Executable (`--onefile`)**:
   - Single-file packaging extracts the full 100+ MB Qt runtime into a temporary `%TEMP%/_MEIxxxx` directory on every launch, resulting in slow startup times (3–8 seconds), disk thrashing, and elevated false-positive virus detections.
   - Directory bundle mode starts instantly (<0.5 seconds), enables direct access to the Godot project assets, and is effortlessly distributed as a portable ZIP archive (`OCTA-v0.1.0-windows-x64.zip`).

2. **Dual Entry Points (`OCTA.exe` and `octa-cli.exe`)**:
   - Windows executable subsystems determine whether a console window is allocated by the OS before Python even starts.
   - Compiling both executables from the same codebase in one PyInstaller spec provides native desktop behavior:
     - `OCTA.exe` runs silently in the system tray and desktop monitor without an unwanted black CMD window.
     - `octa-cli.exe` allows command-line flags (`--version`, `--help`, `--no-auto-start`, `--headless-godot`, `--config <path>`) with output rendered directly in the calling terminal.

3. **Frozen Path Resolution (`AppConfig`)**:
   - In development, `godot_project_dir` resolves relative to the current working directory.
   - In a frozen bundle, `AppConfig.__post_init__` checks `sys.frozen` and `sys._MEIPASS` (or `sys.executable.parent`), automatically binding the bundled Godot project directory regardless of which working directory launched the application.

4. **Secret Hygiene & Security Assurance**:
   - The build process audits the generated bundle against forbidden files (`.env`, `.env.*`, `*.key`, `*.pem`, `tutorial_feedback.json`, `tutorial_session_metrics.json`, `*.log`).
   - Development tools and test frameworks (`pytest`, `ruff`) are explicitly excluded.
   - User data and logs are strictly stored in `%LOCALAPPDATA%\OCTA`, keeping the application bundle read-only and suitable for installation in protected directories.
