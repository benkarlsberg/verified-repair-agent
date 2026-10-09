"""Model adapter and usage accounting.

Real attempts use the OpenAI Responses API and the official Python SDK.
``gpt-5.4-mini-2026-03-17`` rejects function tools together with reasoning
effort on Chat Completions, so tool calls go through ``responses.create``
and the ``tools`` parameter. The key is read from ``OPENAI_API_KEY`` only
when a real client is constructed. It is never logged or written into a
run bundle.

Requests set ``store`` to false, so ``previous_response_id`` cannot continue
a tool call: the provider does not keep the response. The next request
resends prior input plus sanitized output items. Output-only fields
(``status``, ``id``, annotations) are dropped, and reasoning items are not
replayed. Token totals come from the provider ``usage`` object of each
request that was actually sent, including retransmitted and cached input at
full weight. Dropped reasoning items are not added back into that total.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Protocol

from repair_agent.config import Pricing
from repair_agent.schemas import SCRIPTED_MODEL_ID

logger = logging.getLogger("repair_agent.model")

ENV_API_KEY = "OPENAI_API_KEY"
_TRANSIENT_NAMES = ("APIConnectionError", "APITimeoutError")


class MissingAPIKeyError(Exception):
    """``run`` and ``evaluate`` need ``OPENAI_API_KEY`` for the OpenAI model."""


class ModelIdentityError(Exception):
    """The provider returned a different model id. No substitute is used."""


class ModelTransportError(Exception):
    """A transport error remained after two retries."""


class ModelClosedError(Exception):
    """Model access was closed at the end of the attempt."""


class ModelProviderError(Exception):
    """A non-transient provider error, such as HTTP 400. Not retried."""


@dataclass
class Usage:
    """Token counts for one response.

    ``output_tokens`` includes reasoning tokens when the provider bills them
    as output. ``cached_input_tokens`` is None when the provider did not
    report a cache split. ``complete`` is false when the usage object itself
    was missing.
    """

    input_tokens: int = 0
    cached_input_tokens: int | None = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = 0
    complete: bool = True

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] | None
    raw_arguments: str


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    model_id: str
    replay_items: list[dict[str, Any]]
    malformed: bool = False


class ModelClient(Protocol):
    model_id: str
    provider: str

    def complete(
        self,
        items: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        max_output_tokens: int,
        text_format: dict[str, Any] | None = None,
    ) -> ModelTurn: ...

    def close(self) -> None: ...


def one_shot_text_format() -> dict[str, Any]:
    """Responses API JSON-schema format for the one-shot baseline.

    The loop still validates the returned text with ``OneShotResponse``.
    """
    return {
        "type": "json_schema",
        "name": "one_shot_repair",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "claim": {
                    "type": "string",
                    "enum": ["repaired", "unresolved", "insufficient_evidence"],
                },
                "summary": {"type": "string"},
                "limitations": {"type": "array", "items": {"type": "string"}},
                "patch": {"type": "string"},
            },
            "required": ["claim", "summary", "limitations", "patch"],
        },
    }


def items_for_input(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert response output items into Responses API input items.

    Reasoning items are omitted. They are not valid continuation input when
    the response was not stored. Token accounting does not invent a count
    for them; the next request's provider usage is the source of truth.
    """
    prepared: list[dict[str, Any]] = []
    for item in items:
        if item.get("type") == "reasoning":
            continue
        prepared.append(_input_item(item))
    return prepared


