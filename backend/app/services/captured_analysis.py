from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from secrets import token_urlsafe

from app.domain.evidence import RequirementSource
from app.schemas.analysis import AnalysisResponse, ExplanationResult, ResolverResult
from app.services.compatibility_report import build_compatibility_report
from app.services.pypi_metadata import fetch_package_metadata
from app.services.requirements_parser import RequirementsInputError, parse_requirements, validate_python_version
from app.services.workflow import save_analysis, save_source

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"
MAX_CAPTURE_BYTES = 24_000
_CAPTURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_LEGACY_ID = "resolver-result-requests-urllib3-conflict-20261018"
_LEGACY_FIXTURE = "requests==2.25.0\nurllib3==2.2.0\n"
_LEGACY_STDERR = (
    "error: No solution found when resolving dependencies\n"
    "  cause: Because requests==2.25.0 depends on urllib3>=1.21.1,<1.27 and you require requests==2.25.0, we can conclude that you require urllib3>=1.21.1,<1.27.\n"
    "         And because you require urllib3==2.2.0, we can conclude that your requirements are unsatisfiable.\n"
)
_ALLOWED = {
    "schema_version", "requirements_sha256", "python_target", "sandbox_image",
    "status", "execution_mode", "failure_category", "resolver", "resolver_version",
    "python_target", "sandbox_os", "sandbox_python", "target_matches_sandbox_python",
    "exit_code", "stdout", "stderr", "resolved_requirements", "limitation", "capture_truncated",
}
_REQUIRED = {"status", "execution_mode", "resolver", "resolver_version", "python_target", "sandbox_os", "sandbox_python", "target_matches_sandbox_python", "exit_code", "stdout", "stderr", "resolved_requirements", "limitation", "capture_truncated"}


class CaptureInputError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _read_capture(capture_id: str) -> dict:
    if not _CAPTURE_ID.fullmatch(capture_id):
        raise CaptureInputError("CAPTURE_NOT_FOUND", "Capture identifier is invalid.")
    root = RESULTS_DIR.resolve()
    candidate = root / f"{capture_id}.json"
    if candidate.is_symlink():
        raise CaptureInputError("CAPTURE_INVALID", "Symbolic-link captures are not accepted.")
    path = candidate.resolve()
    if path.parent != root or not path.is_file():
        raise CaptureInputError("CAPTURE_NOT_FOUND", "No capture with this identifier exists in the designated results directory.")
    if path.stat().st_size > MAX_CAPTURE_BYTES:
        raise CaptureInputError("CAPTURE_TOO_LARGE", "The saved resolver capture exceeds the size limit.")
    raw = path.read_bytes()
    if len(raw) > MAX_CAPTURE_BYTES:
        raise CaptureInputError("CAPTURE_TOO_LARGE", "The saved resolver capture exceeds the size limit.")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CaptureInputError("CAPTURE_INVALID", "The saved resolver capture is not valid UTF-8 JSON.") from None
    if not isinstance(payload, dict) or set(payload) - _ALLOWED:
        raise CaptureInputError("CAPTURE_INVALID", "The capture schema contains unsupported fields.")
    if not _REQUIRED.issubset(payload) or any(not isinstance(payload.get(key), str) for key in ("status", "execution_mode", "resolver", "resolver_version", "python_target", "sandbox_os", "sandbox_python", "stdout", "stderr", "resolved_requirements", "limitation")):
        raise CaptureInputError("CAPTURE_INVALID", "The capture schema is missing required fields or has invalid field types.")
    if not isinstance(payload.get("exit_code"), int) or not isinstance(payload.get("target_matches_sandbox_python"), bool) or not isinstance(payload.get("capture_truncated"), bool):
        raise CaptureInputError("CAPTURE_INVALID", "The capture schema contains invalid result field types.")
    if "schema_version" in payload and (payload.get("schema_version") != 2 or not isinstance(payload.get("requirements_sha256"), str) or not isinstance(payload.get("sandbox_image"), str)):
        raise CaptureInputError("CAPTURE_INVALID", "The versioned capture schema is incomplete.")
    return payload


