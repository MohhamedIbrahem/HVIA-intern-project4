# db/models.py
"""
SQLAlchemy 2.0 declarative ORM models for all 42 PostgreSQL tables and 5 ENUMs.

Design guarantees:
- 1:1 parity with the restored `public` schema (and portable via
  `execution_options(schema_translate_map={None: "raw"})` for read-only `raw` queries).
- All 5 PostgreSQL native ENUM types are mapped with `create_type=False`.
- Every `__repr__` emits strictly non-sensitive identifiers/statuses and NEVER exposes
  passwords, hashes, emails, phone numbers, addresses, or personal names.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# PostgreSQL ENUM Mappings (5 ENUMs)
# ---------------------------------------------------------------------------
class ApprovalStatus(enum.StrEnum):
    """PostgreSQL `approvalstatus` ENUM values."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OrderStatus(enum.StrEnum):
    """PostgreSQL `orderstatus` ENUM values."""

    NEW = "NEW"
    CONFIRMED = "CONFIRMED"
    PREPARING = "PREPARING"
    READY = "READY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class PaymentStatus(enum.StrEnum):
    """PostgreSQL `paymentstatus` ENUM values."""

    UNPAID = "UNPAID"
    PARTIAL = "PARTIAL"
    PAID = "PAID"


class UserRole(enum.StrEnum):
    """PostgreSQL `role` ENUM values."""

    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"
    OWNER = "OWNER"


class ShippingStatus(enum.StrEnum):
    """PostgreSQL `shippingstatus` ENUM values."""

    IN_TRANSIT = "IN_TRANSIT"
    ARRIVED = "ARRIVED"
    DELIVERED = "DELIVERED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"


def _pg_enum(enum_cls: type[enum.StrEnum], pg_name: str) -> SAEnum:
    """Create a non-DDL-creating SQLAlchemy Enum bound to an existing PG ENUM name."""
    return SAEnum(
        enum_cls,
        name=pg_name,
        values_callable=lambda obj: [item.value for item in obj],
        native_enum=True,
        create_type=False,
    )


# ---------------------------------------------------------------------------
# Declarative Base with PII-Safe __repr__
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """Base declarative class with non-PII structured `__repr__`."""

    _repr_fields: ClassVar[tuple[str, ...]] = ("id",)

    def __repr__(self) -> str:
        """Render non-sensitive identifying attributes only."""
        parts: list[str] = []
        for attr in self._repr_fields:
            val = getattr(self, attr, None)
            if isinstance(val, enum.Enum):
                val = val.value
            parts.append(f"{attr}={val!r}")
        joined = ", ".join(parts)
        return f"<{self.__class__.__name__}({joined})>"


# ---------------------------------------------------------------------------
# Organization, Security & Config
# ---------------------------------------------------------------------------
class Branch(Base):
    """ORM model for `branches`."""

    __tablename__ = "branches"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "active", "archived")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    phones: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    users: Mapped[list[User]] = relationship(back_populates="branch")
    sales_people: Mapped[list[SalesPerson]] = relationship(back_populates="branch")
    invoices: Mapped[list[Invoice]] = relationship(back_populates="branch")
    expenses: Mapped[list[Expense]] = relationship(back_populates="branch")
    treasury_additions: Mapped[list[TreasuryAddition]] = relationship(back_populates="branch")
    partner_transactions: Mapped[list[PartnerTransaction]] = relationship(back_populates="branch")
    supplier_purchases: Mapped[list[SupplierPurchase]] = relationship(back_populates="branch")
    inventory_location: Mapped[InventoryLocation | None] = relationship(back_populates="branch")


class User(Base):
    """ORM model for `users` (all accounts disabled in training dump)."""

    __tablename__ = "users"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "role",
        "branch_id",
        "is_active",
        "archived",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(_pg_enum(UserRole, "role"), nullable=False, index=True)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    gmail: Mapped[str] = mapped_column(String(255), nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    profile_completed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    permissions: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    branch: Mapped[Branch | None] = relationship(back_populates="users")
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="user")


