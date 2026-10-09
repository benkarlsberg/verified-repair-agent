"""Tool paths stay inside the case workspace."""

from __future__ import annotations

from pathlib import Path

from repair_agent.diffs import new_file_diff, unified_diff
from repair_agent.runner import RunnerResult
from repair_agent.tools import ToolSurface


class _Runner:
    def run_visible_tests(self, workspace: Path, timeout_seconds: float | None = None) -> RunnerResult:
        del workspace, timeout_seconds
        return RunnerResult(exit_code=0, stdout="ok\n", stderr="")


def _surface(tmp_path: Path) -> ToolSurface:
    source = tmp_path / "order_service"
    source.mkdir()
    (source / "pricing.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests_visible"
    tests.mkdir()
    (tests / "test_pricing.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "secrets.txt").write_text("host-secret\n", encoding="utf-8")
    hidden = tmp_path / "evaluator_private"
    hidden.mkdir()
    (hidden / "test_hidden.py").write_text("SENTINEL_HIDDEN\n", encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    return ToolSurface(
        tmp_path,
        ["order_service/pricing.py"],
        _Runner(),  # type: ignore[arg-type]
        max_changed_lines=200,
        max_files=5,
        max_source_patches=3,
        test_timeout_seconds=30,
    )


def test_list_read_and_search_stay_inside_the_workspace(tmp_path: Path) -> None:
    surface = _surface(tmp_path)
    files = surface.list_files()
    assert "order_service/pricing.py" in files
    assert "tests_visible/test_pricing.py" in files
    assert "secrets.txt" not in files
    assert all("evaluator_private" not in item for item in files)

    blocked = surface.dispatch("read_file", {"path": "../outside.txt"})
    assert '"ok": false' in blocked.model_text
    hidden = surface.dispatch("read_file", {"path": "evaluator_private/test_hidden.py"})
    assert "SENTINEL_HIDDEN" not in hidden.model_text
    secret = surface.dispatch("search_text", {"query": "host-secret"})
    assert '"matches": []' in secret.model_text
    assert "secrets.txt" not in secret.model_text
    literal = surface.dispatch("search_text", {"query": "v.lue"})
    assert '"matches": []' in literal.model_text


def test_invalid_source_edit_is_rejected_and_the_file_stays(tmp_path: Path) -> None:
    surface = _surface(tmp_path)
    surface.source_edits_allowed = True
    original = (tmp_path / "tests_visible" / "test_pricing.py").read_text(encoding="utf-8")
    patch = unified_diff(
        "tests_visible/test_pricing.py",
        original,
        "def test_ok():\n    assert False\n",
    )
    outcome = surface.dispatch("apply_patch", {"patch": patch})
    assert outcome.ok is False
    assert "immutable" in outcome.model_text or "allowlist" in outcome.model_text
    assert (tmp_path / "tests_visible" / "test_pricing.py").read_text(encoding="utf-8") == original
    assert outcome.source_applied is False


def test_source_edit_before_a_reproduction_is_rejected(tmp_path: Path) -> None:
    surface = _surface(tmp_path)
    patch = unified_diff("order_service/pricing.py", "value = 1\n", "value = 2\n")
    outcome = surface.dispatch("apply_patch", {"patch": patch})
    assert outcome.ok is False
    assert "reproduction_required" in outcome.model_text
    assert (tmp_path / "order_service" / "pricing.py").read_text(encoding="utf-8") == "value = 1\n"


def test_agent_test_can_be_added_and_then_source_can_change(tmp_path: Path) -> None:
    surface = _surface(tmp_path)
    created = new_file_diff("agent_tests/test_repro.py", "def test_repro():\n    assert True\n")
    added = surface.dispatch("apply_patch", {"patch": created})
    assert added.ok is True
    assert (tmp_path / "agent_tests" / "test_repro.py").is_file()
    surface.source_edits_allowed = True
    changed = surface.dispatch(
        "apply_patch",
        {"patch": unified_diff("order_service/pricing.py", "value = 1\n", "value = 2\n")},
    )
    assert changed.ok is True
    assert changed.source_applied is True
    assert (tmp_path / "order_service" / "pricing.py").read_text(encoding="utf-8") == "value = 2\n"


def test_wrong_hunk_count_applies_and_begin_patch_is_rejected(tmp_path: Path) -> None:
    surface = _surface(tmp_path)
    surface.source_edits_allowed = True
    patch = unified_diff("order_service/pricing.py", "value = 1\n", "value = 2\n")
    lines = ["@@ -1,40 +1,40 @@" if line.startswith("@@") else line for line in patch.splitlines()]
    bad = "\n".join(lines) + "\n"
    applied = surface.dispatch("apply_patch", {"patch": bad})
    assert applied.ok is True
    assert (tmp_path / "order_service" / "pricing.py").read_text(encoding="utf-8") == "value = 2\n"
    begin = surface.dispatch(
        "apply_patch",
        {"patch": "*** Begin Patch\n*** Update File: order_service/pricing.py\n*** End Patch\n"},
    )
    assert begin.ok is False
    assert "format" in begin.model_text
    assert "diff --git" in begin.model_text
    assert "Begin Patch" in begin.model_text
    assert (tmp_path / "order_service" / "pricing.py").read_text(encoding="utf-8") == "value = 2\n"


def test_fourth_source_patch_stops_the_budget(tmp_path: Path) -> None:
    surface = _surface(tmp_path)
    surface.source_edits_allowed = True
    surface.max_source_patches = 1
    surface.source_patches_applied = 1
    outcome = surface.dispatch(
        "apply_patch",
        {"patch": unified_diff("order_service/pricing.py", "value = 1\n", "value = 9\n")},
    )
    assert outcome.budget_stop == "patch_attempts"
    assert (tmp_path / "order_service" / "pricing.py").read_text(encoding="utf-8") == "value = 1\n"
