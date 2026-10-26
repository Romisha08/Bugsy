from __future__ import annotations

import hashlib
import base64
from hmac import compare_digest
from secrets import token_urlsafe

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse, Response

from app.config import sandbox_image_for_python_target, settings
from app.schemas.verification import (
    FreshInstallationApprovalRequest, ImportStageApprovalRequest, ImportStageResult,
    InstallationStageApprovalRequest, InstallationStageResult, ProposalApprovalRequest, StartVerificationRequest,
)
from app.services.verification import execute_installation_stage, execute_verification_attempt, import_modules_for, new_verification, run_verification
from app.adapters.contree import UV_RESOLVER_VERSION, build_sdk_import_kwargs, build_sdk_installation_kwargs
from app.services.workflow import (
    active_verification,
    approve_download,
    claim_installation_stage,
    get_analysis,
    get_installation_stage,
    latest_installation_stage,
    get_source,
    get_verification,
    is_download_approved,
    save_verification,
    update_verification,
    save_installation_stage_result,
    claim_verification_attempt,
    get_verification_attempt,
    list_verification_attempts,
    retained_installation_image,
    persisted_proposal_is_valid,
)

router = APIRouter(tags=["verification"])


def _error(code: str, message: str, status: int):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _diff_hash_matches(proposal, submitted_hash: str) -> bool:
    if not proposal.diff or not proposal.diff_sha256:
        return False
    actual = hashlib.sha256(proposal.diff.encode("utf-8")).hexdigest()
    return compare_digest(actual, proposal.diff_sha256) and compare_digest(proposal.diff_sha256, submitted_hash)


def _supported_target_error(analysis):
    try:
        sandbox_image_for_python_target(analysis.python_target)
    except ValueError:
        return _error("UNSUPPORTED_PYTHON_TARGET", "This Bugsy release supports Python 3.10, 3.11, 3.12, 3.13, and 3.14; no Sandbox action was submitted.", 422)
    return None


def _run(verification_id: str) -> None:
    job = get_verification(verification_id)
    if not job:
        return
    analysis = get_analysis(job.analysis_id)
    source = get_source(analysis.source_id) if analysis else None
    if not analysis or not source:
        update_verification(verification_id, status="incomplete", execution_mode="not_run", limitation="The in-memory analysis or original requirements expired before verification.")
        return
    update_verification(verification_id, status="running")
    current_job = get_verification(verification_id)
    if current_job is None:
        return
    result = run_verification(analysis, source, current_job)
    update_verification(verification_id, **result.model_dump(exclude={"verification_id"}))


def _run_installation_stage(stage_id: str) -> None:
    try:
        execute_installation_stage(stage_id)
    except Exception as exc:
        # Persist only a safe exception class. The consumed approval is never restored.
        stage = get_installation_stage(stage_id)
        if stage and stage.status == "running":
            from datetime import UTC, datetime
            failed = stage.model_copy(update={
                "status": "unknown", "failure_category": "unknown_failure",
                "limitation": f"Installation outcome unknown/incomplete ({type(exc).__name__}); approval remains consumed and no automatic retry will occur.",
                "updated_at": datetime.now(UTC).isoformat(),
            })
            save_installation_stage_result(failed)


def _run_verification_attempt(stage_id: str) -> None:
    try:
        execute_verification_attempt(stage_id)
    except Exception as exc:
        stage = get_verification_attempt(stage_id)
        if stage and stage.status == "running":
            from datetime import UTC, datetime
            failed = stage.model_copy(update={
                "status": "unknown", "failure_category": "unknown_failure",
                "limitation": f"Sandbox outcome unknown/incomplete ({type(exc).__name__}); approval remains consumed and no automatic retry will occur.",
                "updated_at": datetime.now(UTC).isoformat(),
            })
            from app.services.workflow import save_verification_attempt_result
            save_verification_attempt_result(failed)


@router.post("/analyses/{analysis_id}/verification/installation", status_code=202)
def start_installation_stage(
    analysis_id: str,
    payload: InstallationStageApprovalRequest,
    background_tasks: BackgroundTasks,
):
    analysis = get_analysis(analysis_id)
    if not analysis:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable.", 404)
    target_error = _supported_target_error(analysis)
    if target_error:
        return target_error
    proposal = analysis.repair_proposal
    if not proposal or proposal.status != "ready" or not proposal.proposed_requirements:
        return _error("PROPOSAL_NOT_READY", "A resolver-accepted proposal is required before installation.", 409)
    if payload.stage != "installation" or payload.proposal_id != proposal.proposal_id or not _diff_hash_matches(proposal, payload.diff_sha256):
        return _error("INSTALLATION_APPROVAL_MISMATCH", "Approval must match this proposal, exact diff hash, and installation stage.", 409)
    if not settings.sandbox_enabled:
        return _error("SANDBOX_DISABLED", "Sandbox execution is disabled; no installation approval was consumed.", 503)
    source = get_source(analysis.source_id)
    if not source or source.source_sha256 != proposal.original_sha256:
        return _error("SOURCE_UNAVAILABLE", "The original requirements source is unavailable or does not match this proposal.", 409)
    if not settings.nebius_api_key or not settings.contree_base_url:
        return _error("SANDBOX_NOT_CONFIGURED", "ConTree is not configured; no installation approval was consumed.", 503)
    stage_id = token_urlsafe(18)
    claimed = claim_installation_stage(
        analysis_id, payload.proposal_id, payload.diff_sha256, stage_id, stage=payload.stage,
    )
    if claimed is None:
        return _error("INSTALLATION_STAGE_STALE_OR_CONSUMED", "The proposal is modified, stale, invalid, or this installation approval was already consumed.", 409)
    background_tasks.add_task(_run_installation_stage, stage_id)
    return claimed


