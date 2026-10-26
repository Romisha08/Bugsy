from __future__ import annotations

from secrets import token_urlsafe

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from app.adapters.contree import ContreeResolverAdapter
from app.config import settings
from app.schemas.analysis import (AnalysisResponse, AnalyzeRequest, CaptureAnalysisRequest,
    RepairPreviewApproval, RepairResolutionApproval, RepairRequestPreview, ResolverResult)
from app.services.compatibility import analyze_requirements
from app.services.compatibility_report import build_compatibility_report
from app.services.diagnosis import explain_report
from app.services.repair import build_repair_request_preview, generate_repair_proposal, resolve_repair_proposal
from app.services.captured_analysis import CaptureInputError, reconstruct_analysis
from app.services.workflow import (consume_repair_preview, claim_resolution, get_analysis,
    get_repair_preview, get_source, save_analysis, save_repair_preview, update_analysis)

router = APIRouter(tags=["analysis"])


def _error(code: str, message: str, details=None, status_code: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, "details": details}})


def run_analysis(analysis_id: str) -> None:
    current = get_analysis(analysis_id)
    if current is None:
        return
    source = get_source(current.source_id)
    if source is None:
        update_analysis(analysis_id, status="analysis_incomplete", errors=["The parsed source is no longer available."])
        return
    update_analysis(analysis_id, status="running")
    try:
        metadata, resolver = analyze_requirements(source.original_text, source.requirements, source.python_version)
        metadata_errors = [
            f"{item.package}: {item.error_message}"
            for item in metadata
            if item.status == "error"
        ]
        report = build_compatibility_report(source.requirements, source.python_version, metadata, resolver)
        explanation = explain_report(report)
        incomplete = resolver.status in {"not_run", "error"} or bool(metadata_errors) or report.status == "incomplete"
        update_analysis(
            analysis_id,
            status="analysis_incomplete" if incomplete else "complete",
            metadata=metadata,
            resolver=resolver,
            compatibility_report=report,
            explanation=explanation,
            errors=metadata_errors,
        )
    except Exception as exc:
        update_analysis(
            analysis_id,
            status="analysis_incomplete",
            resolver=ResolverResult(
                status="error",
                execution_mode="not_run",
                python_target=source.python_version,
                limitation=f"Analysis stopped because of an internal {type(exc).__name__}. No compatibility claim is available.",
            ),
            errors=["Analysis could not finish. Retry after checking the backend logs."],
        )


@router.post("/analyses", response_model=AnalysisResponse, status_code=202)
def create_analysis(payload: AnalyzeRequest, background_tasks: BackgroundTasks):
    source = get_source(payload.source_id)
    if source is None:
        return _error("SOURCE_NOT_FOUND", "Parse a requirements.txt successfully before starting analysis.", status_code=404)
    analysis_id = token_urlsafe(18)
    response = AnalysisResponse(
        analysis_id=analysis_id,
        status="queued",
        source_id=source.source_id,
        python_target=source.python_version,
        requirements=source.requirements,
        poll_url=f"/api/v1/analyses/{analysis_id}",
    )
    save_analysis(response)
    background_tasks.add_task(run_analysis, analysis_id)
    return response


@router.post("/analyses/from-capture", response_model=AnalysisResponse)
def create_analysis_from_capture(payload: CaptureAnalysisRequest):
    try:
        return reconstruct_analysis(payload.capture_id, payload.requirements_text, payload.python_version)
    except CaptureInputError as exc:
        return _error(exc.code, str(exc), status_code=422)


