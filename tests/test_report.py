"""Evaluation report measures."""

from __future__ import annotations

from pathlib import Path

from repair_agent.cli import main
from repair_agent.report import render_report, write_report
from repair_agent.schemas import RunResult


def test_report_separates_splits_and_states_limits(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write(runs, case_id="H01", method="iterative", verification="passed", claim="repaired", repetition=1)
    _write(runs, case_id="H01", method="iterative", verification="failed", claim="repaired", repetition=2, visible=False)
    _write(
        runs,
        case_id="H02",
        method="one_shot",
        verification="failed",
        claim="repaired",
        repetition=1,
        visible=True,
        protected=False,
    )
    _write(runs, case_id="D01", method="iterative", verification="passed", claim="repaired")
    _write(
        runs,
        case_id="P01",
        method="iterative",
        verification=None,
        claim="insufficient_evidence",
        changed=[],
        summary="No reproduction. The report does not justify a change.",
    )
    _write(runs, case_id="D02", method="iterative", verification="infra_error", claim=None, status="infra_error")
    text = write_report(runs, tmp_path / "reports" / "evaluation.md")
    heldout = text.split("## Development results")[0]
    dev = text.split("## Development results")[1].split("## Abstention probes")[0]
    probes = text.split("## Abstention probes")[1].split("## Example case")[0]
    assert "1 / 1" in heldout or "| iterative | 1 | 2 |" in heldout
    assert "D01" not in heldout.split("### Verified repairs")[1].split("### Case coverage")[0]
    assert "These are development results" in dev
    assert "D01" in dev
    assert "H01" not in dev.split("### Case coverage")[1].split("### Attempt matrix")[0]
    assert "Mechanical abstention" in probes
    assert "insufficient_evidence" in probes
    assert "D04 and H02" in text
    assert "within-service generalization" in text
    assert "No statistical significance is claimed." in text
    assert "ordinary suite" in text.lower() or "Ordinary" in text
    assert "2026-10-09" in text
    assert "0.75" in text
    regression = text.split("### Regressions")[1].split("### Protected-suite failures")[0]
    protected = text.split("### Protected-suite failures")[1].split("### Efficiency")[0]
    assert "H01" in regression
    assert "H02" in protected
    assert "H02" not in regression


def test_false_claim_is_repaired_plus_failed_or_rejected() -> None:
    failed = _attempt("H03", verification="failed", claim="repaired")
    rejected = _attempt("H04", verification="rejected", claim="repaired")
    cautious = _attempt("H01", verification="passed", claim="unresolved")
    text = render_report([failed, rejected, cautious])
    claims = text.split("### False repair claims")[1].split("### Regressions")[0]
    assert "H03" in claims
    assert "H04" in claims
    assert "H01" not in claims


def test_efficiency_reports_median_and_range() -> None:
    attempts = [
        _attempt("D01", method="iterative", seconds=10, tokens_in=100, tokens_out=20, tools=4, cost=0.002),
        _attempt("D01", method="iterative", seconds=30, tokens_in=300, tokens_out=40, tools=8, cost=0.004, repetition=2),
    ]
    text = render_report(attempts)
    dev = text.split("## Development results")[1]
    assert "20.000" in dev
    assert "10.000" in dev
    assert "30.000" in dev
    assert "200" in dev
    assert "0.003000" in dev


def test_scripted_attempts_are_not_priced() -> None:
    attempt = _attempt(
        "X00",
        method="one_shot",
        cost=None,
        cost_label="not_applicable",
        model_id="scripted-fake",
        provider="fake",
    )
    text = render_report([attempt])
    example = text.split("## Example case")[1].split("## Limitations")[0]
    assert "| estimated cost (USD) | not applicable | not applicable | not applicable |" in example
    assert "Estimated cost is not applicable" in text
    assert "gpt-5.4-mini" not in text


def test_report_command_writes_the_file(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write(runs, case_id="X00", method="one_shot", verification="passed", claim="repaired")
    output = tmp_path / "reports" / "evaluation.md"
    code = main(["report", "--runs", str(runs), "--output", str(output)])
    assert code == 0
    body = output.read_text(encoding="utf-8")
    assert "## Example case" in body
    assert "X00" in body
    assert "No passed held-out attempt" in body


def _write(
    runs: Path,
    *,
    case_id: str,
    method: str,
    verification: str | None,
    claim: str | None,
    repetition: int = 1,
    visible: bool | None = True,
    protected: bool | None = True,
    changed: list[str] | None = None,
    summary: str = "Recorded summary.",
    status: str = "completed",
) -> None:
    run_id = f"{case_id}-{method}-{repetition}".lower()
    # Report ids are not required to be UUIDs. Keep them readable.
    run_dir = runs / run_id
    run_dir.mkdir(parents=True)
    result = _result(
        run_id=run_id,
        case_id=case_id,
        method=method,
        verification=verification,
        claim=claim,
        repetition=repetition,
        visible=visible,
        protected=protected,
        changed=changed,
        summary=summary,
        status=status,
    )
    (run_dir / "result.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if changed is not None and not changed:
        (run_dir / "final.patch").write_text("\n", encoding="utf-8")


def _attempt(
    case_id: str,
    *,
    method: str = "iterative",
    verification: str | None = "passed",
    claim: str | None = "repaired",
    seconds: float = 1,
    tokens_in: int = 10,
    tokens_out: int = 5,
    tools: int = 1,
    cost: float | None = 0.001,
    cost_label: str | None = None,
    model_id: str = "gpt-5.4-mini-2026-03-17",
    provider: str = "openai",
    repetition: int = 1,
):
    from repair_agent.measures import Attempt, split_of

    return Attempt(
        run_id=f"{case_id}-{repetition}",
        case_id=case_id,
        split=split_of(case_id),
        method=method,
        repetition=repetition,
        status="completed",
        agent_claim=claim,
        verification=verification,
        summary="A short explanation of the attempt.",
        visible_passed=True,
        protected_passed=verification == "passed",
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        tool_calls=tools,
        elapsed_seconds=seconds,
        estimated_cost_usd=cost,
        cost_label=cost_label if cost_label is not None else ("estimated" if cost is not None else "unavailable"),
        pricing={
            "as_of": "2026-10-09",
            "source": "https://developers.openai.com/api/docs/models/gpt-5.4-mini",
            "currency": "USD",
            "input_per_million": 0.75,
            "cached_input_per_million": 0.075,
            "output_per_million": 4.5,
        },
        model_id=model_id,
        provider=provider,
        controller_commit="abc123",
        sampling={},
        budgets={},
        stop_reason="finish",
        empty_source=False,
    )


def _result(**kwargs: object) -> RunResult:
    return RunResult(
        run_id=str(kwargs["run_id"]),
        case_id=str(kwargs["case_id"]),
        method=kwargs["method"],  # type: ignore[arg-type]
        repetition=int(kwargs["repetition"]),
        status=kwargs["status"],  # type: ignore[arg-type]
        agent_claim=kwargs["claim"],  # type: ignore[arg-type]
        verification=kwargs["verification"],  # type: ignore[arg-type]
        summary=str(kwargs["summary"]),
        source_files_changed=list(kwargs["changed"]) if kwargs["changed"] is not None else ["order_service/pricing.py"],
        visible_passed=kwargs["visible"],  # type: ignore[arg-type]
        protected_passed=kwargs["protected"],  # type: ignore[arg-type]
        input_tokens=12,
        output_tokens=3,
        elapsed_seconds=1.25,
        estimated_cost_usd=0.0002,
        cost_label="estimated",
        model_id="gpt-5.4-mini-2026-03-17",
        provider="openai",
        fixture_sha="c" * 64,
        pricing={
            "as_of": "2026-10-09",
            "input_per_million": 0.75,
            "cached_input_per_million": 0.075,
            "output_per_million": 4.5,
        },
        started_at_utc="2026-10-09T00:00:00Z",
        finished_at_utc="2026-10-09T00:00:02Z",
    )
