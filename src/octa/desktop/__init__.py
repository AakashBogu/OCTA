"""PySide6 desktop user interface and system tray integration for OCTA."""

from __future__ import annotations

from octa.desktop.application import OctaApplication
from octa.desktop.controller import DesktopController
from octa.desktop.plugin_manager_dialog import PluginManagerDialog
from octa.desktop.tray import OctaSystemTray, create_tray_icon
from octa.desktop.tutorial_authoring_dialog import TutorialAuthoringDialog
from octa.desktop.window import DiagnosticWindow

__all__ = [
    "DesktopController",
    "DiagnosticWindow",
    "OctaApplication",
    "OctaSystemTray",
    "PluginManagerDialog",
    "TutorialAuthoringDialog",
    "create_tray_icon",
]
