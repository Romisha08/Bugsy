from __future__ import annotations

import hashlib
from dataclasses import replace
import re
from typing import Any

from app.adapters.contree import ContreeImportAdapter, ContreeInstallationAdapter, ContreeVerificationAdapter, VERIFIED_IMPORT_NAMES
from app.config import Settings, settings
from app.domain.evidence import RequirementSource
from app.schemas.analysis import AnalysisResponse
from app.schemas.verification import (
    ImportStageResult, InstallationStageEnvironment, InstallationStageResult, VerificationCheck,
    VerificationChecks, VerificationEnvironment, VerificationResult,
)

_INSTALLATION_FAILURE_PHASES = {
    "sdk_configuration", "image_selection", "operation_submission", "operation_wait",
    "import_image_selection", "import_operation_submission", "import_operation_wait",
    "client_configuration", "file_staging", "operation_tracking", "image_validation",
    "adapter_invocation", "setup", "resolver_version", "environment", "installation", "imports", "unknown",
}


def sanitize_installation_diagnostics(phase: object, detail: object) -> tuple[str | None, str | None]:
    """Constrain persisted diagnostics to known phases and redact common secret forms."""
    safe_phase = phase if isinstance(phase, str) and phase in _INSTALLATION_FAILURE_PHASES else "unknown"
    if not isinstance(detail, str):
        return safe_phase, None
    safe_detail = re.sub(r"(?i)https?://\S+", "[redacted URL]", detail)
    safe_detail = re.sub(r"(?i)\b(?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+[^\s,;]+", "[redacted credential]", safe_detail)
    safe_detail = re.sub(
        r"(?i)\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API[_-]?KEY|CREDENTIAL)[A-Z0-9_]*\s*[:=]\s*[^,\s;]+",
        "[redacted secret]", safe_detail,
    )
    safe_detail = "".join(character for character in safe_detail if character.isprintable() or character in "\t\n")
    return safe_phase, safe_detail[:240] or None

def import_modules_for(analysis: AnalysisResponse) -> list[tuple[str, str]]:
    mapped: dict[str, str] = {}
    for item in analysis.requirements:
        if item.name in VERIFIED_IMPORT_NAMES:
            mapped[item.name] = VERIFIED_IMPORT_NAMES[item.name]
    return list(mapped.items())


def _empty_checks(reason: str) -> VerificationChecks:
    return VerificationChecks(
        resolution=VerificationCheck(status="not_run", reason=reason),
        installation=VerificationCheck(status="not_run", reason=reason),
        imports=VerificationCheck(status="not_run", reason=reason),
        project_tests=VerificationCheck(status="not_run", reason="Project code not provided."),
    )


def new_verification(analysis: AnalysisResponse) -> VerificationResult:
    proposal = analysis.repair_proposal
    assert proposal and proposal.proposal_id and proposal.diff_sha256
    from secrets import token_urlsafe
    return VerificationResult(
        verification_id=token_urlsafe(18), analysis_id=analysis.analysis_id,
        proposal_id=proposal.proposal_id, diff_sha256=proposal.diff_sha256,
        status="queued", execution_mode="not_run",
        environment=VerificationEnvironment(requested_python=analysis.python_target),
        checks=_empty_checks("Verification has not started."),
    )


def _check(data: Any, default_reason: str) -> VerificationCheck:
    if not isinstance(data, dict) or data.get("status") not in {"passed", "failed", "not_run", "skipped"}:
        return VerificationCheck(status="not_run", reason=default_reason)
    return VerificationCheck(
        status=data["status"],
        exit_code=data.get("exit_code") if isinstance(data.get("exit_code"), int) else None,
        stdout=data.get("stdout", "")[-4000:] if isinstance(data.get("stdout"), str) else "",
        stderr=data.get("stderr", "")[-4000:] if isinstance(data.get("stderr"), str) else "",
        reason=data.get("reason") if isinstance(data.get("reason"), str) else None,
    )


def _target_matches(requested: str, actual: str | None) -> bool | None:
    if not actual:
        return None
    req, got = requested.split("."), actual.split(".")
    return len(req) <= len(got) and req == got[:len(req)]


