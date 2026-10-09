"""Controller budgets, format repair, claims, and evaluator isolation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from repair_agent.artifacts import Clock
from repair_agent.cases import load_case
from repair_agent.cli import main
from repair_agent.config import load_defaults
from repair_agent.evaluate import EvaluationRecord, SuiteOutcome, decide_verdict
from repair_agent.loop import (
    AttemptConfig,
    build_case_packet,
    initial_items,
    iterative_instructions,
    one_shot_instructions,
    run_attempt,
)
from repair_agent.model import FakeModel, FakeTurn, ModelProviderError, Usage, one_shot_text_format
from repair_agent.runner import RunnerResult


class _Clock:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)
        self.now = datetime(2026, 10, 9, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]

    def utc_now(self) -> datetime:
        return self.now


class _VisibleRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_visible_tests(self, workspace: Path, timeout_seconds: float | None = None) -> RunnerResult:
        del timeout_seconds
        self.calls += 1
        source = (workspace / "order_service" / "example_ops.py").read_text(encoding="utf-8")
        repro = workspace / "agent_tests"
        has_repro = repro.is_dir() and any(repro.glob("test_*.py"))
        fixed = '"cancelled": "Cancelled"' in source
        if has_repro and not fixed:
            return RunnerResult(exit_code=1, stdout="FAILED test_cancelled_display_label\n", stderr="")
        return RunnerResult(exit_code=0, stdout="passed\n", stderr="")


def _settings(**budget_overrides: object) -> AttemptConfig:
    base = AttemptConfig.from_defaults()
    budgets = base.budgets.model_copy(update=budget_overrides)
    return AttemptConfig(
        model_id=base.model_id,
        reasoning_effort=base.reasoning_effort,
        temperature=base.temperature,
        top_p=base.top_p,
        store=base.store,
        api=base.api,
        budgets=budgets,
        pricing=base.pricing,
        max_changed_lines=base.max_changed_lines,
        max_files=base.max_files,
        request_timeout_seconds=base.request_timeout_seconds,
        runner_image=base.runner_image,
    )


def _eval_failed(**kwargs: object) -> EvaluationRecord:
    run_dir = kwargs["run_dir"]
    assert isinstance(run_dir, Path)
    (run_dir / "evaluator.txt").write_text("PROTECTED_SENTINEL_OUTPUT\n", encoding="utf-8")
    (run_dir / "evaluator.json").write_text(
        json.dumps({"protected_output": "PROTECTED_SENTINEL_OUTPUT"}) + "\n",
        encoding="utf-8",
    )
    return EvaluationRecord(
        verification="failed",
        visible_passed=True,
        protected_passed=False,
        detail="protected tests failed",
        evaluator_sha256="a" * 64,
    )


def _run(
    tmp_path: Path,
    turns: list[FakeTurn],
    *,
    method: str = "iterative",
    settings: AttemptConfig | None = None,
    clock: Clock | None = None,
    estimator: object | None = None,
    evaluate: object | None = None,
    runner: object | None = None,
) -> tuple[object, FakeModel]:
    case = load_case("X00")
    model_box: dict[str, FakeModel] = {}
    active = settings or _settings()

    def factory(workspace: Path) -> FakeModel:
        del workspace
        model_box["model"] = FakeModel(active.model_id, list(turns))
        return model_box["model"]

    result = run_attempt(
        case,
        method=method,
        repetition=1,
        settings=active,
        model_factory=factory,
        runs_root=tmp_path,
        runner=runner if runner is not None else _VisibleRunner(),  # type: ignore[arg-type]
        clock=clock,
        estimator=estimator,  # type: ignore[arg-type]
        evaluate=evaluate,  # type: ignore[arg-type]
    )
    return result, model_box["model"]


def _finish(claim: str = "unresolved") -> FakeTurn:
    return FakeTurn(
        tool_calls=[
            (
                "finish",
                {
                    "claim": claim,
                    "summary": "Done.",
                    "limitations": [],
                    "evidence": [],
                },
            )
        ],
        usage=Usage(10, 0, 5, 1, True),
    )


def test_token_budget_stops_before_the_request(tmp_path: Path) -> None:
    result, model = _run(
        tmp_path,
        [_finish()],
        settings=_settings(max_total_tokens=1000, max_output_tokens_per_response=4000),
        estimator=lambda _payload: 900,
    )
    assert model.calls == 0
    assert result.stop_reason == "tokens"  # type: ignore[attr-defined]
    assert result.agent_claim == "unresolved"  # type: ignore[attr-defined]
    assert result.verification == "rejected"  # type: ignore[attr-defined]


def test_retransmitted_input_counts_even_when_cached(tmp_path: Path) -> None:
    def estimator(payload: object) -> int:
        assert isinstance(payload, dict)
        items = payload["input"]
        assert isinstance(items, list)
        # The first request is the system prompt plus the case packet.
        return 100 if len(items) <= 2 else 900

    result, model = _run(
        tmp_path,
        [
            FakeTurn(
                tool_calls=[("list_files", {})],
                usage=Usage(input_tokens=1000, cached_input_tokens=800, output_tokens=10, reasoning_tokens=4, complete=True),
            )
        ],
        settings=_settings(max_total_tokens=2000, max_output_tokens_per_response=200),
        estimator=estimator,
    )
    assert model.calls == 1
    assert result.input_tokens == 1000  # type: ignore[attr-defined]
    assert result.cached_input_tokens == 800  # type: ignore[attr-defined]
    assert result.output_tokens == 10  # type: ignore[attr-defined]
    assert result.stop_reason == "tokens"  # type: ignore[attr-defined]
    assert result.input_tokens + result.output_tokens == 1010  # type: ignore[attr-defined]


def test_finish_only_turn_stays_inside_the_token_budget(tmp_path: Path) -> None:
    def estimator(payload: object) -> int:
        assert isinstance(payload, dict)
        tools = payload["tools"]
        names = [tool["name"] for tool in tools] if isinstance(tools, list) else []
        if names == ["finish"]:
            return 50
        return 800

    result, model = _run(
        tmp_path,
        [
            FakeTurn(
                tool_calls=[("list_files", {})],
                usage=Usage(input_tokens=1400, cached_input_tokens=1100, output_tokens=100, reasoning_tokens=20, complete=True),
            ),
            _finish("repaired"),
        ],
        settings=_settings(max_total_tokens=2000, max_output_tokens_per_response=400),
        estimator=estimator,
        evaluate=_eval_failed,
    )
    assert model.calls == 2
    assert [tool["name"] for tool in model.seen_tools[0] or []] == [
        "list_files",
        "read_file",
        "search_text",
        "apply_patch",
        "run_visible_tests",
        "finish",
    ]
    assert [tool["name"] for tool in model.seen_tools[1] or []] == ["finish"]
    assert result.stop_reason == "finish"  # type: ignore[attr-defined]
    assert result.agent_claim == "repaired"  # type: ignore[attr-defined]
    assert result.input_tokens == 1410  # type: ignore[attr-defined]
    assert result.cached_input_tokens == 1100  # type: ignore[attr-defined]
    assert result.output_tokens == 105  # type: ignore[attr-defined]
    assert result.input_tokens + result.output_tokens <= 2000  # type: ignore[attr-defined]


def test_provider_error_still_writes_an_infra_result(tmp_path: Path) -> None:
    case = load_case("X00")
    settings = _settings()

    class _FailOnSecond(FakeModel):
        def complete(self, items, *, tools, max_output_tokens, text_format=None):  # type: ignore[no-untyped-def]
            if self.calls >= 1:
                self.calls += 1
                self.seen_inputs.append(items)
                raise ModelProviderError("400 Unknown parameter: 'input[2].status'")
            return super().complete(
                items, tools=tools, max_output_tokens=max_output_tokens, text_format=text_format
            )

    def factory(workspace: Path) -> _FailOnSecond:
        del workspace
        return _FailOnSecond(
            settings.model_id,
            [FakeTurn(tool_calls=[("list_files", {})], usage=Usage(10, 0, 5, 0, True))],
        )

    result = run_attempt(
        case,
        method="iterative",
        repetition=1,
        settings=settings,
        model_factory=factory,
        runs_root=tmp_path,
        runner=_VisibleRunner(),  # type: ignore[arg-type]
        evaluate=_eval_failed,
    )
    saved = tmp_path / result.run_id / "result.json"
    events = tmp_path / result.run_id / "events.jsonl"
    assert saved.is_file()
    assert events.is_file()
    document = json.loads(saved.read_text(encoding="utf-8"))
    assert document["status"] == "infra_error"
    assert document["verification"] == "infra_error"
    assert document["stop_reason"] == "provider"
    assert "input[2].status" in document["limitations"][0]
    assert result.status == "infra_error"


def test_response_tool_and_patch_budgets_stop(tmp_path: Path) -> None:
    response, response_model = _run(
        tmp_path / "responses",
        [FakeTurn(tool_calls=[("list_files", {})], usage=Usage(10, 0, 5, 0, True))],
        settings=_settings(max_model_responses=1),
    )
    assert response_model.calls == 1
    assert response.stop_reason == "model_responses"  # type: ignore[attr-defined]
    assert response.agent_claim == "unresolved"  # type: ignore[attr-defined]

    tools, tools_model = _run(
        tmp_path / "tools",
        [FakeTurn(tool_calls=[("list_files", {}), ("list_files", {})], usage=Usage(10, 0, 5, 0, True))],
        settings=_settings(max_tool_calls=1),
    )
    assert tools_model.calls == 1
    assert tools.tool_calls == 1  # type: ignore[attr-defined]
    assert tools.stop_reason == "tool_calls"  # type: ignore[attr-defined]

    from repair_agent.diffs import new_file_diff, unified_diff
    from repair_agent.fake_scripts import _REPRO

    second = unified_diff("order_service/example_ops.py", "alpha\n", "beta\n")
    patch_result, patch_model = _run(
        tmp_path / "patches",
        [
            FakeTurn(tool_calls=[("apply_patch", {"patch": new_file_diff("agent_tests/test_repro.py", _REPRO)})]),
            FakeTurn(tool_calls=[("run_visible_tests", {})]),
            FakeTurn(tool_calls=[("apply_patch", {"patch": _real_x00_patch(False)})]),
            FakeTurn(tool_calls=[("apply_patch", {"patch": second})]),
        ],
        settings=_settings(max_source_patch_submissions=1),
        runner=_VisibleRunner(),
        evaluate=_eval_failed,
    )
    assert patch_model.calls == 4
    assert patch_result.patch_attempts == 1  # type: ignore[attr-defined]
    assert patch_result.stop_reason == "patch_attempts"  # type: ignore[attr-defined]
    assert patch_result.agent_claim == "unresolved"  # type: ignore[attr-defined]


def test_wall_clock_stops_before_a_model_call(tmp_path: Path) -> None:
    result, model = _run(
        tmp_path,
        [_finish()],
        settings=_settings(wall_clock_seconds=30),
        clock=_Clock([0.0, 30.0]),
    )
    assert model.calls == 0
    assert result.stop_reason == "wall_clock"  # type: ignore[attr-defined]
    assert result.agent_claim == "unresolved"  # type: ignore[attr-defined]


def test_format_retry_is_used_once(tmp_path: Path) -> None:
    result, model = _run(
        tmp_path,
        [FakeTurn(bad_arguments=True, usage=Usage(10, 0, 5, 0, True)), _finish("insufficient_evidence")],
    )
    assert model.calls == 2
    assert any("not usable" in json.dumps(items) for items in model.seen_inputs[1:])
    assert result.agent_claim == "insufficient_evidence"  # type: ignore[attr-defined]
    assert result.stop_reason == "finish"  # type: ignore[attr-defined]

    again, again_model = _run(
        tmp_path / "twice",
        [FakeTurn(bad_arguments=True), FakeTurn(bad_arguments=True), _finish()],
    )
    assert again_model.calls == 2
    assert again.stop_reason == "malformed_output"  # type: ignore[attr-defined]
    assert again.agent_claim == "unresolved"  # type: ignore[attr-defined]


def test_stripped_patch_wrapper_is_recorded_in_the_trace(tmp_path: Path) -> None:
    from repair_agent.diffs import new_file_diff

    diff = new_file_diff("agent_tests/test_repro.py", "def test_repro():\n    assert False\n")
    wrapped = "*** Begin Patch\n" + diff + "*** End Patch\n"
    result, _model = _run(
        tmp_path,
        [
            FakeTurn(tool_calls=[("apply_patch", {"patch": wrapped})], usage=Usage(10, 0, 5, 0, True)),
            _finish(),
        ],
        evaluate=_eval_failed,
    )
    events = (tmp_path / result.run_id / "events.jsonl").read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "wrapper_stripped" in events
    assert "Stripped *** Begin Patch / *** End Patch wrapper lines" in events
    assert result.stop_reason == "finish"  # type: ignore[attr-defined]


def test_one_shot_malformed_output_is_rejected_without_a_retry(tmp_path: Path) -> None:
    result, model = _run(tmp_path, [FakeTurn(text="this is not json")], method="one_shot")
    assert model.calls == 1
    assert model.seen_tools == [None]
    assert model.seen_text_formats == [one_shot_text_format()]
    assert result.agent_claim is None  # type: ignore[attr-defined]
    assert result.verification == "rejected"  # type: ignore[attr-defined]
    assert result.stop_reason == "malformed_output"  # type: ignore[attr-defined]


def test_claim_is_kept_when_verification_fails(tmp_path: Path) -> None:
    result, model = _run(tmp_path, [_finish("repaired")], evaluate=_eval_failed)
    assert model.closed
    assert result.agent_claim == "repaired"  # type: ignore[attr-defined]
    assert result.verification == "failed"  # type: ignore[attr-defined]
    blob = json.dumps(model.seen_inputs)
    events = (tmp_path / result.run_id / "events.jsonl").read_text(encoding="utf-8")  # type: ignore[attr-defined]
    saved = (tmp_path / result.run_id / "result.json").read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "PROTECTED_SENTINEL_OUTPUT" not in blob
    assert "PROTECTED_SENTINEL_OUTPUT" not in events
    assert "PROTECTED_SENTINEL_OUTPUT" not in saved
    assert "PROTECTED_SENTINEL_OUTPUT" in (tmp_path / result.run_id / "evaluator.txt").read_text(encoding="utf-8")  # type: ignore[attr-defined]


def test_reasoning_secret_is_not_written_to_the_bundle(tmp_path: Path) -> None:
    secret = "CHAIN_OF_THOUGHT_SECRET"
    result, model = _run(tmp_path, [FakeTurn(reasoning_secret=secret, tool_calls=[], text=""), _finish()])
    # The first turn is malformed (no tool call), the retry finishes.
    bundle = tmp_path / result.run_id  # type: ignore[attr-defined]
    saved = bundle.read_text() if bundle.is_file() else "\n".join(path.read_text(encoding="utf-8") for path in bundle.rglob("*") if path.is_file())
    assert secret not in saved
    assert all(secret not in json.dumps(items) for items in model.seen_inputs)


def test_case_packet_is_shared_and_has_no_oracle_paths() -> None:
    case = load_case("X00")
    from repair_agent.cases import materialize_workspace
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "work"
        materialize_workspace(workspace, case)
        packet = build_case_packet(case, workspace, "contract text")
    iterative = initial_items("iterative", packet, list(case.record.allowed_paths))
    one_shot = initial_items("one_shot", packet, list(case.record.allowed_paths))
    assert iterative[1] == one_shot[1]
    assert "evaluator_private" not in packet
    assert "reference_fix" not in packet
    assert "test_cancelled_label_is_spelled_with_two_ls" not in packet
    assert "Canceled" in packet
    assert "split:" not in packet
    iterative_prompt = iterative_instructions(list(case.record.allowed_paths))
    one_shot_prompt = one_shot_instructions(list(case.record.allowed_paths))
    from repair_agent.tools import tool_schemas

    apply_patch = next(tool for tool in tool_schemas() if tool["name"] == "apply_patch")
    for text in (iterative_prompt, one_shot_prompt, apply_patch["description"], apply_patch["parameters"]["properties"]["patch"]["description"]):
        assert "diff --git a/order_service/pricing.py b/order_service/pricing.py" in text
        assert "--- a/order_service/pricing.py" in text
        assert "+++ b/order_service/pricing.py" in text
        assert "@@ -1,3 +1,3 @@" in text
        assert "Begin Patch" in text
    assert "Call finish once your reproduction test and the visible tests pass." in iterative_prompt


def test_both_methods_record_the_same_model_id(tmp_path: Path) -> None:
    pinned = load_defaults().model.model_id
    iterative, _ = _run(tmp_path / "i", [_finish()])
    one_shot, _ = _run(tmp_path / "o", [FakeTurn(text=_abstain_json())], method="one_shot")
    assert iterative.model_id == pinned  # type: ignore[attr-defined]
    assert one_shot.model_id == pinned  # type: ignore[attr-defined]
    assert iterative.sampling["reasoning_effort"] == "low"  # type: ignore[attr-defined]
    assert one_shot.sampling["temperature"] is None  # type: ignore[attr-defined]


def test_held_out_and_missing_key_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["run", "--case", "H01", "--method", "iterative", "--model", "fake"]) == 2
    assert "allow-heldout" in capsys.readouterr().err
    assert main(["evaluate", "--split", "heldout", "--method", "both", "--model", "fake"]) == 2
    assert main(["run", "--case", "X00", "--method", "iterative", "--repeat", "1"]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    monkeypatch.chdir(tmp_path)
    assert main(["validate-cases", "--split", "probe"]) == 0


def test_fake_cli_does_not_copy_the_key_into_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-stored")
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--case", "X00", "--method", "one_shot", "--model", "fake", "--fake-script", "abstain", "--runs", "runs"]) == 0
    blob = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "runs").rglob("*") if path.is_file())
    assert "sk-should-not-be-stored" not in blob


def test_verdict_rules() -> None:
    passed_suite = SuiteOutcome(passed=True, integrity_ok=True, collected=2, executed=2, exit_code=0)
    failed_suite = SuiteOutcome(failed=True, integrity_ok=True, exit_code=1)
    timeout_suite = SuiteOutcome(timed_out=True, failed=True, integrity_ok=True)
    tampered = SuiteOutcome(passed=True, integrity_ok=False, exit_code=0)
    assert decide_verdict(kind="bug", rejected_reason="empty source diff on a bug case", visible=None, protected=None)[0] == "rejected"
    assert decide_verdict(kind="bug", rejected_reason=None, visible=passed_suite, protected=passed_suite)[0] == "passed"
    assert decide_verdict(kind="bug", rejected_reason=None, visible=passed_suite, protected=failed_suite)[0] == "failed"
    assert decide_verdict(kind="bug", rejected_reason=None, visible=timeout_suite, protected=passed_suite)[0] == "failed"
    assert decide_verdict(kind="bug", rejected_reason=None, visible=tampered, protected=passed_suite)[0] == "rejected"
    assert decide_verdict(kind="bug", rejected_reason=None, visible=passed_suite, protected=None)[0] == "infra_error"
    assert decide_verdict(kind="probe", rejected_reason=None, visible=passed_suite, protected=None)[0] is None


def _real_x00_patch(fixed: bool) -> str:
    from repair_agent.cases import materialize_workspace
    from repair_agent.diffs import unified_diff
    import tempfile

    case = load_case("X00")
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "work"
        materialize_workspace(workspace, case)
        source = (workspace / "order_service" / "example_ops.py").read_text(encoding="utf-8")
    replacement = '"cancelled": "Cancelled"' if fixed else '"cancelled": "Cancellled"'
    updated = source.replace('"cancelled": "Canceled"', replacement, 1)
    return unified_diff("order_service/example_ops.py", source, updated)


def _abstain_json() -> str:
    return json.dumps(
        {
            "claim": "unresolved",
            "summary": "No change.",
            "limitations": [],
            "patch": "",
        }
    )
