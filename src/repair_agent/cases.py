"""Load case manifests, materialize a fixture, and validate bug cases.

Protected tests and reference fixes resolve from ``REPAIR_AGENT_PRIVATE_DIR``
when they are not in this repository. The public example case is. Missing
private material is reported and skipped. This module does not import the
order service; pytest runs only inside the container runner.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError as ModelError

from repair_agent.patch_policy import PatchRejected, inspect_patch
from repair_agent.runner import Runner, RunnerResult, docker_available
from repair_agent.schemas import BugCase, ManifestFile, ProbeCase, ProbeScoreFile

PUBLIC_MANIFESTS = {
    "dev": "benchmark/manifests/dev.json",
    "probe": "benchmark/manifests/probes.json",
    "example": "examples/manifests/example.json",
}
HELD_OUT_MANIFEST = "manifests/heldout.json"
PROBE_SCORING = "evaluator_private/probe_scoring.json"
FIXTURE_DIR = "benchmark/fixture"

# Not benchmark or config content. Matched on the path relative to the hashed
# root, so a checkout that itself lives under ``.venv`` or ``.git`` is still hashed.
_SKIPPED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "htmlcov",
        ".eggs",
        "node_modules",
        ".cache",
        "dist",
        "build",
    }
)
_SKIPPED_FILE_NAMES = frozenset({".coverage", ".DS_Store", ".git"})
_SKIPPED_SUFFIXES = frozenset({".pyc", ".pyo"})


@dataclass
class PrivateDir:
    configured: Path
    present: bool


@dataclass
class CaseCheck:
    case_id: str
    status: str
    detail: str


@dataclass
class ValidationReport:
    private_dir: PrivateDir
    checks: list[CaseCheck] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def locate_private_dir() -> PrivateDir:
    raw = os.environ.get("REPAIR_AGENT_PRIVATE_DIR", "private")
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root() / path
    return PrivateDir(configured=path, present=path.is_dir())


def fixture_root() -> Path:
    return repo_root() / FIXTURE_DIR


def tree_sha256(root: Path) -> str:
    """Hash a directory of benchmark and config files.

    The digest is SHA-256 over UTF-8 lines ``"<file sha256>  <relative posix path>"``,
    sorted by path, joined with newlines and a trailing newline. Symlinks and
    anything :func:`_is_content_file` rejects are omitted. The fixture tree has
    none of those extra paths, so this digest stays put until the fixture changes.
    """
    lines: list[str] = []
    for path in _content_files(root):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def _content_files(root: Path) -> list[Path]:
    return [path for path in sorted(root.rglob("*")) if _is_content_file(root, path)]


def _is_content_file(root: Path, path: Path) -> bool:
    """True for a regular file that is benchmark or config content under ``root``."""
    if path.is_symlink() or not path.is_file():
        return False
    relative = path.relative_to(root)
    if any(_is_skipped_dir(part) for part in relative.parts[:-1]):
        return False
    name = relative.name
    if name in _SKIPPED_FILE_NAMES or Path(name).suffix in _SKIPPED_SUFFIXES:
        return False
    return not name.endswith(".egg-info")


def _is_skipped_dir(name: str) -> bool:
    return name in _SKIPPED_DIR_NAMES or name.endswith(".egg-info")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_cases(split: str, *, runner: Runner | None = None) -> ValidationReport:
    """Validate ``all`` or one of dev, heldout, probe, example."""
    if split not in {"all", "dev", "heldout", "probe", "example"}:
        raise ValueError(f"unknown split: {split}")
    private = locate_private_dir()
    report = ValidationReport(private_dir=private)
    _reject_public_leaks(report)
    names = ["dev", "example", "probe", "heldout"] if split == "all" else [split]
    loaded: list[ManifestFile] = []
    for name in names:
        manifest = _load_split(name, private, report)
        if manifest is not None:
            loaded.append(manifest)
    if report.failures:
        return report
    bug_cases = [case for manifest in loaded for case in manifest.cases if isinstance(case, BugCase)]
    if bug_cases and not docker_available():
        report.failures.append(
            "Docker is required to execute fixture tests and is not available. "
            "Install Docker and re-run validate-cases. No fixture tests were executed."
        )
        return report
    active = runner or Runner()
    actual_sha = tree_sha256(fixture_root())
    for manifest in loaded:
        for case in manifest.cases:
            if case.fixture_sha != actual_sha:
                message = f"{case.case_id}: fixture_sha does not match benchmark/fixture ({actual_sha})"
                report.failures.append(message)
                report.checks.append(CaseCheck(case.case_id, "failed", "fixture hash mismatch"))
                continue
            if isinstance(case, ProbeCase):
                _validate_probe(case, private, report)
            else:
                _validate_bug(case, private, active, report)
    if private.present and any(isinstance(case, ProbeCase) for manifest in loaded for case in manifest.cases):
        _validate_probe_scoring(private, report)
    elif "probe" in names and private.present:
        _validate_probe_scoring(private, report)
    return report


def format_report(report: ValidationReport) -> str:
    private = report.private_dir
    lines = []
    if private.present:
        lines.append(f"Private benchmark directory: {private.configured}")
    else:
        lines.append(f"Private benchmark directory is not present: {private.configured}")
        lines.append("Set REPAIR_AGENT_PRIVATE_DIR to a checkout of benkarlsberg/verified-repair-agent-private.")
        lines.append("The default is a gitignored private/ directory.")
        lines.append(
            "Skipped held-out cases H01–H04, protected tests and reference fixes for D01–D08, "
            "and private probe scoring."
        )
        lines.append("Public cases are still validated.")
    lines.append("")
    for check in report.checks:
        lines.append(f"{check.case_id}  {check.status}  {check.detail}")
    if report.failures:
        lines.extend(["", "Failures:"])
        lines.extend(f"- {failure}" for failure in report.failures)
        lines.append("validate-cases: failed")
    else:
        lines.extend(["", "validate-cases: passed"])
    return "\n".join(lines) + "\n"


def write_freeze(output: Path) -> dict[str, object]:
    """Hash the public fixture and manifests, plus private evaluator files when mounted."""
    root = repo_root()
    private = locate_private_dir()
    manifests = {
        relative: file_sha256(root / relative)
        for relative in PUBLIC_MANIFESTS.values()
        if (root / relative).is_file()
    }
    private_files: dict[str, str] = {}
    if private.present:
        for path in _content_files(private.configured):
            private_files[path.relative_to(private.configured).as_posix()] = file_sha256(path)
        heldout = private.configured / HELD_OUT_MANIFEST
        if heldout.is_file():
            manifests[HELD_OUT_MANIFEST] = file_sha256(heldout)
    document: dict[str, object] = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixture": {"path": FIXTURE_DIR, "sha256": tree_sha256(fixture_root())},
        "manifests": manifests,
        "config_sha256": file_sha256(root / "config" / "defaults.json"),
        "private_dir": {
            "present": private.present,
            "path": str(private.configured),
            "files": private_files,
        },
        "notes": [
            "fixture sha256 covers benchmark/fixture, including visible tests.",
            "Held-out cases and D01–D08 protected tests are hashed only when the private directory is present.",
            "Private hashes omit VCS metadata, virtual environments, and caches.",
            "This repository does not call a model. Controller and prompt hashes are not included yet.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n")
    return document


def materialize(dest: Path, bug_patch: Path, reference_fix: Path | None = None) -> None:
    """Copy the clean fixture, apply the bug patch, then an optional reference fix."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        fixture_root(),
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        symlinks=False,
    )
    _git_apply(bug_patch, dest)
    if reference_fix is not None:
        _git_apply(reference_fix, dest)


