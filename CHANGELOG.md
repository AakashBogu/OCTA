# OCTA — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> This project does not yet follow [Semantic Versioning](https://semver.org/) because no application code has been released.

---

## [Unreleased]

### Added
- **Phase 4.0 — Screen Capture & AI Integration (2026-11-09)**
  - `src/octa/intelligence/exceptions.py` — Dedicated exception hierarchy for the Phase 4 Intelligence Layer (`IntelligenceError`, `ScreenCaptureError`, `UserActionRequiredError`, `CaptureCancelledError`, `CaptureRegionError`, `TaskValidationError`, `ConsentError`, `ConsentDeclinedError`, `LLMError`, `MissingCredentialsError`, `LLMTransportError`, `MalformedModelOutputError`, `OverlayError`, `InvalidOverlayCoordinatesError`).
  - `src/octa/intelligence/capture.py` — Windows screen and region capture (`ScreenCapturer`, `CaptureRegion`, `CapturedImage`, `grab_screen_with_qt`, `encode_qimage_to_png_bytes`) using PySide6 `QGuiApplication.primaryScreen().grabWindow` and in-memory `QBuffer`. Enforces explicit user initiation (`user_initiated=True`), keeps screenshots in memory by default (`save_screenshots=False`), and hides raw PNG bytes and base64 payloads from `repr`, `str`, and logs.
  - `src/octa/intelligence/tasks.py` — Strictly validated task and verification schemas (`TaskDefinition`, `TaskStep`, `ExpectedOutcome`, `OverlayHint`, `OverlayElementType`, `GuidanceInstruction`, `ProgressResult`, `ProgressStatus`: `complete`, `incomplete`, `uncertain`) with JSON/dict serialization and validation.
  - `src/octa/intelligence/consent.py` — Remote transmission consent workflow and modal Qt dialog (`ConsentRequest`, `ConsentDecision`, `ScreenShareConsentDialog`, `ConsentManager`, `compute_outbound_request_sha256`, `format_endpoint_for_disclosure`, `sanitize_endpoint_for_display`). Binds approval internally to the exact raw endpoint URL (`endpoint_sha256`) and complete outbound request (`request_sha256` and `image_sha256`), shows the sanitized endpoint (`scheme://host[:port]/path`) and indicates whether URL credentials, query parameters, or a fragment are present with their values redacted (`[REDACTED]`), never exposes raw URL secrets or endpoint/request/component hash fingerprints in the dialog, `repr`, `str`, logs, or errors, displays complete system and user prompts, defaults to cancellation, and rejects reused or mismatched approvals before any network request.
  - `src/octa/intelligence/llm.py` — Isolated, configurable OpenAI-compatible Chat Completions adapter (`LLMConfig`, `OpenAICompatibleAdapter`, `LLMResponse`, `NoRedirectHandler`, `build_no_redirect_opener`, `validate_llm_endpoint`, `extract_json_object`, `redact_sensitive_text`) using standard-library `urllib.request`. Reads endpoint, model, and API key from `AppConfig`/environment, requires HTTPS for remote endpoints (allowing plain HTTP only for loopback hosts under explicit `allow_local_http=True` / `OCTA_LLM_ALLOW_LOCAL_HTTP=true`), never sends a bearer API key to a non-HTTPS remote endpoint, blocks automatic HTTP redirects (`301`, `302`, `303`, `307`, `308`) so images and bearer keys are never forwarded to unapproved destinations, redacts credentials from logs and error strings, blocks live HTTP calls during `pytest`, and enforces exact-request user consent before multimodal transmissions.
  - `src/octa/intelligence/verifier.py` — Structured progress verifier and instruction generator (`ProgressVerifier`, `parse_verification_output`, `parse_instruction_output`) enforcing a conservative evidence policy that downgrades `"complete"` claims to `"uncertain"` whenever confidence is below threshold (`0.75`), required elements are missing/unconfirmed, or model reasoning indicates ambiguity.
  - `src/octa/intelligence/overlay.py` — Passive, click-through Qt visual overlay (`GuidanceOverlayWidget`, `HighlightSpec`, `ArrowSpec`, `OverlayBounds`, `overlay_hint_to_spec`) validating coordinates against screen geometry and rendering non-interactive highlights and directional arrows with `QPainter` (`WindowTransparentForInput`, `WindowDoesNotAcceptFocus`, `WA_TransparentForMouseEvents`, `NoFocus`).
  - `src/octa/intelligence/__init__.py` — Clean public exports for the Phase 4.0 Intelligence Layer.
  - `src/octa/core/config.py`, `.env.example` — Added `llm_endpoint`, `llm_model`, `llm_api_key` (`repr=False`), `llm_timeout_seconds`, `llm_allow_local_http` (`OCTA_LLM_ALLOW_LOCAL_HTTP`), `is_loopback_host`, and `capture_save_screenshots` configuration support.
  - `tests/unit/test_screen_capture.py`, `test_tasks.py`, `test_consent.py`, `test_llm_adapter.py`, `test_progress_verifier.py`, `test_overlay.py`, `test_config.py` — 56 new focused unit tests covering user-initiated screen capture, screenshot privacy, task validation, full-request/exact-endpoint consent binding and single-use enforcement, HTTPS/loopback-HTTP endpoint enforcement, HTTP redirect blocking, missing credentials, mock HTTP responses, malformed model output, conservative verification downgrades, and invalid overlay coordinates.
