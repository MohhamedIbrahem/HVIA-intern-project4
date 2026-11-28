# tests/conftest.py
"""
Shared test fixtures for HVIA Task 04.

Provides:
- Non-skipping `-m integration` / `HVIA_REQUIRE_INTEGRATION=1` gate enforcement
- Session-scoped environment defaults
- Session-scoped PostgreSQL engine & factory + savepoint-isolated `db_session`
- Pydantic synthetic data factories (`customer_factory`, `product_factory`, `invoice_factory`, `payment_factory`)
- Configurable offline `mock_ollama` fixture
- Temporary log and report directory fixtures
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path for imports
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.fixtures import (
    MockOllamaController,
    SyntheticCustomer,
    SyntheticInvoice,
    SyntheticPayment,
    SyntheticProduct,
    make_synthetic_customer,
    make_synthetic_invoice,
    make_synthetic_payment,
    make_synthetic_product,
)


def _pg_is_healthy() -> bool:
    """Return True if the hvia_postgres container reports healthy."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", "hvia_postgres"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "healthy"
    except Exception:  # noqa: BLE001
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """
    Prevent integration gate from silently passing with 100% skipped tests.
    When `-m integration` is explicitly requested (and not `not integration`) or
    `HVIA_REQUIRE_INTEGRATION=1` is set, fail immediately if PostgreSQL is down.
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
    Preserves real `.env` credentials while supplying safe defaults for unit tests.
    """
    defaults = {
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "OLLAMA_MODEL": "qwen3.5:4b",
        "LOG_LEVEL": "DEBUG",
    }
    env = {k: v for k, v in defaults.items() if k not in os.environ}
    with patch.dict(os.environ, env, clear=False):
        yield


# ---------------------------------------------------------------------------
# Database Session & Transaction Isolation Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine, None, None]:
    """Session-scoped SQLAlchemy engine for PostgreSQL integration tests."""
    if not _pg_is_healthy():
        pytest.skip("PostgreSQL container 'hvia_postgres' is not running/healthy")

    from config.settings import get_settings
    from db.connection import dispose_engines, get_engine

    get_settings.cache_clear()
    engine = get_engine()
    yield engine
    dispose_engines()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    """Session-scoped sessionmaker bound to the real PostgreSQL test engine."""
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    """
    Provide a database session wrapped in an outer transaction + savepoint.
    Any writes or commits inside a test are rolled back completely on teardown.
    """
    connection = db_engine.connect()
    outer_tx = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        if outer_tx.is_active:
            outer_tx.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Synthetic Data Factories (Pydantic — Zero Real Records)
# ---------------------------------------------------------------------------
@pytest.fixture
def customer_factory() -> Callable[..., SyntheticCustomer]:
    """Return a factory creating synthetic Pydantic customer models."""
    return make_synthetic_customer


@pytest.fixture
def product_factory() -> Callable[..., SyntheticProduct]:
    """Return a factory creating synthetic Pydantic product models."""
    return make_synthetic_product


@pytest.fixture
def invoice_factory() -> Callable[..., SyntheticInvoice]:
    """Return a factory creating arithmetic-valid synthetic invoice models."""
    return make_synthetic_invoice


@pytest.fixture
def payment_factory() -> Callable[..., SyntheticPayment]:
    """Return a factory creating synthetic Pydantic payment models."""
    return make_synthetic_payment


# ---------------------------------------------------------------------------
# Local Ollama Mock & Temp Directories
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_ollama() -> MockOllamaController:
    """Return a configurable offline mock controller for local Ollama calls."""
    return MockOllamaController()


@pytest.fixture
def temp_log_dir(tmp_path: Path) -> Path:
    """Provide an isolated temporary directory for log file tests."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


@pytest.fixture
def temp_reports_dir(tmp_path: Path) -> Path:
    """Provide an isolated temporary directory for generated test reports."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir
