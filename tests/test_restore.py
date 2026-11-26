# tests/test_restore.py
"""
Tests for etl/restore.py — SQL dump restoration and raw schema creation.
Integration tests that require a running PostgreSQL Docker container.
"""

from __future__ import annotations

import subprocess

import pytest

from config.settings import get_settings

# Force reload settings from real .env (not test overrides from conftest)
get_settings.cache_clear()

from etl.restore import (
    EXPECTED_ENUM_TYPES,
    EXPECTED_TABLE_COUNT,
    _psql,
    verify_restore,
)


# ---------------------------------------------------------------------------
# Skip all tests if PostgreSQL container is not running
# ---------------------------------------------------------------------------
def _pg_is_healthy() -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", "hvia_postgres"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "healthy"
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_is_healthy(),
    reason="PostgreSQL container 'hvia_postgres' is not running/healthy",
)


class TestRestoreVerification:
    """Tests that verify the restored database state."""

    def test_public_table_count(self):
        """Public schema should contain exactly 42 base tables."""
        count = int(_psql(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"
        ))
        assert count == EXPECTED_TABLE_COUNT, (
            f"Expected {EXPECTED_TABLE_COUNT} public tables, found {count}"
        )

    def test_raw_schema_exists(self):
        """The 'raw' schema should exist."""
        result = _psql(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name = 'raw';"
        )
        assert result == "raw", "Schema 'raw' does not exist"

    def test_raw_table_count(self):
        """Raw schema should contain the same number of tables as public."""
        raw_count = int(_psql(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'raw' AND table_type = 'BASE TABLE';"
        ))
        assert raw_count == EXPECTED_TABLE_COUNT, (
            f"Expected {EXPECTED_TABLE_COUNT} raw tables, found {raw_count}"
        )

    def test_raw_tables_match_public(self):
        """Raw schema table names should exactly match public schema."""
        public_csv = _psql(
            "SELECT string_agg(table_name, ',' ORDER BY table_name) "
            "FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"
        )
        raw_csv = _psql(
            "SELECT string_agg(table_name, ',' ORDER BY table_name) "
            "FROM information_schema.tables "
            "WHERE table_schema = 'raw' AND table_type = 'BASE TABLE';"
        )
        public_set = set(public_csv.split(","))
        raw_set = set(raw_csv.split(","))
        assert public_set == raw_set, (
            f"Table mismatch. Only in public: {public_set - raw_set}, "
            f"Only in raw: {raw_set - public_set}"
        )

    def test_enum_types_exist(self):
        """All 5 expected ENUM types should exist."""
        enums_csv = _psql(
            "SELECT string_agg(typname, ',') FROM pg_type WHERE typtype = 'e';"
        )
        found = {e.strip() for e in enums_csv.split(",") if e.strip()}
        assert found == EXPECTED_ENUM_TYPES, (
            f"ENUM mismatch. Expected: {EXPECTED_ENUM_TYPES}, Found: {found}"
        )

    def test_raw_row_counts_match_public(self):
        """Row counts in raw tables should match public tables."""
        # Get list of tables
        tables_csv = _psql(
            "SELECT string_agg(tablename, ',') FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename NOT LIKE 'pg_%';"
        )
        tables = [t.strip() for t in tables_csv.split(",") if t.strip()]

        mismatches = []
        for table in tables:
            pub_count = int(_psql(f"SELECT COUNT(*) FROM public.{table};"))
            raw_count = int(_psql(f"SELECT COUNT(*) FROM raw.{table};"))
            if pub_count != raw_count:
                mismatches.append(f"{table}: public={pub_count}, raw={raw_count}")

        assert not mismatches, (
            f"Row count mismatches:\n" + "\n".join(mismatches)
        )

    def test_verify_restore_summary(self):
        """verify_restore() returns a valid summary dict."""
        summary = verify_restore()
        assert summary["public_table_count"] == EXPECTED_TABLE_COUNT
        assert summary["raw_table_count"] == EXPECTED_TABLE_COUNT
        assert summary["tables_match"] is True
        assert summary["enum_match"] is True
        assert summary["enum_types"] == EXPECTED_ENUM_TYPES

    def test_restore_guard_prevents_double_restore(self):
        """
        Re-running the restore on a populated DB should trigger
        the $student_restore_guard$ and fail safely.
        """
        from etl.restore import execute_restore, copy_dump_to_container
        from pathlib import Path
        from config.settings import PROJECT_ROOT

        dump_path = PROJECT_ROOT / "amira_training_original_invoices_fixed.sql"
        if not dump_path.exists():
            pytest.skip("SQL dump file not found")

        # Ensure dump is in container
        copy_dump_to_container(dump_path)

        # Attempting to restore again should raise RuntimeError
        with pytest.raises(RuntimeError, match="[Rr]estore guard|already contains"):
            execute_restore()
