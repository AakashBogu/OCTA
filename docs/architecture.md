# OCTA — Architecture Documentation

> **Status:** Phase 1 (Godot character prototype), Phase 2 (Python foundation, process manager, protocol v1, WebSocket server, PySide6 desktop interface), Phase 3 (Godot WebSocket client & end-to-end bridge), Phase 4.0 (Screen capture, task validation, consent workflow, OpenAI-compatible LLM adapter, progress verification, and Qt visual overlay), Phase 5.0 (Push-to-talk voice interaction, injectable STT/TTS providers, remote speech consent, and instruction pipeline routing), Phase 6.0 (End-to-end tutorial engine, sample tutorials, feedback loop, performance optimization, and packaging), Phase 7.0 (Enhanced Godot character, transparent desktop window, AnimationTree), Phase 8.1 (External tutorial plugin system), Phase 8.2 (Multi-monitor geometry and display adaptation), Phase 8.3 (Accessibility adaptations), Phase 9.1 (Secure package archive and CLI), Phase 9.2 (Cryptographic integrity & provenance trust model), Phase 9.3 (Desktop plugin manager UI & dynamic catalog integration), Phase 10.1 (Visual region & directional cue selector), Phase 10.2 (Interactive step & tutorial builder UI), and Phase 10.3 (Plugin project workspace & integrated package wizard) are implemented and verified.
>
> **Last updated:** 2026-11-12

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

## Godot Character & Desktop Window Architecture (Phase 7.0)

The following describes the enhanced Godot companion architecture implemented in Phase 7.0.

### Scene Tree

```
Main (Node3D)                                 ← main_scene.gd
├── Camera3D                                  ← Positioned at (0, 0.72, 1.95), FOV 38°, looking at robot
├── DirectionalLight3D (key)                  ← Warm white, shadow enabled, -35°/30° rotation
├── DirectionalLight3D (fill)                 ← Cool blue-white, no shadow, -20°/-55° rotation
├── DirectionalLight3D (rim)                  ← Subtle rim highlight, 30°/150° rotation
├── WorldEnvironment                          ← Transparent background (Color(0,0,0,0)), filmic tonemapping
├── RobotCharacter (Node3D)                   ← robot_character.gd (class_name RobotCharacter)
│   ├── BodyPivot (Node3D)                    ← Pivot for root bobbing and celebratory bouncing
│   │   ├── Chassis (MeshInstance3D)          ← Pearlescent metallic capsule torso
│   │   ├── ChestCore (MeshInstance3D)        ← Status reactor cylinder, pulsing emission
│   │   ├── Neck (MeshInstance3D)             ← Graphite articulation joint
│   │   ├── HeadPivot (Node3D)                ← Expressive rotation pivot (pitch/yaw/roll)
│   │   │   ├── Head (MeshInstance3D)         ← Sleek chassis head casing
│   │   │   ├── Visor (MeshInstance3D)        ← Dark obsidian glossy curved face visor
│   │   │   ├── LeftEye (MeshInstance3D)      ← Digital LED eye disc with state-dependent glow
│   │   │   ├── RightEye (MeshInstance3D)     ← Digital LED eye disc with state-dependent glow
│   │   │   ├── MouthGrille (MeshInstance3D)  ← Audio waveform / smile LED bar, pulses while talking
│   │   │   ├── LeftAntennaPivot (Node3D)     ← Angled stalk with glowing status orb tip
│   │   │   └── RightAntennaPivot (Node3D)    ← Angled stalk with glowing status orb tip
│   │   ├── LeftArmPivot (Node3D)             ← Shoulder ball, upper arm, forearm pivot, hand
│   │   ├── RightArmPivot (Node3D)            ← Shoulder ball, upper arm, forearm pivot, hand
│   │   │   └── RightPointerFinger (Mesh)     ← Outstretched pointer finger for screen guidance
│   │   └── ThrusterRing (MeshInstance3D)     ← Anti-gravity ring with subtle cyan thruster glow
│   ├── GroundShadow (MeshInstance3D)         ← Soft alpha-blended transparent disc contact shadow
│   ├── AnimPlayer (AnimationPlayer)          ← Library of 6 clips (idle, listen, think, talk, point, celebrate)
│   └── AnimTree (AnimationTree)              ← AnimationNodeStateMachine with smooth 0.22s crossfades
├── WsClient (Node)                           ← ws_client.gd (authenticated WebSocket connection)
└── CanvasLayer (HUD, layer 10)
    ├── Label (state display)                 ← Top-left, shows current state name
    ├── Label (connection display)            ← Below state, shows WebSocket connection status
    └── Label (keyboard hints)                ← Bottom-left, shows state & drag instructions
```

### State Machine & Animation Architecture

The `RobotCharacter` combines an `AnimationTree` state machine for procedural skeleton transitions with real-time shader emission modulation:

| State | Eye / Core Glow | Antenna Tip | Mouth Waveform | Skeletal Animation |
|---|---|---|---|---|
| IDLE | Bright cyan (0.9 energy) | Steady gentle pulse (0.3) | Off / minimal | Gentle vertical bobbing, subtle head sway, balanced arms |
| LISTENING | Emerald green (1.4 energy) | Rhythmic green pulse (0.8) | Low glow | Attentive forward-tilted head, receptive arm stance |
| THINKING | Warm amber (1.1 energy) | Rapid amber pulse (1.2) | Low glow | Quizzical head tilt, right hand touching chin/visor |
| TALKING | Luminous cyan (1.2 energy) | Speech sync pulse (0.5) | Rapid speech flicker (0.6–2.2) | Rhythmic speech nodding, alternating conversational gestures |
| POINTING | High-contrast cyan (1.5) | Steady glow (0.8) | Moderate glow | Right arm extended forward-upward with pointer finger |
| CELEBRATING / SUCCESS | Joyous emerald (1.8 energy) | Bright celebratory flash (1.6) | High burst | Arms raised overhead in victory (\o/), energetic jumping bounces |

### Desktop Window Integration

