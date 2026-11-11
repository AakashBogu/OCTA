# OCTA — On-screen Companion Tutor & Assistant

**Status:** 🟢 Phase 7.0 — Enhanced Godot Character (complete)

OCTA is an AI-powered desktop tutor for Windows. It guides users through desktop applications with an animated 3D companion, conversational step-by-step instructions, visual screen guidance, progress verification, and push-to-talk voice interaction.

> Note: OCTA is in active development. Phase 1 (Godot 3D character prototype), Phase 2 (Python application foundation, process manager, protocol v1, WebSocket server, PySide6 desktop interface), Phase 3 (Godot WebSocket client & end-to-end bridge), Phase 4.0 (Screen capture, task validation, consent workflow, OpenAI-compatible LLM adapter, progress verification, and Qt visual overlay), Phase 5.0 (Push-to-talk voice interaction, injectable STT/TTS provider interfaces, remote voice consent disclosure, and instruction readout), Phase 6.0 (End-to-end tutorial engine, sample tutorials, feedback loop, performance optimization, and Windows distribution packaging), and Phase 7.0 (Enhanced Godot character, transparent desktop window, AnimationTree, and 6 animations) are implemented and verified.

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
5. **Voice Interaction** — Push-to-talk speech-to-text and text-to-speech instruction readout with visible microphone activity and immediate cancel/stop.

---

## What Works Now

OCTA currently includes eight verified subsystems:

### 1. Phase 7.0 Enhanced Godot 3D Character & Desktop Window
- **Transparent Borderless Window:** Transparent desktop companion overlay with per-pixel alpha transparency, always-on-top ordering, default bottom-right screen docking, and left-click mouse dragging to position the character anywhere on the desktop.
- **Articulated 3D Robot Character:** Built natively in Godot GDScript from primitive meshes with polished PBR materials: pearlescent chassis torso, glowing chest reactor core, graphite neck joint, head with dark obsidian curved visor, digital LED eyes, audio waveform mouth grille, dual angled antennae with glowing status tips, articulated arms with shoulders, upper arms, forearms, hands, pointer finger for on-screen guidance, anti-gravity thruster ring, and soft alpha-blended ground drop shadow disc.
- **Six Expressive Animations:** Defined in `AnimationPlayer` and `AnimationLibrary` (`idle` breathing/bobbing, `listening` attentive tilt/receptive arms, `thinking` hand-to-chin pondering, `talking` speech nodding/cadence gestures, `pointing` outstretched arm and index finger, and `celebrating` overhead victory bounces).
- **AnimationTree State Machine:** `AnimationTree` with `AnimationNodeStateMachine` managing smooth crossfades (0.22s) between all states, dynamic emission modulation during `_process`, and backward-compatible state mapping for protocol v1.0.0 (`IDLE`, `LISTENING`, `THINKING`, `SUCCESS`).
- **Asset Provenance & License:** 100% first-party procedural assets created natively for OCTA under the MIT License (`godot/ASSETS_LICENSE.md`). Zero third-party proprietary meshes or copyright liabilities.

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
- **Process Termination Scope:** The integration tests confirm termination of the direct Godot process. Descendant-process containment is not guaranteed by the current implementation (kernel Job Objects and process-tree traversal are deferred).

### 4. Phase 2.4 Communication Protocol & Qt WebSocket Server
- Versioned protocol v1 envelopes and typed payloads for authentication, heartbeat, state changes, errors, and shutdown acknowledgements.
- A Qt event-loop server using PySide6 QWebSocketServer, bound only to 127.0.0.1.
- The server prefers port 8765 and asks the operating system for an available port if that port is occupied. Port 0 can be requested directly.
- A single authenticated client is accepted at a time. The server generates a fresh in-memory bearer token for each start, compares it safely, and never logs it.
- Handshake, authentication, heartbeat, shutdown, and transport message-size limits are enforced.

### 5. Phase 3.0 Godot WebSocket Client & End-to-End Bridge
- GDScript WebSocket client (`OctaWsClient`, `OctaProtocol`) using Godot 4's `WebSocketPeer` and scene-tree event loop.
- Safe local handoff of server port and bearer token via child process environment (`QProcessEnvironment`) and secure one-time temporary handoff file, without any command-line argument exposure or secret logging.
- Correlated HELLO/WELCOME authentication handshake, bidirectional PING/PONG heartbeats, SET_STATE/SET_STATE_ACK state synchronization, and graceful SHUTDOWN/SHUTDOWN_ACK process termination.
- Strict 65,536-byte message size enforcement, SemVer version validation, and rejection of malformed or incompatible protocol messages.

