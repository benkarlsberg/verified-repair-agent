"""Create, fetch, list, and cancel orders against an in-memory store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .errors import ConflictError, NotFoundError, ValidationError
from .inventory import reserve_stock
from .pagination import paginate
from .pricing import discount_cents, order_total, shipping_cents
from .serialization import serialize_order
from .validation import validate_address, validate_quantity, validate_token

FIXTURE_PAYMENT_TOKEN = "tok_fixture"
CATALOG = {
    "sku-mug": 1_200,
    "sku-book": 2_500,
    "sku-lamp": 8_000,
}
DEFAULT_STOCK = {"sku-mug": 10, "sku-book": 10, "sku-lamp": 5}


class FixedClock:
    """Always returns the UTC instant it was given."""

    def __init__(self, instant: datetime) -> None:
        offset = instant.utcoffset() if isinstance(instant, datetime) else None
        if not isinstance(instant, datetime) or instant.tzinfo is None or offset != timedelta(0):
            raise ValidationError("clock instant must be a timezone-aware UTC datetime")
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


@dataclass(frozen=True)
class LineItem:
    product_id: str
    quantity: int
    unit_price_cents: int


@dataclass
class Order:
    id: str
    user_id: str
    items: list[LineItem]
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    total_cents: int
    status: str
    creation_sequence: int
    payment_token: str
    address: str
    created_at: datetime


@dataclass(frozen=True)
class _IdempotencyRecord:
    canonical: tuple[Any, ...]
    order_id: str


class Store:
    """In-memory catalog, stock, orders, and idempotency records."""

    def __init__(
        self,
        *,
        catalog: dict[str, int] | None = None,
        stock: dict[str, int] | None = None,
        clock: FixedClock | None = None,
    ) -> None:
        self.catalog = dict(CATALOG if catalog is None else catalog)
        self.stock = dict(DEFAULT_STOCK if stock is None else stock)
        self.clock = clock or FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.orders: dict[str, Order] = {}
        self._idempotency: dict[tuple[str, str], _IdempotencyRecord] = {}
        self._sequence = 0


def fresh_store(**kwargs: Any) -> Store:
    """Return a store with the sample catalog, stock, and a fixed clock."""
    return Store(**kwargs)


def canonical_payload(payload: object) -> tuple[str, tuple[tuple[str, int], ...]]:
    """Trim the address, aggregate items by product, and sort by product id."""
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")
    address = validate_address(payload.get("address"))
    raw_items = payload.get("items")
    if isinstance(raw_items, (str, bytes)) or not isinstance(raw_items, list) or len(raw_items) == 0:
        raise ValidationError("items must be a nonempty list")
    aggregated: dict[str, int] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValidationError("each item must include product_id and quantity")
        product_id = item.get("product_id")
        if not isinstance(product_id, str) or not product_id:
            raise ValidationError("product_id must be a nonempty string")
        aggregated[product_id] = aggregated.get(product_id, 0) + validate_quantity(item.get("quantity"))
    for quantity in aggregated.values():
        validate_quantity(quantity)
    items = tuple((product_id, aggregated[product_id]) for product_id in sorted(aggregated))
    return address, items


def create_order(store: Store, user_id: object, key: object, payload: object) -> Order:
    """Reserve stock once. The same user, key, and payload return the original order."""
    user = validate_token(user_id, name="user_id")
    idempotency_key = validate_token(key, name="idempotency key")
    canonical = canonical_payload(payload)
    existing = store._idempotency.get((user, idempotency_key))
    if existing is not None:
        if existing.canonical != canonical:
            raise ConflictError("idempotency key reused with a different payload")
        return store.orders[existing.order_id]

    lines = []
    priced: list[dict[str, int]] = []
    for product_id, quantity in canonical[1]:
        price = store.catalog.get(product_id)
        if isinstance(price, bool) or not isinstance(price, int):
            raise ValidationError(f"unknown product {product_id}")
        lines.append(LineItem(product_id, quantity, price))
        priced.append({"unit_price_cents": price, "quantity": quantity})
    subtotal = order_total(priced)
    discount = discount_cents(subtotal)
    shipping = shipping_cents(subtotal, discount)
    reserve_stock(
        store.stock,
        [{"product_id": line.product_id, "quantity": line.quantity} for line in lines],
    )
    store._sequence += 1
    order = Order(
        id=f"ord-{store._sequence}",
        user_id=user,
        items=lines,
        subtotal_cents=subtotal,
        discount_cents=discount,
        shipping_cents=shipping,
        total_cents=subtotal - discount + shipping,
        status="open",
        creation_sequence=store._sequence,
        payment_token=FIXTURE_PAYMENT_TOKEN,
        address=canonical[0],
        created_at=store.clock.now(),
    )
    store.orders[order.id] = order
    store._idempotency[(user, idempotency_key)] = _IdempotencyRecord(canonical, order.id)
    return order


def cancel_order(store: Store, user_id: object, order_id: object) -> Order:
    """Cancel an owned order and release its stock once. A repeat is a no-op."""
    order = _owned_order(store, user_id, order_id)
    if order.status == "cancelled":
        return order
    for line in order.items:
        store.stock[line.product_id] = store.stock.get(line.product_id, 0) + line.quantity
    order.status = "cancelled"
    return order


def list_orders(store: Store, user_id: object, page: object, size: object) -> dict[str, Any]:
    """Return the caller's orders ordered by creation sequence."""
    user = validate_token(user_id, name="user_id")
    owned = sorted(
        (order for order in store.orders.values() if order.user_id == user),
        key=lambda order: order.creation_sequence,
    )
    page_items, total = paginate(owned, page, size)
    return {
        "items": [serialize_order(order) for order in page_items],
        "total_count": total,
        "page": page,
        "size": size,
    }


def get_order(store: Store, user_id: object, order_id: object) -> dict[str, Any]:
    """Return the public view of an order the caller owns."""
    return serialize_order(_owned_order(store, user_id, order_id))


def _owned_order(store: Store, user_id: object, order_id: object) -> Order:
    user = validate_token(user_id, name="user_id")
    if not isinstance(order_id, str) or not order_id:
        raise NotFoundError("order not found")
    order = store.orders.get(order_id)
    if order is None or order.user_id != user:
        raise NotFoundError("order not found")
    return order
