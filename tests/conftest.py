# tests/conftest.py
"""
Shared test fixtures for HVIA Task 04.
Provides env isolation and non-skipping integration gate enforcement.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path for imports
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _pg_is_healthy() -> bool:
    """Return True if the hvia_postgres container reports healthy."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", "hvia_postgres"],
            check=False, capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "healthy"
    except Exception:  # noqa: BLE001
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """
    Prevent integration gate from silently passing with 100% skipped tests.
    When `-m integration` is explicitly requested (and not `not integration`) or
    `HVIA_REQUIRE_INTEGRATION=1` is set, fail immediately if PostgreSQL is down
    instead of letting module skipif markers pass with 0 executed tests.
    """
    markexpr = (getattr(config.option, "markexpr", "") or "").strip()
    explicit_integration = (
        ("integration" in markexpr and "not integration" not in markexpr)
        or os.environ.get("HVIA_REQUIRE_INTEGRATION") == "1"
    )
    if not explicit_integration:
        return

    if not _pg_is_healthy():
        pytest.exit(
            "INTEGRATION GATE FAILED: PostgreSQL container 'hvia_postgres' is not running/healthy. "
            "Start Docker PostgreSQL before running `-m integration` tests.",
            returncode=1,
        )


@pytest.fixture(scope="session", autouse=True)
def _set_test_env():
    """
    Set minimal env vars for tests that need Settings.
    Only sets vars that are NOT already defined in the real .env,
    so integration tests (restore, DB) use real credentials while
    unit tests get safe defaults.
    """
    defaults = {
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "OLLAMA_MODEL": "qwen3.5:4b",
        "LOG_LEVEL": "DEBUG",
    }
    env = {k: v for k, v in defaults.items() if k not in os.environ}
    with patch.dict(os.environ, env, clear=False):
        yield
