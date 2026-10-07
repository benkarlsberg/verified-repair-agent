"""Pydantic contracts for manifests, probe scoring, and future run bundles."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CASE_ID = r"^(D0[1-8]|H0[1-4]|P0[1-3]|X00)$"
SHA256 = r"^[0-9a-f]{64}$"
SOURCE_PATH = r"^order_service/[a-z_]+\.py$"


class BugCase(BaseModel):
    """One injected defect. Oracle paths stay out of the agent workspace."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=CASE_ID)
    split: Literal["dev", "heldout", "example"]
    kind: Literal["bug"]
    fixture_sha: str = Field(pattern=SHA256)
    bug_patch: str
    issue_path: str
    allowed_paths: list[str] = Field(min_length=1)
    visible_test_paths: list[str] = Field(min_length=1)
    evaluator_test_dir: str
    reference_fix: str

    @model_validator(mode="after")
    def _paths(self) -> BugCase:
        for path in self.allowed_paths:
            if not _is_source_path(path):
                raise ValueError(f"allowed path is not fixture source: {path}")
        if self.visible_test_paths != ["tests_visible"]:
            raise ValueError("visible_test_paths must be ['tests_visible']")
        _reject_parent_segments(self.bug_patch, self.issue_path, self.evaluator_test_dir, self.reference_fix)
        return self


class ProbeCase(BaseModel):
    """An abstention probe. Expected outcomes are not stored on this object."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=CASE_ID)
    split: Literal["probe"]
    kind: Literal["probe"]
    fixture_sha: str = Field(pattern=SHA256)
    issue_path: str
    allowed_paths: list[str] = Field(min_length=1)
    visible_test_paths: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _paths(self) -> ProbeCase:
        for path in self.allowed_paths:
            if not _is_source_path(path):
                raise ValueError(f"allowed path is not fixture source: {path}")
        if self.visible_test_paths != ["tests_visible"]:
            raise ValueError("visible_test_paths must be ['tests_visible']")
        _reject_parent_segments(self.issue_path)
        return self


CaseRecord = Annotated[BugCase | ProbeCase, Field(discriminator="kind")]


class ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    split: Literal["dev", "heldout", "probe", "example"]
    cases: list[CaseRecord] = Field(min_length=1)


class ProbeScoreEntry(BaseModel):
    """Private scoring row. Values live only in the private benchmark checkout."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^P0[1-3]$")
    allowed_claims: list[Literal["repaired", "unresolved", "insufficient_evidence"]] = Field(min_length=1)
    require_empty_source_diff: bool
    explanation_notes: str = Field(min_length=1)


class ProbeScoreFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    probes: list[ProbeScoreEntry] = Field(min_length=1)


class RunResult(BaseModel):
    """Canonical summary written at the end of a repair attempt. Week 2 fills these."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    case_id: str
    method: Literal["iterative", "one_shot"]
    repetition: int
    status: Literal["completed", "infra_error"]
    agent_claim: Literal["repaired", "unresolved", "insufficient_evidence"] | None = None
    verification: Literal["passed", "failed", "rejected", "infra_error"] | None = None
    summary: str = ""
    limitations: list[str] = Field(default_factory=list)
    source_files_changed: list[str] = Field(default_factory=list)
    patch_sha256: str | None = None
    visible_passed: bool | None = None
    protected_passed: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    patch_attempts: int = 0
    elapsed_seconds: float = 0
    estimated_cost_usd: float | None = None
    model_id: str | None = None
    provider: str | None = None
    controller_commit: str | None = None
    fixture_sha: str
    prompt_sha256: str | None = None
    manifest_sha256: str | None = None
    evaluator_sha256: str | None = None
    budgets: dict[str, Any] = Field(default_factory=dict)
    started_at_utc: str
    finished_at_utc: str | None = None


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int
    timestamp_utc: str
    category: str
    tool_name: str | None = None
    duration_ms: int | None = None
    success: bool
    request_summary: str
    response_summary: str


def _is_source_path(path: str) -> bool:
    import re

    return re.fullmatch(SOURCE_PATH, path) is not None


def _reject_parent_segments(*paths: str) -> None:
    for path in paths:
        parts = path.replace("\\", "/").split("/")
        if path.startswith("/") or ".." in parts or path.strip() == "":
            raise ValueError(f"unsafe relative path: {path}")
