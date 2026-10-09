"""1-based pagination."""

from __future__ import annotations

from collections.abc import Sequence

from .errors import ValidationError

MAX_PAGE_SIZE = 100


def paginate(items: Sequence[object], page: object, size: object) -> tuple[list[object], int]:
    """Return ``(page_items, total_count)`` using offset ``(page - 1) * size``."""
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise ValidationError("items must be a sequence")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValidationError("page must be an integer >= 1")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_PAGE_SIZE:
        raise ValidationError("size must be an integer from 1 through 100")
    offset = (page - 1) * size
    return list(items[offset : offset + size]), len(items)