@router.get("/verification-stages/{stage_id}", response_model=InstallationStageResult)
def read_installation_stage(stage_id: str):
    result = get_installation_stage(stage_id)
    if not result:
        return _error("VERIFICATION_STAGE_NOT_FOUND", "This installation stage is unavailable.", 404)
    return result


@router.get(
    "/analyses/{analysis_id}/verification/installation",
    response_model=InstallationStageResult | None,
)
def read_latest_installation_stage(analysis_id: str, proposal_id: str):
    """Read persisted installation state without creating or consuming approval."""
    analysis = get_analysis(analysis_id)
    if not analysis:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable.", 404)
    proposal = analysis.repair_proposal
    if not proposal or proposal.proposal_id != proposal_id:
        return _error("PROPOSAL_NOT_FOUND", "This proposal is unavailable for the analysis.", 404)
    return latest_installation_stage(analysis_id, proposal_id)


@router.get("/analyses/{analysis_id}/verification/installation/preview")
def preview_fresh_installation(analysis_id: str, proposal_id: str):
    analysis = get_analysis(analysis_id)
    if not analysis or not analysis.repair_proposal or analysis.repair_proposal.proposal_id != proposal_id:
        return _error("PROPOSAL_NOT_FOUND", "This proposal is unavailable.", 404)
    target_error = _supported_target_error(analysis)
    if target_error:
        return target_error
    proposal = analysis.repair_proposal
    if proposal.status != "ready" or not proposal.proposed_requirements or not _diff_hash_matches(proposal, proposal.diff_sha256 or "") or not persisted_proposal_is_valid(analysis_id, proposal_id, proposal.diff_sha256 or ""):
        return _error("PROPOSAL_NOT_READY", "The exact proposed requirements are not validated for installation.", 409)
    requirements = proposal.proposed_requirements.encode("utf-8")
    kwargs = build_sdk_installation_kwargs(proposal.proposed_requirements, analysis.python_target, retain_artifact=settings.sandbox_retain_installation_artifact)
    return {
        "analysis_id": analysis_id, "proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256,
        "stage": "installation", "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
        "requirements_bytes_base64": base64.b64encode(requirements).decode("ascii"),
        "requirements_byte_length": len(requirements), "target_python": analysis.python_target,
        "sandbox_image": sandbox_image_for_python_target(analysis.python_target), "resolver_tool": "uv", "resolver_version": UV_RESOLVER_VERSION,
        "command": kwargs["command"], "args": kwargs["args"], "env": kwargs["env"], "cwd": kwargs["cwd"],
        "input_path": "/tmp/requirements.txt", "timeout_seconds": kwargs["timeout"],
        "output_limit_bytes": kwargs["truncate_output_at"], "retain_image": settings.sandbox_retain_installation_artifact,
        "image_disposable": kwargs["disposable"],
        "sandbox_enabled": settings.sandbox_enabled,
        "retention_limitation": "Artifact cost, lifetime, and availability are account/service specific and have not been established." if settings.sandbox_retain_installation_artifact else "Retention is disabled by default; a passed install will not enable a separate import run.",
        "operation_count": 1, "automatic_retries": False,
    }


@router.get("/analyses/{analysis_id}/verification/installation/attempts")
def read_installation_attempts(analysis_id: str, proposal_id: str):
    analysis = get_analysis(analysis_id)
    if not analysis or not analysis.repair_proposal or analysis.repair_proposal.proposal_id != proposal_id or not persisted_proposal_is_valid(analysis_id, proposal_id, analysis.repair_proposal.diff_sha256 or ""):
        return _error("PROPOSAL_NOT_FOUND", "This proposal is unavailable.", 404)
    return list_verification_attempts(analysis_id, proposal_id, "installation")


