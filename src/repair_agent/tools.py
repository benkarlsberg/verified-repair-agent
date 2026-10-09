"""The six tools the repair loop may call.

Paths resolve only inside the case workspace. The host repository, the
private benchmark directory, and evaluator files are not reachable from
here. Patch checks go through the existing policy, and visible tests go
through the existing Docker runner.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from repair_agent.patch_policy import UNIFIED_DIFF_EXAMPLE, PatchRejected, inspect_patch, validate_patch
from repair_agent.runner import Runner, RunnerError

MAX_READ_BYTES = 20 * 1024
MAX_MATCHES = 50
MAX_LINE_CHARS = 200
_BLOCKED_PARTS = frozenset({"evaluator_private", "private", ".git", ".hg", ".svn"})


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = Field(min_length=1)


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1, max_length=200)
    path: str | None = None


class ApplyPatchArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    patch: str = Field(min_length=1)


class FinishArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim: Literal["repaired", "unresolved", "insufficient_evidence"]
    summary: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


TOOL_MODELS: dict[str, type[BaseModel]] = {
    "list_files": EmptyArgs,
    "read_file": ReadFileArgs,
    "search_text": SearchArgs,
    "apply_patch": ApplyPatchArgs,
    "run_visible_tests": EmptyArgs,
    "finish": FinishArgs,
}

_CLAIM = {"repaired", "unresolved", "insufficient_evidence"}


def tool_schemas() -> list[dict[str, Any]]:
    """Responses API function tools. Names match the section 7 surface."""
    return [
        _function(
            "list_files",
            "List allowlisted source files, visible tests, and agent_tests/test_*.py in this workspace.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        _function(
            "read_file",
            "Read a UTF-8 text file inside the workspace. At most 20 KB. Symlinks and paths outside the workspace are rejected.",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Workspace-relative path."}},
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        _function(
            "search_text",
            "Search visible source and tests for a literal string. This is not a regular expression and it does not run a shell.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "description": "Optional file or directory to limit the search."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _function(
            "apply_patch",
            "Apply a git unified diff. Source edits must stay on the allowlisted paths. "
            "New files are allowed only as agent_tests/test_*.py. "
            "Write and run a reproduction before the first source edit. "
            "The patch argument must be a unified diff in this shape, including the "
            "diff --git, ---, +++, and @@ lines. *** Begin Patch is not accepted.\n"
            f"{UNIFIED_DIFF_EXAMPLE}",
            {
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": "A git unified diff with diff --git, ---, +++, and @@ lines. "
                        "*** Begin Patch is not accepted. "
                        f"Example:\n{UNIFIED_DIFF_EXAMPLE}",
                    }
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
        ),
        _function(
            "run_visible_tests",
            "Run the ordinary visible suite and any agent_tests in a fresh container. There is no shell argument.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        _function(
            "finish",
            "Stop. claim is repaired, unresolved, or insufficient_evidence. Call this when the investigation is done.",
            {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "enum": ["repaired", "unresolved", "insufficient_evidence"]},
                    "summary": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "limitations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim", "summary"],
                "additionalProperties": False,
            },
        ),
    ]


def _function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "name": name, "description": description, "parameters": parameters}


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class ToolOutcome:
    ok: bool
    model_text: str
    source_applied: bool = False
    finished: bool = False
    claim: str | None = None
    summary: str = ""
    limitations: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    budget_stop: str | None = None
    visible_output: str | None = None
    reproduction_observed: bool = False
    tests_passed: bool | None = None


class ToolSurface:
    """Dispatch validated tool calls against one materialized workspace."""

    def __init__(
        self,
        workspace: Path,
        allowed_paths: list[str] | tuple[str, ...],
        runner: Runner,
        *,
        max_changed_lines: int,
        max_files: int,
        max_source_patches: int,
        test_timeout_seconds: float,
    ) -> None:
        self.workspace = workspace
        self.allowed_paths = list(allowed_paths)
        self.runner = runner
        self.max_changed_lines = max_changed_lines
        self.max_files = max_files
        self.max_source_patches = max_source_patches
        self.test_timeout_seconds = test_timeout_seconds
        self.source_edits_allowed = False
        self.source_patches_applied = 0

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        try:
            if name == "list_files":
                return self._ok({"files": self.list_files()})
            if name == "read_file":
                return self._ok(self.read_file(str(arguments["path"])))
            if name == "search_text":
                return self._ok(self.search_text(str(arguments["query"]), arguments.get("path")))
            if name == "apply_patch":
                return self.apply_patch(str(arguments["patch"]))
            if name == "run_visible_tests":
                return self.run_visible_tests()
            if name == "finish":
                return self.finish(arguments)
            return self._error("unknown_tool", f"unknown tool: {name}")
        except ToolError as exc:
            return self._error(exc.code, str(exc))

    def list_files(self) -> list[str]:
        found: list[str] = []
        for relative in self.allowed_paths:
            path = self.workspace / relative
            if path.is_file() and not path.is_symlink():
                found.append(relative)
        visible = self.workspace / "tests_visible"
        if visible.is_dir() and not visible.is_symlink():
            for path in sorted(visible.rglob("*")):
                if not _is_listed_file(path):
                    continue
                found.append(path.relative_to(self.workspace).as_posix())
        agent = self.workspace / "agent_tests"
        if agent.is_dir() and not agent.is_symlink():
            for path in sorted(agent.glob("test_*.py")):
                if path.is_file() and not path.is_symlink():
                    found.append(path.relative_to(self.workspace).as_posix())
        return found

    def read_file(self, relative: str) -> dict[str, Any]:
        path = self._resolve(relative)
        if not path.is_file():
            raise ToolError("not_a_file", "path is not a file")
        data = path.read_bytes()
        truncated = len(data) > MAX_READ_BYTES
        chunk = data[:MAX_READ_BYTES]
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("binary", "file is not UTF-8 text") from exc
        return {"path": relative, "truncated": truncated, "text": text}

    def search_text(self, query: str, relative: str | None) -> dict[str, Any]:
        if not query:
            raise ToolError("empty_query", "query is empty")
        files = self.list_files()
        if relative:
            prefix = str(relative).rstrip("/")
            files = [item for item in files if item == prefix or item.startswith(prefix + "/")]
        matches: list[dict[str, Any]] = []
        for item in files:
            if len(matches) >= MAX_MATCHES:
                break
            text = self.read_file(item)["text"]
            for number, line in enumerate(text.splitlines(), start=1):
                if query not in line:
                    continue
                matches.append(
                    {
                        "path": item,
                        "line": number,
                        "text": line[:MAX_LINE_CHARS],
                    }
                )
                if len(matches) >= MAX_MATCHES:
                    break
        return {"query": query, "matches": matches, "truncated": len(matches) >= MAX_MATCHES}

    def apply_patch(self, patch: str) -> ToolOutcome:
        try:
            inspected = inspect_patch(
                patch,
                allowed_paths=self.allowed_paths,
                max_changed_lines=self.max_changed_lines,
                max_files=self.max_files,
            )
        except PatchRejected as exc:
            return self._error(exc.code, str(exc))
        source_files = [item for item in inspected.files if not item.startswith("agent_tests/")]
        if source_files and not self.source_edits_allowed:
            return self._error(
                "reproduction_required",
                "Add a test under agent_tests/test_*.py and run the visible tests before editing source.",
            )
        if source_files and self.source_patches_applied >= self.max_source_patches:
            return ToolOutcome(
                ok=False,
                model_text=_dump(
                    {
                        "ok": False,
                        "error": "patch_budget",
                        "message": "The source patch budget is exhausted. The latest valid source diff will be kept.",
                    }
                ),
                budget_stop="patch_attempts",
            )
        try:
            validate_patch(
                patch,
                allowed_paths=self.allowed_paths,
                workspace=self.workspace,
                max_changed_lines=self.max_changed_lines,
                max_files=self.max_files,
            )
        except PatchRejected as exc:
            return self._error(exc.code, _safe(str(exc)))
        completed = subprocess.run(
            ["git", "apply", "--recount", "--whitespace=nowarn", "-"],
            input=patch.encode(),
            cwd=self.workspace,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode(errors="replace").strip().splitlines()
            message = detail[0][:300] if detail else "git apply failed"
            return self._error("apply_check", _safe(message))
        if source_files:
            self.source_patches_applied += 1
        return ToolOutcome(
            ok=True,
            model_text=_dump({"ok": True, "files": list(inspected.files), "source": bool(source_files)}),
            source_applied=bool(source_files),
        )

    def run_visible_tests(self) -> ToolOutcome:
        try:
            result = self.runner.run_visible_tests(
                self.workspace,
                timeout_seconds=self.test_timeout_seconds,
            )
        except RunnerError as exc:
            return self._error("runner", _safe(str(exc)))
        output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        passed = (
            result.exit_code == 0
            and not result.timed_out
            and result.infra_error is None
            and not result.tests_modified
        )
        payload = {
            "ok": passed,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "truncated": result.truncated or result.output_truncated,
            "runner_failed": result.infra_error is not None,
            "output": output,
        }
        return ToolOutcome(
            ok=passed,
            model_text=_dump(payload),
            visible_output=output,
            reproduction_observed=self._has_agent_tests(),
            tests_passed=passed,
        )

    def finish(self, arguments: dict[str, Any]) -> ToolOutcome:
        claim = arguments.get("claim")
        if claim not in _CLAIM:
            raise ToolError("claim", "claim must be repaired, unresolved, or insufficient_evidence")
        summary = str(arguments.get("summary") or "").strip()
        if not summary:
            raise ToolError("summary", "summary is required")
        limitations = [str(item) for item in arguments.get("limitations") or []]
        evidence = [str(item) for item in arguments.get("evidence") or []]
        return ToolOutcome(
            ok=True,
            model_text=_dump({"ok": True, "claim": claim, "stopped": True}),
            finished=True,
            claim=claim,
            summary=summary,
            limitations=limitations,
            evidence=evidence,
        )

    def _has_agent_tests(self) -> bool:
        root = self.workspace / "agent_tests"
        return root.is_dir() and any(root.glob("test_*.py"))

    def _resolve(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise ToolError("path", "path is required")
        if relative.startswith(("/", "\\")) or "\\" in relative:
            raise ToolError("traversal", "absolute paths are rejected")
        parts = Path(relative).parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ToolError("traversal", "path escapes the workspace")
        if any(part in _BLOCKED_PARTS for part in parts):
            raise ToolError("forbidden", "path is not available in this workspace")
        current = self.workspace
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ToolError("symlink", "symlinks are rejected")
        if not current.exists():
            raise ToolError("missing", "file does not exist")
        root = self.workspace.resolve()
        resolved = current.resolve()
        if not resolved.is_relative_to(root):
            raise ToolError("traversal", "path escapes the workspace")
        return current

    def _ok(self, payload: dict[str, Any]) -> ToolOutcome:
        body = dict(payload)
        body["ok"] = True
        return ToolOutcome(ok=True, model_text=_dump(body))

    def _error(self, code: str, message: str) -> ToolOutcome:
        return ToolOutcome(ok=False, model_text=_dump({"ok": False, "error": code, "message": message}))


def parse_tool_arguments(name: str, arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return validated arguments, or None when the model output is the wrong shape."""
    model = TOOL_MODELS.get(name)
    if model is None or arguments is None:
        return None
    try:
        return model.model_validate(arguments).model_dump()
    except ValidationError:
        return None


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _safe(message: str) -> str:
    """Drop absolute host paths before a message is shown to the model."""
    parts = []
    for token in message.split():
        if token.startswith("/") and len(token) > 1:
            parts.append("[path]")
        else:
            parts.append(token)
    return " ".join(parts)[:500]


def _is_listed_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
        return False
    return True
