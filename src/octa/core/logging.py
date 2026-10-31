"""Structured logging configuration for OCTA.

Configures console and rotating file handlers, prevents duplicate handlers,
and provides clean shutdown hooks for Windows file safety.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from octa.core.config import AppConfig


class LoggingError(RuntimeError):
    """Raised when logging directories or handlers cannot be initialized."""


CONSOLE_FORMAT = "%(asctime)s [%(levelname)-7s] [%(name)s] %(message)s"
FILE_FORMAT = "%(asctime)s [%(levelname)-7s] [%(name)s] (%(filename)s:%(lineno)d) %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

OCTA_HANDLER_ATTR = "_octa_managed_handler"


def shutdown_logging(logger_name: str = "octa") -> None:
    """Flush, close, and remove all octa-managed handlers on the specified logger.

    Essential on Windows to release open file locks before directory cleanup.
    """
    target_logger = logging.getLogger(logger_name)
    for handler in list(target_logger.handlers):
        if getattr(handler, OCTA_HANDLER_ATTR, False):
            handler.flush()
            handler.close()
            target_logger.removeHandler(handler)


def setup_logging(
    config: AppConfig | None = None,
    logger_name: str = "octa",
) -> logging.Logger:
    """Configure structured logging with console and rotating file handlers.

    Ensures idempotent setup without duplicate handlers.
    """
    if config is None:
        config = AppConfig.load()

    target_logger = logging.getLogger(logger_name)
    target_logger.setLevel(getattr(logging, config.log_level))

    # Remove any existing octa-managed handlers to prevent duplicate logging
    shutdown_logging(logger_name)

    # 1. Console Handler
    if config.log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, config.log_level))
        console_formatter = logging.Formatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT)
        console_handler.setFormatter(console_formatter)
        setattr(console_handler, OCTA_HANDLER_ATTR, True)
        target_logger.addHandler(console_handler)

    # 2. Rotating File Handler
    if config.log_to_file and config.log_dir is not None:
        log_dir = Path(config.log_dir)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LoggingError(f"Cannot create log directory '{log_dir}': {exc}") from exc

        log_file = log_dir / "octa.log"
        try:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=config.log_max_bytes,
                backupCount=config.log_backup_count,
                encoding="utf-8",
            )
        except OSError as exc:
            raise LoggingError(f"Cannot open log file '{log_file}': {exc}") from exc

        file_handler.setLevel(getattr(logging, config.log_level))
        file_formatter = logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        setattr(file_handler, OCTA_HANDLER_ATTR, True)
        target_logger.addHandler(file_handler)

    return target_logger
