"""Public fields are present and nested containers are copies."""

from order_service.orders import create_order, fresh_store
from order_service.serialization import PUBLIC_ORDER_FIELDS, serialize_order


def test_public_fields_are_present_and_items_are_copies() -> None:
    store = fresh_store()
    order = create_order(
        store,
        "ada",
        "key-1",
        {"items": [{"product_id": "sku-mug", "quantity": 1}], "address": "1 Main St"},
    )
    body = serialize_order(order)
    for field in PUBLIC_ORDER_FIELDS:
        assert field in body
    body["items"][0]["quantity"] = 99
    body["items"].append({"product_id": "extra", "quantity": 1, "unit_price_cents": 1})
    assert order.items[0].quantity == 1
    assert len(order.items) == 1
    assert body["items"][0]["product_id"] == "sku-mug"
    assert body["subtotal_cents"] == 1_200
    assert body["total_cents"] == 1_700
    assert body["status"] == "open"
