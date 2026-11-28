# tests/test_models.py
"""
Tests for db/models.py and db/queries.py (P1-T06).

Covers:
- Unit checks: 42-table registry, 5 ENUM mappings, configure_mappers(),
  non-PII __repr__ guarantees, and db.queries input validation
- Integration checks: 1:1 reflection parity (tables, columns, PKs, nullability, FKs,
  ENUM labels) against live PostgreSQL, ORM relationship traversal, and read-only `raw` queries
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, configure_mappers

from db.models import (
    ALL_MODELS,
    EXPECTED_ENUM_MAP,
    MODEL_BY_TABLE,
    ApprovalStatus,
    Base,
    Branch,
    Customer,
    CustomerAddress,
    Invoice,
    OrderStatus,
    Partner,
    PaymentStatus,
    SalesPerson,
    ShippingStatus,
    User,
    UserRole,
)
from db.queries import (
    fetch_invoice_summary_counts,
    get_model_inventory,
    get_registered_table_names,
    get_schema_row_counts,
    get_schema_table_names,
    get_table_model,
    partition_empty_tables,
    schema_execution_options,
    validate_schema_name,
)
from etl.restore import EXPECTED_TABLE_COUNT


# ---------------------------------------------------------------------------
# Unit Tests (No DB Required)
# ---------------------------------------------------------------------------
class TestORMRegistryUnit:
    """Validate declarative metadata, mapper compilation, and PII-safe repr."""

    def test_metadata_contains_all_42_tables(self):
        assert len(Base.metadata.tables) == EXPECTED_TABLE_COUNT
        assert len(ALL_MODELS) == EXPECTED_TABLE_COUNT
        assert len(MODEL_BY_TABLE) == EXPECTED_TABLE_COUNT
        assert set(Base.metadata.tables.keys()) == set(MODEL_BY_TABLE.keys())

    def test_configure_mappers_compiles_all_relationships(self):
        configure_mappers()

    def test_expected_enum_mappings_defined(self):
        assert set(EXPECTED_ENUM_MAP.keys()) == {
            "approvalstatus",
            "orderstatus",
            "paymentstatus",
            "role",
            "shippingstatus",
        }
        assert [e.value for e in ApprovalStatus] == ["PENDING", "APPROVED", "REJECTED"]
        assert [e.value for e in OrderStatus] == [
            "NEW",
            "CONFIRMED",
            "PREPARING",
            "READY",
            "DELIVERED",
            "CANCELLED",
        ]
        assert [e.value for e in PaymentStatus] == ["UNPAID", "PARTIAL", "PAID"]
        assert [e.value for e in UserRole] == ["ADMIN", "SUPER_ADMIN", "OWNER"]
        assert [e.value for e in ShippingStatus] == [
            "IN_TRANSIT",
            "ARRIVED",
            "DELIVERED",
            "DELAYED",
            "CANCELLED",
        ]

    def test_repr_never_exposes_pii_or_secrets(self):
        now = datetime.now(UTC)
        user = User(
            id=7,
            email="secret_admin@example.invalid",
            password_hash="!DISABLED_NON_HASH!",
            role=UserRole.OWNER,
            branch_id=1,
            full_name="Secret Full Name",
            phone="01099988877",
            address="99 Private St",
            gmail="private_gmail@gmail.com",
            is_active=False,
            archived=False,
            profile_completed=True,
            permissions={},
            token_version=1,
            created_at=now,
            updated_at=now,
        )
        user_repr = repr(user)
        for sensitive in (
            "secret_admin@example.invalid",
            "!DISABLED_NON_HASH!",
            "Secret Full Name",
            "01099988877",
            "99 Private St",
            "private_gmail@gmail.com",
        ):
            assert sensitive not in user_repr
        assert "id=7" in user_repr
        assert "OWNER" in user_repr

        cust = Customer(
            id=42,
            name="Private Customer",
            phone="01122334455",
            email="cust@example.invalid",
            notes="Confidential note",
            archived=False,
            created_at=now,
            updated_at=now,
        )
        cust_repr = repr(cust)
        assert "Private Customer" not in cust_repr
        assert "01122334455" not in cust_repr
        assert "cust@example.invalid" not in cust_repr
        assert "Confidential note" not in cust_repr
        assert "id=42" in cust_repr

        addr = CustomerAddress(
            id=5,
            customer_id=42,
            label="Home",
            address="Secret Building 5",
            is_default=True,
        )
        assert "Secret Building 5" not in repr(addr)

        branch = Branch(
            id=2,
            name="Branch Secret Name",
            address="Secret Branch Address",
            phone="01234567890",
            phones=["01234567890"],
            active=True,
            archived=False,
            created_at=now,
        )
        assert "Secret Branch Address" not in repr(branch)
        assert "01234567890" not in repr(branch)

        partner = Partner(
            id=3,
            name="Partner Secret",
            phone="01555666777",
            email="partner@example.invalid",
            notes="note",
            active=True,
            archived=False,
            created_at=now,
            updated_at=now,
        )
        assert "Partner Secret" not in repr(partner)
        assert "01555666777" not in repr(partner)
        assert "partner@example.invalid" not in repr(partner)

        sp = SalesPerson(
            id=4,
            branch_id=1,
            name="Sales Person Secret",
            phone="01077788899",
            active=True,
            archived=False,
            created_at=now,
        )
        assert "Sales Person Secret" not in repr(sp)
        assert "01077788899" not in repr(sp)

        inv = Invoice(
            id=99,
            number="INV-TEST-0099",
            customer_id=42,
            created_by_id=7,
            branch_id=2,
            sales_person_id=4,
            supplier_id=None,
            is_supplier_importer=False,
            party_type=None,
            status=OrderStatus.DELIVERED,
            is_draft=False,
            subtotal=Decimal("100.00"),
            discount_type="none",
            discount_value=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            discount_reason="",
            shipping_required=False,
            shipping_price=Decimal("0.00"),
            shipping_address="Secret Receiver Address",
            shipping_receiver_name="Secret Receiver",
            shipping_receiver_phone="01999999999",
            shipping_notes="",
            shipping_status=None,
            total=Decimal("100.00"),
            paid_amount=Decimal("100.00"),
            remaining_amount=Decimal("0.00"),
            payment_status=PaymentStatus.PAID,
            due_date=None,
            payment_notes="",
            notes="",
            cancel_reason="",
            cancelled_by_id=None,
            cancelled_at=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        inv_repr = repr(inv)
        assert "Secret Receiver" not in inv_repr
        assert "01999999999" not in inv_repr
        assert "INV-TEST-0099" in inv_repr


class TestQueryHelpersUnit:
    """Unit tests for db/queries.py registry and validation helpers."""

    def test_validate_schema_name_allows_project_schemas(self):
        for schema in ("public", "raw", "staging", "analytics", "dq_audit"):
            assert validate_schema_name(schema) == schema

    def test_validate_schema_name_rejects_unknown_schema(self):
        with pytest.raises(ValueError, match="Unsupported schema"):
            validate_schema_name("pg_temp_unknown; DROP TABLE invoices;")

    def test_get_table_model_and_inventory(self):
        assert get_table_model("invoices") is Invoice
        with pytest.raises(KeyError, match="Unknown table name"):
            get_table_model("does_not_exist")

        names = get_registered_table_names()
        assert len(names) == EXPECTED_TABLE_COUNT
        inventory = get_model_inventory()
        assert inventory["invoices"]["column_count"] == 36
        assert inventory["invoices"]["pk_columns"] == ["id"]


# ---------------------------------------------------------------------------
# Integration Tests (PostgreSQL Required)
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestORMReflectionParityIntegration:
    """Compare SQLAlchemy ORM models 1:1 against live PostgreSQL `inspect(engine)`."""

    def test_table_names_and_enums_match_postgresql(self, db_engine: Engine):
        insp = inspect(db_engine)
        db_tables = set(insp.get_table_names(schema="public"))
        assert db_tables == set(Base.metadata.tables.keys())

        db_enums = {e["name"]: list(e["labels"]) for e in insp.get_enums(schema="public")}
        assert set(db_enums.keys()) == set(EXPECTED_ENUM_MAP.keys())
        for enum_name, enum_cls in EXPECTED_ENUM_MAP.items():
            assert db_enums[enum_name] == [item.value for item in enum_cls]

    def test_all_42_tables_columns_pks_nullability_and_fks_match(self, db_engine: Engine):
        insp = inspect(db_engine)
        for table_name, table in Base.metadata.tables.items():
            db_cols = {c["name"]: c for c in insp.get_columns(table_name, schema="public")}
            orm_cols = {c.name: c for c in table.columns}

            assert set(orm_cols.keys()) == set(db_cols.keys()), (
                f"Column mismatch in {table_name}: "
                f"ORM-only={set(orm_cols) - set(db_cols)}, DB-only={set(db_cols) - set(orm_cols)}"
            )

            for col_name, orm_col in orm_cols.items():
                assert orm_col.nullable == db_cols[col_name]["nullable"], (
                    f"Nullability mismatch on {table_name}.{col_name}: "
                    f"ORM={orm_col.nullable}, DB={db_cols[col_name]['nullable']}"
                )

            db_pk = insp.get_pk_constraint(table_name, schema="public")["constrained_columns"]
            orm_pk = [c.name for c in table.primary_key.columns]
            assert orm_pk == db_pk, f"PK mismatch on {table_name}: ORM={orm_pk}, DB={db_pk}"

            db_fks = {
                (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                for fk in insp.get_foreign_keys(table_name, schema="public")
            }
            orm_fks = {
                (
                    (fk.parent.name,),
                    fk.column.table.name,
                    (fk.column.name,),
                )
                for fk in table.foreign_keys
            }
            assert orm_fks == db_fks, f"FK mismatch on {table_name}: ORM={orm_fks}, DB={db_fks}"


@pytest.mark.integration
class TestORMQueriesAndRelationshipsIntegration:
    """Verify real ORM queries, relationship traversal, and read-only raw mapping."""

    def test_traverse_invoice_relationships(self, db_session: Session):
        inv = db_session.scalar(select(Invoice).limit(1))
        assert inv is not None
        assert inv.customer is not None
        assert inv.sales_person is not None
        assert inv.created_by is not None
        assert isinstance(inv.items, list)
        assert isinstance(inv.payments, list)
        assert isinstance(inv.payment_status, PaymentStatus)

    def test_user_repr_with_live_rows_never_leaks_secrets(self, db_session: Session):
        users = db_session.scalars(select(User)).all()
        assert len(users) == 9
        for user in users:
            rendered = repr(user)
            assert "!DISABLED_NON_HASH!" not in rendered
            assert "@example.invalid" not in rendered
            assert user.email not in rendered

    def test_raw_schema_orm_query_and_row_counts(self, db_session: Session):
        tables = get_schema_table_names(db_session, schema="raw")
        assert len(tables) == EXPECTED_TABLE_COUNT

        counts = get_schema_row_counts(db_session, schema="raw")
        assert counts["invoices"] == 409
        assert counts["products"] == 461
        assert counts["customers"] == 335
        assert counts["payments"] == 571
        assert counts["audit_logs"] == 2297

        populated, empty = partition_empty_tables(db_session, schema="raw")
        assert len(populated) + len(empty) == EXPECTED_TABLE_COUNT
        assert len(empty) == 17
        assert "categories" in empty

        summary = fetch_invoice_summary_counts(db_session, schema="raw")
        assert summary["schema"] == "raw"
        assert summary["total_invoices"] == 409
        assert summary["min_created_at"] is not None
        assert summary["max_created_at"] is not None

        # Verify ORM query executed against immutable `raw` schema via schema_translate_map
        raw_inv = db_session.scalar(
            select(Invoice).limit(1),
            execution_options=schema_execution_options("raw"),
        )
        assert raw_inv is not None
