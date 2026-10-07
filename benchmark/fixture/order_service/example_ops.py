"""Display labels for example case X00. Not a benchmark defect."""

from __future__ import annotations

from .errors import ValidationError

_LABELS = {
    "open": "Open",
    "cancelled": "Cancelled",
}


def status_label(status: object) -> str:
    """Return the display label for a known order status."""
    if not isinstance(status, str):
        raise ValidationError("status must be a string")
    try:
        return _LABELS[status]
    except KeyError as exc:
        raise ValidationError("unknown status") from exc
