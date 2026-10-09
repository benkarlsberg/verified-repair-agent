"""Discount, shipping, coupons, and merchandise totals.

``shipping_cents`` uses the discount it is given. It does not call
``discount_cents``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from .errors import ValidationError
from .validation import validate_cents, validate_quantity

DISCOUNT_THRESHOLD_CENTS = 10_000
FREE_SHIPPING_NET_CENTS = 5_000
SHIPPING_FEE_CENTS = 500


def discount_cents(subtotal: object) -> int:
    """Return 10% of subtotal, rounded down, once subtotal reaches 10000 cents."""
    amount = validate_cents(subtotal, name="subtotal")
    if amount >= DISCOUNT_THRESHOLD_CENTS:
        return amount // 10
    return 0


def shipping_cents(subtotal: object, discount: object) -> int:
    """Return shipping for a supplied discount.

    Free when ``subtotal - discount`` is at least 5000 cents, else 500.
    ``discount`` must satisfy ``0 <= discount <= subtotal``.
    """
    gross = validate_cents(subtotal, name="subtotal")
    supplied = validate_cents(discount, name="discount")
    if supplied > gross:
        raise ValidationError("discount must not exceed subtotal")
    net = gross - supplied
    if net >= FREE_SHIPPING_NET_CENTS:
        return 0
    return SHIPPING_FEE_CENTS


def order_total(items: object) -> int:
    """Sum ``unit_price_cents * quantity``. Does not include shipping or discount."""
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence) or len(items) == 0:
        raise ValidationError("items must be a nonempty list")
    total = 0
    for item in items:
        if not isinstance(item, Mapping):
            raise ValidationError("each item must include unit_price_cents and quantity")
        price = validate_cents(item.get("unit_price_cents"), name="unit_price_cents")
        quantity = validate_quantity(item.get("quantity"))
        total += price * quantity
    return total


def coupon_valid(now: object, expires_at: object) -> bool:
    """True only when ``now`` is strictly before ``expires_at``. Both must be UTC."""
    return _require_utc(now, "now") < _require_utc(expires_at, "expires_at")


def _require_utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"{name} must be a datetime")
    offset = value.utcoffset()
    if value.tzinfo is None or offset != timedelta(0):
        raise ValidationError(f"{name} must be a timezone-aware UTC datetime")
    return value
