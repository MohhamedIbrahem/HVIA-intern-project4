# db/__init__.py
"""Database package — connection pool, models, and queries."""

from db.connection import (
    DatabaseConnectionError,
    DatabaseQueryError,
    async_health_check,
    dispose_engines,
    get_async_engine,
    get_async_session,
    get_engine,
    get_session,
    health_check,
    wait_for_db,
)

__all__ = [
    "DatabaseConnectionError",
    "DatabaseQueryError",
    "async_health_check",
    "dispose_engines",
    "get_async_engine",
    "get_async_session",
    "get_engine",
    "get_session",
    "health_check",
    "wait_for_db",
]
