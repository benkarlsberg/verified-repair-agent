"""Routine labels. X00 is the only case that targets this helper."""

import pytest

from order_service.errors import ValidationError
from order_service.example_ops import status_label


def test_open_label() -> None:
    assert status_label("open") == "Open"


@pytest.mark.parametrize("value", ["", "shipped", None, 1])
def test_unknown_status(value: object) -> None:
    with pytest.raises(ValidationError):
        status_label(value)
