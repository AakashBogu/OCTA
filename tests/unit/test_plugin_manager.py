"""Unit and integration tests for Phase 9.3 — Desktop Plugin Manager UI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from PySide6.QtWidgets import QDialog, QMessageBox

from octa.desktop.controller import DesktopController
from octa.desktop.plugin_manager_dialog import (
    PluginInstallPreviewDialog,
    PluginManagerDialog,
)
from octa.desktop.window import DiagnosticWindow
from octa.tutorials.packaging import (
    InstalledPluginInfo,
    PackageIntegrityError,
    PackageSecurityError,
    PackageTrustLevel,
    TrustedKey,
    TrustStore,
    extract_tutorial_package,
    get_installed_plugins_info,
    install_tutorial_package,
    pack_tutorial_package,
    uninstall_tutorial_plugin,
    verify_tutorial_package,
)
from octa.tutorials.plugins import PluginCollisionError

SAMPLE_PLUGIN_ID = "paint-pack"
SAMPLE_MANIFEST = {
    "schema_version": "1.0",
    "id": SAMPLE_PLUGIN_ID,
    "name": "MS Paint Tutorial Pack",
    "version": "1.0.0",
    "author": "OCTA Community",
    "description": "Tutorials for creating artwork in MS Paint.",
    "tutorial_files": ["tutorials/draw_shape.json", "tutorials/fill_bucket.json"],
}

SAMPLE_TUTORIAL_1 = {
    "task_id": "plugin.paint-pack.draw_shape",
    "version": "1.0",
    "title": "Draw a Basic Shape",
    "application": "MS Paint",
    "goal": "Draw a rectangle shape in MS Paint.",
    "steps": [
        {
            "step_id": "select_rectangle",
            "order": 1,
            "title": "Select Rectangle Tool",
            "instruction": "Select the rectangle tool from the toolbar.",
            "expected_outcome": {
                "description": "Rectangle tool selected.",
                "required_elements": ["Rectangle tool active"],
            },
        }
    ],
}

SAMPLE_TUTORIAL_2 = {
    "task_id": "plugin.paint-pack.fill_bucket",
    "version": "1.0",
    "title": "Fill with Color",
    "application": "MS Paint",
    "goal": "Fill a closed shape with color.",
    "steps": [
        {
            "step_id": "select_bucket",
            "order": 1,
            "title": "Select Bucket Tool",
            "instruction": "Click the Paint Bucket tool from the tools palette.",
            "expected_outcome": {
                "description": "Fill bucket selected.",
                "required_elements": ["Bucket cursor visible"],
            },
        }
    ],
}


def _create_sample_plugin_dir(base_dir: Path, plugin_id: str = SAMPLE_PLUGIN_ID) -> Path:
    src_dir = base_dir / plugin_id
    src_dir.mkdir(parents=True, exist_ok=True)
    tuts_dir = src_dir / "tutorials"
    tuts_dir.mkdir(parents=True, exist_ok=True)

    manifest_data = dict(SAMPLE_MANIFEST)
    manifest_data["id"] = plugin_id

    (src_dir / "plugin.json").write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    (tuts_dir / "draw_shape.json").write_text(
        json.dumps(SAMPLE_TUTORIAL_1, indent=2), encoding="utf-8"
    )
    (tuts_dir / "fill_bucket.json").write_text(
        json.dumps(SAMPLE_TUTORIAL_2, indent=2), encoding="utf-8"
    )
    (src_dir / "README.md").write_text("# MS Paint Tutorials\nSafe docs.", encoding="utf-8")
    return src_dir


def test_extract_tutorial_package_success(tmp_path: Path) -> None:
    """Safe extraction unpacks all files with verified digests into target directory."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    pkg = pack_tutorial_package(src_dir, tmp_path / "pkg.octapack")
    managed_dir = tmp_path / "installed_plugins"

    info = extract_tutorial_package(pkg, managed_dir)
    assert isinstance(info, InstalledPluginInfo)
    assert info.plugin_id == SAMPLE_PLUGIN_ID
    assert info.name == "MS Paint Tutorial Pack"
    assert info.tutorial_count == 2
    assert "Draw a Basic Shape" in info.tutorial_titles
    assert info.install_dir.is_dir()
    assert (info.install_dir / "plugin.json").is_file()
    assert (info.install_dir / "tutorials" / "draw_shape.json").is_file()
    assert (info.install_dir / "README.md").is_file()
    assert info.trust_level == PackageTrustLevel.UNSIGNED_COMMUNITY


