"""Routine quantity, money, and address checks."""

import pytest

from order_service.errors import ValidationError
from order_service.validation import validate_address, validate_cents, validate_quantity


@pytest.mark.parametrize("value", [1, 2, 20, 100])
def test_accepts_ordinary_quantities(value: int) -> None:
    assert validate_quantity(value) == value


@pytest.mark.parametrize("value", [-1, 101, True, False, 1.5, "2", None])
def test_rejects_non_quantities(value: object) -> None:
    with pytest.raises(ValidationError):
        validate_quantity(value)


def test_cents_reject_bools_floats_and_negatives() -> None:
    assert validate_cents(0, name="amount") == 0
    assert validate_cents(1200, name="amount") == 1200
    for value in (True, 1.5, -1, "10"):
        with pytest.raises(ValidationError):
            validate_cents(value, name="amount")


def test_address_is_trimmed_and_blank_is_rejected() -> None:
    assert validate_address("  1 Main St  ") == "1 Main St"
    for value in ("", "   ", None, 12):
        with pytest.raises(ValidationError):
            validate_address(value)
