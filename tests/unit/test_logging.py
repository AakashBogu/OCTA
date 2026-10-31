"""Unit tests for OCTA structured logging infrastructure."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from octa.core.config import AppConfig
from octa.core.logging import (
    LoggingError,
    setup_logging,
    shutdown_logging,
)


def test_log_file_creation(tmp_path: Path) -> None:
    """Verify that setup_logging creates the log directory and log file."""
    log_dir = tmp_path / "logs"
    config = AppConfig(log_dir=log_dir, log_level="INFO", log_to_file=True)

    logger = setup_logging(config=config, logger_name="octa.test")
    logger.info("Test log message: %s", "hello octa")
    shutdown_logging(logger_name="octa.test")

    log_file = log_dir / "octa.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Test log message: hello octa" in content
    assert "[INFO   ]" in content


def test_log_level_filtering(tmp_path: Path) -> None:
    """Verify that messages below configured log_level are filtered out."""
    log_dir = tmp_path / "logs_filtering"
    config = AppConfig(log_dir=log_dir, log_level="WARNING", log_to_file=True)

    logger = setup_logging(config=config, logger_name="octa.test_filter")
    logger.debug("Debug hidden message")
    logger.info("Info hidden message")
    logger.warning("Warning visible message")
    shutdown_logging(logger_name="octa.test_filter")

    log_file = log_dir / "octa.log"
    content = log_file.read_text(encoding="utf-8")
    assert "Debug hidden message" not in content
    assert "Info hidden message" not in content
    assert "Warning visible message" in content


def test_log_rotation(tmp_path: Path) -> None:
    """Verify log file rotation when max_bytes threshold is exceeded."""
    log_dir = tmp_path / "logs_rotation"
    # Set a tiny max_bytes (120 bytes) to force rotation with few lines
    config = AppConfig(
        log_dir=log_dir,
        log_level="INFO",
        log_to_file=True,
        log_max_bytes=120,
        log_backup_count=2,
    )

    logger = setup_logging(config=config, logger_name="octa.test_rotation")
    for idx in range(15):
        logger.info("Rotation test message entry number %02d", idx)
    shutdown_logging(logger_name="octa.test_rotation")

    log_file = log_dir / "octa.log"
    backup_file_1 = log_dir / "octa.log.1"

    assert log_file.exists()
    assert backup_file_1.exists()


def test_repeated_initialization_prevents_duplicate_handlers(tmp_path: Path) -> None:
    """Verify that calling setup_logging repeatedly does not duplicate handlers or log lines."""
    log_dir = tmp_path / "logs_duplicates"
    config = AppConfig(log_dir=log_dir, log_level="INFO", log_to_file=True)

    # Initialize 3 times in a row
    for _ in range(3):
        setup_logging(config=config, logger_name="octa.test_duplicates")

    logger = logging.getLogger("octa.test_duplicates")
    # Verify exactly 2 handlers (1 console, 1 file)
    assert len(logger.handlers) == 2

    logger.info("Single unique message")
    shutdown_logging(logger_name="octa.test_duplicates")

    log_file = log_dir / "octa.log"
    lines = [
        line
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if "Single unique message" in line
    ]
    # Must appear exactly once, not 3 times
    assert len(lines) == 1


def test_failure_handling_when_log_dir_cannot_be_created(tmp_path: Path) -> None:
    """Verify clear LoggingError when log directory creation fails."""
    blocked_dir = tmp_path / "blocked_logs"

    config = AppConfig(log_dir=blocked_dir, log_to_file=True)

    with patch.object(Path, "mkdir", side_effect=OSError("Access denied mock")):
        with pytest.raises(LoggingError, match="Cannot create log directory"):
            setup_logging(config=config, logger_name="octa.test_error")


def test_failure_handling_when_log_file_cannot_be_opened(tmp_path: Path) -> None:
    """Verify clear LoggingError when log file cannot be opened."""
    log_dir = tmp_path / "logs_open_err"
    log_dir.mkdir(parents=True, exist_ok=True)

    config = AppConfig(log_dir=log_dir, log_to_file=True)

    with patch("octa.core.logging.RotatingFileHandler", side_effect=OSError("File lock error")):
        with pytest.raises(LoggingError, match="Cannot open log file"):
            setup_logging(config=config, logger_name="octa.test_file_error")
