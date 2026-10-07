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
FIXTURE_SHA = "c4dc0b9dc61e26e0a0f0f62ebc97127f227c1b512f2c3dac15e6bab1d4c04e6c"


def test_public_repo_has_no_held_out_material() -> None:
    assert not (ROOT / "evaluator_private").exists()
    assert not (ROOT / "benchmark" / "manifests" / "heldout.json").exists()
    for case_id in ("H01", "H02", "H03", "H04"):
        assert not (ROOT / "benchmark" / "cases" / case_id).exists()


def test_fixture_hash_matches_every_public_manifest() -> None:
    actual = tree_sha256(fixture_root())
    assert actual == FIXTURE_SHA
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


def test_freeze_relative_output_uses_the_caller_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPAIR_AGENT_PRIVATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["freeze", "--output", "out/freeze.json"]) == 0
    written = tmp_path / "out" / "freeze.json"
    assert written.is_file()
    assert not (ROOT / "out" / "freeze.json").exists()
    document = json.loads(written.read_text())
    assert document["fixture"]["sha256"] == FIXTURE_SHA


def test_freeze_absolute_output_stays_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPAIR_AGENT_PRIVATE_DIR", raising=False)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    target = tmp_path / "elsewhere" / "freeze.json"
    assert main(["freeze", "--output", str(target)]) == 0
    assert target.is_file()
    assert not (cwd / "freeze.json").exists()


def test_freeze_omits_vcs_metadata_caches_and_venvs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private = tmp_path / ".venv" / "private-checkout"
    (private / ".git" / "objects").mkdir(parents=True)
    (private / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (private / ".git" / "objects" / "ab").write_bytes(b"\x00git")
    (private / ".venv" / "lib").mkdir(parents=True)
    (private / ".venv" / "lib" / "junk.py").write_text("x = 1\n")
    (private / "evaluator_private" / "__pycache__").mkdir(parents=True)
    (private / "evaluator_private" / "__pycache__" / "x.pyc").write_bytes(b"\x00pyc")
    (private / ".pytest_cache" / "v").mkdir(parents=True)
    (private / ".pytest_cache" / "v" / "cache").write_text("not content\n")
    (private / "pkg.egg-info").mkdir()
    (private / "pkg.egg-info" / "PKG-INFO").write_text("Metadata-Version: 2.1\n")
    (private / "cases" / "H01").mkdir(parents=True)
    (private / "cases" / "H01" / "issue.md").write_text("symptom\n")
    (private / "manifests").mkdir()
    (private / "manifests" / "heldout.json").write_text("{}\n")
    (private / ".gitignore").write_text("*.pyc\n")
    monkeypatch.setenv("REPAIR_AGENT_PRIVATE_DIR", str(private))

    document = write_freeze(tmp_path / "freeze.json")
    private_dir = document["private_dir"]
    assert isinstance(private_dir, dict)
    files = private_dir["files"]
    assert isinstance(files, dict)
    assert set(files) == {
        ".gitignore",
        "cases/H01/issue.md",
        "manifests/heldout.json",
    }
    assert document["fixture"]["sha256"] == FIXTURE_SHA  # type: ignore[index]


def test_tree_sha256_ignores_checkout_parents_and_local_junk(tmp_path: Path) -> None:
    nested = tmp_path / ".venv" / ".git" / "fixture"
    (nested / "order_service").mkdir(parents=True)
    (nested / "order_service" / "api.py").write_text("value = 1\n")
    (nested / ".git").mkdir()
    (nested / ".git" / "HEAD").write_text("ref\n")
    (nested / "__pycache__").mkdir()
    (nested / "__pycache__" / "api.cpython-311.pyc").write_bytes(b"pyc")
    plain = tmp_path / "plain"
    (plain / "order_service").mkdir(parents=True)
    (plain / "order_service" / "api.py").write_text("value = 1\n")
    assert tree_sha256(nested) == tree_sha256(plain)
    assert tree_sha256(fixture_root()) == FIXTURE_SHA


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