class BusinessRule(Base):
    """ORM model for `business_rules`."""

    __tablename__ = "business_rules"
    __table_args__ = (UniqueConstraint("key", name="business_rules_key_key"),)
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "key", "updated_by_id")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(150), nullable=False)
    value: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    updated_by: Mapped[User | None] = relationship(foreign_keys=[updated_by_id])


class SystemSetting(Base):
    """ORM model for `system_settings` (0 rows in dump)."""

    __tablename__ = "system_settings"
    __table_args__ = (UniqueConstraint("key", name="system_settings_key_key"),)
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "key")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(150), nullable=False)
    value: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    """ORM model for `audit_logs`."""

    __tablename__ = "audit_logs"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "user_id",
        "action",
        "entity_type",
        "entity_id",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    details: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    user: Mapped[User | None] = relationship(back_populates="audit_logs", foreign_keys=[user_id])


class ApprovalRequest(Base):
    """ORM model for `approval_requests`."""

    __tablename__ = "approval_requests"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "kind",
        "entity_type",
        "entity_id",
        "status",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    payload: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        _pg_enum(ApprovalStatus, "approvalstatus"), nullable=False, index=True
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requested_by: Mapped[User] = relationship(foreign_keys=[requested_by_id])
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id])


class Notification(Base):
    """ORM model for `notifications`."""

    __tablename__ = "notifications"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "recipient_user_id",
        "kind",
        "entity_type",
        "is_read",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    recipient_role: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    recipient_user: Mapped[User | None] = relationship(foreign_keys=[recipient_user_id])


class BackupRecord(Base):
    """ORM model for `backup_records`."""

    __tablename__ = "backup_records"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "status", "created_by_id")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])


class ScheduledReport(Base):
    """ORM model for `scheduled_reports` (0 rows in dump)."""

    __tablename__ = "scheduled_reports"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "frequency", "channel", "active")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    frequency: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    runs: Mapped[list[ReportRun]] = relationship(back_populates="scheduled_report")


class ReportRun(Base):
    """ORM model for `report_runs` (0 rows in dump)."""

    __tablename__ = "report_runs"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "scheduled_report_id",
        "report_type",
        "status",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheduled_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("scheduled_reports.id"), nullable=True
    )
    report_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    scheduled_report: Mapped[ScheduledReport | None] = relationship(back_populates="runs")


# ---------------------------------------------------------------------------
# Catalog, CRM & Sales Force
# ---------------------------------------------------------------------------
class Category(Base):
    """ORM model for `categories` (0 rows in dump)."""

    __tablename__ = "categories"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "active", "archived")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    products: Mapped[list[Product]] = relationship(back_populates="category")


class Product(Base):
    """ORM model for `products` (catalog prices/costs altered for training)."""

    __tablename__ = "products"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "sku",
        "category_id",
        "active",
        "archived",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    inventory_managed: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    inventory_origin: Mapped[str] = mapped_column(String(50), nullable=False)
    needs_setup: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    low_stock_threshold: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    category: Mapped[Category | None] = relationship(back_populates="products")
    invoice_items: Mapped[list[InvoiceItem]] = relationship(back_populates="product")
    inventory_stocks: Mapped[list[InventoryStock]] = relationship(back_populates="product")
    inventory_movements: Mapped[list[InventoryMovement]] = relationship(back_populates="product")
    inventory_counts: Mapped[list[InventoryCount]] = relationship(back_populates="product")
    inventory_transfers: Mapped[list[InventoryTransfer]] = relationship(back_populates="product")


class Customer(Base):
    """ORM model for `customers` (anonymized in training dump)."""

    __tablename__ = "customers"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "archived")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    addresses: Mapped[list[CustomerAddress]] = relationship(back_populates="customer")
    invoices: Mapped[list[Invoice]] = relationship(back_populates="customer")
    supplier_importer_links: Mapped[list[SupplierImporter]] = relationship(
        back_populates="customer"
    )


