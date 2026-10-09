"""Read-only replay viewer."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from repair_agent.cases import repo_root
from repair_agent.measures import DEV_CASES, HELD_OUT_CASES, PROBE_CASES
from repair_agent.publish import scan_text, scan_tree
from repair_agent.schemas import PublicRun
from repair_agent.web import RECORDED_LINE, create_app, resolve_public_dir, resolve_public_dirs

EXAMPLES = repo_root() / "examples" / "public_runs"
DEV_RUNS = repo_root() / "examples" / "dev_runs"
CHECKED_IN = (EXAMPLES, DEV_RUNS)
_HELDOUT_ID = re.compile(r"\bH0[1-4]\b")
_HELDOUT_PATH = re.compile(r"(?:cases|tests|reference_fixes)/H0[1-4]|manifests/heldout", re.IGNORECASE)


def test_checked_in_examples_pass_the_leak_scan() -> None:
    for directory in CHECKED_IN:
        assert scan_tree(directory) == []


def test_checked_in_bundles_exclude_private_and_heldout_content() -> None:
    bundles = [path for root in CHECKED_IN for path in sorted(root.glob("*/public.json"))]
    assert len(bundles) == 18
    for path in bundles:
        text = path.read_text(encoding="utf-8")
        assert scan_text(text) == [], path.name
        run = PublicRun.model_validate_json(text)
        assert run.case_id not in HELD_OUT_CASES
        issue = _public_issue(run.case_id)
        assert run.issue == issue
        assert heldout_case_content(text, public_issue=issue) == []
    assert heldout_case_content('{"case_id": "H02"}') == ["held-out case id"]
    assert heldout_case_content("copied cases/H03/issue.md") == ["held-out path", "held-out case id"]
    assert heldout_case_content("It is not one of D01–D08 or H01–H04.\n") == []
    leaked = "sk-proj-abcdefghijklmnopqrstuvwxyz /home/ada/secret evaluator_private/tests/D01"
    assert set(scan_text(leaked)) == {"key-like string", "path", "evaluator_private"}


def test_routes_render_example_bundles() -> None:
    client = TestClient(create_app(EXAMPLES))
    index = client.get("/")
    assert index.status_code == 200
    assert RECORDED_LINE in index.text
    assert "Recorded runs" in index.text
    assert "Recorded demo" not in index.text
    assert "X00" in index.text
    assert "passed" in index.text
    assert "failed" in index.text
    health = client.get("/healthz")
    assert health.status_code == 200
    body = health.json()
    assert set(body) == {"status", "schema_version", "bundles"}
    assert body["schema_version"] == 1
    assert body["bundles"] >= 2
    assert "/" not in health.text
    case = client.get("/cases/X00")
    assert case.status_code == 200
    assert "EXAMPLE ONLY" in case.text
    assert "evaluator_private" not in case.text
    comparison = client.get("/comparison")
    assert comparison.status_code == 200
    assert "Held-out results" in comparison.text
    assert "Development results" in comparison.text
    assert "D04 and H02" in comparison.text
    assert RECORDED_LINE in comparison.text
    run_ids = [path.parent.name for path in EXAMPLES.glob("*/public.json")]
    assert len(run_ids) >= 2
    pages = []
    for run_id in run_ids:
        run = client.get(f"/runs/{run_id}")
        assert run.status_code == 200
        assert "Agent claim" in run.text
        assert "Evaluator verdict" in run.text
        assert "evaluator_private" not in run.text
        pages.append(run.text)
    assert any('class="verdict">passed<' in page for page in pages)
    assert any('class="verdict">failed<' in page for page in pages)
    assert any("False repair claim" in page for page in pages)
    assert "<form" not in index.text.lower()
    assert "upload" not in index.text.lower()
    assert "scripted-fake" in index.text
    assert "not applicable" in index.text
    assert "not applicable" in comparison.text
    assert "gpt-5.4-mini" not in comparison.text
    for page in pages:
        assert "Scripted fake model" in page
        assert "did not call a hosted model" in page
        assert "scripted-fake" in page
        assert "provider fake" in page
        assert "not applicable" in page
        assert "gpt-5.4-mini" not in page
        assert "$0." not in page


def test_unknown_ids_are_404_and_html_is_escaped(tmp_path: Path) -> None:
    run_id = "22222222-2222-2222-2222-222222222222"
    _write(tmp_path, run_id)
    client = TestClient(create_app(tmp_path))
    missing_case = client.get("/cases/D01")
    missing_run = client.get("/runs/33333333-3333-3333-3333-333333333333")
    assert missing_case.status_code == 404
    assert missing_run.status_code == 404
    assert "Not found" in missing_case.text
    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "<script>" not in page.text
    assert "&lt;script&gt;" in page.text
    assert "Agent claim" in page.text
    assert "Evaluator verdict" in page.text
    assert "failed" in page.text
    assert "unresolved" in page.text
    index = client.get("/")
    assert "failed" in index.text
    assert "unresolved" in index.text
    style = client.get("/static/style.css")
    assert style.status_code == 200
    assert "max-width: 700px" in style.text


def test_viewer_has_no_execute_routes(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        assert "POST" not in methods
        assert "PUT" not in methods
    templates = repo_root() / "src" / "repair_agent" / "templates"
    for path in templates.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "|safe" not in text
    source = (repo_root() / "src" / "repair_agent" / "web.py").read_text(encoding="utf-8")
    assert "evaluator_private" not in source
    assert "OPENAI" not in source
    assert ' / "runs"' not in source


def test_resolve_uses_examples_and_ignores_raw_runs(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "runs").mkdir()
    (tmp_path / "public_runs").mkdir()
    (tmp_path / "private").mkdir()
    demo = tmp_path / "examples" / "public_runs" / "sample"
    demo.mkdir(parents=True)
    (demo / "public.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.delenv("REPAIR_AGENT_PUBLIC_RUNS", raising=False)
    assert resolve_public_dir(tmp_path) == tmp_path / "examples" / "public_runs"
    assert resolve_public_dirs(tmp_path) == [tmp_path / "examples" / "public_runs"]
    dev = tmp_path / "examples" / "dev_runs" / "sample"
    dev.mkdir(parents=True)
    (dev / "public.json").write_text("{}\n", encoding="utf-8")
    assert resolve_public_dirs(tmp_path) == [
        tmp_path / "examples" / "public_runs",
        tmp_path / "examples" / "dev_runs",
    ]
    published = tmp_path / "public_runs" / "local"
    published.mkdir()
    (published / "public.json").write_text("{}\n", encoding="utf-8")
    assert resolve_public_dirs(tmp_path) == [tmp_path / "public_runs"]
    monkeypatch.setenv("REPAIR_AGENT_PUBLIC_RUNS", str(tmp_path / "public_runs"))
    assert resolve_public_dir(tmp_path) == tmp_path / "public_runs"
    assert resolve_public_dirs(tmp_path) == [tmp_path / "public_runs"]


def test_scripted_run_page_does_not_invent_a_dollar_cost(tmp_path: Path) -> None:
    run_id = "44444444-4444-4444-4444-444444444444"
    payload = PublicRun(
        run_id=run_id,
        case_id="X00",
        method="one_shot",
        repetition=1,
        status="completed",
        agent_claim="repaired",
        verification="failed",
        summary="A scripted miss.",
        fixture_sha="c" * 64,
        started_at_utc="2026-10-09T00:00:00Z",
        finished_at_utc="2026-10-09T00:00:01Z",
        issue="A public issue.",
        provider="fake",
        model_id="scripted-fake",
        cost_label="not_applicable",
        estimated_cost_usd=None,
        input_tokens=120,
        output_tokens=40,
    )
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "public.json").write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
    page = TestClient(create_app(tmp_path)).get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert "Scripted fake model. This attempt did not call a hosted model." in page.text
    assert "Model scripted-fake · provider fake · cost not applicable" in page.text
    assert "not applicable" in page.text
    assert "gpt-5.4-mini" not in page.text
    assert "$" not in page.text


def test_default_catalog_serves_development_runs_and_x00(monkeypatch) -> None:
    monkeypatch.delenv("REPAIR_AGENT_PUBLIC_RUNS", raising=False)
    client = TestClient(create_app())
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "schema_version": 1, "bundles": 18}
    index = client.get("/")
    assert "Recorded runs" in index.text
    assert "D01" in index.text
    assert "X00" in index.text
    assert "scripted-fake" in index.text
    runs = [run for directory in CHECKED_IN for run in _load(directory)]
    d01 = next(item for item in runs if item.case_id == "D01" and item.method == "iterative")
    page = client.get(f"/runs/{d01.run_id}")
    assert page.status_code == 200
    assert "Scripted fake model" not in page.text
    assert "gpt-5.4-mini-2026-03-17" in page.text
    assert 'class="verdict">passed<' in page.text
    x00 = next(item for item in runs if item.case_id == "X00")
    fake = client.get(f"/runs/{x00.run_id}")
    assert "Scripted fake model. This attempt did not call a hosted model." in fake.text
    comparison = client.get("/comparison")
    assert comparison.status_code == 200
    assert "Development results" in comparison.text
    assert "D01" in comparison.text


def test_app_starts_without_a_model_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(EXAMPLES))
    response = client.get("/healthz")
    assert response.status_code == 200


def heldout_case_content(text: str, *, public_issue: str = "") -> list[str]:
    """Held-out case ids and paths. The public X00 range mention is not case content."""
    findings: list[str] = []
    if _HELDOUT_PATH.search(text):
        findings.append("held-out path")
    remainder = text.replace(public_issue, "") if public_issue else text
    for phrase in ("H01–H04", "H01-H04"):
        remainder = remainder.replace(phrase, "")
    if _HELDOUT_ID.search(remainder):
        findings.append("held-out case id")
    return findings


def _public_issue(case_id: str) -> str:
    root = repo_root()
    if case_id == "X00":
        path = root / "examples" / "cases" / "X00" / "issue.md"
    elif case_id in DEV_CASES:
        path = root / "benchmark" / "cases" / case_id / "issue.md"
    elif case_id in PROBE_CASES:
        path = root / "benchmark" / "probes" / case_id / "issue.md"
    else:
        raise AssertionError(f"no public issue for {case_id}")
    return path.read_text(encoding="utf-8")


def _load(directory: Path) -> list[PublicRun]:
    runs: list[PublicRun] = []
    for path in sorted(directory.glob("*/public.json")):
        runs.append(PublicRun.model_validate_json(path.read_text(encoding="utf-8")))
    return runs


def _write(directory: Path, run_id: str) -> None:
    payload = PublicRun(
        run_id=run_id,
        case_id="X00",
        method="iterative",
        repetition=1,
        status="completed",
        agent_claim="unresolved",
        verification="failed",
        summary="<script>alert(1)</script>",
        source_diff="<script>diff</script>",
        visible_tests="1 failed\n",
        fixture_sha="c" * 64,
        started_at_utc="2026-10-09T00:00:00Z",
        finished_at_utc="2026-10-09T00:00:01Z",
        issue="A public issue.",
    )
    run_dir = directory / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "public.json").write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (directory / "note.json").write_text(json.dumps({"ignore": True}), encoding="utf-8")
