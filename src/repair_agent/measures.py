"""Section 11 measures, shared by the evaluation report and the viewer.

Counts are raw attempt counts. This module does not claim statistical
significance, and it does not score probe explanations.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

HELD_OUT_CASES = ("H01", "H02", "H03", "H04")
DEV_CASES = tuple(f"D0{index}" for index in range(1, 9))
PROBE_CASES = ("P01", "P02", "P03")
METHODS = ("iterative", "one_shot")

PROTOCOL_NOTE = (
    "The held-out protocol is 4 cases, 2 methods, and 3 repetitions: 24 attempts. "
    "Each attempt is kept. Probes are 3 cases and 2 methods, scored separately, "
    "and they are outside the repair-rate denominator."
)

ABSTENTION_RUBRIC = (
    "Correct abstention is an empty source diff plus a claim of unresolved or "
    "insufficient_evidence, with an explanation that fits the probe. A generated "
    "reproduction test is not a source change. Explanation fit is reviewed by "
    "hand and is not scored in this table. The mechanical column records only "
    "the empty diff and the claim."
)

LIMITATIONS = (
    "No statistical significance is claimed. With four held-out tasks, repeated runs measure model variability on this service rather than broad task diversity.",
    "These results are not a claim about software repair in general, and they are not a comparison with commercial agents.",
    "D04 and H02 both cover create_order idempotency. H02 is an unseen related behavior on the same function. That overlap is within-service generalization.",
    "Development results support debugging. They are not the held-out score. Example case X00 is a public demonstration and is outside the repair-rate denominator.",
    "Probe explanations are reviewed manually. The abstention table records the mechanical part of the rubric only.",
    "Docker limits reduce exposure for this owned fixture. They are not a strong boundary against hostile arbitrary code. The patch policy is a curated-task filter, not a malware detector.",
    "Protected tests can be inspected by code running in the evaluator container. Host-side hashes and fixed test discovery are the checks this release uses.",
    "Cost figures are estimates from the dated prices stored on each attempt. They are not invoices. A missing cache split is labeled unavailable. A scripted fake model is labeled not applicable and is not priced.",
    "A repaired claim stays a repaired claim when the evaluator records failed or rejected. That pair is a false repair claim.",
)


@dataclass(frozen=True)
class Attempt:
    """One finished attempt, reduced to the fields the measures use."""

    run_id: str
    case_id: str
    split: str
    method: str
    repetition: int
    status: str
    agent_claim: str | None
    verification: str | None
    summary: str
    visible_passed: bool | None
    protected_passed: bool | None
    input_tokens: int
    output_tokens: int
    tool_calls: int
    elapsed_seconds: float
    estimated_cost_usd: float | None
    cost_label: str
    pricing: dict[str, Any]
    model_id: str | None
    provider: str | None
    controller_commit: str | None
    sampling: dict[str, Any]
    budgets: dict[str, Any]
    stop_reason: str | None
    empty_source: bool
    source_files_changed: tuple[str, ...] = ()

    @property
    def false_claim(self) -> bool:
        return self.agent_claim == "repaired" and self.verification in {"failed", "rejected"}

    @property
    def regression(self) -> bool:
        """Ordinary suite failed. Protected failures are a separate count."""
        return self.visible_passed is False

    @property
    def protected_failure(self) -> bool:
        return self.protected_passed is False

    @property
    def eligible(self) -> bool:
        return self.status == "completed" and self.verification in {"passed", "failed", "rejected"}

    @property
    def infra_error(self) -> bool:
        return self.status == "infra_error" or self.verification == "infra_error"

    @property
    def mechanical_abstention(self) -> bool:
        return self.empty_source and self.agent_claim in {"unresolved", "insufficient_evidence"}


@dataclass
class MethodRate:
    method: str
    passed: int
    eligible: int
    infra_errors: int


@dataclass
class CoverageRow:
    case_id: str
    iterative: str
    one_shot: str


@dataclass
class EfficiencyRow:
    method: str
    measure: str
    median: str
    low: str
    high: str
    count: int


@dataclass
class SplitSummary:
    split: str
    title: str
    note: str
    attempts: list[Attempt] = field(default_factory=list)
    rates: list[MethodRate] = field(default_factory=list)
    passed: int = 0
    eligible: int = 0
    infra_errors: list[Attempt] = field(default_factory=list)
    excluded: list[Attempt] = field(default_factory=list)
    coverage: list[CoverageRow] = field(default_factory=list)
    full_success: list[str] = field(default_factory=list)
    any_success: list[str] = field(default_factory=list)
    false_claims: list[Attempt] = field(default_factory=list)
    regressions: list[Attempt] = field(default_factory=list)
    protected_failures: list[Attempt] = field(default_factory=list)
    efficiency: list[EfficiencyRow] = field(default_factory=list)


def split_of(case_id: str) -> str:
    if case_id in DEV_CASES:
        return "dev"
    if case_id in HELD_OUT_CASES:
        return "heldout"
    if case_id in PROBE_CASES:
        return "probe"
    if case_id == "X00":
        return "example"
    return "other"


def method_label(method: str) -> str:
    if method == "one_shot":
        return "one-shot"
    return method


def summarize(attempts: list[Attempt], split: str) -> SplitSummary:
    """Measures for one split. Probe splits skip the repair rate."""
    selected = [item for item in attempts if item.split == split]
    selected.sort(key=lambda item: (item.case_id, item.method, item.repetition, item.run_id))
    summary = SplitSummary(split=split, title=_title(split), note=_note(split), attempts=selected)
    if split == "probe":
        summary.efficiency = _efficiency(selected)
        return summary
    summary.infra_errors = [item for item in selected if item.infra_error]
    summary.excluded = [item for item in selected if not item.eligible and not item.infra_error]
    summary.rates = [_rate(selected, method) for method in METHODS]
    summary.passed = sum(rate.passed for rate in summary.rates)
    summary.eligible = sum(rate.eligible for rate in summary.rates)
    summary.coverage = _coverage(selected, _cases_for(split, selected))
    summary.full_success = _coverage_lines(selected, predicate=lambda passed, total: total == 3 and passed == 3)
    summary.any_success = _coverage_lines(selected, predicate=lambda passed, total: total == 3 and passed >= 1)
    summary.false_claims = [item for item in selected if item.false_claim]
    summary.regressions = [item for item in selected if item.regression]
    summary.protected_failures = [item for item in selected if item.protected_failure]
    summary.efficiency = _efficiency([item for item in selected if item.status == "completed"])
    return summary


def pricing_note(attempts: list[Attempt]) -> str:
    """Dated price assumption taken from the attempts themselves."""
    scripted = [item for item in attempts if item.cost_label == "not_applicable"]
    if attempts and len(scripted) == len(attempts):
        return (
            "Every attempt in this set used a scripted fake model. "
            "Estimated cost is not applicable. No hosted-model price was applied."
        )
    seen: list[dict[str, Any]] = []
    for attempt in attempts:
        if attempt.cost_label == "not_applicable":
            continue
        pricing = attempt.pricing
        if pricing and pricing not in seen:
            seen.append(pricing)
    if not seen:
        return (
            "No attempt in this set recorded a price configuration. "
            "Estimated cost is unavailable."
        )
    lines = []
    for pricing in seen:
        as_of = pricing.get("as_of", "an unstated date")
        currency = pricing.get("currency", "USD")
        lines.append(
            f"Prices dated {as_of}: {currency} {pricing.get('input_per_million')} per million input tokens, "
            f"{pricing.get('cached_input_per_million')} per million cached input tokens, "
            f"{pricing.get('output_per_million')} per million output tokens."
        )
    lines.append(
        "Cached input uses the cached rate when the provider reported a cache split. "
        "Otherwise the attempt's cost is unavailable. These are estimates, not invoices."
    )
    if scripted:
        lines.append("Scripted attempts are labeled not applicable and are left out of the cost range.")
    return " ".join(lines)


def _title(split: str) -> str:
    return {
        "heldout": "Held-out results",
        "dev": "Development results",
        "probe": "Abstention probes",
        "example": "Example case",
    }.get(split, split)


def _note(split: str) -> str:
    if split == "dev":
        return "These are development results from D01–D08. They are not the held-out score."
    if split == "probe":
        return ABSTENTION_RUBRIC
    if split == "example":
        return "Example case X00 is a public demonstration. It is outside the repair-rate denominator."
    if split == "heldout":
        return PROTOCOL_NOTE
    return ""


def _cases_for(split: str, attempts: list[Attempt]) -> list[str]:
    known = list(
        {"heldout": HELD_OUT_CASES, "dev": DEV_CASES, "example": ("X00",), "probe": PROBE_CASES}.get(split, ())
    )
    present = {item.case_id for item in attempts}
    extras = sorted(present - set(known))
    return known + extras


def _rate(attempts: list[Attempt], method: str) -> MethodRate:
    rows = [item for item in attempts if item.method == method]
    eligible = [item for item in rows if item.eligible]
    return MethodRate(
        method=method,
        passed=sum(1 for item in eligible if item.verification == "passed"),
        eligible=len(eligible),
        infra_errors=sum(1 for item in rows if item.infra_error),
    )


def _coverage(attempts: list[Attempt], case_ids: list[str]) -> list[CoverageRow]:
    rows = []
    for case_id in case_ids:
        cells = {}
        for method in METHODS:
            matched = [
                item
                for item in attempts
                if item.case_id == case_id and item.method == method and item.eligible
            ]
            passed = sum(1 for item in matched if item.verification == "passed")
            cells[method] = "—" if not matched else f"{passed}/{len(matched)}"
        rows.append(CoverageRow(case_id=case_id, iterative=cells["iterative"], one_shot=cells["one_shot"]))
    return rows


def _coverage_lines(attempts: list[Attempt], *, predicate) -> list[str]:
    lines = []
    cases = sorted({item.case_id for item in attempts})
    for case_id in cases:
        for method in METHODS:
            matched = [
                item
                for item in attempts
                if item.case_id == case_id and item.method == method and item.eligible
            ]
            passed = sum(1 for item in matched if item.verification == "passed")
            if predicate(passed, len(matched)):
                lines.append(f"{case_id} {method_label(method)} {passed}/{len(matched)}")
    return lines


def _efficiency(attempts: list[Attempt]) -> list[EfficiencyRow]:
    rows: list[EfficiencyRow] = []
    specs = (
        ("elapsed seconds", lambda item: item.elapsed_seconds, _seconds),
        ("input tokens", lambda item: item.input_tokens, _count),
        ("output tokens", lambda item: item.output_tokens, _count),
        ("tool calls", lambda item: item.tool_calls, _count),
    )
    for method in METHODS:
        group = [item for item in attempts if item.method == method]
        if not group:
            continue
        for label, getter, fmt in specs:
            values = [float(getter(item)) for item in group]
            median, low, high = _range(values, fmt)
            rows.append(EfficiencyRow(method, label, median, low, high, len(values)))
        priced = [
            item.estimated_cost_usd
            for item in group
            if item.cost_label == "estimated" and item.estimated_cost_usd is not None
        ]
        scripted = [item for item in group if item.cost_label == "not_applicable"]
        if priced:
            median, low, high = _range([float(value) for value in priced], _dollars)
            rows.append(EfficiencyRow(method, "estimated cost (USD)", median, low, high, len(priced)))
        elif scripted and len(scripted) == len(group):
            rows.append(
                EfficiencyRow(
                    method,
                    "estimated cost (USD)",
                    "not applicable",
                    "not applicable",
                    "not applicable",
                    len(scripted),
                )
            )
        else:
            rows.append(
                EfficiencyRow(method, "estimated cost (USD)", "unavailable", "unavailable", "unavailable", 0)
            )
    return rows


def _range(values: list[float], fmt) -> tuple[str, str, str]:
    if not values:
        return "—", "—", "—"
    ordered = sorted(values)
    middle = statistics.median(ordered)
    return fmt(middle), fmt(ordered[0]), fmt(ordered[-1])


def _seconds(value: float) -> str:
    return f"{value:.3f}"


def _count(value: float) -> str:
    return str(int(round(value)))


def _dollars(value: float) -> str:
    return f"{value:.6f}"