def test_extract_tutorial_package_rejects_tampered(tmp_path: Path) -> None:
    """Tampered package is rejected prior to extraction, leaving no staging artifacts."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    pkg = pack_tutorial_package(src_dir, tmp_path / "pkg.octapack")

    # Corrupt one tutorial file in archive
    import zipfile

    corrupted_pkg = tmp_path / "corrupted.octapack"
    with zipfile.ZipFile(pkg, "r") as zin, zipfile.ZipFile(corrupted_pkg, "w") as zout:
        for item in zin.infolist():
            content = zin.read(item.filename)
            if item.filename.endswith("draw_shape.json"):
                content = content.replace(b"Select the rectangle tool", b"TAMPERED")
            zout.writestr(item, content)

    managed_dir = tmp_path / "installed_plugins"
    with pytest.raises(PackageIntegrityError):
        extract_tutorial_package(corrupted_pkg, managed_dir)

    # Destination directory should not have the plugin installed
    assert not (managed_dir / SAMPLE_PLUGIN_ID).exists()


def test_extract_tutorial_package_built_in_collision(tmp_path: Path) -> None:
    """A package containing tutorial definitions that collide with built-ins is rejected."""
    src_dir = tmp_path / "collision_src"
    src_dir.mkdir(parents=True)
    (src_dir / "tutorials").mkdir()

    manifest_data = dict(SAMPLE_MANIFEST)
    manifest_data["id"] = "collision-pack"
    manifest_data["tutorial_files"] = ["tutorials/collide.json"]

    # Collide with built-in task ID
    collide_tutorial = dict(SAMPLE_TUTORIAL_1)
    collide_tutorial["task_id"] = "octa-welcome-task"  # Built-in ID

    (src_dir / "plugin.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    (src_dir / "tutorials" / "collide.json").write_text(
        json.dumps(collide_tutorial), encoding="utf-8"
    )

    pkg = pack_tutorial_package(src_dir, tmp_path / "collide.octapack")
    managed_dir = tmp_path / "installed_plugins"

    with pytest.raises(
        PluginCollisionError, match="collides with an existing or built-in tutorial"
    ):
        extract_tutorial_package(pkg, managed_dir)


def test_extract_tutorial_package_overwrite_protection(tmp_path: Path) -> None:
    """Extraction refuses to overwrite existing plugin unless overwrite=True is explicit."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    pkg = pack_tutorial_package(src_dir, tmp_path / "pkg.octapack")
    managed_dir = tmp_path / "installed_plugins"

    extract_tutorial_package(pkg, managed_dir)
    assert (managed_dir / SAMPLE_PLUGIN_ID).exists()

    # Re-extracting with overwrite=False raises FileExistsError
    with pytest.raises(FileExistsError, match="already exists"):
        extract_tutorial_package(pkg, managed_dir, overwrite=False)

    # Re-extracting with overwrite=True succeeds
    info = extract_tutorial_package(pkg, managed_dir, overwrite=True)
    assert info.plugin_id == SAMPLE_PLUGIN_ID


