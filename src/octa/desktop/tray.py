"""System tray icon and context menu for OCTA."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from octa.desktop.controller import DesktopController
from octa.desktop.window import DiagnosticWindow
from octa.process.state import ProcessState


def create_tray_icon() -> QIcon:
    """Generate a crisp robot companion icon programmatically."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Robot head (soft blue rounded rectangle)
    painter.setBrush(QBrush(QColor(89, 140, 199)))
    painter.setPen(QPen(QColor(60, 95, 140), 1.5))
    painter.drawRoundedRect(4, 5, 24, 23, 5, 5)

    # Antenna
    painter.setPen(QPen(QColor(180, 180, 190), 1.5))
    painter.drawLine(16, 5, 16, 2)
    painter.setBrush(QBrush(QColor(240, 80, 80)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(14, 0, 4, 4)

    # Glowing eyes (soft green)
    painter.setBrush(QBrush(QColor(100, 220, 140)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(8, 12, 5, 5)
    painter.drawEllipse(19, 12, 5, 5)

    painter.end()
    return QIcon(pixmap)


class OctaSystemTray(QSystemTrayIcon):
    """System tray icon and context menu for companion lifecycle controls."""

    def __init__(
        self,
        controller: DesktopController,
        window: DiagnosticWindow,
        parent: QApplication | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._window = window

        self.setIcon(create_tray_icon())
        self._build_menu()
        self._connect_signals()
        self._update_state()

    def _build_menu(self) -> None:
        """Create context menu actions for tray."""
        self._menu = QMenu()

        # Show monitor action (default, bold)
        self._action_show = QAction("Show Monitor", self)
        font = QFont()
        font.setBold(True)
        self._action_show.setFont(font)
        self._action_show.triggered.connect(self._show_window)
        self._menu.addAction(self._action_show)

        self._menu.addSeparator()

        # Lifecycle actions
        self._action_start = QAction("Start Companion", self)
        self._action_start.triggered.connect(self._controller.start)
        self._menu.addAction(self._action_start)

        self._action_stop = QAction("Stop Companion", self)
        self._action_stop.triggered.connect(self._controller.stop)
        self._menu.addAction(self._action_stop)

        self._action_restart = QAction("Restart Companion", self)
        self._action_restart.triggered.connect(self._controller.restart)
        self._menu.addAction(self._action_restart)

        self._menu.addSeparator()

        # Exit action
        self._action_exit = QAction("Exit OCTA", self)
        self._action_exit.triggered.connect(self._exit_application)
        self._menu.addAction(self._action_exit)

        self.setContextMenu(self._menu)

    def _connect_signals(self) -> None:
        """Connect signals from tray activation and controller."""
        self.activated.connect(self._on_activated)
        self._controller.status_changed.connect(self._on_status_changed)
        self._controller.server_started.connect(self._on_server_started)
        self._controller.server_stopped.connect(self._on_server_stopped)

    # ──────────────────────────────────────────
    # Actions and Slots
    # ──────────────────────────────────────────

    def _show_window(self) -> None:
        """Show, raise, and activate the diagnostic monitor window."""
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _exit_application(self) -> None:
        """Gracefully stop services and exit the application."""
        self._controller.stop()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle single/double clicks on tray icon."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self._window.isVisible() and not self._window.isMinimized():
                self._window.hide()
            else:
                self._show_window()

    @Slot(str, str, str)
    def _on_status_changed(self, _proc: str, _srv: str, _rbt: str) -> None:
        self._update_state()

    @Slot(str, int, str)
    def _on_server_started(self, _host: str, _port: int, _url: str) -> None:
        self._update_state()

    @Slot()
    def _on_server_stopped(self) -> None:
        self._update_state()

    def _update_state(self) -> None:
        """Update action enabled states and dynamic tooltip."""
        is_running = self._controller.process_manager.is_running
        is_starting = self._controller.process_state == ProcessState.STARTING
        is_stopping = self._controller.process_state == ProcessState.STOPPING

        if is_stopping:
            self._action_start.setEnabled(False)
            self._action_stop.setEnabled(False)
            self._action_restart.setEnabled(False)
        elif is_running or is_starting or self._controller.server_url:
            self._action_start.setEnabled(False)
            self._action_stop.setEnabled(True)
            self._action_restart.setEnabled(True)
        else:
            self._action_start.setEnabled(True)
            self._action_stop.setEnabled(False)
            self._action_restart.setEnabled(False)

        # Dynamic tooltip (never displays token)
        url = self._controller.server_url
        robot = self._controller.robot_state
        if url:
            self.setToolTip(f"OCTA — Companion Tutor\nServer: {url}\nRobot: {robot}")
        else:
            self.setToolTip("OCTA — Companion Tutor\nServer: Stopped")

    # ──────────────────────────────────────────
    # Testing Accessors
    # ──────────────────────────────────────────

    @property
    def action_start(self) -> QAction:
        return self._action_start

    @property
    def action_stop(self) -> QAction:
        return self._action_stop

    @property
    def action_restart(self) -> QAction:
        return self._action_restart

    @property
    def action_show(self) -> QAction:
        return self._action_show

    @property
    def action_exit(self) -> QAction:
        return self._action_exit
