"""Runner isolation. Behavioral checks run only when Docker is available."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from repair_agent.runner import Runner, RunnerError, docker_available, isolation_args

pytestmark_docker = pytest.mark.skipif(not docker_available(), reason="Docker is not available")


def _workspace(tmp_path: Path, source: str) -> Path:
    tests = tmp_path / "tests_visible"
    tests.mkdir(parents=True)
    (tests / "test_case.py").write_text(textwrap.dedent(source))
    return tmp_path


def test_isolation_args_match_the_runner_contract(tmp_path: Path) -> None:
    args = isolation_args(cpus="1", memory="512m", pids_limit=128, tmpfs_size="64m")
    assert args == [
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=128",
        "--cpus=1",
        "--memory=512m",
        "--memory-swap=512m",
        "--user=10001:10001",
        "--tmpfs",
        "/tmp:rw,nosuid,size=64m",
    ]
    command = Runner().docker_command(
        name="vra-test",
        workspace=tmp_path,
        protected=None,
        targets=["tests_visible"],
    )
    joined = " ".join(command)
    assert "docker.sock" not in joined
    assert f"src={tmp_path.resolve()},dst=/work" in joined
    assert "/protected" not in joined
    for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges"):
        assert flag in command


def test_pytest_target_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="escapes"):
        Runner().run_pytest(tmp_path, targets=["../outside"])


@pytest.fixture(scope="module")
def runner() -> Runner:
    if not docker_available():
        pytest.skip("Docker is not available")
    instance = Runner()
    instance.ensure_image()
    return instance


def _host_can_reach_network() -> bool:
    try:
        sock = socket.socket()
        sock.settimeout(3)
        sock.connect(("1.1.1.1", 443))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def test_network_user_and_root_filesystem(runner: Runner, tmp_path: Path) -> None:
    if not _host_can_reach_network():
        pytest.skip("host has no outbound network, so a refused connect would not prove isolation")
    workspace = _workspace(
        tmp_path,
        """\
        import os
        import socket

        def test_container_is_not_root_and_cannot_write_the_root_fs():
            assert os.geteuid() != 0
            try:
                open("/usr/local/vra-probe", "w")
            except OSError:
                return
            raise AssertionError("root filesystem is writable")

        def test_network_is_denied():
            sock = socket.socket()
            sock.settimeout(3)
            try:
                sock.connect(("1.1.1.1", 443))
            except OSError:
                return
            raise AssertionError("network connection succeeded")
        """,
    )
    result = runner.run_pytest(workspace, targets=["tests_visible"], timeout_seconds=30)
    assert result.infra_error is None
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _containers() == []


def test_timeout_removes_the_container(runner: Runner, tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        """\
        import time

        def test_sleep():
            time.sleep(30)
        """,
    )
    result = runner.run_pytest(workspace, targets=["tests_visible"], timeout_seconds=3)
    assert result.timed_out
    assert _containers() == []


def test_interrupt_removes_the_container(runner: Runner, tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        """\
        import time

        def test_sleep():
            time.sleep(30)
        """,
    )
    script = tmp_path / "child.py"
    script.write_text(
        "from pathlib import Path\n"
        "from repair_agent.runner import Runner\n"
        f"Runner().run_pytest(Path({str(workspace)!r}), targets=['tests_visible'], timeout_seconds=30)\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    deadline = time.time() + 20
    while time.time() < deadline and not _containers():
        time.sleep(0.2)
    assert _containers(), "container did not start"
    os.killpg(proc.pid, signal.SIGINT)
    proc.wait(timeout=20)
    deadline = time.time() + 10
    while time.time() < deadline and _containers():
        time.sleep(0.2)
    assert _containers() == []


def test_fresh_state_between_runs(runner: Runner, tmp_path: Path) -> None:
    first = _workspace(
        tmp_path / "first",
        """\
        from pathlib import Path

        def test_write_marker():
            Path("/tmp/marker").write_text("leaked")
            Path("marker.txt").write_text("leaked")
        """,
    )
    second = _workspace(
        tmp_path / "second",
        """\
        from pathlib import Path

        def test_marker_is_absent():
            assert not Path("/tmp/marker").exists()
            assert not Path("marker.txt").exists()
        """,
    )
    assert runner.run_pytest(first, targets=["tests_visible"], timeout_seconds=30).exit_code == 0
    result = runner.run_pytest(second, targets=["tests_visible"], timeout_seconds=30)
    assert result.exit_code == 0, result.stdout + result.stderr


def test_modified_visible_tests_are_detected(runner: Runner, tmp_path: Path) -> None:
    tests = tmp_path / "tests_visible"
    tests.mkdir()
    (tests / "test_keep.py").write_text("def test_ok():\n    assert True\n")
    (tests / "test_tamper.py").write_text(
        "from pathlib import Path\n\n"
        "def test_tamper():\n"
        "    Path('tests_visible/test_keep.py').write_text('def test_ok():\\n    assert False\\n')\n"
    )
    result = runner.run_pytest(tmp_path, targets=["tests_visible"], timeout_seconds=30)
    assert result.tests_modified
    assert "assert False" in (tests / "test_keep.py").read_text()


def test_logs_and_generated_files_are_capped(runner: Runner, tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        """\
        def test_spam_and_write():
            print("x" * 200_000)
            with open("blob.bin", "wb") as handle:
                handle.write(b"a" * (11 * 1024 * 1024))
        """,
    )
    result = runner.run_pytest(workspace, targets=["tests_visible"], timeout_seconds=30)
    assert result.exit_code == 0, result.stderr
    assert result.truncated
    assert len(result.stdout.encode()) <= 64 * 1024
    assert result.output_truncated


def _containers() -> list[str]:
    completed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [name for name in completed.stdout.split() if name.startswith("vra-")]
