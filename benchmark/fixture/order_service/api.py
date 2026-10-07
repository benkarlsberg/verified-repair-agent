"""HTTP fixture. ``X-User-ID`` identifies the caller in local tests only."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .errors import ConflictError, InventoryError, NotFoundError, ValidationError
from .orders import Store, cancel_order, create_order, fresh_store, get_order, list_orders

_STATUS: dict[type[Exception], int] = {
    ValidationError: 422,
    ConflictError: 409,
    InventoryError: 409,
    NotFoundError: 404,
}


class ItemIn(BaseModel):
    product_id: str
    quantity: int


class OrderIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1)
    address: str


def create_app(store: Store | None = None) -> FastAPI:
    """Build the fixture API over ``store``, or a fresh store when omitted."""
    app = FastAPI(title="Order service fixture")
    app.state.store = fresh_store() if store is None else store
    for error_type, status in _STATUS.items():
        app.add_exception_handler(error_type, _error_handler(status))

    @app.post("/orders", status_code=201)
    def post_order(
        body: OrderIn,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        user_id: str = Header(alias="X-User-ID"),
    ) -> dict[str, object]:
        order = create_order(app.state.store, user_id, idempotency_key, body.model_dump())
        return get_order(app.state.store, user_id, order.id)

    @app.get("/orders")
    def get_orders(
        page: int = 1,
        size: int = 20,
        user_id: str = Header(alias="X-User-ID"),
    ) -> dict[str, object]:
        return list_orders(app.state.store, user_id, page, size)

    @app.get("/orders/{order_id}")
    def get_one(
        order_id: str,
        user_id: str = Header(alias="X-User-ID"),
    ) -> dict[str, object]:
        return get_order(app.state.store, user_id, order_id)

    @app.post("/orders/{order_id}/cancel")
    def post_cancel(
        order_id: str,
        user_id: str = Header(alias="X-User-ID"),
    ) -> dict[str, object]:
        cancel_order(app.state.store, user_id, order_id)
        return get_order(app.state.store, user_id, order_id)

    return app


def _error_handler(status: int) -> Callable[[Request, Exception], JSONResponse]:
    def handle(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    return handle
