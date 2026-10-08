"""Page validation and totals. Item identity on page 1 is not asserted here."""

import pytest

from order_service.errors import ValidationError
from order_service.pagination import paginate


def test_out_of_range_page_is_empty_and_keeps_the_total() -> None:
    items, total = paginate(list(range(30)), 9, 10)
    assert items == []
    assert total == 30


def test_middle_page_length() -> None:
    items, total = paginate(list(range(30)), 2, 10)
    assert len(items) == 10
    assert total == 30


@pytest.mark.parametrize(("page", "size"), [(0, 10), (-1, 10), (True, 10), (1, 0), (1, 101), (1, False)])
def test_invalid_page_or_size(page: object, size: object) -> None:
    with pytest.raises(ValidationError):
        paginate([1, 2, 3], page, size)
