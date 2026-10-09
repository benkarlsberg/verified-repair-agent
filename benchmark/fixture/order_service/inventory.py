"""Atomic stock reservation."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence

from .errors import InventoryError, ValidationError
from .validation import validate_quantity


def reserve_stock(stock: MutableMapping[str, int], items: Sequence[Mapping[str, object]]) -> None:
    """Aggregate by product, check every balance, then decrement."""
    if not isinstance(stock, MutableMapping):
        raise ValidationError("stock must be a mapping")
    needed = _aggregate(items)
    for product_id, quantity in needed.items():
        _require_available(stock, product_id, quantity)
    for product_id, quantity in needed.items():
        stock[product_id] -= quantity


def _aggregate(items: Sequence[Mapping[str, object]]) -> dict[str, int]:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence) or len(items) == 0:
        raise ValidationError("items must be a nonempty list")
    needed: dict[str, int] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ValidationError("each item must include product_id and quantity")
        product_id = item.get("product_id")
        if not isinstance(product_id, str) or not product_id:
            raise ValidationError("product_id must be a nonempty string")
        quantity = validate_quantity(item.get("quantity"))
        needed[product_id] = needed.get(product_id, 0) + quantity
    for quantity in needed.values():
        validate_quantity(quantity)
    return needed


def _require_available(stock: Mapping[str, int], product_id: str, quantity: int) -> None:
    available = stock.get(product_id)
    if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
        raise InventoryError(f"insufficient stock for {product_id}")
