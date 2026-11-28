# tests/test_restore.py
"""
Tests for etl/restore.py — SQL dump restoration and immutable raw schema creation.
Integration tests that require a running PostgreSQL Docker container.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from config.settings import PROJECT_ROOT, get_settings

# Force reload settings from real .env (not test overrides from conftest)
get_settings.cache_clear()

from etl.restore import (
    EXPECTED_ENUM_TYPES,
    EXPECTED_TABLE_COUNT,
    _psql,
    copy_dump_to_container,
    create_raw_schema,
    execute_restore,
    rebuild_raw_schema,
    verify_restore,
)


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


class TestRestoreVerification:
    """Tests that verify the restored database state in amira_training_lab."""

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
        summary = verify_restore()
        assert summary["tables_match"], (
            f"Only public: {summary['public_tables'] - summary['raw_tables']}, "
            f"Only raw: {summary['raw_tables'] - summary['public_tables']}"
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
        """Row counts in all 42 raw tables match public tables (single SQL query)."""
        summary = verify_restore()
        assert summary["row_counts_match"], f"Row count mismatches: {summary['row_mismatches']}"

    def test_verify_restore_summary(self):
        """verify_restore() returns a complete summary dict with full parity."""
        summary = verify_restore()
        assert summary["public_table_count"] == EXPECTED_TABLE_COUNT
        assert summary["raw_table_count"] == EXPECTED_TABLE_COUNT
        assert summary["tables_match"] is True
        assert summary["enum_match"] is True
        assert summary["row_counts_match"] is True
        assert summary["metadata_match"] is True
        assert summary["enum_types"] == EXPECTED_ENUM_TYPES

    def test_restore_guard_prevents_double_restore_on_disposable_db(self):
        """
        Re-running restore on a populated database triggers $student_restore_guard$
        and fails safely — executed against a disposable test database, never
        against the live training database.
        """
        dump_path = PROJECT_ROOT / "amira_training_original_invoices_fixed.sql"
        if not dump_path.exists():
            pytest.skip("SQL dump file not found")

        disposable_db = f"amira_test_guard_{uuid.uuid4().hex[:8]}"
        copy_dump_to_container(dump_path)

        try:
            _psql(f'CREATE DATABASE "{disposable_db}";', db="postgres")
            # First restore into empty disposable DB must succeed
            execute_restore(db=disposable_db)
            # Second restore into now-populated disposable DB must trigger guard
            with pytest.raises(RuntimeError, match="[Rr]estore guard|already contains"):
                execute_restore(db=disposable_db)
        finally:
            _psql(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{disposable_db}' AND pid <> pg_backend_pid();",
                db="postgres",
            )
            _psql(f'DROP DATABASE IF EXISTS "{disposable_db}";', db="postgres")


class TestRawSchemaConstraintParityAndImmutability:
    """Tests that raw preserves PKs, FKs, NOT NULL, indexes, defaults, and blocks writes."""

    def test_primary_key_parity(self):
        """Raw schema should have identical PK count to public."""
        summary = verify_restore()
        assert summary["raw_pk_count"] == summary["public_pk_count"] == 42

    def test_foreign_key_parity(self):
        """Raw schema should have identical FK count to public (all 70 FKs preserved)."""
        summary = verify_restore()
        assert summary["raw_fk_count"] == summary["public_fk_count"] == 70

    def test_not_null_parity(self):
        """Raw schema should have identical NOT NULL column count to public."""
        summary = verify_restore()
        assert summary["raw_not_null_count"] == summary["public_not_null_count"] == 347

    def test_index_parity(self):
        """Raw schema should have identical index count to public."""
        summary = verify_restore()
        assert summary["raw_index_count"] == summary["public_index_count"] == 176

    def test_default_parity(self):
        """Raw schema should preserve all column defaults."""
        summary = verify_restore()
        assert summary["raw_default_count"] == summary["public_default_count"] == 45

    def test_immutability_blocks_direct_writes(self):
        """INSERT, UPDATE, DELETE, and TRUNCATE against raw tables must all fail."""
        from etl.restore import _compose_cmd, _run_docker_cmd

        settings = get_settings()
        test_statements = [
            "INSERT INTO raw.branches SELECT * FROM raw.branches LIMIT 1;",
            "UPDATE raw.branches SET id = id;",
            "DELETE FROM raw.branches WHERE FALSE;",
            "TRUNCATE TABLE raw.audit_logs;",
        ]
        for stmt in test_statements:
            res = _run_docker_cmd(
                _compose_cmd(
                    "exec", "-T", "postgres",
                    "psql", "-U", settings.postgres_user, "-d", settings.postgres_db,
                    "-c", stmt,
                ),
                check=False,
            )
            assert res.returncode != 0, f"Write succeeded unexpectedly: {stmt}"
            assert "immutable" in (res.stderr or "").lower(), (
                f"Expected immutability error for {stmt}, got: {res.stderr}"
            )

    def test_create_raw_schema_is_non_destructive_on_rerun(self):
        """Re-running create_raw_schema() preserves existing raw tables without dropping them."""
        before_oids = _psql(
            "SELECT string_agg(c.relname || ':' || c.oid::text, ',' ORDER BY c.relname) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'raw' AND c.relkind = 'r';"
        )
        create_raw_schema()
        after_oids = _psql(
            "SELECT string_agg(c.relname || ':' || c.oid::text, ',' ORDER BY c.relname) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'raw' AND c.relkind = 'r';"
        )
        assert before_oids == after_oids, "create_raw_schema() recreated existing raw tables!"

    def test_rebuild_raw_requires_confirmation(self):
        """rebuild_raw_schema() must reject calls without confirm=True."""
        with pytest.raises(ValueError, match="confirm=True"):
            rebuild_raw_schema(confirm=False)
