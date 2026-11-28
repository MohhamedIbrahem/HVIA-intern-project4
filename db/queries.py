# db/queries.py
"""
Base metadata helpers and read-only query utilities across PostgreSQL schemas.

Supports inspecting table metadata, computing single-roundtrip schema row counts,
routing ORM queries to `raw` (immutable source) or `public` via `schema_translate_map`,
and fetching baseline operational counts without hardcoding raw business records.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.models import (
    ALL_MODELS,
    MODEL_BY_TABLE,
    Base,
    Invoice,
    OrderStatus,
)

ALLOWED_SCHEMAS: frozenset[str] = frozenset({"public", "raw", "staging", "analytics", "dq_audit"})


def validate_schema_name(schema: str) -> str:
    """Validate that a target PostgreSQL schema belongs to the project allowlist."""
    if schema not in ALLOWED_SCHEMAS:
        raise ValueError(
            f"Unsupported schema {schema!r}. Allowed schemas: {sorted(ALLOWED_SCHEMAS)}"
        )
    return schema


def get_table_model(table_name: str) -> type[Base]:
    """Return the SQLAlchemy ORM model class registered for `table_name`."""
    try:
        return MODEL_BY_TABLE[table_name]
    except KeyError as exc:
        raise KeyError(f"Unknown table name in ORM registry: {table_name!r}") from exc


def get_registered_table_names() -> list[str]:
    """Return all 42 ORM-registered table names in sorted order."""
    return sorted(MODEL_BY_TABLE.keys())


def get_model_inventory() -> dict[str, dict[str, Any]]:
    """Return structural metadata (columns, PKs, FKs) from the ORM registry."""
    inventory: dict[str, dict[str, Any]] = {}
    for model in ALL_MODELS:
        table = model.__table__
        inventory[table.name] = {
            "model_name": model.__name__,
            "column_count": len(table.columns),
            "pk_columns": [col.name for col in table.primary_key.columns],
            "fk_count": len(table.foreign_keys),
        }
    return inventory


def schema_execution_options(schema: str = "raw") -> dict[str, Any]:
    """
    Build SQLAlchemy execution_options to route unqualified ORM tables into `schema`
    (for example, read-only ORM queries against `raw` or `staging`).
    """
    validate_schema_name(schema)
    return {"schema_translate_map": {None: schema}}


def get_schema_table_names(session: Session, schema: str = "public") -> list[str]:
    """Query `information_schema.tables` for all base tables in `schema`."""
    validate_schema_name(schema)
    rows = session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        ),
        {"schema": schema},
    ).scalars()
    return list(rows)


def get_schema_row_counts(session: Session, schema: str = "public") -> dict[str, int]:
    """
    Return exact row counts for all base tables in `schema` using one SQL roundtrip.
    Never modifies data and handles empty tables cleanly (`0`).
    """
    validate_schema_name(schema)
    rows = session.execute(
        text(
            """
            SELECT
                c.relname AS table_name,
                (xpath('/row/cnt/text()', query_to_xml(
                    format('SELECT count(*) AS cnt FROM %I.%I', n.nspname, c.relname),
                    false, true, ''
                )))[1]::text::bigint AS row_cnt
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema
              AND c.relkind = 'r'
            ORDER BY c.relname
            """
        ),
        {"schema": schema},
    ).all()
    return {row.table_name: int(row.row_cnt) for row in rows}


def partition_empty_tables(
    session: Session, schema: str = "public"
) -> tuple[dict[str, int], list[str]]:
    """
    Split `schema` tables into `(populated_counts, empty_table_names)`.
    Useful for diagnostics/profiling where the 17 zero-row tables skip row rules.
    """
    counts = get_schema_row_counts(session, schema=schema)
    populated = {tbl: cnt for tbl, cnt in counts.items() if cnt > 0}
    empty = [tbl for tbl, cnt in counts.items() if cnt == 0]
    return populated, empty


def fetch_invoice_summary_counts(
    session: Session,
    *,
    schema: str = "raw",
) -> dict[str, Any]:
    """
    Fetch read-only summary counts from `invoices` inside `schema` (defaults to `raw`).
    Uses ORM query + `schema_translate_map`.
    """
    exec_opts = schema_execution_options(schema)

    total_count = (
        session.scalar(select(func.count(Invoice.id)), execution_options=exec_opts) or 0
    )
    cancelled_count = (
        session.scalar(
            select(func.count(Invoice.id)).where(Invoice.status == OrderStatus.CANCELLED),
            execution_options=exec_opts,
        )
        or 0
    )
    draft_count = (
        session.scalar(
            select(func.count(Invoice.id)).where(Invoice.is_draft.is_(True)),
            execution_options=exec_opts,
        )
        or 0
    )
    min_created, max_created = session.execute(
        select(func.min(Invoice.created_at), func.max(Invoice.created_at)),
        execution_options=exec_opts,
    ).one()

    return {
        "schema": schema,
        "total_invoices": int(total_count),
        "cancelled_invoices": int(cancelled_count),
        "active_non_cancelled": int(total_count - cancelled_count),
        "draft_invoices": int(draft_count),
        "min_created_at": min_created,
        "max_created_at": max_created,
    }
