# OCTA — Roadmap

> **Last updated:** 2026-11-11
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
- [ ] Install PySide6 and verify a minimal window launches *(deferred to Phase 2.5)*
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

## Phase 2 — Core Python Application *(complete)*

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

### Milestone 2.4 — Communication Protocol & Python Qt WebSocket Server *(complete)*

#### Phase 2.4.1 — Protocol Schema & Datatypes *(complete)*
- [x] Define protocol v1 envelopes and typed message payloads
- [x] Enforce strict SemVer compatibility and 64 KiB UTF-8 message limits
- [x] Define connection lifecycle states and safe authentication error handling
- [x] Test protocol schema, encoding, payloads, and boundaries

#### Phase 2.4.2 — Python Qt WebSocket Server *(complete)*
- [x] Implement QWebSocketServer on the Qt event loop, loopback-only
- [x] Prefer port 8765 with operating-system port-0 fallback
- [x] Enforce single-client authentication, session lifecycle, timeouts, and message-size limits
- [x] Implement correlated heartbeat, state-command, and SHUTDOWN_ACK handling
- [x] Add server/session tests and update architecture and project documentation

### Milestone 2.5 — PySide6 Desktop Interface *(complete)*
- [x] Build system tray integration with PySide6 (`QSystemTrayIcon`, procedural icon, dynamic tooltip, context menu)
- [x] Create companion diagnostic and monitor window (`DiagnosticWindow` with process, server, URL, client, and robot status `IDLE`, `LISTENING`, `THINKING`, `SUCCESS`)
- [x] Connect UI controls to process manager and communication server (`DesktopController`)
- [x] Start server before Godot, rotating credentials and passing via child environment
- [x] Implement bounded graceful shutdown handshake (`SHUTDOWN`/`SHUTDOWN_ACK`) and wait for process exit / force-kill fallback while keeping WebSocket server available before server cleanup
- [x] Ensure restart waits for both old process and WebSocket session to finish closing before relaunching
- [x] Provide graceful fallback to windowed mode when system tray is unavailable
- [x] Add focused UI and controller unit tests with `pytest-qt` (24 tests)

### Milestone 2.6 — Documentation & Phase 2 Wrap-up *(complete)*
- [x] Complete Phase 2 verification, test suite expansion (180 tests), and documentation sync

## Phase 3 — Communication Bridge *(complete)*

### Phase 3.0 — Godot Client Integration & End-to-End Bridge *(complete)*

- [x] Implement the Godot WebSocket client in GDScript (`OctaWsClient`, `OctaProtocol` using `WebSocketPeer`)
- [x] Connect the client to the authenticated Python server on actual loopback address and selected port
- [x] Safe local handoff of port and token (process environment and secure one-time file) without CLI argument exposure
- [x] Correlated HELLO/WELCOME handshake, PING/PONG heartbeats, SET_STATE/SET_STATE_ACK/STATE_CHANGED, and SHUTDOWN/SHUTDOWN_ACK
- [x] Enforce protocol 65,536-byte limit and reject malformed/incompatible messages
- [x] End-to-end integration tests: Python server controls real Godot character state and coordinates graceful shutdown (4 e2e tests + 13 Godot protocol tests)

---

## Phase 4 — Intelligence Layer *(complete)*

### Phase 4.0 — Screen Capture & AI Integration *(complete)*

- [x] Implement Windows screen and region capture gated on explicit user actions (`ScreenCapturer`, `CaptureRegion`, `CapturedImage` held in memory by default and never logged)
- [x] Build and validate task definition format for instructions, steps, expected outcomes, and progress results (`TaskDefinition`, `TaskStep`, `ExpectedOutcome`, `OverlayHint`, `GuidanceInstruction`, `ProgressResult`)
- [x] Require clear user consent before sending captured screen content to a remote service (`ConsentRequest`, `ConsentDecision`, `ScreenShareConsentDialog`, `ConsentManager` bound to exact outbound request including image digest, exact endpoint URL digest, model, system/user prompts, task/step context, and purpose)
- [x] Integrate isolated, configurable OpenAI-compatible LLM adapter (`LLMConfig`, `OpenAICompatibleAdapter` reading endpoint, model, and API key from config/environment, enforcing HTTPS for remote endpoints, explicit loopback-only HTTP for local development, and an explicit no-redirect policy, without live calls in tests)
- [x] Implement structured progress verification (`ProgressVerifier`, `ProgressStatus`: `complete`, `incomplete`, `uncertain`) that never claims success when evidence is unclear
- [x] Create passive, click-through Qt visual overlay system for validated highlights and arrows (`GuidanceOverlayWidget`, `HighlightSpec`, `ArrowSpec`, `OverlayBounds`)
- [x] Add focused unit tests with mocked screen capture and HTTP responses (56 new unit tests; 236 Python tests + 13 Godot protocol tests total)