@router.post("/analyses/{analysis_id}/verification/installation/attempts", status_code=202)
def start_fresh_installation_attempt(analysis_id: str, payload: FreshInstallationApprovalRequest, background_tasks: BackgroundTasks):
    if not settings.sandbox_enabled:
        return _error("SANDBOX_DISABLED", "Sandbox execution is disabled; no approval was consumed.", 503)
    if not settings.nebius_api_key or not settings.contree_base_url:
        return _error("SANDBOX_NOT_CONFIGURED", "ConTree is not configured; no approval was consumed.", 503)
    analysis = get_analysis(analysis_id)
    if not analysis:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable.", 404)
    target_error = _supported_target_error(analysis)
    if target_error:
        return target_error
    proposal = analysis.repair_proposal
    if not proposal or payload.proposal_id != proposal.proposal_id or not _diff_hash_matches(proposal, payload.diff_sha256):
        return _error("INSTALLATION_APPROVAL_MISMATCH", "Approval must match the current proposal and exact diff hash.", 409)
    stage_id = token_urlsafe(18)
    result = claim_verification_attempt(analysis_id, payload.proposal_id, payload.diff_sha256, stage_id, payload.request_id, stage="installation")
    if result is None:
        return _error("VERIFICATION_ATTEMPT_REJECTED", "Proposal validation failed, another attempt is running, or this approval request was already consumed.", 409)
    background_tasks.add_task(_run_verification_attempt, stage_id)
    return result


@router.get("/analyses/{analysis_id}/verification/imports/preview")
def preview_import_attempt(analysis_id: str, proposal_id: str):
    analysis = get_analysis(analysis_id)
    if not analysis or not analysis.repair_proposal or analysis.repair_proposal.proposal_id != proposal_id or not persisted_proposal_is_valid(analysis_id, proposal_id, analysis.repair_proposal.diff_sha256 or ""):
        return _error("PROPOSAL_NOT_FOUND", "This proposal is unavailable.", 404)
    target_error = _supported_target_error(analysis)
    if target_error:
        return target_error
    attempts = list_verification_attempts(analysis_id, proposal_id, "installation")
    latest = attempts[-1] if attempts else None
    successful = latest if latest and latest.status == "passed" and latest.artifact_available else None
    if not successful:
        return _error("INSTALLATION_ARTIFACT_UNAVAILABLE", "A passed installation with a validated retained Sandbox image is required before imports.", 409)
    image_ref = retained_installation_image(successful.stage_id)
    modules = import_modules_for(analysis)
    if not image_ref:
        return _error("INSTALLATION_ARTIFACT_UNAVAILABLE", "The retained image reference is missing or invalid.", 409)
    if not modules:
        return _error("NO_RELIABLE_IMPORTS", "No direct requirement has a reliable allowlisted import mapping.", 409)
    kwargs = build_sdk_import_kwargs(analysis.python_target, modules)
    return {
        "analysis_id": analysis_id, "proposal_id": proposal_id, "diff_sha256": analysis.repair_proposal.diff_sha256,
        "stage": "imports", "installation_stage_id": successful.stage_id,
        "sandbox_image_reference": image_ref, "target_python": analysis.python_target,
        "sandbox_os": successful.environment.os, "sandbox_python": successful.environment.python_version,
        "import_mappings": [{"package": package, "module": module} for package, module in modules],
        "command": kwargs["command"], "args": kwargs["args"], "env": kwargs["env"], "cwd": kwargs["cwd"],
        "timeout_seconds": kwargs["timeout"], "output_limit_bytes": kwargs["truncate_output_at"],
        "image_disposable": kwargs["disposable"], "operation_count": 1, "automatic_retries": False,
        "limitation": "This operation runs imports only against the retained image. It does not rerun resolution, installation, or project tests.",
    }


@router.get("/analyses/{analysis_id}/verification/imports/attempts")
def read_import_attempts(analysis_id: str, proposal_id: str):
    analysis = get_analysis(analysis_id)
    if not analysis or not analysis.repair_proposal or analysis.repair_proposal.proposal_id != proposal_id:
        return _error("PROPOSAL_NOT_FOUND", "This proposal is unavailable.", 404)
    return list_verification_attempts(analysis_id, proposal_id, "imports")