### 6. Phase 2.5 PySide6 Desktop Interface
- System tray integration (`OctaSystemTray`) with procedural robot companion icon, dynamic tooltip, and context menu controls.
- Companion diagnostic and monitor window (`DiagnosticWindow`) displaying real-time process, server, loopback URL, client authentication, and robot states (`IDLE`, `LISTENING`, `THINKING`, `SUCCESS`).
- Lifecycle orchestration (`DesktopController`, `OctaApplication`) starting the WebSocket server before Godot, coordinating bounded graceful shutdown (`SHUTDOWN`/`SHUTDOWN_ACK`), and waiting for process exit and session closure before server teardown or restart.

### 7. Phase 4.0 Screen Capture & AI Integration (`src/octa/intelligence`)
- **User-Initiated Windows Screen & Region Capture (`capture.py`):** Captures full-screen or rectangular regions in memory using PySide6 (`QScreen.grabWindow` and `QBuffer`). Requires explicit user initiation (`user_initiated=True`), does not save screenshots to disk by default (`save_screenshots=False`), and excludes raw PNG bytes and base64 strings from `repr`, `str`, and logs.
- **Task Definition & Validation (`tasks.py`):** Strictly validated schemas for tasks (`TaskDefinition`), steps (`TaskStep`), observable completion criteria (`ExpectedOutcome`), visual overlay cues (`OverlayHint`), generated guidance (`GuidanceInstruction`), and verification results (`ProgressResult` with `ProgressStatus`: `complete`, `incomplete`, `uncertain`).
- **Remote Screen-Sharing Consent (`consent.py`):** Requires explicit user approval (`ConsentRequest`, `ConsentDecision`, `ScreenShareConsentDialog`, `ConsentManager`) bound internally to the exact outbound request (`request_sha256` over image SHA-256 digest, exact raw endpoint URL SHA-256 digest `endpoint_sha256`, model, system and user prompts, task/step context, and purpose) before sending any captured screen image to a remote AI endpoint. Displays the sanitized endpoint (`scheme://host[:port]/path`), indicates whether URL credentials, query parameters, or a fragment are present with their values redacted (`[REDACTED]`), shows the complete system and user prompts, region coordinates, image dimensions/byte size, and thumbnail preview, never exposes raw URL secrets or endpoint/request/component hash fingerprints in the dialog, `repr`, `str`, logs, or errors, defaults to cancellation, and enforces single-use approval validation that rejects reused or mismatched approvals before any network request.
- **Isolated OpenAI-Compatible LLM Adapter (`llm.py`):** Configurable client (`LLMConfig`, `OpenAICompatibleAdapter`) using Python standard library `urllib.request`. Reads endpoint, model, and API key from `AppConfig`/environment (`OCTA_LLM_*`), requires HTTPS for all remote endpoints (permitting plain HTTP only for loopback hosts under explicit `OCTA_LLM_ALLOW_LOCAL_HTTP=true` / `allow_local_http=True`), never sends a bearer API key to a non-HTTPS remote endpoint, enforces an explicit no-redirect policy (`NoRedirectHandler`, `build_no_redirect_opener`) so images and bearer keys are never forwarded to a redirected destination without fresh consent, redacts credentials from logs/errors, blocks live network calls during automated `pytest` runs, and enforces exact-request user consent before multimodal requests.
- **Conservative Progress Verification (`verifier.py`):** Evaluates screenshots against step expectations (`ProgressVerifier`, `parse_verification_output`) and enforces a strict anti-hallucination policy: downgrades `"complete"` claims to `"uncertain"` whenever confidence is below threshold (`0.75`), required elements are missing/unconfirmed, or model reasoning expresses ambiguity.
- **Passive Qt Visual Overlay (`overlay.py`):** Click-through, non-focusable transparent widget (`GuidanceOverlayWidget`, `HighlightSpec`, `ArrowSpec`, `OverlayBounds`) that validates coordinates against screen bounds and renders highlights and directional arrows with `QPainter`. Strictly passive (`WindowTransparentForInput`, `WindowDoesNotAcceptFocus`, `WA_TransparentForMouseEvents`) — never clicks, types, or controls other applications.