---

## Phase 5 — Voice *(complete)*

### Phase 5.0 — Voice Interaction *(complete)*

- [x] Verify `PySide6.QtMultimedia` (`QAudioSource`, `QAudioSink`, `QAudioFormat`, `QMediaDevices`, `QBuffer`) availability without adding external dependencies
- [x] Integrate injectable text-to-speech (`TextToSpeechProvider`, `AudioPlaybackBackend`, `DisabledTTSProvider`, `MockTTSProvider`, `RemoteHTTPTTSProvider`, `QtAudioPlaybackBackend`, `create_playback_backend`) and speech-to-text (`SpeechToTextProvider`, `DisabledSTTProvider`, `MockSTTProvider`, `RemoteHTTPSTTProvider`) interfaces without assuming a paid cloud speech provider
- [x] Implement in-memory push-to-talk microphone capture (`PushToTalkRecorder`, `RecordedAudio`, `TranscriptionResult`, `SynthesizedSpeech`) that requires explicit user initiation (`user_initiated=True`), never records continuously or saves audio by default, and keeps credentials, audio bytes, and transcripts out of logs and persistent files
- [x] Require explicit destination disclosure and single-use user consent (`VoiceConsentRequest`, `VoiceConsentDecision`, `VoiceConsentDialog`, `VoiceConsentManager`) before sending microphone audio or text to any remote speech provider, enforcing HTTPS for remote endpoints and blocking HTTP redirects (`VoiceNoRedirectHandler`)
- [x] Connect push-to-talk voice pipeline to the task and instruction engine (`VoiceInteractionController`, `route_spoken_request_to_instruction`) and wire `QtAudioPlaybackBackend` in `VoiceInteractionController` / `from_app_config` for real WAV instruction readout
- [x] Clearly indicate disabled providers in `DiagnosticWindow`, make microphone activity visibly clear, support immediate cancel/stop (`cancel_or_stop`), and explicitly defer wake-word detection
- [x] Add focused unit tests with mocked and production-configured audio/STT/TTS/playback paths (21 new unit tests; 257 Python tests + 13 Godot protocol tests total)
- [ ] Optional wake-word activation *(explicitly deferred)*

---

## Phase 6 — Integration *(complete)*

### Phase 6.0 — End-to-End Tutorial Engine *(complete)*

- [x] Combine all subsystems into a cohesive tutorial flow (`TutorialEngine` in `src/octa/tutorials/engine.py`: tutorial selection, text-based navigation/hints/manual completion, user-initiated capture + single-use consent + AI verification on a background `QThread`, passive overlay sync, voice step sync, companion state requests, cancellation and bounded shutdown; wired into `DesktopController` and `DiagnosticWindow`)
- [x] Create sample tutorials for common applications (`src/octa/tutorials/catalog.py`: Getting Started with OCTA, Windows Notepad save, Windows File Explorer new folder; JSON tutorial loader)
- [x] User testing and feedback loop (`FeedbackCollector`, `TutorialFeedback`, `TutorialSessionMetrics`, `TutorialFeedbackDialog`, and session metrics tracking across hints, verifications, completions, and ratings)
- [x] Performance optimization
- [x] Installer / distribution packaging (`octa.spec`, `scripts/build_windows_dist.py`: PyInstaller onedir distribution with dual GUI/CLI entry points, bundled Godot assets, Qt platform plugins, secret hygiene audit, and portable ZIP creation)

---

## Phase 7 — Advanced Character *(complete)*

### Phase 7.0 — Enhanced Godot Character *(complete)*

- [x] Build transparent borderless window for desktop overlay (`godot/project.godot`, `godot/scripts/main_scene.gd`: viewport transparency, borderless, always-on-top, default bottom-right usable desktop positioning, left-click mouse dragging)
- [x] Create or integrate a higher-quality 3D character model (`godot/scripts/robot_character.gd`: articulated joints, torso chassis, chest core, neck, head with dark curved visor, digital eye indicators, mouth grille LED bar, dual antennae, articulated arms with pointer finger, thruster ring, ground drop shadow; 100% first-party procedural geometry and materials under MIT license documented in `godot/ASSETS_LICENSE.md`)
- [x] Implement advanced animations (talking, pointing, celebrating) (`godot/scripts/robot_character.gd`: `idle`, `listening`, `thinking`, `talking`, `pointing`, and `celebrating` tracks with speech cadence, outstretched pointing, and victory bouncing)
- [x] Add AnimationTree-based state transitions (`godot/scripts/robot_character.gd`: `AnimationTree` with `AnimationNodeStateMachine`, smooth cross-fade transitions, `AnimationNodeStateMachinePlayback`, and backwards-compatible state mapping)

