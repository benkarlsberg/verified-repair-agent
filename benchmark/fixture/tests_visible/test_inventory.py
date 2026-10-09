"""Routine reservations, including a single-item miss and a full success."""

import pytest

from order_service.errors import InventoryError, ValidationError
from order_service.inventory import reserve_stock


def test_reserve_decrements_each_requested_product() -> None:
    stock = {"sku-mug": 4, "sku-book": 2}
    reserve_stock(
        stock,
        [
            {"product_id": "sku-mug", "quantity": 1},
            {"product_id": "sku-book", "quantity": 2},
        ],
    )
    assert stock == {"sku-mug": 3, "sku-book": 0}


def test_duplicate_lines_for_one_product_are_summed() -> None:
    stock = {"sku-mug": 5}
    reserve_stock(
        stock,
        [
            {"product_id": "sku-mug", "quantity": 2},
            {"product_id": "sku-mug", "quantity": 1},
        ],
    )
    assert stock["sku-mug"] == 2


def test_exact_single_item_stock_is_accepted() -> None:
    stock = {"sku-mug": 2}
    reserve_stock(stock, [{"product_id": "sku-mug", "quantity": 2}])
    assert stock["sku-mug"] == 0


def test_single_item_miss_leaves_stock_unchanged() -> None:
    stock = {"sku-mug": 1}
    with pytest.raises(InventoryError):
        reserve_stock(stock, [{"product_id": "sku-mug", "quantity": 2}])
    assert stock == {"sku-mug": 1}


def test_unknown_product_leaves_other_stock_unchanged() -> None:
    stock = {"sku-mug": 3}
    with pytest.raises(InventoryError):
        reserve_stock(stock, [{"product_id": "sku-missing", "quantity": 1}])
    assert stock == {"sku-mug": 3}


def test_invalid_reservation_input() -> None:
    stock = {"sku-mug": 3}
    with pytest.raises(ValidationError):
        reserve_stock(stock, [])
    with pytest.raises(ValidationError):
        reserve_stock(stock, [{"product_id": "sku-mug", "quantity": True}])
    assert stock == {"sku-mug": 3}
