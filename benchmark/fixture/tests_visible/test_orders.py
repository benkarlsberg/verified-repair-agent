"""Routine order flow on a fresh store for each test."""

from datetime import datetime, timezone

import pytest

from order_service.errors import InventoryError, NotFoundError, ValidationError
from order_service.orders import FixedClock, cancel_order, create_order, fresh_store, get_order, list_orders


def _mug(address: str = "1 Main St") -> dict[str, object]:
    return {"items": [{"product_id": "sku-mug", "quantity": 1}], "address": address}


def test_create_prices_one_mug_and_stamps_the_injected_clock() -> None:
    instant = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
    store = fresh_store(clock=FixedClock(instant))
    order = create_order(store, "ada", "key-1", _mug("  1 Main St  "))
    assert order.id == "ord-1"
    assert order.creation_sequence == 1
    assert order.address == "1 Main St"
    assert order.created_at == instant
    assert order.items[0].unit_price_cents == 1_200
    assert order.subtotal_cents == 1_200
    assert order.discount_cents == 0
    assert order.shipping_cents == 500
    assert order.total_cents == 1_700
    assert store.stock["sku-mug"] == 9
    assert order.payment_token == "tok_fixture"


def test_items_are_aggregated_and_sorted_by_product_id() -> None:
    store = fresh_store()
    order = create_order(
        store,
        "ada",
        "key-1",
        {
            "items": [
                {"product_id": "sku-mug", "quantity": 1},
                {"product_id": "sku-book", "quantity": 1},
                {"product_id": "sku-mug", "quantity": 1},
            ],
            "address": "1 Main St",
        },
    )
    assert [(item.product_id, item.quantity) for item in order.items] == [
        ("sku-book", 1),
        ("sku-mug", 2),
    ]
    assert store.stock["sku-book"] == 9
    assert store.stock["sku-mug"] == 8


def test_two_keys_create_two_orders() -> None:
    store = fresh_store()
    first = create_order(store, "ada", "key-1", _mug())
    second = create_order(store, "ada", "key-2", _mug())
    assert first.id != second.id
    assert len(store.orders) == 2
    assert store.stock["sku-mug"] == 8


def test_same_key_is_scoped_to_the_user() -> None:
    store = fresh_store()
    ada = create_order(store, "ada", "shared", _mug())
    grace = create_order(store, "grace", "shared", _mug())
    assert ada.id != grace.id


def test_unknown_product_and_blank_user_are_rejected() -> None:
    store = fresh_store()
    payload = {"items": [{"product_id": "sku-missing", "quantity": 1}], "address": "1 Main St"}
    with pytest.raises(ValidationError):
        create_order(store, "ada", "key-1", payload)
    with pytest.raises(ValidationError):
        create_order(store, "  ", "key-1", _mug())
    assert store.orders == {}
    assert store.stock["sku-mug"] == 10


def test_failed_create_does_not_reserve_stock() -> None:
    store = fresh_store(stock={"sku-mug": 0, "sku-book": 10, "sku-lamp": 5})
    with pytest.raises(InventoryError):
        create_order(store, "ada", "key-1", _mug())
    assert store.stock["sku-mug"] == 0
    assert store.orders == {}


def test_cancel_once_restores_stock_and_marks_status() -> None:
    store = fresh_store()
    order = create_order(store, "ada", "key-1", _mug())
    other = create_order(store, "grace", "key-9", _mug())
    cancelled = cancel_order(store, "ada", order.id)
    assert cancelled.status == "cancelled"
    assert store.stock["sku-mug"] == 9
    assert get_order(store, "grace", other.id)["status"] == "open"


def test_missing_order_raises_not_found() -> None:
    store = fresh_store()
    with pytest.raises(NotFoundError):
        get_order(store, "ada", "ord-missing")


def test_list_reports_total_count_and_rejects_bad_pages() -> None:
    store = fresh_store()
    for index in range(3):
        create_order(store, "ada", f"key-{index}", _mug())
    listed = list_orders(store, "ada", 1, 20)
    assert listed["total_count"] == 3
    assert listed["page"] == 1
    assert listed["size"] == 20
    far = list_orders(store, "ada", 5, 20)
    assert far["items"] == []
    assert far["total_count"] == 3
    empty = list_orders(store, "grace", 1, 20)
    assert empty == {"items": [], "total_count": 0, "page": 1, "size": 20}
    for page, size in ((0, 20), (1, 0), (1, 101), (True, 20)):
        with pytest.raises(ValidationError):
            list_orders(store, "ada", page, size)
