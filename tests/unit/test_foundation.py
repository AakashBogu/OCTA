"""Foundation tests for OCTA package structure, version, and CLI entry point."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import octa
from octa import __version__
from octa.__main__ import main, parse_args


def test_package_version_format() -> None:
    """Verify that octa.__version__ follows semantic versioning."""
    semver_pattern = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
    assert re.match(semver_pattern, __version__), f"Invalid version string: {__version__}"
    assert octa.__version__ == "0.1.0"


def test_package_exports() -> None:
    """Verify top-level package exports."""
    assert hasattr(octa, "__version__")
    assert "__version__" in octa.__all__


def test_cli_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that python -m octa --version prints version and exits cleanly."""
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"octa {__version__}"


def test_cli_short_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify that python -m octa -V prints version and exits cleanly."""
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["-V"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"octa {__version__}"


def test_cli_default_run(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify default entry point execution without arguments."""
    exit_code = main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert f"OCTA v{__version__}" in captured.out


def test_isolated_workspace_fixture(isolated_workspace: Path) -> None:
    """Verify that test I/O uses isolated temporary directories."""
    test_file = isolated_workspace / "sample.txt"
    test_file.write_text("octa test", encoding="utf-8")
    assert test_file.exists()
    assert test_file.read_text(encoding="utf-8") == "octa test"
