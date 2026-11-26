# etl/restore.py
"""
Restore the SQL dump into PostgreSQL and create an immutable `raw` schema.

This module:
1. Copies the SQL dump into the Docker PostgreSQL container.
2. Executes the dump with ON_ERROR_STOP=1.
3. Creates a `raw` schema and snapshots all public tables with full metadata
   (PKs, FKs, NOT NULL, defaults, indexes, CHECK/UNIQUE constraints, comments).
4. Enforces immutability via write-privilege revocation, a read-only role, and
   PostgreSQL triggers blocking INSERT/UPDATE/DELETE/TRUNCATE on `raw`.

The dump's built-in $student_restore_guard$ prevents restoration into
a database that already contains application tables.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from config.logging_config import get_logger, log_execution, setup_logging
from config.settings import PROJECT_ROOT, get_settings

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DUMP_FILENAME = "amira_training_original_invoices_fixed.sql"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
CONTAINER_NAME = "hvia_postgres"
CONTAINER_DUMP_PATH = "/tmp/restore_dump.sql"
READONLY_ROLE = "amira_readonly"

EXPECTED_TABLE_COUNT = 42
EXPECTED_ENUM_TYPES = {
    "approvalstatus",
    "orderstatus",
    "paymentstatus",
    "role",
    "shippingstatus",
}

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _validate_identifier(name: str) -> str:
    """Validate that a schema/table/role identifier is safe for DDL interpolation."""
    if not _IDENT_RE.fullmatch(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def _compose_cmd(*args: str) -> list[str]:
    """Build a docker compose command pinned to the repository compose file."""
    return ["compose", "-f", str(COMPOSE_FILE), *args]


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
        check=False,
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
    result = _run_docker_cmd(
        _compose_cmd(
            "exec", "-T", "postgres",
            "psql", "-U", settings.postgres_user, "-d", target_db,
            "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", sql,
        )
    )
    return result.stdout.strip()


def _psql_stdin(sql: str, *, db: str | None = None) -> str:
    """Execute multi-statement SQL via psql stdin inside the Docker container."""
    settings = get_settings()
    target_db = db or settings.postgres_db
    cmd = [
        "docker",
        *_compose_cmd(
            "exec", "-T", "postgres",
            "psql", "-U", settings.postgres_user, "-d", target_db,
            "-t", "-A", "-v", "ON_ERROR_STOP=1",
        ),
    ]
    result = subprocess.run(
        cmd,
        check=False,
        input=sql,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"psql stdin execution failed:\n{stderr}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Step 1: Copy dump into container
# ---------------------------------------------------------------------------
@log_execution("restore.copy")
def copy_dump_to_container(dump_path: Path | None = None) -> Path:
    """Copy the SQL dump file into the PostgreSQL container."""
    dump = dump_path or (PROJECT_ROOT / DEFAULT_DUMP_FILENAME)
    if not dump.exists():
        raise FileNotFoundError(f"SQL dump not found: {dump}")

    logger.info(
        "Copying SQL dump to container: %s -> %s:%s",
        dump.name, CONTAINER_NAME, CONTAINER_DUMP_PATH,
    )
    _run_docker_cmd(["cp", str(dump), f"{CONTAINER_NAME}:{CONTAINER_DUMP_PATH}"])
    return dump


# ---------------------------------------------------------------------------
# Step 2: Restore the dump
# ---------------------------------------------------------------------------
@log_execution("restore.execute")
def execute_restore(*, db: str | None = None) -> None:
    """
    Execute the SQL dump inside the container.
    The dump's $student_restore_guard$ raises an error if the target
    database already contains application tables.
    """
    settings = get_settings()
    target_db = db or settings.postgres_db

    logger.info("Restoring dump into database: %s", target_db)
    result = _run_docker_cmd(
        _compose_cmd(
            "exec", "-T", "postgres",
            "psql", "-U", settings.postgres_user, "-d", target_db,
            "-v", "ON_ERROR_STOP=1",
            "-f", CONTAINER_DUMP_PATH,
        ),
        check=False,
    )

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
def _copy_raw_foreign_keys(*, db: str | None = None) -> int:
    """
    Replicate all FOREIGN KEY constraints from `public` onto `raw` tables
    pointing to `raw.<ref_table>` (LIKE ... INCLUDING ALL omits FKs).
    Returns count of FK constraints added.
    """
    fk_sql = """
    DO $$
    DECLARE
        r RECORD;
        fk_def TEXT;
        added_cnt INT := 0;
    BEGIN
        FOR r IN
            SELECT
                c.oid AS con_oid,
                c.conname AS con_name,
                cls.relname AS tbl_name,
                pg_get_constraintdef(c.oid, true) AS con_def
            FROM pg_constraint c
            JOIN pg_class cls ON cls.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = 'public'
              AND c.contype = 'f'
            ORDER BY cls.relname, c.conname
        LOOP
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint rc
                JOIN pg_class rcls ON rcls.oid = rc.conrelid
                JOIN pg_namespace rn ON rn.oid = rc.connamespace
                WHERE rn.nspname = 'raw'
                  AND rcls.relname = r.tbl_name
                  AND rc.conname = r.con_name
            ) THEN
                -- Rewrite REFERENCES table(...) or REFERENCES public.table(...) -> REFERENCES raw.table(...)
                fk_def := regexp_replace(
                    r.con_def,
                    'REFERENCES (?:public\\.)?([a-zA-Z0-9_"]+)',
                    'REFERENCES raw.\\1',
                    'g'
                );
                EXECUTE format(
                    'ALTER TABLE raw.%I ADD CONSTRAINT %I %s;',
                    r.tbl_name,
                    r.con_name,
                    fk_def
                );
                added_cnt := added_cnt + 1;
            END IF;
        END LOOP;
        RAISE NOTICE 'Added % FK constraints to raw schema', added_cnt;
    END $$;
    """
    _psql_stdin(fk_sql, db=db)
    raw_fk_count = int(
        _psql(
            "SELECT COUNT(*) FROM information_schema.table_constraints "
            "WHERE constraint_schema = 'raw' AND constraint_type = 'FOREIGN KEY';",
            db=db,
        )
    )
    logger.info("Raw schema foreign keys verified: %d", raw_fk_count)
    return raw_fk_count


def _enforce_raw_immutability(*, db: str | None = None) -> None:
    """
    Lock down the `raw` schema against accidental or unauthorized mutations:
    1. Create a non-superuser read-only role `amira_readonly` with SELECT on `raw`.
    2. Revoke INSERT, UPDATE, DELETE, TRUNCATE from PUBLIC and app user.
    3. Install BEFORE INSERT/UPDATE/DELETE/TRUNCATE triggers on all `raw` tables
       so even superuser sessions cannot mutate `raw` without explicit rebuild.
    """
    settings = get_settings()
    app_user = _validate_identifier(settings.postgres_user)
    ro_role = _validate_identifier(READONLY_ROLE)

    lock_sql = f"""
    DO $$
    DECLARE
        tbl TEXT;
    BEGIN
        -- 1. Ensure read-only role exists
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ro_role}') THEN
            CREATE ROLE {ro_role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
        END IF;

        -- 2. Grant strictly read-only privileges to amira_readonly
        GRANT USAGE ON SCHEMA raw TO {ro_role};
        GRANT SELECT ON ALL TABLES IN SCHEMA raw TO {ro_role};
        ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT SELECT ON TABLES TO {ro_role};

        -- 3. Revoke write privileges from PUBLIC and app role
        REVOKE CREATE ON SCHEMA raw FROM PUBLIC;
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA raw FROM PUBLIC;
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA raw FROM {app_user};
        ALTER DEFAULT PRIVILEGES IN SCHEMA raw
            REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLES FROM {app_user};
    END $$;

    CREATE OR REPLACE FUNCTION raw.prevent_raw_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $fn$
    BEGIN
        RAISE EXCEPTION 'raw schema is immutable: % on %.% is forbidden',
            TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END;
    $fn$;

    DO $$
    DECLARE
        tbl TEXT;
    BEGIN
        FOR tbl IN
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'raw'
            ORDER BY tablename
        LOOP
            IF NOT EXISTS (
                SELECT 1
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'raw'
                  AND c.relname = tbl
                  AND t.tgname = 'trg_raw_immutable_row'
            ) THEN
                EXECUTE format(
                    'CREATE TRIGGER trg_raw_immutable_row '
                    'BEFORE INSERT OR UPDATE OR DELETE ON raw.%I '
                    'FOR EACH ROW EXECUTE FUNCTION raw.prevent_raw_mutation();',
                    tbl
                );
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'raw'
                  AND c.relname = tbl
                  AND t.tgname = 'trg_raw_immutable_stmt'
            ) THEN
                EXECUTE format(
                    'CREATE TRIGGER trg_raw_immutable_stmt '
                    'BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON raw.%I '
                    'FOR EACH STATEMENT EXECUTE FUNCTION raw.prevent_raw_mutation();',
                    tbl
                );
            END IF;
        END LOOP;
    END $$;
    """
    _psql_stdin(lock_sql, db=db)
    logger.info("Immutability triggers and read-only permissions active on 'raw' schema")


def ensure_raw_protection(*, db: str | None = None) -> None:
    """
    Idempotently apply missing FK constraints and immutability locks onto an
    already-created `raw` schema without modifying or dropping existing rows.
    """
    _copy_raw_foreign_keys(db=db)
    _enforce_raw_immutability(db=db)


@log_execution("restore.raw_schema")
def create_raw_schema(*, db: str | None = None) -> None:
    """
    Safely create the `raw` schema and snapshot any missing public tables into it
    preserving full metadata (PKs, FKs, NOT NULL, defaults, indexes, comments).

    Existing `raw` tables are NEVER dropped or overwritten here; use
    `rebuild_raw_schema(confirm=True)` when an explicit full rebuild is required.
    """
    settings = get_settings()
    target_db = db or settings.postgres_db

    logger.info("Ensuring 'raw' schema snapshot in '%s' (non-destructive)", target_db)
    _psql("CREATE SCHEMA IF NOT EXISTS raw;", db=target_db)

    tables_csv = _psql(
        "SELECT string_agg(tablename, ',' ORDER BY tablename) FROM pg_tables "
        "WHERE schemaname = 'public' AND tablename NOT LIKE 'pg_%';",
        db=target_db,
    )
    if not tables_csv:
        raise RuntimeError("No tables found in public schema after restore")

    tables = [_validate_identifier(t.strip()) for t in tables_csv.split(",") if t.strip()]

    existing_csv = _psql(
        "SELECT COALESCE(string_agg(tablename, ','), '') FROM pg_tables "
        "WHERE schemaname = 'raw';",
        db=target_db,
    )
    existing_raw = {t.strip() for t in existing_csv.split(",") if t.strip()}

    created_count = 0
    for table in tables:
        if table in existing_raw:
            logger.debug("raw.%s already exists — preserving immutable table", table)
            continue

        _psql(f"CREATE TABLE raw.{table} (LIKE public.{table} INCLUDING ALL);", db=target_db)
        _psql(f"INSERT INTO raw.{table} SELECT * FROM public.{table};", db=target_db)
        created_count += 1

    if created_count:
        logger.info("Created %d new tables in 'raw' schema", created_count)
    else:
        logger.info("All %d 'raw' tables already exist — none dropped or modified", len(tables))

    # Ensure FK parity and immutability enforcement are active
    ensure_raw_protection(db=target_db)


@log_execution("restore.rebuild_raw")
def rebuild_raw_schema(*, confirm: bool = False, db: str | None = None) -> None:
    """
    Explicitly drop and rebuild the `raw` schema from `public`.
    Requires `confirm=True` so callers cannot accidentally wipe `raw`.
    """
    if not confirm:
        raise ValueError(
            "Refusing to rebuild 'raw' schema without explicit confirmation (confirm=True)."
        )

    settings = get_settings()
    target_db = db or settings.postgres_db
    logger.warning("Explicit rebuild of 'raw' schema confirmed in '%s'", target_db)

    _psql("DROP SCHEMA IF EXISTS raw CASCADE;", db=target_db)
    create_raw_schema(db=target_db)


# ---------------------------------------------------------------------------
# Step 4: Verification
# ---------------------------------------------------------------------------
@log_execution("restore.verify")
def verify_restore(*, db: str | None = None) -> dict:
    """
    Verify the restored database in minimal SQL round-trips:
    - Table count and names parity (`public` vs `raw`)
    - ENUM types
    - Single-query row-count parity across all tables
    - Constraint/index parity (PK, FK, NOT NULL, DEFAULT, indexes)
    - Raw immutability triggers presence
    """
    verify_sql = """
    WITH counts AS (
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            (xpath('/row/cnt/text()', query_to_xml(
                format('SELECT count(*) AS cnt FROM %I.%I', n.nspname, c.relname),
                false, true, ''
            )))[1]::text::bigint AS row_cnt
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('public', 'raw')
          AND c.relkind = 'r'
    )
    SELECT 'ROW_MISMATCH=' || COALESCE((
        SELECT string_agg(COALESCE(p.table_name, r.table_name) || ':' || COALESCE(p.row_cnt,-1) || '!=' || COALESCE(r.row_cnt,-1), ',')
        FROM (SELECT * FROM counts WHERE schema_name='public') p
        FULL OUTER JOIN (SELECT * FROM counts WHERE schema_name='raw') r USING (table_name)
        WHERE p.row_cnt IS DISTINCT FROM r.row_cnt
    ), '');

    SELECT 'PUBLIC_TABLES=' || COALESCE((
        SELECT string_agg(table_name, ',' ORDER BY table_name)
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ), '');

    SELECT 'RAW_TABLES=' || COALESCE((
        SELECT string_agg(table_name, ',' ORDER BY table_name)
        FROM information_schema.tables
        WHERE table_schema = 'raw' AND table_type = 'BASE TABLE'
    ), '');

    SELECT 'ENUMS=' || COALESCE((
        SELECT string_agg(typname, ',' ORDER BY typname)
        FROM pg_type WHERE typtype = 'e'
    ), '');

    SELECT 'PUBLIC_PKS=' || COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = 'public' AND constraint_type = 'PRIMARY KEY';
    SELECT 'RAW_PKS=' || COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = 'raw' AND constraint_type = 'PRIMARY KEY';

    SELECT 'PUBLIC_FKS=' || COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = 'public' AND constraint_type = 'FOREIGN KEY';
    SELECT 'RAW_FKS=' || COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_schema = 'raw' AND constraint_type = 'FOREIGN KEY';

    SELECT 'PUBLIC_NOT_NULL=' || COUNT(*) FROM information_schema.columns
        WHERE table_schema = 'public' AND is_nullable = 'NO';
    SELECT 'RAW_NOT_NULL=' || COUNT(*) FROM information_schema.columns
        WHERE table_schema = 'raw' AND is_nullable = 'NO';

    SELECT 'PUBLIC_INDEXES=' || COUNT(*) FROM pg_indexes WHERE schemaname = 'public';
    SELECT 'RAW_INDEXES=' || COUNT(*) FROM pg_indexes WHERE schemaname = 'raw';

    SELECT 'PUBLIC_DEFAULTS=' || COUNT(*) FROM information_schema.columns
        WHERE table_schema = 'public' AND column_default IS NOT NULL;
    SELECT 'RAW_DEFAULTS=' || COUNT(*) FROM information_schema.columns
        WHERE table_schema = 'raw' AND column_default IS NOT NULL;

    SELECT 'RAW_IMMUTABILITY_TRIGGERS=' || COUNT(DISTINCT c.relname)
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'raw' AND t.tgname = 'trg_raw_immutable_stmt';
    """
    raw_out = _psql_stdin(verify_sql, db=db)
    kv: dict[str, str] = {}
    for line in raw_out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()

    public_tables = {t for t in kv.get("PUBLIC_TABLES", "").split(",") if t}
    raw_tables = {t for t in kv.get("RAW_TABLES", "").split(",") if t}
    found_enums = {e for e in kv.get("ENUMS", "").split(",") if e}
    row_mismatch_str = kv.get("ROW_MISMATCH", "")

    pub_pks = int(kv.get("PUBLIC_PKS", 0))
    raw_pks = int(kv.get("RAW_PKS", 0))
    pub_fks = int(kv.get("PUBLIC_FKS", 0))
    raw_fks = int(kv.get("RAW_FKS", 0))
    pub_nn = int(kv.get("PUBLIC_NOT_NULL", 0))
    raw_nn = int(kv.get("RAW_NOT_NULL", 0))
    pub_idx = int(kv.get("PUBLIC_INDEXES", 0))
    raw_idx = int(kv.get("RAW_INDEXES", 0))
    pub_def = int(kv.get("PUBLIC_DEFAULTS", 0))
    raw_def = int(kv.get("RAW_DEFAULTS", 0))
    raw_trig = int(kv.get("RAW_IMMUTABILITY_TRIGGERS", 0))

    metadata_match = (
        pub_pks == raw_pks
        and pub_fks == raw_fks
        and pub_nn == raw_nn
        and pub_idx == raw_idx
        and pub_def == raw_def
    )

    summary = {
        "public_table_count": len(public_tables),
        "raw_table_count": len(raw_tables),
        "enum_types": found_enums,
        "public_tables": public_tables,
        "raw_tables": raw_tables,
        "tables_match": public_tables == raw_tables,
        "enum_match": found_enums == EXPECTED_ENUM_TYPES,
        "row_counts_match": row_mismatch_str == "",
        "row_mismatches": [m for m in row_mismatch_str.split(",") if m],
        "public_pk_count": pub_pks,
        "raw_pk_count": raw_pks,
        "public_fk_count": pub_fks,
        "raw_fk_count": raw_fks,
        "public_not_null_count": pub_nn,
        "raw_not_null_count": raw_nn,
        "public_index_count": pub_idx,
        "raw_index_count": raw_idx,
        "public_default_count": pub_def,
        "raw_default_count": raw_def,
        "metadata_match": metadata_match,
        "raw_immutability_trigger_tables": raw_trig,
    }

    logger.info(
        "Verification: public=%d raw=%d enums=%d tables_match=%s rows_match=%s "
        "metadata_match=%s (PK=%d/%d FK=%d/%d NN=%d/%d IDX=%d/%d DEF=%d/%d) triggers=%d",
        len(public_tables),
        len(raw_tables),
        len(found_enums),
        summary["tables_match"],
        summary["row_counts_match"],
        metadata_match,
        raw_pks, pub_pks,
        raw_fks, pub_fks,
        raw_nn, pub_nn,
        raw_idx, pub_idx,
        raw_def, pub_def,
        raw_trig,
    )

    return summary


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------
@log_execution("restore.pipeline")
def run_full_restore(dump_path: Path | None = None) -> dict:
    """
    Execute the restore pipeline safely:
    1. Copy dump to container
    2. Restore SQL dump (protected by $student_restore_guard$)
    3. Create missing `raw` tables & enforce immutability (never drops existing `raw`)
    4. Verify tables, ENUMs, rows, constraints, and triggers
    """
    setup_logging()

    logger.info("=" * 60)
    logger.info("STARTING DATABASE RESTORE")
    logger.info("=" * 60)

    copy_dump_to_container(dump_path)
    execute_restore()
    create_raw_schema()
    summary = verify_restore()

    if summary["public_table_count"] != EXPECTED_TABLE_COUNT:
        logger.warning(
            "Expected %d public tables, found %d",
            EXPECTED_TABLE_COUNT, summary["public_table_count"],
        )
    if not summary["enum_match"]:
        logger.warning("ENUM mismatch. Expected: %s, Found: %s", EXPECTED_ENUM_TYPES, summary["enum_types"])
    if not summary["tables_match"]:
        logger.warning("Public and raw schemas have different table sets!")
    if not summary["metadata_match"]:
        logger.warning("Public and raw metadata constraints do not fully match!")

    logger.info("=" * 60)
    logger.info(
        "RESTORE COMPLETE — public: %d, raw: %d, metadata_match: %s",
        summary["public_table_count"],
        summary["raw_table_count"],
        summary["metadata_match"],
    )
    logger.info("=" * 60)

    return summary


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore SQL dump & maintain immutable raw schema.")
    parser.add_argument("dump_path", nargs="?", type=Path, help="Optional path to SQL dump file.")
    parser.add_argument(
        "--rebuild-raw",
        action="store_true",
        help="Explicitly rebuild `raw` schema from `public` (requires --confirm).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm destructive raw rebuild operation.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    setup_logging()

    if args.rebuild_raw:
        if not args.confirm:
            print("ERROR: --rebuild-raw requires --confirm to avoid accidental raw schema deletion.")
            sys.exit(2)
        rebuild_raw_schema(confirm=True)
        result = verify_restore()
    else:
        result = run_full_restore(args.dump_path)

    if result["public_table_count"] != EXPECTED_TABLE_COUNT:
        print(f"WARNING: Expected {EXPECTED_TABLE_COUNT} tables, got {result['public_table_count']}")
        sys.exit(1)
    if not (result["tables_match"] and result["row_counts_match"] and result["metadata_match"]):
        print("WARNING: public and raw schemas parity check failed")
        sys.exit(1)

    print(
        f"SUCCESS: {result['public_table_count']} tables verified, "
        f"PK={result['raw_pk_count']}, FK={result['raw_fk_count']}, raw immutability active"
    )
