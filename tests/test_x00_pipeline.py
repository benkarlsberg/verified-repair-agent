"""Example case X00 through the fake model and the real Docker evaluator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repair_agent.cli import main
from repair_agent.runner import docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker is not available")


def _run(tmp_path: Path, script: str, method: str) -> dict[str, object]:
    runs = tmp_path / "runs"
    code = main(
        [
            "run",
            "--case",
            "X00",
            "--method",
            method,
            "--model",
            "fake",
            "--fake-script",
            script,
            "--runs",
            str(runs),
            "--repeat",
            "1",
        ]
    )
    assert code == 0
    results = list(runs.glob("*/result.json"))
    assert len(results) == 1
    return json.loads(results[0].read_text(encoding="utf-8"))


def test_x00_fake_iterative_pass_and_fail(tmp_path: Path) -> None:
    passed = _run(tmp_path / "pass", "x00-pass", "iterative")
    failed = _run(tmp_path / "fail", "x00-fail", "iterative")
    assert passed["agent_claim"] == "repaired"
    assert passed["verification"] == "passed"
    assert passed["visible_passed"] is True
    assert passed["protected_passed"] is True
    assert passed["reproduction_failed_on_original"] is True
    assert passed["reproduction_passed_after_patch"] is True

    assert failed["agent_claim"] == "repaired"
    assert failed["verification"] == "failed"
    assert failed["visible_passed"] is True
    assert failed["protected_passed"] is False
    assert "order_service/example_ops.py" in failed["source_files_changed"]


def test_x00_fake_one_shot_pass(tmp_path: Path) -> None:
    passed = _run(tmp_path / "pass", "x00-pass", "one_shot")
    assert passed["agent_claim"] == "repaired"
    assert passed["verification"] == "passed"
    assert passed["tool_calls"] == 0