class CustomerAddress(Base):
    """ORM model for `customer_addresses`."""

    __tablename__ = "customer_addresses"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "customer_id", "is_default")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="addresses")


class SalesPerson(Base):
    """ORM model for `sales_people`."""

    __tablename__ = "sales_people"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "branch_id", "active", "archived")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    branch: Mapped[Branch | None] = relationship(back_populates="sales_people")
    invoices: Mapped[list[Invoice]] = relationship(back_populates="sales_person")
    sales_targets: Mapped[list[SalesTarget]] = relationship(back_populates="sales_person")


class SalesTarget(Base):
    """ORM model for `sales_targets` (0 rows in dump)."""

    __tablename__ = "sales_targets"
    __table_args__ = (UniqueConstraint("sales_person_id", "month", name="uq_sales_target_month"),)
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "sales_person_id", "month")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_person_id: Mapped[int] = mapped_column(
        ForeignKey("sales_people.id"), nullable=False, index=True
    )
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    sales_person: Mapped[SalesPerson] = relationship(back_populates="sales_targets")


class SupplierImporter(Base):
    """ORM model for `supplier_importers`."""

    __tablename__ = "supplier_importers"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "party_type",
        "customer_id",
        "active",
        "archived",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    party_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    customer: Mapped[Customer | None] = relationship(back_populates="supplier_importer_links")
    invoices: Mapped[list[Invoice]] = relationship(back_populates="supplier")
    supplier_purchases: Mapped[list[SupplierPurchase]] = relationship(back_populates="supplier")
    importer_credits: Mapped[list[ImporterCredit]] = relationship(back_populates="importer")


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
class InventoryLocation(Base):
    """ORM model for `inventory_locations`."""

    __tablename__ = "inventory_locations"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "code",
        "location_type",
        "branch_id",
        "active",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    location_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, unique=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    branch: Mapped[Branch | None] = relationship(back_populates="inventory_location")
    stocks: Mapped[list[InventoryStock]] = relationship(back_populates="location")
    movements: Mapped[list[InventoryMovement]] = relationship(back_populates="location")
    counts: Mapped[list[InventoryCount]] = relationship(back_populates="location")
    invoice_items: Mapped[list[InvoiceItem]] = relationship(back_populates="inventory_location")


class InventoryStock(Base):
    """ORM model for `inventory_stocks` (0 rows in dump)."""

    __tablename__ = "inventory_stocks"
    __table_args__ = (
        UniqueConstraint("product_id", "location_id", name="uq_inventory_product_location"),
    )
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "product_id", "location_id", "quantity")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    location_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    reserved_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    low_stock_threshold: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    product: Mapped[Product] = relationship(back_populates="inventory_stocks")
    location: Mapped[InventoryLocation] = relationship(back_populates="stocks")


class InventoryMovement(Base):
    """ORM model for `inventory_movements` (0 rows in dump)."""

    __tablename__ = "inventory_movements"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "product_id",
        "location_id",
        "movement_type",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    location_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=False, index=True
    )
    movement_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    product: Mapped[Product] = relationship(back_populates="inventory_movements")
    location: Mapped[InventoryLocation] = relationship(back_populates="movements")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])


class InventoryCount(Base):
    """ORM model for `inventory_counts` (0 rows in dump)."""

    __tablename__ = "inventory_counts"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "location_id", "product_id", "variance")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    system_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    physical_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    variance: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    location: Mapped[InventoryLocation] = relationship(back_populates="counts")
    product: Mapped[Product] = relationship(back_populates="inventory_counts")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class InventoryTransfer(Base):
    """ORM model for `inventory_transfers` (0 rows in dump)."""

    __tablename__ = "inventory_transfers"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "number",
        "from_location_id",
        "to_location_id",
        "product_id",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    from_location_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=False, index=True
    )
    to_location_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    from_location: Mapped[InventoryLocation] = relationship(foreign_keys=[from_location_id])
    to_location: Mapped[InventoryLocation] = relationship(foreign_keys=[to_location_id])
    product: Mapped[Product] = relationship(back_populates="inventory_transfers")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