def redact_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy history for traces. Drop reasoning items and encrypted content."""
    redacted: list[dict[str, Any]] = []
    for item in items:
        if item.get("type") == "reasoning":
            redacted.append({"type": "reasoning", "omitted": True})
            continue
        redacted.append(_strip_secrets(item))
    return redacted


def estimate_tokens(payload: object) -> int:
    """Conservative input estimate: about 4 bytes per token, rounded up."""
    encoded = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
    return max(1, (len(encoded) + 3) // 4)


def estimate_cost(
    *,
    input_tokens: int,
    cached_input_tokens: int | None,
    output_tokens: int,
    pricing: Pricing,
) -> tuple[float | None, str]:
    """Price a completed attempt. Unknown cache splits are unavailable."""
    if cached_input_tokens is None or input_tokens < 0 or output_tokens < 0:
        return None, "unavailable"
    if cached_input_tokens > input_tokens or cached_input_tokens < 0:
        return None, "unavailable"
    uncached = input_tokens - cached_input_tokens
    cost = (
        Decimal(uncached) * Decimal(str(pricing.input_per_million))
        + Decimal(cached_input_tokens) * Decimal(str(pricing.cached_input_per_million))
        + Decimal(output_tokens) * Decimal(str(pricing.output_per_million))
    ) / Decimal(1_000_000)
    quantized = cost.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    return float(quantized), "estimated"


def scrub(text: str, secret: str | None) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


class OpenAIModel:
    """Responses API client. Transport errors are retried at most twice."""

    provider = "openai"

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        reasoning_effort: str,
        timeout_seconds: float,
        store: bool = False,
        client: object | None = None,
        sleep: Any = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise MissingAPIKeyError(
                "OPENAI_API_KEY is not set. repair-agent run and evaluate read it "
                "from the environment when --model openai is selected. "
                "validate-cases, freeze, and --model fake do not need a key."
            )
        self.model_id = model_id
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.store = store
        self._api_key = api_key
        self._client = client
        self._sleep = time.sleep if sleep is None else sleep
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @classmethod
    def from_env(
        cls,
        *,
        model_id: str,
        reasoning_effort: str,
        timeout_seconds: float,
        store: bool = False,
    ) -> OpenAIModel:
        return cls(
            model_id=model_id,
            api_key=os.environ.get(ENV_API_KEY, ""),
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            store=store,
        )

    def close(self) -> None:
        self._closed = True

    def complete(
        self,
        items: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        max_output_tokens: int,
        text_format: dict[str, Any] | None = None,
    ) -> ModelTurn:
        if self._closed:
            raise ModelClosedError("model access is closed")
        response = self._create_with_retries(
            items,
            tools=tools,
            max_output_tokens=max_output_tokens,
            text_format=text_format,
        )
        returned = _read(response, "model")
        if returned != self.model_id:
            raise ModelIdentityError(
                f"Refusing to continue: requested {self.model_id}, provider returned {returned}."
            )
        output = _read(response, "output") or []
        output_items = [_item_dict(item) for item in output]
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        malformed = False
        for item in output_items:
            if item.get("type") == "reasoning":
                continue
            if item.get("type") == "function_call":
                raw_arguments = item.get("arguments") or ""
                if not isinstance(raw_arguments, str):
                    raw_arguments = json.dumps(raw_arguments)
                try:
                    parsed = json.loads(raw_arguments) if raw_arguments else {}
                except json.JSONDecodeError:
                    parsed = None
                    malformed = True
                if parsed is not None and not isinstance(parsed, dict):
                    parsed = None
                    malformed = True
                calls.append(
                    ToolCall(
                        call_id=str(item.get("call_id") or item.get("id") or ""),
                        name=str(item.get("name") or ""),
                        arguments=parsed,
                        raw_arguments=raw_arguments,
                    )
                )
                continue
            if item.get("type") in {None, "message"} or item.get("role") in {"assistant", "system"}:
                text_parts.append(_message_text(item))
        return ModelTurn(
            text="".join(text_parts),
            tool_calls=calls,
            usage=_usage_from(response),
            model_id=self.model_id,
            replay_items=items_for_input(output_items),
            malformed=malformed,
        )

    def _create_with_retries(
        self,
        items: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        max_output_tokens: int,
        text_format: dict[str, Any] | None,
    ) -> object:
        transient = _transient_types()
        provider_errors = _provider_error_types()
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self._create(
                    items,
                    tools=tools,
                    max_output_tokens=max_output_tokens,
                    text_format=text_format,
                )
            except transient as exc:
                last = exc
                message = scrub(f"{type(exc).__name__}: {exc}", self._api_key)
                if attempt < 2:
                    logger.warning("transient OpenAI transport error; retrying (%s/2): %s", attempt + 1, message)
                    self._sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("transient OpenAI transport error; retries exhausted: %s", message)
                raise ModelTransportError(message) from exc
            except provider_errors as exc:
                message = scrub(f"{type(exc).__name__}: {exc}", self._api_key)
                logger.warning("OpenAI rejected the request: %s", message)
                raise ModelProviderError(message) from exc
        raise ModelTransportError(scrub(str(last), self._api_key))

    def _create(
        self,
        items: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        max_output_tokens: int,
        text_format: dict[str, Any] | None,
    ) -> object:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "input": items,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": max_output_tokens,
            "store": self.store,
        }
        if tools:
            kwargs["tools"] = tools
        if text_format is not None:
            kwargs["text"] = {"format": text_format}
        return self._sdk().responses.create(**kwargs)

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import OpenAI

        self._client = OpenAI(api_key=self._api_key, max_retries=0, timeout=self.timeout_seconds)
        return self._client


@dataclass
class FakeTurn:
    """One scripted response. No network."""

    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(100, 0, 20, 0, True))
    bad_arguments: bool = False
    reasoning_secret: str | None = None


class FakeModel:
    """Deterministic stand-in used by tests and ``--model fake``."""

    provider = "fake"

    def __init__(self, model_id: str, turns: list[FakeTurn]) -> None:
        self.model_id = model_id
        self._turns = list(turns)
        self.seen_inputs: list[list[dict[str, Any]]] = []
        self.seen_tools: list[list[dict[str, Any]] | None] = []
        self.seen_text_formats: list[dict[str, Any] | None] = []
        self.calls = 0
        self._closed = False
        self._call_index = 0

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def complete(
        self,
        items: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        max_output_tokens: int,
        text_format: dict[str, Any] | None = None,
    ) -> ModelTurn:
        del max_output_tokens
        if self._closed:
            raise ModelClosedError("model access is closed")
        self.calls += 1
        self.seen_inputs.append(json.loads(json.dumps(items, default=str)))
        self.seen_tools.append(tools)
        self.seen_text_formats.append(text_format)
        if not self._turns:
            return ModelTurn(
                text="",
                tool_calls=[],
                usage=Usage(1, 0, 1, 0, True),
                model_id=self.model_id,
                replay_items=[],
                malformed=True,
            )
        turn = self._turns.pop(0)
        self._call_index += 1
        replay: list[dict[str, Any]] = []
        if turn.reasoning_secret:
            replay.append(
                {
                    "type": "reasoning",
                    "encrypted_content": turn.reasoning_secret,
                    "summary": [{"text": turn.reasoning_secret}],
                }
            )
        calls: list[ToolCall] = []
        if turn.bad_arguments:
            call_id = f"call_bad_{self._call_index}"
            replay.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": "finish",
                    "arguments": "{not-json",
                }
            )
            return ModelTurn(
                text=turn.text,
                tool_calls=[
                    ToolCall(call_id=call_id, name="finish", arguments=None, raw_arguments="{not-json")
                ],
                usage=turn.usage,
                model_id=self.model_id,
                replay_items=replay,
                malformed=True,
            )
        for offset, (name, arguments) in enumerate(turn.tool_calls, start=1):
            call_id = f"call_{self._call_index}_{offset}"
            raw = json.dumps(arguments)
            replay.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": raw,
                }
            )
            calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments, raw_arguments=raw))
        if turn.text:
            replay.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": turn.text}],
                }
            )
        return ModelTurn(
            text=turn.text,
            tool_calls=calls,
            usage=turn.usage,
            model_id=self.model_id,
            replay_items=replay,
            malformed=False,
        )


def _transient_types() -> tuple[type[BaseException], ...]:
    try:
        import openai
    except ImportError:
        return ()
    found = [getattr(openai, name) for name in _TRANSIENT_NAMES if hasattr(openai, name)]
    return tuple(found)


def _provider_error_types() -> tuple[type[BaseException], ...]:
    """Non-transient API failures. ``BadRequestError`` is an ``APIStatusError``."""
    try:
        import openai
    except ImportError:
        return ()
    status = getattr(openai, "APIStatusError", None)
    if status is None:
        return ()
    return (status,)


def _usage_from(response: object) -> Usage:
    usage = _read(response, "usage")
    if usage is None:
        return Usage(0, None, 0, None, complete=False)
    input_tokens = _optional_int(_read(usage, "input_tokens"))
    output_tokens = _optional_int(_read(usage, "output_tokens"))
    if input_tokens is None or output_tokens is None:
        return Usage(0, None, 0, None, complete=False)
    input_details = _read(usage, "input_tokens_details")
    output_details = _read(usage, "output_tokens_details")
    cached = _optional_int(_read(input_details, "cached_tokens")) if input_details is not None else None
    reasoning = _optional_int(_read(output_details, "reasoning_tokens")) if output_details is not None else None
    # OpenAI includes reasoning inside output_tokens. If a stub reports them
    # separately, keep the cap honest by counting both.
    if reasoning is not None and reasoning > output_tokens:
        output_tokens = output_tokens + reasoning
    return Usage(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning,
        complete=True,
    )


def _item_dict(item: object) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        data = dump(mode="json")
        if isinstance(data, dict):
            return data
    data: dict[str, Any] = {}
    for key in ("type", "role", "name", "arguments", "call_id", "id", "content", "encrypted_content", "summary"):
        if hasattr(item, key):
            value = getattr(item, key)
            data[key] = _jsonable(value)
    return data


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _jsonable(dump(mode="json"))
    return str(value)


def _input_item(item: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields the Responses API accepts on input."""
    kind = item.get("type")
    if kind == "function_call":
        arguments = item.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if arguments is not None else {})
        call_id = item.get("call_id") or item.get("id") or ""
        return {
            "type": "function_call",
            "call_id": str(call_id),
            "name": str(item.get("name") or ""),
            "arguments": arguments,
        }
    if kind == "function_call_output":
        output = item.get("output")
        if not isinstance(output, str):
            output = json.dumps(output if output is not None else "")
        return {
            "type": "function_call_output",
            "call_id": str(item.get("call_id") or ""),
            "output": output,
        }
    if kind in {None, "message"}:
        role = item.get("role") or "assistant"
        if role not in {"assistant", "user", "system"}:
            role = "assistant"
        return {
            "type": "message",
            "role": role,
            "content": [{"type": "output_text", "text": _message_text(item)}],
        }
    return {key: value for key, value in item.items() if key not in {"status", "id"} and value is not None}


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and "text" in part:
            parts.append(str(part.get("text") or ""))
    return "".join(parts)


def _strip_secrets(value: object) -> object:
    if isinstance(value, dict):
        return {key: _strip_secrets(item) for key, item in value.items() if key != "encrypted_content"}
    if isinstance(value, list):
        return [_strip_secrets(item) for item in value]
    return value


def _read(obj: object, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
