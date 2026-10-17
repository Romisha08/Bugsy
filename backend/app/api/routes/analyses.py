from __future__ import annotations

from secrets import token_urlsafe

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from app.schemas.analysis import AnalysisResponse, AnalyzeRequest, ResolverResult
from app.services.compatibility import analyze_requirements
from app.services.workflow import get_analysis, get_source, save_analysis, update_analysis

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
        incomplete = resolver.status in {"not_run", "error"} or bool(metadata_errors)
        update_analysis(
            analysis_id,
            status="analysis_incomplete" if incomplete else "complete",
            metadata=metadata,
            resolver=resolver,
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


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
def read_analysis(analysis_id: str):
    response = get_analysis(analysis_id)
    if response is None:
        return _error("ANALYSIS_NOT_FOUND", "This analysis is unavailable or has expired.", status_code=404)
    return response
