# tests/test_db_connection.py
"""
Tests for db/connection.py — connection pool, sessions, health checks, retry.
Integration tests require a running PostgreSQL container.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from sqlalchemy import text

from db.connection import (
    DatabaseConnectionError,
    DatabaseQueryError,
    dispose_engines,
    get_engine,
    get_session,
    health_check,
    wait_for_db,
)


# ---------------------------------------------------------------------------
# Skip if PostgreSQL is not running
# ---------------------------------------------------------------------------
def _pg_is_healthy() -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", "hvia_postgres"],
            check=False, capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "healthy"
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _pg_is_healthy(),
        reason="PostgreSQL container 'hvia_postgres' is not running/healthy",
    ),
]


@pytest.fixture(autouse=True)
def _reset_engines():
    """Reset engine singletons and settings cache between tests."""
    from config.settings import get_settings
    get_settings.cache_clear()
    dispose_engines()
    yield
    dispose_engines()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Integration: Health Check
# ---------------------------------------------------------------------------
class TestHealthCheck:

    def test_health_check_returns_true(self):
        assert health_check() is True

    def test_select_one_via_session(self):
        with get_session() as session:
            row = session.execute(text("SELECT 1 AS test")).scalar()
            assert row == 1

    def test_session_reads_42_tables(self):
        with get_session() as session:
            count = session.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )).scalar()
            assert count == 42


# ---------------------------------------------------------------------------
# Unit: wait_for_db retry logic
# ---------------------------------------------------------------------------
class TestWaitForDB:

    def test_succeeds_immediately(self):
        wait_for_db(retries=2, backoff_seconds=0.01)

    def test_retries_then_succeeds(self):
        call_count = 0
        def flaky(*a, **kw):
            nonlocal call_count
            call_count += 1
            return call_count >= 2

        with patch("db.connection.health_check", side_effect=flaky):
            wait_for_db(retries=3, backoff_seconds=0.01)
        assert call_count == 2

    def test_raises_after_exhausted_retries(self):
        with patch("db.connection.health_check", return_value=False), pytest.raises(DatabaseConnectionError, match="not reachable"):
            wait_for_db(retries=2, backoff_seconds=0.01)


# ---------------------------------------------------------------------------
# Integration: Session error handling
# ---------------------------------------------------------------------------
class TestSessionErrors:

    def test_invalid_sql_raises_query_error(self):
        with pytest.raises(DatabaseQueryError), get_session() as session:
            session.execute(text("SELECT * FROM nonexistent_table_xyz"))

    def test_no_pool_leak_after_error(self):
        """Pool still works after a rolled-back error."""
        with pytest.raises(DatabaseQueryError), get_session() as session:
            session.execute(text("INVALID SQL"))

        with get_session() as session:
            assert session.execute(text("SELECT 1")).scalar() == 1

    def test_session_commits_on_success(self):
        """Commits cleanly using an isolated _test_scratch schema (never alters public)."""
        with get_session() as session:
            session.execute(text("CREATE SCHEMA IF NOT EXISTS _test_scratch"))
            session.execute(text(
                "CREATE TABLE IF NOT EXISTS _test_scratch._test_commit (id int)"
            ))
            session.execute(text("INSERT INTO _test_scratch._test_commit VALUES (42)"))

        try:
            with get_session() as session:
                assert session.execute(
                    text("SELECT id FROM _test_scratch._test_commit")
                ).scalar() == 42
        finally:
            with get_session() as session:
                session.execute(text("DROP SCHEMA IF EXISTS _test_scratch CASCADE"))


# ---------------------------------------------------------------------------
# Integration: Async
# ---------------------------------------------------------------------------
class TestAsync:

    @pytest.mark.asyncio
    async def test_async_health_check(self):
        from db.connection import async_health_check
        assert await async_health_check() is True

    @pytest.mark.asyncio
    async def test_async_session_read(self):
        from db.connection import get_async_session
        async with get_async_session() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_concurrent_async_reads(self):
        import asyncio

        from db.connection import get_async_session

        async def count_tables():
            async with get_async_session() as session:
                r = await session.execute(text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                ))
                return r.scalar()

        results = await asyncio.gather(
            count_tables(), count_tables(), count_tables(), count_tables()
        )
        assert all(r >= 42 for r in results)


# ---------------------------------------------------------------------------
# Integration: Engine config
# ---------------------------------------------------------------------------
class TestEngineConfig:

    def test_pool_settings(self):
        engine = get_engine()
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 10
