from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Callable

from app.schemas.analysis import ResolverResult

MAX_CAPTURE_BYTES = 24_000
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API[_-]?KEY|CREDENTIAL)[A-Z0-9_]*\s*=\s*[^\r\n,;]+"
)
_URL_AUTH = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/@\s]+@")
_DIAGNOSTIC_FIELDS = ("stdout", "stderr", "resolved_requirements", "limitation")


def _safe_text(value: str, limit: int, *, tail: bool = False) -> str:
    value = value[-limit:] if tail else value[:limit]
    value = _URL_AUTH.sub(r"\1[REDACTED]@", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _SECRET_ASSIGNMENT.sub("[REDACTED_SECRET_ASSIGNMENT]", value)


def _payload(result: ResolverResult) -> str:
    # Explicit allowlist: never serialize settings, environment variables, or credentials.
    initially_truncated = (
        len(result.stdout) > 6_000
        or len(result.stderr) > 6_000
        or len(result.resolved_requirements or "") > 8_000
        or len(result.limitation or "") > 512
    )
    record = {
        "status": result.status,
        "execution_mode": result.execution_mode,
        "failure_category": result.failure_category,
        "resolver": _safe_text(result.resolver or "", 64),
        "resolver_version": _safe_text(result.resolver_version or "", 64),
        "python_target": _safe_text(result.python_target, 32),
        "sandbox_os": _safe_text(result.sandbox_os or "", 256),
        "sandbox_python": _safe_text(result.sandbox_python or "", 64),
        "target_matches_sandbox_python": result.target_matches_sandbox_python,
        "exit_code": result.exit_code,
        "stdout": _safe_text(result.stdout, 6_000, tail=True),
        "stderr": _safe_text(result.stderr, 6_000, tail=True),
        "resolved_requirements": _safe_text(result.resolved_requirements or "", 8_000),
        "limitation": _safe_text(result.limitation or "", 512),
        "capture_truncated": initially_truncated,
    }

    encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    # Reserve one byte for the newline written by capture_before_print.
    while len(encoded.encode("utf-8")) + 1 > MAX_CAPTURE_BYTES:
        record["capture_truncated"] = True
        candidates = [key for key in _DIAGNOSTIC_FIELDS if record[key]]
        if not candidates:
            record["sandbox_os"] = record["sandbox_os"][:64]
            record["limitation"] = "[diagnostics truncated]"
            encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
            break
        key = max(candidates, key=lambda item: len(record[item]))
        value = record[key]
        new_length = len(value) // 2
        record[key] = (
            value[-new_length:]
            if key in {"stdout", "stderr"} and new_length
            else value[:new_length]
        )
        encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True)

    if len(encoded.encode("utf-8")) + 1 > MAX_CAPTURE_BYTES:
        record.update(
            {"stdout": "", "stderr": "", "resolved_requirements": "", "limitation": ""}
        )
        record["sandbox_os"] = record["sandbox_os"][:64]
        record["capture_truncated"] = True
        encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    return encoded


def capture_before_print(
    result: ResolverResult,
    path: Path,
    printer: Callable[[str], None] = print,
    *,
    requirements_text: str | None = None,
    python_target: str | None = None,
    sandbox_image: str | None = None,
) -> bool:
    record = json.loads(_payload(result))
    if requirements_text is not None:
        record.update({
            "schema_version": 2,
            "requirements_sha256": hashlib.sha256(requirements_text.encode("utf-8")).hexdigest(),
            "python_target": python_target or result.python_target,
            "sandbox_image": _safe_text(sandbox_image or "", 128),
        })
    payload = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    while len(payload.encode("utf-8")) + 1 > MAX_CAPTURE_BYTES:
        record["capture_truncated"] = True
        fields = [key for key in _DIAGNOSTIC_FIELDS if isinstance(record.get(key), str) and record[key]]
        if not fields:
            record["sandbox_os"] = str(record.get("sandbox_os", ""))[:64]
            record["limitation"] = ""
            break
        field = max(fields, key=lambda key: len(record[key]))
        record[field] = record[field][: max(0, len(record[field]) // 2)]
        payload = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    payload = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    if len(payload.encode("utf-8")) + 1 > MAX_CAPTURE_BYTES:
        printer("CAPTURE_FAILED=SizeLimit; result was not saved; resolver status is unchanged.")
        printer("[bounded resolver result omitted]")
        return False
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    except OSError as exc:
        printer(
            f"CAPTURE_FAILED={type(exc).__name__}; result was not saved; "
            "resolver status is unchanged."
        )
        printer(payload)
        return False
    printer(payload)
    return True
