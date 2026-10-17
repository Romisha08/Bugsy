from __future__ import annotations

from secrets import token_urlsafe

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.domain.evidence import RequirementSource
from app.schemas.requirements import ParseResponse
from app.services.requirements_parser import RequirementsInputError, parse_requirements, validate_python_version
from app.services.workflow import save_source

router = APIRouter(tags=["requirements"])


def _error(code: str, message: str, details=None, status_code: int = 422) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _parse(text: str, python_version: str):
    try:
        target = validate_python_version(python_version)
        result = parse_requirements(text)
    except RequirementsInputError as exc:
        return _error(exc.code, str(exc), exc.details)
    if result.issues:
        return _error(
            "UNSUPPORTED_REQUIREMENTS",
            "Fix the listed lines and upload or paste the file again. Full compatibility analysis is blocked until every dependency line can be interpreted.",
            [{"line": issue.line, "original": issue.original, "reason": issue.reason} for issue in result.issues],
        )
    source_id = token_urlsafe(18)
    save_source(RequirementSource(
        source_id=source_id,
        original_text=text,
        source_sha256=result.source_sha256,
        python_version=target,
        requirements=result.requirements,
    ))
    return ParseResponse(
        source_id=source_id,
        python_version=target,
        requirements=result.requirements,
        source_sha256=result.source_sha256,
    )


@router.post("/requirements/parse", response_model=ParseResponse)
async def parse_pasted_requirements(payload: dict):
    text = payload.get("text")
    python_version = payload.get("python_version")
    if not isinstance(text, str) or not isinstance(python_version, str):
        return _error(
            "INVALID_PARSE_REQUEST",
            "Provide requirements text and a Python version.",
            {"text": "string required", "python_version": "string required"},
        )
    return _parse(text, python_version)


@router.post("/requirements/parse-file", response_model=ParseResponse)
async def parse_uploaded_requirements(
    python_version: str = Form(...),
    file: UploadFile = File(...),
):
    if (file.filename or "").lower() != "requirements.txt":
        return _error("UNSUPPORTED_FILE", "Upload a file named requirements.txt.", {"filename": "unsupported"})
    raw = await file.read(settings.max_requirements_bytes + 1)
    if len(raw) > settings.max_requirements_bytes:
        return _error(
            "REQUIREMENTS_TOO_LARGE",
            f"The requirements file exceeds the {settings.max_requirements_bytes // 1024} KiB limit.",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _error("INVALID_ENCODING", "Save requirements.txt as UTF-8 text.", {"encoding": "UTF-8 required"})
    return _parse(text, python_version)
