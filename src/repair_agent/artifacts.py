"""Append-only traces and atomic result files.

Events are written as they happen. ``result.json`` is replaced only after the
attempt has a complete document, so a crash mid-write leaves the previous
file intact. Reasoning items and encrypted provider state are not written.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from repair_agent.cases import repo_root
from repair_agent.schemas import RunResult, TraceEvent


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def utc_now(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        import time

        return time.monotonic()

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def controller_commit() -> str | None:
    """Current git HEAD, or None when the checkout cannot be read."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    commit = completed.stdout.strip()
    return commit or None


class RunBundle:
    """One ``runs/<run_id>/`` directory."""

    def __init__(self, run_dir: Path, clock: Clock | None = None) -> None:
        self.run_dir = run_dir
        self.clock = clock or SystemClock()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = run_dir / "events.jsonl"
        self.result_path = run_dir / "result.json"
        self._seq = 0
        if not self.events_path.exists():
            self.events_path.touch()

    def append(
        self,
        *,
        category: str,
        success: bool,
        request_summary: str,
        response_summary: str,
        tool_name: str | None = None,
        duration_ms: int | None = None,
    ) -> TraceEvent:
        self._seq += 1
        event = TraceEvent(
            seq=self._seq,
            timestamp_utc=format_utc(self.clock.utc_now()),
            category=category,
            tool_name=tool_name,
            duration_ms=duration_ms,
            success=success,
            request_summary=request_summary,
            response_summary=response_summary,
        )
        line = event.model_dump_json() + "\n"
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def write_text(self, name: str, text: str) -> None:
        """Write a named artifact next to the trace. ``name`` is not model text."""
        if "/" in name or name.startswith("."):
            raise ValueError(f"refusing artifact name: {name}")
        path = self.run_dir / name
        _atomic_write(path, text if text.endswith("\n") or text == "" else text + "\n")

    def write_result(self, result: RunResult) -> None:
        _atomic_write(self.result_path, result.model_dump_json(indent=2) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