### 8. Phase 5.0 Voice Interaction (`src/octa/voice`)
- **Push-to-Talk In-Memory Audio Capture (`audio.py`):** Uses `PySide6.QtMultimedia` (`QAudioSource`, `QAudioFormat`, `QMediaDevices`, `QBuffer`) and standard-library `wave` (`PushToTalkRecorder`, `QtAudioCaptureBackend`, `MockAudioCaptureBackend`, `RecordedAudio`, `TranscriptionResult`, `SynthesizedSpeech`). Requires explicit user initiation (`user_initiated=True`), never records continuously or saves audio to disk by default (`voice_save_audio=False`), explicitly rejects wake-word or continuous recording modes (`ContinuousRecordingForbiddenError`), supports immediate cancel/stop that discards buffered audio in memory, and excludes raw audio bytes and transcripts from `repr`, `str`, and logs.
- **Injectable Speech-to-Text & Text-to-Speech Providers (`providers.py`):** Runtime-checkable `SpeechToTextProvider`, `TextToSpeechProvider`, and `AudioPlaybackBackend` protocols. Defaults to safe disabled providers (`DisabledSTTProvider`, `DisabledTTSProvider`) so no paid cloud provider is assumed. Includes deterministic mocked providers (`MockSTTProvider`, `MockTTSProvider`, `MockAudioPlaybackBackend`), `PySide6.QtMultimedia` in-memory WAV playback (`QtAudioPlaybackBackend`), and configurable remote HTTPS speech adapters (`RemoteVoiceConfig`, `RemoteHTTPSTTProvider`, `RemoteHTTPTTSProvider`) enforcing HTTPS for remote hosts, opt-in loopback HTTP (`OCTA_VOICE_ALLOW_LOCAL_HTTP=true`), no HTTP redirects (`VoiceNoRedirectHandler`), and test network blocking.
- **Remote Speech Provider Consent (`consent.py`):** Discloses the sanitized destination endpoint (`scheme://host[:port]/path`) and `[REDACTED]` indicators for any URL credentials, query parameters, or fragments (`VoiceConsentRequest`, `VoiceConsentDecision`, `VoiceConsentDialog`, `VoiceConsentManager`), binds single-use approval internally to the exact raw endpoint URL digest and exact audio/text payload digest before sending microphone audio or text to any remote speech provider, defaults to cancellation, and never logs or exposes credentials, audio bytes, transcripts, or hash fingerprints.
- **Push-to-Talk Task Pipeline & Desktop UI (`controller.py`, `desktop/window.py`):** Connects spoken requests to `TaskDefinition`, `TaskStep`, `GuidanceInstruction`, and `ProgressVerifier` (`VoiceInteractionController`, `route_spoken_request_to_instruction`), reads step instructions aloud, surfaces a live visible microphone indicator (`Microphone: RECORDING (LIVE...)` vs `INACTIVE`), and provides immediate `Stop / Cancel Voice` controls in `DiagnosticWindow`.

> **Component Coupling Note:** Python manages the Godot process, hosts the authenticated localhost WebSocket server, provides the PySide6 system tray and diagnostic UI, supplies the Phase 4.0 screen capture, consent, LLM verification, and visual overlay modules, and provides the Phase 5.0 push-to-talk voice pipeline. End-to-end interactive tutorial loop orchestration is planned for Phase 6.0.

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
| AI | OpenAI-compatible HTTP API (`urllib.request`) | Instruction generation, multimodal progress analysis |
| Screen capture | PySide6 (`QScreen.grabWindow`, `QBuffer`) | User-initiated in-memory screen and region capture |
| Visual overlay | PySide6 (`QPainter`, click-through `QWidget`) | Passive on-screen highlights and directional arrows |
| Voice | PySide6 (`QtMultimedia`) + Injectable STT/TTS (`urllib.request` / local) | Push-to-talk microphone capture, speech recognition, and instruction readout |

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
| 2.4.1 | Communication protocol v1 | ✅ Complete |
| 2.4.2 | Python Qt WebSocket server | ✅ Complete |
| 3.0 | Godot WebSocket client and end-to-end bridge | ✅ Complete |
| 2.5 | PySide6 desktop interface | ✅ Complete |
| 4.0 | Screen capture & AI integration | ✅ Complete |
| 5.0 | Voice interaction (Push-to-Talk) | ✅ Complete |
| 6.0 | End-to-end tutorial engine & packaging | ✅ Complete |
| 7.0 | Enhanced Godot character & desktop window | ✅ Complete |

---

## Development Status

