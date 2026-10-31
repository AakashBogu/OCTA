"""Shared pytest configuration and fixtures for OCTA tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from octa.core.logging import shutdown_logging


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers for the test suite."""
    config.addinivalue_line("markers", "integration: mark test as real-binary integration test")


@pytest.fixture
def isolated_workspace(tmp_path: Path) -> Path:
    """Fixture providing a clean, isolated temporary workspace for file I/O tests."""
    workspace = tmp_path / "octa_test_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture(autouse=True)
def clean_logging_state() -> Generator[None, None, None]:
    """Ensure all octa-managed logging handlers are closed after each test."""
    yield
    shutdown_logging()
