# Retrying a checkout creates a second order

Submitting the same order again with the same idempotency key should return the original order. A retry created a new order and reserved stock again.

Example: user ada sends idempotency key "pay-1" for one mug, addressed to "1 Main St". The first response is order A. Sending that same request again should return order A, the order count should stay 1, and only one mug should be reserved. A different idempotency key should still create a different order.
