# config/logging_config.py
"""
Centralized structured logging with rotating file handler and PII/secret sanitization.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from functools import wraps
from typing import Any, Callable

from config.settings import get_settings


# ---------------------------------------------------------------------------
# PII / Secret Patterns to Redact
# ---------------------------------------------------------------------------
_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Database URLs with passwords: postgresql://user:SECRET@host
    (re.compile(r"(postgresql(?:\+\w+)?://\w+:)[^@]+(@)"), r"\1***\2"),
    # Generic password= or password: values
    (re.compile(r"(password\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    # Egyptian phone numbers (01x-xxxx-xxxx patterns)
    (re.compile(r"\b(01[0-9])[- ]?(\d{4})[- ]?(\d{4})\b"), r"\1****\3"),
    # Email addresses
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), r"***@***.***"),
    # Generic token/secret/key patterns
    (re.compile(r"((?:token|secret|key|api_key)\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    # Disabled password hash marker from the dump
    (re.compile(r"!DISABLED_NON_HASH!", re.IGNORECASE), r"***REDACTED_HASH***"),
]


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that redacts PII and secrets from log records.
    Applied to all handlers to prevent credential/PII leakage.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._sanitize(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._sanitize(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True

    @staticmethod
    def _sanitize(text: str) -> str:
        """Apply all redaction patterns to text."""
        for pattern, replacement in _SENSITIVE_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def _sanitize_text(text: str) -> str:
    """Public utility: sanitize a string using the same PII patterns."""
    return SensitiveDataFilter._sanitize(text)


# ---------------------------------------------------------------------------
# Log Format
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_level: str | None = None,
    log_dir: Path | None = None,
    log_filename: str = "hvia_pipeline.log",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """
    Configure root logger with console + rotating file handlers.
    Should be called once at application startup.
    """
    settings = get_settings()
    level = getattr(logging, log_level or settings.log_level, logging.INFO)
    target_dir = log_dir or settings.log_path

    # Ensure log directory exists
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create the PII filter
    pii_filter = SensitiveDataFilter()

    # Formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(pii_filter)

    # Rotating file handler (UTF-8 for Arabic text metadata)
    file_handler = RotatingFileHandler(
        filename=str(target_dir / log_filename),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(pii_filter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-init
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger. Call setup_logging() once before using this.
    If setup_logging() hasn't been called, returns a basic logger.
    """
    return logging.getLogger(name)


def log_execution(stage: str) -> Callable:
    """
    Decorator that logs function entry/exit with timing and captures exceptions.

    Usage:
        @log_execution("etl.restore")
        def restore_database():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger = get_logger(func.__module__ or stage)
            logger.info("[%s] Starting: %s", stage, func.__name__)
            try:
                result = func(*args, **kwargs)
                logger.info("[%s] Completed: %s", stage, func.__name__)
                return result
            except Exception:
                logger.exception("[%s] FAILED: %s", stage, func.__name__)
                raise
        return wrapper
    return decorator