def _validate_bug(case: BugCase, private: PrivateDir, runner: Runner, report: ValidationReport) -> None:
    bug_patch = _resolve(case.bug_patch, private)
    issue = _resolve(case.issue_path, private)
    if bug_patch is None or issue is None:
        _missing(case, private, report, "bug patch or issue file")
        return
    if not issue.read_text().strip():
        report.failures.append(f"{case.case_id}: issue is empty")
        report.checks.append(CaseCheck(case.case_id, "failed", "empty issue"))
        return
    try:
        inspect_patch(bug_patch.read_text(), allowed_paths=case.allowed_paths)
    except PatchRejected as exc:
        report.failures.append(f"{case.case_id}: bug patch rejected ({exc.code}): {exc}")
        report.checks.append(CaseCheck(case.case_id, "failed", "bug patch rejected"))
        return

    oracle = _resolve_oracle(case, private)
    if oracle is None:
        if not private.present and _oracle_is_private(case):
            result = _pytest_visible(runner, bug_patch, None)
            if _visible_ok(result):
                report.checks.append(
                    CaseCheck(
                        case.case_id,
                        "passed",
                        "visible_buggy=passed protected=skipped reference_fix=skipped",
                    )
                )
            else:
                _fail_result(case, report, "visible tests on the buggy fixture", result)
            return
        report.failures.append(f"{case.case_id}: protected tests or reference fix are missing")
        report.checks.append(CaseCheck(case.case_id, "failed", "oracle files missing"))
        return

    protected_dir, reference_fix = oracle
    with tempfile.TemporaryDirectory(prefix=f"vra-{case.case_id}-") as tmp:
        root = Path(tmp)
        buggy = root / "buggy"
        fixed = root / "fixed"
        try:
            materialize(buggy, bug_patch)
            materialize(fixed, bug_patch, reference_fix)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode(errors="replace").strip()
            report.failures.append(f"{case.case_id}: git apply failed ({detail[:200]})")
            report.checks.append(CaseCheck(case.case_id, "failed", "git apply failed"))
            return
        buggy_visible = runner.run_visible_tests(buggy)
        if not _visible_ok(buggy_visible):
            _fail_result(case, report, "visible tests on the buggy fixture", buggy_visible)
            return
        buggy_protected = runner.run_protected_tests(buggy, protected_dir)
        if not _protected_failed(buggy_protected):
            _fail_result(case, report, "protected tests on the buggy fixture", buggy_protected, expect_failure=True)
            return
        fixed_visible = runner.run_visible_tests(fixed)
        if not _visible_ok(fixed_visible):
            _fail_result(case, report, "visible tests after the reference fix", fixed_visible)
            return
        fixed_protected = runner.run_protected_tests(fixed, protected_dir)
        if not _visible_ok(fixed_protected):
            _fail_result(case, report, "protected tests after the reference fix", fixed_protected)
            return
    report.checks.append(
        CaseCheck(
            case.case_id,
            "passed",
            "visible_buggy=passed protected_buggy=failed visible_fixed=passed protected_fixed=passed",
        )
    )


