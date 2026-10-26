from __future__ import annotations

import json
import re
from collections.abc import Callable

from pydantic import ValidationError

from app.adapters.token_factory import TokenFactoryAdapter, TokenFactoryError
from app.config import Settings, settings
from app.schemas.analysis import CompatibilityReport, ExplanationResult

MAX_EVIDENCE_BYTES = 24_000
MAX_EXPLANATION_CHARS = 2_000


def _evidence_bundle(report: CompatibilityReport) -> tuple[str, set[str]]:
    evidence: list[dict[str, str]] = []
    for finding in report.findings[:50]:
        for item in finding.evidence:
            ref = f"E{len(evidence) + 1:03d}"
            evidence.append({
                "id": ref,
                "finding_code": finding.code,
                "classification": finding.classification,
                "label": item.label[:160],
                "value": item.value[:1_200],
                "package": (item.package or "")[:200],
                "source": item.source,
            })
    payload = {
        "deterministic_status": report.status,
        "deterministic_summary": report.summary[:1_000],
        "findings": [
            {"code": f.code, "classification": f.classification, "title": f.title[:300], "explanation": f.explanation[:800]}
            for f in report.findings[:50]
        ],
        "evidence": evidence,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise TokenFactoryError("EVIDENCE_TOO_LARGE")
    return encoded, {item["id"] for item in evidence}


def _parse_explanation(content: str, allowed_refs: set[str], api_key: str | None) -> ExplanationResult:
    try:
        decoded = json.loads(content)
        if not isinstance(decoded, dict) or set(decoded) != {"explanation", "evidence_refs"}:
            raise ValueError("unexpected fields")
        text = decoded["explanation"]
        refs = decoded["evidence_refs"]
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_EXPLANATION_CHARS:
            raise ValueError("invalid explanation")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in allowed_refs for ref in refs):
            raise ValueError("invalid evidence references")
        if allowed_refs and not refs:
            raise ValueError("evidence references are required")
        # The key is never sent as input; suppress output if a provider echoes it.
        if api_key and api_key in text:
            raise ValueError("sensitive value echoed")
        if re.search(r"(?i)\b(?:pip install|uv (?:pip )?(?:install|sync)|subprocess)\b", text):
            raise ValueError("command-like content")
        return ExplanationResult(status="available", text=text.strip(), evidence_refs=refs)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        raise TokenFactoryError("TOKEN_FACTORY_INVALID_OUTPUT") from None


def explain_report(
    report: CompatibilityReport,
    *,
    config: Settings = settings,
    adapter_factory: Callable[..., TokenFactoryAdapter] = TokenFactoryAdapter,
) -> ExplanationResult:
    if report.status == "simulated":
        return ExplanationResult(status="not_requested", error_code="SIMULATED_EVIDENCE")
    if not config.token_factory_enabled:
        return ExplanationResult(status="unavailable", error_code="TOKEN_FACTORY_DISABLED")
    if not config.nebius_api_key:
        return ExplanationResult(status="unavailable", error_code="TOKEN_FACTORY_NOT_CONFIGURED")
    try:
        bundle, allowed_refs = _evidence_bundle(report)
        if not allowed_refs:
            return ExplanationResult(status="unavailable", error_code="NO_EVIDENCE_TO_EXPLAIN")
        adapter = adapter_factory(
            api_key=config.nebius_api_key,
            base_url=config.token_factory_base_url,
            model_id=config.token_factory_model_id,
            timeout_seconds=config.token_factory_timeout_seconds,
        )
        content, _model = adapter.explain(bundle)
        return _parse_explanation(content, allowed_refs, config.nebius_api_key)
    except TokenFactoryError as exc:
        return ExplanationResult(status="unavailable", error_code=exc.code)
    except Exception:
        return ExplanationResult(status="unavailable", error_code="TOKEN_FACTORY_REQUEST_FAILED")
