"""Run pytest in a disposable, offline container.

Target code is not imported in this process. The container is removed on a
normal exit, a timeout, and an interrupt.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

LOG_LIMIT_BYTES = 64 * 1024
OUTPUT_LIMIT_BYTES = 10 * 1024 * 1024
DEFAULT_IMAGE = "verified-repair-agent-runner:week1"
DEFAULT_TIMEOUT_SECONDS = 30


class RunnerError(Exception):
    """The runner could not start a container."""


@dataclass
class RunnerResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False
    output_truncated: bool = False
    tests_modified: bool = False
    infra_error: str | None = None
    duration_seconds: float = 0
    container_name: str = ""
    generated_bytes: int = 0


@dataclass
class Runner:
    """Build and invoke the week-1 runner image."""

    image: str = DEFAULT_IMAGE
    cpus: str = "1"
    memory: str = "512m"
    pids_limit: int = 128
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    log_limit_bytes: int = LOG_LIMIT_BYTES
    output_limit_bytes: int = OUTPUT_LIMIT_BYTES
    tmpfs_size: str = "64m"
    dockerfile: Path | None = None
    _image_ready: bool = field(default=False, init=False, repr=False)

    def run_visible_tests(self, workspace: Path, *, timeout_seconds: float | None = None) -> RunnerResult:
        targets = ["tests_visible"]
        if (workspace / "agent_tests").is_dir():
            targets.append("agent_tests")
        return self.run_pytest(workspace, targets=targets, timeout_seconds=timeout_seconds)

    def run_protected_tests(
        self,
        workspace: Path,
        protected_dir: Path,
        *,
        timeout_seconds: float | None = None,
    ) -> RunnerResult:
        return self.run_pytest(
            workspace,
            targets=["/protected"],
            protected_dir=protected_dir,
            timeout_seconds=timeout_seconds,
        )

    def run_pytest(
        self,
        workspace: Path,
        *,
        targets: list[str],
        protected_dir: Path | None = None,
        timeout_seconds: float | None = None,
    ) -> RunnerResult:
        """Execute pytest. ``targets`` are workspace-relative, or ``/protected``."""
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise RunnerError(f"workspace does not exist: {workspace}")
        _grant_container_access(workspace)
        safe_targets = [_safe_target(target) for target in targets]
        protected = protected_dir.resolve() if protected_dir is not None else None
        if protected is not None and not protected.is_dir():
            raise RunnerError(f"protected tests directory does not exist: {protected}")
        self.ensure_image()
        name = f"vra-{uuid.uuid4().hex[:16]}"
        before_tests = _snapshot(workspace / "tests_visible")
        before_sizes = _file_sizes(workspace)
        command = self.docker_command(
            name=name,
            workspace=workspace,
            protected=protected,
            targets=safe_targets,
        )
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        started = time.monotonic()
        timed_out = False
        infra: str | None = None
        proc: subprocess.Popen[bytes] | None = None
        out = b""
        err = b""
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                remove_container(name)
                out, err = proc.communicate()
        except FileNotFoundError:
            infra = "docker executable not found"
        except KeyboardInterrupt:
            remove_container(name)
            raise
        finally:
            remove_container(name)
        stdout, out_truncated = _cap(out, self.log_limit_bytes)
        stderr, err_truncated = _cap(err, self.log_limit_bytes)
        if infra is None and proc is not None and proc.returncode not in (0, 1) and not timed_out:
            if proc.returncode == 125 or b"Error response from daemon" in err:
                infra = stderr.strip() or f"docker run failed with exit {proc.returncode}"
        generated = _generated_bytes(before_sizes, workspace)
        return RunnerResult(
            exit_code=None if proc is None else proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=out_truncated or err_truncated,
            output_truncated=generated > self.output_limit_bytes,
            tests_modified=_snapshot(workspace / "tests_visible") != before_tests,
            infra_error=infra,
            duration_seconds=time.monotonic() - started,
            container_name=name,
            generated_bytes=generated,
        )

    def docker_command(
        self,
        *,
        name: str,
        workspace: Path,
        protected: Path | None,
        targets: list[str],
    ) -> list[str]:
        """Argv for one container. Only the workspace, and optional protected tests, are mounted."""
        command = [
            "docker",
            "run",
            "--name",
            name,
            "--rm",
            *isolation_args(
                cpus=self.cpus,
                memory=self.memory,
                pids_limit=self.pids_limit,
                tmpfs_size=self.tmpfs_size,
            ),
            "--mount",
            f"type=bind,src={workspace},dst=/work",
        ]
        if protected is not None:
            command.extend(["--mount", f"type=bind,src={protected},dst=/protected,readonly"])
        command.extend(
            [
                "--workdir",
                "/work",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-e",
                "PYTHONUNBUFFERED=1",
                "-e",
                "PYTHONPATH=/work",
                "-e",
                "HOME=/tmp",
                self.image,
                "python",
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                "-s",
                "--tb=short",
                *targets,
            ]
        )
        return command

    def ensure_image(self) -> None:
        if self._image_ready:
            return
        inspect = subprocess.run(
            ["docker", "image", "inspect", self.image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if inspect.returncode != 0:
            dockerfile = self.dockerfile or _default_dockerfile()
            # A failed BuildKit build is reported. The legacy builder is only
            # used when the buildx plugin is not installed.
            command = image_build_command(self.image, dockerfile, use_buildx=buildx_available())
            build = subprocess.run(command, check=False)
            if build.returncode != 0:
                raise RunnerError(f"failed to build {self.image} from {dockerfile}")
        self._image_ready = True


def isolation_args(*, cpus: str, memory: str, pids_limit: int, tmpfs_size: str) -> list[str]:
    """Docker flags for the section 8 runner. No network and no host secrets."""
    return [
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={pids_limit}",
        f"--cpus={cpus}",
        f"--memory={memory}",
        f"--memory-swap={memory}",
        "--user=10001:10001",
        "--tmpfs",
        f"/tmp:rw,nosuid,size={tmpfs_size}",
    ]


def remove_container(name: str) -> None:
    """Force-remove a container. Missing containers are ignored."""
    if not name or "/" in name or name.startswith("-"):
        return
    subprocess.run(
        ["docker", "rm", "-f", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )


def image_build_command(image: str, dockerfile: Path, *, use_buildx: bool) -> list[str]:
    """Argv that builds the runner image into the local Docker image store.

    ``docker buildx build --load`` is the supported BuildKit path. Docker 29
    warns that the legacy ``docker build`` builder will be removed. That
    command remains the fallback when buildx is not installed, because
    ``--load`` is what puts the image where ``docker run`` can see it.
    """
    context = str(dockerfile.parent)
    dockerfile_arg = str(dockerfile)
    if use_buildx:
        return ["docker", "buildx", "build", "--load", "-t", image, "-f", dockerfile_arg, context]
    return ["docker", "build", "-t", image, "-f", dockerfile_arg, context]


def buildx_available() -> bool:
    """True when the Docker CLI has a working buildx plugin."""
    try:
        completed = subprocess.run(
            ["docker", "buildx", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def docker_available() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _grant_container_access(workspace: Path) -> None:
    """Let uid 10001 read and write a workspace created by another user.

    Pytest's temporary directories are mode 0700. The container user is fixed
    and is not the host user, so the directory has to be opened up. The
    workspace is disposable.
    """
    for path in [workspace, *workspace.rglob("*")]:
        if path.is_symlink():
            continue
        extra = 0o007 if path.is_dir() else 0o006
        mode = path.stat().st_mode
        if mode & extra != extra:
            path.chmod(mode | extra)


def _safe_target(target: str) -> str:
    if target == "/protected" or target.startswith("/protected/"):
        return target
    path = Path(target)
    if target.startswith("-") or path.is_absolute() or ".." in path.parts:
        raise RunnerError(f"pytest target escapes the workspace: {target}")
    return path.as_posix()


def _default_dockerfile() -> Path:
    return Path(__file__).resolve().parents[2] / "docker" / "runner.Dockerfile"


def _snapshot(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _file_sizes(root: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        sizes[path.relative_to(root).as_posix()] = path.stat().st_size
    return sizes


def _generated_bytes(before: dict[str, int], root: Path) -> int:
    total = 0
    for rel, size in _file_sizes(root).items():
        previous = before.get(rel, 0)
        if size > previous:
            total += size - previous
    return total


def _cap(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    return data[:limit].decode("utf-8", errors="replace"), truncated