def _validate_probe(case: ProbeCase, private: PrivateDir, report: ValidationReport) -> None:
    issue = _resolve(case.issue_path, private)
    if issue is None or not issue.read_text().strip():
        report.failures.append(f"{case.case_id}: issue is missing or empty")
        report.checks.append(CaseCheck(case.case_id, "failed", "issue missing"))
        return
    report.checks.append(CaseCheck(case.case_id, "passed", "issue=ok scoring=private"))


def _pytest_visible(runner: Runner, bug_patch: Path, reference_fix: Path | None) -> RunnerResult:
    with tempfile.TemporaryDirectory(prefix="vra-visible-") as tmp:
        workspace = Path(tmp) / "work"
        materialize(workspace, bug_patch, reference_fix)
        return runner.run_visible_tests(workspace)


def _visible_ok(result: RunnerResult) -> bool:
    return (
        result.infra_error is None
        and not result.timed_out
        and not result.tests_modified
        and not result.output_truncated
        and result.exit_code == 0
    )


def _protected_failed(result: RunnerResult) -> bool:
    """A real test failure is exit code 1. Timeouts and startup errors are not."""
    return (
        result.infra_error is None
        and not result.timed_out
        and not result.tests_modified
        and result.exit_code == 1
    )


def _fail_result(
    case: BugCase,
    report: ValidationReport,
    what: str,
    result: RunnerResult,
    *,
    expect_failure: bool = False,
) -> None:
    if result.infra_error:
        detail = result.infra_error
    elif result.timed_out:
        detail = "timed out"
    elif result.tests_modified:
        detail = "visible tests were modified"
    elif expect_failure and result.exit_code == 0:
        detail = "protected suite passed; the bug was not detected"
    else:
        tail = (result.stdout or result.stderr).strip().splitlines()
        detail = tail[-1] if tail else f"exit {result.exit_code}"
    report.failures.append(f"{case.case_id}: {what}: {detail}")
    report.checks.append(CaseCheck(case.case_id, "failed", what))


