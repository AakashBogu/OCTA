"""Unit tests for Windows distribution packaging and asset integrity."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scripts.build_windows_dist import (
    audit_distribution_assets,
    get_repo_root,
    verify_secret_hygiene,
)

from octa.core.config import AppConfig


def test_repo_root_anchor() -> None:
    """get_repo_root verifies critical project anchor files."""
    root = get_repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "octa").is_dir()


def test_spec_file_structure_and_exclusions() -> None:
    """octa.spec defines mandatory runtime modules and strictly excludes dev tools/tests."""
    root = get_repo_root()
    spec_path = root / "octa.spec"
    assert spec_path.is_file(), "octa.spec must exist at repository root"

    spec_text = spec_path.read_text(encoding="utf-8")

    # Essential runtime modules
    for expected in (
        "octa.core",
        "octa.communication",
        "octa.desktop",
        "octa.intelligence",
        "octa.voice",
        "octa.tutorials",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtWebSockets",
        "PySide6.QtMultimedia",
    ):
        assert expected in spec_text, f"Missing expected hiddenimport: {expected}"

    # Strict exclusions to guarantee tests, linters, and secrets are not bundled
    for forbidden in (
        '"pytest"',
        '"pytest_mock"',
        '"pytestqt"',
        '"ruff"',
        '"tests"',
    ):
        assert forbidden in spec_text, f"Missing exclusion for development tool: {forbidden}"

    # Dual executables configured
    assert 'name="OCTA"' in spec_text
    assert 'name="octa-cli"' in spec_text
    assert "console=False" in spec_text
    assert "console=True" in spec_text


def test_secret_hygiene_verification(tmp_path: Path) -> None:
    """verify_secret_hygiene flags forbidden credential and user-data files."""
    # Clean directory
    (tmp_path / "OCTA.exe").write_bytes(b"dummy_exe")
    (tmp_path / "readme.txt").write_text("safe content", encoding="utf-8")
    assert verify_secret_hygiene(tmp_path) == []

    # Dirty directory with secrets, local env, and telemetry files
    (tmp_path / ".env").write_text("OCTA_LLM_API_KEY=secret", encoding="utf-8")
    (tmp_path / "secret.key").write_text("private_key", encoding="utf-8")
    (tmp_path / "tutorial_feedback.json").write_text("{}", encoding="utf-8")
    (tmp_path / "debug.log").write_text("trace", encoding="utf-8")

    violations = verify_secret_hygiene(tmp_path)
    assert len(violations) == 4
    assert any(".env" in v for v in violations)
    assert any("secret.key" in v for v in violations)
    assert any("tutorial_feedback.json" in v for v in violations)
    assert any("debug.log" in v for v in violations)


def test_distribution_asset_auditor(tmp_path: Path) -> None:
    """audit_distribution_assets verifies executables, Qt plugins, and Godot assets."""
    # Empty directory produces descriptive missing-file errors
    errors = audit_distribution_assets(tmp_path)
    assert any("Missing or empty GUI executable" in e for e in errors)
    assert any("Missing or empty CLI executable" in e for e in errors)
    assert any("Missing required Qt Windows platform plugin" in e for e in errors)
    assert any("Missing bundled Godot project file" in e for e in errors)

    # Valid populated structure
    (tmp_path / "OCTA.exe").write_bytes(b"binary_payload")
    (tmp_path / "octa-cli.exe").write_bytes(b"cli_payload")

    platforms_dir = tmp_path / "PySide6" / "plugins" / "platforms"
    platforms_dir.mkdir(parents=True, exist_ok=True)
    (platforms_dir / "qwindows.dll").write_bytes(b"qwindows_dll")

    godot_dir = tmp_path / "godot"
    godot_dir.mkdir(parents=True, exist_ok=True)
    (godot_dir / "project.godot").write_text("config", encoding="utf-8")
    (godot_dir / "scenes").mkdir()
    (godot_dir / "scripts").mkdir()

    clean_errors = audit_distribution_assets(tmp_path)
    assert clean_errors == []


def test_app_config_frozen_godot_project_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AppConfig resolves bundled godot_project_dir from _MEIPASS when frozen."""
    fake_meipass = tmp_path / "_internal"
    fake_meipass.mkdir()
    bundled_godot = fake_meipass / "godot"
    bundled_godot.mkdir()
    (bundled_godot / "project.godot").write_text("config", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)

    config = AppConfig(godot_project_dir=Path("godot"))
    assert config.godot_project_dir == bundled_godot
    assert config.godot_project_dir.is_absolute()