def test_uninstall_tutorial_plugin_success(tmp_path: Path) -> None:
    """Plugin is safely uninstalled from managed directory."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    pkg = pack_tutorial_package(src_dir, tmp_path / "pkg.octapack")
    managed_dir = tmp_path / "installed_plugins"

    extract_tutorial_package(pkg, managed_dir)
    assert (managed_dir / SAMPLE_PLUGIN_ID).exists()

    removed = uninstall_tutorial_plugin(SAMPLE_PLUGIN_ID, managed_dir)
    assert removed is True
    assert not (managed_dir / SAMPLE_PLUGIN_ID).exists()

    # Second removal attempt returns False
    assert uninstall_tutorial_plugin(SAMPLE_PLUGIN_ID, managed_dir) is False


def test_uninstall_tutorial_plugin_safety_guards(tmp_path: Path) -> None:
    """Uninstallation strictly rejects directory traversal, invalid IDs, and non-plugin dirs."""
    managed_dir = tmp_path / "installed_plugins"
    managed_dir.mkdir(parents=True)

    # Invalid ID format (traversal attempt)
    with pytest.raises(ValueError, match="Invalid plugin_id format"):
        uninstall_tutorial_plugin("../other_dir", managed_dir)

    with pytest.raises(ValueError, match="Invalid plugin_id format"):
        uninstall_tutorial_plugin("dir/subdir", managed_dir)

    # Non-plugin directory (missing plugin.json)
    fake_dir = managed_dir / "user_photos"
    fake_dir.mkdir()
    (fake_dir / "photo.png").write_bytes(b"image")

    with pytest.raises(PackageSecurityError, match="does not contain 'plugin.json'"):
        uninstall_tutorial_plugin("user_photos", managed_dir)
    assert fake_dir.exists()  # Not deleted

    # Directory with mismatched plugin manifest ID
    mismatch_dir = managed_dir / "plugin_a"
    mismatch_dir.mkdir()
    (mismatch_dir / "plugin.json").write_text(json.dumps({"id": "plugin_b"}))

    with pytest.raises(PackageSecurityError, match="Manifest ID 'plugin_b' in .* does not match"):
        uninstall_tutorial_plugin("plugin_a", managed_dir)
    assert mismatch_dir.exists()  # Not deleted


def test_get_installed_plugins_info(tmp_path: Path) -> None:
    """get_installed_plugins_info inspects and reports metadata for installed plugins."""
    managed_dir = tmp_path / "installed_plugins"
    managed_dir.mkdir(parents=True)

    # Plugin 1: Unsigned
    src1 = _create_sample_plugin_dir(tmp_path, "pack-one")
    pkg1 = pack_tutorial_package(src1, tmp_path / "one.octapack")
    install_tutorial_package(pkg1, managed_dir)

    # Plugin 2: Signed
    private_key = ed25519.Ed25519PrivateKey.generate()
    pub_hex = private_key.public_key().public_bytes_raw().hex()
    trusted_key = TrustedKey(
        key_id="district-key",
        name="District Authority",
        algorithm="ed25519",
        public_key_hex=pub_hex,
    )
    trust_store = TrustStore([trusted_key])

    src2 = _create_sample_plugin_dir(tmp_path, "pack-two")
    pkg2 = pack_tutorial_package(
        src2,
        tmp_path / "two.octapack",
        signing_key=private_key,
        key_id="district-key",
    )
    install_tutorial_package(pkg2, managed_dir, trust_store=trust_store)

    plugins = get_installed_plugins_info(managed_dir, trust_store=trust_store)
    assert len(plugins) == 2

    p1 = next(p for p in plugins if p.plugin_id == "pack-one")
    assert p1.trust_level == PackageTrustLevel.UNSIGNED_COMMUNITY
    assert p1.is_unsigned_community is True

    p2 = next(p for p in plugins if p.plugin_id == "pack-two")
    assert p2.trust_level == PackageTrustLevel.TRUSTED_SIGNED
    assert p2.is_trusted_signed is True
    assert p2.signer_id == "district-key"
    assert p2.signer_name == "District Authority"


def test_plugin_install_preview_dialog_trusted_signed(qtbot: pytest.QtBot, tmp_path: Path) -> None:
    """Preview for a TRUSTED_SIGNED package enables Install immediately without checkbox."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    private_key = ed25519.Ed25519PrivateKey.generate()
    pub_hex = private_key.public_key().public_bytes_raw().hex()
    trusted_key = TrustedKey(
        key_id="edu-trust",
        name="Educational Trust",
        algorithm="ed25519",
        public_key_hex=pub_hex,
    )
    trust_store = TrustStore([trusted_key])

    pkg = pack_tutorial_package(
        src_dir,
        tmp_path / "signed.octapack",
        signing_key=private_key,
        key_id="edu-trust",
    )
    res = verify_tutorial_package(pkg, trust_store=trust_store)
    assert res.trust_level == PackageTrustLevel.TRUSTED_SIGNED

    dlg = PluginInstallPreviewDialog(None, res, pkg)
    qtbot.addWidget(dlg)

    assert dlg._chk_acknowledge is None
    assert dlg._btn_install.isEnabled() is True


