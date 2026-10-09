"""Keep the suite off the network and off a real model client."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _block_real_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tests must not construct the real OpenAI client")

    monkeypatch.setattr("openai.OpenAI", _blocked)
