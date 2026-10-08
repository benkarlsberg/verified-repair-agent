"""Domain failures. The API maps these to HTTP status codes."""


class OrderServiceError(Exception):
    """Base class for failures the API turns into a response."""


class ValidationError(OrderServiceError):
    """Input does not match the contract. HTTP 422."""


class ConflictError(OrderServiceError):
    """Idempotency key reused with a different payload. HTTP 409."""


class InventoryError(OrderServiceError):
    """Reservation cannot be satisfied. HTTP 409."""


class NotFoundError(OrderServiceError):
    """No matching order for this caller. HTTP 404."""