def _incomplete(analysis: AnalysisResponse, job: VerificationResult, code: str) -> VerificationResult:
    messages = {
        "SANDBOX_DISABLED": "ConTree verification is disabled. No real checks ran.",
        "SANDBOX_NOT_CONFIGURED": "ConTree credentials or endpoint are not configured. No real checks ran.",
        "CONTREE_NETWORK_FAILURE": "ConTree could not be reached. No verification result is available.",
        "CONTREE_SANDBOX_FAILURE": "ConTree failed to execute the verification job.",
        "CONTREE_UNKNOWN_FAILURE": "ConTree verification failed for an unknown reason.",
        "CONTREE_RESULT_UNAVAILABLE": "ConTree returned no usable structured result.",
    }
    reason = messages.get(code, "Verification did not complete. No compatibility claim is available.")
    category = "network_failure" if code == "CONTREE_NETWORK_FAILURE" else "sandbox_failure" if code == "CONTREE_SANDBOX_FAILURE" else "unknown_failure" if code == "CONTREE_UNKNOWN_FAILURE" else None
    return VerificationResult(
        verification_id=job.verification_id, analysis_id=analysis.analysis_id, proposal_id=job.proposal_id,
        diff_sha256=job.diff_sha256,
        status="unavailable" if code in {"SANDBOX_DISABLED", "SANDBOX_NOT_CONFIGURED"} else "incomplete",
        execution_mode="not_run", environment=VerificationEnvironment(requested_python=analysis.python_target),
        failure_category=category, checks=_empty_checks(reason), limitation=reason,
    )


def run_verification(
    analysis: AnalysisResponse,
    source: RequirementSource,
    job: VerificationResult,
    *,
    config: Settings | None = None,
    adapter: Any | None = None,
) -> VerificationResult:
    # Resolve the module setting at call time so the API and tests use the
    # effective configuration rather than an import-time default snapshot.
    config = config or settings
    proposal = analysis.repair_proposal
    if not proposal or proposal.status != "ready" or not proposal.proposed_requirements:
        return _incomplete(analysis, job, "PROPOSAL_NOT_READY")
    if source.source_sha256 != proposal.original_sha256:
        return _incomplete(analysis, job, "SOURCE_HASH_MISMATCH")
    actual_hash = hashlib.sha256(proposal.diff.encode("utf-8")).hexdigest() if proposal.diff else ""
    if actual_hash != job.diff_sha256 or proposal.diff_sha256 != job.diff_sha256:
        return _incomplete(analysis, job, "DIFF_HASH_MISMATCH")
    if not config.sandbox_enabled:
        return _incomplete(analysis, job, "SANDBOX_DISABLED")
    if not config.nebius_api_key or not config.contree_base_url:
        return _incomplete(analysis, job, "SANDBOX_NOT_CONFIGURED")
    try:
        output = (adapter or ContreeVerificationAdapter()).verify(
            proposal.proposed_requirements, analysis.python_target, import_modules_for(analysis)
        )
    except RuntimeError as exc:
        return _incomplete(analysis, job, str(exc).split(":", 1)[0])
    except Exception:
        return _incomplete(analysis, job, "CONTREE_UNKNOWN_FAILURE")
    if not isinstance(output, dict):
        return _incomplete(analysis, job, "CONTREE_RESULT_UNAVAILABLE")

    env = output.get("environment") if isinstance(output.get("environment"), dict) else {}
    actual_python = env.get("python_version") if isinstance(env.get("python_version"), str) else None
    actual_os = env.get("os") if isinstance(env.get("os"), str) else None
    matches = _target_matches(analysis.python_target, actual_python)
    raw = output.get("checks") if isinstance(output.get("checks"), dict) else {}
    checks = VerificationChecks(
        resolution=_check(raw.get("resolution"), "Resolution result was not returned."),
        installation=_check(raw.get("installation"), "Installation result was not returned."),
        imports=_check(raw.get("imports"), "Import result was not returned."),
        project_tests=VerificationCheck(status="not_run", reason="Project code not provided."),
    )
    limitation = None
    if matches is False:
        limitation = f"Requested Python {analysis.python_target} differs from actual Sandbox Python {actual_python}. Installation and imports are not evidence for the requested target."
        checks = checks.model_copy(update={
            "installation": VerificationCheck(status="not_run", reason=limitation),
            "imports": VerificationCheck(status="not_run", reason=limitation),
        })
    complete = checks.resolution.status == "passed" and checks.installation.status == "passed" and checks.imports.status in {"passed", "skipped"} and matches is True
    category = output.get("failure_category")
    return VerificationResult(
        verification_id=job.verification_id, analysis_id=analysis.analysis_id,
        proposal_id=job.proposal_id, diff_sha256=job.diff_sha256,
        status="complete" if complete else "incomplete", execution_mode="real",
        environment=VerificationEnvironment(
            sandbox_provider="contree", os=actual_os, python_version=actual_python,
            requested_python=analysis.python_target, target_matches_sandbox_python=matches,
        ),
        resolver="uv",
        resolver_version=output.get("resolver_version") if isinstance(output.get("resolver_version"), str) else None,
        failure_category=category if category in {"sandbox_failure", "network_failure", "resolver_failure", "unknown_failure"} else None,
        checks=checks, limitation=limitation or (output.get("limitation") if isinstance(output.get("limitation"), str) else None),
    )


