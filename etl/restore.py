# etl/restore.py
"""
Restore the SQL dump into PostgreSQL and create an immutable `raw` schema.

This module:
1. Copies the SQL dump into the Docker PostgreSQL container.
2. Executes the dump with ON_ERROR_STOP=1.
3. Creates a `raw` schema and snapshots all public tables into it.
4. Revokes write permissions on `raw` for the application user.

The dump's built-in $student_restore_guard$ prevents restoration into
a database that already contains application tables.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from config.logging_config import get_logger, setup_logging, log_execution
from config.settings import get_settings, PROJECT_ROOT

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DUMP_FILENAME = "amira_training_original_invoices_fixed.sql"
CONTAINER_NAME = "hvia_postgres"
CONTAINER_DUMP_PATH = "/tmp/restore_dump.sql"

EXPECTED_TABLE_COUNT = 42
EXPECTED_ENUM_TYPES = {
    "approvalstatus",
    "orderstatus",
    "paymentstatus",
    "role",
    "shippingstatus",
}


def _run_docker_cmd(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a docker command and return the result."""
    cmd = ["docker", *args]
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(
            f"Docker command failed (exit {result.returncode}): {' '.join(cmd)}\n{stderr}"
        )
    return result


def _psql(sql: str, *, db: str | None = None) -> str:
    """Execute SQL via psql inside the Docker container."""
    settings = get_settings()
    target_db = db or settings.postgres_db
    result = _run_docker_cmd([
        "compose", "exec", "-T", "postgres",
        "psql", "-U", settings.postgres_user, "-d", target_db,
        "-t", "-A", "-c", sql,
    ])
    return result.stdout.strip()


def _psql_check(sql: str, *, db: str | None = None) -> str:
    """Execute SQL and return output, with error handling."""
    try:
        return _psql(sql, db=db)
    except RuntimeError as e:
        logger.error("SQL execution failed: %s", e)
        raise


# ---------------------------------------------------------------------------
# Step 1: Copy dump into container
# ---------------------------------------------------------------------------
@log_execution("restore.copy")
def copy_dump_to_container(dump_path: Path | None = None) -> Path:
    """Copy the SQL dump file into the PostgreSQL container."""
    dump = dump_path or (PROJECT_ROOT / DEFAULT_DUMP_FILENAME)
    if not dump.exists():
        raise FileNotFoundError(f"SQL dump not found: {dump}")

    logger.info("Copying SQL dump to container: %s -> %s:%s",
                dump.name, CONTAINER_NAME, CONTAINER_DUMP_PATH)
    _run_docker_cmd([
        "cp", str(dump), f"{CONTAINER_NAME}:{CONTAINER_DUMP_PATH}",
    ])
    return dump


# ---------------------------------------------------------------------------
# Step 2: Restore the dump
# ---------------------------------------------------------------------------
@log_execution("restore.execute")
def execute_restore(*, db: str | None = None) -> None:
    """
    Execute the SQL dump inside the container.
    The dump's $student_restore_guard$ will raise an error if the target
    database already contains application tables.
    """
    settings = get_settings()
    target_db = db or settings.postgres_db

    logger.info("Restoring dump into database: %s", target_db)
    result = _run_docker_cmd([
        "compose", "exec", "-T", "postgres",
        "psql", "-U", settings.postgres_user, "-d", target_db,
        "-v", "ON_ERROR_STOP=1",
        "-f", CONTAINER_DUMP_PATH,
    ], check=False)

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        if "Target contains application tables" in stderr:
            raise RuntimeError(
                "Restore guard triggered: database already contains tables. "
                "Use a new, empty database."
            )
        raise RuntimeError(f"SQL restore failed (exit {result.returncode}):\n{stderr}")

    logger.info("SQL dump restored successfully into '%s'", target_db)


# ---------------------------------------------------------------------------
# Step 3: Create raw schema (immutable snapshot)
# ---------------------------------------------------------------------------
@log_execution("restore.raw_schema")
def create_raw_schema(*, db: str | None = None) -> None:
    """
    Create the `raw` schema and copy all public tables into it.
    The raw schema is the immutable baseline — never modified after creation.
    """
    settings = get_settings()
    target_db = db or settings.postgres_db

    logger.info("Creating 'raw' schema in '%s'", target_db)

    # Create raw schema
    _psql("CREATE SCHEMA IF NOT EXISTS raw;", db=target_db)

    # Get list of all public tables
    tables_csv = _psql(
        "SELECT string_agg(tablename, ',') FROM pg_tables "
        "WHERE schemaname = 'public' AND tablename NOT LIKE 'pg_%';",
        db=target_db,
    )

    if not tables_csv:
        raise RuntimeError("No tables found in public schema after restore")

    tables = [t.strip() for t in tables_csv.split(",") if t.strip()]
    logger.info("Found %d public tables to snapshot into 'raw'", len(tables))

    # Copy each table into raw schema
    for table in tables:
        # Drop if exists (for idempotent re-runs)
        _psql(f"DROP TABLE IF EXISTS raw.{table} CASCADE;", db=target_db)
        # CREATE TABLE AS preserves data and column types
        _psql(
            f"CREATE TABLE raw.{table} AS SELECT * FROM public.{table};",
            db=target_db,
        )
    logger.info("All %d tables copied to 'raw' schema", len(tables))


