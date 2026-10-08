"""Public order view. ``payment_token`` is not included."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import ValidationError

PUBLIC_ORDER_FIELDS = (
    "id",
    "user_id",
    "items",
    "subtotal_cents",
    "discount_cents",
    "shipping_cents",
    "total_cents",
    "status",
    "creation_sequence",
)


def serialize_order(order: object) -> dict[str, Any]:
    """Return a fresh dict. Nested item dicts are copies."""
    data = _mapping(order)
    raw_items = data.get("items")
    if not isinstance(raw_items, (list, tuple)):
        raise ValidationError("order items must be a list")
    items = []
    for item in raw_items:
        item_data = _mapping(item)
        items.append(
            {
                "product_id": item_data["product_id"],
                "quantity": item_data["quantity"],
                "unit_price_cents": item_data["unit_price_cents"],
            }
        )
    return {field: items if field == "items" else data[field] for field in PUBLIC_ORDER_FIELDS}


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    raise ValidationError("expected an order or line item")
