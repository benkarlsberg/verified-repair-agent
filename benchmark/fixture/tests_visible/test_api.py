"""HTTP mapping for the local fixture headers."""

from fastapi.testclient import TestClient

from order_service.api import create_app
from order_service.orders import fresh_store


def _client(stock: dict[str, int] | None = None) -> TestClient:
    store = fresh_store(stock=stock) if stock is not None else fresh_store()
    return TestClient(create_app(store))


def test_create_get_and_cancel_round_trip() -> None:
    client = _client()
    headers = {"X-User-ID": "ada", "Idempotency-Key": "key-1"}
    created = client.post(
        "/orders",
        headers=headers,
        json={"items": [{"product_id": "sku-mug", "quantity": 1}], "address": " 1 Main St "},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == "ord-1"
    assert body["total_cents"] == 1_700
    assert body["status"] == "open"
    fetched = client.get("/orders/ord-1", headers={"X-User-ID": "ada"})
    assert fetched.status_code == 200
    assert fetched.json()["id"] == "ord-1"
    listed = client.get("/orders", headers={"X-User-ID": "ada"})
    assert listed.status_code == 200
    assert listed.json()["total_count"] == 1
    cancelled = client.post("/orders/ord-1/cancel", headers={"X-User-ID": "ada"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_status_codes_for_missing_input_unknown_product_and_stock() -> None:
    client = _client(stock={"sku-mug": 0, "sku-book": 1, "sku-lamp": 1})
    missing_header = client.post(
        "/orders",
        headers={"X-User-ID": "ada"},
        json={"items": [{"product_id": "sku-mug", "quantity": 1}], "address": "1 Main St"},
    )
    assert missing_header.status_code == 422
    unknown = client.post(
        "/orders",
        headers={"X-User-ID": "ada", "Idempotency-Key": "key-1"},
        json={"items": [{"product_id": "nope", "quantity": 1}], "address": "1 Main St"},
    )
    assert unknown.status_code == 422
    shortage = client.post(
        "/orders",
        headers={"X-User-ID": "ada", "Idempotency-Key": "key-2"},
        json={"items": [{"product_id": "sku-mug", "quantity": 1}], "address": "1 Main St"},
    )
    assert shortage.status_code == 409
    missing = client.get("/orders/ord-nope", headers={"X-User-ID": "ada"})
    assert missing.status_code == 404
