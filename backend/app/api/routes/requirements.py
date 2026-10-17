from __future__ import annotations

from secrets import token_urlsafe

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

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
async def parse_requirements_source(request: Request):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > settings.max_requirements_bytes + 8192:
        return _error(
            "REQUIREMENTS_TOO_LARGE",
            f"The requirements source exceeds the {settings.max_requirements_bytes // 1024} KiB input limit.",
            status_code=413,
        )
    media_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if media_type == "application/json":
        try:
            payload = await request.json()
        except ValueError:
            return _error("INVALID_PARSE_REQUEST", "Provide a valid JSON request.")
        if not isinstance(payload, dict):
            return _error("INVALID_PARSE_REQUEST", "Provide requirements text and a Python version.")
        text = payload.get("text")
        python_version = payload.get("python_version")
        if not isinstance(text, str) or not isinstance(python_version, str):
            return _error(
                "INVALID_PARSE_REQUEST",
                "Provide requirements text and a Python version.",
                {"text": "string required", "python_version": "string required"},
            )
        return _parse(text, python_version)
    if media_type == "multipart/form-data":
        try:
            form = await request.form(max_files=1, max_fields=2, max_part_size=settings.max_requirements_bytes + 1)
        except (ValueError, AssertionError):
            return _error("INVALID_PARSE_REQUEST", "Upload one requirements.txt file and a Python version.")
        file = form.get("file")
        python_version = form.get("python_version")
        if not isinstance(file, UploadFile) or not isinstance(python_version, str):
            return _error("INVALID_PARSE_REQUEST", "Upload one requirements.txt file and a Python version.")
        if (file.filename or "").lower() != "requirements.txt":
            return _error("UNSUPPORTED_FILE", "Upload a file named requirements.txt.", {"filename": "unsupported"})
        raw = await file.read(settings.max_requirements_bytes + 1)
        if len(raw) > settings.max_requirements_bytes:
            return _error(
                "REQUIREMENTS_TOO_LARGE",
                f"The requirements file exceeds the {settings.max_requirements_bytes // 1024} KiB limit.",
                status_code=413,
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _error("INVALID_ENCODING", "Save requirements.txt as UTF-8 text.", {"encoding": "UTF-8 required"})
        return _parse(text, python_version)
    return _error("UNSUPPORTED_CONTENT_TYPE", "Use JSON for pasted text or multipart form-data for requirements.txt.", status_code=415)
