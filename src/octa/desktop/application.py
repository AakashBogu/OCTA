"""Desktop application coordinator managing the controller, window, and system tray."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from octa.core.config import AppConfig
from octa.desktop.controller import DEFAULT_STOP_TIMEOUT_MS, DesktopController
from octa.desktop.tray import OctaSystemTray
from octa.desktop.window import DiagnosticWindow


class OctaApplication(QObject):
    """High-level desktop application setup binding UI, tray, and services."""

    def __init__(
        self,
        config: AppConfig | None = None,
        controller: DesktopController | None = None,
        *,
        headless_godot: bool = False,
        shutdown_timeout_ms: int = DEFAULT_STOP_TIMEOUT_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._logger = logging.getLogger("octa.desktop.app")
        self._config = config if config is not None else AppConfig.load()
        self._headless_godot = headless_godot
        self._shutdown_timeout_ms = max(1, int(shutdown_timeout_ms))

        self._controller = (
            controller
            if controller is not None
            else DesktopController(
                config=self._config,
                headless=self._headless_godot,
                parent=self,
            )
        )

        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self._window = DiagnosticWindow(
            self._controller,
            tray_enabled=self._tray_available,
        )

        self._tray: OctaSystemTray | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Initialize tray, window visibility, and application-level hooks."""
        app = QApplication.instance()

        if self._tray_available:
            self._tray = OctaSystemTray(self._controller, self._window)
            self._tray.show()
            if app is not None:
                # Keep running in the tray even when window is closed
                app.setQuitOnLastWindowClosed(False)
            self._logger.info("System tray initialized.")
        else:
            self._logger.warning(
                "System tray is not available on this environment. Running in windowed mode."
            )
            if app is not None:
                app.setQuitOnLastWindowClosed(True)

        if app is not None:
            app.aboutToQuit.connect(self._on_about_to_quit)

        # Window visibility based on configuration
        if self._config.start_minimized and self._tray_available:
            self._logger.info("Starting minimized to system tray.")
            self._window.hide()
        else:
            self._window.show()

        # Honor auto-start configuration
        if self._config.godot_auto_start:
            self._logger.info("Auto-starting companion services...")
            self._controller.start()

    def _on_about_to_quit(self) -> None:
        """Handle application termination gracefully, waiting for Godot and server cleanup."""
        self._logger.info("Application shutting down, stopping services...")
        self._controller.stop(timeout_ms=self._shutdown_timeout_ms)
        if self._tray is not None:
            self._tray.hide()

    @property
    def controller(self) -> DesktopController:
        """Active desktop controller."""
        return self._controller

    @property
    def window(self) -> DiagnosticWindow:
        """Active diagnostic monitor window."""
        return self._window

    @property
    def tray(self) -> OctaSystemTray | None:
        """System tray icon if available, or None."""
        return self._tray

    @property
    def is_tray_available(self) -> bool:
        """Whether system tray is supported and initialized."""
        return self._tray_available
