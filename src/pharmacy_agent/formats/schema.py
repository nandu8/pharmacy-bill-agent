"""Unified internal schema every vendor format normalizes onto (PRD S10)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LineItem:
    vendor: str
    invoice_no: str
    invoice_date: str  # ISO 8601, YYYY-MM-DD
    item_name: str
    batch_no: str
    expiry_date: str  # ISO 8601, YYYY-MM-DD (day defaulted to 01 when the
    # source only carries month/year, e.g. "MM/YY")
    quantity: float
    rate: float
    discount: float
    taxable_value: float
    tax_component_1_label: str
    tax_component_1_rate: float
    tax_component_1_amount: float
    tax_component_2_label: str
    tax_component_2_rate: float
    tax_component_2_amount: float
    mrp: float
    line_total: float
    hsn_code: str
    source_format: str  # "format_a" | "format_b" | "format_c"


@dataclass
class Bill:
    vendor: str
    invoice_no: str
    invoice_date: str
    source_format: str
    line_items: list[LineItem] = field(default_factory=list)
    total_amount: float | None = None  # footer/header total as declared by the source, if any
