"""Quantity, cent, and address checks."""

from __future__ import annotations

from .errors import ValidationError

MIN_QUANTITY = 1
MAX_QUANTITY = 100


def validate_quantity(value: object) -> int:
    """Accept an integer from 1 through 100. Booleans are rejected."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("quantity must be an integer")
    if not MIN_QUANTITY <= value <= MAX_QUANTITY:
        raise ValidationError("quantity must be from 1 through 100")
    return value


def validate_cents(value: object, *, name: str) -> int:
    """Accept a nonnegative integer number of cents."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer number of cents")
    if value < 0:
        raise ValidationError(f"{name} must be nonnegative")
    return value


def validate_address(value: object) -> str:
    """Trim an address and reject a blank result."""
    if not isinstance(value, str):
        raise ValidationError("address must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValidationError("address must not be empty")
    return normalized


def validate_token(value: object, *, name: str) -> str:
    """Return a nonempty string identifier."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a nonempty string")
    return value.strip()
