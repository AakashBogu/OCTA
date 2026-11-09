"""Safe local credential handoff utilities for Python and Godot inter-process communication."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ENV_WS_HOST: str = "OCTA_WS_HOST"
ENV_WS_PORT: str = "OCTA_WS_PORT"
ENV_AUTH_TOKEN: str = "OCTA_AUTH_TOKEN"
ENV_HANDOFF_FILE: str = "OCTA_HANDOFF_FILE"
DEFAULT_HOST: str = "127.0.0.1"


def validate_handoff_parameters(port: int, auth_token: str, host: str = DEFAULT_HOST) -> None:
    """Validate handoff parameters before generating process environment or file."""
    if not isinstance(port, int) or isinstance(port, bool):
        raise TypeError("port must be an integer")
    if port < 1 or port > 65535:
        raise ValueError(f"port must be between 1 and 65535, got {port}")

    if not isinstance(auth_token, str) or not auth_token.strip():
        raise ValueError("auth_token must be a non-empty string")

    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty string")


def prepare_process_environment(
    port: int,
    auth_token: str,
    host: str = DEFAULT_HOST,
) -> dict[str, str]:
    """Prepare environment variables for child process handoff without CLI exposure.

    On Windows and POSIX, child process environment blocks are private to the
    created process and do not appear in process command lines, Task Manager,
    Process Explorer command-line columns, or process-creation audit logs (e.g. Event 4688).
    """
    validate_handoff_parameters(port, auth_token, host)
    return {
        ENV_WS_HOST: host.strip(),
        ENV_WS_PORT: str(port),
        ENV_AUTH_TOKEN: auth_token.strip(),
    }


def write_handoff_file(
    port: int,
    auth_token: str,
    host: str = DEFAULT_HOST,
    directory: Path | None = None,
) -> Path:
    """Create a temporary handoff file with user-only permissions.

    The client reads and immediately deletes this file upon startup.
    """
    validate_handoff_parameters(port, auth_token, host)

    dir_str = str(directory) if directory is not None else None
    fd, temp_path = tempfile.mkstemp(
        suffix=".json",
        prefix="octa_handoff_",
        dir=dir_str,
        text=True,
    )
    try:
        # Restrict permissions to current user only (read/write)
        os.chmod(temp_path, 0o600)
        data = {
            "host": host.strip(),
            "port": port,
            "auth_token": auth_token.strip(),
        }
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise

    return Path(temp_path)


def read_handoff_file(
    file_path: str | Path,
    delete: bool = True,
) -> tuple[str, int, str]:
    """Read host, port, and auth token from a handoff file, optionally unlinking it.

    Returns:
        tuple of (host, port, auth_token)
    """
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"Handoff file does not exist: {p}")

    try:
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("Handoff file root must be a JSON object dictionary")

        host = data.get("host", DEFAULT_HOST)
        port = data["port"]
        auth_token = data["auth_token"]
        validate_handoff_parameters(port, auth_token, host)
        return str(host), int(port), str(auth_token)
    finally:
        if delete:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
