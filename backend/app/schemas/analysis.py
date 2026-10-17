from __future__ import annotations

from typing import Literal
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.schemas.requirements import ParsedRequirement


class AnalyzeRequest(BaseModel):
    source_id: str


class MetadataResult(BaseModel):
    package: str
    status: Literal["available", "unavailable", "error"]
    source_url: str | None = None
    release: str | None = None
    latest_version: str | None = None
    requires_python: str | None = None
    requires_dist: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    retrieved_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ResolverResult(BaseModel):
    status: Literal["not_run", "resolved", "unresolved", "error"]
    execution_mode: Literal["real", "simulated", "not_run"]
    resolver: str | None = None
    resolver_version: str | None = None
    python_target: str
    sandbox_os: str | None = None
    sandbox_python: str | None = None
    target_matches_sandbox_python: bool | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    resolved_requirements: str | None = None
    limitation: str | None = None


class AnalysisResponse(BaseModel):
    analysis_id: str
    status: Literal["queued", "running", "complete", "analysis_incomplete"]
    source_id: str
    python_target: str
    requirements: list[ParsedRequirement]
    metadata: list[MetadataResult] = Field(default_factory=list)
    resolver: ResolverResult | None = None
    errors: list[str] = Field(default_factory=list)
    poll_url: str | None = None