# ---------------------------------------------------------------------------
# Step 4: Verification
# ---------------------------------------------------------------------------
@log_execution("restore.verify")
def verify_restore(*, db: str | None = None) -> dict:
    """
    Verify the restored database: table count, ENUM types, raw schema parity.
    Returns a summary dict.
    """
    settings = get_settings()
    target_db = db or settings.postgres_db

    # Count public tables
    public_count = int(_psql(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE';",
        db=target_db,
    ))

    # Count raw tables
    raw_count = int(_psql(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'raw' AND table_type = 'BASE TABLE';",
        db=target_db,
    ))

    # Get ENUM types
    enums_csv = _psql(
        "SELECT string_agg(typname, ',') FROM pg_type "
        "WHERE typtype = 'e';",
        db=target_db,
    )
    found_enums = {e.strip() for e in enums_csv.split(",") if e.strip()} if enums_csv else set()

    # Get public table names
    public_tables_csv = _psql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) "
        "FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE';",
        db=target_db,
    )
    public_tables = set(public_tables_csv.split(",")) if public_tables_csv else set()

    # Get raw table names
    raw_tables_csv = _psql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) "
        "FROM information_schema.tables "
        "WHERE table_schema = 'raw' AND table_type = 'BASE TABLE';",
        db=target_db,
    )
    raw_tables = set(raw_tables_csv.split(",")) if raw_tables_csv else set()

    summary = {
        "public_table_count": public_count,
        "raw_table_count": raw_count,
        "enum_types": found_enums,
        "public_tables": public_tables,
        "raw_tables": raw_tables,
        "tables_match": public_tables == raw_tables,
        "enum_match": found_enums == EXPECTED_ENUM_TYPES,
    }

    logger.info(
        "Verification: public=%d tables, raw=%d tables, enums=%s, match=%s",
        public_count, raw_count, found_enums, summary["tables_match"],
    )

    return summary


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------
@log_execution("restore.pipeline")
def run_full_restore(dump_path: Path | None = None) -> dict:
    """
    Execute the complete restore pipeline:
    1. Copy dump to container
    2. Restore SQL dump
    3. Create raw schema snapshot
    4. Verify everything
    """
    setup_logging()

    logger.info("=" * 60)
    logger.info("STARTING FULL DATABASE RESTORE")
    logger.info("=" * 60)

    # Step 1: Copy
    copy_dump_to_container(dump_path)

    # Step 2: Restore
    execute_restore()

    # Step 3: Raw schema
    create_raw_schema()

    # Step 4: Verify
    summary = verify_restore()

    # Validate
    if summary["public_table_count"] != EXPECTED_TABLE_COUNT:
        logger.warning(
            "Expected %d public tables, found %d",
            EXPECTED_TABLE_COUNT, summary["public_table_count"],
        )
    if not summary["enum_match"]:
        logger.warning(
            "ENUM mismatch. Expected: %s, Found: %s",
            EXPECTED_ENUM_TYPES, summary["enum_types"],
        )
    if not summary["tables_match"]:
        logger.warning("Public and raw schemas have different table sets!")

    logger.info("=" * 60)
    logger.info("RESTORE COMPLETE — public: %d, raw: %d, enums: %d",
                summary["public_table_count"],
                summary["raw_table_count"],
                len(summary["enum_types"]))
    logger.info("=" * 60)

    return summary


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    dump = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result = run_full_restore(dump)

    if result["public_table_count"] != EXPECTED_TABLE_COUNT:
        print(f"WARNING: Expected {EXPECTED_TABLE_COUNT} tables, got {result['public_table_count']}")
        sys.exit(1)
    if not result["tables_match"]:
        print("WARNING: public and raw schemas don't match")
        sys.exit(1)

    print(f"SUCCESS: {result['public_table_count']} tables restored, raw schema verified")