def execute_installation_stage(
    stage_id: str,
    *,
    config: Settings = settings,
    adapter_factory: Any | None = None,
) -> InstallationStageResult | None:
    """Run exactly one installation-only operation for an atomically claimed stage."""
    from datetime import UTC, datetime
    from app.services.workflow import (
        get_installation_stage, installation_stage_context, mark_installation_stage_started,
        persist_installation_operation_id, save_installation_stage_result,
    )

    pending = get_installation_stage(stage_id)
    if pending is None or pending.status != "running":
        return pending
    context = installation_stage_context(stage_id)
    if context is None:
        result = pending.model_copy(update={
            "status": "incomplete", "execution_mode": "not_run", "failure_category": "unknown_failure",
            "limitation": "Persisted proposal or approval failed revalidation before installation; no Sandbox operation ran.",
            "updated_at": datetime.now(UTC).isoformat(),
        })
        save_installation_stage_result(result)
        return result
    analysis, _source, requirements_text = context
    started = mark_installation_stage_started(stage_id)
    if started is None:
        return get_installation_stage(stage_id)
    submitted_operation_id: list[str | None] = [None]

    def record_submission(operation_id: str) -> bool:
        saved = persist_installation_operation_id(stage_id, operation_id)
        if saved:
            submitted_operation_id[0] = operation_id
        return saved

    try:
        factory = adapter_factory or (lambda: ContreeInstallationAdapter(config=config))
        output = factory().install(
            requirements_text, analysis.python_target,
            on_operation_submitted=record_submission,
        )
    except Exception as exc:
        output = {
            "status": "unknown", "environment": {}, "exit_code": None, "stdout": "", "stderr": "",
            "failure_category": "unknown_failure",
            "failure_phase": "adapter_invocation",
            "failure_detail": f"Installation adapter failed ({type(exc).__name__}); no structured Sandbox outcome is available.",
            "limitation": f"Installation outcome is unknown ({type(exc).__name__}); approval remains consumed and no retry will occur.",
        }
    if not isinstance(output, dict):
        output = {
            "status": "unknown", "environment": {}, "exit_code": None, "stdout": "", "stderr": "",
            "failure_category": "unknown_failure", "limitation": "Installation adapter returned no structured result.",
        }
    environment = output.get("environment") if isinstance(output.get("environment"), dict) else {}
    target_match = environment.get("target_matches_sandbox_python")
    status = output.get("status")
    if status == "passed" and (output.get("exit_code") != 0 or target_match is not True):
        status = "incomplete"
    if status not in {"passed", "failed", "incomplete", "unknown"}:
        status = "unknown"
    category = output.get("failure_category")
    if category not in {"sandbox_failure", "network_failure", "installation_failure", "unknown_failure"}:
        category = None
    safe_failure_phase, safe_failure_detail = sanitize_installation_diagnostics(
        output.get("failure_phase"), output.get("failure_detail"),
    )
    result = started.model_copy(update={
        "status": status,
        "execution_mode": "real",
        "contree_operation_id": submitted_operation_id[0],
        "environment": InstallationStageEnvironment(
            sandbox_provider="contree",
            os=environment.get("os") if isinstance(environment.get("os"), str) else None,
            python_version=environment.get("python_version") if isinstance(environment.get("python_version"), str) else None,
            requested_python=analysis.python_target,
            target_matches_sandbox_python=target_match if isinstance(target_match, bool) else None,
        ),
        "uv_version": output.get("uv_version") if isinstance(output.get("uv_version"), str) else None,
        "exit_code": output.get("exit_code") if isinstance(output.get("exit_code"), int) else None,
        "stdout": output.get("stdout", "")[-4000:] if isinstance(output.get("stdout"), str) else "",
        "stderr": output.get("stderr", "")[-4000:] if isinstance(output.get("stderr"), str) else "",
        "failure_category": category,
        "failure_phase": safe_failure_phase if output.get("failure_phase") is not None else None,
        "failure_detail": safe_failure_detail,
        "limitation": output.get("limitation") if isinstance(output.get("limitation"), str) else None,
        "updated_at": datetime.now(UTC).isoformat(),
    })
    save_installation_stage_result(result)
    return result


