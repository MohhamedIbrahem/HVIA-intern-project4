# tests/conftest.py
"""
Shared test fixtures for HVIA Task 04.
Provides DB session management, synthetic data factories, and mock helpers.
"""

from __future__ import annotations

import os
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
    # Only inject defaults for vars not already set by .env
    env = {k: v for k, v in defaults.items() if k not in os.environ}
    with patch.dict(os.environ, env, clear=False):
        yield