def _legacy_match(capture_id: str, payload: dict, requirements_text: str, target: str) -> None:
    if capture_id != _LEGACY_ID or requirements_text != _LEGACY_FIXTURE or target != "3.12":
        raise CaptureInputError("LEGACY_CAPTURE_UNBOUND", "Legacy captures are accepted only for the exact known fixture and target; they are not requirements-hash-bound.")
    diagnostic = payload.get("stderr", "")
    matched = (
        payload.get("status") == "unresolved"
        and payload.get("execution_mode") == "real"
        and payload.get("failure_category") == "resolver_failure"
        and payload.get("resolver") == "uv"
        and payload.get("resolver_version") == "0.12.19"
        and payload.get("python_target") == "3.12"
        and payload.get("sandbox_os") == "Linux-7.0.6-x86_64-with-glibc2.41"
        and payload.get("sandbox_python") == "3.12.13"
        and payload.get("target_matches_sandbox_python") is True
        and payload.get("exit_code") == 1
        and diagnostic == _LEGACY_STDERR
        and payload.get("stdout") == ""
        and payload.get("capture_truncated") is False
        and not payload.get("resolved_requirements")
    )
    if not matched:
        raise CaptureInputError("LEGACY_CAPTURE_MISMATCH", "The saved legacy diagnostic does not match the known unresolved fixture exactly enough to reuse.")


def reconstruct_analysis(capture_id: str, requirements_text: str, python_version: str) -> AnalysisResponse:
    payload = _read_capture(capture_id)
    try:
        target = validate_python_version(python_version)
        parsed = parse_requirements(requirements_text)
    except RequirementsInputError as exc:
        raise CaptureInputError(exc.code, str(exc)) from None
    if parsed.issues:
        raise CaptureInputError("UNSUPPORTED_REQUIREMENTS", "Every requirements line must parse before capture reconstruction.")
    digest = parsed.source_sha256
    legacy = "schema_version" not in payload
    if legacy:
        _legacy_match(capture_id, payload, requirements_text, target)
        provenance = "legacy_diagnostic_match; not requirements-hash-bound"
    else:
        if payload.get("schema_version") != 2 or payload.get("requirements_sha256") != digest:
            raise CaptureInputError("CAPTURE_PROVENANCE_MISMATCH", "The versioned capture is not bound to these exact requirements bytes.")
        if payload.get("python_target") != target:
            raise CaptureInputError("CAPTURE_PROVENANCE_MISMATCH", "The captured Python target does not match the requested target.")
        provenance = "requirements_sha256"
    resolver_fields = {key: payload.get(key) for key in ResolverResult.model_fields if key in payload}
    resolver_fields["limitation"] = (payload.get("limitation") or "") + f" Reconstructed from saved capture ({provenance}); captured unresolved status is preserved."
    try:
        resolver = ResolverResult.model_validate(resolver_fields)
    except Exception:
        raise CaptureInputError("CAPTURE_INVALID", "The saved resolver fields do not match the supported resolver schema.") from None
    if resolver.status != "unresolved" or resolver.execution_mode != "real":
        raise CaptureInputError("CAPTURE_NOT_UNRESOLVED", "Only a real unresolved resolver capture can seed this repair workflow.")
    metadata = [fetch_package_metadata(str(item["name"]), str(item.get("specifier", ""))) for item in parsed.requirements]
    report = build_compatibility_report(parsed.requirements, target, metadata, resolver)
    metadata_errors = [f"{item.package}: {item.error_message or item.error_code}" for item in metadata if item.status == "error"]
    source_id = token_urlsafe(18)
    source = RequirementSource(source_id, requirements_text, digest, target, parsed.requirements)
    analysis = AnalysisResponse(
        analysis_id=token_urlsafe(18), status="analysis_incomplete" if metadata_errors or report.status == "incomplete" else "complete", source_id=source_id, python_target=target,
        provenance={"source": "saved_resolver_capture", "capture_id": capture_id, "classification": provenance},
        requirements=parsed.requirements, metadata=metadata, resolver=resolver,
        compatibility_report=report,
        explanation=ExplanationResult(status="not_requested"),
        errors=metadata_errors, poll_url=None,
    )
    save_source(source)
    save_analysis(analysis)
    return analysis