# ---------------------------------------------------------------------------
# Sales & Invoices
# ---------------------------------------------------------------------------
class Invoice(Base):
    """ORM model for `invoices` (historical totals/facts protected)."""

    __tablename__ = "invoices"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "number",
        "status",
        "payment_status",
        "total",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    sales_person_id: Mapped[int] = mapped_column(
        ForeignKey("sales_people.id"), nullable=False, index=True
    )
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_importers.id"), nullable=True, index=True
    )
    is_supplier_importer: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    party_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    status: Mapped[OrderStatus] = mapped_column(
        _pg_enum(OrderStatus, "orderstatus"), nullable=False, index=True
    )
    is_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False)
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    shipping_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    shipping_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_address: Mapped[str] = mapped_column(Text, nullable=False)
    shipping_receiver_name: Mapped[str] = mapped_column(String(255), nullable=False)
    shipping_receiver_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    shipping_notes: Mapped[str] = mapped_column(Text, nullable=False)
    shipping_status: Mapped[ShippingStatus | None] = mapped_column(
        _pg_enum(ShippingStatus, "shippingstatus"), nullable=True, index=True
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        _pg_enum(PaymentStatus, "paymentstatus"), nullable=False, index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_notes: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    cancel_reason: Mapped[str] = mapped_column(Text, nullable=False)
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="invoices")
    branch: Mapped[Branch | None] = relationship(back_populates="invoices")
    sales_person: Mapped[SalesPerson] = relationship(back_populates="invoices")
    supplier: Mapped[SupplierImporter | None] = relationship(back_populates="invoices")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    cancelled_by: Mapped[User | None] = relationship(foreign_keys=[cancelled_by_id])

    items: Mapped[list[InvoiceItem]] = relationship(back_populates="invoice")
    payments: Mapped[list[Payment]] = relationship(back_populates="invoice")
    refunds: Mapped[list[Refund]] = relationship(back_populates="invoice")
    versions: Mapped[list[InvoiceVersion]] = relationship(back_populates="invoice")
    payment_proofs: Mapped[list[PaymentProof]] = relationship(back_populates="invoice")
    manual_product_suggestions: Mapped[list[ManualProductSuggestion]] = relationship(
        back_populates="invoice"
    )


class InvoiceItem(Base):
    """ORM model for `invoice_items` (historical `unit_sale_price` preserved)."""

    __tablename__ = "invoice_items"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "invoice_id",
        "product_id",
        "manual_product",
        "line_total",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    inventory_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=True, index=True
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    manual_product: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_sale_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_override_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    price_review_status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="ok", index=True
    )
    price_review_note: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    price_review_action: Mapped[str] = mapped_column(String(30), nullable=False, server_default="")
    price_review_original_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    price_reviewed_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    invoice: Mapped[Invoice] = relationship(back_populates="items")
    product: Mapped[Product | None] = relationship(back_populates="invoice_items")
    inventory_location: Mapped[InventoryLocation | None] = relationship(
        back_populates="invoice_items"
    )
    manual_suggestions: Mapped[list[ManualProductSuggestion]] = relationship(
        back_populates="invoice_item"
    )


class InvoiceVersion(Base):
    """ORM model for `invoice_versions`."""

    __tablename__ = "invoice_versions"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "invoice_id", "version", "changed_by_id")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="versions")
    changed_by: Mapped[User] = relationship(foreign_keys=[changed_by_id])


class ManualProductSuggestion(Base):
    """ORM model for `manual_product_suggestions`."""

    __tablename__ = "manual_product_suggestions"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "invoice_id",
        "invoice_item_id",
        "status",
        "product_id",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    invoice_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_items.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="manual_product_suggestions")
    invoice_item: Mapped[InvoiceItem | None] = relationship(back_populates="manual_suggestions")
    product: Mapped[Product | None] = relationship(foreign_keys=[product_id])
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id])


