"""Manifest checks, the public example oracle, and the private-dir message."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from repair_agent.cases import (
    fixture_root,
    format_report,
    locate_private_dir,
    tree_sha256,
    validate_cases,
    write_freeze,
)
from repair_agent.cli import main
from repair_agent.runner import docker_available
from repair_agent.schemas import ManifestFile

ROOT = Path(__file__).resolve().parents[1]


def test_public_repo_has_no_held_out_material() -> None:
    assert not (ROOT / "evaluator_private").exists()
    assert not (ROOT / "benchmark" / "manifests" / "heldout.json").exists()
    for case_id in ("H01", "H02", "H03", "H04"):
        assert not (ROOT / "benchmark" / "cases" / case_id).exists()


def test_fixture_hash_matches_every_public_manifest() -> None:
    actual = tree_sha256(fixture_root())
    for relative in (
        "benchmark/manifests/dev.json",
        "benchmark/manifests/probes.json",
        "examples/manifests/example.json",
    ):
        manifest = ManifestFile.model_validate_json((ROOT / relative).read_text())
        assert {case.fixture_sha for case in manifest.cases} == {actual}


def test_probe_manifest_has_no_scoring_fields() -> None:
    document = json.loads((ROOT / "benchmark/manifests/probes.json").read_text())
    for case in document["cases"]:
        assert "allowed_claims" not in case
        assert "reference_fix" not in case
        assert case["kind"] == "probe"


def test_private_dir_absent_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPAIR_AGENT_PRIVATE_DIR", raising=False)
    located = locate_private_dir()
    assert located.present is False
    report = validate_cases("probe")
    text = format_report(report)
    assert report.ok, report.failures
    assert "Private benchmark directory is not present" in text
    assert "Skipped held-out cases" in text
    assert "P01" in text and "scoring=private" in text


def test_visible_suite_passes_on_the_clean_fixture() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "benchmark/fixture/tests_visible",
            "-q",
            "-p",
            "no:cacheprovider",
            "--tb=line",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_freeze_hashes_public_files_and_notes_a_missing_private_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPAIR_AGENT_PRIVATE_DIR", raising=False)
    output = tmp_path / "freeze.json"
    document = write_freeze(output)
    assert output.is_file()
    assert document["fixture"]["sha256"] == tree_sha256(fixture_root())  # type: ignore[index]
    private = document["private_dir"]
    assert isinstance(private, dict)
    assert private["present"] is False
    assert private["files"] == {}


def test_later_commands_are_not_implemented() -> None:
    assert main(["run", "--case", "D01", "--method", "iterative"]) == 2
    assert main(["evaluate", "--split", "dev", "--method", "both"]) == 2


@pytest.mark.skipif(not docker_available(), reason="Docker is not available")
def test_validate_cases_public_splits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPAIR_AGENT_PRIVATE_DIR", raising=False)
    report = validate_cases("all")
    text = format_report(report)
    assert report.ok, text
    assert "Private benchmark directory is not present" in text
    for case_id in ("D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08"):
        assert f"{case_id}  passed  visible_buggy=passed protected=skipped" in text
    assert "X00  passed  visible_buggy=passed protected_buggy=failed visible_fixed=passed protected_fixed=passed" in text
    assert "H01–H04  skipped" in text
