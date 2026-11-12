"""Tutorial Plugin Project Workspace & Integrated Package Wizard (Phase 10.3).

Provides an in-app visual studio for authoring, validating, packaging, and testing
OCTA tutorial plugin projects (.octapack archives):
- Project directory creation and workspace loading.
- Manifest editing (plugin ID, name, version, author, description, tutorial files).
- Multi-tutorial management (add, edit in builder, remove).
- Comprehensive pre-flight workspace validation (symlinks, scripts, quotas, ID collisions).
- Integrated packaging wizard with opt-in Ed25519 digital signature signing.
- Safe private key handling: keys are loaded only into memory on demand, never persisted,
  never included in archives, and never leaked in logs or error messages.
- 'Test in OCTA' action: previews package, prompts confirmation, installs into managed
  directory without restarting, and triggers dynamic catalog refresh.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from octa.desktop.controller import DesktopController
from octa.desktop.plugin_manager_dialog import PluginInstallPreviewDialog
from octa.desktop.tutorial_authoring_dialog import TutorialAuthoringDialog
from octa.tutorials.packaging import (
    PackageTrustLevel,
    compute_file_sha256,
    load_private_key_from_hex_or_file,
    verify_tutorial_package,
)
from octa.tutorials.project import (
    TutorialPluginProject,
)

logger = logging.getLogger("octa.desktop.plugin_project")


class NewProjectDialog(QDialog):
    """Dialog to create a new tutorial plugin project workspace."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create New Tutorial Plugin Project")
        self.setMinimumWidth(480)
        self.setModal(True)
        self.created_project: TutorialPluginProject | None = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(14, 14, 14, 14)

        # Directory Row
        row_dir = QHBoxLayout()
        self._txt_dir = QLineEdit(self)
        self._txt_dir.setPlaceholderText("Select workspace folder...")
        self._txt_dir.setAccessibleName("Project Directory")
        self._btn_browse_dir = QPushButton("Browse...", self)
        self._btn_browse_dir.clicked.connect(self._on_browse_dir)
        row_dir.addWidget(QLabel("Folder:", self))
        row_dir.addWidget(self._txt_dir, stretch=1)
        row_dir.addWidget(self._btn_browse_dir)
        layout.addLayout(row_dir)

        # Plugin ID
        row_id = QHBoxLayout()
        self._txt_id = QLineEdit(self)
        self._txt_id.setPlaceholderText("e.g. notepad-advanced")
        self._txt_id.setAccessibleName("Plugin ID")
        row_id.addWidget(QLabel("Plugin ID:", self))
        row_id.addWidget(self._txt_id, stretch=1)
        layout.addLayout(row_id)

        # Name
        row_name = QHBoxLayout()
        self._txt_name = QLineEdit(self)
        self._txt_name.setPlaceholderText("e.g. Advanced Notepad Guide")
        self._txt_name.setAccessibleName("Plugin Name")
        row_name.addWidget(QLabel("Name:", self))
        row_name.addWidget(self._txt_name, stretch=1)
        layout.addLayout(row_name)

        # Version & Author
        row_v = QHBoxLayout()
        self._txt_version = QLineEdit("1.0.0", self)
        self._txt_version.setAccessibleName("Plugin Version")
        self._txt_author = QLineEdit(self)
        self._txt_author.setPlaceholderText("Author / Organization")
        self._txt_author.setAccessibleName("Plugin Author")
        row_v.addWidget(QLabel("Version:", self))
        row_v.addWidget(self._txt_version)
        row_v.addWidget(QLabel("Author:", self))
        row_v.addWidget(self._txt_author, stretch=1)
        layout.addLayout(row_v)

        # Description
        self._txt_desc = QLineEdit(self)
        self._txt_desc.setPlaceholderText("Brief description of the tutorial pack...")
        self._txt_desc.setAccessibleName("Plugin Description")
        layout.addWidget(QLabel("Description:", self))
        layout.addWidget(self._txt_desc)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton("&Cancel", self)
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_create = QPushButton("&Create Project", self)
        self._btn_create.setStyleSheet(
            "background-color: #0284C7; color: white; font-weight: bold; padding: 5px 12px;"
        )
        self._btn_create.clicked.connect(self._on_create_clicked)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_create)
        layout.addLayout(btn_row)

    def _on_browse_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Project Folder")
        if folder:
            self._txt_dir.setText(folder)
            if not self._txt_id.text():
                self._txt_id.setText(Path(folder).name.lower().replace(" ", "-"))

    def _on_create_clicked(self) -> None:
        folder_str = self._txt_dir.text().strip()
        plugin_id = self._txt_id.text().strip()
        name = self._txt_name.text().strip()
        version = self._txt_version.text().strip() or "1.0.0"
        author = self._txt_author.text().strip()
        desc = self._txt_desc.text().strip()

        if not folder_str:
            QMessageBox.warning(self, "Validation Error", "Please select a project folder.")
            return
        if not plugin_id:
            QMessageBox.warning(self, "Validation Error", "Plugin ID is required.")
            return
        if not name:
            QMessageBox.warning(self, "Validation Error", "Plugin name is required.")
            return

        try:
            self.created_project = TutorialPluginProject.create(
                project_dir=folder_str,
                plugin_id=plugin_id,
                name=name,
                version=version,
                author=author,
                description=desc,
            )
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Project Creation Failed", str(exc))


