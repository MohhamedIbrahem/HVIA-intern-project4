# db/connection.py
"""
Database connection pool, retry logic, async engine, and health checks.

Provides:
- Synchronous SQLAlchemy engine + session management (get_session)
- Async SQLAlchemy engine + session management (get_async_session)
- Health check and wait_for_db with retry/backoff
- Custom domain exceptions
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from config.logging_config import get_logger
from config.settings import get_settings

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------
class DatabaseConnectionError(Exception):
    """Raised when the database cannot be reached after retries."""


class DatabaseQueryError(Exception):
    """Raised when a database query fails."""


# ---------------------------------------------------------------------------
# Sync Engine & Session
# ---------------------------------------------------------------------------
_engine = None
_SessionFactory = None


def get_engine():
    """Get or create the synchronous SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            echo=False,
        )
        logger.info(
            "Sync engine created: pool_size=%d, max_overflow=%d",
            settings.db_pool_size,
            settings.db_max_overflow,
        )
    return _engine


def get_session_factory():
    """Get or create the session factory (singleton)."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context-managed database session.
    Commits on success, rolls back + logs on failure.

    Usage:
        with get_session() as session:
            result = session.execute(text("SELECT 1"))
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        logger.exception("Database session error — rolled back")
        raise DatabaseQueryError(str(e)) from e
    except Exception:
        session.rollback()
        logger.exception("Unexpected error in DB session — rolled back")
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Async Engine & Session
# ---------------------------------------------------------------------------
_async_engine = None
_async_semaphore = asyncio.Semaphore(4)


def get_async_engine():
    """Get or create the async SQLAlchemy engine (singleton)."""
    global _async_engine
    if _async_engine is None:
        settings = get_settings()
        _async_engine = create_async_engine(
            settings.async_database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            echo=False,
        )
        logger.info("Async engine created")
    return _async_engine


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context-managed async database session with bounded concurrency.
    Read-only profiling queries should use this.
    """
    async with _async_semaphore:
        engine = get_async_engine()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                yield session
            except SQLAlchemyError as e:
                await session.rollback()
                logger.exception("Async DB session error — rolled back")
                raise DatabaseQueryError(str(e)) from e


# ---------------------------------------------------------------------------
# Health Check & Wait
# ---------------------------------------------------------------------------
def health_check() -> bool:
    """
    Check if the database is reachable by executing SELECT 1.
    Returns True if healthy, False otherwise.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Database health check failed: %s", e)
        return False


async def async_health_check() -> bool:
    """Async version of health_check."""
    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Async database health check failed: %s", e)
        return False


def wait_for_db(
    retries: int = 3,
    backoff_seconds: float = 1.5,
) -> None:
    """
    Wait for the database to become available, retrying with exponential backoff.
    Raises DatabaseConnectionError if all retries are exhausted.
    """
    for attempt in range(1, retries + 1):
        if health_check():
            logger.info("Database is ready (attempt %d/%d)", attempt, retries)
            return

        if attempt < retries:
            wait = backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Database not ready (attempt %d/%d), retrying in %.1fs...",
                attempt, retries, wait,
            )
            time.sleep(wait)

    raise DatabaseConnectionError(
        f"Database not reachable after {retries} attempts"
    )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def dispose_engines() -> None:
    """Dispose of all engine connections. Call at application shutdown."""
    global _engine, _async_engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _SessionFactory = None
        logger.info("Sync engine disposed")
    if _async_engine is not None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_async_engine.dispose())
            else:
                loop.run_until_complete(_async_engine.dispose())
        except RuntimeError:
            pass
        _async_engine = None
        logger.info("Async engine disposed")
