"""Patch policy checks from sections 7 and 8. These do not import the fixture."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from repair_agent.patch_policy import (
    AcceptedPatch,
    PatchRejected,
    format_apply_stderr,
    inspect_patch,
    prepare_patch,
    validate_patch,
)


def _diff(path: str, before: str, after: str) -> str:
    import difflib

    body = "".join(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return f"diff --git a/{path} b/{path}\n{body}"


def test_policy_does_not_import_the_fixture() -> None:
    for name in list(sys.modules):
        if name == "order_service" or name.startswith("order_service."):
            del sys.modules[name]
    inspect_patch(
        _diff("order_service/pricing.py", "value = 1\n", "value = 2\n"),
        allowed_paths=["order_service/pricing.py"],
    )
    assert "order_service" not in sys.modules


def test_allowlisted_edit_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "order_service"
    source.mkdir()
    target = source / "pricing.py"
    target.write_text("value = 1\n")
    patch = _diff("order_service/pricing.py", "value = 1\n", "value = 2\n")
    accepted = validate_patch(patch, allowed_paths=["order_service/pricing.py"], workspace=tmp_path)
    assert accepted == AcceptedPatch(files=("order_service/pricing.py",), changed_lines=2)
    assert target.read_text() == "value = 1\n"


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    patch = textwrap.dedent(
        """\
        diff --git a/order_service/../../etc/passwd b/order_service/../../etc/passwd
        --- a/order_service/../../etc/passwd
        +++ b/order_service/../../etc/passwd
        @@ -1 +1 @@
        -root
        +nope
        """
    )
    with pytest.raises(PatchRejected) as caught:
        inspect_patch(patch, allowed_paths=["order_service/pricing.py"])
    assert caught.value.code == "traversal"


def test_rejects_delete_rename_mode_symlink_and_binary() -> None:
    samples = {
        "delete": """\
            diff --git a/order_service/pricing.py b/order_service/pricing.py
            deleted file mode 100644
            --- a/order_service/pricing.py
            +++ /dev/null
            @@ -1 +0,0 @@
            -value = 1
            """,
        "rename": """\
            diff --git a/order_service/pricing.py b/order_service/other.py
            similarity index 100
            rename from order_service/pricing.py
            rename to order_service/other.py
            """,
        "mode": """\
            diff --git a/order_service/pricing.py b/order_service/pricing.py
            old mode 100644
            new mode 100755
            """,
        "symlink": """\
            diff --git a/order_service/pricing.py b/order_service/pricing.py
            new file mode 120000
            --- /dev/null
            +++ b/order_service/pricing.py
            @@ -0,0 +1 @@
            +/tmp/elsewhere
            """,
        "binary": """\
            diff --git a/order_service/pricing.py b/order_service/pricing.py
            index 111..222
            GIT binary patch
            literal 0
            """,
    }
    for code, body in samples.items():
        with pytest.raises(PatchRejected) as caught:
            inspect_patch(textwrap.dedent(body), allowed_paths=["order_service/pricing.py"])
        assert caught.value.code == code


def test_rejects_visible_tests_config_and_off_allowlist() -> None:
    def patch_for(path: str) -> str:
        return _diff(path, "x = 1\n", "x = 2\n")

    for path in (
        "tests_visible/test_pricing.py",
        "pyproject.toml",
        "conftest.py",
        "order_service/orders.py",
    ):
        with pytest.raises(PatchRejected):
            inspect_patch(patch_for(path), allowed_paths=["order_service/pricing.py"])


def test_agent_tests_are_allowed_and_other_new_files_are_not(tmp_path: Path) -> None:
    (tmp_path / "order_service").mkdir()
    (tmp_path / "order_service" / "pricing.py").write_text("value = 1\n")
    created = textwrap.dedent(
        """\
        diff --git a/agent_tests/test_repro.py b/agent_tests/test_repro.py
        new file mode 100644
        --- /dev/null
        +++ b/agent_tests/test_repro.py
        @@ -0,0 +1,2 @@
        +def test_repro():
        +    assert True
        """
    )
    accepted = validate_patch(created, allowed_paths=["order_service/pricing.py"], workspace=tmp_path)
    assert accepted.files == ("agent_tests/test_repro.py",)
    helper = created.replace("test_repro.py", "helper.py")
    with pytest.raises(PatchRejected) as caught:
        inspect_patch(helper, allowed_paths=["order_service/pricing.py"])
    assert caught.value.code == "new_file"


def test_line_and_file_limits() -> None:
    added = "".join(f"+line {index}\n" for index in range(201))
    wide = (
        "diff --git a/order_service/pricing.py b/order_service/pricing.py\n"
        "--- a/order_service/pricing.py\n"
        "+++ b/order_service/pricing.py\n"
        "@@ -1,0 +1,201 @@\n"
        f"{added}"
    )
    with pytest.raises(PatchRejected) as caught:
        inspect_patch(wide, allowed_paths=["order_service/pricing.py"])
    assert caught.value.code == "line_limit"

    parts = []
    for index in range(6):
        name = f"agent_tests/test_file{index}.py"
        parts.append(
            textwrap.dedent(
                f"""\
                diff --git a/{name} b/{name}
                new file mode 100644
                --- /dev/null
                +++ b/{name}
                @@ -0,0 +1 @@
                +def test_{index}():
                """
            )
        )
    with pytest.raises(PatchRejected) as caught:
        inspect_patch("".join(parts), allowed_paths=["order_service/pricing.py"])
    assert caught.value.code == "file_limit"


def test_evaluator_path_in_an_added_line_is_rejected() -> None:
    patch = _diff(
        "order_service/pricing.py",
        "value = 1\n",
        'value = open("evaluator_private/tests/D01/secret.py").read()\n',
    )
    with pytest.raises(PatchRejected) as caught:
        inspect_patch(patch, allowed_paths=["order_service/pricing.py"])
    assert caught.value.code == "evaluator_path"


def test_wrong_hunk_line_count_still_passes_apply_check(tmp_path: Path) -> None:
    source = tmp_path / "order_service"
    source.mkdir()
    (source / "pricing.py").write_text("value = 1\n", encoding="utf-8")
    patch = _diff("order_service/pricing.py", "value = 1\n", "value = 2\n")
    lines = ["@@ -1,40 +1,40 @@" if line.startswith("@@") else line for line in patch.splitlines()]
    bad = "\n".join(lines) + "\n"
    import subprocess

    rejected = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        input=bad.encode(),
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0
    accepted = validate_patch(bad, allowed_paths=["order_service/pricing.py"], workspace=tmp_path)
    assert accepted.files == ("order_service/pricing.py",)
    assert (source / "pricing.py").read_text(encoding="utf-8") == "value = 1\n"


def test_wrapper_lines_around_a_unified_diff_are_stripped(tmp_path: Path) -> None:
    source = tmp_path / "order_service"
    source.mkdir()
    (source / "pricing.py").write_text("value = 1\n", encoding="utf-8")
    diff = _diff("order_service/pricing.py", "value = 1\n", "value = 2\n")
    trailing, trailing_stripped = prepare_patch(diff + "*** End Patch\n")
    wrapped, wrapped_stripped = prepare_patch("*** Begin Patch\n" + diff + "*** End Patch\n")
    assert trailing_stripped is True
    assert wrapped_stripped is True
    assert "*** End Patch" not in trailing
    assert "*** Begin Patch" not in wrapped
    assert "*** End Patch" not in wrapped
    for raw in (diff + "*** End Patch\n", "*** Begin Patch\n" + diff + "*** End Patch\n"):
        accepted = validate_patch(raw, allowed_paths=["order_service/pricing.py"], workspace=tmp_path)
        assert accepted.files == ("order_service/pricing.py",)
    untouched, untouched_stripped = prepare_patch(diff)
    assert untouched_stripped is False
    assert untouched == diff


def test_update_file_body_is_still_rejected_when_a_diff_is_embedded() -> None:
    diff = _diff("order_service/pricing.py", "value = 1\n", "value = 2\n")
    body = "*** Begin Patch\n*** Update File: order_service/pricing.py\n" + diff + "*** End Patch\n"
    with pytest.raises(PatchRejected, match="Begin Patch") as caught:
        prepare_patch(body)
    assert caught.value.code == "format"
    plain = "*** Begin Patch\n*** Update File: order_service/pricing.py\n@@\n-value = 1\n+value = 2\n*** End Patch\n"
    with pytest.raises(PatchRejected, match="Begin Patch") as plain_caught:
        inspect_patch(plain, allowed_paths=["order_service/pricing.py"])
    assert plain_caught.value.code == "format"


def test_apply_stderr_puts_errors_before_warnings_and_drops_host_paths() -> None:
    stderr = "\n".join(
        [
            "warning: recount: unexpected line: *** End Patch",
            "error: patch failed: order_service/pricing.py:1",
            "error: cannot read /tmp/vra-apply-abc/checkout/order_service/pricing.py",
            "error: order_service/pricing.py: patch does not apply",
        ]
    )
    message = format_apply_stderr(stderr, fallback="git apply --check failed")
    assert message.index("error: patch failed") < message.index("warning: recount")
    assert message.index("error: order_service/pricing.py") < message.index("warning: recount")
    assert "/tmp/" not in message
    assert "[path]" in message
    assert "checkout" not in message


def test_begin_patch_format_names_the_expected_unified_diff() -> None:
    body = "*** Begin Patch\n*** Update File: order_service/pricing.py\n@@\n-value = 1\n+value = 2\n*** End Patch\n"
    with pytest.raises(PatchRejected, match="Begin Patch") as caught:
        inspect_patch(body, allowed_paths=["order_service/pricing.py"])
    assert caught.value.code == "format"
    message = str(caught.value)
    assert "diff --git" in message
    assert "---" in message
    assert "+++" in message
    assert "@@" in message


def test_bug_patches_touch_only_their_allowlisted_file() -> None:
    root = Path(__file__).resolve().parents[1]
    import json

    manifest = json.loads((root / "benchmark/manifests/dev.json").read_text())
    for case in manifest["cases"]:
        patch = (root / case["bug_patch"]).read_text()
        accepted = inspect_patch(patch, allowed_paths=case["allowed_paths"])
        assert accepted.files == tuple(case["allowed_paths"])
        assert accepted.changed_lines <= 200
