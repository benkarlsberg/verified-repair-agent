"""Validate a unified diff before it is applied.

The checks are a curated task policy: allowlisted source, new files only under
``agent_tests/test_*.py``, size limits, and a ``git apply --check --recount``
against a disposable copy of the workspace. ``--recount`` tolerates a wrong
hunk line count. It does not accept any format other than a git unified diff.
They are not a detector for hostile code. Nothing here imports or executes
the fixture.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_CHANGED_LINES = 200
MAX_FILES = 5
AGENT_TEST = re.compile(r"^agent_tests/test_[A-Za-z0-9_]+\.py$")
_DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
UNIFIED_DIFF_EXAMPLE = (
    "diff --git a/order_service/pricing.py b/order_service/pricing.py\n"
    "--- a/order_service/pricing.py\n"
    "+++ b/order_service/pricing.py\n"
    "@@ -1,3 +1,3 @@\n"
    " def discount_cents(subtotal):\n"
    "-    if subtotal > 10000:\n"
    "+    if subtotal >= 10000:\n"
    "         return subtotal // 10\n"
)
_FORMAT_EXPECTED = (
    "Expected a git unified diff with diff --git, ---, +++, and @@ lines. "
    "*** Begin Patch is not accepted."
)

_FORBIDDEN_NAMES = {
    "pyproject.toml",
    "uv.lock",
    "pytest.ini",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "requirements.txt",
    "conftest.py",
    "Dockerfile",
}
_FORBIDDEN_PREFIXES = (
    "tests_visible/",
    "tests/",
    "docker/",
    "evaluator_private/",
    ".github/",
    "benchmark/",
    "examples/",
    "agent_tests/",
)


class PatchRejected(Exception):
    """The diff violates patch policy. ``code`` is a stable short reason."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AcceptedPatch:
    files: tuple[str, ...]
    changed_lines: int


def validate_patch(
    patch: str,
    *,
    allowed_paths: list[str] | tuple[str, ...],
    workspace: Path,
    max_changed_lines: int = MAX_CHANGED_LINES,
    max_files: int = MAX_FILES,
) -> AcceptedPatch:
    """Accept a diff that is safe to apply onto ``workspace``.

    ``git apply --check --recount`` runs in a temporary copy. ``workspace`` is
    not modified and the fixture package is not imported.
    """
    accepted = inspect_patch(
        patch,
        allowed_paths=allowed_paths,
        max_changed_lines=max_changed_lines,
        max_files=max_files,
    )
    ensure_applies(patch, workspace)
    return accepted


def inspect_patch(
    patch: str,
    *,
    allowed_paths: list[str] | tuple[str, ...],
    max_changed_lines: int = MAX_CHANGED_LINES,
    max_files: int = MAX_FILES,
) -> AcceptedPatch:
    """Parse and reject a diff without applying it or importing target code."""
    if not isinstance(patch, str) or not patch.strip():
        raise PatchRejected("empty", "patch is empty")
    if "\0" in patch or "GIT binary patch" in patch or "\nBinary files " in f"\n{patch}":
        raise PatchRejected("binary", "binary patches are rejected")

    sections = re.split(r"(?=^diff --git )", patch, flags=re.M)
    sections = [section for section in sections if section.startswith("diff --git ")]
    if not sections:
        raise PatchRejected("format", f"patch must be a git unified diff. {_FORMAT_EXPECTED}")

    allowed = set(allowed_paths)
    files: list[str] = []
    changed_lines = 0
    for section in sections:
        path, lines = _inspect_section(section, allowed)
        files.append(path)
        changed_lines += lines

    if len(files) > max_files:
        raise PatchRejected("file_limit", f"patch touches {len(files)} files; limit is {max_files}")
    if changed_lines > max_changed_lines:
        raise PatchRejected(
            "line_limit",
            f"patch changes {changed_lines} lines; limit is {max_changed_lines}",
        )
    return AcceptedPatch(files=tuple(files), changed_lines=changed_lines)