- **Phase 2.5 — PySide6 Desktop Interface (2026-11-09)**
  - `src/octa/desktop/controller.py` — `DesktopController` (QObject) coordinating `WebSocketServer` and `GodotProcessManager`. Sequences server startup before Godot launch, generates and passes credentials through child process environment via `prepare_process_environment`, monitors bidirectional signals, coordinates bounded graceful shutdown (`SHUTDOWN`/`SHUTDOWN_ACK`), waits for Godot process exit (allowing the process manager's force-kill fallback to run while Qt's event loop is active and keeping the WebSocket server available until the client/process has finished), waits for WebSocket session closure on stop and restart, and disconnects temporary signal handlers after waiting.
  - `src/octa/desktop/window.py` — `DiagnosticWindow` companion monitor rendering live process state (with PID), server state, loopback WebSocket URL and port, client authentication state, robot companion state (`IDLE`, `LISTENING`, `THINKING`, `SUCCESS`), interactive controls (Start, Stop, Restart, Clear Log), and a monospace activity log console. Auth token is strictly never displayed or logged anywhere.
  - `src/octa/desktop/tray.py` — `OctaSystemTray` providing a procedural robot companion icon (`create_tray_icon`), dynamic tooltip (displaying URL and robot state without secrets), single/double-click window toggle, and context menu actions (`Show Monitor`, `Start Companion`, `Stop Companion`, `Restart Companion`, `Exit OCTA`).
  - `src/octa/desktop/application.py` — `OctaApplication` high-level coordinator binding UI, tray, and services. Honors `godot_auto_start` and `start_minimized` configuration, connects `aboutToQuit` graceful shutdown hook with bounded timeout waiting for Godot and server cleanup, and falls back cleanly to windowed mode (`quitOnLastWindowClosed(True)`) when system tray is unavailable.
  - `src/octa/desktop/__init__.py` — Clean public exports for desktop subsystem (`DesktopController`, `DiagnosticWindow`, `OctaSystemTray`, `OctaApplication`, `create_tray_icon`).
  - `src/octa/__main__.py` — Enhanced CLI entry point supporting `--version`, `--minimized`, `--no-auto-start`, `--headless-godot`, and `--config` options, running `OctaApplication` on the Qt event loop.
  - `src/octa/core/logging.py` — Enhanced `shutdown_logging` with safe exception handling for already-closed standard streams during test teardown.
  - `tests/unit/test_desktop_controller.py` — 8 unit tests verifying initial controller state, start ordering (server listening before Godot process launch), bounded graceful shutdown handshake, delayed process completion before server stop, force-kill timeout fallback while server remains available, restart waiting for old process and WebSocket session closure before relaunching, temporary signal handler disconnection across repeated stops, and signal routing across protocol states (`IDLE`, `LISTENING`, `THINKING`, `SUCCESS`).
  - `tests/unit/test_desktop_window.py` — 6 unit tests validating initial UI state, live dynamic updates on controller signals across all protocol states, token absence from all UI text and logs, button-triggered controller invocations, log clearing, and tray-aware close events.
  - `tests/unit/test_desktop_tray.py` — 5 unit tests validating procedural icon creation, initial menu state, controller/window triggering from tray actions, click toggle visibility, and token absence from tooltips.
  - `tests/unit/test_desktop_application.py` — 5 unit tests verifying configuration honor (auto-start, start-minimized), clean fallback when system tray is unavailable, and `aboutToQuit` waiting for delayed process exit and force-kill fallback before stopping the WebSocket server.