- **Per-Pixel Transparency:** Configured via `project.godot` (`window/size/transparent=true`, `window/per_pixel_transparency/allowed=true`, `rendering/viewport/transparent_background=true`) and DisplayServer window flags (`WINDOW_FLAG_TRANSPARENT`, `WINDOW_FLAG_BORDERLESS`, `WINDOW_FLAG_ALWAYS_ON_TOP`).
- **Screen Positioning:** Automatically docks to the bottom-right corner of the primary screen usable area (`DisplayServer.screen_get_usable_rect()`).
- **Left-Click Dragging:** Left mouse button dragging anywhere on the character window dynamically translates the window across the user's desktop.
- **Ground Contact Shadow:** Rather than an opaque floor plane, a soft alpha-blended disc shadow (`StandardMaterial3D.TRANSPARENCY_ALPHA`) visually grounds the character while leaving the rest of the desktop visible.

### Scripts

| Script | Class | Responsibility |
|---|---|---|
| `scripts/robot_character.gd` | `RobotCharacter` | Builds articulated robot hierarchy, defines 6 animations in `AnimationPlayer`, manages `AnimationTree` state machine with crossfades, updates dynamic emissions |
| `scripts/main_scene.gd` | *(anonymous)* | Configures desktop transparency, window positioning, mouse dragging, 3-point lighting, environment, robot instance, HUD, and WebSocket orchestration |

### Asset Provenance & Licensing

All 3D character models, materials, animations, and scenes are 100% procedural first-party assets created natively in GDScript for OCTA under the MIT License. No third-party proprietary meshes, textures, or audio are required. Full details are recorded in `godot/ASSETS_LICENSE.md`.
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
| Godot Character Scene | ✅ Enhanced (Phase 7.0) | `godot/` articulated 3D character, transparent window, AnimationTree, 6 animations |
| Python Packaging & Entry Point | ✅ Implemented (Phase 2.1) | `pyproject.toml`, `src/octa/__main__.py` |
| Configuration Infrastructure | ✅ Implemented (Phase 2.2) | `octa.core.config.AppConfig` |
| Logging Infrastructure | ✅ Implemented (Phase 2.2) | `octa.core.logging.setup_logging` |
| Godot Process Manager | ✅ Implemented (Phase 2.3) | `octa.process.manager.GodotProcessManager`, `QProcess` lifecycle, path validation |
| Python Qt WebSocket Server | ✅ Implemented (Phase 2.4.2) | Authenticated single-client protocol v1 server on IPv4 loopback |
| Godot WebSocket Client | ✅ Implemented (Phase 3.0) | GDScript `OctaWsClient` and `OctaProtocol` using `WebSocketPeer`, safe local handoff |
| PySide6 Desktop UI / Tray | ✅ Implemented (Phase 2.5) | `OctaSystemTray`, `DiagnosticWindow`, `DesktopController`, `OctaApplication` |
| Screen Capture & AI Integration | ✅ Implemented (Phase 4.0) | `octa.intelligence` (`ScreenCapturer`, `TaskDefinition`, `ConsentManager`, `OpenAICompatibleAdapter`, `ProgressVerifier`, `GuidanceOverlayWidget`) |
| Voice Interaction (STT/TTS) | ✅ Implemented (Phase 5.0) | `octa.voice` (`PushToTalkRecorder`, `SpeechToTextProvider`, `TextToSpeechProvider`, `VoiceConsentManager`, `VoiceInteractionController`) |
| End-to-End Tutorial Engine | ✅ Implemented (Phase 6.0) | `octa.tutorials` (`TutorialEngine`, `TutorialCatalog`, `FeedbackCollector`) |
| External Tutorial Plugin System | ✅ Implemented (Phase 8.1) | `octa.tutorials.plugins` (sandboxed manifests, path containment, dynamic reload) |
| Multi-Monitor Geometry & Display Adaptation | ✅ Implemented (Phase 8.2) | `octa.intelligence.display` (`DisplayManager`, `DisplayInfo`, negative coords, per-monitor overlay/capture) |
| Accessibility Adaptations | ✅ Implemented (Phase 8.3) | `octa.intelligence.overlay` (dual-stroke high contrast, non-color cues, text scaling), `octa.tutorials.sound` (in-memory harmonic chime), Qt a11y labels & keyboard nav |
| Tutorial Package Archive & CLI | ✅ Implemented (Phase 9.1) | `octa.tutorials.packaging` (`.octapack` format, atomic packager, zero-extraction verifier, Zip Slip defense, quotas) |

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


---

## Phase 8 — Extensibility & External Tutorial Plugin Architecture

### Overview & Security Posture

OCTA Phase 8.1 introduces a local-only plugin architecture allowing community or local third-party tutorial packs to extend OCTA's tutorial catalog without modifying the core codebase or requiring network connectivity.

Because external tutorial files may come from untrusted sources, the plugin loader implements a **strict zero-trust sandbox**:
1. **Zero Code Execution**: Plugins are strictly declarative JSON. No Python scripts, native DLLs, shell scripts, or dynamic code execution mechanisms (`eval`, `exec`, `__import__`) are supported or permitted.
2. **Path Traversal & Escape Prevention**: Manifests and tutorial references are validated against malicious paths. Absolute file paths (e.g. `C:\...`, `/...`) and directory traversal sequences (`..`) are strictly rejected. Furthermore, canonical resolved paths are verified using Python's `Path.is_relative_to(canonical_plugin_dir)` to guarantee no symlink escapes outside the plugin directory root.
3. **Read-Only Operations**: All external plugin files are opened strictly in read-only mode (`open(..., "r", encoding="utf-8")`). The application never writes to, modifies, or deletes files in the plugin directory.
4. **Reserved Built-in ID Protection**: Core tutorial IDs (`BUILTIN_TASK_IDS`: `octa-welcome-task`, `windows-notepad-save`, `windows-explorer-new-folder`) are strictly reserved. External plugins attempting to use or shadow these task IDs are rejected with `ID_COLLISION` diagnostics, ensuring built-in tutorials cannot be hijacked.
5. **Startup Resilience**: Corrupted, malformed, or hostile plugins produce structured `PluginLoadDiagnostic` entries instead of raising uncaught exceptions, preventing broken plugins from crashing the desktop controller, tutorial engine, or application startup.

