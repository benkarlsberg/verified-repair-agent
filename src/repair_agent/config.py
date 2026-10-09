"""Load ``config/defaults.json``. The API key is not part of this file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from repair_agent.cases import repo_root


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: Literal["openai"]
    model_id: str = Field(min_length=1)
    alias: str = "gpt-5.4-mini"
    api: Literal["responses"] = "responses"
    store: bool = False
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"]
    temperature: float | None = None
    top_p: float | None = None
    request_timeout_seconds: float = Field(default=120, gt=0)


class Pricing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    as_of: str
    source: str
    currency: Literal["USD"] = "USD"
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


class Budgets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    wall_clock_seconds: float = Field(gt=0)
    max_model_responses: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    max_source_patch_submissions: int = Field(ge=0)
    # Provisional until the final freeze. The value and the measured note live in defaults.json.
    max_total_tokens: int = Field(ge=1)
    max_output_tokens_per_response: int = Field(ge=1)
    note: str | None = None
    test_timeout_seconds: float = Field(gt=0)


class PatchLimits(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_changed_lines: int = Field(ge=1)
    max_files: int = Field(ge=1)


class RunnerSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image: str
    cpus: str
    memory: str
    pids_limit: int
    timeout_seconds: float
    log_limit_bytes: int
    output_limit_bytes: int
    tmpfs_size: str


class Defaults(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1]
    model: ModelSettings
    pricing: Pricing
    budgets: Budgets
    runner: RunnerSettings
    patch_policy: PatchLimits


def defaults_path() -> Path:
    return repo_root() / "config" / "defaults.json"


def contract_path() -> Path:
    return repo_root() / "config" / "service_contract.md"


def load_defaults(path: Path | None = None) -> Defaults:
    target = path or defaults_path()
    return Defaults.model_validate(json.loads(target.read_text(encoding="utf-8")))


def load_contract() -> str:
    return contract_path().read_text(encoding="utf-8")
