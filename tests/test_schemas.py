"""PublicRun is a separate contract from the private run result."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from repair_agent.publish import PUBLIC_RESULT_FIELDS
from repair_agent.schemas import PublicRun, RunResult


def test_public_run_rejects_private_fields() -> None:
    assert "evaluator_sha256" not in PublicRun.model_fields
    assert "evaluator_sha256" not in PUBLIC_RESULT_FIELDS
    assert "evaluator_sha256" in RunResult.model_fields


def test_public_run_rejects_unknown_keys() -> None:
    payload = _public()
    payload["evaluator_sha256"] = "ab" * 32
    with pytest.raises(ValidationError):
        PublicRun.model_validate(payload)


def test_public_run_requires_a_finished_timestamp() -> None:
    payload = _public()
    payload["finished_at_utc"] = ""
    with pytest.raises(ValidationError):
        PublicRun.model_validate(payload)


def _public() -> dict[str, object]:
    return {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "case_id": "X00",
        "method": "iterative",
        "repetition": 1,
        "status": "completed",
        "fixture_sha": "c" * 64,
        "started_at_utc": "2026-10-09T00:00:00Z",
        "finished_at_utc": "2026-10-09T00:00:01Z",
    }
