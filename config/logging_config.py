# config/logging_config.py
"""
Centralized structured logging with rotating file handler and PII/secret sanitization.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

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

_BASIC_EXCEPTION_FORMATTER = logging.Formatter()


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that redacts PII and secrets from log records,
    including exception tracebacks before formatting.
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
        # Pre-render and sanitize exception tracebacks before Formatter formats them
        if record.exc_info and not record.exc_text:
            record.exc_text = _BASIC_EXCEPTION_FORMATTER.formatException(record.exc_info)
        if record.exc_text and isinstance(record.exc_text, str):
            record.exc_text = self._sanitize(record.exc_text)
        if record.stack_info and isinstance(record.stack_info, str):
            record.stack_info = self._sanitize(record.stack_info)
        return True

    @staticmethod
    def _sanitize(text: str) -> str:
        """Apply all redaction patterns to text."""
        for pattern, replacement in _SENSITIVE_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


class SanitizingFormatter(logging.Formatter):
    """Formatter that ensures final formatted output (including tracebacks) is sanitized."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return SensitiveDataFilter._sanitize(formatted)


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
    Preserves external/pytest handlers (such as pytest's caplog handler)
    while replacing only previously installed HVIA handlers on re-init.
    """
    settings = get_settings()
    level = getattr(logging, log_level or settings.log_level, logging.INFO)
    target_dir = log_dir or settings.log_path

    target_dir.mkdir(parents=True, exist_ok=True)

    pii_filter = SensitiveDataFilter()
    formatter = SanitizingFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(pii_filter)
    console_handler._hvia_managed = True

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
    file_handler._hvia_managed = True

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Close and remove only our own prior handlers; leave external/pytest handlers intact
    kept_handlers: list[logging.Handler] = []
    for handler in root_logger.handlers:
        if getattr(handler, "_hvia_managed", False):
            handler.close()
        else:
            kept_handlers.append(handler)
    root_logger.handlers = kept_handlers
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
    Decorator that logs function entry/exit with elapsed timing and captures exceptions.

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
            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - t0
                logger.info("[%s] Completed: %s (%.2fs)", stage, func.__name__, elapsed)
                return result
            except Exception:
                elapsed = time.perf_counter() - t0
                logger.exception("[%s] FAILED: %s (%.2fs)", stage, func.__name__, elapsed)
                raise
        return wrapper
    return decorator