# ---------------------------------------------------------------------------
# Payments, Treasury, Expenses & Partners
# ---------------------------------------------------------------------------
class Payment(Base):
    """ORM model for `payments`."""

    __tablename__ = "payments"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "receipt_number",
        "invoice_id",
        "amount",
        "method",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_number: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    invoice: Mapped[Invoice] = relationship(back_populates="payments")
    recorded_by: Mapped[User] = relationship(foreign_keys=[recorded_by_id])
    proofs: Mapped[list[PaymentProof]] = relationship(back_populates="payment")


class PaymentProof(Base):
    """ORM model for `payment_proofs` (0 rows in dump)."""

    __tablename__ = "payment_proofs"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "payment_id",
        "invoice_id",
        "source",
        "reference_code",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id"), nullable=True, index=True
    )
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    payment: Mapped[Payment | None] = relationship(back_populates="proofs")
    invoice: Mapped[Invoice] = relationship(back_populates="payment_proofs")
    uploaded_by: Mapped[User] = relationship(foreign_keys=[uploaded_by_id])


class Refund(Base):
    """ORM model for `refunds` (0 rows in dump)."""

    __tablename__ = "refunds"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "invoice_id", "kind", "amount")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="refunds")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class Expense(Base):
    """ORM model for `expenses`."""

    __tablename__ = "expenses"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "branch_id", "amount", "method")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    method: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    branch: Mapped[Branch | None] = relationship(back_populates="expenses")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class TreasuryAddition(Base):
    """ORM model for `treasury_additions`."""

    __tablename__ = "treasury_additions"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "branch_id", "amount", "method")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    branch: Mapped[Branch | None] = relationship(back_populates="treasury_additions")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


class Partner(Base):
    """ORM model for `partners`."""

    __tablename__ = "partners"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "active", "archived")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    transactions: Mapped[list[PartnerTransaction]] = relationship(back_populates="partner")


class PartnerTransaction(Base):
    """ORM model for `partner_transactions`."""

    __tablename__ = "partner_transactions"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "partner_id",
        "transaction_type",
        "amount",
        "branch_id",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False, index=True)
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    partner: Mapped[Partner] = relationship(back_populates="transactions")
    branch: Mapped[Branch | None] = relationship(back_populates="partner_transactions")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])


# ---------------------------------------------------------------------------
# Supply Purchases & Importer Credits
# ---------------------------------------------------------------------------
class SupplierPurchase(Base):
    """ORM model for `supplier_purchases` (0 rows in dump)."""

    __tablename__ = "supplier_purchases"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "number", "supplier_id", "total")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_importers.id"), nullable=False, index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    branch_id: Mapped[int | None] = mapped_column(
        ForeignKey("branches.id"), nullable=True, index=True
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    supplier: Mapped[SupplierImporter] = relationship(back_populates="supplier_purchases")
    branch: Mapped[Branch | None] = relationship(back_populates="supplier_purchases")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    items: Mapped[list[SupplierPurchaseItem]] = relationship(back_populates="purchase")
    payments: Mapped[list[SupplierPayment]] = relationship(back_populates="purchase")


class SupplierPurchaseItem(Base):
    """ORM model for `supplier_purchase_items` (0 rows in dump)."""

    __tablename__ = "supplier_purchase_items"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "purchase_id", "line_total")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_purchases.id"), nullable=False, index=True
    )
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    purchase: Mapped[SupplierPurchase] = relationship(back_populates="items")


class SupplierPayment(Base):
    """ORM model for `supplier_payments` (0 rows in dump)."""

    __tablename__ = "supplier_payments"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "receipt_number",
        "purchase_id",
        "amount",
        "method",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_number: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    purchase_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_purchases.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    purchase: Mapped[SupplierPurchase] = relationship(back_populates="payments")
    recorded_by: Mapped[User] = relationship(foreign_keys=[recorded_by_id])


