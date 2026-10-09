# Order service contract

Amounts are integer cents. Quantities are integers from 1 to 100. Time values are timezone-aware UTC datetimes. The service keeps state in memory and reads no clock, network, or randomness except values the caller passes in.

`validate_quantity(value)` accepts an integer from 1 to 100 and rejects booleans and every other type. It raises `ValidationError`.

`discount_cents(subtotal)` returns `subtotal // 10` when `subtotal` is at least 10000, and 0 otherwise. The input is a nonnegative integer number of cents.

`shipping_cents(subtotal, discount)` uses the discount it is given. It does not compute the discount itself. `discount` must satisfy `0 <= discount <= subtotal`. Shipping is 0 when `subtotal - discount` is at least 5000, and 500 otherwise.

`order_total(items)` sums `unit_price_cents * quantity` for a nonempty item list. It validates quantities and nonnegative integer prices. It does not add shipping or subtract a discount.

`coupon_valid(now, expires_at)` is true only when both values are timezone-aware UTC datetimes and `now < expires_at`.

`reserve_stock(stock, items)` adds up quantities by product, checks every balance, and only then mutates stock. Insufficient stock raises `InventoryError` and leaves every balance unchanged.

`create_order(store, user_id, key, payload)` checks catalog products, quantities, and a nonempty address. The same user, idempotency key, and canonical payload returns the original order. A different canonical payload for that key raises `ConflictError`. Stock is reserved once. Address matching trims the ends. Canonical items are aggregated by product and sorted by id.

`cancel_order(store, user_id, order_id)` is owner-only. The first cancel sets the status to cancelled and releases reserved stock once. Cancelling again returns the same order and does not release stock again. An unknown id or another user's order raises `NotFoundError`.

`list_orders(store, user_id, page, size)` uses a 1-based page and `size` from 1 to 100. It returns that user's orders sorted by creation sequence, plus `total_count`. The offset is `(page - 1) * size`. A page past the end has an empty item list. Invalid page or size is rejected.

`get_order(store, user_id, order_id)` returns the owner's order or raises `NotFoundError`. `serialize_order` omits `payment_token` and returns fresh nested containers.

An order total is `subtotal - discount + shipping`. HTTP mapping for this fixture: `ValidationError` is 422, `ConflictError` and `InventoryError` are 409, and `NotFoundError` is 404. `X-User-ID` identifies the caller in this local fixture.
