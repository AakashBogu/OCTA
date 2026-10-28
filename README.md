# OCTA — On-screen Companion Tutor & Assistant

**Status:** 🟡 Pre-development (documentation phase)

OCTA is a planned AI-powered desktop tutor for Windows. It aims to guide users through desktop applications with an animated 3D companion, conversational step-by-step instructions, visual screen guidance, and progress verification.

> **Note:** OCTA is in the planning and documentation stage. No application code has been implemented yet. All features described below are proposed goals, not working functionality.

---

## Table of Contents

- [Project Goals](#project-goals)
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
│  • PySide6 system UI    │                        │                         │
└─────────────────────────┘                        └─────────────────────────┘
```

| Layer | Planned Technology | Purpose |
|---|---|---|
| Application logic | Python 3.11+ | Core orchestration, AI pipeline, task engine |
| Desktop UI | PySide6 | System tray, settings dialogs, overlay windows |
| 3D Character | Godot 4.x | Animated companion rendering and interaction |
| IPC | WebSocket (localhost) | Real-time bidirectional messaging between Python and Godot |
| AI | LLM APIs (TBD) | Instruction generation, progress analysis |
| Screen capture | Platform APIs (TBD) | Capturing and analyzing on-screen content |
| Voice | STT / TTS engines (TBD) | Speech recognition and synthesis |

> All technology choices above are proposed and subject to change as development progresses.

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full milestone plan.

| Phase | Description | Status |
|---|---|---|
| 0.1 | Project foundation & documentation | 🔄 In progress |
| 0.2 | Development environment setup | ⬜ Planned |
| 1.0 | Core Python application skeleton | ⬜ Planned |
| 2.0 | Godot 3D character prototype | ⬜ Planned |
| 3.0 | WebSocket bridge | ⬜ Planned |
| 4.0 | Screen capture & AI integration | ⬜ Planned |
| 5.0 | Voice interaction | ⬜ Planned |
| 6.0 | End-to-end tutorial engine | ⬜ Planned |

---

## Development Status

- **Current phase:** 0.1 — Project Foundation
- **What exists:** Documentation files only (`README.md`, `ROADMAP.md`, `CHANGELOG.md`, `PROJECT_RULES.md`, architecture docs).
- **What does not exist yet:** Application code, dependencies, build scripts, installers, or any runnable software.

---

## Getting Started

OCTA has no runnable code yet. To follow the project:

1. Clone the repository:
   ```bash
   git clone https://github.com/AakashBogu/OCTA.git
   cd OCTA
   ```
2. Read the [ROADMAP.md](ROADMAP.md) for planned milestones.
3. Read [PROJECT_RULES.md](PROJECT_RULES.md) for contribution and development rules.

When development begins, setup instructions will be added here.

---

## Project Structure

```
OCTA/
├── docs/
│   ├── architecture.md      # Detailed architecture documentation
│   └── PROGRESS.md           # Task-by-task progress log
├── .env.example              # Template for environment variables
├── .gitignore                # Git ignore rules
├── CHANGELOG.md              # Version changelog
├── PROJECT_RULES.md          # Development rules and workflow requirements
├── README.md                 # This file
└── ROADMAP.md                # Milestone roadmap
```

---

## Contributing

OCTA is in early planning. Contributions are welcome once the project reaches active development. In the meantime:

1. Read [PROJECT_RULES.md](PROJECT_RULES.md) for the documentation and commit workflow.
2. Open an issue on [GitHub](https://github.com/AakashBogu/OCTA/issues) to discuss ideas.
3. All future pull requests must follow the documentation update rules in `PROJECT_RULES.md`.

---

## License

License TBD. To be determined before the first code release.
