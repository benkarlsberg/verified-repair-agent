"""Scripted model transcripts for ``--model fake``.

These transcripts are fixed text. They do not call a provider. The X00
scripts read the materialized example file so the diff matches that bug.
"""

from __future__ import annotations

import json
from pathlib import Path

from repair_agent.diffs import new_file_diff, unified_diff
from repair_agent.model import FakeTurn, Usage

X00_NEEDLE = '"cancelled": "Canceled"'
_REPRO = '''"""Reproduction for the cancelled-order display label."""

from order_service.example_ops import status_label


def test_cancelled_display_label() -> None:
    assert status_label("cancelled") == "Cancelled"
'''
_USAGE = Usage(input_tokens=120, cached_input_tokens=0, output_tokens=40, reasoning_tokens=0, complete=True)


class FakeScriptError(Exception):
    """The requested script does not apply to this case."""


def build_fake_turns(script: str, workspace: Path, method: str) -> list[FakeTurn]:
    if script == "abstain":
        return _abstain(method)
    if script == "x00-pass":
        return _x00(workspace, method, replacement='"cancelled": "Cancelled"', summary=_PASS_SUMMARY)
    if script == "x00-fail":
        return _x00(workspace, method, replacement='"cancelled": "Cancellled"', summary=_FAIL_SUMMARY)
    raise FakeScriptError(f"unknown fake script: {script}")


def _abstain(method: str) -> list[FakeTurn]:
    summary = "No source change. This script does not attempt a repair."
    if method == "one_shot":
        return [FakeTurn(text=_one_shot("unresolved", summary, ""), usage=_USAGE)]
    return [
        FakeTurn(
            tool_calls=[
                (
                    "finish",
                    {
                        "claim": "unresolved",
                        "summary": summary,
                        "limitations": ["scripted abstain"],
                        "evidence": [],
                    },
                )
            ],
            usage=_USAGE,
        )
    ]


def _x00(workspace: Path, method: str, *, replacement: str, summary: str) -> list[FakeTurn]:
    path = workspace / "order_service" / "example_ops.py"
    source = path.read_text(encoding="utf-8")
    if X00_NEEDLE not in source:
        raise FakeScriptError("x00-pass and x00-fail only apply to example case X00")
    updated = source.replace(X00_NEEDLE, replacement, 1)
    patch = unified_diff("order_service/example_ops.py", source, updated)
    if method == "one_shot":
        return [FakeTurn(text=_one_shot("repaired", summary, patch), usage=_USAGE)]
    reproduction = new_file_diff("agent_tests/test_repro.py", _REPRO)
    return [
        FakeTurn(tool_calls=[("apply_patch", {"patch": reproduction})], usage=_USAGE),
        FakeTurn(tool_calls=[("run_visible_tests", {})], usage=_USAGE),
        FakeTurn(tool_calls=[("apply_patch", {"patch": patch})], usage=_USAGE),
        FakeTurn(tool_calls=[("run_visible_tests", {})], usage=_USAGE),
        FakeTurn(
            tool_calls=[
                (
                    "finish",
                    {
                        "claim": "repaired",
                        "summary": summary,
                        "limitations": [],
                        "evidence": ["agent_tests/test_repro.py"],
                    },
                )
            ],
            usage=_USAGE,
        ),
    ]


def _one_shot(claim: str, summary: str, patch: str) -> str:
    return json.dumps(
        {"claim": claim, "summary": summary, "limitations": [], "patch": patch},
        ensure_ascii=False,
    )


_PASS_SUMMARY = (
    "The cancelled status is labeled with two Ls. "
    "A reproduction failed on the original code and passed after the edit."
)
_FAIL_SUMMARY = "The cancelled status label was edited and the case is repaired."
