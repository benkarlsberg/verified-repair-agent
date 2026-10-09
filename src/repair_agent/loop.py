"""Iterative repair controller and one-shot baseline.

The evaluator runs only after ``model.close()``. Its logs are written by
``evaluate_patch`` and are not appended to the model conversation.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from repair_agent.artifacts import (
    Clock,
    RunBundle,
    SystemClock,
    controller_commit,
    format_utc,
    sha256_bytes,
    sha256_text,
)
from repair_agent.cases import LoadedCase
from repair_agent.config import Budgets, Defaults, Pricing, load_contract, load_defaults
from repair_agent.diffs import diff_against_snapshot, snapshot_sources
from repair_agent.evaluate import EvaluationRecord, evaluate_patch
from repair_agent.model import (
    MissingAPIKeyError,
    ModelClient,
    ModelClosedError,
    ModelIdentityError,
    ModelProviderError,
    ModelTransportError,
    ModelTurn,
    Usage,
    estimate_cost,
    estimate_tokens,
    items_for_input,
    one_shot_text_format,
    redact_items,
)
from repair_agent.patch_policy import UNIFIED_DIFF_EXAMPLE
from repair_agent.runner import Runner
from repair_agent.schemas import RunResult
from repair_agent.tools import ToolOutcome, ToolSurface, parse_tool_arguments, tool_schemas

FILE_CAP_BYTES = 20 * 1024
VISIBLE_LOG_CAP = 64 * 1024
SUMMARY_CAP = 4000
FORMAT_RETRY = (
    "The previous response was not usable. "
    "Call tools with JSON arguments that match each tool schema. "
    "Do not answer with prose only."
)


@dataclass
class AttemptConfig:
    """Limits and sampling for one attempt. Both methods share the model id."""

    model_id: str
    reasoning_effort: str
    temperature: float | None
    top_p: float | None
    store: bool
    api: str
    budgets: Budgets
    pricing: Pricing
    max_changed_lines: int
    max_files: int
    request_timeout_seconds: float = 120
    runner_image: str = "verified-repair-agent-runner:fixture"

    @classmethod
    def from_defaults(cls, defaults: Defaults | None = None) -> AttemptConfig:
        loaded = defaults or load_defaults()
        return cls(
            model_id=loaded.model.model_id,
            reasoning_effort=loaded.model.reasoning_effort,
            temperature=loaded.model.temperature,
            top_p=loaded.model.top_p,
            store=loaded.model.store,
            api=loaded.model.api,
            budgets=loaded.budgets,
            pricing=loaded.pricing,
            max_changed_lines=loaded.patch_policy.max_changed_lines,
            max_files=loaded.patch_policy.max_files,
            request_timeout_seconds=loaded.model.request_timeout_seconds,
            runner_image=loaded.runner.image,
        )


@dataclass
class _Totals:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_known: bool = True
    reasoning_known: bool = True
    complete: bool = True

    def add(self, usage: Usage, *, estimated_input: int, max_output: int) -> None:
        if not usage.complete:
            self.complete = False
            self.cached_known = False
            self.reasoning_known = False
            self.input_tokens += estimated_input
            self.output_tokens += max_output
            return
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        if usage.cached_input_tokens is None:
            self.cached_known = False
        else:
            self.cached_input_tokens += usage.cached_input_tokens
        if usage.reasoning_tokens is None:
            self.reasoning_known = False
        else:
            self.reasoning_tokens += usage.reasoning_tokens

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class _Stop:
    reason: str
    status: str = "completed"
    claim: str | None = None
    summary: str = ""
    limitations: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    skip_evaluator: bool = False
    forced_verification: str | None = None
    forced_detail: str | None = None
    tool_calls_after: int = 0


@dataclass
class _RequestPlan:
    tools: list[dict[str, object]] | None = None
    estimated_input: int = 0
    finish_only: bool = False
    stop_reason: str | None = None


def run_attempt(
    case: LoadedCase,
    *,
    method: str,
    repetition: int,
    settings: AttemptConfig,
    model_factory: Callable[[Path], ModelClient],
    runs_root: Path,
    runner: Runner | None = None,
    clock: Clock | None = None,
    estimator: Callable[[object], int] | None = None,
    evaluate: Callable[..., EvaluationRecord] | None = None,
    contract: str | None = None,
) -> RunResult:
    """Run one fresh attempt and write its bundle under ``runs_root``."""
    if method not in {"iterative", "one_shot"}:
        raise ValueError(f"unknown method: {method}")
    active_clock = clock or SystemClock()
    started_mono = active_clock.monotonic()
    started_at = active_clock.utc_now()
    active_runner = runner or Runner(image=settings.runner_image, timeout_seconds=settings.budgets.test_timeout_seconds)
    token_estimate = estimator or estimate_tokens
    evaluator = evaluate or evaluate_patch
    contract_text = contract if contract is not None else load_contract()
    workspace_dir = tempfile.TemporaryDirectory(prefix=f"vra-{case.case_id}-")
    model: ModelClient | None = None
    try:
        workspace = Path(workspace_dir.name) / "work"
        from repair_agent.cases import materialize_workspace

        materialize_workspace(workspace, case)
        model = model_factory(workspace)
        if model.model_id != settings.model_id:
            raise ModelIdentityError(
                f"Refusing to continue: configured model is {settings.model_id}, "
                f"client is {model.model_id}."
            )
        run_id = _new_run_id()
        bundle = RunBundle(runs_root / run_id, active_clock)
        packet = build_case_packet(case, workspace, contract_text)
        items = initial_items(method, packet, list(case.record.allowed_paths))
        prompt_sha = sha256_text(json.dumps(redact_items(items), ensure_ascii=False, sort_keys=True))
        baseline = snapshot_sources(workspace, case.record.allowed_paths)
        surface = ToolSurface(
            workspace,
            case.record.allowed_paths,
            active_runner,
            max_changed_lines=settings.max_changed_lines,
            max_files=settings.max_files,
            max_source_patches=settings.budgets.max_source_patch_submissions,
            test_timeout_seconds=settings.budgets.test_timeout_seconds,
        )
        if method == "one_shot":
            surface.source_edits_allowed = True
        totals = _Totals()
        visible_logs: list[str] = []
        responses = 0
        tool_calls = 0
        format_repairs = 0
        reproduction_failed: bool | None = None
        reproduction_passed: bool | None = None
        stop: _Stop | None = None
        bundle.append(
            category="prompt",
            success=True,
            request_summary=f"method={method} items={len(items)}",
            response_summary=_clip(packet),
        )
        while stop is None:
            reason = _budget_block(
                clock=active_clock,
                started=started_mono,
                settings=settings,
                responses=responses,
                tool_calls=tool_calls,
            )
            if reason is not None:
                stop = _unresolved(reason)
                bundle.append(
                    category="budget",
                    success=False,
                    request_summary=reason,
                    response_summary="stopped before the next model request",
                )
                break
            plan = _request_plan(
                method=method,
                settings=settings,
                items=items,
                used_tokens=totals.total,
                estimator=token_estimate,
            )
            if plan.stop_reason is not None:
                stop = _unresolved(plan.stop_reason)
                bundle.append(
                    category="budget",
                    success=False,
                    request_summary=plan.stop_reason,
                    response_summary="stopped before the next model request",
                )
                break
            tools = plan.tools
            estimated = plan.estimated_input
            try:
                turn = model.complete(
                    items,
                    tools=tools,
                    max_output_tokens=settings.budgets.max_output_tokens_per_response,
                    text_format=one_shot_text_format() if method == "one_shot" else None,
                )
            except MissingAPIKeyError:
                raise
            except ModelIdentityError as exc:
                stop = _provider_failure("model_identity", exc, limitation="The provider returned a different model id.")
                bundle.append(
                    category="model_response",
                    success=False,
                    request_summary="model identity check",
                    response_summary=_clip(str(exc)),
                )
                break
            except ModelClosedError as exc:
                stop = _provider_failure("model_closed", exc)
                break
            except (ModelTransportError, ModelProviderError) as exc:
                reason_name = "transport" if isinstance(exc, ModelTransportError) else "provider"
                stop = _provider_failure(reason_name, exc)
                bundle.append(
                    category=reason_name,
                    success=False,
                    request_summary="provider request failed",
                    response_summary=_clip(str(exc)),
                )
                break
            except Exception as exc:
                stop = _provider_failure("provider", exc)
                bundle.append(
                    category="provider",
                    success=False,
                    request_summary="provider request failed",
                    response_summary=_clip(f"{type(exc).__name__}: {exc}"),
                )
                break
            responses += 1
            totals.add(
                turn.usage,
                estimated_input=estimated,
                max_output=settings.budgets.max_output_tokens_per_response,
            )
            items.extend(items_for_input(turn.replay_items))
            bundle.append(
                category="model_response",
                success=not turn.malformed,
                request_summary=_request_summary(items),
                response_summary=_response_summary(turn),
                duration_ms=None,
            )
            if method == "one_shot":
                stop = _finish_one_shot(turn, surface, bundle)
                break
            if plan.finish_only:
                stop = _consume_finish_only(
                    turn,
                    surface=surface,
                    bundle=bundle,
                    items=items,
                    tool_calls=tool_calls,
                    max_tool_calls=settings.budgets.max_tool_calls,
                )
                tool_calls = stop.tool_calls_after
                break
            parsed = _validated_calls(turn)
            if parsed is None:
                if format_repairs < 1:
                    format_repairs += 1
                    items.append({"role": "user", "content": FORMAT_RETRY})
                    bundle.append(
                        category="format_retry",
                        success=False,
                        request_summary="model output failed validation",
                        response_summary=FORMAT_RETRY,
                    )
                    continue
                stop = _unresolved("malformed_output")
                break
            for call, arguments in parsed:
                if tool_calls >= settings.budgets.max_tool_calls:
                    stop = _unresolved("tool_calls")
                    bundle.append(
                        category="budget",
                        success=False,
                        request_summary="tool_calls",
                        response_summary="stopped before another tool call",
                    )
                    break
                if call.name == "run_visible_tests" and _clock_would_exceed(
                    active_clock, started_mono, settings.budgets, extra=settings.budgets.test_timeout_seconds
                ):
                    stop = _unresolved("wall_clock")
                    bundle.append(
                        category="budget",
                        success=False,
                        request_summary="wall_clock",
                        response_summary="stopped before a visible test run",
                    )
                    break
                if arguments.get("__unknown__"):
                    outcome = surface.dispatch(call.name, {})
                else:
                    outcome = surface.dispatch(call.name, arguments)
                tool_calls += 1
                items.append(
                    {"type": "function_call_output", "call_id": call.call_id, "output": outcome.model_text}
                )
                bundle.append(
                    category="tool",
                    tool_name=call.name,
                    success=outcome.ok,
                    request_summary=_clip(call.raw_arguments),
                    response_summary=_clip(outcome.model_text),
                )
                if outcome.visible_output is not None:
                    visible_logs.append(outcome.visible_output)
                if outcome.reproduction_observed and outcome.tests_passed is not None:
                    if surface.source_patches_applied == 0 and reproduction_failed is None:
                        surface.source_edits_allowed = True
                        reproduction_failed = not outcome.tests_passed
                    elif surface.source_patches_applied > 0:
                        reproduction_passed = outcome.tests_passed
                        surface.source_edits_allowed = True
                if outcome.finished:
                    stop = _Stop(
                        reason="finish",
                        claim=outcome.claim,
                        summary=outcome.summary,
                        limitations=list(outcome.limitations),
                        evidence=list(outcome.evidence),
                    )
                    break
                if outcome.budget_stop:
                    stop = _unresolved(outcome.budget_stop)
                    break
            if stop is not None:
                break
        assert stop is not None
        model.close()
        bundle.append(
            category="model_closed",
            success=True,
            request_summary="model access closed",
            response_summary="evaluator has not run",
        )
        source_patch, changed = diff_against_snapshot(workspace, baseline)
        bundle.write_text("final.patch", source_patch)
        bundle.write_text("visible_tests.txt", _cap_log("\n\n".join(visible_logs)))
        stored_patch = (bundle.run_dir / "final.patch").read_bytes()
        evaluation = _evaluate_after_close(
            stop=stop,
            case=case,
            source_patch=source_patch,
            bundle=bundle,
            settings=settings,
            runner=active_runner,
            evaluator=evaluator,
        )
        finished = active_clock.utc_now()
        cached = totals.cached_input_tokens if totals.cached_known and totals.complete else None
        reasoning = totals.reasoning_tokens if totals.reasoning_known and totals.complete else None
        if totals.complete and totals.cached_known:
            cost, label = estimate_cost(
                input_tokens=totals.input_tokens,
                cached_input_tokens=totals.cached_input_tokens,
                output_tokens=totals.output_tokens,
                pricing=settings.pricing,
            )
        else:
            cost, label = None, "unavailable"
        result = RunResult(
            run_id=run_id,
            case_id=case.case_id,
            method=method,  # type: ignore[arg-type]
            repetition=repetition,
            status=stop.status,  # type: ignore[arg-type]
            agent_claim=stop.claim,  # type: ignore[arg-type]
            verification=evaluation.verification,
            summary=stop.summary,
            limitations=stop.limitations,
            evidence=stop.evidence,
            source_files_changed=changed,
            patch_sha256=sha256_bytes(stored_patch),
            visible_passed=evaluation.visible_passed,
            protected_passed=evaluation.protected_passed,
            input_tokens=totals.input_tokens,
            cached_input_tokens=cached,
            output_tokens=totals.output_tokens,
            reasoning_tokens=reasoning,
            tool_calls=tool_calls,
            patch_attempts=surface.source_patches_applied,
            elapsed_seconds=round(max(0.0, active_clock.monotonic() - started_mono), 3),
            estimated_cost_usd=cost,
            cost_label=label,  # type: ignore[arg-type]
            model_id=settings.model_id,
            provider=model.provider,
            controller_commit=controller_commit(),
            fixture_sha=case.record.fixture_sha,
            prompt_sha256=prompt_sha,
            manifest_sha256=case.manifest_sha256,
            evaluator_sha256=evaluation.evaluator_sha256,
            budgets=settings.budgets.model_dump(),
            sampling=_sampling(settings),
            pricing=settings.pricing.model_dump(),
            reproduction_failed_on_original=reproduction_failed,
            reproduction_passed_after_patch=reproduction_passed,
            stop_reason=stop.reason,
            started_at_utc=format_utc(started_at),
            finished_at_utc=format_utc(finished),
        )
        bundle.write_result(result)
        bundle.append(
            category="result",
            success=result.status == "completed",
            request_summary="result.json",
            response_summary=(
                f"agent_claim={result.agent_claim} verification={result.verification} "
                f"stop={result.stop_reason}"
            ),
        )
        return result
    finally:
        if model is not None:
            model.close()
        workspace_dir.cleanup()


def build_case_packet(case: LoadedCase, workspace: Path, contract: str) -> str:
    """Issue, contract, and capped workspace files. No oracle paths."""
    sections = []
    for relative in _packet_files(workspace):
        data = (workspace / relative).read_bytes()
        truncated = len(data) > FILE_CAP_BYTES
        text = data[:FILE_CAP_BYTES].decode("utf-8", errors="replace")
        marker = "\n[truncated]\n" if truncated else ""
        sections.append(f"## {relative}\n\n```\n{text}{marker}```")
    allowed = ", ".join(case.record.allowed_paths)
    return (
        f"case_id: {case.case_id}\n"
        f"allowed_paths: {allowed}\n\n"
        f"# Issue\n\n{case.issue_text.strip()}\n\n"
        f"# API contract\n\n{contract.strip()}\n\n"
        f"# Workspace files\n\n" + "\n\n".join(sections)
    )


def initial_items(method: str, packet: str, allowed_paths: list[str]) -> list[dict[str, str]]:
    system = iterative_instructions(allowed_paths) if method == "iterative" else one_shot_instructions(allowed_paths)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": packet},
    ]


def iterative_instructions(allowed_paths: list[str]) -> str:
    allowed = ", ".join(allowed_paths)
    return (
        "You repair one deterministic defect in a local Python order service. "
        "You only have this workspace.\n"
        f"You may edit these source files: {allowed}. "
        "You may add reproduction tests only as agent_tests/test_*.py. "
        "Existing tests and configuration are immutable.\n"
        "Write a reproduction and run the visible tests before you edit source. "
        "The reproduction should fail on the current code. After a source change, run the visible tests again.\n"
        "Call finish once your reproduction test and the visible tests pass. "
        "Do not keep investigating after that.\n"
        "Use only the provided tools. When you stop, call finish. "
        "claim is repaired, unresolved, or insufficient_evidence. "
        "Use repaired only when the reproduction failed before the edit and passed after, and the visible suite passed. "
        "Use unresolved when you do not have a fix you trust. "
        "Use insufficient_evidence when the report does not justify a code change.\n"
        "apply_patch accepts only a git unified diff, in this exact shape:\n"
        f"{UNIFIED_DIFF_EXAMPLE}\n"
        "Do not send *** Begin Patch or any other patch format.\n"
        "You will not receive an independent evaluation."
    )


def one_shot_instructions(allowed_paths: list[str]) -> str:
    allowed = ", ".join(allowed_paths)
    return (
        "You repair one deterministic defect in a local Python order service. "
        "You get one response. There are no tools and no test feedback.\n"
        f"The source diff may only touch: {allowed}.\n"
        "The response is a JSON object with keys claim, summary, limitations, and patch. "
        "claim is repaired, unresolved, or insufficient_evidence. "
        "patch is a git unified diff, or an empty string when you make no source change. "
        "limitations is an array of strings.\n"
        "A source patch must be a git unified diff in this exact shape:\n"
        f"{UNIFIED_DIFF_EXAMPLE}\n"
        "Do not send *** Begin Patch or any other patch format.\n"
        "You will not receive an independent evaluation."
    )


def _finish_one_shot(turn: ModelTurn, surface: ToolSurface, bundle: RunBundle) -> _Stop:
    from repair_agent.schemas import OneShotResponse
    from pydantic import ValidationError

    try:
        parsed = OneShotResponse.model_validate(json.loads(turn.text.strip()))
    except (json.JSONDecodeError, ValidationError):
        bundle.append(
            category="format_retry",
            success=False,
            request_summary="one-shot output",
            response_summary="malformed output rejected; no format retry",
        )
        return _Stop(
            reason="malformed_output",
            claim=None,
            summary="",
            limitations=["The one-shot response was not a valid JSON object."],
            forced_verification="rejected",
            forced_detail="one-shot response was not valid JSON",
        )
    if parsed.patch.strip():
        outcome = surface.apply_patch(parsed.patch)
        bundle.append(
            category="patch_policy",
            success=outcome.ok,
            request_summary=_clip(parsed.patch),
            response_summary=_clip(outcome.model_text),
        )
        if not outcome.ok and outcome.budget_stop is None:
            return _Stop(
                reason="patch_rejected",
                claim=parsed.claim,
                summary=parsed.summary,
                limitations=list(parsed.limitations),
                forced_verification="rejected",
                forced_detail="one-shot patch was rejected by policy",
            )
    return _Stop(
        reason="one_shot",
        claim=parsed.claim,
        summary=parsed.summary,
        limitations=list(parsed.limitations),
    )


def _evaluate_after_close(
    *,
    stop: _Stop,
    case: LoadedCase,
    source_patch: str,
    bundle: RunBundle,
    settings: AttemptConfig,
    runner: Runner,
    evaluator: Callable[..., EvaluationRecord],
) -> EvaluationRecord:
    if stop.skip_evaluator or stop.forced_verification is not None:
        record = EvaluationRecord(
            verification=stop.forced_verification,  # type: ignore[arg-type]
            visible_passed=None,
            protected_passed=None,
            detail=stop.forced_detail or stop.reason,
            evaluator_sha256=None,
        )
        bundle.write_text(
            "evaluator.json",
            json.dumps(
                {
                    "verification": record.verification,
                    "detail": record.detail,
                    "visible_output": "",
                    "protected_output": "",
                },
                indent=2,
            )
            + "\n",
        )
        bundle.write_text("evaluator.txt", "")
    else:
        record = evaluator(
            case=case,
            source_patch=source_patch,
            run_dir=bundle.run_dir,
            runner=runner,
            max_changed_lines=settings.max_changed_lines,
            max_files=settings.max_files,
        )
    bundle.append(
        category="evaluation",
        success=record.verification == "passed",
        request_summary="final source diff",
        response_summary=f"verification={record.verification}",
    )
    return record


def _validated_calls(turn: ModelTurn) -> list[tuple[object, dict[str, object]]] | None:
    from repair_agent.model import ToolCall
    from repair_agent.tools import TOOL_MODELS

    if turn.malformed or not turn.tool_calls:
        return None
    ready: list[tuple[ToolCall, dict[str, object]]] = []
    for call in turn.tool_calls:
        if call.name not in TOOL_MODELS:
            ready.append((call, {"__unknown__": True}))
            continue
        parsed = parse_tool_arguments(call.name, call.arguments)
        if parsed is None:
            return None
        ready.append((call, parsed))
    return ready


def _budget_block(
    *,
    clock: Clock,
    started: float,
    settings: AttemptConfig,
    responses: int,
    tool_calls: int,
) -> str | None:
    budgets = settings.budgets
    if _clock_would_exceed(clock, started, budgets, extra=0):
        return "wall_clock"
    if responses >= budgets.max_model_responses:
        return "model_responses"
    if tool_calls >= budgets.max_tool_calls:
        return "tool_calls"
    return None


def _request_plan(
    *,
    method: str,
    settings: AttemptConfig,
    items: list[dict[str, object]],
    used_tokens: int,
    estimator: Callable[[object], int],
) -> _RequestPlan:
    """Choose the next request, or stop before one that cannot fit.

    A normal iterative round uses every tool. When that round's estimated
    input plus the output cap does not fit in the remaining token budget,
    one finish-only call is reserved if it fits. Cached tokens are not
    subtracted: the estimator sees the input that will actually be sent.
    """
    remaining = settings.budgets.max_total_tokens - used_tokens
    output_cap = settings.budgets.max_output_tokens_per_response
    if method != "iterative":
        estimated = estimator({"input": items, "tools": None})
        if estimated + output_cap > remaining:
            return _RequestPlan(stop_reason="tokens")
        return _RequestPlan(tools=None, estimated_input=estimated)
    all_tools = tool_schemas()
    normal = estimator({"input": items, "tools": all_tools})
    if normal + output_cap <= remaining:
        return _RequestPlan(tools=all_tools, estimated_input=normal)
    finish_tools = [tool for tool in all_tools if tool.get("name") == "finish"]
    finish_estimate = estimator({"input": items, "tools": finish_tools})
    if finish_estimate + output_cap <= remaining:
        return _RequestPlan(tools=finish_tools, estimated_input=finish_estimate, finish_only=True)
    return _RequestPlan(stop_reason="tokens")


def _consume_finish_only(
    turn: ModelTurn,
    *,
    surface: ToolSurface,
    bundle: RunBundle,
    items: list[dict[str, object]],
    tool_calls: int,
    max_tool_calls: int,
) -> _Stop:
    """Accept only ``finish`` on the reserved last call, then stop."""
    parsed = _validated_calls(turn)
    if parsed:
        for call, arguments in parsed:
            if call.name != "finish":
                bundle.append(
                    category="tool",
                    tool_name=call.name,
                    success=False,
                    request_summary=_clip(call.raw_arguments),
                    response_summary="this turn allows only finish",
                )
                continue
            if tool_calls >= max_tool_calls:
                stopped = _unresolved("tool_calls")
                stopped.tool_calls_after = tool_calls
                bundle.append(
                    category="budget",
                    success=False,
                    request_summary="tool_calls",
                    response_summary="stopped before another tool call",
                )
                return stopped
            if arguments.get("__unknown__"):
                outcome = surface.dispatch(call.name, {})
            else:
                outcome = surface.dispatch(call.name, arguments)
            tool_calls += 1
            items.append({"type": "function_call_output", "call_id": call.call_id, "output": outcome.model_text})
            bundle.append(
                category="tool",
                tool_name=call.name,
                success=outcome.ok,
                request_summary=_clip(call.raw_arguments),
                response_summary=_clip(outcome.model_text),
            )
            if outcome.finished:
                stopped = _Stop(
                    reason="finish",
                    claim=outcome.claim,
                    summary=outcome.summary,
                    limitations=list(outcome.limitations),
                    evidence=list(outcome.evidence),
                    tool_calls_after=tool_calls,
                )
                return stopped
    stopped = _unresolved("tokens")
    stopped.tool_calls_after = tool_calls
    return stopped


def _provider_failure(reason: str, exc: BaseException, *, limitation: str | None = None) -> _Stop:
    """Record a provider or infrastructure failure without dropping the bundle."""
    detail = str(exc).strip() or reason
    note = limitation or detail
    return _Stop(
        reason=reason,
        status="infra_error",
        skip_evaluator=True,
        forced_verification="infra_error",
        forced_detail=detail,
        limitations=[note],
    )


def _clock_would_exceed(clock: Clock, started: float, budgets: Budgets, *, extra: float) -> bool:
    return clock.monotonic() - started + extra >= budgets.wall_clock_seconds


def _unresolved(reason: str) -> _Stop:
    return _Stop(
        reason=reason,
        claim="unresolved",
        summary=f"Stopped before a finished repair: {reason}.",
        limitations=[reason],
    )


def _packet_files(workspace: Path) -> list[str]:
    found: list[str] = []
    for root_name in ("order_service", "tests_visible"):
        root = workspace / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.suffix != ".py":
                continue
            if "__pycache__" in path.parts:
                continue
            found.append(path.relative_to(workspace).as_posix())
    return found


def _request_summary(items: list[dict[str, object]]) -> str:
    public = redact_items(items)  # type: ignore[arg-type]
    if not public:
        return "empty"
    last = public[-1]
    return _clip(f"{len(public)} items; last={json.dumps(last, ensure_ascii=False, default=str)}")


def _response_summary(turn: ModelTurn) -> str:
    public = redact_items(turn.replay_items)
    calls = [{"name": call.name, "arguments": call.raw_arguments} for call in turn.tool_calls]
    payload = {"text": turn.text, "tool_calls": calls, "items": public}
    return _clip(json.dumps(payload, ensure_ascii=False, default=str))


def _sampling(settings: AttemptConfig) -> dict[str, object]:
    return {
        "api": settings.api,
        "reasoning_effort": settings.reasoning_effort,
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "max_output_tokens": settings.budgets.max_output_tokens_per_response,
        "store": settings.store,
    }


def _clip(text: str, limit: int = SUMMARY_CAP) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _cap_log(text: str) -> str:
    if len(text.encode("utf-8")) <= VISIBLE_LOG_CAP:
        return text
    encoded = text.encode("utf-8")[:VISIBLE_LOG_CAP]
    return encoded.decode("utf-8", errors="ignore") + "\n[truncated]\n"


def _new_run_id() -> str:
    import uuid

    return str(uuid.uuid4())


def run_repetitions(
    case: LoadedCase,
    *,
    method: str,
    repeats: int,
    settings: AttemptConfig,
    model_factory: Callable[[Path], ModelClient],
    runs_root: Path,
    runner: Runner | None = None,
) -> list[RunResult]:
    """Independent repetitions. Each one gets a new workspace and conversation."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    return [
        run_attempt(
            case,
            method=method,
            repetition=index,
            settings=settings,
            model_factory=model_factory,
            runs_root=runs_root,
            runner=runner,
        )
        for index in range(1, repeats + 1)
    ]


def evaluate_split(
    split: str,
    *,
    method: str,
    repeats: int,
    allow_heldout: bool,
    settings: AttemptConfig,
    model_factory_for: Callable[[str], Callable[[Path], ModelClient]],
    runs_root: Path,
    runner: Runner | None = None,
) -> list[RunResult]:
    """Evaluate every case in a split. ``both`` alternates method order."""
    from repair_agent.cases import cases_for_split

    if method not in {"iterative", "one_shot", "both"}:
        raise ValueError(f"unknown method: {method}")
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    cases = cases_for_split(split, allow_heldout=allow_heldout)
    methods = ["iterative", "one_shot"] if method == "both" else [method]
    results: list[RunResult] = []
    for case_index, case in enumerate(cases):
        for repetition in range(1, repeats + 1):
            order = list(methods)
            if len(order) == 2 and (case_index + repetition) % 2 == 1:
                order.reverse()
            for item in order:
                results.append(
                    run_attempt(
                        case,
                        method=item,
                        repetition=repetition,
                        settings=settings,
                        model_factory=model_factory_for(item),
                        runs_root=runs_root,
                        runner=runner,
                    )
                )
    return results
