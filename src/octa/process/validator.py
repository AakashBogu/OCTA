"""Path and environment validation for the Godot runtime."""

from __future__ import annotations

import os
from pathlib import Path

from octa.process.exceptions import GodotNotFoundError, GodotProjectNotFoundError


def validate_executable_path(executable_path: str | Path | None) -> Path:
    """Validate that the specified path points to an existing Godot executable.

    Resolves relative paths to absolute paths. Validates the .exe extension on Windows
    and verifies that the path is a regular file.

    Raises:
        GodotNotFoundError: If the path is empty, has an invalid type, does not exist,
            is not a file, or does not have a .exe extension on Windows.
    """
    if executable_path is None:
        raise GodotNotFoundError("Godot executable path must be specified.")

    if not isinstance(executable_path, (str, Path)):
        type_name = type(executable_path).__name__
        raise GodotNotFoundError(
            f"Invalid executable path type: expected str or Path, got {type_name}."
        )

    path_str = str(executable_path).strip()
    if not path_str:
        raise GodotNotFoundError("Godot executable path cannot be empty.")

    resolved_path = Path(path_str).resolve()

    if not resolved_path.exists():
        raise GodotNotFoundError(f"Godot executable not found at '{resolved_path}'.")

    if not resolved_path.is_file():
        raise GodotNotFoundError(f"Godot executable path '{resolved_path}' is not a regular file.")

    # Windows-specific: ensure .exe extension
    if os.name == "nt" or resolved_path.suffix.lower() == ".exe":
        if resolved_path.suffix.lower() != ".exe":
            raise GodotNotFoundError(
                f"Godot executable '{resolved_path}' must have a '.exe' extension on Windows."
            )

    return resolved_path


def validate_project_dir(project_dir: str | Path | None) -> Path:
    """Validate that the specified path points to a valid Godot project directory.

    Resolves relative paths to absolute paths. Verifies that the directory exists
    and contains a valid 'project.godot' configuration file.

    Raises:
        GodotProjectNotFoundError: If the path is empty, has an invalid type, does not exist,
            is not a directory, or does not contain 'project.godot'.
    """
    if project_dir is None:
        raise GodotProjectNotFoundError("Godot project directory must be specified.")

    if not isinstance(project_dir, (str, Path)):
        type_name = type(project_dir).__name__
        raise GodotProjectNotFoundError(
            f"Invalid project directory type: expected str or Path, got {type_name}."
        )

    path_str = str(project_dir).strip()
    if not path_str:
        raise GodotProjectNotFoundError("Godot project directory cannot be empty.")

    resolved_dir = Path(path_str).resolve()

    if not resolved_dir.exists():
        raise GodotProjectNotFoundError(f"Godot project directory not found at '{resolved_dir}'.")

    if not resolved_dir.is_dir():
        raise GodotProjectNotFoundError(f"Godot project path '{resolved_dir}' is not a directory.")

    project_file = resolved_dir / "project.godot"
    if not project_file.is_file():
        raise GodotProjectNotFoundError(
            f"Godot project configuration 'project.godot' not found in '{resolved_dir}'."
        )

    return resolved_dir


def validate_godot_environment(
    executable_path: str | Path | None,
    project_dir: str | Path | None,
) -> tuple[Path, Path]:
    """Validate both the Godot executable and project directory.

    Returns:
        tuple[Path, Path]: A tuple containing the validated (executable_path, project_dir).
    """
    valid_exe = validate_executable_path(executable_path)
    valid_proj = validate_project_dir(project_dir)
    return valid_exe, valid_proj
