"""Export finished run bundles into a public replay directory.

The export is a ``PublicRun`` document. Raw ``runs/`` files, evaluator logs,
reference fixes, and host paths stay out of it. A leak scan runs on the
written files and refuses the export when one still contains a path, a
key-like string, or ``evaluator_private``.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from repair_agent.schemas import PublicRun, PublicTraceEvent, RunResult, TraceEvent

# Result fields approved for the public document. ``evaluator_sha256`` is
# private metadata and is intentionally absent.
PUBLIC_RESULT_FIELDS = (
    "schema_version",
    "run_id",
    "case_id",
    "method",
    "repetition",
    "status",
    "agent_claim",
    "verification",
    "summary",
    "limitations",
    "evidence",
    "source_files_changed",
    "patch_sha256",
    "visible_passed",
    "protected_passed",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "tool_calls",
    "patch_attempts",
    "elapsed_seconds",
    "estimated_cost_usd",
    "cost_label",
    "model_id",
    "provider",
    "controller_commit",
    "fixture_sha",
    "prompt_sha256",
    "manifest_sha256",
    "budgets",
    "sampling",
    "pricing",
    "reproduction_failed_on_original",
    "reproduction_passed_after_patch",
    "stop_reason",
    "started_at_utc",
    "finished_at_utc",
)

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_URL = re.compile(r"https?://[^\s\"']+")
_EVALUATOR_PRIVATE = re.compile(r"evaluator_private", re.IGNORECASE)
_REFERENCE_FIX = re.compile(r"reference_fixes[/\\][A-Za-z0-9._-]+")
_PROTECTED_TREE = re.compile(r"/protected(?:/[A-Za-z0-9._\-]+)+")
_WORK_PREFIX = re.compile(r"/work/")
_HOME = re.compile(r"~/(?:[A-Za-z0-9._\-]+/)*[A-Za-z0-9._\-]+")
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:\.\.)?(?:[A-Za-z]:\\(?:[^\\\s\"']+\\)*[^\\\s\"']+|(?:/[A-Za-z0-9._\-]+){2,})"
)
_HOST_ROOTS = frozenset({"home", "Users", "root", "tmp", "private", "opt", "workspace", "var", "mnt"})
_KEY = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_\-]{8,}|\bAKIA[0-9A-Z]{16}\b|\bghp_[A-Za-z0-9]{20,}|"
    r"\bgithub_pat_[A-Za-z0-9_]{20,}|\bbearer\s+[A-Za-z0-9._\-]{12,}|"
    r"\b(?:api[_-]?key|openai_api_key|secret_key|access_token)\b\s*[:=]\s*\S+)"
)
_TEST_NAME = re.compile(r"\btest_[A-Za-z0-9_]+\b")
_TEST_FILE = re.compile(r"(?:[A-Za-z0-9_./\\-]*?)test_[A-Za-z0-9_]+\.py")
_PROMPT_HINTS = (
    "You repair one deterministic defect",
    "# Workspace files",
    "# API contract",
)
_REQUIRED_FILES = ("result.json", "events.jsonl", "final.patch", "visible_tests.txt", "evaluator.json", "evaluator.txt")


class PublishError(Exception):
    """The export was refused."""


class IncompleteRun(PublishError):
    """One bundle is not a finished attempt."""


@dataclass
class PublishResult:
    published: list[str] = field(default_factory=list)
    incomplete: list[tuple[str, str]] = field(default_factory=list)


def publish(runs: Path, output: Path, *, issue_text_for=None) -> PublishResult:
    """Write one ``public.json`` per complete bundle.

    Incomplete bundles are left out of ``output``. A leak-scan failure writes
    nothing and raises ``PublishError``. ``issue_text_for`` defaults to the
    public issue file for the case.
    """
    if not runs.is_dir():
        raise PublishError(f"runs directory is missing: {runs.name}")
    loader = issue_text_for or load_issue_text
    staging = Path(tempfile.mkdtemp(prefix="vra-publish-"))
    result = PublishResult()
    try:
        for run_dir in sorted(path for path in runs.iterdir() if path.is_dir()):
            try:
                public = build_public_run(run_dir, issue_text=loader(_case_id(run_dir)))
            except IncompleteRun as exc:
                result.incomplete.append((run_dir.name, str(exc)))
                continue
            _write_public(staging / public.run_id, public)
            result.published.append(public.run_id)
        findings = scan_tree(staging)
        if findings:
            raise PublishError("leak scan failed: " + ", ".join(findings))
        _install(staging, output)
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def build_public_run(run_dir: Path, *, issue_text: str = "") -> PublicRun:
    """Validate one bundle and return its public document."""
    _require_complete(run_dir)
    result = _load_result(run_dir)
    if run_dir.name != result.run_id:
        raise IncompleteRun("directory name does not match run_id")
    if not result.finished_at_utc:
        raise IncompleteRun("result.json has no finished_at_utc")
    events = _load_events(run_dir / "events.jsonl")
    _agree_with_evaluator(run_dir, result)
    markers = _protected_markers(run_dir)
    raw = result.model_dump()
    payload = {key: _sanitize_value(raw[key], markers) for key in PUBLIC_RESULT_FIELDS}
    payload["issue"] = _sanitize_text(issue_text, markers)
    payload["source_diff"] = _sanitize_text((run_dir / "final.patch").read_text(encoding="utf-8"), markers)
    payload["visible_tests"] = _sanitize_text((run_dir / "visible_tests.txt").read_text(encoding="utf-8"), markers)
    payload["trace"] = [_public_event(event, markers) for event in events]
    try:
        public = PublicRun.model_validate(payload)
    except ValidationError as exc:
        raise IncompleteRun("export did not match PublicRun") from exc
    PublicRun.model_validate_json(public.model_dump_json())
    return public


def load_issue_text(case_id: str) -> str:
    """Issue text for a case. Reference fixes and protected tests are not read."""
    if not case_id:
        return ""
    try:
        from repair_agent.cases import CaseNotFound, load_case

        return load_case(case_id, allow_heldout=True).issue_text
    except (CaseNotFound, OSError, ValueError):
        return ""


def scan_tree(root: Path) -> list[str]:
    """Return leak kinds found under ``root``. Empty means the tree is clean."""
    if not root.exists():
        return []
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        found.extend(scan_text(path.read_text(encoding="utf-8", errors="replace")))
    return _unique(found)


def scan_text(text: str) -> list[str]:
    """Kinds of forbidden content in one exported document."""
    found: list[str] = []
    if _EVALUATOR_PRIVATE.search(text):
        found.append("evaluator_private")
    if _search_outside_urls(text, _KEY):
        found.append("key-like string")
    if _search_outside_urls(text, _ABSOLUTE_PATH) or _HOME.search(text):
        found.append("path")
    return found


def sanitize_text(text: str) -> str:
    """Strip secrets, host paths, and private path fragments from one string."""
    return _sanitize_text(text, markers=())


def _public_event(event: TraceEvent, markers: tuple[str, ...]) -> dict[str, object]:
    request = event.request_summary
    response = event.response_summary
    if event.category == "prompt" or _looks_like_prompt(request) or _looks_like_prompt(response):
        request = "Full prompt omitted."
        response = "Full prompt omitted."
    else:
        request = _sanitize_text(request, markers)
        response = _sanitize_text(response, markers)
    return PublicTraceEvent(
        seq=event.seq,
        timestamp_utc=event.timestamp_utc,
        category=event.category,
        tool_name=event.tool_name,
        duration_ms=event.duration_ms,
        success=event.success,
        request_summary=request,
        response_summary=response,
    ).model_dump()


def _sanitize_value(value: object, markers: tuple[str, ...]) -> object:
    if isinstance(value, str):
        return _sanitize_text(value, markers)
    if isinstance(value, list):
        return [_sanitize_value(item, markers) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item, markers) for key, item in value.items()}
    return value


def _sanitize_text(text: str, markers: tuple[str, ...]) -> str:
    def scrub(chunk: str) -> str:
        chunk = _PROTECTED_TREE.sub("[redacted]", chunk)
        chunk = _WORK_PREFIX.sub("", chunk)
        chunk = _EVALUATOR_PRIVATE.sub("[redacted]", chunk)
        chunk = _REFERENCE_FIX.sub("[redacted]", chunk)
        chunk = _KEY.sub("[redacted]", chunk)
        chunk = _HOME.sub("[path]", chunk)
        chunk = _ABSOLUTE_PATH.sub(_rewrite_absolute, chunk)
        for marker in markers:
            if marker:
                chunk = chunk.replace(marker, "[redacted]")
        return chunk

    return _map_outside_urls(text, scrub)


def _rewrite_absolute(match: re.Match[str]) -> str:
    """Drop host paths. Keep the last two parts of a library path."""
    raw = match.group(0).replace("\\", "/")
    parts = [part for part in raw.split("/") if part and part != ".."]
    if not parts:
        return "[path]"
    root = parts[0]
    if root in _HOST_ROOTS or (len(root) == 2 and root[1] == ":"):
        return "[path]"
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return "[path]"


def _looks_like_prompt(text: str) -> bool:
    return sum(1 for hint in _PROMPT_HINTS if hint in text) >= 2


def _require_complete(run_dir: Path) -> None:
    missing = [name for name in _REQUIRED_FILES if not (run_dir / name).is_file()]
    if missing:
        raise IncompleteRun("missing " + ", ".join(missing))


def _load_result(run_dir: Path) -> RunResult:
    try:
        result = RunResult.model_validate_json((run_dir / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        raise IncompleteRun("result.json is not a complete RunResult") from exc
    if not _UUID.fullmatch(result.run_id):
        raise IncompleteRun("run_id is not a UUID")
    return result


def _case_id(run_dir: Path) -> str:
    try:
        document = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    case_id = document.get("case_id")
    return case_id if isinstance(case_id, str) else ""


def _load_events(path: Path) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise IncompleteRun("events.jsonl could not be read") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(TraceEvent.model_validate_json(line))
        except ValidationError as exc:
            raise IncompleteRun("events.jsonl has a row that is not a TraceEvent") from exc
    if not events:
        raise IncompleteRun("events.jsonl is empty")
    return events


def _agree_with_evaluator(run_dir: Path, result: RunResult) -> None:
    """Read only the verdict and the two pass/fail booleans."""
    try:
        document = json.loads((run_dir / "evaluator.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IncompleteRun("evaluator.json is not valid JSON") from exc
    if not isinstance(document, dict):
        raise IncompleteRun("evaluator.json is not an object")
    for key in ("verification", "visible_passed", "protected_passed"):
        if key in document and document[key] != getattr(result, key):
            raise IncompleteRun(f"evaluator.json {key} does not match result.json")


def _protected_markers(run_dir: Path) -> tuple[str, ...]:
    """Test names that appear only in the private evaluator output."""
    chunks = [(run_dir / "evaluator.txt").read_text(encoding="utf-8", errors="replace")]
    try:
        document = json.loads((run_dir / "evaluator.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        document = {}
    if isinstance(document, dict):
        for key in ("protected_output", "detail"):
            value = document.get(key)
            if isinstance(value, str):
                chunks.append(value)
    text = "\n".join(chunks)
    markers = set(_TEST_NAME.findall(text))
    markers.update(_TEST_FILE.findall(text))
    return tuple(sorted(markers, key=len, reverse=True))


def _write_public(dest: Path, public: PublicRun) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    text = public.model_dump_json(indent=2) + "\n"
    PublicRun.model_validate_json(text)
    (dest / "public.json").write_text(text, encoding="utf-8")


def _install(staging: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(staging, output)


def _map_outside_urls(text: str, fn) -> str:
    parts: list[str] = []
    last = 0
    for match in _URL.finditer(text):
        parts.append(fn(text[last : match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(fn(text[last:]))
    return "".join(parts)


def _search_outside_urls(text: str, pattern: re.Pattern[str]) -> bool:
    last = 0
    for match in _URL.finditer(text):
        if pattern.search(text[last : match.start()]):
            return True
        last = match.end()
    return pattern.search(text[last:]) is not None


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
