from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CheckStatus = Literal["passed", "failed", "not_run", "skipped"]


class VerificationCheck(BaseModel):
    status: CheckStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    reason: str | None = None


class VerificationChecks(BaseModel):
    resolution: VerificationCheck
    installation: VerificationCheck
    imports: VerificationCheck
    project_tests: VerificationCheck = Field(default_factory=lambda: VerificationCheck(
        status="not_run", reason="Project code not provided."
    ))


class VerificationEnvironment(BaseModel):
    sandbox_provider: Literal["contree"] | None = None
    os: str | None = None
    python_version: str | None = None
    requested_python: str
    target_matches_sandbox_python: bool | None = None


class VerificationResult(BaseModel):
    verification_id: str
    analysis_id: str
    proposal_id: str
    diff_sha256: str
    status: Literal["queued", "running", "complete", "incomplete", "unavailable"]
    execution_mode: Literal["real", "not_run"]
    environment: VerificationEnvironment
    resolver: str | None = None
    resolver_version: str | None = None
    failure_category: Literal[
        "sandbox_failure", "network_failure", "resolver_failure", "unknown_failure"
    ] | None = None
    checks: VerificationChecks
    limitation: str | None = None


class StartVerificationRequest(BaseModel):
    proposal_id: str
    approved: Literal[True]
    diff_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class ProposalApprovalRequest(BaseModel):
    proposal_id: str
    approved: Literal[True]
    diff_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


InstallationStageStatus = Literal["running", "passed", "failed", "incomplete", "unknown"]


class InstallationStageApprovalRequest(BaseModel):
    proposal_id: str
    approved: Literal[True]
    diff_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    stage: Literal["installation"] = "installation"


class FreshInstallationApprovalRequest(InstallationStageApprovalRequest):
    """A new user approval is distinct from any historical installation attempt."""
    request_id: str = Field(min_length=12, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")


class ImportStageApprovalRequest(BaseModel):
    proposal_id: str
    approved: Literal[True]
    diff_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    stage: Literal["imports"] = "imports"
    request_id: str = Field(min_length=12, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")


class InstallationStageEnvironment(BaseModel):
    sandbox_provider: Literal["contree"] | None = None
    os: str | None = None
    python_version: str | None = None
    requested_python: str
    target_matches_sandbox_python: bool | None = None


class InstallationStageResult(BaseModel):
    stage_id: str
    analysis_id: str
    proposal_id: str
    diff_sha256: str
    stage: Literal["installation"] = "installation"
    status: InstallationStageStatus
    execution_mode: Literal["real", "not_run"]
    approval_state: Literal["consumed"]
    # Present only after the provider confirms operation creation. Older rows
    # remain valid and deserialize this field as None.
    contree_operation_id: str | None = None
    environment: InstallationStageEnvironment
    uv_version: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    failure_category: Literal[
        "sandbox_failure", "network_failure", "installation_failure", "unknown_failure"
    ] | None = None
    failure_phase: str | None = None
    failure_detail: str | None = None
    artifact_available: bool = False
    limitation: str | None = None
    created_at: str
    updated_at: str


class ImportStageResult(BaseModel):
    stage_id: str
    analysis_id: str
    proposal_id: str
    diff_sha256: str
    stage: Literal["imports"] = "imports"
    status: InstallationStageStatus
    execution_mode: Literal["real", "not_run"]
    approval_state: Literal["consumed"]
    environment: InstallationStageEnvironment
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    failure_category: Literal["sandbox_failure", "network_failure", "import_failure", "unknown_failure"] | None = None
    failure_phase: str | None = None
    failure_detail: str | None = None
    limitation: str | None = None
    created_at: str
    updated_at: str