- **Current phase:** Phase 7.0 Complete — Enhanced Godot character, transparent borderless desktop window, mouse dragging, 6 expressive animations (`idle`, `listening`, `thinking`, `talking`, `pointing`, `celebrating`), and `AnimationTree` state machine implemented, documented, and verified.
- **What exists:**
  - Runnable Godot 4.7 project with transparent borderless desktop overlay, articulated 3D character, 6 animations, `AnimationTree` state machine, 100% procedural assets under MIT license (`ASSETS_LICENSE.md`), and embedded `OctaWsClient`.
  - Python 3.12 package foundation with CLI, configuration system, structured logging, `GodotProcessManager`, authenticated `WebSocketServer`, and secure credential handoff.
  - PySide6 desktop interface (`src/octa/desktop`) featuring system tray controls (`OctaSystemTray`), companion diagnostic window (`DiagnosticWindow`), high-level coordinator (`OctaApplication`), and lifecycle controller (`DesktopController`).
  - Phase 4.0 Intelligence Layer (`src/octa/intelligence`) with user-initiated in-memory screen/region capture (`ScreenCapturer`), task/step/outcome/result validation (`TaskDefinition`, `ProgressResult`), screen-sharing consent disclosure and cancellation (`ScreenShareConsentDialog`, `ConsentManager`), isolated OpenAI-compatible LLM adapter (`OpenAICompatibleAdapter`), conservative progress verifier (`ProgressVerifier`), and click-through Qt visual overlay (`GuidanceOverlayWidget`).
  - Phase 5.0 Voice Interaction (`src/octa/voice`) with push-to-talk in-memory audio capture (`PushToTalkRecorder`), injectable STT/TTS/playback interfaces (`SpeechToTextProvider`, `TextToSpeechProvider`, `AudioPlaybackBackend`), mocked and remote HTTPS adapters (`MockSTTProvider`, `MockTTSProvider`, `RemoteHTTPSTTProvider`, `RemoteHTTPTTSProvider`), remote voice consent disclosure (`VoiceConsentDialog`, `VoiceConsentManager`), task/instruction pipeline routing (`VoiceInteractionController`), visible microphone status, and immediate cancel/stop.
  - Phase 6.0 Tutorial Engine (`src/octa/tutorials`) with `TutorialEngine` orchestrating tutorial selection, text-based step navigation and manual completion, user-initiated capture with single-use consent and background AI verification, overlay and voice synchronization, built-in sample tutorials (OCTA welcome, Windows Notepad, Windows File Explorer), structured feedback collection (`FeedbackCollector`, `TutorialFeedbackDialog`, `TutorialSessionMetrics`), performance optimizations, and standalone Windows packaging (`octa.spec`, `build_windows_dist.py`).
  - Full automated test suite: 288 Python tests passing + 79 headless Godot tests passing (66 enhanced character & animation tests + 13 protocol tests).
- **What does not exist yet:** Wake-word detection (explicitly deferred), multi-monitor support, and cloud sync.

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

### Building the Windows Standalone Distribution

To build a standalone, portable Windows distribution bundle (`dist/OCTA/` and `dist/OCTA-v0.1.0-windows-x64.zip`) without requiring a Python installation on the target machine:

```powershell
python scripts/build_windows_dist.py --clean --zip
```

This automated build script:
1. Compiles `src/octa` via PyInstaller using `octa.spec` into a high-performance directory bundle.
2. Produces dual executables:
   - `dist/OCTA/OCTA.exe`: Windowed desktop GUI application (no flashing terminal window).
   - `dist/OCTA/octa-cli.exe`: Console CLI application for automation, testing, and `--help` / `--version`.
