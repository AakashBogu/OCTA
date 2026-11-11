# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for OCTA on Windows.

Builds a reproducible standalone distribution folder in `dist/OCTA/`:
- OCTA.exe: Windowed GUI application (no console window) for desktop & system tray use.
- octa-cli.exe: Console application sharing the same bundle for CLI usage and testing.
- Bundled Godot 4 companion project assets (godot/project.godot, scenes, scripts).
"""

from pathlib import Path

ROOT_DIR = Path.cwd().resolve()
SRC_DIR = ROOT_DIR / "src"

block_cipher = None

# Core and third-party dependencies required at runtime
hidden_imports = [
    "octa",
    "octa.__main__",
    "octa.core",
    "octa.core.config",
    "octa.core.logging",
    "octa.communication",
    "octa.communication.exceptions",
    "octa.communication.messages",
    "octa.communication.protocol",
    "octa.communication.server",
    "octa.communication.state",
    "octa.communication.handoff",
    "octa.process",
    "octa.process.manager",
    "octa.process.validator",
    "octa.process.exceptions",
    "octa.desktop",
    "octa.desktop.application",
    "octa.desktop.controller",
    "octa.desktop.window",
    "octa.desktop.tray",
    "octa.intelligence",
    "octa.intelligence.capture",
    "octa.intelligence.tasks",
    "octa.intelligence.consent",
    "octa.intelligence.llm",
    "octa.intelligence.verifier",
    "octa.intelligence.overlay",
    "octa.intelligence.exceptions",
    "octa.voice",
    "octa.voice.audio",
    "octa.voice.consent",
    "octa.voice.providers",
    "octa.voice.controller",
    "octa.voice.exceptions",
    "octa.tutorials",
    "octa.tutorials.engine",
    "octa.tutorials.catalog",
    "octa.tutorials.feedback",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtWebSockets",
    "PySide6.QtMultimedia",
    "psutil",
    "dotenv",
]

# Explicit exclusions to ensure zero dev tools, tests, or build caches leak
excludes = [
    "pytest",
    "pytest_mock",
    "pytestqt",
    "ruff",
    "tests",
    "tkinter",
    "matplotlib",
    "numpy",
]

# Runtime application assets: Godot project sources (excluding editor caches)
datas = [
    (str(ROOT_DIR / "godot" / "project.godot"), "godot"),
    (str(ROOT_DIR / "godot" / "icon.svg"), "godot"),
    (str(ROOT_DIR / "godot" / "scenes"), "godot/scenes"),
    (str(ROOT_DIR / "godot" / "scripts"), "godot/scripts"),
]

a = Analysis(
    [str(SRC_DIR / "octa" / "__main__.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 1. Windowed GUI Executable (desktop mode, no flashing cmd console)
exe_gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OCTA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# 2. Console Executable (CLI flags, automation, testing)
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="octa-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Combined directory distribution
coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OCTA",
)
