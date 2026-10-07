"""Command line for the repair agent.

Week 1 implements ``validate-cases`` and ``freeze``. The repair loop, evaluator
batch, report, and publisher are later weeks and exit with a clear message.
"""

from __future__ import annotations

import argparse
import sys

from repair_agent.cases import format_report, validate_cases, write_freeze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repair-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-cases", help="Check manifests and bug/oracle fixtures")
    validate.add_argument("--split", required=True, choices=("all", "dev", "heldout", "probe", "example"))

    freeze = sub.add_parser("freeze", help="Hash manifests, the fixture, and private evaluator files")
    freeze.add_argument("--output", default="freeze.json")

    run = sub.add_parser("run", help="Run one repair attempt (week 2)")
    run.add_argument("--case", required=True)
    run.add_argument("--method", required=True, choices=("iterative", "one_shot"))
    run.add_argument("--repeat", type=int, default=1)

    evaluate = sub.add_parser("evaluate", help="Evaluate a split (week 2)")
    evaluate.add_argument("--split", required=True)
    evaluate.add_argument("--method", required=True)
    evaluate.add_argument("--repeats", type=int, default=1)

    report = sub.add_parser("report", help="Write the evaluation report (week 4)")
    report.add_argument("--runs", default="runs")
    report.add_argument("--output", default="reports/evaluation.md")

    publish = sub.add_parser("publish", help="Sanitize run bundles (week 3)")
    publish.add_argument("--runs", default="runs")
    publish.add_argument("--output", default="public_runs")

    args = parser.parse_args(argv)
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
    print(
        f"repair-agent {args.command} is not implemented in week 1. "
        "The model adapter, repair loop, and baseline are week 2. "
        "The viewer and publish step are week 3. The evaluation report is week 4.",
        file=sys.stderr,
    )
    return 2


def _output_path(raw: str):
    from pathlib import Path

    path = Path(raw)
    if not path.is_absolute():
        from repair_agent.cases import repo_root

        path = repo_root() / path
    return path


if __name__ == "__main__":
    raise SystemExit(main())
