"""Unit and integration tests for Phase 10.3 — Plugin Project Workspace & Package Wizard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from PySide6.QtCore import QProcess
from pytestqt.qtbot import QtBot

from octa.communication.server import WebSocketServer
from octa.desktop.controller import DesktopController
from octa.desktop.plugin_manager_dialog import PluginManagerDialog
from octa.desktop.plugin_project_dialog import (
    PackageWizardDialog,
    PluginProjectDialog,
)
from octa.desktop.tutorial_authoring_dialog import TutorialAuthoringDialog
from octa.desktop.window import DiagnosticWindow
from octa.intelligence.tasks import (
    ExpectedOutcome,
    TaskDefinition,
    TaskStep,
    save_task_definition_to_file,
)
from octa.process.manager import GodotProcessManager
from octa.tutorials.catalog import BUILTIN_TASK_IDS
from octa.tutorials.packaging import (
    PackageTrustLevel,
    TrustedKey,
    TrustStore,
    verify_tutorial_package,
)
from octa.tutorials.plugins import (
    TUTORIAL_PLUGIN_MANIFEST_FILENAME,
    PluginCollisionError,
)
from octa.tutorials.project import (
    ProjectValidationError,
    TutorialPluginProject,
)


def _create_sample_task(task_id: str, title: str = "Test Task") -> TaskDefinition:
    return TaskDefinition(
        task_id=task_id,
        version="1.0.0",
        title=title,
        application="TestApp",
        goal="Test Goal",
        steps=(
            TaskStep(
                step_id="step-1",
                order=1,
                title="Action 1",
                instruction="Do action 1",
                expected_outcome=ExpectedOutcome(description="Action 1 done"),
            ),
        ),
    )


def test_plugin_project_create_and_open_roundtrip(tmp_path: Path) -> None:
    """Creating a project sets up directory structure, manifest, and can be reopened."""
    proj_dir = tmp_path / "custom_plugin_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="demo-plugin",
        name="Demo Plugin",
        version="1.2.0",
        author="OCTA Author",
        description="A sample test plugin.",
    )

    assert project.project_dir == proj_dir.resolve()
    assert project.manifest.plugin_id == "demo-plugin"
    assert project.manifest.name == "Demo Plugin"
    assert project.manifest.version == "1.2.0"
    assert project.manifest.author == "OCTA Author"
    assert (proj_dir / TUTORIAL_PLUGIN_MANIFEST_FILENAME).is_file()
    assert (proj_dir / "tutorials").is_dir()
    assert (proj_dir / "README.md").is_file()
    assert len(project.manifest.tutorial_files) == 1

    # Reopen from disk
    reopened = TutorialPluginProject.open(proj_dir)
    assert reopened.manifest.plugin_id == "demo-plugin"
    assert reopened.manifest.name == "Demo Plugin"
    assert reopened.manifest.version == "1.2.0"
    assert reopened.manifest.tutorial_files == project.manifest.tutorial_files


def test_plugin_project_create_rejects_duplicate(tmp_path: Path) -> None:
    """Creating a project where plugin.json already exists raises FileExistsError."""
    proj_dir = tmp_path / "existing_proj"
    TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="p1",
        name="P1",
    )

    with pytest.raises(FileExistsError):
        TutorialPluginProject.create(
            project_dir=proj_dir,
            plugin_id="p1",
            name="P1",
        )


def test_plugin_project_update_manifest(tmp_path: Path) -> None:
    """Updating manifest updates attributes and persists atomically to disk."""
    proj_dir = tmp_path / "update_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="update-me",
        name="Initial Name",
        version="1.0.0",
    )

    project.update_manifest(
        name="Updated Name",
        version="2.0.0",
        author="New Author",
        description="New Desc",
    )

    assert project.manifest.name == "Updated Name"
    assert project.manifest.version == "2.0.0"

    # Reload from disk
    reloaded = TutorialPluginProject.open(proj_dir)
    assert reloaded.manifest.name == "Updated Name"
    assert reloaded.manifest.version == "2.0.0"
    assert reloaded.manifest.author == "New Author"
    assert reloaded.manifest.description == "New Desc"


def test_plugin_project_add_and_remove_tutorial(tmp_path: Path) -> None:
    """Adding a tutorial copies/registers it, and removing cleans up."""
    proj_dir = tmp_path / "tut_manage_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="tut-proj",
        name="Tut Proj",
    )

    # Create an external tutorial file
    ext_dir = tmp_path / "external_tuts"
    ext_dir.mkdir()
    tut2_file = ext_dir / "second-task.json"
    task2 = _create_sample_task("tut-proj-task-2", "Second Task")
    save_task_definition_to_file(task2, tut2_file)

    rel_path = project.add_tutorial_file(tut2_file, copy_into_project=True)
    assert rel_path == "tutorials/second-task.json"
    assert (proj_dir / rel_path).is_file()
    assert len(project.manifest.tutorial_files) == 2

    # Remove tutorial
    ok = project.remove_tutorial_reference(rel_path, delete_from_disk=True)
    assert ok is True
    assert len(project.manifest.tutorial_files) == 1
    assert not (proj_dir / rel_path).exists()

    # Removing the last remaining tutorial should be rejected
    rem_tut = project.manifest.tutorial_files[0]
    with pytest.raises(ProjectValidationError, match="retain at least one tutorial"):
        project.remove_tutorial_reference(rem_tut)


def test_plugin_project_builtin_task_id_collision_rejected(tmp_path: Path) -> None:
    """Adding a tutorial whose task ID collides with built-in IDs is rejected."""
    proj_dir = tmp_path / "collision_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="coll-plugin",
        name="Coll Plugin",
    )

    for builtin_id in BUILTIN_TASK_IDS:
        bad_task = _create_sample_task(builtin_id)
        bad_file = tmp_path / f"bad_{builtin_id}.json"
        save_task_definition_to_file(bad_task, bad_file)

        with pytest.raises(PluginCollisionError, match="reserved built-in"):
            project.add_tutorial_file(bad_file)


def test_plugin_project_validation_clean(tmp_path: Path) -> None:
    """Cleanly created project passes workspace validation."""
    proj_dir = tmp_path / "clean_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="clean-proj",
        name="Clean Project",
        description="A valid workspace.",
        author="OCTA Team",
    )

    report = project.validate_workspace()
    assert report.is_valid is True
    assert len(report.errors) == 0
    assert report.tutorial_count == 1
    assert report.file_count >= 3  # plugin.json, README.md, tutorial.json


def test_plugin_project_validation_rejects_forbidden_extensions(tmp_path: Path) -> None:
    """Workspace validation strictly detects and rejects executable and script extensions."""
    proj_dir = tmp_path / "evil_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="evil-proj",
        name="Evil Project",
    )

    evil_script = proj_dir / "evil.bat"
    evil_script.write_text("@echo off\ncalc.exe", encoding="utf-8")

    report = project.validate_workspace()
    assert report.is_valid is False
    assert any("Forbidden file extension '.bat'" in err for err in report.errors)


def test_plugin_project_validation_rejects_missing_and_corrupt_tutorials(tmp_path: Path) -> None:
    """Validation detects missing tutorial files or files that fail schema validation."""
    proj_dir = tmp_path / "broken_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="broken-proj",
        name="Broken Project",
    )

    # Reference nonexistent tutorial
    project.update_manifest(tutorial_files=("tutorials/ghost.json",))
    report = project.validate_workspace()
    assert report.is_valid is False
    assert any("does not exist on disk" in err for err in report.errors)

    # Create corrupt JSON tutorial
    ghost_file = proj_dir / "tutorials" / "ghost.json"
    ghost_file.write_text("{not json", encoding="utf-8")
    report2 = project.validate_workspace()
    assert report2.is_valid is False
    assert any("failed schema validation" in err for err in report2.errors)


def test_plugin_project_pack_unsigned_and_verify(tmp_path: Path) -> None:
    """Packaging an unsigned workspace produces a verified UNSIGNED_COMMUNITY package."""
    proj_dir = tmp_path / "pack_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="unsigned-pack",
        name="Unsigned Pack",
    )

    out_file = tmp_path / "output.octapack"
    res_path = project.pack_package(output_path=out_file, overwrite=True)
    assert res_path == out_file
    assert out_file.is_file()

    # In-memory package verification
    ver_res = verify_tutorial_package(out_file)
    assert ver_res.is_valid is True
    assert ver_res.trust_level == PackageTrustLevel.UNSIGNED_COMMUNITY
    assert ver_res.manifest is not None
    assert ver_res.manifest.plugin_id == "unsigned-pack"


def test_plugin_project_pack_signed_and_verify(tmp_path: Path) -> None:
    """Packaging with a private key produces a signed package verified against trust store."""
    proj_dir = tmp_path / "signed_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="signed-pack",
        name="Signed Pack",
    )

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_bytes = priv_key.public_key().public_bytes_raw()
    pub_hex = pub_bytes.hex()

    # 1. Pinned TrustStore
    pinned_store = TrustStore(
        keys=[
            TrustedKey(
                key_id="my-key-id",
                name="Author Key",
                algorithm="ed25519",
                public_key_hex=pub_hex,
            )
        ]
    )

    out_file = tmp_path / "signed_output.octapack"
    project.pack_package(
        output_path=out_file,
        overwrite=True,
        signing_key=priv_key,
        key_id="my-key-id",
    )

    # Verify against pinned store -> TRUSTED_SIGNED
    ver_pinned = verify_tutorial_package(out_file, trust_store=pinned_store)
    assert ver_pinned.is_valid is True
    assert ver_pinned.trust_level == PackageTrustLevel.TRUSTED_SIGNED

    # Verify against empty default store -> SIGNED_UNKNOWN_KEY (not in trust store)
    ver_empty = verify_tutorial_package(out_file, trust_store=TrustStore(keys=[]))
    assert ver_empty.is_valid is True
    assert ver_empty.trust_level == PackageTrustLevel.SIGNED_UNKNOWN_KEY


def test_package_wizard_signing_options_and_safety(tmp_path: Path, qtbot: QtBot) -> None:
    """PackageWizardDialog handles opt-in signing, invalid keys, and clears memory safely."""
    proj_dir = tmp_path / "wizard_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="wizard-proj",
        name="Wizard Proj",
    )

    dlg = PackageWizardDialog(project)
    qtbot.addWidget(dlg)

    # Signing is disabled by default
    assert dlg._chk_enable_signing.isChecked() is False
    assert dlg._txt_key.isEnabled() is False

    # Enable signing
    dlg._chk_enable_signing.setChecked(True)
    assert dlg._txt_key.isEnabled() is True

    with (
        patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn,
        patch("PySide6.QtWidgets.QMessageBox.critical") as mock_crit,
        patch("PySide6.QtWidgets.QMessageBox.information") as mock_info,
    ):
        # Try building with empty key -> blocked
        dlg._on_build_clicked()
        assert dlg.output_package_path is None
        assert mock_warn.called

        # Try building with invalid hex key -> sanitized error dialog, zero crash
        dlg._txt_key.setText("not-a-valid-hex-key")
        dlg._on_build_clicked()
        assert dlg.output_package_path is None
        assert mock_crit.called

        # Provide valid 64-char hex key
        priv_key = ed25519.Ed25519PrivateKey.generate()
        hex_key = priv_key.private_bytes_raw().hex()
        dlg._txt_key.setText(hex_key)
        out_pkg = tmp_path / "wizard_out.octapack"
        dlg._txt_dest.setText(str(out_pkg))

        dlg._on_build_clicked()
        assert dlg.output_package_path == out_pkg
        assert out_pkg.is_file()
        assert mock_info.called


def test_plugin_project_test_install_flow(tmp_path: Path) -> None:
    """'Test in OCTA' installs package into managed directory and refreshes catalog."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    managed_dir = tmp_path / "managed_plugins"
    managed_dir.mkdir()
    # Direct controller plugin dir to temp managed dir
    with patch.object(DesktopController, "tutorial_plugin_dir", new=managed_dir):
        proj_dir = tmp_path / "test_install_proj"
        project = TutorialPluginProject.create(
            project_dir=proj_dir,
            plugin_id="test-run-pack",
            name="Test Run Pack",
        )

        installed_info, ver_result = project.test_install(
            controller=controller,
            overwrite=False,
        )

        assert installed_info.plugin_id == "test-run-pack"
        assert installed_info.name == "Test Run Pack"
        assert (managed_dir / "test-run-pack" / TUTORIAL_PLUGIN_MANIFEST_FILENAME).is_file()
        assert ver_result.is_valid is True

        # Running again without overwrite should fail (never overwrite silently)
        with pytest.raises(FileExistsError):
            project.test_install(controller=controller, overwrite=False)