### Plugin Manifest Specification (`plugin.json`)

Each tutorial plugin resides in a distinct subdirectory within the configured plugin root (`tutorial_plugin_dir`, defaulting to `%LOCALAPPDATA%\OCTA\plugins\tutorials`, overrideable via `OCTA_TUTORIAL_PLUGIN_DIR`).

A plugin directory must contain a `plugin.json` manifest:

```json
{
  "schema_version": "1.0",
  "id": "paint-drawing-pack",
  "name": "MS Paint Tutorials",
  "version": "1.0.0",
  "author": "Community Contributor",
  "description": "Step-by-step interactive drawing tutorials for Microsoft Paint.",
  "tutorial_files": [
    "tutorials/draw_shape.json",
    "tutorials/color_picker.json"
  ]
}
```

#### Manifest Fields:
- `schema_version` *(string, required)*: Version of the plugin manifest format (must be `"1.0"`).
- `id` *(string, required)*: Unique slug identifier for the plugin (alphanumeric, hyphens, underscores).
- `name` *(string, required)*: Human-readable display name of the plugin pack.
- `version` *(string, required)*: SemVer or numeric version string.
- `author` *(string, optional)*: Author or organization name.
- `description` *(string, optional)*: Short summary of the plugin's content.
- `tutorial_file` *(string, optional)*: Relative path to a single tutorial JSON definition.
- `tutorial_files` *(list of strings, optional)*: Relative paths to one or more tutorial JSON definitions. Either `tutorial_file` or `tutorial_files` must be provided.

### Discovery & Catalog Architecture

```
┌────────────────────────────────────────────────────────┐
│                   AppConfig                            │
│           (tutorial_plugin_dir)                        │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│             discover_tutorial_plugins()                │
│    Scans subdirectories for valid plugin.json files    │
│    Validates manifests, paths, and prevents escapes    │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                  TutorialCatalog                       │
│    Combines BUILTIN_TUTORIALS + Discovered Tutorials   │
│    Enforces ID uniqueness & tracks plugin provenance   │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                  TutorialEngine                        │
│    Executes tutorials, manages AI verification,        │
│    records metrics, and emits catalog reload signals   │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│             DiagnosticWindow (PySide6 UI)              │
│    Displays [Plugin: <name>] in dropdown selector      │
│    Dynamically refreshes when plugins reload           │
└────────────────────────────────────────────────────────┘
```

### UI Provenance & Dynamic Reloading

External plugin tutorials are displayed with their source plugin name in the tutorial selector dropdown:
- Built-in tutorial: `Create and Save a Note in Windows Notepad (Windows Notepad)`
- External plugin tutorial: `[Plugin: MS Paint Tutorials] Draw a Basic Shape (MS Paint)`

Calling `DesktopController.reload_tutorial_plugins()` rescans the plugin directory, rebuilds the catalog, and updates the UI dropdown dynamically via the `tutorials_catalog_reloaded` signal, falling back cleanly to the default tutorial if the currently selected tutorial was removed.


---

## Phase 8.2 — Multi-Monitor Geometry & Display Adaptation Architecture

### Overview & Display Topology

OCTA Phase 8.2 introduces a unified multi-monitor geometry and display adaptation subsystem (`src/octa/intelligence/display.py`) ensuring screen capture, visual guidance overlays, and companion positioning operate reliably across arbitrary monitor setups.

On Windows, multiple displays are arranged on a virtual desktop canvas. Depending on user arrangement in Windows Display Settings, secondary displays can be placed to the left of or above the primary monitor, giving them **negative virtual desktop coordinates**:

```
 ┌───────────────────────┐ ┌───────────────────────┐ ┌───────────────────────┐
 │ Left Display (Index 1)│ │Primary Display(Index 0)│ │Right Display (Index 2)│
 │ x: -1920, y: 0        │ │ x: 0, y: 0            │ │ x: 1920, y: 0         │
 │ 1920x1080 @ 100% DPR  │ │ 1920x1080 @ 100% DPR  │ │ 2560x1440 @ 125% DPR  │
 └───────────────────────┘ └───────────────────────┘ └───────────────────────┘
```

### Key Architectural Components

1. **`DisplayInfo` (Data Model)**:
   - Strongly-typed, immutable representation of a display.
   - Captures logical geometry (`x, y, width, height`), available usable area excluding Windows taskbars (`available_x, available_y, available_width, available_height`), device pixel ratio (`device_pixel_ratio`), and primary status.
   - Computes physical pixel dimensions (`physical_width`, `physical_height`) for DPI scaling.
   - Provides bidirectional coordinate mapping (`global_to_local`, `local_to_global`, `global_rect_to_local`, `local_rect_to_global`).

2. **`DisplayManager` (Lifecycle & Discovery)**:
   - Subclasses `QObject`, discovering displays via `QGuiApplication.screens()` or an injectable `DisplayProvider` callable.
   - Connects to Qt display change notifications (`screenAdded`, `screenRemoved`, `primaryScreenChanged`).
   - Maintains the currently selected `active_display` (defaulting to the primary display or `OCTA_MONITOR_INDEX`).
   - If an active secondary monitor is unplugged or disconnected, `refresh()` automatically detects the removal and gracefully falls back to the primary display without raising exceptions or freezing the UI.

3. **Explicit Capture Gating & Negative Coordinates (`CaptureRegion`)**:
   - `CaptureRegion` accepts `allow_negative=True` when representing virtual desktop space on negative monitors.
   - `grab_screen_with_qt` resolves the target screen by index, translates global virtual coordinates into monitor-local coordinates (`local_x = region.x - screen_x`, `local_y = region.y - screen_y`), and invokes `screen.grabWindow(0, local_x, local_y, w, h)`.
   - Never captures unintended screens: only the user-selected or active display is captured.

4. **Visual Overlay Adaptation (`GuidanceOverlayWidget`)**:
   - `GuidanceOverlayWidget.set_display(display)` moves the click-through transparent overlay widget to match the active monitor's exact bounds (`OverlayBounds(x=d.x, y=d.y, width=d.width, height=d.height)`).
   - In-widget painting translates annotations relative to the monitor's top-left corner (`origin_x = bounds.x`, `origin_y = bounds.y`), ensuring highlights and arrows render accurately regardless of display offset.

