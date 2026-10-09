"""Unified diffs for source snapshots. Nothing here imports the fixture."""

from __future__ import annotations

import difflib
from pathlib import Path


def unified_diff(path: str, before: str, after: str) -> str:
    """Git-style diff for an existing file. Empty when the bytes match."""
    if before == after:
        return ""
    body = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if not body:
        return ""
    if not body.endswith("\n"):
        body += "\n"
    return f"diff --git a/{path} b/{path}\n{body}"


def new_file_diff(path: str, content: str) -> str:
    """Diff that creates ``path``. The patch policy allows this only for agent tests."""
    text = content if content.endswith("\n") else content + "\n"
    lines = text.splitlines()
    hunk = [f"@@ -0,0 +1,{len(lines)} @@", *[f"+{line}" for line in lines]]
    body = "\n".join(hunk) + "\n"
    return (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"{body}"
    )


def snapshot_sources(workspace: Path, allowed_paths: list[str] | tuple[str, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for relative in allowed_paths:
        path = workspace / relative
        if path.is_symlink() or not path.is_file():
            continue
        found[relative] = path.read_text(encoding="utf-8")
    return found


def diff_against_snapshot(workspace: Path, baseline: dict[str, str]) -> tuple[str, list[str]]:
    """Net source diff from the buggy baseline. Agent tests are not included."""
    sections: list[str] = []
    changed: list[str] = []
    for relative, before in baseline.items():
        path = workspace / relative
        after = path.read_text(encoding="utf-8") if path.is_file() and not path.is_symlink() else ""
        section = unified_diff(relative, before, after)
        if section:
            sections.append(section)
            changed.append(relative)
    return "".join(sections), changed
