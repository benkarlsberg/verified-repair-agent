"""Routine pricing. Shipping cases here use a supplied discount."""

from datetime import datetime, timedelta, timezone

import pytest

from order_service.errors import ValidationError
from order_service.pricing import coupon_valid, discount_cents, order_total, shipping_cents


@pytest.mark.parametrize(
    ("subtotal", "expected"),
    [(0, 0), (1, 0), (9_999, 0), (10_001, 1_000), (10_010, 1_001), (50_000, 5_000)],
)
def test_discount_away_from_the_threshold_edge(subtotal: int, expected: int) -> None:
    assert discount_cents(subtotal) == expected


@pytest.mark.parametrize("value", [-1, True, 1.5, "10000", None])
def test_discount_rejects_invalid_subtotals(value: object) -> None:
    with pytest.raises(ValidationError):
        discount_cents(value)


@pytest.mark.parametrize(
    ("subtotal", "discount", "expected"),
    [
        (0, 0, 500),
        (4_999, 0, 500),
        (5_000, 0, 0),
        (6_000, 0, 0),
        (6_000, 500, 0),
        (1_000, 1_000, 500),
        (4_000, 1_000, 500),
    ],
)
def test_shipping_with_a_supplied_discount(subtotal: int, discount: int, expected: int) -> None:
    assert shipping_cents(subtotal, discount) == expected


def test_shipping_rejects_discount_above_subtotal_and_invalid_types() -> None:
    with pytest.raises(ValidationError):
        shipping_cents(1_000, 1_001)
    for value in (-1, True, 1.5):
        with pytest.raises(ValidationError):
            shipping_cents(value, 0)
        with pytest.raises(ValidationError):
            shipping_cents(1_000, value)


def test_order_total_sums_single_quantity_lines() -> None:
    assert order_total([{"unit_price_cents": 1_200, "quantity": 1}]) == 1_200
    assert (
        order_total(
            [
                {"unit_price_cents": 1_200, "quantity": 1},
                {"unit_price_cents": 2_500, "quantity": 1},
            ]
        )
        == 3_700
    )


def test_order_total_rejects_empty_and_bad_lines() -> None:
    with pytest.raises(ValidationError):
        order_total([])
    with pytest.raises(ValidationError):
        order_total([{"unit_price_cents": -1, "quantity": 1}])
    with pytest.raises(ValidationError):
        order_total([{"unit_price_cents": True, "quantity": 1}])
    with pytest.raises(ValidationError):
        order_total([{"unit_price_cents": 100, "quantity": -1}])


def test_coupon_before_and_after_expiry() -> None:
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    later = start + timedelta(days=1)
    assert coupon_valid(start, later) is True
    assert coupon_valid(later, start) is False


def test_coupon_requires_utc_datetimes() -> None:
    aware = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for value in (datetime(2026, 6, 1), aware.astimezone(timezone(timedelta(hours=1))), "2026-06-01"):
        with pytest.raises(ValidationError):
            coupon_valid(value, aware)
        with pytest.raises(ValidationError):
            coupon_valid(aware, value)
