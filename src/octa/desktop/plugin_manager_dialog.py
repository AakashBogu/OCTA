"""Desktop Plugin Manager Dialog and Pre-Flight Installation Preview (Phase 9.3).

Provides in-app management for local tutorial plugin packages (.octapack):
- Visual listing of active/installed tutorial plugins with provenance and trust status.
- Pre-flight package inspection preview modal showing metadata, tutorials, and trust level.
- Strict blocking of invalid or tampered packages.
- Explicit acknowledgement requirement before installing unsigned or unknown-key packages.
- Confirmation modal with overwrite protection for existing plugins.
- Safe uninstallation with confirmation.
- Dynamic catalog synchronization without requiring application restarts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from octa.tutorials.packaging import (
    InstalledPluginInfo,
    PackageTrustLevel,
    PackageVerificationResult,
    verify_tutorial_package,
)

if TYPE_CHECKING:
    from octa.desktop.controller import DesktopController

logger = logging.getLogger("octa.desktop.plugins")


class PluginInstallPreviewDialog(QDialog):
    """Pre-flight package inspection and consent dialog before installation.

    Displays plugin metadata, included tutorial definitions, and cryptographic trust level.
    Requires explicit user acknowledgement before allowing installation of unsigned or
    unknown-key community packages.
    """

    def __init__(
        self,
        parent: QWidget | None,
        verification_result: PackageVerificationResult,
        package_path: Path | str,
    ) -> None:
        super().__init__(parent)
        self._result = verification_result
        self._package_path = Path(package_path).resolve()
        self._chk_acknowledge: QCheckBox | None = None

        self.setWindowTitle("Install Tutorial Plugin — Verification Preview")
        self.setMinimumWidth(540)
        self.setMinimumHeight(440)
        self.setModal(True)

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        header_font = QFont()
        header_font.setBold(True)
        header_font.setPointSize(11)

        manifest = self._result.manifest
        plugin_name = manifest.name if manifest else self._result.plugin_id
        plugin_ver = manifest.version if manifest else "1.0.0"

        title_lbl = QLabel(f"{plugin_name} (v{plugin_ver})", self)
        title_lbl.setFont(header_font)
        title_lbl.setAccessibleName("Plugin Name and Version")
        layout.addWidget(title_lbl)

        # ── Plugin Metadata Group ──────────────────
        meta_group = QGroupBox("Plugin Metadata", self)
        meta_layout = QVBoxLayout(meta_group)
        meta_layout.setSpacing(4)

        id_text = f"<b>Plugin ID:</b> {self._result.plugin_id}"
        author_val = manifest.author if manifest and manifest.author else "Unspecified Author"
        author_text = f"<b>Author:</b> {author_val}"
        desc_val = (
            manifest.description
            if manifest and manifest.description
            else "No description provided."
        )
        desc_text = f"<b>Description:</b> {desc_val}"
        size_text = (
            f"<b>Archive Size:</b> {self._result.total_compressed_bytes:,} bytes compressed, "
            f"{self._result.total_uncompressed_bytes:,} bytes expanded "
            f"({self._result.file_count} entries)"
        )

        for text in (id_text, author_text, desc_text, size_text):
            lbl = QLabel(text, self)
            lbl.setWordWrap(True)
            meta_layout.addWidget(lbl)

        layout.addWidget(meta_group)

        # ── Included Tutorials Group ──────────────
        tut_group = QGroupBox("Included Tutorials", self)
        tut_layout = QVBoxLayout(tut_group)

        self._list_tutorials = QListWidget(self)
        self._list_tutorials.setAccessibleName("Included Tutorials List")
        self._list_tutorials.setAccessibleDescription("List of tutorial definitions in package")

        if manifest and manifest.tutorial_files:
            for tf in manifest.tutorial_files:
                self._list_tutorials.addItem(f"• {tf}")
        else:
            self._list_tutorials.addItem("• (No tutorials referenced)")

        tut_layout.addWidget(self._list_tutorials)
        layout.addWidget(tut_group)

        # ── Trust & Provenance Status Banner ──────
        trust_group = QGroupBox("Security & Provenance", self)
        trust_layout = QVBoxLayout(trust_group)
        trust_layout.setSpacing(6)

        trust_box = QFrame(self)
        trust_box.setFrameShape(QFrame.StyledPanel)
        trust_box_layout = QVBoxLayout(trust_box)
        trust_box_layout.setSpacing(4)

        trust_level = self._result.trust_level

        if trust_level == PackageTrustLevel.TRUSTED_SIGNED:
            status_title = QLabel("✓ Verified Signature (Trusted Authority)", self)
            status_title.setStyleSheet("color: #00E676; font-weight: bold;")
            signer_info = QLabel(
                f"Signed by pinned authority: <b>{self._result.signer_name}</b> "
                f"[{self._result.signer_id}]",
                self,
            )
            detail_info = QLabel(
                "This package was cryptographically verified against a pinned public key in your "
                "local trust store. Author authenticity is verified.",
                self,
            )
            detail_info.setWordWrap(True)
            trust_box_layout.addWidget(status_title)
            trust_box_layout.addWidget(signer_info)
            trust_box_layout.addWidget(detail_info)

        elif trust_level == PackageTrustLevel.SIGNED_UNKNOWN_KEY:
            status_title = QLabel("⚠ Cryptographic Signature by Unpinned Key", self)
            status_title.setStyleSheet("color: #FFB300; font-weight: bold;")
            signer_info = QLabel(
                f"Signer ID: <b>{self._result.signer_id or 'unknown'}</b> "
                f"({self._result.signer_name})",
                self,
            )
            detail_info = QLabel(
                "The package has a mathematically valid Ed25519 signature, but the signing public "
                "key is NOT pinned in your trust store. The author's real-world identity is "
                "unverified.",
                self,
            )
            detail_info.setWordWrap(True)
            trust_box_layout.addWidget(status_title)
            trust_box_layout.addWidget(signer_info)
            trust_box_layout.addWidget(detail_info)

            self._chk_acknowledge = QCheckBox(
                "I understand this package is from an unpinned / unverified author and "
                "acknowledge the risks.",
                self,
            )
            self._chk_acknowledge.setAccessibleName("Acknowledge Unverified Author Risk")
            trust_box_layout.addWidget(self._chk_acknowledge)

        else:  # UNSIGNED_COMMUNITY
            status_title = QLabel("⚠ Unsigned Community Package", self)
            status_title.setStyleSheet("color: #FFB300; font-weight: bold;")
            detail_info = QLabel(
                "This package contains valid tutorial files and asset digests, but carries no "
                "digital signature. The author's identity cannot be cryptographically verified.",
                self,
            )
            detail_info.setWordWrap(True)
            trust_box_layout.addWidget(status_title)
            trust_box_layout.addWidget(detail_info)

            self._chk_acknowledge = QCheckBox(
                "I understand this package is unsigned and acknowledge the risks.",
                self,
            )
            self._chk_acknowledge.setAccessibleName("Acknowledge Unsigned Package Risk")
            trust_box_layout.addWidget(self._chk_acknowledge)

        trust_layout.addWidget(trust_box)
        layout.addWidget(trust_group)

        # ── Action Buttons ────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._btn_cancel = QPushButton("&Cancel", self)
        self._btn_cancel.setAccessibleName("Cancel Installation")
        self._btn_cancel.clicked.connect(self.reject)

        self._btn_install = QPushButton("&Install Plugin", self)
        self._btn_install.setAccessibleName("Confirm and Install Plugin")
        self._btn_install.setDefault(True)
        self._btn_install.clicked.connect(self.accept)

        if self._chk_acknowledge is not None:
            self._btn_install.setEnabled(False)
            self._chk_acknowledge.toggled.connect(self._btn_install.setEnabled)

        btn_layout.addWidget(self._btn_cancel)
        btn_layout.addWidget(self._btn_install)
        layout.addLayout(btn_layout)

        # Tab order
        if self._chk_acknowledge is not None:
            self.setTabOrder(self._list_tutorials, self._chk_acknowledge)
            self.setTabOrder(self._chk_acknowledge, self._btn_cancel)
        else:
            self.setTabOrder(self._list_tutorials, self._btn_cancel)
        self.setTabOrder(self._btn_cancel, self._btn_install)


class PluginManagerDialog(QDialog):
    """Main in-app management dialog for tutorial plugins.

    Lists installed tutorial plugins, shows provenance and trust badges, allows installing
    new packages with pre-flight verification, and provides safe uninstallation.
    """

    def __init__(
        self,
        controller: DesktopController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._installed_plugins: list[InstalledPluginInfo] = []

        self.setWindowTitle("OCTA Tutorial Plugin Manager")
        self.setMinimumWidth(680)
        self.setMinimumHeight(480)
        self.resize(720, 520)

        self._init_ui()
        self._populate_plugins()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 14)

        header_font = QFont()
        header_font.setBold(True)
        header_font.setPointSize(11)

        # Header section
        header_label = QLabel("Installed Tutorial Plugins", self)
        header_label.setFont(header_font)
        main_layout.addWidget(header_label)

        managed_dir = self._controller.tutorial_plugin_dir
        self._lbl_dir = QLabel(f"Managed Plugin Directory: {managed_dir}", self)
        self._lbl_dir.setStyleSheet("color: #888888; font-size: 11px;")
        self._lbl_dir.setWordWrap(True)
        main_layout.addWidget(self._lbl_dir)

        # Plugins Table
        self._table = QTableWidget(self)
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Plugin Name", "Plugin ID", "Version", "Author", "Trust Status", "Tutorials"]
        )
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._table.setAccessibleName("Installed Plugins Table")
        main_layout.addWidget(self._table, stretch=1)

        # Details Box for selected plugin
        self._details_group = QGroupBox("Plugin Details", self)
        details_layout = QVBoxLayout(self._details_group)
        details_layout.setSpacing(4)

        self._lbl_details_desc = QLabel("Select a plugin to view details.", self)
        self._lbl_details_desc.setWordWrap(True)
        self._lbl_details_tut = QLabel("", self)
        self._lbl_details_tut.setWordWrap(True)
        self._lbl_details_path = QLabel("", self)
        self._lbl_details_path.setStyleSheet("color: #888888; font-size: 11px;")
        self._lbl_details_path.setWordWrap(True)

        details_layout.addWidget(self._lbl_details_desc)
        details_layout.addWidget(self._lbl_details_tut)
        details_layout.addWidget(self._lbl_details_path)
        main_layout.addWidget(self._details_group)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._btn_install = QPushButton("&Install Package (.octapack)...", self)
        self._btn_install.setAccessibleName("Install Plugin Package")
        self._btn_install.setAccessibleDescription(
            "Select and verify a .octapack archive to install"
        )
        self._btn_install.clicked.connect(self._on_install_clicked)

        self._btn_uninstall = QPushButton("&Uninstall Plugin", self)
        self._btn_uninstall.setAccessibleName("Uninstall Selected Plugin")
        self._btn_uninstall.setAccessibleDescription(
            "Safely remove the selected plugin from managed directory"
        )
        self._btn_uninstall.setEnabled(False)
        self._btn_uninstall.clicked.connect(self._on_uninstall_clicked)

        self._btn_project_studio = QPushButton("&Project Studio...", self)
        self._btn_project_studio.setAccessibleName("Open Plugin Project Studio")
        self._btn_project_studio.setAccessibleDescription(
            "Create, author, validate, and package tutorial plugin projects"
        )
        self._btn_project_studio.clicked.connect(self._on_project_studio_clicked)

        self._btn_refresh = QPushButton("&Refresh", self)
        self._btn_refresh.setAccessibleName("Refresh Plugins List")
        self._btn_refresh.clicked.connect(self._populate_plugins)

        self._btn_close = QPushButton("&Close", self)
        self._btn_close.setAccessibleName("Close Plugin Manager")
        self._btn_close.clicked.connect(self.accept)

        btn_layout.addWidget(self._btn_install)
        btn_layout.addWidget(self._btn_uninstall)
        btn_layout.addWidget(self._btn_project_studio)
        btn_layout.addWidget(self._btn_refresh)
        btn_layout.addStretch()
        btn_layout.addWidget(self._btn_close)

        main_layout.addLayout(btn_layout)

        # Tab order
        self.setTabOrder(self._table, self._btn_install)
        self.setTabOrder(self._btn_install, self._btn_uninstall)
        self.setTabOrder(self._btn_uninstall, self._btn_project_studio)
        self.setTabOrder(self._btn_project_studio, self._btn_refresh)
        self.setTabOrder(self._btn_refresh, self._btn_close)

    def _populate_plugins(self) -> None:
        """Scan managed plugin directory and refresh table contents."""
        self._installed_plugins = list(self._controller.get_installed_plugins())
        self._table.setRowCount(len(self._installed_plugins))

        for row, plugin in enumerate(self._installed_plugins):
            item_name = QTableWidgetItem(plugin.name)
            item_id = QTableWidgetItem(plugin.plugin_id)
            item_ver = QTableWidgetItem(plugin.version)
            item_author = QTableWidgetItem(plugin.author or "Community")

            trust_label = plugin.trust_level.name
            if plugin.trust_level == PackageTrustLevel.TRUSTED_SIGNED:
                trust_label = f"TRUSTED ({plugin.signer_id})"
            elif plugin.trust_level == PackageTrustLevel.SIGNED_UNKNOWN_KEY:
                trust_label = "SIGNED (Unpinned)"
            elif plugin.trust_level == PackageTrustLevel.UNSIGNED_COMMUNITY:
                trust_label = "UNSIGNED"
            item_trust = QTableWidgetItem(trust_label)

            item_tut = QTableWidgetItem(str(plugin.tutorial_count))
            item_tut.setTextAlignment(Qt.AlignCenter)

            self._table.setItem(row, 0, item_name)
            self._table.setItem(row, 1, item_id)
            self._table.setItem(row, 2, item_ver)
            self._table.setItem(row, 3, item_author)
            self._table.setItem(row, 4, item_trust)
            self._table.setItem(row, 5, item_tut)

        self._on_table_selection_changed()

    def _on_table_selection_changed(self) -> None:
        selected_rows = self._table.selectionModel().selectedRows()
        if not selected_rows:
            self._btn_uninstall.setEnabled(False)
            self._lbl_details_desc.setText("Select a plugin to view details.")
            self._lbl_details_tut.setText("")
            self._lbl_details_path.setText("")
            return

        row = selected_rows[0].row()
        if 0 <= row < len(self._installed_plugins):
            plugin = self._installed_plugins[row]
            self._btn_uninstall.setEnabled(True)
            desc = plugin.description or "None provided."
            self._lbl_details_desc.setText(f"<b>Description:</b> {desc}")
            tuts_str = ", ".join(plugin.tutorial_titles) if plugin.tutorial_titles else "None"
            self._lbl_details_tut.setText(f"<b>Tutorials ({plugin.tutorial_count}):</b> {tuts_str}")
            self._lbl_details_path.setText(f"Location: {plugin.install_dir}")

    def _on_install_clicked(self) -> None:
        """Open file dialog, run pre-flight inspection, and prompt installation preview."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Tutorial Package",
            "",
            "OCTA Packages (*.octapack *.zip);;All Files (*)",
        )
        if not file_path:
            return

        pkg_path = Path(file_path).resolve()

        # Pre-flight package inspection & verification
        try:
            result = verify_tutorial_package(pkg_path, raise_on_error=False)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Package Read Error",
                f"Failed to inspect tutorial package file:\n\n{exc}",
            )
            return

        # Block invalid or tampered packages completely
        if not result.is_valid or result.trust_level == PackageTrustLevel.INVALID_TAMPERED:
            errors_str = (
                "\n".join(f"• {e}" for e in result.errors) or "Unknown integrity violation."
            )
            QMessageBox.critical(
                self,
                "Package Verification Failed",
                f"This package failed security verification and cannot be installed:\n\n"
                f"{errors_str}",
            )
            return

        # Show pre-flight installation preview dialog
        preview = PluginInstallPreviewDialog(self, result, pkg_path)
        if preview.exec() != QDialog.Accepted:
            return

        # Check for existing installation collision
        already_installed = any(p.plugin_id == result.plugin_id for p in self._installed_plugins)
        overwrite = False
        if already_installed:
            reply = QMessageBox.question(
                self,
                "Overwrite Existing Plugin",
                f"A plugin with ID '{result.plugin_id}' is already installed.\n\n"
                "Do you want to overwrite it with this package?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            overwrite = True

        # Perform atomic installation
        try:
            info = self._controller.install_tutorial_package(pkg_path, overwrite=overwrite)
            QMessageBox.information(
                self,
                "Plugin Installed Successfully",
                f"Plugin '{info.name}' ({info.plugin_id}) was installed successfully.\n\n"
                f"{info.tutorial_count} tutorial(s) added to the active catalog.",
            )
            self._populate_plugins()
        except Exception as exc:
            logger.exception("Failed to install tutorial plugin %s", pkg_path)
            QMessageBox.critical(
                self,
                "Installation Error",
                f"An error occurred while installing the plugin package:\n\n{exc}",
            )

    def _on_uninstall_clicked(self) -> None:
        """Prompt confirmation and safely uninstall the selected plugin."""
        selected_rows = self._table.selectionModel().selectedRows()
        if not selected_rows:
            return

        row = selected_rows[0].row()
        if not (0 <= row < len(self._installed_plugins)):
            return

        plugin = self._installed_plugins[row]
        reply = QMessageBox.question(
            self,
            "Confirm Plugin Uninstallation",
            f"Are you sure you want to uninstall '{plugin.name}' ({plugin.plugin_id})?\n\n"
            f"This will remove the plugin folder and its {plugin.tutorial_count} tutorial(s) "
            "from OCTA.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            removed = self._controller.uninstall_tutorial_plugin(plugin.plugin_id)
            if removed:
                QMessageBox.information(
                    self,
                    "Plugin Uninstalled",
                    f"Plugin '{plugin.name}' was successfully removed.",
                )
                self._populate_plugins()
            else:
                QMessageBox.warning(
                    self,
                    "Uninstall Warning",
                    f"Plugin '{plugin.plugin_id}' was not found in the managed directory.",
                )
        except Exception as exc:
            logger.exception("Failed to uninstall tutorial plugin %s", plugin.plugin_id)
            QMessageBox.critical(
                self,
                "Uninstallation Error",
                f"An error occurred while uninstalling the plugin:\n\n{exc}",
            )

    @Slot()
    def _on_project_studio_clicked(self) -> None:
        """Open the Plugin Project Studio dialog."""
        from octa.desktop.plugin_project_dialog import PluginProjectDialog

        dlg = PluginProjectDialog(controller=self._controller, parent=self)
        dlg.exec()
        self._populate_plugins()
