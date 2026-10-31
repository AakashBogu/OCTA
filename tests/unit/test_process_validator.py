"""Unit tests for process states, exceptions, and path validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from octa.process.exceptions import (
    GodotNotFoundError,
    GodotProjectNotFoundError,
    ProcessAlreadyRunningError,
    ProcessError,
    ProcessLaunchError,
)
from octa.process.state import ProcessState
from octa.process.validator import (
    validate_executable_path,
    validate_godot_environment,
    validate_project_dir,
)


def test_process_state_enum() -> None:
    """Verify ProcessState enum members and string representations."""
    expected_states = {"STOPPED", "STARTING", "RUNNING", "STOPPING", "CRASHED", "ERROR"}
    actual_states = {state.value for state in ProcessState}
    assert actual_states == expected_states

    for state in ProcessState:
        assert str(state) == state.value
        assert state.name == state.value


def test_exception_hierarchy() -> None:
    """Verify that all process exceptions derive from ProcessError."""
    assert issubclass(GodotNotFoundError, ProcessError)
    assert issubclass(GodotProjectNotFoundError, ProcessError)
    assert issubclass(ProcessAlreadyRunningError, ProcessError)
    assert issubclass(ProcessLaunchError, ProcessError)
    assert issubclass(ProcessError, Exception)


def test_valid_executable_path(tmp_path: Path) -> None:
    """Verify validation of a valid executable path."""
    fake_exe = tmp_path / "godot.exe"
    fake_exe.write_bytes(b"MZfakebinary")

    validated = validate_executable_path(fake_exe)
    assert validated == fake_exe.resolve()
    assert validated.is_file()


def test_executable_path_with_spaces(tmp_path: Path) -> None:
    """Verify validation of an executable path containing spaces."""
    dir_with_spaces = tmp_path / "Program Files" / "Godot 4.7 Engine"
    dir_with_spaces.mkdir(parents=True, exist_ok=True)
    fake_exe = dir_with_spaces / "Godot 4.7.2.exe"
    fake_exe.write_bytes(b"MZfakebinary")

    validated = validate_executable_path(fake_exe)
    assert validated == fake_exe.resolve()
    assert "Program Files" in str(validated)


def test_missing_executable(tmp_path: Path) -> None:
    """Verify error when executable file does not exist."""
    missing_exe = tmp_path / "missing_godot.exe"
    with pytest.raises(GodotNotFoundError, match="Godot executable not found"):
        validate_executable_path(missing_exe)


def test_invalid_executable_extension(tmp_path: Path) -> None:
    """Verify error when file does not have .exe extension."""
    invalid_file = tmp_path / "godot.bin"
    invalid_file.write_bytes(b"somecontent")

    with pytest.raises(GodotNotFoundError, match="must have a '.exe' extension"):
        validate_executable_path(invalid_file)


def test_executable_is_directory(tmp_path: Path) -> None:
    """Verify error when executable path points to a directory."""
    fake_dir = tmp_path / "fake_godot.exe"
    fake_dir.mkdir()

    with pytest.raises(GodotNotFoundError, match="is not a regular file"):
        validate_executable_path(fake_dir)


@pytest.mark.parametrize("invalid_input", [None, "", "   "])
def test_empty_executable_input(invalid_input: str | None) -> None:
    """Verify error on None or empty executable input."""
    with pytest.raises(GodotNotFoundError):
        validate_executable_path(invalid_input)


def test_invalid_executable_type() -> None:
    """Verify error on non-string, non-Path executable input."""
    with pytest.raises(GodotNotFoundError, match="Invalid executable path type"):
        validate_executable_path(12345)  # type: ignore[arg-type]


def test_valid_project_dir(tmp_path: Path) -> None:
    """Verify validation of a valid project directory."""
    project_dir = tmp_path / "godot_project"
    project_dir.mkdir()
    project_file = project_dir / "project.godot"
    project_file.write_text("config_version=5", encoding="utf-8")

    validated = validate_project_dir(project_dir)
    assert validated == project_dir.resolve()
    assert validated.is_dir()


def test_project_dir_with_spaces(tmp_path: Path) -> None:
    """Verify validation of a project directory with spaces."""
    project_dir = tmp_path / "OCTA Project Workspace" / "godot"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_file = project_dir / "project.godot"
    project_file.write_text("config_version=5", encoding="utf-8")

    validated = validate_project_dir(project_dir)
    assert validated == project_dir.resolve()
    assert "OCTA Project Workspace" in str(validated)


def test_missing_project_dir(tmp_path: Path) -> None:
    """Verify error when project directory does not exist."""
    missing_dir = tmp_path / "missing_dir"
    with pytest.raises(GodotProjectNotFoundError, match="Godot project directory not found"):
        validate_project_dir(missing_dir)


def test_missing_project_godot_file(tmp_path: Path) -> None:
    """Verify error when project directory exists but project.godot is missing."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()

    with pytest.raises(GodotProjectNotFoundError, match="project.godot' not found"):
        validate_project_dir(empty_dir)


def test_project_dir_is_file(tmp_path: Path) -> None:
    """Verify error when project directory path points to a file."""
    fake_file = tmp_path / "project_is_a_file"
    fake_file.write_text("not a dir", encoding="utf-8")

    with pytest.raises(GodotProjectNotFoundError, match="is not a directory"):
        validate_project_dir(fake_file)


@pytest.mark.parametrize("invalid_input", [None, "", "   "])
def test_empty_project_dir_input(invalid_input: str | None) -> None:
    """Verify error on None or empty project directory input."""
    with pytest.raises(GodotProjectNotFoundError):
        validate_project_dir(invalid_input)


def test_invalid_project_dir_type() -> None:
    """Verify error on non-string, non-Path project directory input."""
    with pytest.raises(GodotProjectNotFoundError, match="Invalid project directory type"):
        validate_project_dir(["not", "a", "path"])  # type: ignore[arg-type]


def test_validate_godot_environment_success(tmp_path: Path) -> None:
    """Verify validate_godot_environment returns a tuple of resolved paths."""
    exe_file = tmp_path / "godot.exe"
    exe_file.write_bytes(b"MZfake")

    proj_dir = tmp_path / "godot"
    proj_dir.mkdir()
    (proj_dir / "project.godot").write_text("config_version=5", encoding="utf-8")

    valid_exe, valid_proj = validate_godot_environment(exe_file, proj_dir)
    assert valid_exe == exe_file.resolve()
    assert valid_proj == proj_dir.resolve()


def test_validate_real_project_godot() -> None:
    """Verify that OCTA's existing godot/ directory passes validation."""
    real_godot_dir = Path("godot")
    assert real_godot_dir.is_dir()

    validated = validate_project_dir(real_godot_dir)
    assert validated.is_dir()
    assert (validated / "project.godot").is_file()