class PackageWizardDialog(QDialog):
    """Packaging and signing options wizard for .octapack archives."""

    def __init__(
        self,
        project: TutorialPluginProject,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self.output_package_path: Path | None = None
        self.setWindowTitle("OCTA — Package Tutorial Plugin (.octapack)")
        self.setMinimumWidth(520)
        self.setModal(True)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # Destination path
        grp_dest = QGroupBox("Package Destination", self)
        dest_layout = QVBoxLayout(grp_dest)
        row_dest = QHBoxLayout()
        default_name = f"{self._project.manifest.plugin_id}.octapack"
        default_path = self._project.project_dir.parent / default_name
        self._txt_dest = QLineEdit(str(default_path), self)
        self._txt_dest.setAccessibleName("Package Output Path")
        self._btn_browse_dest = QPushButton("Browse...", self)
        self._btn_browse_dest.clicked.connect(self._on_browse_dest)
        row_dest.addWidget(self._txt_dest, stretch=1)
        row_dest.addWidget(self._btn_browse_dest)
        dest_layout.addLayout(row_dest)

        self._chk_overwrite = QCheckBox("&Overwrite existing package file", self)
        self._chk_overwrite.setChecked(True)
        dest_layout.addWidget(self._chk_overwrite)
        layout.addWidget(grp_dest)

        # Signing Group (Strictly Opt-In)
        self._grp_signing = QGroupBox("Cryptographic Provenance & Signing (Optional)", self)
        sign_layout = QVBoxLayout(self._grp_signing)
        sign_layout.setSpacing(6)

        self._chk_enable_signing = QCheckBox(
            "&Sign package with Ed25519 digital signature", self._grp_signing
        )
        self._chk_enable_signing.setChecked(False)
        self._chk_enable_signing.toggled.connect(self._on_signing_toggled)
        sign_layout.addWidget(self._chk_enable_signing)

        row_key_id = QHBoxLayout()
        self._txt_key_id = QLineEdit("community", self._grp_signing)
        self._txt_key_id.setPlaceholderText("Key identifier (e.g. author-key-1)")
        self._txt_key_id.setAccessibleName("Signing Key ID")
        self._txt_key_id.setEnabled(False)
        row_key_id.addWidget(QLabel("Key ID:", self._grp_signing))
        row_key_id.addWidget(self._txt_key_id, stretch=1)
        sign_layout.addLayout(row_key_id)

        row_key = QHBoxLayout()
        self._txt_key = QLineEdit(self._grp_signing)
        self._txt_key.setPlaceholderText("64-char private key hex string or file path")
        self._txt_key.setAccessibleName("Private Key Input")
        self._txt_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._txt_key.setEnabled(False)
        self._btn_browse_key = QPushButton("Browse Key...", self._grp_signing)
        self._btn_browse_key.setEnabled(False)
        self._btn_browse_key.clicked.connect(self._on_browse_key)
        row_key.addWidget(QLabel("Private Key:", self._grp_signing))
        row_key.addWidget(self._txt_key, stretch=1)
        row_key.addWidget(self._btn_browse_key)
        sign_layout.addLayout(row_key)

        lbl_safety = QLabel(
            "Security note: Private signing keys are loaded only in memory for signing. "
            "They are never persisted to disk or included inside the .octapack archive.",
            self._grp_signing,
        )
        lbl_safety.setStyleSheet("color: #64748B; font-size: 11px;")
        lbl_safety.setWordWrap(True)
        sign_layout.addWidget(lbl_safety)

        layout.addWidget(self._grp_signing)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton("&Cancel", self)
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_build = QPushButton("📦 &Build Package", self)
        self._btn_build.setStyleSheet(
            "background-color: #0284C7; color: white; font-weight: bold; padding: 6px 14px;"
        )
        self._btn_build.clicked.connect(self._on_build_clicked)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_build)
        layout.addLayout(btn_row)

    def _on_signing_toggled(self, checked: bool) -> None:
        self._txt_key_id.setEnabled(checked)
        self._txt_key.setEnabled(checked)
        self._btn_browse_key.setEnabled(checked)

    def _on_browse_dest(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Tutorial Package",
            self._txt_dest.text(),
            "OCTA Package (*.octapack);;ZIP Archive (*.zip)",
        )
        if path:
            self._txt_dest.setText(path)

    def _on_browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Ed25519 Private Key File",
            "",
            "Key Files (*.key *.hex *.txt);;All Files (*.*)",
        )
        if path:
            self._txt_key.setText(path)

    def _on_build_clicked(self) -> None:
        dest_str = self._txt_dest.text().strip()
        if not dest_str:
            QMessageBox.warning(self, "Missing Output", "Please specify output package path.")
            return

        signing_key: ed25519.Ed25519PrivateKey | None = None
        key_id = self._txt_key_id.text().strip() or "community"

        if self._chk_enable_signing.isChecked():
            key_source = self._txt_key.text().strip()
            if not key_source:
                QMessageBox.warning(
                    self,
                    "Missing Signing Key",
                    "Signing is enabled but no private key was provided.\n"
                    "Please paste a 64-character hex key or select a key file.",
                )
                return
            try:
                signing_key = load_private_key_from_hex_or_file(key_source)
            except Exception:
                # Sanitize error: never leak key contents in dialogs
                QMessageBox.critical(
                    self,
                    "Invalid Signing Key",
                    "Failed to load Ed25519 private key: invalid format.\n"
                    "Key must be a 64-character (32-byte) hexadecimal string or file.",
                )
                return

        try:
            out_path = self._project.pack_package(
                output_path=dest_str,
                overwrite=self._chk_overwrite.isChecked(),
                signing_key=signing_key,
                key_id=key_id,
            )
            self.output_package_path = out_path

            # Cleanly verify output package
            res = verify_tutorial_package(out_path)
            digest = compute_file_sha256(out_path)
            size_kb = out_path.stat().st_size / 1024

            trust_desc = {
                PackageTrustLevel.TRUSTED_SIGNED: "Trusted (Signed with Pinned Key)",
                PackageTrustLevel.SIGNED_UNKNOWN_KEY: (
                    "Signed (Unknown Key - Not in default trust store)"
                ),
                PackageTrustLevel.UNSIGNED_COMMUNITY: "Unsigned (Community Package)",
                PackageTrustLevel.INVALID_TAMPERED: "Invalid / Tampered",
            }.get(res.trust_level, "Unknown")

            QMessageBox.information(
                self,
                "Package Built Successfully",
                f"Tutorial package created successfully:\n\n"
                f"File: {out_path.name}\n"
                f"Location: {out_path.parent}\n"
                f"Size: {size_kb:.1f} KB\n"
                f"Trust Status: {trust_desc}\n"
                f"SHA-256 Digest: {digest[:16]}...{digest[-16:]}\n",
            )
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Packaging Failed", f"Failed to build package:\n\n{exc}")
        finally:
            # Clear sensitive key reference
            signing_key = None


