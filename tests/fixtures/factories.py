# tests/fixtures/factories.py
"""
Pydantic-validated synthetic data factories for HVIA Task 04 tests.

Security guarantee:
All generators produce strictly synthetic test records with dummy identifiers.
Real production or dump business records must NEVER be hardcoded in test files.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from itertools import count

from pydantic import BaseModel, Field

_seq = count(1)

ARABIC_PRODUCT_VARIANTS: tuple[str, ...] = (
    "عرض ال ٦ قطع",
    "عرض ست قطع",
    "عرض 6 قطع",
)

DIRTY_PHONE_SAMPLES: tuple[str, ...] = (
    "N/A",
    "000",
    "",
    "   ",
    "01000000000",
    "12345",
)


class SyntheticCustomer(BaseModel):
    """Synthetic customer payload for unit and DQ tests."""

    id: int = Field(gt=0)
    name: str = Field(min_length=1)
    phone: str
    email: str
    notes: str = ""
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class SyntheticProduct(BaseModel):
    """Synthetic product catalog payload for unit and DQ tests."""

    id: int = Field(gt=0)
    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category_id: int | None = None
    sale_price: Decimal = Field(ge=Decimal("0.00"))
    cost_price: Decimal = Field(ge=Decimal("0.00"))
    active: bool = True
    archived: bool = False


class SyntheticInvoiceItem(BaseModel):
    """Synthetic invoice line item with derived line total."""

    id: int = Field(gt=0)
    invoice_id: int = Field(gt=0)
    product_id: int | None = None
    product_name: str = Field(min_length=1)
    manual_product: bool = False
    quantity: Decimal = Field(gt=Decimal(0))
    unit_sale_price: Decimal = Field(ge=Decimal("0.00"))
    unit_cost_price: Decimal = Field(ge=Decimal("0.00"))
    line_total: Decimal = Field(ge=Decimal("0.00"))


class SyntheticInvoice(BaseModel):
    """Synthetic invoice payload with line items and reconciled totals."""

    id: int = Field(gt=0)
    number: str = Field(min_length=1)
    customer_id: int = Field(gt=0)
    branch_id: int = 1
    sales_person_id: int = 1
    status: str = "DELIVERED"
    is_draft: bool = False
    subtotal: Decimal
    discount_amount: Decimal = Decimal("0.00")
    shipping_price: Decimal = Decimal("0.00")
    total: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
    payment_status: str = "PAID"
    items: list[SyntheticInvoiceItem] = Field(default_factory=list)
    created_at: datetime


class SyntheticPayment(BaseModel):
    """Synthetic payment payload for reconciliation tests."""

    id: int = Field(gt=0)
    receipt_number: str = Field(min_length=1)
    invoice_id: int = Field(gt=0)
    amount: Decimal = Field(gt=Decimal("0.00"))
    method: str = "cash"
    paid_at: datetime


def make_synthetic_customer(
    *,
    customer_id: int | None = None,
    name: str | None = None,
    phone: str = "01012345678",
    email: str | None = None,
    archived: bool = False,
) -> SyntheticCustomer:
    """Build a valid Pydantic SyntheticCustomer instance."""
    n = customer_id or next(_seq)
    now = datetime.now(UTC)
    return SyntheticCustomer(
        id=n,
        name=name or f"Synthetic_Customer_{n:04d}",
        phone=phone,
        email=email or f"synthetic_{n}@example.invalid",
        notes="",
        archived=archived,
        created_at=now,
        updated_at=now,
    )


def make_synthetic_product(
    *,
    product_id: int | None = None,
    sku: str | None = None,
    name: str | None = None,
    sale_price: Decimal | str = "150.00",
    cost_price: Decimal | str = "90.00",
    category_id: int | None = None,
) -> SyntheticProduct:
    """Build a valid Pydantic SyntheticProduct instance."""
    n = product_id or next(_seq)
    return SyntheticProduct(
        id=n,
        sku=sku or f"SYN-SKU-{n:04d}",
        name=name or f"منتج تجريبي {n}",
        category_id=category_id,
        sale_price=Decimal(str(sale_price)),
        cost_price=Decimal(str(cost_price)),
    )


def make_synthetic_invoice_item(
    *,
    item_id: int | None = None,
    invoice_id: int = 1,
    product_id: int | None = 1,
    product_name: str = "منتج تجريبي",
    manual_product: bool = False,
    quantity: Decimal | str = "2.000",
    unit_sale_price: Decimal | str = "100.00",
    unit_cost_price: Decimal | str = "60.00",
) -> SyntheticInvoiceItem:
    """Build a valid SyntheticInvoiceItem with exact derived `line_total`."""
    n = item_id or next(_seq)
    qty = Decimal(str(quantity))
    price = Decimal(str(unit_sale_price))
    line_total = (qty * price).quantize(Decimal("0.01"))
    return SyntheticInvoiceItem(
        id=n,
        invoice_id=invoice_id,
        product_id=product_id,
        product_name=product_name,
        manual_product=manual_product,
        quantity=qty,
        unit_sale_price=price,
        unit_cost_price=Decimal(str(unit_cost_price)),
        line_total=line_total,
    )


def make_synthetic_invoice(
    *,
    invoice_id: int | None = None,
    customer_id: int = 1,
    items: list[SyntheticInvoiceItem] | None = None,
    discount_amount: Decimal | str = "0.00",
    shipping_price: Decimal | str = "0.00",
    paid_amount: Decimal | str | None = None,
    status: str = "DELIVERED",
) -> SyntheticInvoice:
    """Build an arithmetic-consistent SyntheticInvoice with derived totals."""
    n = invoice_id or next(_seq)
    now = datetime.now(UTC)
    inv_items = items or [make_synthetic_invoice_item(invoice_id=n)]
    subtotal = sum((item.line_total for item in inv_items), Decimal("0.00"))
    disc = Decimal(str(discount_amount))
    ship = Decimal(str(shipping_price))
    total = (subtotal - disc + ship).quantize(Decimal("0.01"))
    paid = total if paid_amount is None else Decimal(str(paid_amount)).quantize(Decimal("0.01"))
    remaining = (total - paid).quantize(Decimal("0.01"))
    if remaining <= Decimal("0.00"):
        pay_status = "PAID"
    elif paid > Decimal("0.00"):
        pay_status = "PARTIAL"
    else:
        pay_status = "UNPAID"

    return SyntheticInvoice(
        id=n,
        number=f"SYN-INV-{n:05d}",
        customer_id=customer_id,
        status=status,
        subtotal=subtotal,
        discount_amount=disc,
        shipping_price=ship,
        total=total,
        paid_amount=paid,
        remaining_amount=remaining,
        payment_status=pay_status,
        items=inv_items,
        created_at=now,
    )


def make_synthetic_payment(
    *,
    payment_id: int | None = None,
    invoice_id: int = 1,
    amount: Decimal | str = "200.00",
    method: str = "cash",
) -> SyntheticPayment:
    """Build a valid Pydantic SyntheticPayment instance."""
    n = payment_id or next(_seq)
    return SyntheticPayment(
        id=n,
        receipt_number=f"SYN-RCPT-{n:05d}",
        invoice_id=invoice_id,
        amount=Decimal(str(amount)),
        method=method,
        paid_at=datetime.now(UTC),
    )
