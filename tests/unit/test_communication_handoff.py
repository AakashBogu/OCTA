"""Unit tests for safe local credential and configuration handoff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

from octa.communication.handoff import (
    DEFAULT_HOST,
    ENV_AUTH_TOKEN,
    ENV_HANDOFF_FILE,
    ENV_WS_HOST,
    ENV_WS_PORT,
    prepare_process_environment,
    read_handoff_file,
    validate_handoff_parameters,
    write_handoff_file,
)
from octa.process.manager import GodotProcessManager


def test_validate_handoff_parameters_valid() -> None:
    """Valid parameters pass without exception."""
    validate_handoff_parameters(8765, "secret_token_123", "127.0.0.1")
    validate_handoff_parameters(1, "token")
    validate_handoff_parameters(65535, "token")


@pytest.mark.parametrize(
    ("port", "token", "host"),
    [
        (0, "token", "127.0.0.1"),
        (65536, "token", "127.0.0.1"),
        (-1, "token", "127.0.0.1"),
        ("8765", "token", "127.0.0.1"),
        (True, "token", "127.0.0.1"),
        (8765, "", "127.0.0.1"),
        (8765, "   ", "127.0.0.1"),
        (8765, 12345, "127.0.0.1"),
        (8765, "token", ""),
        (8765, "token", "   "),
    ],
)
def test_validate_handoff_parameters_invalid(port: int, token: str, host: str) -> None:
    """Invalid parameters raise TypeError or ValueError."""
    with pytest.raises((TypeError, ValueError)):
        validate_handoff_parameters(port, token, host)


def test_prepare_process_environment() -> None:
    """Environment dictionary correctly maps port, host, and bearer token."""
    env = prepare_process_environment(8765, "test_token_abc")
    assert env[ENV_WS_HOST] == DEFAULT_HOST
    assert env[ENV_WS_PORT] == "8765"
    assert env[ENV_AUTH_TOKEN] == "test_token_abc"
    assert ENV_HANDOFF_FILE not in env


def test_prepare_process_environment_custom_host() -> None:
    """Custom loopback host is properly populated."""
    env = prepare_process_environment(9000, "token_xyz", host="127.0.0.2")
    assert env[ENV_WS_HOST] == "127.0.0.2"
    assert env[ENV_WS_PORT] == "9000"
    assert env[ENV_AUTH_TOKEN] == "token_xyz"


def test_write_and_read_handoff_file(tmp_path: Path) -> None:
    """Handoff file is created, read, and deleted securely."""
    token = "file_token_456"
    handoff_path = write_handoff_file(54321, token, directory=tmp_path)

    assert handoff_path.is_file()
    assert handoff_path.parent == tmp_path

    # Read with delete=True (default)
    host, port, read_token = read_handoff_file(handoff_path, delete=True)
    assert host == DEFAULT_HOST
    assert port == 54321
    assert read_token == token

    # Verify file was securely removed
    assert not handoff_path.exists()


def test_read_handoff_file_without_delete(tmp_path: Path) -> None:
    """Reading with delete=False leaves the file on disk."""
    token = "persistent_token"
    handoff_path = write_handoff_file(1234, token, directory=tmp_path)

    host, port, read_token = read_handoff_file(handoff_path, delete=False)
    assert host == DEFAULT_HOST
    assert port == 1234
    assert read_token == token
    assert handoff_path.is_file()

    handoff_path.unlink()


def test_read_handoff_file_nonexistent() -> None:
    """Reading a missing handoff file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        read_handoff_file("nonexistent_octa_handoff.json")


def test_read_handoff_file_malformed(tmp_path: Path) -> None:
    """Malformed handoff JSON raises JSONDecodeError."""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json content", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_handoff_file(bad_file, delete=True)


def test_read_handoff_file_missing_fields(tmp_path: Path) -> None:
    """Missing required keys in handoff JSON raises KeyError or ValueError."""
    bad_file = tmp_path / "incomplete.json"
    bad_file.write_text(json.dumps({"port": 8765}), encoding="utf-8")
    with pytest.raises((KeyError, ValueError)):
        read_handoff_file(bad_file, delete=True)


def test_godot_manager_start_applies_environment(
    qtbot,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """GodotProcessManager.start sets child QProcessEnvironment when env is provided."""
    dummy_exe = tmp_path / "godot_dummy.exe"
    dummy_exe.touch()
    dummy_proj = tmp_path / "godot_proj"
    dummy_proj.mkdir()
    (dummy_proj / "project.godot").touch()

    mock_process = QProcess()
    manager = GodotProcessManager(
        executable_path=dummy_exe,
        project_dir=dummy_proj,
        process=mock_process,
    )

    env_dict = prepare_process_environment(8765, "secret_env_token")
    manager.start(env=env_dict)

    proc_env = mock_process.processEnvironment()
    assert proc_env.value(ENV_WS_HOST) == DEFAULT_HOST
    assert proc_env.value(ENV_WS_PORT) == "8765"
    assert proc_env.value(ENV_AUTH_TOKEN) == "secret_env_token"
