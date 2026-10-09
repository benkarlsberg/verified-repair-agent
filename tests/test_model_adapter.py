"""Usage, cost, retries, and the refusal to swap models. No network."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from repair_agent.config import load_defaults
from repair_agent.model import (
    MissingAPIKeyError,
    ModelIdentityError,
    ModelProviderError,
    ModelTransportError,
    OpenAIModel,
    estimate_cost,
    one_shot_text_format,
    redact_items,
)


class _Responses:
    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responder(kwargs)


class _Client:
    def __init__(self, responder: Any) -> None:
        self.responses = _Responses(responder)


def _usage(**overrides: Any) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "input_tokens": 1_000,
        "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 40},
    }
    usage.update(overrides)
    return usage


def _ok(model_id: str, *, usage: dict[str, Any] | None = None, output: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "model": model_id,
        "output": output
        or [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "{\"claim\":\"unresolved\"}"}],
            }
        ],
        "usage": _usage() if usage is None else usage,
    }


def test_defaults_pin_the_dated_snapshot() -> None:
    defaults = load_defaults()
    assert defaults.model.model_id == "gpt-5.4-mini-2026-03-17"
    assert defaults.model.alias == "gpt-5.4-mini"
    assert defaults.model.reasoning_effort == "low"
    assert defaults.model.temperature is None
    assert defaults.model.top_p is None
    assert defaults.pricing.input_per_million == 0.75
    assert defaults.pricing.cached_input_per_million == 0.075
    assert defaults.pricing.output_per_million == 4.5
    assert defaults.budgets.max_total_tokens == 160_000
    assert defaults.budgets.note is not None
    assert "provisional" in defaults.budgets.note
    assert "final freeze" in defaults.budgets.note


def test_missing_key_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
        OpenAIModel.from_env(model_id="gpt-5.4-mini-2026-03-17", reasoning_effort="low", timeout_seconds=5)


def test_request_uses_the_pinned_model_and_does_not_send_temperature() -> None:
    model_id = "gpt-5.4-mini-2026-03-17"
    client = _Client(lambda _kwargs: _ok(model_id))
    model = OpenAIModel(
        model_id=model_id,
        api_key="sk-test",
        reasoning_effort="low",
        timeout_seconds=5,
        store=False,
        client=client,
        sleep=lambda _seconds: None,
    )
    turn = model.complete(
        [{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "name": "list_files"}],
        max_output_tokens=4000,
    )
    sent = client.responses.calls[0]
    assert sent["model"] == model_id
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["max_output_tokens"] == 4000
    assert sent["store"] is False
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert sent["tools"][0]["name"] == "list_files"
    assert turn.usage.input_tokens == 1000
    assert turn.usage.output_tokens == 100
    assert turn.usage.reasoning_tokens == 40
    assert turn.model_id == model_id


def test_recorded_output_is_replayed_without_output_only_fields() -> None:
    """A recorded Responses payload must not be sent back with output-only fields.

    Usage stays the provider's usage object. Stripping ``status``, ``id``, and
    reasoning items does not shrink or invent the billed token counts.
    """
    model_id = "gpt-5.4-mini-2026-03-17"
    secret = "SECRET"
    recorded = [
        {
            "id": "fc_123",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_abc",
            "name": "list_files",
            "arguments": "{}",
        },
        {
            "id": "msg_123",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "note", "annotations": []}],
        },
        {
            "id": "rs_123",
            "type": "reasoning",
            "status": "completed",
            "encrypted_content": secret,
            "summary": [],
        },
    ]
    usage = _usage(
        input_tokens=10_000,
        output_tokens=500,
        input_tokens_details={"cached_tokens": 8_500},
        output_tokens_details={"reasoning_tokens": 400},
    )
    calls = {"n": 0}

    def responder(_kwargs: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok(model_id, usage=usage, output=recorded)
        return _ok(model_id, usage=_usage(input_tokens=12, output_tokens=3))

    client = _Client(responder)
    model = OpenAIModel(
        model_id=model_id,
        api_key="sk-test",
        reasoning_effort="low",
        timeout_seconds=5,
        client=client,
        sleep=lambda _seconds: None,
    )
    first = [{"role": "user", "content": "hello"}]
    turn = model.complete(first, tools=[{"type": "function", "name": "list_files"}], max_output_tokens=4000)
    assert turn.usage.input_tokens == 10_000
    assert turn.usage.cached_input_tokens == 8_500
    assert turn.usage.output_tokens == 500
    assert turn.usage.reasoning_tokens == 400
    assert turn.usage.total_tokens == 10_500
    model.complete(first + turn.replay_items, tools=[{"type": "function", "name": "list_files"}], max_output_tokens=4000)
    sent = client.responses.calls[1]["input"]
    blob = str(sent)
    assert secret not in blob
    assert "status" not in blob
    assert "encrypted_content" not in blob
    assert "annotations" not in blob
    replayed = sent[1:]
    assert [item["type"] for item in replayed] == ["function_call", "message"]
    assert set(replayed[0]) == {"type", "call_id", "name", "arguments"}
    assert replayed[0]["arguments"] == "{}"
    assert replayed[0]["call_id"] == "call_abc"
    assert "id" not in replayed[0]
    assert replayed[1]["role"] == "assistant"
    assert replayed[1]["content"] == [{"type": "output_text", "text": "note"}]


def test_bad_request_is_not_retried() -> None:
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(400, request=request)

    def responder(_kwargs: dict[str, Any]) -> dict[str, Any]:
        raise openai.BadRequestError(
            "Unknown parameter: 'input[2].status'",
            response=response,
            body={"error": {"message": "Unknown parameter: 'input[2].status'"}},
        )

    client = _Client(responder)
    model = OpenAIModel(
        model_id="gpt-5.4-mini-2026-03-17",
        api_key="sk-test",
        reasoning_effort="low",
        timeout_seconds=5,
        client=client,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ModelProviderError, match="input\\[2\\].status"):
        model.complete([{"role": "user", "content": "hello"}], tools=None, max_output_tokens=10)
    assert len(client.responses.calls) == 1


def test_one_shot_requests_json_schema_and_omits_tools() -> None:
    model_id = "gpt-5.4-mini-2026-03-17"
    client = _Client(lambda _kwargs: _ok(model_id, output=[
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": '{"claim":"unresolved","summary":"No change.","limitations":[],"patch":""}',
                }
            ],
        }
    ]))
    model = OpenAIModel(
        model_id=model_id,
        api_key="sk-test",
        reasoning_effort="low",
        timeout_seconds=5,
        client=client,
        sleep=lambda _seconds: None,
    )
    text_format = one_shot_text_format()
    turn = model.complete(
        [{"role": "user", "content": "hello"}],
        tools=None,
        max_output_tokens=4000,
        text_format=text_format,
    )
    sent = client.responses.calls[0]
    assert "tools" not in sent
    assert sent["text"]["format"]["type"] == "json_schema"
    assert sent["text"]["format"]["strict"] is True
    assert sent["text"]["format"]["name"] == "one_shot_repair"
    schema = sent["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["claim", "summary", "limitations", "patch"]
    from repair_agent.schemas import OneShotResponse

    parsed = OneShotResponse.model_validate_json(turn.text)
    assert parsed.claim == "unresolved"
    assert parsed.patch == ""


def test_one_shot_omits_tools() -> None:
    model_id = "gpt-5.4-mini-2026-03-17"
    client = _Client(lambda _kwargs: _ok(model_id))
    model = OpenAIModel(
        model_id=model_id,
        api_key="sk-test",
        reasoning_effort="low",
        timeout_seconds=5,
        client=client,
        sleep=lambda _seconds: None,
    )
    model.complete([{"role": "user", "content": "hello"}], tools=None, max_output_tokens=4000)
    assert "tools" not in client.responses.calls[0]


def test_different_model_id_is_refused_without_a_substitute() -> None:
    requested = "gpt-5.4-mini-2026-03-17"
    client = _Client(lambda _kwargs: _ok("gpt-5.4-mini"))
    model = OpenAIModel(
        model_id=requested,
        api_key="sk-test",
        reasoning_effort="low",
        timeout_seconds=5,
        client=client,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ModelIdentityError, match=requested):
        model.complete([{"role": "user", "content": "hello"}], tools=None, max_output_tokens=100)
    assert len(client.responses.calls) == 1
    assert client.responses.calls[0]["model"] == requested


def test_transport_errors_retry_twice_and_do_not_log_the_key(caplog: pytest.LogCaptureFixture) -> None:
    import httpx
    import openai

    model_id = "gpt-5.4-mini-2026-03-17"
    secret = "sk-test-secret-value"
    attempts = {"n": 0}
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")

    def responder(_kwargs: dict[str, Any]) -> dict[str, Any]:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise openai.APIConnectionError(message=f"reset {secret}", request=request)
        return _ok(model_id)

    client = _Client(responder)
    model = OpenAIModel(
        model_id=model_id,
        api_key=secret,
        reasoning_effort="low",
        timeout_seconds=5,
        client=client,
        sleep=lambda _seconds: None,
    )
    with caplog.at_level(logging.WARNING):
        turn = model.complete([{"role": "user", "content": "hello"}], tools=None, max_output_tokens=10)
    assert turn.model_id == model_id
    assert attempts["n"] == 3
    text = caplog.text
    assert "retrying (1/2)" in text
    assert "retrying (2/2)" in text
    assert secret not in text
    assert "[redacted]" in text


def test_transport_errors_stop_after_two_retries() -> None:
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.openai.com/v1/responses")

    def responder(_kwargs: dict[str, Any]) -> dict[str, Any]:
        raise openai.APITimeoutError(request)

    client = _Client(responder)
    model = OpenAIModel(
        model_id="gpt-5.4-mini-2026-03-17",
        api_key="sk-test",
        reasoning_effort="low",
        timeout_seconds=5,
        client=client,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ModelTransportError):
        model.complete([{"role": "user", "content": "hello"}], tools=None, max_output_tokens=10)
    assert len(client.responses.calls) == 3


def test_cost_uses_the_dated_prices_and_labels_unknown_cache() -> None:
    pricing = load_defaults().pricing
    cost, label = estimate_cost(input_tokens=1_000_000, cached_input_tokens=0, output_tokens=0, pricing=pricing)
    assert label == "estimated"
    assert cost == pytest.approx(0.75)
    cached, cached_label = estimate_cost(
        input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, pricing=pricing
    )
    assert cached_label == "estimated"
    assert cached == pytest.approx(0.075)
    output, output_label = estimate_cost(
        input_tokens=0, cached_input_tokens=0, output_tokens=1_000_000, pricing=pricing
    )
    assert output_label == "estimated"
    assert output == pytest.approx(4.5)
    unknown, unknown_label = estimate_cost(
        input_tokens=100, cached_input_tokens=None, output_tokens=10, pricing=pricing
    )
    assert unknown is None
    assert unknown_label == "unavailable"


def test_reasoning_items_are_stripped_from_the_trace_view() -> None:
    secret = "CHAIN_OF_THOUGHT_SECRET"
    redacted = redact_items(
        [
            {"type": "reasoning", "encrypted_content": secret, "summary": [{"text": secret}]},
            {"type": "message", "content": [{"type": "output_text", "text": "visible"}]},
        ]
    )
    blob = str(redacted)
    assert secret not in blob
    assert "encrypted_content" not in blob
    assert "visible" in blob


def test_reasoning_tokens_above_output_are_counted_as_output() -> None:
    model_id = "gpt-5.4-mini-2026-03-17"
    usage = _usage(output_tokens=10, output_tokens_details={"reasoning_tokens": 25})
    client = _Client(lambda _kwargs: _ok(model_id, usage=usage))
    model = OpenAIModel(
        model_id=model_id,
        api_key="sk-test",
        reasoning_effort="low",
        timeout_seconds=5,
        client=client,
        sleep=lambda _seconds: None,
    )
    turn = model.complete([{"role": "user", "content": "hello"}], tools=None, max_output_tokens=10)
    assert turn.usage.output_tokens == 35
    assert turn.usage.reasoning_tokens == 25