class PluginProjectDialog(QDialog):
    """Main interactive workbench for a Tutorial Plugin Project."""

    def __init__(
        self,
        controller: DesktopController | None = None,
        project: TutorialPluginProject | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._project = project
        self.setWindowTitle("OCTA — Tutorial Plugin Project Studio")
        self.resize(780, 620)
        self.setMinimumSize(680, 520)

        self._init_ui()
        if self._project is not None:
            self._load_project_into_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 14)

        # ── 1. Project Directory Bar ──────────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)
        self._lbl_folder = QLineEdit(self)
        self._lbl_folder.setReadOnly(True)
        self._lbl_folder.setPlaceholderText("No project loaded. Open or create a project folder.")
        self._lbl_folder.setAccessibleName("Active Project Path")
        self._btn_open_proj = QPushButton("📂 &Open...", self)
        self._btn_open_proj.setAccessibleName("Open Existing Project")
        self._btn_open_proj.clicked.connect(self._on_open_project_clicked)
        self._btn_new_proj = QPushButton("✨ &New...", self)
        self._btn_new_proj.setAccessibleName("Create New Project")
        self._btn_new_proj.clicked.connect(self._on_new_project_clicked)

        top_bar.addWidget(QLabel("Project Folder:", self))
        top_bar.addWidget(self._lbl_folder, stretch=1)
        top_bar.addWidget(self._btn_open_proj)
        top_bar.addWidget(self._btn_new_proj)
        main_layout.addLayout(top_bar)

        # ── 2. Splitter: Manifest (Left) & Tutorials / Diagnostics (Right) ─
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left Column: Manifest Editor
        left_widget = QWidget(splitter)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        self._grp_manifest = QGroupBox("Plugin Manifest (plugin.json)", left_widget)
        manifest_layout = QVBoxLayout(self._grp_manifest)
        manifest_layout.setSpacing(6)

        row_id = QHBoxLayout()
        self._txt_plugin_id = QLineEdit(self._grp_manifest)
        self._txt_plugin_id.setReadOnly(True)
        self._txt_plugin_id.setAccessibleName("Plugin ID")
        row_id.addWidget(QLabel("ID:", self._grp_manifest))
        row_id.addWidget(self._txt_plugin_id, stretch=1)
        manifest_layout.addLayout(row_id)

        row_name = QHBoxLayout()
        self._txt_name = QLineEdit(self._grp_manifest)
        self._txt_name.setAccessibleName("Plugin Name")
        row_name.addWidget(QLabel("Name:", self._grp_manifest))
        row_name.addWidget(self._txt_name, stretch=1)
        manifest_layout.addLayout(row_name)

        row_meta = QHBoxLayout()
        self._txt_version = QLineEdit(self._grp_manifest)
        self._txt_version.setAccessibleName("Plugin Version")
        self._txt_author = QLineEdit(self._grp_manifest)
        self._txt_author.setAccessibleName("Plugin Author")
        row_meta.addWidget(QLabel("Ver:", self._grp_manifest))
        row_meta.addWidget(self._txt_version)
        row_meta.addWidget(QLabel("Author:", self._grp_manifest))
        row_meta.addWidget(self._txt_author, stretch=1)
        manifest_layout.addLayout(row_meta)

        manifest_layout.addWidget(QLabel("Description:", self._grp_manifest))
        self._txt_description = QPlainTextEdit(self._grp_manifest)
        self._txt_description.setMaximumHeight(80)
        self._txt_description.setAccessibleName("Plugin Description")
        manifest_layout.addWidget(self._txt_description)

        self._btn_save_manifest = QPushButton("💾 Save Manifest Changes", self._grp_manifest)
        self._btn_save_manifest.setAccessibleName("Save Manifest Changes")
        self._btn_save_manifest.clicked.connect(self._on_save_manifest_clicked)
        manifest_layout.addWidget(self._btn_save_manifest)

        left_layout.addWidget(self._grp_manifest)
        left_layout.addStretch()
        splitter.addWidget(left_widget)

        # Right Column: Tutorials & Diagnostics
        right_widget = QWidget(splitter)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(8)

        # Tutorials List Group
        self._grp_tutorials = QGroupBox("Tutorial Definitions in Package", right_widget)
        tut_layout = QVBoxLayout(self._grp_tutorials)
        tut_layout.setSpacing(6)

        self._list_tutorials = QListWidget(self._grp_tutorials)
        self._list_tutorials.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list_tutorials.setAccessibleName("Tutorial Definitions List")
        tut_layout.addWidget(self._list_tutorials, stretch=1)

        row_tut_btns = QHBoxLayout()
        self._btn_add_tut = QPushButton("➕ Add Tutorial...", self._grp_tutorials)
        self._btn_add_tut.setAccessibleName("Add Tutorial File")
        self._btn_add_tut.clicked.connect(self._on_add_tutorial_clicked)
        self._btn_edit_tut = QPushButton("✏️ Edit in Builder...", self._grp_tutorials)
        self._btn_edit_tut.setAccessibleName("Edit Selected Tutorial")
        self._btn_edit_tut.clicked.connect(self._on_edit_tutorial_clicked)
        self._btn_remove_tut = QPushButton("🗑 Remove", self._grp_tutorials)
        self._btn_remove_tut.setAccessibleName("Remove Selected Tutorial")
        self._btn_remove_tut.clicked.connect(self._on_remove_tutorial_clicked)

        row_tut_btns.addWidget(self._btn_add_tut)
        row_tut_btns.addWidget(self._btn_edit_tut)
        row_tut_btns.addWidget(self._btn_remove_tut)
        tut_layout.addLayout(row_tut_btns)
        right_layout.addWidget(self._grp_tutorials, stretch=1)

        # Diagnostics Group
        self._grp_diagnostics = QGroupBox("Workspace Validation Diagnostics", right_widget)
        diag_layout = QVBoxLayout(self._grp_diagnostics)
        diag_layout.setSpacing(6)

        self._txt_diagnostics = QPlainTextEdit(self._grp_diagnostics)
        self._txt_diagnostics.setReadOnly(True)
        self._txt_diagnostics.setMaximumHeight(100)
        self._txt_diagnostics.setPlaceholderText(
            "Run 'Validate Workspace' to inspect project health..."
        )
        self._txt_diagnostics.setStyleSheet("font-family: monospace; font-size: 11px;")
        diag_layout.addWidget(self._txt_diagnostics)

        self._btn_validate = QPushButton("🔍 Validate Workspace", self._grp_diagnostics)
        self._btn_validate.setAccessibleName("Validate Project Workspace")
        self._btn_validate.clicked.connect(self._on_validate_clicked)
        diag_layout.addWidget(self._btn_validate)

        right_layout.addWidget(self._grp_diagnostics)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)

        main_layout.addWidget(splitter, stretch=1)

        # ── 3. Bottom Action Bar ──────────────────
        bot_bar = QHBoxLayout()
        bot_bar.setSpacing(8)

        self._btn_package = QPushButton("📦 &Package (.octapack)...", self)
        self._btn_package.setStyleSheet("font-weight: bold; padding: 6px 12px;")
        self._btn_package.setAccessibleName("Open Package Wizard")
        self._btn_package.clicked.connect(self._on_package_clicked)

        self._btn_test = QPushButton("🚀 &Test in OCTA", self)
        self._btn_test.setStyleSheet(
            "background-color: #059669; color: white; font-weight: bold; padding: 6px 14px;"
        )
        self._btn_test.setAccessibleName("Test Plugin in OCTA")
        self._btn_test.clicked.connect(self._on_test_in_octa_clicked)

        self._btn_close = QPushButton("&Close", self)
        self._btn_close.clicked.connect(self.accept)

        bot_bar.addWidget(self._btn_package)
        bot_bar.addWidget(self._btn_test)
        bot_bar.addStretch()
        bot_bar.addWidget(self._btn_close)
        main_layout.addLayout(bot_bar)

        self._update_controls_state()

    def _update_controls_state(self) -> None:
        has_proj = self._project is not None
        self._grp_manifest.setEnabled(has_proj)
        self._grp_tutorials.setEnabled(has_proj)
        self._grp_diagnostics.setEnabled(has_proj)
        self._btn_package.setEnabled(has_proj)
        self._btn_test.setEnabled(has_proj and self._controller is not None)

    def _load_project_into_ui(self) -> None:
        if self._project is None:
            return

        self._lbl_folder.setText(str(self._project.project_dir))
        m = self._project.manifest
        self._txt_plugin_id.setText(m.plugin_id)
        self._txt_name.setText(m.name)
        self._txt_version.setText(m.version)
        self._txt_author.setText(m.author)
        self._txt_description.setPlainText(m.description)

        self._refresh_tutorials_list()
        self._update_controls_state()

    def _refresh_tutorials_list(self) -> None:
        self._list_tutorials.clear()
        if self._project is None:
            return

        tasks = self._project.get_tutorial_tasks()
        for rel_path in self._project.manifest.tutorial_files:
            task = tasks.get(rel_path)
            if task:
                text = f"{rel_path} — '{task.title}' ({len(task.steps)} steps)"
            else:
                text = f"{rel_path} — [File missing or invalid]"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, rel_path)
            self._list_tutorials.addItem(item)

    @Slot()
    def _on_new_project_clicked(self) -> None:
        dlg = NewProjectDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.created_project is not None:
            self._project = dlg.created_project
            self._load_project_into_ui()
            self._on_validate_clicked()

    @Slot()
    def _on_open_project_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Tutorial Plugin Project Folder")
        if not folder:
            return

        try:
            self._project = TutorialPluginProject.open(folder)
            self._load_project_into_ui()
            self._on_validate_clicked()
        except Exception as exc:
            QMessageBox.critical(self, "Cannot Open Project", str(exc))

    @Slot()
    def _on_save_manifest_clicked(self) -> None:
        if self._project is None:
            return

        try:
            self._project.update_manifest(
                name=self._txt_name.text().strip(),
                version=self._txt_version.text().strip(),
                author=self._txt_author.text().strip(),
                description=self._txt_description.toPlainText().strip(),
            )
            QMessageBox.information(self, "Manifest Saved", "plugin.json updated successfully.")
        except Exception as exc:
            QMessageBox.critical(self, "Save Manifest Failed", str(exc))

    @Slot()
    def _on_add_tutorial_clicked(self) -> None:
        if self._project is None:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Tutorial JSON File",
            "",
            "Tutorial JSON (*.json);;All Files (*.*)",
        )
        if not file_path:
            return

        try:
            rel_path = self._project.add_tutorial_file(file_path, copy_into_project=True)
            self._refresh_tutorials_list()
            QMessageBox.information(
                self,
                "Tutorial Added",
                f"Tutorial copied and registered as '{rel_path}'.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Cannot Add Tutorial", str(exc))

    @Slot()
    def _on_edit_tutorial_clicked(self) -> None:
        if self._project is None:
            return

        cur_item = self._list_tutorials.currentItem()
        if cur_item is None:
            QMessageBox.warning(self, "Selection Required", "Select a tutorial to edit.")
            return

        rel_path = cur_item.data(Qt.ItemDataRole.UserRole)
        full_path = (self._project.project_dir / rel_path).resolve()
        if not full_path.is_file():
            QMessageBox.warning(self, "Missing File", f"File '{rel_path}' not found on disk.")
            return

        dlg = TutorialAuthoringDialog(controller=self._controller, parent=self)
        dlg.open_file(full_path)
        dlg.exec()
        self._refresh_tutorials_list()

    @Slot()
    def _on_remove_tutorial_clicked(self) -> None:
        if self._project is None:
            return

        cur_item = self._list_tutorials.currentItem()
        if cur_item is None:
            return

        rel_path = cur_item.data(Qt.ItemDataRole.UserRole)
        res = QMessageBox.question(
            self,
            "Confirm Remove",
            f"Remove tutorial '{rel_path}' from manifest?\n\n(File on disk will be preserved).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            try:
                self._project.remove_tutorial_reference(rel_path, delete_from_disk=False)
                self._refresh_tutorials_list()
            except Exception as exc:
                QMessageBox.critical(self, "Remove Failed", str(exc))

    @Slot()
    def _on_validate_clicked(self) -> None:
        if self._project is None:
            return

        report = self._project.validate_workspace()
        lines: list[str] = []
        if report.is_valid:
            lines.append("✔ Workspace is valid and ready for packaging.")
            kb_size = report.total_bytes / 1024
            lines.append(
                f"  Files: {report.file_count} | Size: {kb_size:.1f} KB | "
                f"Tutorials: {report.tutorial_count}"
            )
        else:
            lines.append(f"✖ Found {len(report.errors)} validation error(s):")
            for err in report.errors:
                lines.append(f"  - ERROR: {err}")

        if report.warnings:
            lines.append(f"⚠ {len(report.warnings)} warning(s):")
            for warn in report.warnings:
                lines.append(f"  - WARN: {warn}")

        self._txt_diagnostics.setPlainText("\n".join(lines))

    @Slot()
    def _on_package_clicked(self) -> None:
        if self._project is None:
            return

        report = self._project.validate_workspace()
        if not report.is_valid:
            QMessageBox.critical(
                self,
                "Cannot Package",
                f"Please fix {len(report.errors)} workspace error(s) before packaging.",
            )
            return

        wizard = PackageWizardDialog(self._project, parent=self)
        wizard.exec()

    @Slot()
    def _on_test_in_octa_clicked(self) -> None:
        """Package project to temp archive, preview, confirm, and install into OCTA."""
        if self._project is None or self._controller is None:
            return

        # 1. Workspace pre-flight check
        report = self._project.validate_workspace()
        if not report.is_valid:
            QMessageBox.critical(
                self,
                "Cannot Test in OCTA",
                f"Workspace has {len(report.errors)} error(s). Fix them before testing.",
            )
            return

        # 2. Check if plugin is already installed
        installed = self._controller.get_installed_plugins()
        already_installed = any(p.plugin_id == self._project.manifest.plugin_id for p in installed)
        if already_installed:
            res = QMessageBox.question(
                self,
                "Plugin Already Installed",
                f"Plugin '{self._project.manifest.plugin_id}' is already installed in OCTA.\n\n"
                "Do you want to overwrite it with this test build?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                return

        # 3. Build temp package
        temp_dir = Path(tempfile.mkdtemp(prefix="octa_test_run_"))
        temp_pkg = temp_dir / f"{self._project.manifest.plugin_id}.octapack"

        try:
            self._project.pack_package(output_path=temp_pkg, overwrite=True)
            res = verify_tutorial_package(temp_pkg)

            # 4. Preview and confirmation modal (Phase 9.3)
            preview_dlg = PluginInstallPreviewDialog(
                parent=self,
                verification_result=res,
                package_path=temp_pkg,
            )
            if preview_dlg.exec() != QDialog.DialogCode.Accepted:
                return

            # 5. Install and reload
            info = self._controller.install_tutorial_package(
                package_path=temp_pkg,
                overwrite=True,
            )

            QMessageBox.information(
                self,
                "Test Installation Complete",
                f"Plugin '{info.name}' (v{info.version}) has been installed into OCTA!\n\n"
                f"Active tutorials: {len(info.tutorial_titles)}\n"
                "The live tutorial dropdown in OCTA has been updated.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Test Installation Failed", str(exc))
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
