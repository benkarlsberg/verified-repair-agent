"""Write the evaluation report from local run bundles.

The report reads ``result.json`` files. It does not copy evaluator logs into
the markdown. Development results stay in their own section.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from repair_agent.measures import (
    LIMITATIONS,
    Attempt,
    SplitSummary,
    method_label,
    pricing_note,
    split_of,
    summarize,
)
from repair_agent.schemas import RunResult


class ReportError(Exception):
    """The report could not be written."""


def write_report(runs: Path, output: Path) -> str:
    """Render ``runs`` to ``output`` and return the markdown."""
    if not runs.is_dir():
        raise ReportError(f"runs directory is missing: {runs.name}")
    attempts, exclusions = load_attempts(runs)
    text = render_report(attempts, exclusions)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    return text


def load_attempts(runs: Path) -> tuple[list[Attempt], list[str]]:
    """Load finished results. Incomplete directories are named, not measured."""
    attempts: list[Attempt] = []
    exclusions: list[str] = []
    for run_dir in sorted(path for path in runs.iterdir() if path.is_dir()):
        loaded = _load_one(run_dir)
        if isinstance(loaded, Attempt):
            attempts.append(loaded)
        else:
            exclusions.append(f"{run_dir.name}: {loaded}")
    return attempts, exclusions


def render_report(attempts: list[Attempt], exclusions: list[str] | None = None) -> str:
    """Markdown for the held-out protocol, with development results kept apart."""
    exclusions = exclusions or []
    heldout = summarize(attempts, "heldout")
    dev = summarize(attempts, "dev")
    probes = summarize(attempts, "probe")
    example = summarize(attempts, "example")
    parts = [
        "# Evaluation report",
        "",
        "These figures are computed from recorded run bundles. The evaluator's verdict is independent of the agent's claim.",
        "",
        "No statistical significance is claimed. These counts are not a measure of general software repair, and they are not a comparison with commercial agents.",
        "",
        "## Setup recorded on the attempts",
        "",
        _setup(attempts),
        "",
        _pricing_block(attempts),
        "",
        _exclusions_block(exclusions),
        "",
        _split_section(heldout),
        "",
        "## Quoted attempts",
        "",
        _quotes(heldout),
        "",
        _split_section(dev),
        "",
        _split_section(probes),
        "",
        _split_section(example),
        "",
        "## Limitations",
        "",
        *(f"- {item}" for item in LIMITATIONS),
        "",
    ]
    return "\n".join(parts)


def _load_one(run_dir: Path) -> Attempt | str:
    path = run_dir / "result.json"
    if not path.is_file():
        return "missing result.json"
    try:
        result = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError):
        return "result.json is not a complete RunResult"
    if not result.finished_at_utc:
        return "result.json has no finished_at_utc"
    return Attempt(
        run_id=result.run_id,
        case_id=result.case_id,
        split=split_of(result.case_id),
        method=result.method,
        repetition=result.repetition,
        status=result.status,
        agent_claim=result.agent_claim,
        verification=result.verification,
        summary=result.summary,
        visible_passed=result.visible_passed,
        protected_passed=result.protected_passed,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        tool_calls=result.tool_calls,
        elapsed_seconds=result.elapsed_seconds,
        estimated_cost_usd=result.estimated_cost_usd,
        cost_label=result.cost_label,
        pricing=dict(result.pricing),
        model_id=result.model_id,
        provider=result.provider,
        controller_commit=result.controller_commit,
        sampling=dict(result.sampling),
        budgets=dict(result.budgets),
        stop_reason=result.stop_reason,
        empty_source=_empty_source(run_dir, result),
        source_files_changed=tuple(result.source_files_changed),
    )


def _empty_source(run_dir: Path, result: RunResult) -> bool:
    if result.source_files_changed:
        return False
    patch = run_dir / "final.patch"
    if patch.is_file() and patch.read_text(encoding="utf-8").strip():
        return False
    return True


def _setup(attempts: list[Attempt]) -> str:
    if not attempts:
        return "No finished attempts were found."
    models = sorted({item.model_id or "unrecorded" for item in attempts})
    providers = sorted({item.provider or "unrecorded" for item in attempts})
    commits = sorted({(item.controller_commit or "unrecorded")[:12] for item in attempts})
    return "\n".join(
        [
            f"- Model: {', '.join(models)}",
            f"- Provider: {', '.join(providers)}",
            f"- Controller commit: {', '.join(commits)}",
            f"- Finished attempts: {len(attempts)}",
            f"- Held-out attempts in this directory: {sum(1 for item in attempts if item.split == 'heldout')}",
        ]
    )


def _pricing_block(attempts: list[Attempt]) -> str:
    return "Pricing assumption: " + pricing_note(attempts)


def _exclusions_block(exclusions: list[str]) -> str:
    if not exclusions:
        return "Incomplete bundles excluded from the tables: none."
    lines = ["Incomplete bundles excluded from the tables:", ""]
    lines.extend(f"- {item}" for item in exclusions)
    return "\n".join(lines)


def _split_section(summary: SplitSummary) -> str:
    lines = [f"## {summary.title}", "", summary.note, ""]
    if summary.split == "probe":
        lines.extend(_probe_table(summary))
        lines.extend(["", "### Efficiency", "", _efficiency_table(summary)])
        return "\n".join(lines)
    lines.extend(
        [
            "### Verified repairs",
            "",
            "Passed attempts over eligible completed attempts. Infrastructure errors are counted beside the rate and are not part of the denominator.",
            "",
            _rate_table(summary),
            "",
            _infra_block(summary),
            "",
            "### Case coverage",
            "",
            _coverage_table(summary),
            "",
            _coverage_flags(summary),
            "",
            "### Attempt matrix",
            "",
            _matrix(summary),
            "",
            "### False repair claims",
            "",
            "A false repair claim is an agent claim of repaired with an evaluator verdict of failed or rejected.",
            "",
            _attempt_table(summary.false_claims, empty="No false repair claims in this split."),
            "",
            "### Regressions",
            "",
            "A regression is a final patch that fails the original ordinary suite. Protected-suite failures are in the next table and are not counted here.",
            "",
            _attempt_table(summary.regressions, empty="No ordinary-suite regressions in this split."),
            "",
            "### Protected-suite failures",
            "",
            _attempt_table(summary.protected_failures, empty="No protected-suite failures in this split."),
            "",
            "### Efficiency",
            "",
            "Median and range over completed attempts in this split.",
            "",
            _efficiency_table(summary),
        ]
    )
    return "\n".join(lines)


def _rate_table(summary: SplitSummary) -> str:
    rows = [
        "| Method | Passed | Eligible completed | Infrastructure errors |",
        "| --- | --- | --- | --- |",
    ]
    for rate in summary.rates:
        rows.append(
            f"| {method_label(rate.method)} | {rate.passed} | {rate.eligible} | {rate.infra_errors} |"
        )
    rows.append(f"| total | {summary.passed} | {summary.eligible} | {len(summary.infra_errors)} |")
    return "\n".join(rows)


def _infra_block(summary: SplitSummary) -> str:
    if not summary.infra_errors and not summary.excluded:
        return "Infrastructure errors: 0. Other exclusions in this split: 0."
    lines = []
    if summary.infra_errors:
        lines.append("Infrastructure errors:")
        lines.append("")
        lines.append(_attempt_table(summary.infra_errors, empty=""))
    else:
        lines.append("Infrastructure errors: 0.")
    if summary.excluded:
        lines.append("")
        lines.append("Completed attempts excluded because verification was missing:")
        lines.append("")
        lines.append(_attempt_table(summary.excluded, empty=""))
    return "\n".join(lines)


def _coverage_table(summary: SplitSummary) -> str:
    rows = [
        "| Case | Iterative | One-shot |",
        "| --- | --- | --- |",
    ]
    for row in summary.coverage:
        rows.append(f"| {row.case_id} | {row.iterative} | {row.one_shot} |")
    if len(rows) == 2:
        rows.append("| — | — | — |")
    return "\n".join(rows)


def _coverage_flags(summary: SplitSummary) -> str:
    full = ", ".join(summary.full_success) or "none"
    partial = ", ".join(summary.any_success) or "none"
    return "\n".join(
        [
            f"Cases with 3/3 on a method: {full}.",
            f"Cases with at least 1/3 on a method: {partial}.",
            "Those two lines count a method only when that case has exactly three eligible attempts.",
        ]
    )


def _matrix(summary: SplitSummary) -> str:
    if not summary.attempts:
        return "No attempts in this split."
    rows = [
        "| Case | Method | Repetition | Claim | Verification | Ordinary tests | Protected tests |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summary.attempts:
        rows.append(
            "| "
            + " | ".join(
                [
                    item.case_id,
                    method_label(item.method),
                    str(item.repetition),
                    _word(item.agent_claim),
                    _word(item.verification),
                    _bool_word(item.visible_passed),
                    _bool_word(item.protected_passed),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _attempt_table(attempts: list[Attempt], *, empty: str) -> str:
    if not attempts:
        return empty
    rows = [
        "| Case | Method | Repetition | Claim | Verification | Summary |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in attempts:
        rows.append(
            "| "
            + " | ".join(
                [
                    item.case_id,
                    method_label(item.method),
                    str(item.repetition),
                    _word(item.agent_claim),
                    _word(item.verification),
                    _cell(item.summary),
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _probe_table(summary: SplitSummary) -> list[str]:
    if not summary.attempts:
        return ["No probe attempts in this directory."]
    rows = [
        "| Case | Method | Repetition | Empty source diff | Claim | Mechanical abstention | Summary |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summary.attempts:
        rows.append(
            "| "
            + " | ".join(
                [
                    item.case_id,
                    method_label(item.method),
                    str(item.repetition),
                    "yes" if item.empty_source else "no",
                    _word(item.agent_claim),
                    "yes" if item.mechanical_abstention else "no",
                    _cell(item.summary),
                ]
            )
            + " |"
        )
    return rows


def _efficiency_table(summary: SplitSummary) -> str:
    if not summary.efficiency:
        return "No completed attempts to summarize."
    rows = [
        "| Method | Measure | Median | Min | Max | Attempts |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary.efficiency:
        rows.append(
            f"| {method_label(row.method)} | {row.measure} | {row.median} | {row.low} | {row.high} | {row.count} |"
        )
    return "\n".join(rows)


def _quotes(summary: SplitSummary) -> str:
    successes = [item for item in summary.attempts if item.verification == "passed"]
    failures = [
        item
        for item in summary.attempts
        if item.verification in {"failed", "rejected"} or item.agent_claim in {"unresolved", "insufficient_evidence"}
    ]
    lines = [
        "Quotes below are held-out attempts only. Development runs are not used as substitutes.",
        "",
    ]
    if successes:
        item = successes[0]
        lines.append(f"One passed attempt: {item.case_id} {method_label(item.method)} repetition {item.repetition}.")
        lines.append("")
        lines.append(_cell(item.summary, limit=400) or "The attempt recorded an empty summary.")
    else:
        lines.append("No passed held-out attempt is in this directory.")
    lines.append("")
    shown = failures[:2]
    if not shown:
        lines.append("No failed, rejected, or abstaining held-out attempt is in this directory.")
        return "\n".join(lines)
    lines.append("Failure notes:")
    lines.append("")
    for item in shown:
        lines.append(
            f"- {item.case_id} {method_label(item.method)} repetition {item.repetition}, "
            f"claim {_word(item.agent_claim)}, verdict {_word(item.verification)}: "
            f"{_cell(item.summary, limit=400) or 'empty summary.'}"
        )
    if len(shown) < 2:
        lines.append("- A second held-out failure was not in this directory.")
    return "\n".join(lines)


def _word(value: str | None) -> str:
    return value or "not recorded"


def _bool_word(value: bool | None) -> str:
    if value is True:
        return "passed"
    if value is False:
        return "failed"
    return "not recorded"


def _cell(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split()).replace("|", "/")
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