def _missing(case: BugCase, private: PrivateDir, report: ValidationReport, what: str) -> None:
    if not private.present:
        report.checks.append(CaseCheck(case.case_id, "skipped", f"{what} is not in the public repo"))
        return
    report.failures.append(f"{case.case_id}: {what} is missing from the private benchmark directory")
    report.checks.append(CaseCheck(case.case_id, "failed", f"missing {what}"))


def _resolve_oracle(case: BugCase, private: PrivateDir) -> tuple[Path, Path] | None:
    protected = _resolve(case.evaluator_test_dir, private)
    reference = _resolve(case.reference_fix, private)
    if protected is None or reference is None:
        return None
    if not any(protected.glob("test_*.py")):
        return None
    return protected, reference


def _oracle_is_private(case: BugCase) -> bool:
    """True when the oracle path is not shipped in this repository."""
    return not (repo_root() / case.evaluator_test_dir).exists()


def _resolve(relative: str, private: PrivateDir) -> Path | None:
    _reject_relative(relative)
    public = repo_root() / relative
    if public.exists():
        return public
    if private.present:
        candidate = private.configured / relative
        if candidate.exists():
            return candidate
    return None


def _reject_relative(relative: str) -> None:
    parts = relative.replace("\\", "/").split("/")
    if relative.startswith("/") or ".." in parts:
        raise ValueError(f"unsafe manifest path: {relative}")


def _load_split(name: str, private: PrivateDir, report: ValidationReport) -> ManifestFile | None:
    if name == "heldout":
        if not private.present:
            report.checks.append(
                CaseCheck("H01–H04", "skipped", "private benchmark directory is not present")
            )
            return None
        path = private.configured / HELD_OUT_MANIFEST
        if not path.is_file():
            report.failures.append(
                f"Private directory is present but {HELD_OUT_MANIFEST} is missing: {private.configured}"
            )
            return None
    else:
        path = repo_root() / PUBLIC_MANIFESTS[name]
        if not path.is_file():
            report.failures.append(f"manifest is missing: {PUBLIC_MANIFESTS[name]}")
            return None
    try:
        manifest = ManifestFile.model_validate_json(path.read_text())
    except ModelError as exc:
        report.failures.append(f"{path}: {exc.errors()[0]['msg']}")
        return None
    expected = {"dev": "dev", "probe": "probe", "example": "example", "heldout": "heldout"}[name]
    if manifest.split != expected:
        report.failures.append(f"{path}: split is {manifest.split}, expected {expected}")
        return None
    return manifest


def _validate_probe_scoring(private: PrivateDir, report: ValidationReport) -> None:
    path = private.configured / PROBE_SCORING
    if not path.is_file():
        report.failures.append(f"Private directory is present but {PROBE_SCORING} is missing")
        return
    try:
        score = ProbeScoreFile.model_validate_json(path.read_text())
    except ModelError as exc:
        report.failures.append(f"{PROBE_SCORING}: {exc.errors()[0]['msg']}")
        return
    ids = [entry.case_id for entry in score.probes]
    if ids != ["P01", "P02", "P03"]:
        report.failures.append(f"{PROBE_SCORING}: probes must be P01, P02, P03 in order")


def _reject_public_leaks(report: ValidationReport) -> None:
    root = repo_root()
    leaked = [
        root / "evaluator_private",
        root / "benchmark" / "manifests" / "heldout.json",
        *(root / "benchmark" / "cases" / case_id for case_id in ("H01", "H02", "H03", "H04")),
    ]
    for path in leaked:
        if path.exists():
            report.failures.append(f"held-out or protected material must not live in the public repo: {path}")


def _git_apply(patch: Path, workspace: Path) -> None:
    subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch)],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
