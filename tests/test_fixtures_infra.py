# tests/test_fixtures_infra.py
"""
Verification tests for shared P1-T07 test infrastructure:
- Transaction/savepoint rollback isolation in `db_session`
- Pydantic synthetic data factories (zero real records)
- Configurable `mock_ollama` fixture (JSON, timeout, offline, zero-PII guard)
- Temporary log & report directory fixtures
- Non-skipping `-m integration` / `HVIA_REQUIRE_INTEGRATION=1` gate behavior
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.connection import get_session
from db.models import Category
from tests.conftest import pytest_collection_modifyitems
from tests.fixtures import (
    ARABIC_PRODUCT_VARIANTS,
    DIRTY_PHONE_SAMPLES,
    MockOllamaController,
    SyntheticCustomer,
    SyntheticInvoice,
    SyntheticPayment,
    SyntheticProduct,
)


# ---------------------------------------------------------------------------
# Synthetic Factories (Unit)
# ---------------------------------------------------------------------------
class TestSyntheticFactories:
    """Verify Pydantic synthetic factories build clean, valid test objects."""

    def test_customer_factory_produces_synthetic_models(self, customer_factory):
        c1 = customer_factory()
        c2 = customer_factory(phone=DIRTY_PHONE_SAMPLES[0])
        assert isinstance(c1, SyntheticCustomer)
        assert c1.id != c2.id
        assert c1.name.startswith("Synthetic_Customer_")
        assert "@example.invalid" in c1.email
        assert c2.phone == "N/A"

    def test_product_factory_arabic_variants(self, product_factory):
        p = product_factory(name=ARABIC_PRODUCT_VARIANTS[0], sale_price="120.50", cost_price="75.00")
        assert isinstance(p, SyntheticProduct)
        assert p.sale_price == Decimal("120.50")
        assert p.cost_price == Decimal("75.00")
        assert p.name == "عرض ال ٦ قطع"

    def test_invoice_factory_reconciles_totals(self, invoice_factory):
        inv = invoice_factory(discount_amount="10.00", shipping_price="25.00")
        assert isinstance(inv, SyntheticInvoice)
        expected_total = inv.subtotal - inv.discount_amount + inv.shipping_price
        assert inv.total == expected_total
        assert inv.paid_amount == expected_total
        assert inv.remaining_amount == Decimal("0.00")
        assert inv.payment_status == "PAID"

    def test_payment_factory_valid_model(self, payment_factory):
        pay = payment_factory(amount="55.25", method="instapay")
        assert isinstance(pay, SyntheticPayment)
        assert pay.amount == Decimal("55.25")
        assert pay.method == "instapay"


# ---------------------------------------------------------------------------
# Mock Ollama Fixture (Unit)
# ---------------------------------------------------------------------------
class TestMockOllamaFixture:
    """Verify `mock_ollama` supports JSON payloads, timeout, offline, and PII checks."""

    def test_default_json_response(self, mock_ollama: MockOllamaController):
        assert mock_ollama.is_available() is True
        raw = mock_ollama.generate("Cluster product names: عرض ال ٦ قطع, عرض ست قطع")
        parsed = json.loads(raw)
        assert parsed["groups"][0]["canonical"] == "عرض 6 قطع"
        assert len(mock_ollama.calls) == 1
        mock_ollama.assert_no_pii_in_prompts()

    def test_sequential_retry_simulation(self, mock_ollama: MockOllamaController):
        mock_ollama.queue_responses("{invalid_json", {"canonical": "عرض 6 قطع", "ok": True})
        with pytest.raises(json.JSONDecodeError):
            mock_ollama.generate_json("attempt 1")
        parsed = mock_ollama.generate_json("attempt 2")
        assert parsed["ok"] is True

    def test_timeout_and_offline_modes(self, mock_ollama: MockOllamaController):
        mock_ollama.set_timeout(True)
        with pytest.raises(TimeoutError, match="timed out"):
            mock_ollama.generate("prompt")

        mock_ollama.set_offline(True)
        assert mock_ollama.is_available() is False
        with pytest.raises(ConnectionError, match="unavailable"):
            mock_ollama.generate("prompt")

    def test_pii_guard_catches_leaked_phone_or_email(self, mock_ollama: MockOllamaController):
        mock_ollama.generate("Customer phone 01012345678 asked for product")
        with pytest.raises(AssertionError, match="Phone PII leaked"):
            mock_ollama.assert_no_pii_in_prompts()


# ---------------------------------------------------------------------------
# Temp Dirs & Non-Skipping Gate (Unit)
# ---------------------------------------------------------------------------
class TestTempDirsAndIntegrationGate:
    """Verify temp fixtures and strict integration-gate failure when DB is down."""

    def test_temp_dirs_created_and_writable(self, temp_log_dir: Path, temp_reports_dir: Path):
        assert temp_log_dir.is_dir()
        assert temp_reports_dir.is_dir()
        sample_report = temp_reports_dir / "dq_test.md"
        sample_report.write_text("# Test Report", encoding="utf-8")
        assert sample_report.read_text(encoding="utf-8") == "# Test Report"

    def test_integration_gate_exits_nonzero_when_db_down(self):
        from _pytest.outcomes import Exit

        fake_cfg = SimpleNamespace(option=SimpleNamespace(markexpr="integration"))
        with (
            patch("tests.conftest._pg_is_healthy", return_value=False),
            patch.dict(os.environ, {"HVIA_REQUIRE_INTEGRATION": "1"}, clear=False),
            pytest.raises(Exit, match="INTEGRATION GATE FAILED"),
        ):
            pytest_collection_modifyitems(fake_cfg, [])


# ---------------------------------------------------------------------------
# Database Rollback Isolation (Integration)
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestDatabaseSessionRollbackIsolation:
    """Verify `db_session` rolls back all writes so test data never persists."""

    def test_db_session_factory_and_engine_active(self, db_engine: Engine, db_session_factory):
        with db_session_factory() as session:
            val = session.execute(text("SELECT 1")).scalar()
            assert val == 1

    def test_db_session_isolates_writes_via_savepoint_rollback(self, db_session: Session):
        from datetime import UTC, datetime

        test_cat = Category(
            id=999_999,
            name="__synthetic_rollback_test_category__",
            description="Synthetic category for rollback verification",
            active=True,
            archived=False,
            created_at=datetime.now(UTC),
        )
        db_session.add(test_cat)
        db_session.commit()  # Commits only to savepoint; outer transaction still holds!

        in_tx = db_session.scalar(
            select(Category).where(Category.name == "__synthetic_rollback_test_category__")
        )
        assert in_tx is not None

        # Separate real session cannot see uncommitted outer transaction changes
        with get_session() as outside_session:
            outside_row = outside_session.scalar(
                select(Category).where(Category.name == "__synthetic_rollback_test_category__")
            )
            assert outside_row is None