5. **Desktop UI Integration (`DiagnosticWindow`)**:
   - Live `Monitor:` combo box displaying all connected displays with their resolutions, virtual coordinates, scaling factors, and primary indicators.
   - When the user selects a different monitor, `DesktopController.select_display(index)` updates the `TutorialEngine`, moving the guidance overlay and capture target to that display immediately.

---

## Phase 8.3 — Accessibility Adaptations Architecture

### Overview

OCTA Phase 8.3 introduces comprehensive accessibility adaptations across the visual guidance overlay, desktop application UI, and auditory completion cues. The design emphasizes multi-modal feedback, universal contrast across heterogeneous background themes, and robust keyboard navigation without requiring mouse interaction.

```
┌────────────────────────────────────────────────────────┐
│                   AppConfig                            │
│  - accessibility_high_contrast: bool                   │
│  - accessibility_text_scale: float [0.5, 3.0]          │
│  - accessibility_sound_cues: bool                      │
└──────────────────────────┬─────────────────────────────┘
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
┌──────────────────┐ ┌───────────┐ ┌──────────────────────┐
│ GuidanceOverlay  │ │ Diagnostic│ │ TutorialSoundPlayer  │
│ - Dual-stroke    │ │ Window    │ │ - In-memory harmonic │
│   high contrast  │ │ - Text sz │ │   PCM WAV synthesis  │
│ - Non-color cues │ │ - Qt a11y │ │ - Optional playback  │
│ - Text scaling   │ │ - Tab nav │ │   respecting config  │
└──────────────────┘ └───────────┘ └──────────────────────┘
```

### Key Architectural Components

1. **Dual-Stroke High-Contrast Rendering (`GuidanceOverlayWidget`)**:
   - Single-color outlines (such as pure white or pure cyan) lose contrast when superimposed on light-themed applications (e.g. Notepad, browser documents) or dark-themed IDEs.
   - Dual-stroke rendering draws a wider, dark outer halo (`QColor(0, 0, 0, 240)`) followed by an inner vibrant yellow stroke (`#FFEA00`, 3.5 px width).
   - This guarantees high-contrast edges (> 7:1 contrast ratio) against any backdrop color without obscuring the target UI elements.

2. **Non-Color Shape Indicators (WCAG Success Criterion 1.4.1)**:
   - Visual guidance does not rely on color alone to convey meaning or focus targets.
   - **Highlights**: Equipped with prominent L-shaped corner indicator brackets extending inward from each corner of the bounding box, providing unambiguous geometric framing.
   - **Arrows**: Equipped with an origin target ring (`O`) at the arrow base and an enlarged directional pointer head (`>`), clearly indicating directionality.
   - **Badges**: Text labels are prefixed with distinct shape glyphs (`[■]` for highlights, `[▶]` for arrows).

3. **Dynamic Text Scaling (0.5x to 3.0x)**:
   - Configurable text scale multiplier allows users with low vision to enlarge overlay cue text and desktop UI fonts up to 300%.
   - Overlay badge bounding boxes dynamically measure text dimensions using `QFontMetrics` with proportional padding (`int(6 * text_scale)`), preventing label clipping or text wrapping truncation.
   - Desktop UI (`DiagnosticWindow`) applies text scaling across all labels, group boxes, combo boxes, buttons, and log viewers.

4. **In-Memory Harmonic Completion Chime Synthesis (`octa.tutorials.sound`)**:
   - To avoid bundling external binary audio assets or adding heavy third-party audio dependencies, completion chimes are synthesized dynamically in memory using pure Python standard library modules (`math`, `struct`, `wave`).
   - Generates a pleasant ascending harmonic triad (C5 523.25 Hz -> E5 659.25 Hz -> G5 783.99 Hz) with smooth cosine attack and exponential decay envelopes at 44.1 kHz 16-bit PCM mono.
   - `TutorialSoundPlayer` plays the synthesized audio via `QtAudioPlaybackBackend` (or mock backend in headless test environments).
   - Audio cues are strictly opt-in (`accessibility_sound_cues = False` by default) and respect user preferences, remaining silent when disabled.

5. **Qt Accessibility Labels & Keyboard Navigation**:
   - All interactive controls in `DiagnosticWindow` and `TutorialFeedbackDialog` are annotated with `setAccessibleName()` and `setAccessibleDescription()` for assistive screen reader compatibility.
   - `Qt.FocusPolicy.StrongFocus` is assigned to all interactive inputs, buttons, and combo selectors.
   - An explicit, logical keyboard tab order (`QWidget.setTabOrder`) chains controls from the tutorial selector down to log views.
   - Standard keyboard shortcuts (e.g. `Alt+F` for feedback) provide fast, mouse-free workflow execution.

---

## Phase 9.1 — Secure Tutorial Package Archive & CLI Architecture

### Overview

OCTA Phase 9.1 introduces a portable, hardened package archive container (`.octapack`) for distributing external tutorial packs, accompanied by CLI commands (`octa pack` and `octa verify`) built exclusively with the Python standard library.

```
┌────────────────────────────────────────────────────────┐
│                   Source Directory                     │
│  - plugin.json                                         │
│  - tutorials/*.json                                    │
│  - README.md                                           │
└──────────────────────────┬─────────────────────────────┘
                           │
             pack_tutorial_package()
             • Pre-scan directory for forbidden extensions
             • Validate manifest & task schemas
             • Enforce quota thresholds
             • Write to atomic temporary file
             • Verify temporary archive
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                 .octapack Package Archive              │
│  - Standard ZIP-DEFLATED container                     │
│  - Forward-slash relative paths                        │
│  - Standard 0o644 non-executable file permissions      │
└──────────────────────────┬─────────────────────────────┘
                           │
             verify_tutorial_package()
             • Zero-extraction in-memory inspection
             • Zip Slip traversal & backslash rejection
             • Quota & anti-zip-bomb verification
             • Manifest & TaskDefinition schema parsing
             • Compute SHA-256 package digest
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│               PackageVerificationResult                │
│  - is_valid: bool                                      │
│  - plugin_id, name, version                            │
│  - file_count, compressed/uncompressed sizes           │
│  - sha256_digest: str                                  │
│  - errors: tuple[str, ...]                             │
└────────────────────────────────────────────────────────┘
```