def ensure_applies(patch: str, workspace: Path) -> None:
    """Run ``git apply --check --recount`` in a disposable copy of ``workspace``."""
    if not workspace.is_dir():
        raise PatchRejected("workspace", f"workspace does not exist: {workspace}")
    with tempfile.TemporaryDirectory(prefix="vra-apply-") as tmp:
        checkout = Path(tmp) / "checkout"
        shutil.copytree(
            workspace,
            checkout,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            symlinks=False,
        )
        completed = subprocess.run(
            ["git", "apply", "--check", "--recount", "--whitespace=nowarn", "-"],
            input=patch.encode(),
            cwd=checkout,
            capture_output=True,
            check=False,
        )
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip() or "git apply --check failed"
        raise PatchRejected("apply_check", detail.splitlines()[0][:300])


def _inspect_section(section: str, allowed: set[str]) -> tuple[str, int]:
    lines = section.splitlines()
    header = _DIFF_HEADER.match(lines[0])
    if header is None:
        raise PatchRejected("format", f"malformed diff header. {_FORMAT_EXPECTED}")
    old_header = _strip_prefix(header.group(1), "a/")
    new_header = _strip_prefix(header.group(2), "b/")
    old_path: str | None = None
    new_path: str | None = None
    changed = 0
    for line in lines[1:]:
        if line.startswith(("rename from ", "rename to ", "similarity index ", "dissimilarity index ")):
            raise PatchRejected("rename", "renames are rejected")
        if line.startswith("deleted file mode"):
            raise PatchRejected("delete", "file deletions are rejected")
        if line.startswith(("old mode ", "new mode ")):
            raise PatchRejected("mode", "permission changes are rejected")
        if "120000" in line and "mode" in line:
            raise PatchRejected("symlink", "symlink changes are rejected")
        if line.startswith("--- "):
            old_path = _diff_path(line[4:])
        elif line.startswith("+++ "):
            new_path = _diff_path(line[4:])
        elif (line.startswith("+") and not line.startswith("+++")) or (
            line.startswith("-") and not line.startswith("---")
        ):
            changed += 1
            if line.startswith("+") and "evaluator_private" in line[1:]:
                raise PatchRejected("evaluator_path", "patch references evaluator_private")

    if old_path is None or new_path is None:
        raise PatchRejected("format", f"patch is missing ---/+++ paths. {_FORMAT_EXPECTED}")
    if new_path == "/dev/null" or old_path == "/dev/null" and new_path == "/dev/null":
        raise PatchRejected("delete", "file deletions are rejected")
    if old_path == "/dev/null":
        destination = new_path
        if AGENT_TEST.fullmatch(destination) is None:
            raise PatchRejected("new_file", "new files are allowed only as agent_tests/test_*.py")
    else:
        destination = new_path
        if destination != old_path:
            raise PatchRejected("rename", "renames are rejected")
    if destination != new_header and new_header != "/dev/null":
        raise PatchRejected("format", "diff header does not match the patched path")
    if old_header not in {destination, "/dev/null"} and old_path != "/dev/null":
        raise PatchRejected("format", "diff header does not match the source path")
    _reject_forbidden(destination)
    if destination not in allowed and AGENT_TEST.fullmatch(destination) is None:
        raise PatchRejected("allowlist", f"path is not allowed: {destination}")
    if "evaluator_private" in destination:
        raise PatchRejected("evaluator_path", "patch touches evaluator_private")
    return destination, changed


def _diff_path(raw: str) -> str:
    token = raw.strip().split("\t", 1)[0].strip().strip('"')
    if token == "/dev/null":
        return token
    prefix = "b/" if token.startswith("b/") else "a/" if token.startswith("a/") else ""
    return _reject_traversal(_strip_prefix(token, prefix) if prefix else token)


def _strip_prefix(path: str, prefix: str) -> str:
    path = path.strip().strip('"')
    if path.startswith(prefix):
        path = path[len(prefix) :]
    return _reject_traversal(path)


def _reject_traversal(path: str) -> str:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or normalized == "" or ".." in parts or any(part == "" for part in parts):
        raise PatchRejected("traversal", f"path escapes the workspace: {path}")
    return normalized


def _reject_forbidden(path: str) -> None:
    name = path.rsplit("/", 1)[-1]
    if name in _FORBIDDEN_NAMES or path.endswith("/conftest.py"):
        raise PatchRejected("immutable", f"path is immutable: {path}")
    for prefix in _FORBIDDEN_PREFIXES:
        if path.startswith(prefix) and not path.startswith("agent_tests/"):
            raise PatchRejected("immutable", f"path is immutable: {path}")
