"""Atomic result writes and append-only traces."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from repair_agent.artifacts import RunBundle, format_utc
from repair_agent.schemas import RunResult


def _result(run_id: str = "run-a") -> RunResult:
    return RunResult(
        run_id=run_id,
        case_id="X00",
        method="iterative",
        repetition=1,
        status="completed",
        fixture_sha="c" * 64,
        started_at_utc="2026-10-09T00:00:00Z",
        finished_at_utc="2026-10-09T00:00:01Z",
    )


def test_events_are_appended_in_order(tmp_path: Path) -> None:
    bundle = RunBundle(tmp_path / "run")
    bundle.append(
        category="prompt",
        success=True,
        request_summary="first",
        response_summary="one",
    )
    first = bundle.events_path.read_text(encoding="utf-8")
    bundle.append(
        category="tool",
        tool_name="list_files",
        success=True,
        request_summary="second",
        response_summary="two",
    )
    second = bundle.events_path.read_text(encoding="utf-8")
    assert second.startswith(first)
    lines = [line for line in second.splitlines() if line]
    assert [json.loads(line)["seq"] for line in lines] == [1, 2]
    assert json.loads(lines[1])["tool_name"] == "list_files"


def test_failed_replace_leaves_the_previous_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = RunBundle(tmp_path / "run")
    bundle.write_result(_result("run-a"))
    original = bundle.result_path.read_text(encoding="utf-8")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        bundle.write_result(_result("run-b"))
    assert bundle.result_path.read_text(encoding="utf-8") == original
    assert json.loads(original)["run_id"] == "run-a"
    assert list(bundle.run_dir.glob(".result.json.*.tmp")) == []


def test_successful_result_is_complete_json(tmp_path: Path) -> None:
    bundle = RunBundle(tmp_path / "run")
    bundle.write_result(_result())
    document = json.loads(bundle.result_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["cost_label"] == "unavailable"
    assert not list(bundle.run_dir.glob(".result.json.*.tmp"))


def test_utc_format_uses_zulu() -> None:
    stamp = datetime(2026, 10, 9, 1, 2, 3, tzinfo=timezone.utc)
    assert format_utc(stamp) == "2026-10-09T01:02:03Z"
