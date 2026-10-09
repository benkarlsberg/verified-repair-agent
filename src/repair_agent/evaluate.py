"""Independent evaluator.

This runs only after model access is closed. It rebuilds the buggy fixture,
applies the final source diff, and runs the ordinary suite plus the protected
suite. Detailed output is written into the run bundle and is not returned to
the caller as log text.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from repair_agent.cases import LoadedCase, materialize_workspace, tree_sha256
from repair_agent.patch_policy import PatchRejected, validate_patch
from repair_agent.runner import Runner, RunnerError, RunnerResult

Verification = Literal["passed", "failed", "rejected", "infra_error"]

_COLLECTED = re.compile(r"(\d+) tests? collected")
_COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")


@dataclass
class SuiteOutcome:
    passed: bool = False
    failed: bool = False
    timed_out: bool = False
    crashed: bool = False
    infra_error: str | None = None
    integrity_ok: bool = True
    collected: int | None = None
    executed: int | None = None
    exit_code: int | None = None
    output: str = ""


@dataclass
class EvaluationRecord:
    """Verdict fields only. Pytest text stays in the run directory."""

    verification: Verification | None
    visible_passed: bool | None
    protected_passed: bool | None
    detail: str
    evaluator_sha256: str | None


def evaluate_patch(
    *,
    case: LoadedCase,
    source_patch: str,
    run_dir: Path,
    runner: Runner | None = None,
    max_changed_lines: int = 200,
    max_files: int = 5,
) -> EvaluationRecord:
    """Evaluate ``source_patch`` in a fresh workspace and write private files."""
    active = runner or Runner()
    evaluator_sha = tree_sha256(case.protected_dir) if case.protected_dir is not None else None
    record, visible_output, protected_output, extra = _evaluate(
        case=case,
        source_patch=source_patch,
        runner=active,
        max_changed_lines=max_changed_lines,
        max_files=max_files,
        evaluator_sha=evaluator_sha,
    )
    document = {
        "verification": record.verification,
        "visible_passed": record.visible_passed,
        "protected_passed": record.protected_passed,
        "detail": record.detail,
        "evaluator_sha256": record.evaluator_sha256,
        "visible_output": visible_output,
        "protected_output": protected_output,
        **extra,
    }
    _write(run_dir / "evaluator.json", json.dumps(document, indent=2) + "\n")
    _write(run_dir / "evaluator.txt", protected_output if protected_output.endswith("\n") or protected_output == "" else protected_output + "\n")
    return record


def decide_verdict(
    *,
    kind: str,
    rejected_reason: str | None,
    visible: SuiteOutcome | None,
    protected: SuiteOutcome | None,
) -> tuple[Verification | None, str]:
    """Section 8 verdict rules. Probe abstentions are not repair passes."""
    if rejected_reason:
        return "rejected", rejected_reason
    if visible is None:
        return "infra_error", "ordinary tests did not run"
    if visible.infra_error:
        return "infra_error", "ordinary test runner failed to start"
    if not visible.integrity_ok or (protected is not None and not protected.integrity_ok):
        return "rejected", "evaluator integrity check failed"
    if _execution_failed(visible):
        return "failed", "ordinary tests failed"
    if kind == "probe":
        if visible.passed:
            return None, "probe has no repair verdict; source diff and claim are scored separately"
        return "failed", "ordinary tests failed"
    if protected is None:
        return "infra_error", "protected tests are not available"
    if protected.infra_error:
        return "infra_error", "protected test runner failed to start"
    if _execution_failed(protected):
        return "failed", "protected tests failed"
    if visible.passed and protected.passed and visible.integrity_ok and protected.integrity_ok:
        return "passed", "ordinary and protected tests passed"
    return "failed", "tests did not pass"


def _evaluate(
    *,
    case: LoadedCase,
    source_patch: str,
    runner: Runner,
    max_changed_lines: int,
    max_files: int,
    evaluator_sha: str | None,
) -> tuple[EvaluationRecord, str, str, dict[str, object]]:
    kind = case.kind
    if kind == "bug" and not source_patch.strip():
        record = EvaluationRecord("rejected", None, None, "empty source diff on a bug case", evaluator_sha)
        return record, "", "", {}
    with tempfile.TemporaryDirectory(prefix=f"vra-eval-{case.case_id}-") as tmp:
        workspace = Path(tmp) / "work"
        try:
            materialize_workspace(workspace, case)
        except subprocess.CalledProcessError:
            record = EvaluationRecord("infra_error", None, None, "could not recreate the buggy fixture", evaluator_sha)
            return record, "", "", {}
        if source_patch.strip():
            try:
                validate_patch(
                    source_patch,
                    allowed_paths=case.record.allowed_paths,
                    workspace=workspace,
                    max_changed_lines=max_changed_lines,
                    max_files=max_files,
                )
            except PatchRejected as exc:
                record = EvaluationRecord("rejected", None, None, f"patch rejected by policy ({exc.code})", evaluator_sha)
                return record, "", "", {}
            applied = subprocess.run(
                ["git", "apply", "--recount", "--whitespace=nowarn", "-"],
                input=source_patch.encode(),
                cwd=workspace,
                capture_output=True,
                check=False,
            )
            if applied.returncode != 0:
                record = EvaluationRecord("rejected", None, None, "final source diff did not apply", evaluator_sha)
                return record, "", "", {}
            if (workspace / "agent_tests").exists():
                shutil.rmtree(workspace / "agent_tests")
        protected_before = evaluator_sha
        visible_hash = tree_sha256(workspace / "tests_visible")
        try:
            visible = _run_suite(runner, workspace, ["tests_visible"], protected=None)
        except RunnerError:
            record = EvaluationRecord("infra_error", None, None, "ordinary test runner failed to start", evaluator_sha)
            return record, "", "", {}
        if tree_sha256(workspace / "tests_visible") != visible_hash:
            visible.integrity_ok = False
        protected: SuiteOutcome | None = None
        if kind == "bug":
            if case.protected_dir is None:
                verification, detail = decide_verdict(
                    kind=kind, rejected_reason=None, visible=visible, protected=None
                )
                record = EvaluationRecord(
                    verification, visible.passed, None, detail, evaluator_sha
                )
                return record, visible.output, "", _suite_extra(visible, None)
            protected_after_hash = tree_sha256(case.protected_dir)
            try:
                protected = _run_suite(runner, workspace, ["/protected"], protected=case.protected_dir)
            except RunnerError:
                record = EvaluationRecord(
                    "infra_error",
                    visible.passed,
                    None,
                    "protected test runner failed to start",
                    evaluator_sha,
                )
                return record, visible.output, "", _suite_extra(visible, None)
            if tree_sha256(case.protected_dir) != protected_before or protected_after_hash != protected_before:
                protected.integrity_ok = False
        verification, detail = decide_verdict(
            kind=kind, rejected_reason=None, visible=visible, protected=protected
        )
        visible_passed = True if visible.passed else False if visible.failed or visible.timed_out or visible.crashed else None
        protected_passed = None
        if protected is not None:
            protected_passed = (
                True if protected.passed else False if protected.failed or protected.timed_out or protected.crashed else None
            )
        record = EvaluationRecord(verification, visible_passed, protected_passed, detail, evaluator_sha)
        protected_output = "" if protected is None else protected.output
        return record, visible.output, protected_output, _suite_extra(visible, protected)


def _run_suite(
    runner: Runner,
    workspace: Path,
    targets: list[str],
    *,
    protected: Path | None,
) -> SuiteOutcome:
    collected_run = runner.run_pytest(
        workspace,
        targets=targets,
        protected_dir=protected,
        pytest_args=["--collect-only"],
    )
    if collected_run.infra_error:
        return SuiteOutcome(infra_error=collected_run.infra_error, output=_join(collected_run), integrity_ok=True)
    if collected_run.timed_out:
        return SuiteOutcome(timed_out=True, failed=True, output=_join(collected_run))
    collected = _collected_count(_join(collected_run))
    result = runner.run_pytest(workspace, targets=targets, protected_dir=protected)
    output = _join(result)
    outcome = SuiteOutcome(
        exit_code=result.exit_code,
        output=output,
        collected=collected,
        timed_out=result.timed_out,
        infra_error=result.infra_error,
    )
    if result.infra_error:
        return outcome
    counts = _summary_counts(output)
    if counts is not None:
        outcome.executed = sum(counts.values())
    tampered = result.tests_modified or result.output_truncated
    mismatch = (
        collected is not None
        and outcome.executed is not None
        and collected != outcome.executed
    )
    empty = collected == 0
    outcome.integrity_ok = not tampered and not mismatch and not empty
    if result.timed_out:
        outcome.failed = True
        outcome.passed = False
        return outcome
    if result.exit_code == 0 and outcome.integrity_ok:
        outcome.passed = True
        return outcome
    if result.exit_code == 0 and not outcome.integrity_ok:
        outcome.passed = False
        outcome.failed = False
        return outcome
    if result.exit_code == 1:
        outcome.failed = True
        return outcome
    outcome.crashed = True
    outcome.failed = True
    return outcome


def _execution_failed(suite: SuiteOutcome) -> bool:
    return suite.failed or suite.timed_out or suite.crashed


def _collected_count(output: str) -> int | None:
    if re.search(r"no tests collected", output):
        return 0
    match = _COLLECTED.search(output)
    if match is None:
        return None
    return int(match.group(1))


def _summary_counts(output: str) -> dict[str, int] | None:
    for line in reversed(output.splitlines()):
        if " in " not in line and "passed" not in line and "failed" not in line:
            continue
        counts: dict[str, int] = {}
        for number, name in _COUNT.findall(line):
            key = "errors" if name == "error" else name
            counts[key] = int(number)
        if counts:
            return counts
    return None


def _join(result: RunnerResult) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _suite_extra(visible: SuiteOutcome | None, protected: SuiteOutcome | None) -> dict[str, object]:
    def pack(suite: SuiteOutcome | None) -> dict[str, object] | None:
        if suite is None:
            return None
        return {
            "exit_code": suite.exit_code,
            "collected": suite.collected,
            "executed": suite.executed,
            "passed": suite.passed,
            "failed": suite.failed,
            "timed_out": suite.timed_out,
            "integrity_ok": suite.integrity_ok,
            "infra_error": suite.infra_error,
        }

    return {"visible": pack(visible), "protected": pack(protected)}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