3. Bundles required Qt runtime plugins (`qwindows.dll`, `tls`, `multimedia`) and Godot 4 companion assets (`godot/project.godot`, `scenes/`, `scripts/`).
4. Performs a secret-hygiene audit to guarantee `.env`, API keys, certificates, logs, and user telemetry are never included in the package.
5. Executes automated runtime smoke tests on the built binaries before generating the final portable ZIP archive.

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
│   │   ├── main_scene.gd         # Scene setup, environment, UI, and client integration
│   │   ├── robot_character.gd    # Robot construction, animation, state machine
│   │   ├── ws_client.gd          # OctaWsClient Node with WebSocketPeer lifecycle
│   │   └── ws_protocol.gd        # OctaProtocol framing, SemVer check, schemas
│   ├── tests/
│   │   └── test_ws_protocol.gd   # Headless Godot test runner for protocol and client
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
│       ├── communication/        # Protocol v1, Qt WebSocket server, and handoff
│       │   ├── __init__.py       # Public communication exports
│       │   ├── exceptions.py     # Protocol, authentication, and transport exceptions
│       │   ├── handoff.py        # Safe process environment and file credential handoff
│       │   ├── messages.py       # Envelopes and typed protocol payloads
│       │   ├── protocol.py       # Strict SemVer, JSON encoding, and size checks
│       │   ├── server.py         # Loopback QWebSocketServer and client sessions
│       │   └── state.py          # Communication lifecycle states
│       ├── desktop/              # PySide6 desktop interface and system tray
│       │   ├── __init__.py       # Desktop module exports
│       │   ├── application.py    # OctaApplication coordinator and window/tray lifecycle
│       │   ├── controller.py     # DesktopController managing server, Godot, and shutdown
│       │   ├── tray.py           # OctaSystemTray icon and context menu
│       │   └── window.py         # DiagnosticWindow companion monitor and log view
│       ├── intelligence/         # Phase 4.0 screen capture, consent, LLM, verifier & overlay
│       │   ├── __init__.py       # Intelligence Layer public exports
│       │   ├── capture.py        # User-initiated Windows screen & region capture in memory
│       │   ├── consent.py        # ConsentRequest, ConsentDecision, dialog & ConsentManager
│       │   ├── exceptions.py     # Capture, task, consent, LLM, and overlay exceptions
│       │   ├── llm.py            # Isolated OpenAI-compatible LLM adapter & secret redaction
│       │   ├── overlay.py        # Passive click-through Qt overlay for highlights & arrows
│       │   ├── tasks.py          # TaskDefinition, TaskStep, ExpectedOutcome, ProgressResult
│       │   └── verifier.py       # Conservative ProgressVerifier & instruction generator
│       ├── process/              # Godot process lifecycle management
│       │   ├── __init__.py       # Process module exports
│       │   ├── exceptions.py     # Process management exception hierarchy
│       │   ├── manager.py        # GodotProcessManager (PySide6 QProcess lifecycle)
│       │   ├── state.py          # ProcessState enum
│       │   └── validator.py      # Executable and project path validation
│       └── voice/                # Phase 5.0 push-to-talk voice interaction
│           ├── __init__.py       # Voice module exports
│           ├── audio.py          # Push-to-talk audio models, WAV codec & capture
│           ├── consent.py        # Remote speech consent disclosure & SHA-256 binding
│           ├── controller.py     # VoiceInteractionController & instruction pipeline routing
│           ├── exceptions.py     # Voice, microphone, consent, and provider exceptions
│           └── providers.py      # Injectable STT/TTS protocols, mocks, HTTP providers & catalog
├── tests/                        # Automated test suite (279 Python tests passing)
│   ├── conftest.py               # Shared test fixtures & custom markers
│   ├── integration/              # Real binary integration tests
│   │   ├── test_end_to_end_bridge.py          # Full Python server and Godot client tests
│   │   └── test_godot_process_integration.py  # Real Godot headless lifecycle tests
│   └── unit/                     # Isolated unit tests and Qt loopback tests
│       ├── test_communication_handoff.py      # Handoff environment & file tests
│       ├── test_communication_protocol.py     # Protocol v1 schema and framing tests
│       ├── test_communication_server.py       # Qt WebSocket server/session tests
│       ├── test_config.py            # Configuration & precedence tests
│       ├── test_consent.py           # Screen-sharing consent disclosure & cancellation tests
│       ├── test_desktop_application.py        # Desktop application setup and fallback tests
│       ├── test_desktop_controller.py         # Controller sequencing and shutdown handshake tests
│       ├── test_desktop_tray.py               # System tray icon and menu action tests
│       ├── test_desktop_window.py             # Diagnostic monitor UI and secret protection tests
│       ├── test_foundation.py        # Package & version CLI tests
│       ├── test_godot_manager.py     # GodotProcessManager lifecycle & timer tests
│       ├── test_llm_adapter.py       # OpenAI-compatible adapter, mock HTTP & redaction tests
│       ├── test_logging.py           # Log creation, rotation & handler tests
│       ├── test_overlay.py           # Passive Qt overlay & coordinate validation tests
│       ├── test_process_validator.py # Path validation unit tests
│       ├── test_progress_verifier.py # Conservative progress verification tests
│       ├── test_screen_capture.py    # User-initiated screen capture & memory/log privacy tests
│       ├── test_tasks.py             # Task, step, outcome, and result schema validation tests
│       ├── test_voice_audio.py       # Push-to-talk capture, WAV codec & privacy tests
│       ├── test_voice_consent.py     # Remote speech destination disclosure & consent tests
│       ├── test_voice_controller.py  # Voice flow, task routing & UI microphone visibility tests
│       └── test_voice_providers.py   # STT/TTS protocols, HTTPS, redirect & mock provider tests
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
