# tests/fixtures/__init__.py
"""Shared synthetic data factories and offline mock helpers for tests."""

from tests.fixtures.factories import (
    ARABIC_PRODUCT_VARIANTS,
    DIRTY_PHONE_SAMPLES,
    SyntheticCustomer,
    SyntheticInvoice,
    SyntheticInvoiceItem,
    SyntheticPayment,
    SyntheticProduct,
    make_synthetic_customer,
    make_synthetic_invoice,
    make_synthetic_invoice_item,
    make_synthetic_payment,
    make_synthetic_product,
)
from tests.fixtures.ollama_mock import MockOllamaController

__all__ = [
    "ARABIC_PRODUCT_VARIANTS",
    "DIRTY_PHONE_SAMPLES",
    "MockOllamaController",
    "SyntheticCustomer",
    "SyntheticInvoice",
    "SyntheticInvoiceItem",
    "SyntheticPayment",
    "SyntheticProduct",
    "make_synthetic_customer",
    "make_synthetic_invoice",
    "make_synthetic_invoice_item",
    "make_synthetic_payment",
    "make_synthetic_product",
]
