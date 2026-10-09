"""Read-only replay viewer.

The process reads a directory of ``PublicRun`` files. It does not read raw
run bundles, the private benchmark directory, or a model API key. Nothing
here starts a repair.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError
from starlette.templating import Jinja2Templates

from repair_agent.measures import (
    LIMITATIONS,
    Attempt,
    method_label,
    pricing_note,
    split_of,
    summarize,
)
from repair_agent.schemas import SCRIPTED_MODEL_ID, PublicRun

SCHEMA_VERSION = 1
RECORDED_LINE = "Recorded investigations; run the agent locally using the repository instructions."
SOURCE_URL = "https://github.com/benkarlsberg/verified-repair-agent"
PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

BENCHMARK_LIMITS = (
    "Eight development cases (D01–D08), four held-out cases (H01–H04), and three abstention probes (P01–P03).",
    "Two methods share one model id: an iterative tool loop and a one-shot baseline with no tool feedback.",
    "One attempt may use 10 minutes, 12 model responses, 30 tool calls, 3 source patches, 160,000 cumulative tokens, and 4,000 output tokens per response.",
    "The evaluator, not the agent, decides whether a repair holds. A repaired claim can still fail verification.",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_public_dir(root: Path | None = None) -> Path:
    """Directory of published bundles. An explicit env path wins.

    When ``public_runs`` has no bundles, the example recordings are used so
    the viewer can start with no separate publish step. Raw ``runs`` are
    never selected.
    """
    raw = os.environ.get("REPAIR_AGENT_PUBLIC_RUNS")
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else Path.cwd() / path
    base = root or _repo_root()
    primary = base / "public_runs"
    if any(primary.glob("*/public.json")):
        return primary
    return base / "examples" / "public_runs"


def create_app(public_dir: Path | None = None) -> FastAPI:
    """Build the viewer. ``public_dir`` defaults to :func:`resolve_public_dir`."""
    directory = public_dir if public_dir is not None else resolve_public_dir()
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["method_label"] = method_label
    env.filters["flag"] = _flag
    templates = Jinja2Templates(env=env)
    app = FastAPI(title="Verified Repair Agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def catalog() -> list[PublicRun]:
        return load_public_runs(directory)

    def render(request: Request, name: str, context: dict[str, object], status_code: int = 200):
        payload = {
            "recorded_line": RECORDED_LINE,
            "source_url": SOURCE_URL,
            "demo_note": demo_note(directory),
            **context,
        }
        return templates.TemplateResponse(request, name, payload, status_code=status_code)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        body = {"status": "ok", "schema_version": SCHEMA_VERSION, "bundles": len(catalog())}
        return JSONResponse(body)

    @app.get("/")
    def index(request: Request):
        runs = catalog()
        return render(
            request,
            "index.html",
            {
                "limits": BENCHMARK_LIMITS,
                "cards": _cards(runs),
                "runs": runs,
                "bundle_count": len(runs),
            },
        )

    @app.get("/cases/{case_id}")
    def case_page(request: Request, case_id: str):
        runs = [item for item in catalog() if item.case_id == case_id]
        if not runs:
            raise HTTPException(status_code=404)
        runs.sort(key=lambda item: (item.method, item.repetition, item.run_id))
        return render(
            request,
            "case.html",
            {
                "case_id": case_id,
                "split": split_of(case_id),
                "issue": next((item.issue for item in runs if item.issue.strip()), ""),
                "runs": runs,
            },
        )

    @app.get("/runs/{run_id}")
    def run_page(request: Request, run_id: str):
        match = next((item for item in catalog() if item.run_id == run_id), None)
        if match is None:
            raise HTTPException(status_code=404)
        return render(
            request,
            "run.html",
            {
                "run": match,
                "false_claim": match.agent_claim == "repaired" and match.verification in {"failed", "rejected"},
                "scripted": _scripted(match),
                "cost": _cost(match),
            },
        )

    @app.get("/comparison")
    def comparison(request: Request):
        runs = catalog()
        attempts = [attempt_from_public(item) for item in runs]
        return render(
            request,
            "comparison.html",
            {
                "heldout": summarize(attempts, "heldout"),
                "dev": summarize(attempts, "dev"),
                "probes": summarize(attempts, "probe"),
                "example": summarize(attempts, "example"),
                "pricing": pricing_note(attempts),
                "limitations": LIMITATIONS,
                "bundle_count": len(runs),
            },
        )

    @app.exception_handler(404)
    async def not_found(request: Request, _exc: HTTPException):
        return render(request, "404.html", {}, status_code=404)

    return app


def load_public_runs(directory: Path) -> list[PublicRun]:
    """Load every valid ``public.json``. Invalid files are skipped."""
    if not directory.is_dir():
        return []
    runs: list[PublicRun] = []
    for path in sorted(directory.iterdir()):
        document = path / "public.json"
        if not document.is_file():
            continue
        try:
            runs.append(PublicRun.model_validate_json(document.read_text(encoding="utf-8")))
        except (OSError, ValidationError, json.JSONDecodeError):
            continue
    return runs


def demo_note(directory: Path) -> str | None:
    path = directory / "demo.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    note = document.get("note") if isinstance(document, dict) else None
    if isinstance(note, str) and note.strip():
        return note.strip()
    return None


def attempt_from_public(run: PublicRun) -> Attempt:
    return Attempt(
        run_id=run.run_id,
        case_id=run.case_id,
        split=split_of(run.case_id),
        method=run.method,
        repetition=run.repetition,
        status=run.status,
        agent_claim=run.agent_claim,
        verification=run.verification,
        summary=run.summary,
        visible_passed=run.visible_passed,
        protected_passed=run.protected_passed,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        tool_calls=run.tool_calls,
        elapsed_seconds=run.elapsed_seconds,
        estimated_cost_usd=run.estimated_cost_usd,
        cost_label=run.cost_label,
        pricing=dict(run.pricing),
        model_id=run.model_id,
        provider=run.provider,
        controller_commit=run.controller_commit,
        sampling=dict(run.sampling),
        budgets=dict(run.budgets),
        stop_reason=run.stop_reason,
        empty_source=not run.source_files_changed and not run.source_diff.strip(),
        source_files_changed=tuple(run.source_files_changed),
    )


def _cards(runs: list[PublicRun]) -> list[dict[str, object]]:
    order = {"heldout": 0, "dev": 1, "probe": 2, "example": 3, "other": 4}
    grouped: dict[str, list[PublicRun]] = {}
    for run in runs:
        grouped.setdefault(run.case_id, []).append(run)
    cards = []
    for case_id, items in grouped.items():
        cards.append(
            {
                "case_id": case_id,
                "split": split_of(case_id),
                "count": len(items),
                "outcomes": _outcome_counts(items),
            }
        )
    cards.sort(key=lambda card: (order.get(str(card["split"]), 9), str(card["case_id"])))
    return cards


def _outcome_counts(runs: list[PublicRun]) -> list[tuple[str, int]]:
    verdicts = [
        ("passed", sum(1 for item in runs if item.verification == "passed")),
        ("failed", sum(1 for item in runs if item.verification == "failed")),
        ("rejected", sum(1 for item in runs if item.verification == "rejected")),
        ("infra error", sum(1 for item in runs if item.verification == "infra_error" or item.status == "infra_error")),
    ]
    claims = [
        ("repaired claim", sum(1 for item in runs if item.agent_claim == "repaired")),
        ("unresolved claim", sum(1 for item in runs if item.agent_claim == "unresolved")),
        ("insufficient evidence", sum(1 for item in runs if item.agent_claim == "insufficient_evidence")),
    ]
    return [(label, count) for label, count in (*verdicts, *claims) if count]


def _flag(value: bool | None) -> str:
    if value is True:
        return "passed"
    if value is False:
        return "failed"
    return "not recorded"


def _scripted(run: PublicRun) -> bool:
    return run.provider == "fake" or run.model_id == SCRIPTED_MODEL_ID


def _cost(run: PublicRun) -> str:
    if run.cost_label == "not_applicable" or _scripted(run):
        return "not applicable"
    if run.cost_label != "estimated" or run.estimated_cost_usd is None:
        return "unavailable"
    return f"${run.estimated_cost_usd:.6f} estimated"


app = create_app()