def _safe_stage_output(value: object) -> str:
    if not isinstance(value, str):
        return ""
    safe = re.sub(r"(?i)https?://\S+", "[redacted URL]", value)
    safe = re.sub(r"(?i)\b(?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+[^\s,;]+", "[redacted credential]", safe)
    safe = re.sub(r"(?i)\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API[_-]?KEY|CREDENTIAL)[A-Z0-9_]*\s*[:=]\s*[^,\s;]+", "[redacted secret]", safe)
    safe = "".join(ch for ch in safe if ch.isprintable() or ch in "\t\n")
    return safe[-4000:]


def execute_verification_attempt(
    stage_id: str,
    *,
    config: Settings = settings,
    installation_adapter_factory: Any | None = None,
    import_adapter_factory: Any | None = None,
) -> InstallationStageResult | ImportStageResult | None:
    """Execute one already-consumed, persisted installation or imports approval."""
    from datetime import UTC, datetime
    from uuid import UUID
    from app.services.workflow import (
        get_verification_attempt, mark_verification_attempt_started,
        persist_installation_operation_id, save_verification_attempt_result, verification_attempt_context,
    )

    pending = get_verification_attempt(stage_id)
    if pending is None or pending.status != "running":
        return pending
    context = verification_attempt_context(stage_id)
    if context is None:
        failed = pending.model_copy(update={
            "status": "incomplete", "execution_mode": "not_run", "failure_category": "unknown_failure",
            "limitation": "Persisted proposal, approval, or required installation artifact failed revalidation; no Sandbox operation ran.",
            "updated_at": datetime.now(UTC).isoformat(),
        })
        save_verification_attempt_result(failed)
        return failed
    analysis, _source, requirements_text, stage, image_ref = context
    started = mark_verification_attempt_started(stage_id)
    if started is None:
        return get_verification_attempt(stage_id)
    submitted_operation_id: list[str | None] = [None]
    output: dict[str, Any]
    try:
        if not config.sandbox_enabled:
            raise RuntimeError("SANDBOX_DISABLED")
        if stage == "installation":
            factory = installation_adapter_factory or (lambda: ContreeInstallationAdapter(config=replace(
                config, sandbox_enabled=True,
            )))
            def record_submission(operation_id: str) -> bool:
                saved = persist_installation_operation_id(stage_id, operation_id)
                if saved:
                    submitted_operation_id[0] = operation_id
                return saved

            output = factory().install(
                requirements_text, analysis.python_target,
                retain_artifact=config.sandbox_retain_installation_artifact,
                on_operation_submitted=record_submission,
            )
        else:
            if not image_ref:
                raise RuntimeError("CONTREE_IMAGE_REFERENCE_MISSING")
            try:
                UUID(image_ref)
            except ValueError:
                raise RuntimeError("CONTREE_IMAGE_REFERENCE_INVALID") from None
            factory = import_adapter_factory or (lambda: ContreeImportAdapter(config=replace(
                config, sandbox_enabled=True,
            )))
            output = factory().verify(image_ref, analysis.python_target, import_modules_for(analysis))
    except Exception as exc:
        output = {
            "status": "unknown", "environment": {}, "exit_code": None, "stdout": "", "stderr": "",
            "failure_category": "unknown_failure", "failure_phase": "adapter_invocation",
            "failure_detail": f"{stage.capitalize()} adapter failed ({type(exc).__name__}); no structured Sandbox outcome is available.",
            "limitation": f"{stage.capitalize()} outcome is unknown/incomplete; approval remains consumed and no automatic retry will occur.",
        }
    if not isinstance(output, dict):
        output = {"status": "unknown", "environment": {}, "exit_code": None, "failure_category": "unknown_failure",
                  "limitation": "Sandbox adapter returned no structured result; outcome is unknown."}
    environment = output.get("environment") if isinstance(output.get("environment"), dict) else {}
    actual_python = environment.get("python_version") if isinstance(environment.get("python_version"), str) else None
    parts = analysis.python_target.split(".")
    actual_parts = actual_python.split(".") if actual_python else []
    target_match = len(actual_parts) >= len(parts) and actual_parts[:len(parts)] == parts
    status = output.get("status")
    exit_code = output.get("exit_code") if isinstance(output.get("exit_code"), int) else None
    if status == "passed" and (exit_code != 0 or not target_match):
        status = "incomplete" if exit_code == 0 else "unknown"
    if status not in {"passed", "failed", "incomplete", "unknown"}:
        status = "unknown"
    categories = {"sandbox_failure", "network_failure", "unknown_failure"}
    if stage == "installation":
        categories.add("installation_failure")
    else:
        categories.add("import_failure")
    category = output.get("failure_category") if output.get("failure_category") in categories else None
    phase, detail = sanitize_installation_diagnostics(output.get("failure_phase", output.get("phase")), output.get("failure_detail"))
    now = datetime.now(UTC).isoformat()
    common = {
        "status": status, "execution_mode": "real",
        "environment": InstallationStageEnvironment(
            sandbox_provider="contree",
            os=environment.get("os") if isinstance(environment.get("os"), str) else None,
            python_version=actual_python, requested_python=analysis.python_target,
            target_matches_sandbox_python=target_match if actual_python else None,
        ),
        "exit_code": exit_code, "stdout": _safe_stage_output(output.get("stdout")),
        "stderr": _safe_stage_output(output.get("stderr")), "failure_category": category,
        "failure_phase": phase if output.get("failure_phase", output.get("phase")) is not None else None,
        "failure_detail": detail, "limitation": output.get("limitation") if isinstance(output.get("limitation"), str) else None,
        "updated_at": now,
    }
    if stage == "installation":
        ref = output.get("sandbox_image_ref") if isinstance(output.get("sandbox_image_ref"), str) else None
        valid_ref = None
        if ref:
            try:
                valid_ref = str(UUID(ref))
            except (ValueError, TypeError):
                valid_ref = None
        result = started.model_copy(update={
            **common, "uv_version": output.get("uv_version") if isinstance(output.get("uv_version"), str) else None,
            "contree_operation_id": submitted_operation_id[0],
            "artifact_available": status == "passed" and target_match and bool(valid_ref),
        })
        if config.sandbox_retain_installation_artifact and status == "passed" and not valid_ref:
            result = result.model_copy(update={"limitation": "Installation passed, but ConTree returned no valid retained image reference; import checks cannot run against this result."})
        if not config.sandbox_retain_installation_artifact and status == "passed":
            result = result.model_copy(update={"limitation": "Installation passed in a disposable Sandbox. Artifact retention is disabled, so a separate import stage is unavailable."})
        save_verification_attempt_result(result, valid_ref)
    else:
        result = started.model_copy(update=common)
        save_verification_attempt_result(result)
    return result
