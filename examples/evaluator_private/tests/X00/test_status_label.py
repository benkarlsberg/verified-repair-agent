"""Example protected checks for X00. Not a benchmark oracle."""

from order_service.example_ops import status_label


def test_cancelled_label_is_spelled_with_two_ls() -> None:
    assert status_label("cancelled") == "Cancelled"


def test_open_label_stays_open() -> None:
    assert status_label("open") == "Open"
