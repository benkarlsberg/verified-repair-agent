"""Read-only replay viewer."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from repair_agent.cases import repo_root
from repair_agent.schemas import PublicRun
from repair_agent.web import RECORDED_LINE, create_app, resolve_public_dir

EXAMPLES = repo_root() / "examples" / "public_runs"


def test_checked_in_examples_pass_the_leak_scan() -> None:
    from repair_agent.publish import scan_tree

    assert scan_tree(EXAMPLES) == []


def test_routes_render_example_bundles() -> None:
    client = TestClient(create_app(EXAMPLES))
    index = client.get("/")
    assert index.status_code == 200
    assert RECORDED_LINE in index.text
    assert "Recorded demo" in index.text
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
    monkeypatch.setenv("REPAIR_AGENT_PUBLIC_RUNS", str(tmp_path / "public_runs"))
    assert resolve_public_dir(tmp_path) == tmp_path / "public_runs"


def test_app_starts_without_a_model_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(EXAMPLES))
    response = client.get("/healthz")
    assert response.status_code == 200


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
