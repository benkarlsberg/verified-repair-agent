"""Command line for the repair agent.

``validate-cases``, ``freeze``, ``report``, and ``publish`` do not call a model.
``run`` and ``evaluate`` use ``OPENAI_API_KEY`` unless ``--model fake`` is set.
Held-out cases are refused unless ``--allow-heldout`` is passed. The replay
viewer is ``repair_agent.web:app`` and does not read a model key.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from repair_agent.cases import (
    CaseNotFound,
    HeldOutRefused,
    cases_for_split,
    format_report,
    load_case,
    validate_cases,
    write_freeze,
)
from repair_agent.cases import repo_root
from repair_agent.fake_scripts import FakeScriptError, build_fake_turns
from repair_agent.loop import AttemptConfig, evaluate_split, run_repetitions
from repair_agent.model import FakeModel, MissingAPIKeyError, ModelIdentityError, OpenAIModel
from repair_agent.publish import PublishError, publish
from repair_agent.report import ReportError, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repair-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-cases", help="Check manifests and bug/oracle fixtures")
    validate.add_argument("--split", required=True, choices=("all", "dev", "heldout", "probe", "example"))

    freeze = sub.add_parser("freeze", help="Hash manifests, the fixture, and private evaluator files")
    freeze.add_argument("--output", default="freeze.json")

    run = sub.add_parser("run", help="Run one case")
    _add_attempt_args(run)
    run.add_argument("--case", required=True)
    run.add_argument("--method", required=True, choices=("iterative", "one_shot"))
    run.add_argument("--repeat", type=int, default=1)

    evaluate = sub.add_parser("evaluate", help="Run every case in a split")
    _add_attempt_args(evaluate)
    evaluate.add_argument("--split", required=True, choices=("dev", "heldout", "probe", "example", "all"))
    evaluate.add_argument("--method", required=True, choices=("iterative", "one_shot", "both"))
    evaluate.add_argument("--repeats", type=int, default=1)

    report = sub.add_parser("report", help="Write the evaluation report from run bundles")
    report.add_argument("--runs", default="runs")
    report.add_argument("--output", default="reports/evaluation.md")

    publish_cmd = sub.add_parser("publish", help="Copy sanitized bundles into a public directory")
    publish_cmd.add_argument("--runs", default="runs")
    publish_cmd.add_argument("--output", default="public_runs")

    args = parser.parse_args(argv)
    try:
        if args.command == "validate-cases":
            result = validate_cases(args.split)
            sys.stdout.write(format_report(result))
            return 0 if result.ok else 1
        if args.command == "freeze":
            document = write_freeze(_output_path(args.output))
            fixture = document["fixture"]
            private = document["private_dir"]
            fixture_sha = fixture["sha256"] if isinstance(fixture, dict) else ""
            present = private["present"] if isinstance(private, dict) else False
            print(f"Wrote {args.output}")
            print(f"fixture {fixture_sha}")
            print("private evaluator files included" if present else "private evaluator files absent")
            return 0
        if args.command == "report":
            return _report(args)
        if args.command == "publish":
            return _publish(args)
        if args.command == "run":
            return _run(args)
        return _evaluate(args)
    except MissingAPIKeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except HeldOutRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (CaseNotFound, FakeScriptError, ModelIdentityError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _report(args: argparse.Namespace) -> int:
    try:
        write_report(_output_path(args.runs), _output_path(args.output))
    except ReportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Wrote {args.output}")
    return 0


def _publish(args: argparse.Namespace) -> int:
    try:
        result = publish(_output_path(args.runs), _output_path(args.output))
    except PublishError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for run_id, reason in result.incomplete:
        print(f"Refusing to export incomplete run {run_id}: {reason}", file=sys.stderr)
    print(f"Published {len(result.published)} run(s) to {args.output}")
    return 1 if result.incomplete else 0


def _add_attempt_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", choices=("openai", "fake"), default="openai")
    parser.add_argument(
        "--fake-script",
        default="abstain",
        choices=("abstain", "x00-pass", "x00-fail"),
        help="Script for --model fake. x00-pass and x00-fail only apply to example case X00.",
    )
    parser.add_argument(
        "--allow-heldout",
        action="store_true",
        help="Permit held-out cases. Development runs leave this off.",
    )
    parser.add_argument("--runs", default=None, help="Directory for run bundles. Default: runs/ in the repo.")


def _run(args: argparse.Namespace) -> int:
    if args.repeat < 1:
        print("--repeat must be at least 1", file=sys.stderr)
        return 2
    settings = AttemptConfig.from_defaults()
    _require_key_if_needed(args.model, settings)
    case = load_case(args.case, allow_heldout=args.allow_heldout)
    results = run_repetitions(
        case,
        method=args.method,
        repeats=args.repeat,
        settings=settings,
        model_factory=_factory(args.model, args.fake_script, args.method, settings),
        runs_root=_runs_root(args.runs),
    )
    return _print_results(results)


def _evaluate(args: argparse.Namespace) -> int:
    if args.repeats < 1:
        print("--repeats must be at least 1", file=sys.stderr)
        return 2
    settings = AttemptConfig.from_defaults()
    _require_key_if_needed(args.model, settings)
    # Refuse held-out before constructing cases. cases_for_split also checks.
    cases_for_split(args.split, allow_heldout=args.allow_heldout)
    results = evaluate_split(
        args.split,
        method=args.method,
        repeats=args.repeats,
        allow_heldout=args.allow_heldout,
        settings=settings,
        model_factory_for=lambda method: _factory(args.model, args.fake_script, method, settings),
        runs_root=_runs_root(args.runs),
    )
    return _print_results(results)


def _require_key_if_needed(model_name: str, settings: AttemptConfig) -> None:
    if model_name == "openai":
        OpenAIModel.from_env(
            model_id=settings.model_id,
            reasoning_effort=settings.reasoning_effort,
            timeout_seconds=settings.request_timeout_seconds,
            store=settings.store,
        )


def _factory(model_name: str, script: str, method: str, settings: AttemptConfig):
    def build(workspace: Path):
        if model_name == "fake":
            return FakeModel(settings.model_id, build_fake_turns(script, workspace, method))
        return OpenAIModel.from_env(
            model_id=settings.model_id,
            reasoning_effort=settings.reasoning_effort,
            timeout_seconds=settings.request_timeout_seconds,
            store=settings.store,
        )

    return build


def _print_results(results: list[object]) -> int:
    failed = False
    for result in results:
        print(
            f"run_id={result.run_id} case={result.case_id} method={result.method} "
            f"repetition={result.repetition} agent_claim={result.agent_claim} "
            f"verification={result.verification} status={result.status}"
        )
        if result.status != "completed":
            failed = True
    return 1 if failed else 0


def _runs_root(raw: str | None) -> Path:
    if raw:
        return _output_path(raw)
    return repo_root() / "runs"


def _output_path(raw: str) -> Path:
    """Resolve ``--output``. Relative paths use the caller's working directory."""
    path = Path(raw)
    if path.is_absolute():
        return path
    return Path.cwd() / path


if __name__ == "__main__":
    raise SystemExit(main())