def test_plugin_project_dialog_interactions(tmp_path: Path, qtbot: QtBot) -> None:
    """PluginProjectDialog loads project, allows editing, validation, and actions."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    proj_dir = tmp_path / "dialog_proj"
    project = TutorialPluginProject.create(
        project_dir=proj_dir,
        plugin_id="dialog-proj",
        name="Dialog Proj",
    )

    dlg = PluginProjectDialog(controller=controller, project=project)
    qtbot.addWidget(dlg)

    assert dlg._txt_plugin_id.text() == "dialog-proj"
    assert dlg._txt_name.text() == "Dialog Proj"
    assert dlg._list_tutorials.count() == 1

    # Validate workspace
    dlg._on_validate_clicked()
    assert "Workspace is valid" in dlg._txt_diagnostics.toPlainText()

    # Save manifest
    with patch("PySide6.QtWidgets.QMessageBox.information"):
        dlg._txt_name.setText("Renamed Dialog Proj")
        dlg._on_save_manifest_clicked()
        assert project.manifest.name == "Renamed Dialog Proj"


def test_diagnostic_window_and_other_dialogs_open_project_studio(qtbot: QtBot) -> None:
    """DiagnosticWindow, PluginManagerDialog, and TutorialAuthoringDialog link to Project Studio."""
    mock_process = QProcess()
    process_mgr = GodotProcessManager(process=mock_process)
    server = WebSocketServer(port=0)
    controller = DesktopController(process_manager=process_mgr, server=server)

    # 1. PluginManagerDialog
    mgr_dlg = PluginManagerDialog(controller=controller)
    qtbot.addWidget(mgr_dlg)
    assert hasattr(mgr_dlg, "_btn_project_studio")
    with patch("octa.desktop.plugin_project_dialog.PluginProjectDialog.exec") as mock_exec1:
        mgr_dlg._btn_project_studio.click()
        assert mock_exec1.called

    # 2. TutorialAuthoringDialog
    author_dlg = TutorialAuthoringDialog(controller=controller)
    qtbot.addWidget(author_dlg)
    assert hasattr(author_dlg, "_btn_project")
    with patch("octa.desktop.plugin_project_dialog.PluginProjectDialog.exec") as mock_exec2:
        author_dlg._btn_project.click()
        assert mock_exec2.called

    # 3. DiagnosticWindow
    window = DiagnosticWindow(controller=controller, tray_enabled=False)
    qtbot.addWidget(window)
    with patch("octa.desktop.plugin_project_dialog.PluginProjectDialog.exec") as mock_exec3:
        window._on_plugin_project_clicked()
        assert mock_exec3.called