@router.post("/analyses/{analysis_id}/repair-request-preview", response_model=RepairRequestPreview)
def preview_repair_request(analysis_id: str):
    current = get_analysis(analysis_id)
    if current is None:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable or has expired.", status_code=404)
    if current.compatibility_report is None or current.resolver is None:
        return _error("ANALYSIS_NOT_READY", "Wait for compatibility analysis to finish before requesting a proposal.", status_code=409)
    if current.repair_proposal and current.repair_proposal.status in {"queued", "running", "awaiting_resolution", "ready"}:
        return _error("PROPOSAL_ALREADY_EXISTS", "This analysis already has a repair proposal.", status_code=409)
    source = get_source(current.source_id)
    if source is None:
        return _error("SOURCE_NOT_FOUND", "The original requirements source is no longer available.", status_code=404)
    preview, candidates, error = build_repair_request_preview(source, current.compatibility_report,
        current.metadata, current.resolver, preview_id=token_urlsafe(18))
    if error:
        return _error(error, "Bugsy could not prepare a safe model request from the current evidence.", status_code=409)
    save_repair_preview(analysis_id, preview, candidates)
    return preview


@router.post("/analyses/{analysis_id}/repair-proposal", response_model=AnalysisResponse)
def create_repair_proposal(analysis_id: str, approval: RepairPreviewApproval):
    current = get_analysis(analysis_id)
    if current is None:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable or has expired.", status_code=404)
    if not approval.approved:
        return _error("APPROVAL_REQUIRED", "Approve the displayed sanitized model request before sending it.", status_code=409)
    if not settings.token_factory_enabled:
        return _error("TOKEN_FACTORY_DISABLED", "Token Factory is disabled; no model request was sent and the approval was not consumed.", status_code=503)
    if not settings.nebius_api_key:
        return _error("TOKEN_FACTORY_NOT_CONFIGURED", "Token Factory credentials are not configured; no model request was sent and the approval was not consumed.", status_code=503)
    preview = get_repair_preview(analysis_id)
    if not preview or preview.preview_sha256 != approval.preview_sha256:
        return _error("PREVIEW_STALE", "The approved model request is no longer current. Review a fresh preview.", status_code=409)
    candidates = consume_repair_preview(analysis_id, approval.preview_sha256)
    if candidates is None:
        return _error("DUPLICATE_SUBMISSION", "This approved model request has already been submitted.", status_code=409)
    source = get_source(current.source_id)
    if source is None:
        return _error("SOURCE_NOT_FOUND", "The original requirements source is no longer available.", status_code=404)
    proposal = generate_repair_proposal(source, current.compatibility_report, preview,
        candidates, config=settings)
    return update_analysis(analysis_id, repair_proposal=proposal)


@router.post("/analyses/{analysis_id}/repair-proposal/{proposal_id}/resolve", response_model=AnalysisResponse)
def resolve_proposal(analysis_id: str, proposal_id: str, approval: RepairResolutionApproval):
    current = get_analysis(analysis_id)
    if current is None or current.repair_proposal is None:
        return _error("PROPOSAL_NOT_FOUND", "This proposal is unavailable.", status_code=404)
    if not settings.sandbox_enabled:
        return _error("SANDBOX_DISABLED", "Sandbox execution is disabled; no resolver operation was submitted and the approval was not consumed.", status_code=503)
    if not settings.nebius_api_key or not settings.contree_base_url or not settings.contree_project_id:
        return _error("SANDBOX_NOT_CONFIGURED", "ConTree is not fully configured; no resolver operation was submitted and the approval was not consumed.", status_code=503)
    proposal = current.repair_proposal
    if not approval.approved or approval.proposal_id != proposal_id or approval.diff_sha256 != proposal.diff_sha256:
        return _error("APPROVAL_MISMATCH", "Explicit approval must match the current proposal ID and diff hash.", status_code=409)
    if not claim_resolution(analysis_id, proposal_id, approval.diff_sha256):
        return _error("DUPLICATE_OR_STALE_RESOLUTION", "The persisted proposal or one-shot approval is missing, modified, stale, or already consumed.", status_code=409)
    resolver_factory = lambda: ContreeResolverAdapter(config=settings)
    updated = resolve_repair_proposal(proposal, current.python_target, resolver_factory=resolver_factory)
    return update_analysis(analysis_id, repair_proposal=updated)


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
def read_analysis(analysis_id: str):
    response = get_analysis(analysis_id)
    if response is None:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable or has expired.", status_code=404)
    return response