### Key Architectural Components

1. **Untrusted Archive Threat Model**:
   - Tutorial packages are treated as untrusted data from potentially hostile sources.
   - All validation occurs prior to disk extraction; `verify_tutorial_package` reads archive headers, entry metadata, and JSON payloads entirely in memory.

2. **Zip Slip & Path Traversal Prevention**:
   - Rejects directory traversal parts (`..`), parent escapes, and relative component evasions.
   - Rejects absolute paths (`/`) and Windows drive letters (`C:`, `D:`).
   - Rejects backslash (`\`) and consecutive forward slashes (`//`). Standard single forward slashes (`/`) are strictly enforced.
   - Rejects duplicate, ambiguous, or case-insensitive colliding entries (e.g., `plugin.json` and `PLUGIN.JSON`).

3. **Quota & Compression Bomb Defense**:
   - **File Count Limit**: Maximum 100 entries (`MAX_PACKAGE_FILE_COUNT`).
   - **Compressed Archive Size**: Maximum 10 MB on disk (`MAX_PACKAGE_COMPRESSED_BYTES`).
   - **Expanded Archive Size**: Maximum 25 MB total uncompressed content (`MAX_PACKAGE_EXPANDED_BYTES`).
   - **Individual File Size**: Maximum 5 MB per file (`MAX_PACKAGE_FILE_BYTES`).
   - **Compression Ratio**: Maximum 50:1 uncompressed-to-compressed ratio (`MAX_COMPRESSION_RATIO`) per entry to defeat zip bombs.

4. **Code Execution & Symlink Prevention**:
   - Complete prohibition of executable and script extensions (`FORBIDDEN_EXTENSIONS`: `.exe`, `.dll`, `.bat`, `.cmd`, `.ps1`, `.py`, `.sh`, `.vbs`, etc.).
   - Symlinks and hardlinks in ZIP metadata (`stat.S_ISLNK`) are strictly rejected.
   - Executable permissions (`0o111`) are rejected; packager enforces regular file mode `0o644`.

5. **Atomic Package Assembly & CLI Subcommands**:
   - `pack_tutorial_package()` builds packages atomically via `tempfile.mkstemp` in the target directory and verifies the temporary archive with `verify_tutorial_package()` before committing it via `os.replace`.
   - `src/octa/__main__.py` routes subcommands `pack` and `verify` to standalone console handlers, providing formatted terminal feedback and non-zero exit codes on failure without loading the GUI event loop.

---

## Phase 9.2 — Cryptographic Integrity & Provenance Trust Model Architecture

### Overview

Phase 9.2 establishes a deterministic cryptographic provenance and integrity layer for `.octapack` tutorial bundles. It addresses untrusted tutorial packages by binding all packaged assets via SHA-256 digests and validating author authenticity through standard Ed25519 digital signatures and pinned public key trust stores without network requests.

```
┌────────────────────────────────────────────────────────┐
│               Tutorial Package Architecture            │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │                    plugin.json                   │  │
│  │  - id, name, version, author, description        │  │
│  │  - tutorial_files: ["tutorials/step.json"]       │  │
│  │  - asset_digests: {                              │  │
│  │      "tutorials/step.json": "<sha256_hex>"       │  │
│  │    }                                             │  │
│  │  - signature: {                                  │  │
│  │      "algorithm": "ed25519",                     │  │
│  │      "key_id": "octa-official",                  │  │
│  │      "public_key": "<64_hex_chars>",             │  │
│  │      "signature": "<128_hex_chars>"              │  │
│  │    }                                             │  │
│  └──────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │             tutorials/step.json                  │  │
│  │  (Payload cryptographically bound by digest)     │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
                           │
             verify_tutorial_package()
                           │
       ┌───────────────────┴───────────────────┐
       ▼                                       ▼
Asset Digest Check                      Signature Check
• Compute asset SHA-256                 • Extract signature block
• Compare with asset_digests            • Canonical manifest bytes
• Verify every tutorial file            • Verify Ed25519 offline
• Detect tampering or omissions         • Match public key in TrustStore
       │                                       │
       └───────────────────┬───────────────────┘
                           │
                           ▼
               Package Verification Result
        ┌──────────────────────────────────────┐
        │  trust_level:                        │
        │  • TRUSTED_SIGNED (Pinned key)       │
        │  • SIGNED_UNKNOWN_KEY (Unpinned)     │
        │  • UNSIGNED_COMMUNITY (Unsigned)     │
        │  • INVALID_TAMPERED (Tampered)       │
        └──────────────────────────────────────┘
```

### Architectural Principles

1. **OCTA Canonical Manifest Byte Format**:
   - Cryptographic signing requires stable, deterministic byte representations regardless of platform, dictionary key insertion order, or formatting variations.
   - `canonical_manifest_bytes()` serializes JSON with `sort_keys=True`, compact separators `(',', ':')`, and literal UTF-8 encoding without ASCII escapes (`ensure_ascii=False`).
   - The format is intentionally project-specific for manifest objects and avoids generic RFC 8785 claims.
   - During signing and verification, the signature block itself is omitted from the canonical payload to prevent circular dependencies.

2. **Hierarchical Cryptographic Binding**:
   - `asset_digests` in `plugin.json` records SHA-256 digests for all referenced tutorial files and documentation.
   - `verify_tutorial_package()` computes the SHA-256 hash of each uncompressed file and asserts a match against `asset_digests`.
   - Because the signature covers `plugin.json` (which contains `asset_digests`), a single Ed25519 signature cryptographically binds the entire package archive without requiring separate signature files for each asset.

3. **Standard Ed25519 Cryptography**:
   - Uses `cryptography.hazmat.primitives.asymmetric.ed25519` from the `cryptography` library (`cryptography>=42.0.0`).
   - No custom cryptographic primitives are implemented.
   - Private signing keys are never embedded in the repository or application binaries.

4. **Trust Store & Key Management**:
   - `TrustStore` holds trusted `TrustedKey` instances (validating 64-character hex strings for 32-byte Ed25519 public keys).
   - Ships with an empty default trust store (`DEFAULT_TRUSTED_KEYS = ()`) to prevent unverified claims of "official" provenance until authentic keys are published via verified project distribution channels.
   - Custom trust stores can be loaded from secure JSON files via `TrustStore.from_json_file()` or `--trust-store` CLI flag.

5. **Four-Tier Trust Classification**:
   - **`PackageTrustLevel.TRUSTED_SIGNED`**: The package has valid asset digests and a cryptographically valid Ed25519 signature whose public key exists in the active `TrustStore`.
   - **`PackageTrustLevel.SIGNED_UNKNOWN_KEY`**: The package has valid asset digests and a cryptographically valid Ed25519 signature, but the signing public key is unpinned / unknown in the active `TrustStore`.
   - **`PackageTrustLevel.UNSIGNED_COMMUNITY`**: The package has valid asset digests and valid schema, but carries no digital signature.
   - **`PackageTrustLevel.INVALID_TAMPERED`**: The package has modified content, corrupted asset digests, invalid signature bytes, or malformed cryptographic structures.

6. **Strict Offline Operation**:
   - Trust and signature verification performs zero network calls.
   - Trust is established solely through pinned public keys in the local trust store.

---

## Phase 9.3 — Desktop Plugin Manager UI & Dynamic Catalog Integration Architecture

The following describes the in-app plugin manager and package lifecycle architecture implemented in Phase 9.3.

### Workflow & Data Flow

```
User selects .octapack
         │
         ▼
Pre-flight In-Memory Verification (`verify_tutorial_package`)
         │
         ├──────────────────────────────────────────────┐
         ▼                                              ▼
   Valid Package?                               Invalid / Tampered
         │                                              │
         ▼                                              ▼
Show Preview Dialog (`PluginInstallPreviewDialog`)  Show Critical Error Dialog
         │                                          & Abort
         ├───────────────────────────────┐
         ▼                               ▼
  TRUSTED_SIGNED          SIGNED_UNKNOWN_KEY or
  (Pinned Key)            UNSIGNED_COMMUNITY
         │                               │
         │                        Requires explicit user
         │                        acknowledgement checkbox
         │                               │
         └───────────────┬───────────────┘
                         ▼
               User clicks "Install"
                         │
                         ▼
        Staged Atomic Extraction (`extract_tutorial_package`)
        1. Create `.staging_<id>_<rand>` inside managed dir
        2. Safe streaming extraction with quota and Zip Slip checks
        3. Forbidden extension & symlink rejection
        4. Validate extracted plugin manifest & task definitions
        5. Verify no collision with reserved built-in IDs
        6. Atomic swap: `os.replace(staging, final_dir)`
           (with backup reversal if overwriting)
                         │
                         ▼
      Dynamic Catalog Sync (`reload_tutorial_plugins`)
      1. Re-scan managed plugin directory
      2. Update `TutorialEngine` and `TutorialCatalog`
      3. Emit `tutorials_catalog_reloaded` signal
      4. Repopulate `_combo_tutorial` in `DiagnosticWindow`
      (No application restart required)
```

### Architectural Principles

1. **In-App Management with Pre-Flight Modal**:
   - `PluginManagerDialog` presents an accessible Qt interface (`&Plugins...` in `DiagnosticWindow`) to inspect, install, and uninstall tutorial packages.
   - Archives selected by the user are validated before any filesystem modification.
   - `PluginInstallPreviewDialog` visualizes manifest metadata, included tutorial titles, and cryptographic provenance.

2. **Explicit User Acknowledgement Guard**:
   - Packages signed by unknown keys (`SIGNED_UNKNOWN_KEY`) or unsigned community packages (`UNSIGNED_COMMUNITY`) are clearly marked.
   - The install action remains disabled until the user checks an explicit acknowledgement checkbox.
   - Packages are never labeled official unless signed by an authentic pinned key in the trust store.
   - Tampered or corrupted packages (`INVALID_TAMPERED`) are blocked unconditionally.

3. **Staged Extraction & Atomic Replacement**:
   - Extraction targets a temporary staging directory on the same filesystem volume (`.staging_<plugin_id>_<rand>`).
   - Quotas, forbidden extensions, and Zip Slip path traversal checks are applied to every entry.
   - Atomic replacement via `os.replace` ensures that partially extracted files never corrupt existing installations.
   - If replacing an existing plugin with explicit consent, a temporary backup is held and restored if the replacement fails.

4. **Safe Uninstallation**:
   - `uninstall_tutorial_plugin` ensures the target directory is strictly contained within the managed plugin directory and is not a symlink.
   - Confirms that `plugin.json` exists and its `id` matches the target plugin ID before deleting.

5. **Zero-Restart Dynamic Synchronization**:
   - After installation or uninstallation, `DesktopController` immediately invokes `reload_tutorial_plugins()`.
   - The catalog emits `tutorials_catalog_reloaded`, which updates the companion UI dropdown seamlessly without terminating or restarting the application.

---

## Phase 10.1 — Visual Region & Directional Cue Selector Overlay Architecture

The following describes the interactive visual authoring overlay and coordinate mapping architecture implemented in Phase 10.1.

### Component Relationship

```
               DiagnosticWindow ("&Cue Selector...")
                              │
                              ▼
                      AuthoringCueDialog
                     (Modal Setup Window)
                              │
                      [🎯 Select on Screen]
                              │
                              ▼
                    AuthoringOverlayWidget
             (Interactive Frameless Window on Target Monitor)
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       Highlight Mode                    Arrow Mode
   (Click & Drag Rectangle)        (Click Start & Drag Head)
              │                               │
              └───────────────┬───────────────┘
                              │
                    HUD Fine-Tuning Bar
               • Real-time Coordinate Spinboxes
               • DPI Physical Pixel Readouts
               • Learner-Accurate Accessibility Preview
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
       [✔ Confirm / Enter]             [✖ Cancel / Esc]
              │                               │
              ▼                               ▼
    Emit HighlightSpec / ArrowSpec      Emit Cancelled
    Hide & Close Overlay                Hide & Close Overlay
    Restore Normal Mouse Interaction    Restore Normal Mouse Interaction
```

### Architectural Principles

1. **Active Authoring vs. Passive Learner Overlay**:
   - While `GuidanceOverlayWidget` is strictly click-through (`WindowTransparentForInput`, `WA_TransparentForMouseEvents`, `NoFocus`), `AuthoringOverlayWidget` is an active pointer tool that captures mouse events exclusively on its own canvas surface.
   - It never clicks, types, focuses, or automates interactions with any underlying desktop applications.
   - It does not grab, record, capture, or stream screen pixels (`QScreen.grabWindow` is never invoked).

2. **Coordinate Systems & Multi-Monitor Topologies**:
   - The overlay geometry matches the target display's logical bounds (`DisplayInfo.x, y, width, height`).
   - Local pointer coordinates inside the widget are mapped to global virtual desktop coordinates using `DisplayInfo.local_to_global(lx, ly)`.
   - For displays positioned to the left of or above the primary monitor, negative virtual desktop coordinates are properly retained and `allow_negative=True` is assigned to `HighlightSpec` and `ArrowSpec`.
   - High-DPI scaling conversions (`logical_to_physical`, `physical_to_logical`) calculate exact physical device pixel dimensions via `DisplayInfo.device_pixel_ratio`.

3. **Pixel-Accurate Accessibility Preview**:
   - The authoring overlay's painting pipeline mirrors `GuidanceOverlayWidget`:
     - Standard mode: translucent fill, solid accent stroke, rounded label pills.
     - High-contrast mode: outer dark halo (6px), inner vibrant yellow stroke (3px), non-color shape indicators (`[■]` corner brackets for highlights, start ring `O` and arrowhead `[▶]` for arrows), and scaled font text.

4. **Guaranteed Dismissal & Interaction Safety**:
   - Pressing `Escape` or clicking `Cancel` triggers `cancel()`, which sets `is_cancelled=True`, emits `cancelled`, and calls `hide()` and `close()`.
   - `closeEvent()` ensures `hide()` is called and dragging state is cleared, preventing lingering click-blocking windows on the user's desktop.

5. **Target Display Scoping & Limitations**:
   - The authoring overlay targets one monitor at a time as selected by the author in the monitor dropdown or active display configuration. It does not span across multiple monitors simultaneously.
   - Coordinate conversion and geometry adaptation are verified across simulated multi-monitor topologies with negative coordinates and scaling, while physical multi-monitor hardware behaviors remain subject to manual verification.

---

## Phase 10.2 — Interactive Step & Tutorial Builder UI Architecture

The following describes the in-app interactive step and tutorial authoring architecture implemented in Phase 10.2.

### Component Relationship

```
                     DiagnosticWindow ("Tutorial &Builder...")
                                     │
                                     ▼
                          TutorialAuthoringDialog
                      (Interactive Studio Workbench)
                                     │
        ┌────────────────────────────┴────────────────────────────┐
        ▼                                                         ▼
Left Pane (Overview)                                     Right Pane (Step Inspector)
• Task Metadata Editor                                   • Step ID & Title
  - task_id, version, title, app, goal                   • Spoken & Displayed Instruction
• Steps Sequence List (`QListWidget`)                    • Progressive Sequential Hints
  - Sequential re-indexing (`order = idx + 1`)           • Expected Outcome (Criteria)
  - ➕ Add Step                                            - description, required_elements
  - 📋 Duplicate Step                                      - verification_hint
  - 🗑 Delete Step (min 1 guard)                          • Visual Guidance Cues List
  - ⬆ Move Up / ⬇ Move Down                                 - 🎯 Select on Screen... (Phase 10.1)
                                                            - 🗑 Remove Cue
                                                            - 👁 Preview Cues (3.5s auto-dismiss)
                                     │
                                     ▼
                    Persistence & Schema Validation
    ┌────────────────────────────────┴────────────────────────────────┐
    ▼                                                                 ▼
In-App Validation (`validate_current`)               Atomic File Operations
• Required non-empty string checks                  • `save_task_definition_to_file`
• Sequential step order invariant                     - Staging `.tmp_<stem>_<rand>.json`
• Unique step ID enforcement                          - `os.replace` volume-local atomic swap
• Reserved built-in collision prevention              - Target overwrite control (`overwrite=True/False`)
  (`BUILTIN_TASK_IDS` protection)                   • `load_task_definition_from_file`
                                                      - Strict UTF-8 decoding & JSON parse
                                                      - Pure-data deserialization (no code exec)
```

### Architectural Principles

1. **Single Source of Truth for Schema & Validation**:
   - `TaskDefinition`, `TaskStep` (aliased as `TaskStepDefinition`), `ExpectedOutcome`, and `OverlayHint` in `src/octa/intelligence/tasks.py` serve as the immutable single source of truth for all tutorial data.
   - Validation logic is executed prior to writing any file to disk via `TaskDefinition.from_dict` and `TaskDefinition.__post_init__`.

2. **Reserved Built-in Tutorial Protection**:
   - The editor explicitly queries `BUILTIN_TASK_IDS` from `octa.tutorials.catalog` (`octa-welcome-task`, `windows-notepad-save`, `windows-explorer-new-folder`).
   - Any attempt to save or validate a tutorial definition whose `task_id` collides with a built-in tutorial is immediately rejected with a descriptive `TaskValidationError`, preventing core tutorial shadowing.

3. **Step Sequence Invariants & Structural CRUD**:
   - Every `TaskStep` enforces `step.order == idx + 1`.
   - Adding, duplicating, deleting, or reordering steps automatically re-indexes step orders sequentially across the list.
   - Deletion enforces a strict safety guard: a tutorial must contain at least one step. The delete button is disabled whenever only one step remains.

4. **Safe Visual Cue Integration & Local Preview**:
   - The visual cue inspector links to Phase 10.1's `AuthoringCueDialog`, allowing authors to visually highlight target UI elements or indicate action flows with directional arrows.
   - Previewing cues instantiates a local `GuidanceOverlayWidget` initialized with high contrast and text scaling options matching user preferences.
   - The preview window is non-recording, never captures pixels or synthesizes input, and features a single-shot auto-dismiss timer (3.5 seconds) to prevent lingering overlay surfaces.

5. **Atomic File Persistence with Volume Locality**:
   - `save_task_definition_to_file` stages data into a temporary file (`.tmp_<stem>_<rand>.json`) in the same directory as the target destination.
   - Atomic replacement is performed via `os.replace`, ensuring failed serializations or disk write interruptions never truncate, corrupt, or zero out existing tutorial files.
   - Loading (`load_task_definition_from_file`) strictly uses standard library `json.loads` without evaluating Python code or accepting arbitrary executable scripts.

---

## Phase 10.3 — Implemented Plugin Project Workspace & Package Wizard Architecture

Phase 10.3 introduces a project-centric authoring workspace model (`TutorialPluginProject`) and an integrated packaging wizard UI (`PluginProjectDialog`, `PackageWizardDialog`), unifying tutorial authoring with Phase 9.1/9.2 secure packaging and Phase 9.3 desktop installation.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Plugin Project Workspace                        │
│                     (`TutorialPluginProject`)                          │
│                                                                        │
│   <project_dir>/                                                       │
│     ├── plugin.json         (Manifest: id, name, version, tutorials)   │
│     ├── README.md           (Documentation asset)                      │
│     └── tutorials/          (Task definitions)                         │
│           ├── step_one.json                                            │
│           └── step_two.json                                            │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
               ┌───────────────────┴───────────────────┐
               ▼                                       ▼
┌─────────────────────────────┐         ┌─────────────────────────────┐
│  Workspace Diagnostics &    │         │  Interactive Studio Dialog  │
│  Pre-Flight Validation      │         │  (`PluginProjectDialog`)    │
│  (`validate_workspace`)     │         │                             │
│                             │         │  • Manifest Metadata Editor │
│  • Schema verification      │◄────────┤  • Tutorial Definitions List│
│  • Forbidden extensions     │         │  • "Open in Builder..."     │
│  • Symlink rejection        │         │  • Diagnostics Report Box   │
│  • Path traversal checks    │         │  • "Package Bundle..."      │
│  • Quota bounds enforcement │         │  • "Test in OCTA" Button    │
│  • Task ID collision guards │         └──────────────┬──────────────┘
└──────────────┬──────────────┘                        │
               │                                       │
               ▼                                       ▼
┌─────────────────────────────┐         ┌─────────────────────────────┐
│  Integrated Package Wizard  │         │  Live "Test in OCTA" Flow   │
│  (`PackageWizardDialog`)    │         │  (`test_install`)           │
│                             │         │                             │
│  • Opt-in digital signing   │         │  • Temp bundle generation   │
│  • Memory-only private key  │         │  • Pre-flight verification  │
│  • Key secret sanitization  │         │  • `PluginInstallPreview`   │
│  • Phase 9.1/9.2 packager   │         │  • Atomic installation      │
│  • Verified .octapack build │         │  • Dynamic catalog reload   │
└─────────────────────────────┘         └─────────────────────────────┘
```

### Architectural Principles & Security Invariants

1. **Workspace Boundary & File System Containment**:
   - `TutorialPluginProject` manages a dedicated project folder structure containing `plugin.json`, documentation (`README.md`), and tutorial task definition files in `tutorials/`.
   - Manifest updates (`save_manifest`, `update_manifest`) write to `.tmp_plugin_<rand>.json` on the same filesystem volume before performing an atomic `os.replace`, guaranteeing manifest integrity against write interruptions.
   - All file operations enforce path containment within the workspace directory, strictly rejecting directory traversal attempts (`..`), absolute paths, and Windows drive roots.

2. **Pre-Flight Workspace Diagnostics & Quota Validation**:
   - `validate_workspace` returns a structured `ProjectValidationReport` evaluating four distinct tiers of project health:
     - **Schema Integrity**: Verifies manifest syntax against `TutorialPluginManifest` rules and parses each referenced tutorial file with `load_task_definition_from_file`.
     - **Code Execution & Symlink Prevention**: Forbids all executable and script extensions (`.exe`, `.bat`, `.cmd`, `.ps1`, `.py`, `.sh`, `.vbs`, etc.) and rejects symlinks anywhere within the workspace tree.
     - **Quota Bounds**: Enforces maximum archive limits ($\le 100$ files, $\le 25$ MB expanded size, $\le 5$ MB per file).
     - **Task ID Collision Guards**: Detects duplicate task IDs within the workspace and strictly forbids collisions with reserved built-in tutorials (`BUILTIN_TASK_IDS`).

3. **Opt-In Digital Signing & Zero Secret Leakage**:
   - Packaging supports both unsigned community packages and cryptographically signed archives via Ed25519.
   - Digital signing is strictly opt-in and disabled by default. Private keys are never persisted to disk, never committed to project files, never included in package archives, and never logged.
   - Private keys are parsed and held strictly in memory during the packaging operation.
   - Exception handlers across `project.py` and `plugin_project_dialog.py` sanitize private key error messages to ensure raw key bytes or sensitive cryptographic traces are never exposed in user-facing dialogs or log outputs.

4. **Live "Test in OCTA" Installation with Safe Preview**:
   - The "Test in OCTA" workflow enables authors to test their created tutorials directly within the host application.
   - A verified `.octapack` bundle is built in a secure temporary directory (`tempfile.mkdtemp`), verified in-memory using `verify_tutorial_package`, and presented via `PluginInstallPreviewDialog` for explicit author confirmation.
   - Installation utilizes Phase 9.3's staged extraction and atomic replacement (`install_tutorial_package`) into the managed plugin directory, protecting against silent overwrites.
   - On completion, `DesktopController.reload_tutorial_plugins` is invoked to immediately refresh the live tutorial catalog and UI selector dropdown without restarting OCTA.
