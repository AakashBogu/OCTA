# OCTA — Project Rules

This document defines the mandatory development workflow and documentation rules for the OCTA project. **Every contributor and every task must follow these rules.**

---

## Documentation Update Workflow

Every task — whether a feature, bugfix, refactor, or infrastructure change — **must** complete the following steps before the task is considered done:

### 1. Update README.md

- Reflect the actual project status (current phase, what exists, what works).
- Never claim functionality that has not been implemented and verified.
- Update the project structure section if files or directories were added or removed.

### 2. Update ROADMAP.md

- Check off completed items.
- Add new milestones or sub-tasks discovered during the work.
- Adjust priorities or phase ordering if plans have changed.

### 3. Update CHANGELOG.md

- Add an entry under `[Unreleased]` describing what changed.
- Group entries by type: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.
- Be specific — name the files, features, or behaviors affected.

### 4. Append to docs/PROGRESS.md

Add a new entry with all of the following fields:

```markdown
## Task: <descriptive title>
**Date:** YYYY-MM-DD
**Phase:** <phase number>

### Files Changed
- `path/to/file` — what changed and why

### Validation
- What was tested or verified
- Exact commands run and their output (or a summary)

### Blockers
- Any issues encountered (or "None")

### Next Step
- What should be done next
```

### 5. Update Architecture Documentation

- If any architectural decisions were made or changed, update `docs/architecture.md`.
- Document the rationale for decisions, not just the decision itself.

### 6. Run Tests and Validation

- Run all relevant tests, linters, or validation commands.
- If no automated tests exist yet, perform manual validation and document the steps.
- Report actual results — do not fabricate passing output.

### 7. Report Actual Results

- Describe what actually happened, including errors and unexpected outcomes.
- Never claim success without evidence.
- If something failed, document the failure and any workaround.

---

## Git Workflow Rules

### Before Every Commit

1. **Review the diff:** Run `git diff --staged` and review every changed line.
2. **Check git status:** Confirm only intended files are staged.
3. **Verify the repository root:** Ensure you are operating in `C:\Users\Admin\Desktop\OCTA`, not a parent or sibling directory.
4. **Write a meaningful commit message:** Use the imperative mood (e.g., "Add user settings dialog", not "Added" or "Adding").

### Never Commit

- `.env` files or any file containing secrets, API keys, or tokens.
- Virtual environment directories (`venv/`, `.venv/`, `env/`).
- IDE-specific configuration that is user-specific (`.vscode/settings.json` with personal paths, etc.).
- Build artifacts, compiled binaries, or `__pycache__/` directories.
- Unrelated personal files.

### Pushing

- Only push when a milestone or meaningful set of changes has been verified.
- Ensure the branch is correct (typically `main` until a branching strategy is defined).
- Confirm the remote is `https://github.com/AakashBogu/OCTA` before pushing.

---

## Code Standards (for future development)

These rules take effect once application code is added:

- **Python:** Follow PEP 8. Use type hints. Write docstrings for public functions and classes.
- **GDScript:** Follow the official Godot GDScript style guide.
- **Comments:** Explain *why*, not *what*. The code should explain *what*.
- **Dependencies:** Pin versions. Document why each dependency is needed.
- **Configuration:** Use environment variables (via `.env`) for secrets. Use config files for non-sensitive settings.

---

## File and Directory Conventions

| Path | Purpose |
|---|---|
| `README.md` | Project overview and quick-start |
| `ROADMAP.md` | Milestone plan |
| `CHANGELOG.md` | Version history |
| `PROJECT_RULES.md` | This file — development rules |
| `docs/PROGRESS.md` | Task-by-task progress log |
| `docs/architecture.md` | Architecture decisions and diagrams |
| `.env.example` | Template for environment variables |
| `.gitignore` | Git ignore rules |

---

## Enforcement

- These rules apply to all contributors: human developers, AI assistants, and automated tools.
- Pull requests that skip documentation updates should be revised before merging.
- If a rule becomes impractical, update this document with the new rule and document the rationale in `docs/PROGRESS.md`.