---

## Phase 8 — Extensibility & Ecosystem *(in progress)*

### Phase 8.1 — External Tutorial Plugin System *(complete)*

- [x] Local-only plugin loader for external tutorial packs with consistent `plugin.json` manifests (`src/octa/tutorials/plugins.py`, `src/octa/tutorials/catalog.py`)
- [x] Strict security boundary: untrusted data handling, zero code execution, rejection of absolute paths, path traversal (`..`), and symlinks resolving outside the plugin directory
- [x] Read-only plugin file operations preventing writes to external tutorial files
- [x] ID collision prevention strictly protecting built-in tutorials (`BUILTIN_TASK_IDS`) and earlier-loaded tutorials from being shadowed
- [x] Secret-safe diagnostic reporting (`PluginLoadDiagnostic`) without crashing application startup or companion monitor
- [x] Provenance tracking and display formatting (`[Plugin: <name>]`) in `TutorialCatalog`, `TutorialEngine`, and `DiagnosticWindow`
- [x] Dynamic plugin reloading via `DesktopController.reload_tutorial_plugins()` and `TutorialEngine.reload_plugins()`
- [x] Configurable plugin search directory (`tutorial_plugin_dir` in `AppConfig` and `OCTA_TUTORIAL_PLUGIN_DIR` environment variable)
- [x] Comprehensive unit and integration test suite (`tests/unit/test_tutorial_plugins.py`) covering valid loading, malformed manifests, path escapes, ID collisions, engine execution, and dynamic UI updates

### Phase 8.2 — Multi-Monitor Geometry & Display Adaptation *(complete)*

- [x] Multi-monitor discovery and topology tracking with `DisplayManager` and `DisplayInfo` (`src/octa/intelligence/display.py`)
- [x] Safe handling of negative virtual desktop coordinates for displays placed left of or above primary display (`CaptureRegion.allow_negative`, `OverlayBounds`, `HighlightSpec`, `ArrowSpec`)
- [x] Screen-coordinate mapping and transformations (`global_to_local`, `local_to_global`, rect mapping, DPI scaling conversions)
- [x] Explicit monitor selection for screen capture and visual overlay cues (`ScreenCapturer`, `GuidanceOverlayWidget.set_display`)
- [x] Graceful fallback on invalid monitor selection or disconnected displays without crashing startup or runtime
- [x] Monitor selector combo and dynamic display change listeners in `DiagnosticWindow` and `DesktopController`
- [x] Comprehensive test suite covering single, multi-monitor, negative coordinates, scaling, dynamic disconnect, and UI selection (`tests/unit/test_display_manager.py`)

---

### Phase 8.3 — Accessibility Adaptations *(complete)*

- [x] Strongly-typed accessibility configuration in `AppConfig` (`accessibility_high_contrast`, `accessibility_text_scale`, `accessibility_sound_cues`) with validation and environment variable loading (`OCTA_ACCESSIBILITY_*`)
- [x] High-contrast overlay cues with dual-stroke rendering (outer dark halo + inner vibrant yellow stroke) guaranteeing readability against both light and dark application backgrounds
- [x] Non-color shape indicators for colorblind and low-vision accessibility (L-shaped corner indicator brackets for highlights, start-point target ring `O` for arrows, and shape badges `[■]` / `[▶]`)
- [x] Dynamic UI and overlay text scaling (0.5x to 3.0x multiplier) with dynamic label and padding measurement preventing text clipping
- [x] In-memory harmonic completion audio chime synthesis (`synthesize_completion_chime_wav`) using standard PCM audio math with zero external files or dependencies
- [x] Optional completion audio player (`TutorialSoundPlayer`) strictly respecting user preferences and disabled audio settings
- [x] Accessible Qt labeling (`setAccessibleName`, `setAccessibleDescription`), keyboard focus policies (`StrongFocus`), and tab order navigation across `DiagnosticWindow` and `TutorialFeedbackDialog`
- [x] Accessibility preference controls in `DiagnosticWindow` (high contrast toggle, text size selector, sound cues toggle, audio cue test button)
- [x] Comprehensive automated test suite (`tests/unit/test_accessibility.py`) covering config validation, overlay contrast, non-color shapes, text scaling, chime synthesis, sound player enable/disable, and UI keyboard/accessibility navigation

---

## Future Ideas *(not scheduled)*

- Plugin distribution tool & package manager (signed archive bundles for external tutorials)
- Localization / multi-language support
- Cloud sync for progress and preferences