class ImporterCredit(Base):
    """ORM model for `importer_credits` (0 rows in dump)."""

    __tablename__ = "importer_credits"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "importer_id", "amount", "remaining_amount")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    importer_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_importers.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    importer: Mapped[SupplierImporter] = relationship(back_populates="importer_credits")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    payments: Mapped[list[ImporterCreditPayment]] = relationship(back_populates="credit")


class ImporterCreditPayment(Base):
    """ORM model for `importer_credit_payments` (0 rows in dump)."""

    __tablename__ = "importer_credit_payments"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "credit_id", "amount", "method")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    credit_id: Mapped[int] = mapped_column(
        ForeignKey("importer_credits.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    credit: Mapped[ImporterCredit] = relationship(back_populates="payments")
    recorded_by: Mapped[User] = relationship(foreign_keys=[recorded_by_id])


# ---------------------------------------------------------------------------
# Nexus AI Chat System Tables
# ---------------------------------------------------------------------------
class NexusSession(Base):
    """ORM model for `nexus_sessions`."""

    __tablename__ = "nexus_sessions"
    _repr_fields: ClassVar[tuple[str, ...]] = (
        "id",
        "owner_user_id",
        "module",
        "record_id",
        "archived",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    context_state: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    running_summary: Mapped[str] = mapped_column(Text, nullable=False)
    summarized_upto_id: Mapped[int] = mapped_column(Integer, nullable=False)
    record_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    owner_user: Mapped[User] = relationship(foreign_keys=[owner_user_id])
    messages: Mapped[list[NexusMessage]] = relationship(back_populates="session")


class NexusMessage(Base):
    """ORM model for `nexus_messages`."""

    __tablename__ = "nexus_messages"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "session_id", "role")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("nexus_sessions.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    session: Mapped[NexusSession] = relationship(back_populates="messages")


class NexusRecord(Base):
    """ORM model for `nexus_records`."""

    __tablename__ = "nexus_records"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "owner_user_id", "kind", "status")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    input: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    output: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    owner_user: Mapped[User] = relationship(foreign_keys=[owner_user_id])


class NexusFile(Base):
    """ORM model for `nexus_files`."""

    __tablename__ = "nexus_files"
    _repr_fields: ClassVar[tuple[str, ...]] = ("id", "owner_user_id", "mime")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    owner_user: Mapped[User] = relationship(foreign_keys=[owner_user_id])


# ---------------------------------------------------------------------------
# Schema Registry
# ---------------------------------------------------------------------------
ALL_MODELS: tuple[type[Base], ...] = (
    ApprovalRequest,
    AuditLog,
    BackupRecord,
    Branch,
    BusinessRule,
    Category,
    Customer,
    CustomerAddress,
    Expense,
    ImporterCredit,
    ImporterCreditPayment,
    InventoryCount,
    InventoryLocation,
    InventoryMovement,
    InventoryStock,
    InventoryTransfer,
    Invoice,
    InvoiceItem,
    InvoiceVersion,
    ManualProductSuggestion,
    NexusFile,
    NexusMessage,
    NexusRecord,
    NexusSession,
    Notification,
    Partner,
    PartnerTransaction,
    Payment,
    PaymentProof,
    Product,
    Refund,
    ReportRun,
    SalesPerson,
    SalesTarget,
    ScheduledReport,
    SupplierImporter,
    SupplierPayment,
    SupplierPurchase,
    SupplierPurchaseItem,
    SystemSetting,
    TreasuryAddition,
    User,
)

MODEL_BY_TABLE: dict[str, type[Base]] = {model.__tablename__: model for model in ALL_MODELS}

EXPECTED_ENUM_MAP: dict[str, type[enum.StrEnum]] = {
    "approvalstatus": ApprovalStatus,
    "orderstatus": OrderStatus,
    "paymentstatus": PaymentStatus,
    "role": UserRole,
    "shippingstatus": ShippingStatus,
}