- **Phase 3.0 — Godot WebSocket Client & End-to-End Bridge (2026-11-09)**
  - `godot/scripts/ws_protocol.gd` — `OctaProtocol` utility class providing protocol v1 envelope creation, SemVer validation, 65,536-byte size validation, JSON decoding, and typed payload builders (`HELLO`, `PONG`, `SET_STATE_ACK`, `STATE_CHANGED`, `SHUTDOWN_ACK`, `ERROR`).
  - `godot/scripts/ws_client.gd` — `OctaWsClient` Node implementing the Godot client using `WebSocketPeer` in the normal scene-tree polling loop. Manages connection lifecycle (`CONNECTING`, `CONNECTED`, `AUTHENTICATED`, `CLOSING`, `CLOSED`), automated credential handoff loading, correlated handshake, PING/PONG heartbeat responses, state change signals, and graceful shutdown handshake.
  - `godot/scripts/main_scene.gd` — Integrated `OctaWsClient` with the 3D robot companion and HUD overlay. Dispatches server `SET_STATE` commands to the robot character, returns correlated `SET_STATE_ACK` and `STATE_CHANGED` events, and notifies the server of autonomous/manual user state changes.
  - `godot/tests/test_ws_protocol.gd` — Headless Godot test suite with 13 automated tests covering SemVer validation, state checking, envelope framing, roundtrip encode/decode, 64 KiB size boundary enforcement, malformed JSON handling, and handoff loading.
  - `src/octa/communication/handoff.py` — Safe credential handoff utilities (`prepare_process_environment`, `write_handoff_file`, `read_handoff_file`) providing token passing via child process environment or secure user-restricted temporary file without command-line exposure or secret logging.
  - `src/octa/process/manager.py` — Added optional `env` dictionary parameter to `GodotProcessManager.start(...)` using `QProcessEnvironment` to safely set child environment variables.
  - `tests/unit/test_communication_handoff.py` — 19 unit tests validating parameter validation, environment preparation, handoff file creation/deletion, permission restrictions, and manager environment propagation.
  - `tests/integration/test_end_to_end_bridge.py` — 4 real-binary integration tests verifying full end-to-end handshake, `SET_STATE` roundtrip state transitions, PING/PONG heartbeats, graceful shutdown coordination, file-based handoff with immediate unlinking, and unauthorized token rejection against actual Godot 4.7.2.
- **Phase 2.4.1 — Protocol Schema & Datatypes**
  - src/octa/communication/messages.py, protocol.py, state.py, and exceptions.py — Added typed protocol v1 envelopes and payloads, strict SemVer compatibility checks, UTF-8 JSON framing with a 64 KiB limit, lifecycle states, and secret-safe authentication exceptions.
  - tests/unit/test_communication_protocol.py — Added 26 tests for payload schemas, envelope validation, message-size boundaries, and protocol compatibility.
- **Phase 2.4.2 — Python Qt WebSocket Server (2026-11-09)**
  - src/octa/communication/server.py — Added an event-loop-driven QWebSocketServer bound to 127.0.0.1, preferring port 8765 and falling back to an OS-assigned port. Each start creates a fresh secrets.token_urlsafe(32) bearer token; comparisons use hmac.compare_digest, and token values are never logged.
  - Added single-client WebSocketSession lifecycle, HELLO/WELCOME authentication, correlated PING/PONG and SET_STATE_ACK handling, state-change notifications, and a correlated SHUTDOWN/SHUTDOWN_ACK timeout handshake.
  - Enforced Qt transport and protocol message-size limits, handshake/authentication/heartbeat/shutdown timeouts, and text-only JSON messages.
  - tests/unit/test_communication_server.py — Added 22 loopback tests for binding, fallback, authentication, client limits, message handling, correlation, lifecycle, and limits.
  - No dependency changes; PySide6 already provides Qt WebSockets. The Godot client remains out of scope for this milestone.
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