@router.post("/analyses/{analysis_id}/verification/imports/attempts", status_code=202)
def start_import_attempt(analysis_id: str, payload: ImportStageApprovalRequest, background_tasks: BackgroundTasks):
    if not settings.sandbox_enabled:
        return _error("SANDBOX_DISABLED", "Sandbox execution is disabled; no approval was consumed.", 503)
    if not settings.nebius_api_key or not settings.contree_base_url:
        return _error("SANDBOX_NOT_CONFIGURED", "ConTree is not configured; no approval was consumed.", 503)
    analysis = get_analysis(analysis_id)
    if not analysis or not analysis.repair_proposal:
        return _error("ANALYSIS_NOT_FOUND", "The analysis or proposal is unavailable.", 404)
    target_error = _supported_target_error(analysis)
    if target_error:
        return target_error
    proposal = analysis.repair_proposal
    if payload.proposal_id != proposal.proposal_id or not _diff_hash_matches(proposal, payload.diff_sha256):
        return _error("IMPORT_APPROVAL_MISMATCH", "Approval must match the current proposal and exact diff hash.", 409)
    if not import_modules_for(analysis):
        return _error("NO_RELIABLE_IMPORTS", "No direct requirement has a reliable allowlisted import mapping.", 409)
    stage_id = token_urlsafe(18)
    result = claim_verification_attempt(analysis_id, payload.proposal_id, payload.diff_sha256, stage_id, payload.request_id, stage="imports")
    if result is None:
        return _error("IMPORT_STAGE_UNAVAILABLE", "Imports require a passed installation artifact; an attempt may already be running or this request was consumed.", 409)
    background_tasks.add_task(_run_verification_attempt, stage_id)
    return result


@router.get("/verification-attempts/{stage_id}")
def read_verification_attempt(stage_id: str):
    result = get_verification_attempt(stage_id)
    if not result:
        return _error("VERIFICATION_ATTEMPT_NOT_FOUND", "This verification attempt is unavailable.", 404)
    return result


@router.post("/analyses/{analysis_id}/verification", status_code=202)
def start_verification(analysis_id: str, payload: StartVerificationRequest, background_tasks: BackgroundTasks):
    analysis = get_analysis(analysis_id)
    if not analysis:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable or has expired.", 404)
    target_error = _supported_target_error(analysis)
    if target_error:
        return target_error
    proposal = analysis.repair_proposal
    if not proposal or proposal.status != "ready" or not proposal.proposed_requirements:
        return _error("PROPOSAL_NOT_READY", "A ready proposal is required before verification.", 409)
    source = get_source(analysis.source_id)
    if not source or source.source_sha256 != proposal.original_sha256:
        return _error("SOURCE_UNAVAILABLE", "The original source is unavailable or no longer matches this proposal.", 409)
    if payload.proposal_id != proposal.proposal_id or not _diff_hash_matches(proposal, payload.diff_sha256):
        return _error("DIFF_APPROVAL_MISMATCH", "Approve the exact currently displayed proposal diff before verification.", 409)
    if active_verification(analysis_id, proposal.proposal_id):
        return _error("VERIFICATION_ALREADY_RUNNING", "A verification for this proposal is already running.", 409)
    job = new_verification(analysis)
    save_verification(job)
    background_tasks.add_task(_run, job.verification_id)
    return job


@router.get("/verifications/{verification_id}")
def read_verification(verification_id: str):
    result = get_verification(verification_id)
    if not result:
        return _error("VERIFICATION_NOT_FOUND", "This verification is unavailable or has expired.", 404)
    return result


@router.post("/analyses/{analysis_id}/approval")
def approve_proposal_download(analysis_id: str, payload: ProposalApprovalRequest):
    analysis = get_analysis(analysis_id)
    if not analysis:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable or has expired.", 404)
    proposal = analysis.repair_proposal
    if not proposal or proposal.status != "ready" or not proposal.proposed_requirements:
        return _error("PROPOSAL_NOT_READY", "A ready proposal is required before download approval.", 409)
    if payload.proposal_id != proposal.proposal_id or not _diff_hash_matches(proposal, payload.diff_sha256):
        return _error("DIFF_APPROVAL_MISMATCH", "The approval must match the exact displayed proposal diff.", 409)
    approve_download(analysis_id, proposal.proposal_id, proposal.diff_sha256)
    return {"status": "approved", "proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256}


@router.get("/analyses/{analysis_id}/download")
def download_proposed_requirements(analysis_id: str):
    analysis = get_analysis(analysis_id)
    if not analysis:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable or has expired.", 404)
    proposal = analysis.repair_proposal
    if not proposal or proposal.status != "ready" or not proposal.proposed_requirements or not proposal.proposal_id or not proposal.diff_sha256:
        return _error("PROPOSAL_NOT_READY", "A ready proposal is required before download.", 409)
    if not is_download_approved(analysis_id, proposal.proposal_id, proposal.diff_sha256):
        return _error("DOWNLOAD_NOT_APPROVED", "Approve the exact displayed diff before downloading requirements.txt.", 403)
    source = get_source(analysis.source_id)
    if not source or source.source_sha256 != proposal.original_sha256:
        return _error("SOURCE_UNAVAILABLE", "The original source is unavailable or no longer matches this proposal.", 409)
    if not _diff_hash_matches(proposal, proposal.diff_sha256):
        return _error("DIFF_APPROVAL_MISMATCH", "The proposed diff changed after approval.", 409)
    return Response(
        content=proposal.proposed_requirements,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=\"requirements.txt\""},
    )
