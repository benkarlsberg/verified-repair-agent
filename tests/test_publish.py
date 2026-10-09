"""Sanitized public exports."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from repair_agent.cli import main
from repair_agent.publish import PublishError, build_public_run, publish, scan_text
from repair_agent.schemas import PublicRun, RunResult, TraceEvent

SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz"
HOST_PATH = "/home/ben/verified-repair-agent"
PROMPT = (
    "You repair one deterministic defect in a local Python order service.\n"
    "# API contract\n\nBe precise.\n\n# Workspace files\n\norder_service/example_ops.py"
)
PROTECTED_NAME = "test_hidden_oracle_behavior"
PROTECTED_SENTENCE = "PROTECTED_OUTPUT_SHOULD_NOT_LEAK"
REFERENCE_SENTENCE = "REFERENCE_FIX_SHOULD_NOT_LEAK"
EVALUATOR_DETAIL = "evaluator detail mentions evaluator_private/tests/X00/test_hidden.py"


def test_publish_strips_secrets_paths_prompts_and_evaluator_output(tmp_path: Path) -> None:
    run_dir = _bundle(
        tmp_path / "runs",
        summary=f"key {SECRET} lives at {HOST_PATH}",
        evidence=["evaluator_private/reference_fixes/D01.patch"],
        prompt=PROMPT,
    )
    (run_dir / "evaluator.txt").write_text(
        f"{PROTECTED_SENTENCE}\n{PROTECTED_NAME}\n{EVALUATOR_DETAIL}\n",
        encoding="utf-8",
    )
    evaluator = {
        "verification": "failed",
        "visible_passed": True,
        "protected_passed": False,
        "detail": EVALUATOR_DETAIL,
        "visible_output": "ordinary suite",
        "protected_output": f"{PROTECTED_SENTENCE} {PROTECTED_NAME}\n",
        "evaluator_sha256": "ab" * 32,
    }
    (run_dir / "evaluator.json").write_text(json.dumps(evaluator), encoding="utf-8")
    (run_dir / "reference_fix.patch").write_text(REFERENCE_SENTENCE + "\n", encoding="utf-8")
    output = tmp_path / "public"
    result = publish(tmp_path / "runs", output, issue_text_for=lambda _case: "Issue text for the case.")
    assert result.published
    text = (output / result.published[0] / "public.json").read_text(encoding="utf-8")
    public = PublicRun.model_validate_json(text)
    assert SECRET not in text
    assert HOST_PATH not in text
    assert "evaluator_private" not in text
    assert PROTECTED_SENTENCE not in text
    assert PROTECTED_NAME not in text
    assert REFERENCE_SENTENCE not in text
    assert "You repair one deterministic defect" not in text
    assert "# Workspace files" not in text
    assert "evaluator_sha256" not in text
    assert "ab" * 32 not in text
    assert public.verification == "failed"
    assert public.visible_passed is True
    assert public.protected_passed is False
    assert public.issue == "Issue text for the case."
    assert any(event.response_summary == "Full prompt omitted." for event in public.trace)
    assert scan_text(text) == []


def test_publish_refuses_incomplete_runs(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    unfinished = runs / str(uuid4())
    unfinished.mkdir()
    (unfinished / "events.jsonl").write_text("", encoding="utf-8")
    missing_finish = _bundle(runs, finished=None)
    good = _bundle(runs)
    output = tmp_path / "public"
    result = publish(runs, output, issue_text_for=lambda _case: "issue")
    exported = {path.name for path in output.iterdir() if path.is_dir()}
    assert good.name in exported
    assert unfinished.name not in exported
    assert missing_finish.name not in exported
    assert {run_id for run_id, _reason in result.incomplete} == {unfinished.name, missing_finish.name}


def test_publish_command_exits_nonzero_for_incomplete_runs(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _bundle(runs, finished=None)
    code = main(["publish", "--runs", str(runs), "--output", str(tmp_path / "public")])
    assert code == 1
    assert list((tmp_path / "public").glob("*/public.json")) == []


def test_leak_scan_rejects_a_bundle_the_sanitizer_missed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("repair_agent.publish._sanitize_text", lambda text, markers=(): text)
    runs = tmp_path / "runs"
    _bundle(runs, summary="see evaluator_private/tests/D01 and " + SECRET)
    output = tmp_path / "public"
    with pytest.raises(PublishError, match="leak scan failed"):
        publish(runs, output, issue_text_for=lambda _case: "issue")
    assert not output.exists()


def test_host_paths_are_removed_and_library_paths_are_shortened() -> None:
    from repair_agent.publish import sanitize_text

    text = sanitize_text(
        "../usr/local/lib/python3.11/site-packages/starlette/testclient.py:37\nsee /home/ben/secret\n"
    )
    assert "/usr/" not in text
    assert "/home/" not in text
    assert "starlette/testclient.py" in text
    assert "..starlette" not in text
    assert "[path]" in text
    assert scan_text(text) == []


def test_scan_text_flags_paths_keys_and_private_dir() -> None:
    assert "path" in scan_text("wrote /tmp/vra-eval/work/file.py")
    assert "key-like string" in scan_text("Authorization bearer abcdefghijklmnop")
    assert "evaluator_private" in scan_text("copied evaluator_private/tests/D01")
    assert scan_text("order_service/example_ops.py passed") == []
    assert scan_text("see https://developers.openai.com/api/docs/models/gpt-5.4-mini") == []


def test_real_issue_text_is_included_without_oracle_paths(tmp_path: Path) -> None:
    run_dir = _bundle(tmp_path / "runs", case_id="X00")
    public = build_public_run(run_dir, issue_text="")
    # The builder stores the issue it was given. The publisher loads it.
    output = tmp_path / "public"
    publish(tmp_path / "runs", output)
    text = next(output.glob("*/public.json")).read_text(encoding="utf-8")
    assert "EXAMPLE ONLY" in text
    assert "evaluator_private" not in text
    assert "reference_fixes" not in text
    assert public.case_id == "X00"


def _bundle(
    runs: Path,
    *,
    case_id: str = "X00",
    summary: str = "Edited the label.",
    evidence: list[str] | None = None,
    prompt: str = "short",
    finished: str | None = "2026-10-09T00:00:02Z",
    verification: str = "failed",
) -> Path:
    runs.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid4())
    run_dir = runs / run_id
    run_dir.mkdir()
    result = RunResult(
        run_id=run_id,
        case_id=case_id,
        method="iterative",
        repetition=1,
        status="completed",
        agent_claim="repaired",
        verification=verification,  # type: ignore[arg-type]
        summary=summary,
        evidence=evidence or [],
        source_files_changed=["order_service/example_ops.py"],
        patch_sha256="d" * 64,
        visible_passed=True,
        protected_passed=False,
        input_tokens=10,
        output_tokens=4,
        tool_calls=2,
        elapsed_seconds=1.5,
        estimated_cost_usd=0.001,
        cost_label="estimated",
        model_id="gpt-5.4-mini-2026-03-17",
        provider="fake",
        fixture_sha="c" * 64,
        evaluator_sha256="ab" * 32,
        pricing={
            "as_of": "2026-10-09",
            "source": "https://developers.openai.com/api/docs/models/gpt-5.4-mini",
            "currency": "USD",
            "input_per_million": 0.75,
            "cached_input_per_million": 0.075,
            "output_per_million": 4.5,
        },
        started_at_utc="2026-10-09T00:00:00Z",
        finished_at_utc=finished,
    )
    (run_dir / "result.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    events = [
        TraceEvent(
            seq=1,
            timestamp_utc="2026-10-09T00:00:00Z",
            category="prompt",
            success=True,
            request_summary="method=iterative items=2",
            response_summary=prompt,
        ),
        TraceEvent(
            seq=2,
            timestamp_utc="2026-10-09T00:00:01Z",
            category="tool",
            tool_name="apply_patch",
            success=True,
            request_summary="diff --git a/order_service/example_ops.py b/order_service/example_ops.py",
            response_summary="applied",
        ),
    ]
    (run_dir / "events.jsonl").write_text(
        "".join(event.model_dump_json() + "\n" for event in events),
        encoding="utf-8",
    )
    (run_dir / "final.patch").write_text(
        "diff --git a/order_service/example_ops.py b/order_service/example_ops.py\n"
        "--- a/order_service/example_ops.py\n"
        "+++ b/order_service/example_ops.py\n"
        "@@ -1 +1 @@\n"
        '-    "cancelled": "Canceled",\n'
        '+    "cancelled": "Cancelled",\n',
        encoding="utf-8",
    )
    (run_dir / "visible_tests.txt").write_text("3 passed\n", encoding="utf-8")
    (run_dir / "evaluator.json").write_text(
        json.dumps(
            {
                "verification": verification,
                "visible_passed": True,
                "protected_passed": False,
                "detail": "protected tests failed",
                "protected_output": "",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "evaluator.txt").write_text("protected tests failed\n", encoding="utf-8")
    return run_dir
