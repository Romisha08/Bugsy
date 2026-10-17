from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.schemas.analysis import ResolverResult

UV_RESOLVER_VERSION = "0.12.19"
_RESOLVER_SCRIPT_TEMPLATE = r'''import json, os, platform, subprocess, sys
PIN = "__UV_RESOLVER_VERSION__"
TARGET = os.environ["BUGSY_PYTHON_TARGET"]
env = {"sandbox_os": platform.platform(), "sandbox_python": sys.version.split()[0]}
install = subprocess.run([sys.executable, "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "uv==" + PIN], capture_output=True, text=True, timeout=90)
if install.returncode:
    print("BUGSY_RESULT=" + json.dumps({"status":"error", "phase":"resolver_setup", "environment":env, "exit_code":install.returncode, "stdout":install.stdout[-6000:], "stderr":install.stderr[-6000:]}))
    raise SystemExit(70)
version = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=10)
actual_version = version.stdout.strip().split()[1] if version.returncode == 0 and len(version.stdout.strip().split()) > 1 else None
if actual_version != PIN:
    print("BUGSY_RESULT=" + json.dumps({"status":"error", "phase":"resolver_version", "environment":env, "resolver_version":actual_version, "stdout":version.stdout[-2000:], "stderr":version.stderr[-2000:]}))
    raise SystemExit(71)
resolved_path = "/tmp/bugsy-resolved.txt"
resolve = subprocess.run(["uv", "pip", "compile", "--python-version", TARGET, "--no-header", "--output-file", resolved_path, "/tmp/requirements.txt"], capture_output=True, text=True, timeout=120)
try:
    with open(resolved_path, "r", encoding="utf-8") as handle:
        resolved = handle.read(20000)
except OSError:
    resolved = None
print("BUGSY_RESULT=" + json.dumps({"status":"resolved" if resolve.returncode == 0 else "unresolved", "phase":"resolution", "environment":env, "resolver_version":actual_version, "exit_code":resolve.returncode, "stdout":resolve.stdout[-6000:], "stderr":resolve.stderr[-6000:], "resolved_requirements":resolved}))
raise SystemExit(resolve.returncode)
'''


def _resolver_script() -> str:
    return _RESOLVER_SCRIPT_TEMPLATE.replace("__UV_RESOLVER_VERSION__", UV_RESOLVER_VERSION)


def build_sdk_run_kwargs(requirements_text: str, python_target: str) -> dict[str, Any]:
    """Build a shell-free, fixed ConTree request using the SDK's file and argv APIs."""
    if len(requirements_text.encode("utf-8")) > settings.max_requirements_bytes:
        raise ValueError("Requirements input exceeds configured size limit.")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", python_target):
        raise ValueError("Python target must be a validated version number.")
    if len(requirements_text.splitlines()) > settings.max_requirements_lines:
        raise ValueError("Requirements input exceeds configured line limit.")
    return {
        "command": "python",
        "args": ["-c", _resolver_script()],
        "env": {"BUGSY_PYTHON_TARGET": python_target},
        "cwd": "/tmp",
        "files": {"/tmp/requirements.txt": requirements_text.encode("utf-8")},
        "timeout": settings.sandbox_operation_timeout,
        "truncate_output_at": 24000,
    }


def _text(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")


def _parse_result(stdout: str, fallback_code: int | None, python_target: str) -> ResolverResult:
    result_data = None
    for line in reversed(stdout.splitlines()):
        if line.startswith("BUGSY_RESULT="):
            try:
                result_data = json.loads(line.removeprefix("BUGSY_RESULT="))
            except json.JSONDecodeError:
                break
            break
    if not isinstance(result_data, dict):
        return ResolverResult(
            status="error",
            execution_mode="real",
            resolver="uv",
            python_target=python_target,
            exit_code=fallback_code,
            stdout=stdout[-6000:],
            limitation="ConTree returned no parseable result record. No compatibility claim is available.",
        )
    env = result_data.get("environment") or {}
    sandbox_python = env.get("sandbox_python")
    target_matches = False
    if isinstance(sandbox_python, str):
        target_parts = python_target.split(".")
        actual_parts = sandbox_python.split(".")
        compared_parts = 3 if len(target_parts) == 3 else 2
        target_matches = target_parts[:compared_parts] == actual_parts[:compared_parts]
    status = result_data.get("status")
    if status not in {"resolved", "unresolved", "error"}:
        status = "error"
    limitation = None
    if not target_matches:
        limitation = (
            f"Requested Python {python_target} differs from the actual Sandbox Python {sandbox_python or 'unknown'}. "
            "uv was asked to resolve for the requested target, but this run does not verify execution on that Python target."
        )
    return ResolverResult(
        status=status,
        execution_mode="real",
        resolver="uv",
        resolver_version=result_data.get("resolver_version"),
        python_target=python_target,
        sandbox_os=env.get("sandbox_os"),
        sandbox_python=sandbox_python,
        target_matches_sandbox_python=target_matches,
        exit_code=result_data.get("exit_code", fallback_code),
        stdout=result_data.get("stdout", "")[-6000:],
        stderr=result_data.get("stderr", "")[-6000:],
        resolved_requirements=result_data.get("resolved_requirements"),
        limitation=limitation,
    )


class ContreeResolverAdapter:
    def resolve(self, requirements_text: str, python_target: str) -> ResolverResult:
        if not settings.sandbox_enabled:
            return ResolverResult(
                status="not_run",
                execution_mode="not_run",
                python_target=python_target,
                limitation="ConTree resolution is disabled. No dependency resolution was performed.",
            )
        if not settings.nebius_api_key or not settings.contree_base_url:
            return ResolverResult(
                status="not_run",
                execution_mode="not_run",
                python_target=python_target,
                limitation="ConTree is enabled but required backend credentials are not configured. No resolution was performed.",
            )
        try:
            from contree_sdk import ContreeSync
            from contree_sdk.auth import IAMAuth
            from contree_sdk.config import ContreeConfig

            config = ContreeConfig(
                auth=IAMAuth(token=settings.nebius_api_key, base_url=settings.contree_base_url),
                transport_timeout=30.0,
            )
            result = (
                ContreeSync(config)
                .images.use(settings.sandbox_image)
                .run(**build_sdk_run_kwargs(requirements_text, python_target))
                .wait()
            )
        except Exception as exc:  # Keep credentials and request contents out of diagnostics.
            return ResolverResult(
                status="error",
                execution_mode="real",
                resolver="uv",
                python_target=python_target,
                limitation=f"ConTree resolution could not complete ({type(exc).__name__}). No compatibility claim is available.",
            )
        return _parse_result(_text(result.stdout), result.exit_code, python_target)
