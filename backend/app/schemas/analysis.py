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
    failure_phase: Literal[
        "client_configuration", "file_staging", "operation_submission",
        "operation_tracking", "python_target_validation", "resolver_setup",
        "resolver_version", "resolution", "result_parsing",
    ] | None = None
    failure_category: Literal[
        "sandbox_failure", "network_failure", "resolver_failure", "unknown_failure"
    ] | None = None
    transport_error_type: Literal[
        "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
        "WriteError", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
        "LocalProtocolError", "ProxyError", "UnsupportedProtocol",
        "NetworkError", "TimeoutException",
    ] | None = None
    transport_cause_type: Literal[
        "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
        "WriteError", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
        "LocalProtocolError", "ProxyError", "UnsupportedProtocol", "NetworkError",
        "gaierror", "herror", "SSLError", "SSLCertVerificationError",
        "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
        "ConnectionAbortedError", "TimeoutError", "OSError", "BrokenPipeError",
    ] | None = None
    transport_os_error_code: int | None = Field(default=None, ge=-65535, le=65535)
    transport_timed_out: bool | None = None
    http_status: int | None = Field(default=None, ge=400, le=599)
    server_error_code: Literal[
        "BAD_REQUEST", "INVALID_REQUEST", "VALIDATION_ERROR", "INVALID_FIELD",
        "MISSING_FIELD", "UNSUPPORTED_MEDIA_TYPE", "FILE_UPLOAD_REJECTED",
    ] | None = None
    server_error_field: Literal[
        "content", "content_type", "file", "files", "path", "uuid", "mode",
    ] | None = None
    resolver: str | None = None
    resolver_version: str | None = None
    python_target: str
    sandbox_os: str | None = None
    sandbox_python: str | None = None
    sandbox_image: str | None = None
    target_matches_sandbox_python: bool | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    resolved_requirements: str | None = None
    limitation: str | None = None


class CompatibilityEvidence(BaseModel):
    source: Literal["pypi", "uv", "analysis"]
    label: str
    value: str
    package: str | None = None
    source_url: str | None = None


class CompatibilityFinding(BaseModel):
    classification: Literal["confirmed_conflict", "potential_issue", "unknown", "incomplete", "resolved"]
    code: str
    title: str
    explanation: str
    packages: list[str] = Field(default_factory=list)
    evidence: list[CompatibilityEvidence] = Field(default_factory=list)


class CompatibilityReport(BaseModel):
    status: Literal["confirmed_conflict", "potential_issues", "unknown", "incomplete", "resolved_in_environment", "simulated"]
    summary: str
    findings: list[CompatibilityFinding] = Field(default_factory=list)


class ExplanationResult(BaseModel):
    status: Literal["available", "unavailable", "not_requested"]
    text: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    error_code: str | None = None


class ReleaseCandidate(BaseModel):
    candidate_id: str
    package: str
    version: str
    source_url: str
    requires_python: str | None = None
    requires_dist: list[str] = Field(default_factory=list)
    yanked: bool = False


class ReleaseCandidateSet(BaseModel):
    package: str
    status: Literal["available", "unavailable", "error"]
    candidates: list[ReleaseCandidate] = Field(default_factory=list)
    error_code: str | None = None


class RepairChange(BaseModel):
    package: str
    line: int
    before: str
    after: str
    candidate_version: str
    pypi_source_url: str
    pypi_requires_python: str | None = None
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)


class RepairResolverEvidence(BaseModel):
    status: Literal["accepted", "rejected", "not_run", "unknown"]
    resolver: str | None = None
    resolver_version: str | None = None
    exit_code: int | None = None
    python_target: str
    sandbox_os: str | None = None
    sandbox_python: str | None = None
    failure_category: Literal[
        "sandbox_failure", "network_failure", "resolver_failure", "unknown_failure"
    ] | None = None


class RepairProposal(BaseModel):
    status: Literal["queued", "running", "awaiting_resolution", "ready", "unavailable"]
    proposal_id: str | None = None
    summary: str | None = None
    original_sha256: str
    diff_sha256: str | None = None
    proposed_requirements: str | None = None
    diff: str | None = None
    changes: list[RepairChange] = Field(default_factory=list)
    resolver_evidence: RepairResolverEvidence | None = None
    resolver_diagnostics: ResolverResult | None = None
    candidate_evidence: list[ReleaseCandidate] = Field(default_factory=list)
    resolver_operation: dict[str, object] | None = None
    verified: Literal[False] = False
    error_code: str | None = None


class RepairRequestPreview(BaseModel):
    preview_id: str
    preview_sha256: str
    model_id: str
    base_url: str
    system_prompt: str
    evidence_json: str
    temperature: float
    max_tokens: int
    timeout_seconds: float


class RepairPreviewApproval(BaseModel):
    approved: bool
    preview_sha256: str


class RepairResolutionApproval(BaseModel):
    approved: bool
    proposal_id: str
    diff_sha256: str


class CaptureAnalysisRequest(BaseModel):
    capture_id: str
    requirements_text: str
    python_version: str


class AnalysisResponse(BaseModel):
    analysis_id: str
    status: Literal["queued", "running", "complete", "analysis_incomplete"]
    source_id: str
    python_target: str
    provenance: dict[str, str] = Field(default_factory=dict)
    requirements: list[ParsedRequirement]
    metadata: list[MetadataResult] = Field(default_factory=list)
    resolver: ResolverResult | None = None
    compatibility_report: CompatibilityReport | None = None
    explanation: ExplanationResult | None = None
    repair_proposal: RepairProposal | None = None
    errors: list[str] = Field(default_factory=list)
    poll_url: str | None = None