def test_plugin_install_preview_dialog_acknowledgement_required(
    qtbot: pytest.QtBot, tmp_path: Path
) -> None:
    """Preview for unsigned or unknown-key packages disables Install until acknowledged."""
    src_dir = _create_sample_plugin_dir(tmp_path)
    pkg = pack_tutorial_package(src_dir, tmp_path / "unsigned.octapack")
    res = verify_tutorial_package(pkg)
    assert res.trust_level == PackageTrustLevel.UNSIGNED_COMMUNITY

    dlg = PluginInstallPreviewDialog(None, res, pkg)
    qtbot.addWidget(dlg)

    assert dlg._chk_acknowledge is not None
    assert dlg._btn_install.isEnabled() is False

    # Check the box enables Install button
    dlg._chk_acknowledge.setChecked(True)
    assert dlg._btn_install.isEnabled() is True

    # Uncheck disables Install button
    dlg._chk_acknowledge.setChecked(False)
    assert dlg._btn_install.isEnabled() is False


def test_plugin_manager_dialog_install_and_uninstall(
    qtbot: pytest.QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PluginManagerDialog allows installing a package and uninstalling it with dynamic refresh."""
    managed_dir = tmp_path / "installed_plugins"
    managed_dir.mkdir(parents=True)

    controller = DesktopController(headless=True)
    monkeypatch.setattr(controller, "tutorial_plugin_dir", managed_dir)

    src_dir = _create_sample_plugin_dir(tmp_path, "paint-pack")
    pkg = pack_tutorial_package(src_dir, tmp_path / "paint.octapack")

    dlg = PluginManagerDialog(controller)
    qtbot.addWidget(dlg)

    # Initial table is empty
    assert dlg._table.rowCount() == 0
    assert dlg._btn_uninstall.isEnabled() is False

    # Mock QFileDialog to select the package
    monkeypatch.setattr(
        "octa.desktop.plugin_manager_dialog.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(pkg), "OCTA Packages (*.octapack)"),
    )
    # Mock confirmation preview dialog accept
    monkeypatch.setattr(
        PluginInstallPreviewDialog,
        "exec",
        lambda self: QDialog.Accepted,
    )
    # Mock success information message box
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: QMessageBox.Ok,
    )

    # Click install
    dlg._on_install_clicked()

    # Table now has 1 row
    assert dlg._table.rowCount() == 1
    assert dlg._table.item(0, 1).text() == "paint-pack"

    # Select row to enable uninstall
    dlg._table.selectRow(0)
    assert dlg._btn_uninstall.isEnabled() is True

    # Mock confirm uninstall to Yes
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )

    # Click uninstall
    dlg._on_uninstall_clicked()

    # Table is now empty again
    assert dlg._table.rowCount() == 0
    assert not (managed_dir / "paint-pack").exists()


def test_plugin_manager_dialog_blocks_invalid_package(
    qtbot: pytest.QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PluginManagerDialog displays critical error and halts when package is invalid."""
    managed_dir = tmp_path / "installed_plugins"
    managed_dir.mkdir(parents=True)

    controller = DesktopController(headless=True)
    monkeypatch.setattr(controller, "tutorial_plugin_dir", managed_dir)

    bad_pkg = tmp_path / "invalid.octapack"
    bad_pkg.write_text("Not a zip file at all")

    dlg = PluginManagerDialog(controller)
    qtbot.addWidget(dlg)

    monkeypatch.setattr(
        "octa.desktop.plugin_manager_dialog.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(bad_pkg), "OCTA Packages (*.octapack)"),
    )

    critical_called = False

    def mock_critical(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        nonlocal critical_called
        critical_called = True
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "critical", mock_critical)

    dlg._on_install_clicked()
    assert critical_called is True
    assert dlg._table.rowCount() == 0


def test_diagnostic_window_plugins_button(qtbot: pytest.QtBot) -> None:
    """DiagnosticWindow features a Manage Plugins button with accessible attributes."""
    controller = DesktopController(headless=True)
    window = DiagnosticWindow(controller)
    qtbot.addWidget(window)

    assert hasattr(window, "_btn_manage_plugins")
    btn = window._btn_manage_plugins
    assert btn.text() == "&Plugins..."
    assert btn.accessibleName() == "Manage Plugins"
    assert "Plugin Manager" in btn.accessibleDescription()
