from __future__ import annotations

import json
import re
from typing import Any, Callable
from uuid import UUID

from app.config import sandbox_image_for_python_target, settings
from app.schemas.analysis import ResolverResult

UV_RESOLVER_VERSION = "0.12.19"
SANDBOX_PYTHON_EXECUTABLE = "/usr/local/bin/python"
VERIFIED_IMPORT_NAMES = {
    "beautifulsoup4": "bs4", "opencv-python": "cv2", "pillow": "PIL",
    "pyyaml": "yaml", "scikit-learn": "sklearn", "tensorflow": "tensorflow",
    "mediapipe": "mediapipe", "numpy": "numpy", "pandas": "pandas",
    "requests": "requests", "streamlit": "streamlit",
}
_NETWORK_EVIDENCE = (
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "connection timed out",
    "network is unreachable",
    "failed to fetch",
)
_CONFLICT_EVIDENCE = "no solution found when resolving dependencies"
_SAFE_SERVER_ERROR_CODES = frozenset({
    "BAD_REQUEST", "INVALID_REQUEST", "VALIDATION_ERROR", "INVALID_FIELD",
    "MISSING_FIELD", "UNSUPPORTED_MEDIA_TYPE", "FILE_UPLOAD_REJECTED",
})
_SAFE_SERVER_ERROR_FIELDS = frozenset({
    "content", "content_type", "file", "files", "path", "uuid", "mode",
})
_SAFE_HTTPX_TRANSPORT_ERROR_TYPES = frozenset({
    "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout",
    "WriteError", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
    "LocalProtocolError", "ProxyError", "UnsupportedProtocol",
    "NetworkError", "TimeoutException",
})
_SAFE_NESTED_TRANSPORT_TYPES = {
    "httpcore": frozenset({
        "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout", "WriteError",
        "WriteTimeout", "PoolTimeout", "RemoteProtocolError", "LocalProtocolError",
        "ProxyError", "UnsupportedProtocol", "NetworkError",
    }),
    "socket": frozenset({"gaierror", "herror"}),
    "ssl": frozenset({"SSLError", "SSLCertVerificationError"}),
    "builtins": frozenset({
        "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
        "ConnectionAbortedError", "TimeoutError", "OSError", "BrokenPipeError",
    }),
}
_SAFE_RESOLVER_FAILURE_PHASES = frozenset({
    "client_configuration", "file_staging", "operation_submission", "operation_tracking",
    "python_target_validation", "resolver_setup", "resolver_version", "resolution",
    "result_parsing",
})
_PYTHON_TARGET_VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
_SANDBOX_PYTHON_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(?:a|b|rc)\d+)?$")


def _validated_runtime_match(python_target: str, sandbox_python: Any) -> tuple[str | None, bool | None]:
    """Return a safe actual version and whether it matches the requested major/minor[/patch]."""
    target_match = _PYTHON_TARGET_VERSION.fullmatch(python_target)
    actual_match = _SANDBOX_PYTHON_VERSION.fullmatch(sandbox_python) if isinstance(sandbox_python, str) else None
    if target_match is None or actual_match is None:
        return None, None
    target_parts = target_match.groups()
    actual_parts = actual_match.groups()
    matches = target_parts[:2] == actual_parts[:2]
    if target_parts[2] is not None:
        matches = matches and target_parts[2] == actual_parts[2]
    return sandbox_python, matches


def _classify_outcome(phase: str, code: int | None, stdout: str, stderr: str) -> str | None:
    evidence = f"{stdout}\n{stderr}".lower()
    if phase == "resolution" and code == 0:
        return None
    if any(marker in evidence for marker in _NETWORK_EVIDENCE):
        return "network_failure"
    if phase == "resolver_version":
        return "sandbox_failure"
    if phase != "resolution":
        return "unknown_failure"
    if code is not None and _CONFLICT_EVIDENCE in evidence:
        return "resolver_failure"
    return "unknown_failure"


def _classify_sdk_exception(exc: Exception) -> str:
    name = type(exc).__name__
    if name in {"ApiTimeoutError", "APIConnectionError", "ContreeTransportError", "ConnectionError", "TimeoutError"}:
        return "network_failure"
    return "unknown_failure"


def _safe_api_error_diagnostics(exc: Exception) -> dict[str, Any]:
    """Extract only allowlisted structured fields from the SDK's API status errors.

    The SDK exposes ``status`` and an arbitrary ``error`` value. Never stringify
    the exception, error payload, traceback, or response body here.
    """
    try:
        from contree_client.exceptions import APIStatusError
    except ImportError:
        return {}
    if not isinstance(exc, APIStatusError):
        return {}

    status = getattr(exc, "status", None)
    result: dict[str, Any] = {}
    if isinstance(status, int) and not isinstance(status, bool) and 400 <= status <= 599:
        result["http_status"] = status

    detail = getattr(exc, "error", None)
    if not isinstance(detail, dict):
        return result
    code = detail.get("code")
    if isinstance(code, str) and code in _SAFE_SERVER_ERROR_CODES:
        result["server_error_code"] = code
    field = detail.get("field")
    if isinstance(field, str) and field in _SAFE_SERVER_ERROR_FIELDS:
        result["server_error_field"] = field
    return result


def _safe_api_connection_diagnostics(exc: Exception) -> dict[str, Any]:
    """Persist only allowlisted transport class names, numeric OS codes, and timeout state."""
    try:
        import httpx
        from contree_client.exceptions import APIConnectionError
    except ImportError:
        return {}
    if not isinstance(exc, APIConnectionError):
        return {}

    result: dict[str, Any] = {}
    timed_out = getattr(exc, "timed_out", None)
    if isinstance(timed_out, bool):
        result["transport_timed_out"] = timed_out

    cause = exc.__cause__ or exc.__context__
    visited: set[int] = set()
    for _ in range(4):
        if cause is None or id(cause) in visited:
            break
        visited.add(id(cause))
        cause_type = type(cause)
        cause_name = cause_type.__name__
        module = cause_type.__module__
        if (module == "httpx" and cause_name in _SAFE_HTTPX_TRANSPORT_ERROR_TYPES
                and isinstance(cause, httpx.TransportError)):
            result["transport_error_type"] = cause_name
            result["transport_cause_type"] = cause_name
        elif cause_name in _SAFE_NESTED_TRANSPORT_TYPES.get(module, ()):
            result["transport_cause_type"] = cause_name
            if isinstance(cause, OSError):
                # Access errno only on an allowlisted OS exception. Never inspect args or text.
                code = cause.errno
                try:
                    import errno
                    import socket
                    allowed_codes = {
                        getattr(errno, name) for name in (
                            "EACCES", "EADDRNOTAVAIL", "ECONNABORTED", "ECONNREFUSED",
                            "ECONNRESET", "EHOSTUNREACH", "EINTR", "EINVAL", "ENETUNREACH",
                            "ENETDOWN", "EPIPE", "ETIMEDOUT",
                        ) if hasattr(errno, name)
                    }
                    allowed_codes.update(
                        getattr(socket, name) for name in ("EAI_AGAIN", "EAI_FAIL", "EAI_NONAME")
                        if hasattr(socket, name)
                    )
                except ImportError:
                    allowed_codes = set()
                if isinstance(code, int) and not isinstance(code, bool) and code in allowed_codes:
                    result["transport_os_error_code"] = code
        cause = cause.__cause__ or cause.__context__
    return result


def _safe_sdk_failure_diagnostics(exc: Exception, phase: str) -> dict[str, str]:
    """Return bounded, non-secret diagnostics; SDK messages may contain request data."""
    safe_phase = phase if phase in {
        "client_configuration", "file_staging", "operation_submission", "operation_tracking",
        "image_validation", "import_image_validation",
    } else "sdk_operation"
    exception_name = type(exc).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", exception_name):
        exception_name = "SDKError"
    cause = exc.__cause__ or exc.__context__
    cause_name = type(cause).__name__ if cause is not None else None
    if cause_name and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", cause_name):
        cause_name = "TransportError"
    category = _classify_sdk_exception(exc)
    if category == "network_failure":
        detail = f"SDK transport/network error ({exception_name}{' caused by ' + cause_name if cause_name else ''}); Sandbox execution outcome is unknown."
    elif category == "sandbox_failure":
        detail = f"ConTree reported an operation error ({exception_name}); see the structured operation result for its outcome."
    else:
        detail = f"SDK operation failed ({exception_name}); no structured Sandbox outcome is available."
    return {"failure_phase": safe_phase, "failure_detail": detail[:240]}


_RESOLVER_SCRIPT_TEMPLATE = r'''import json, os, platform, subprocess, sys
PIN = "__UV_RESOLVER_VERSION__"
TARGET = os.environ["BUGSY_PYTHON_TARGET"]
env = {"sandbox_os": platform.platform(), "sandbox_python": sys.version.split()[0]}
NETWORK_EVIDENCE = ("temporary failure in name resolution", "failed to establish a new connection", "connection timed out", "network is unreachable", "failed to fetch")
CONFLICT_EVIDENCE = "no solution found when resolving dependencies"
target_parts = TARGET.split(".")
actual_parts = env["sandbox_python"].split(".")
if actual_parts[:len(target_parts)] != target_parts:
    message = "Sandbox Python " + env["sandbox_python"] + " does not match requested target " + TARGET + "; dependency resolution was not run."
    print("BUGSY_RESULT=" + json.dumps({"status":"error", "phase":"python_target_validation", "failure_category":"sandbox_failure", "environment":env, "exit_code":None, "stdout":"", "stderr":message, "resolved_requirements":None}))
    raise SystemExit(0)
def category(phase, code, stdout, stderr):
    evidence = (stdout + "\n" + stderr).lower()
    if phase == "resolution" and code == 0: return None
    if any(marker in evidence for marker in NETWORK_EVIDENCE): return "network_failure"
    if phase == "resolver_version": return "sandbox_failure"
    if phase != "resolution": return "unknown_failure"
    if code is not None and CONFLICT_EVIDENCE in evidence: return "resolver_failure"
    return "unknown_failure"
install = subprocess.run([sys.executable, "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "uv==" + PIN], capture_output=True, text=True, timeout=90)
if install.returncode:
    print("BUGSY_RESULT=" + json.dumps({"status":"error", "phase":"resolver_setup", "failure_category":category("resolver_setup", install.returncode, install.stdout, install.stderr), "environment":env, "exit_code":install.returncode, "stdout":install.stdout[-6000:], "stderr":install.stderr[-6000:]}))
    raise SystemExit(70)
version = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=10)
actual_version = version.stdout.strip().split()[1] if version.returncode == 0 and len(version.stdout.strip().split()) > 1 else None
if actual_version != PIN:
    print("BUGSY_RESULT=" + json.dumps({"status":"error", "phase":"resolver_version", "failure_category":category("resolver_version", version.returncode or 1, version.stdout, version.stderr), "environment":env, "resolver_version":actual_version, "exit_code":version.returncode, "stdout":version.stdout[-2000:], "stderr":version.stderr[-2000:]}))
    raise SystemExit(71)
resolved_path = "/tmp/bugsy-resolved.txt"
resolve = subprocess.run(["uv", "pip", "compile", "--python-version", TARGET, "--no-header", "--output-file", resolved_path, "/tmp/requirements.txt"], capture_output=True, text=True, timeout=120)
try:
    with open(resolved_path, "r", encoding="utf-8") as handle:
        resolved = handle.read(20000)
except OSError:
    resolved = None
failure = category("resolution", resolve.returncode, resolve.stdout, resolve.stderr)
status = "resolved" if resolve.returncode == 0 else ("unresolved" if failure == "resolver_failure" else "error")
print("BUGSY_RESULT=" + json.dumps({"status":status, "phase":"resolution", "failure_category":failure, "environment":env, "resolver_version":actual_version, "exit_code":resolve.returncode, "stdout":resolve.stdout[-6000:], "stderr":resolve.stderr[-6000:], "resolved_requirements":resolved}))
raise SystemExit(resolve.returncode)
'''

_VERIFICATION_SCRIPT_TEMPLATE = r'''import importlib, json, os, platform, subprocess, sys
PIN = "__UV_RESOLVER_VERSION__"
TARGET = os.environ["BUGSY_PYTHON_TARGET"]
IMPORTS = json.loads(os.environ["BUGSY_IMPORT_MODULES"])
LIMIT = 1200
env = {"os": platform.platform(), "python_version": sys.version.split()[0]}
def capture(code, stdout, stderr, reason=None):
    return {"status": "passed" if code == 0 else "failed", "exit_code": code, "stdout": (stdout or "")[-LIMIT:], "stderr": (stderr or "")[-LIMIT:], "reason": reason}
def record(phase, **data):
    result["checks"][phase] = data
def stop(phase, code, stdout, stderr):
    evidence = ((stdout or "") + "\n" + (stderr or "")).lower()
    category = "network_failure" if any(x in evidence for x in ("temporary failure in name resolution", "failed to establish a new connection", "connection timed out", "network is unreachable", "failed to fetch")) else ("resolver_failure" if phase == "resolution" and "no solution found when resolving dependencies" in evidence else "unknown_failure")
    record(phase, **capture(code, stdout, stderr))
    for later in ("installation", "imports"):
        if later not in result["checks"]:
            record(later, status="not_run", reason="An earlier verification check failed.", exit_code=None, stdout="", stderr="")
    result["failure_category"] = category
    result["status"] = "incomplete"
    result["resolver_version"] = actual_version
    result["environment"] = env
    print("BUGSY_VERIFICATION=" + json.dumps(result, separators=(",", ":")))
    raise SystemExit(0)
result = {"checks": {"project_tests": {"status":"not_run", "exit_code":None, "stdout":"", "stderr":"", "reason":"Project code not provided."}}}
setup = subprocess.run([sys.executable, "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "uv==" + PIN], capture_output=True, text=True, timeout=90)
if setup.returncode:
    result.update({"status":"incomplete", "failure_category":"unknown_failure", "environment":env, "resolver_version":None})
    for phase in ("resolution", "installation", "imports"):
        result["checks"][phase] = {"status":"not_run", "exit_code":setup.returncode if phase == "resolution" else None, "stdout":setup.stdout[-LIMIT:] if phase == "resolution" else "", "stderr":setup.stderr[-LIMIT:] if phase == "resolution" else "", "reason":"Pinned resolver setup failed; dependency checks did not run."}
    print("BUGSY_VERIFICATION=" + json.dumps(result, separators=(",", ":")))
    raise SystemExit(0)
version = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=10)
actual_version = version.stdout.strip().split()[1] if version.returncode == 0 and len(version.stdout.strip().split()) > 1 else None
if actual_version != PIN:
    result.update({"status":"incomplete", "failure_category":"unknown_failure", "environment":env, "resolver_version":actual_version})
    for phase in ("resolution", "installation", "imports"):
        result["checks"][phase] = {"status":"not_run", "exit_code":version.returncode, "stdout":version.stdout[-LIMIT:], "stderr":version.stderr[-LIMIT:], "reason":"The pinned resolver version could not be confirmed."}
    print("BUGSY_VERIFICATION=" + json.dumps(result, separators=(",", ":")))
    raise SystemExit(0)
resolution = subprocess.run(["uv", "pip", "compile", "--python-version", TARGET, "--no-header", "--output-file", "/tmp/bugsy-resolved.txt", "/tmp/requirements.txt"], capture_output=True, text=True, timeout=120)
if resolution.returncode:
    stop("resolution", resolution.returncode, resolution.stdout, resolution.stderr)
record("resolution", **capture(resolution.returncode, resolution.stdout, resolution.stderr))
target = TARGET.split(".")
actual = env["python_version"].split(".")
match = target[:len(target)] == actual[:len(target)]
if not match:
    for phase in ("installation", "imports"):
        record(phase, status="not_run", exit_code=None, stdout="", stderr="", reason="Requested Python target differs from the actual Sandbox Python; execution checks were not run for the requested target.")
    result.update({"status":"incomplete", "failure_category":None, "environment":env, "resolver_version":actual_version, "target_matches_sandbox_python":False})
    print("BUGSY_VERIFICATION=" + json.dumps(result, separators=(",", ":")))
    raise SystemExit(0)
install = subprocess.run(["uv", "pip", "install", "--python", sys.executable, "--no-input", "-r", "/tmp/bugsy-resolved.txt"], capture_output=True, text=True, timeout=180)
if install.returncode:
    stop("installation", install.returncode, install.stdout, install.stderr)
record("installation", **capture(install.returncode, install.stdout, install.stderr))
if not IMPORTS:
    record("imports", status="skipped", exit_code=None, stdout="", stderr="", reason="No reliable distribution-to-import mappings are available for the submitted direct requirements.")
else:
    failures = []
    for package, module in IMPORTS:
        try:
            importlib.import_module(module)
        except Exception as exc:
            failures.append({"package":package, "module":module, "error":type(exc).__name__})
    imp_status = "failed" if failures else "passed"
    record("imports", status=imp_status, exit_code=1 if failures else 0, stdout=json.dumps({"checked":IMPORTS, "failures":failures})[-LIMIT:], stderr="", reason="One or more mapped imports failed." if failures else None)
result.update({"status":"complete" if result["checks"]["imports"]["status"] in ("passed", "skipped") else "incomplete", "failure_category":None, "environment":env, "resolver_version":actual_version, "target_matches_sandbox_python":True})
print("BUGSY_VERIFICATION=" + json.dumps(result, separators=(",", ":")))
'''

_INSTALLATION_SCRIPT_TEMPLATE = r'''import json, os, platform, re, subprocess, sys
PIN = "__UV_RESOLVER_VERSION__"
TARGET = os.environ["BUGSY_PYTHON_TARGET"]
LIMIT = 6000
NETWORK_EVIDENCE = ("temporary failure in name resolution", "failed to establish a new connection", "connection timed out", "network is unreachable", "failed to fetch", "connection reset")
env = {"os": platform.platform(), "python_version": sys.version.split()[0]}
def clean(value):
    value = value or ""
    value = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/@\s]+@", r"\1[REDACTED]@", value)
    value = re.sub(r"(?i)\bBearer\s+[^\s'\"]+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API[_-]?KEY|CREDENTIAL)[A-Z0-9_]*\s*[:=]\s*[^,\s]+", "[REDACTED]", value)
    return value[-LIMIT:]
def failure(phase, code, stdout, stderr):
    evidence = (stdout + "\n" + stderr).lower()
    if any(marker in evidence for marker in NETWORK_EVIDENCE): return "network_failure"
    if phase in {"setup", "resolver_version"}: return "sandbox_failure"
    if phase == "installation" and code is not None: return "installation_failure"
    return "unknown_failure"
def emit(status, phase, code, stdout="", stderr="", category=None, limitation=None, uv_version=None):
    print("BUGSY_INSTALLATION=" + json.dumps({"status":status,"phase":phase,"environment":env,"target_python":TARGET,"uv_version":uv_version,"exit_code":code,"stdout":clean(stdout),"stderr":clean(stderr),"failure_category":category,"limitation":limitation}, separators=(",", ":")))
setup = subprocess.run([sys.executable, "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "uv==" + PIN], capture_output=True, text=True, timeout=30)
if setup.returncode:
    emit("failed", "setup", setup.returncode, setup.stdout, setup.stderr, failure("setup", setup.returncode, setup.stdout, setup.stderr))
    raise SystemExit(70)
version = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=5)
actual_version = version.stdout.strip().split()[1] if version.returncode == 0 and len(version.stdout.strip().split()) > 1 else None
if version.returncode or actual_version != PIN:
    emit("failed", "resolver_version", version.returncode or 1, version.stdout, version.stderr, failure("resolver_version", version.returncode or 1, version.stdout, version.stderr), uv_version=actual_version)
    raise SystemExit(71)
target_parts = TARGET.split(".")
actual_parts = env["python_version"].split(".")
if len(actual_parts) < len(target_parts) or actual_parts[:len(target_parts)] != target_parts:
    emit("incomplete", "environment", None, limitation="Requested Python target differs from the actual Sandbox Python; installation was not run.", uv_version=actual_version)
    raise SystemExit(0)
# uv pip install may evaluate dependency constraints as part of installing. This stage does not run a separate resolver/compile command.
try:
    install = subprocess.run(["uv", "pip", "install", "--python", sys.executable, "--no-input", "--disable-pip-version-check", "-r", "/tmp/requirements.txt"], capture_output=True, text=True, timeout=120)
except subprocess.TimeoutExpired as exc:
    emit("unknown", "installation", None, exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""), exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""), "unknown_failure", "The package installation command timed out; its final state is unknown.", actual_version)
    raise SystemExit(72)
category = None if install.returncode == 0 else failure("installation", install.returncode, install.stdout, install.stderr)
emit("passed" if install.returncode == 0 else "failed", "installation", install.returncode, install.stdout, install.stderr, category, uv_version=actual_version)
raise SystemExit(install.returncode)
'''


def _resolver_script() -> str:
    return _RESOLVER_SCRIPT_TEMPLATE.replace("__UV_RESOLVER_VERSION__", UV_RESOLVER_VERSION)


def build_sdk_verification_kwargs(requirements_text: str, python_target: str, import_modules: list[tuple[str, str]]) -> dict[str, Any]:
    """Build fixed application-controlled verification input; requirements are transferred as bytes."""
    if len(requirements_text.encode("utf-8")) > settings.max_requirements_bytes:
        raise ValueError("Requirements input exceeds configured size limit.")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", python_target):
        raise ValueError("Python target must be a validated version number.")
    sandbox_image_for_python_target(python_target)
    if len(requirements_text.splitlines()) > settings.max_requirements_lines:
        raise ValueError("Requirements input exceeds configured line limit.")
    if len(import_modules) > len(VERIFIED_IMPORT_NAMES):
        raise ValueError("Import mapping count exceeds configured limit.")
    if any(VERIFIED_IMPORT_NAMES.get(package) != module for package, module in import_modules):
        raise ValueError("Import mapping contains an invalid application-selected name.")
    return {
        "command": SANDBOX_PYTHON_EXECUTABLE,
        "args": ["-c", _VERIFICATION_SCRIPT_TEMPLATE.replace("__UV_RESOLVER_VERSION__", UV_RESOLVER_VERSION)],
        "env": {"BUGSY_PYTHON_TARGET": python_target, "BUGSY_IMPORT_MODULES": json.dumps(import_modules, separators=(",", ":"))},
        "cwd": "/tmp",
        "files": {"/tmp/requirements.txt": requirements_text.encode("utf-8")},
        "timeout": settings.sandbox_operation_timeout,
        "truncate_output_at": 24000,
    }


def build_sdk_installation_kwargs(requirements_text: str, python_target: str, *, retain_artifact: bool = False) -> dict[str, Any]:
    """Build one fixed install-only operation. uv may resolve while installing, but no compile/import/test command runs."""
    if len(requirements_text.encode("utf-8")) > settings.max_requirements_bytes:
        raise ValueError("Requirements input exceeds configured size limit.")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", python_target):
        raise ValueError("Python target must be a validated version number.")
    sandbox_image_for_python_target(python_target)
    if len(requirements_text.splitlines()) > settings.max_requirements_lines:
        raise ValueError("Requirements input exceeds configured line limit.")
    return {
        "command": SANDBOX_PYTHON_EXECUTABLE,
        "args": ["-c", _INSTALLATION_SCRIPT_TEMPLATE.replace("__UV_RESOLVER_VERSION__", UV_RESOLVER_VERSION)],
        "env": {"BUGSY_PYTHON_TARGET": python_target},
        "cwd": "/tmp",
        "files": {"/tmp/requirements.txt": requirements_text.encode("utf-8")},
        "timeout": settings.sandbox_operation_timeout,
        "truncate_output_at": 24000,
        "disposable": not retain_artifact,
    }


_IMPORT_SCRIPT_TEMPLATE = r'''import importlib, json, os, platform, sys
modules = json.loads(os.environ["BUGSY_IMPORT_MODULES"])
failures = []
for item in modules:
    package, module = item
    try:
        importlib.import_module(module)
    except Exception as exc:
        failures.append({"package": package, "module": module, "error": type(exc).__name__})
result = {"status": "failed" if failures else "passed", "environment": {"os": platform.platform(), "python_version": sys.version.split()[0]}, "checks": modules, "failures": failures, "exit_code": 1 if failures else 0}
print("BUGSY_IMPORTS=" + json.dumps(result, separators=(",", ":")))
raise SystemExit(result["exit_code"])
'''


def build_sdk_import_kwargs(python_target: str, import_modules: list[tuple[str, str]]) -> dict[str, Any]:
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", python_target):
        raise ValueError("Python target must be a validated version number.")
    sandbox_image_for_python_target(python_target)
    if not import_modules or len(import_modules) > len(VERIFIED_IMPORT_NAMES):
        raise ValueError("Import checks require a bounded, non-empty allowlisted package mapping.")
    if any(VERIFIED_IMPORT_NAMES.get(package) != module for package, module in import_modules):
        raise ValueError("Import mapping contains a name outside Bugsy's explicit allowlist.")
    if len(set(package for package, _ in import_modules)) != len(import_modules):
        raise ValueError("Import mapping contains duplicate distributions.")
    return {
        "command": SANDBOX_PYTHON_EXECUTABLE, "args": ["-c", _IMPORT_SCRIPT_TEMPLATE],
        "env": {"BUGSY_IMPORT_MODULES": json.dumps(import_modules, separators=(",", ":"))},
        "cwd": "/tmp", "timeout": settings.sandbox_operation_timeout,
        "truncate_output_at": 24000, "disposable": True,
    }


def build_sdk_run_kwargs(requirements_text: str, python_target: str) -> dict[str, Any]:
    """Build a shell-free, fixed ConTree request using the SDK's file and argv APIs."""
    if len(requirements_text.encode("utf-8")) > settings.max_requirements_bytes:
        raise ValueError("Requirements input exceeds configured size limit.")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", python_target):
        raise ValueError("Python target must be a validated version number.")
    sandbox_image_for_python_target(python_target)
    if len(requirements_text.splitlines()) > settings.max_requirements_lines:
        raise ValueError("Requirements input exceeds configured line limit.")
    return {
        "command": SANDBOX_PYTHON_EXECUTABLE,
        "args": ["-c", _resolver_script()],
        "env": {"BUGSY_PYTHON_TARGET": python_target},
        "cwd": "/tmp",
        "files": {"/tmp/requirements.txt": requirements_text.encode("utf-8")},
        "timeout": settings.sandbox_operation_timeout,
        "truncate_output_at": 24000,
    }


def _text(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")


def _create_contree_client(config):
    """Build the supported public HTTP client with retries limited to one attempt."""
    from contree_client.httpx import ContreeClient
    from contree_client.runtime import RetryPolicy

    return ContreeClient(
        config.nebius_api_key,
        base_url=config.contree_base_url,
        project=config.contree_project_id,
        timeout=30.0,
        retry=RetryPolicy(max_attempts=1, retry_unsafe=False),
    )


def _close_contree_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _operation_status(operation: Any) -> str:
    status = getattr(operation, "status", None)
    return str(getattr(status, "value", status) or "").upper()


def _stream_text(stream: Any) -> str:
    if stream is None:
        return ""
    as_text = getattr(stream, "as_text", None)
    if callable(as_text):
        return _text(as_text())
    value = getattr(stream, "value", stream)
    return _text(value)


def _operation_process_result(operation: Any) -> tuple[str, str, int | None]:
    metadata = getattr(operation, "metadata", None)
    process = getattr(metadata, "result", None)
    if process is None:
        return "", "", None
    stdout = _stream_text(getattr(process, "stdout", None))
    stderr = _stream_text(getattr(process, "stderr", None))
    state = getattr(process, "state", None)
    exit_code = getattr(state, "exit_code", None)
    return stdout, stderr, exit_code if isinstance(exit_code, int) else None


def _operation_image_uuid(operation: Any) -> str | None:
    """Read the documented result UUID, with the documented nested form as fallback."""
    value = getattr(operation, "result_image_uuid", None)
    for candidate in (value, getattr(getattr(operation, "result", None), "image", None)):
        if candidate in (None, Ellipsis) or not isinstance(candidate, str):
            continue
        try:
            return str(UUID(candidate))
        except (ValueError, TypeError, AttributeError):
            continue
    return None


def _execute_fixed_operation(
    client: Any,
    image: str,
    kwargs: dict[str, Any],
    *,
    disposable: bool = True,
    set_phase: Callable[[str], None] | None = None,
    on_submitted: Callable[[str], Any] | None = None,
):
    """Stage fixed input bytes, submit once, then track the returned operation ID."""
    from contree_client import FileSpec

    staged_files = {}
    if kwargs.get("files"):
        if set_phase:
            set_phase("file_staging")
        for remote_path, content in kwargs["files"].items():
            uploaded = client.upload_file(content)
            staged_files[remote_path] = FileSpec(uuid=uploaded.uuid, mode="0644")

    image_arg = image
    try:
        image_arg = str(UUID(str(image)))
    except (ValueError, TypeError, AttributeError):
        if not image.startswith("tag:"):
            image_arg = f"tag:{image}"

    if set_phase:
        set_phase("operation_submission")
    response = client.spawn_instance(
        kwargs["command"], image_arg,
        args=list(kwargs.get("args", [])),
        shell=False,
        env=kwargs.get("env", {}),
        cwd=kwargs.get("cwd", ""),
        timeout=int(kwargs.get("timeout", settings.sandbox_operation_timeout)),
        truncate_output_at=int(kwargs.get("truncate_output_at", 24000)),
        disposable=disposable,
        files=staged_files,
    )
    operation_id = getattr(response, "uuid", None)
    if not isinstance(operation_id, str) or not operation_id:
        raise RuntimeError("CONTREE_SUBMISSION_ID_UNAVAILABLE")
    # Persist immediately after spawn_instance returns and before any polling.
    # If persistence refuses/fails, do not continue as if reconciliation data
    # had been saved; the caller will preserve an unknown outcome and consume
    # the approval without resubmitting.
    if on_submitted is not None and on_submitted(operation_id) is False:
        raise RuntimeError("CONTREE_OPERATION_ID_PERSISTENCE_FAILED")
    if set_phase:
        set_phase("operation_tracking")
    operation = client.wait_operation(operation_id, timeout=kwargs.get("timeout", settings.sandbox_operation_timeout))
    return operation_id, operation


def _checked_retained_image_uuid(client: Any, operation: Any) -> str | None:
    if _operation_status(operation) != "SUCCESS":
        return None
    image_uuid = _operation_image_uuid(operation)
    if image_uuid is None:
        return None
    inspected = client.inspect_image(image_uuid)
    inspected_uuid = getattr(inspected, "uuid", None)
    try:
        if str(UUID(str(inspected_uuid))) != image_uuid:
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    return image_uuid


def _parse_result(
    stdout: str, fallback_code: int | None, python_target: str, *, sandbox_image: str | None = None,
) -> ResolverResult:
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
            failure_phase="result_parsing",
            failure_category="unknown_failure",
            resolver="uv",
            python_target=python_target,
            sandbox_image=_safe_image_identifier(sandbox_image),
            exit_code=fallback_code,
            stdout=stdout[-6000:],
            limitation="ConTree returned no parseable result record. No compatibility claim is available.",
        )
    raw_env = result_data.get("environment")
    env = raw_env if isinstance(raw_env, dict) else {}
    sandbox_python, target_matches = _validated_runtime_match(python_target, env.get("sandbox_python"))
    raw_phase = result_data.get("phase")
    phase = raw_phase if isinstance(raw_phase, str) and raw_phase in _SAFE_RESOLVER_FAILURE_PHASES else "unknown"
    exit_code = result_data.get("exit_code", fallback_code)
    result_stdout = result_data.get("stdout", "")
    result_stderr = result_data.get("stderr", "")
    if not isinstance(result_stdout, str):
        result_stdout = ""
    if not isinstance(result_stderr, str):
        result_stderr = ""
    failure_category = (
        "sandbox_failure" if phase == "python_target_validation"
        else _classify_outcome(phase, exit_code, result_stdout, result_stderr)
    )
    runtime_validation_failed = phase == "resolution" and (
        target_matches is False or (exit_code == 0 and target_matches is None)
    )
    if runtime_validation_failed:
        # Even a zero-exit resolver record is not usable evidence for the requested target
        # unless Bugsy can validate the actual Sandbox interpreter independently.
        failure_category = "sandbox_failure"
        status = "error"
    elif phase == "resolution" and exit_code == 0 and target_matches is True:
        status = "resolved"
    elif failure_category == "resolver_failure":
        status = "unresolved"
    else:
        status = "error"
    limitation = None
    if runtime_validation_failed:
        if target_matches is False:
            limitation = (
                f"ConTree reported Sandbox Python {sandbox_python}, which does not match requested target {python_target}. "
                "The resolver result was rejected; no compatibility claim is available."
            )
        else:
            limitation = (
                f"ConTree did not provide a valid Sandbox Python version for requested target {python_target}. "
                "The resolver result was rejected; no compatibility claim is available."
            )
    elif phase == "python_target_validation":
        limitation = (
            f"Sandbox Python {sandbox_python or 'unknown'} does not match requested target {python_target}; "
            "dependency resolution was not run."
        )
    elif not target_matches:
        limitation = (
            f"Requested Python {python_target} differs from the actual Sandbox Python {sandbox_python or 'unknown'}. "
            "uv was asked to resolve for the requested target, but this run does not verify execution on that Python target."
        )
    return ResolverResult(
        status=status,
        execution_mode="real",
        failure_phase=(
            None if status == "resolved"
            else "python_target_validation" if runtime_validation_failed
            else phase if phase in _SAFE_RESOLVER_FAILURE_PHASES else "result_parsing"
        ),
        failure_category=failure_category,
        resolver="uv",
        resolver_version=result_data.get("resolver_version"),
        python_target=python_target,
        sandbox_os=env.get("sandbox_os"),
        sandbox_python=sandbox_python,
        sandbox_image=_safe_image_identifier(sandbox_image),
        target_matches_sandbox_python=target_matches,
        exit_code=exit_code,
        stdout=result_stdout[-6000:],
        stderr=result_stderr[-6000:],
        resolved_requirements=None if runtime_validation_failed else result_data.get("resolved_requirements"),
        limitation=limitation,
    )


_SAFE_IMAGE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


def _safe_image_identifier(value: Any) -> str | None:
    """Keep only a bounded image tag/UUID-like identifier, never URL credentials or query data."""
    if not isinstance(value, str) or not _SAFE_IMAGE_IDENTIFIER.fullmatch(value):
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return value


class ContreeResolverAdapter:
    def __init__(self, *, config=None):
        self.config = config or settings

    def resolve(self, requirements_text: str, python_target: str) -> ResolverResult:
        app_config = self.config
        try:
            sandbox_image = sandbox_image_for_python_target(python_target)
        except ValueError as exc:
            return ResolverResult(
                status="not_run", execution_mode="not_run", python_target=python_target,
                limitation=str(exc),
            )
        run_kwargs = build_sdk_run_kwargs(requirements_text, python_target)
        if not app_config.sandbox_enabled:
            return ResolverResult(
                status="not_run",
                execution_mode="not_run",
                python_target=python_target,
                limitation="ConTree resolution is disabled. No dependency resolution was performed.",
            )
        if not app_config.nebius_api_key or not app_config.contree_base_url:
            return ResolverResult(
                status="not_run",
                execution_mode="not_run",
                python_target=python_target,
                limitation="ConTree is enabled but required backend credentials are not configured. No resolution was performed.",
            )
        phase = ["client_configuration"]
        client = None
        try:
            client = _create_contree_client(app_config)
            operation_id, operation = _execute_fixed_operation(
                client, sandbox_image, run_kwargs,
                set_phase=lambda value: phase.__setitem__(0, value),
            )
            if _operation_status(operation) != "SUCCESS":
                raise RuntimeError("CONTREE_OPERATION_NOT_SUCCESSFUL")
            stdout, stderr, exit_code = _operation_process_result(operation)
        except Exception as exc:  # Keep credentials and request contents out of diagnostics.
            diagnostics = _safe_api_error_diagnostics(exc)
            diagnostics.update(_safe_api_connection_diagnostics(exc))
            return ResolverResult(
                status="error",
                execution_mode="real",
                failure_phase=phase[0] if phase[0] in _SAFE_RESOLVER_FAILURE_PHASES else "result_parsing",
                failure_category=_classify_sdk_exception(exc),
                **diagnostics,
                resolver="uv",
                python_target=python_target,
                sandbox_image=_safe_image_identifier(sandbox_image),
                limitation=f"ConTree resolution result is unavailable ({type(exc).__name__}) during {phase[0]}. No compatibility claim is available.",
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        return _parse_result(stdout, exit_code, python_target, sandbox_image=sandbox_image)


class ContreeVerificationAdapter:
    def verify(self, requirements_text: str, python_target: str, import_modules: list[tuple[str, str]]) -> dict[str, Any]:
        sandbox_image = sandbox_image_for_python_target(python_target)
        if not settings.sandbox_enabled:
            raise RuntimeError("SANDBOX_DISABLED")
        if not settings.nebius_api_key or not settings.contree_base_url:
            raise RuntimeError("SANDBOX_NOT_CONFIGURED")
        client = None
        phase = ["client_configuration"]
        try:
            client = _create_contree_client(settings)
            operation_id, operation = _execute_fixed_operation(
                client,
                sandbox_image,
                build_sdk_verification_kwargs(requirements_text, python_target, import_modules),
                set_phase=lambda value: phase.__setitem__(0, value),
            )
            if _operation_status(operation) != "SUCCESS":
                raise RuntimeError("CONTREE_OPERATION_NOT_SUCCESSFUL")
            stdout, stderr, exit_code = _operation_process_result(operation)
        except Exception as exc:
            diagnostics = _safe_sdk_failure_diagnostics(exc, phase[0])
            raise RuntimeError(f"CONTREE_{_classify_sdk_exception(exc).upper()}:{diagnostics['failure_phase']}:{type(exc).__name__}") from None
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        data = next((json.loads(line.removeprefix("BUGSY_VERIFICATION=")) for line in reversed(stdout.splitlines()) if line.startswith("BUGSY_VERIFICATION=")), None)
        if not isinstance(data, dict):
            raise RuntimeError("CONTREE_RESULT_UNAVAILABLE")
        return data


def _parse_installation_result(stdout: str, fallback_code: int | None, python_target: str) -> dict[str, Any]:
    data = None
    for line in reversed(stdout.splitlines()):
        if line.startswith("BUGSY_INSTALLATION="):
            try:
                data = json.loads(line.removeprefix("BUGSY_INSTALLATION="))
            except json.JSONDecodeError:
                break
            break
    if not isinstance(data, dict):
        return {
            "status": "unknown", "phase": "unknown", "failure_category": "unknown_failure",
            "environment": {}, "target_python": python_target, "uv_version": None,
            "exit_code": fallback_code, "stdout": stdout[-6000:], "stderr": "",
            "limitation": "ConTree returned no parseable installation result; outcome is unknown.",
        }
    environment = data.get("environment") if isinstance(data.get("environment"), dict) else {}
    actual_python = environment.get("python_version")
    target_parts = python_target.split(".")
    actual_parts = actual_python.split(".") if isinstance(actual_python, str) else []
    data["environment"]["target_matches_sandbox_python"] = (
        len(actual_parts) >= len(target_parts) and actual_parts[:len(target_parts)] == target_parts
    )
    if data.get("status") not in {"passed", "failed", "incomplete", "unknown"}:
        data["status"] = "unknown"
    if data.get("status") == "passed" and not data["environment"]["target_matches_sandbox_python"]:
        data["status"] = "incomplete"
        data["limitation"] = "Reported Sandbox Python does not match the requested Python target."
    for key in ("stdout", "stderr"):
        data[key] = data.get(key, "")[-6000:] if isinstance(data.get(key), str) else ""
    data["exit_code"] = data.get("exit_code") if isinstance(data.get("exit_code"), int) else fallback_code
    data["target_python"] = python_target
    return data


class ContreeInstallationAdapter:
    """Runs the fixed installation-only script inside ConTree."""

    def __init__(self, *, config=None):
        self.config = config or settings

    def install(
        self, requirements_text: str, python_target: str, *, retain_artifact: bool = False,
        on_operation_submitted: Callable[[str], Any] | None = None,
    ) -> dict[str, Any]:
        app_config = self.config
        sandbox_image = sandbox_image_for_python_target(python_target)
        if not app_config.sandbox_enabled:
            raise RuntimeError("SANDBOX_DISABLED")
        if not app_config.nebius_api_key or not app_config.contree_base_url:
            raise RuntimeError("SANDBOX_NOT_CONFIGURED")
        phase = ["client_configuration"]
        client = None
        try:
            client = _create_contree_client(app_config)
            operation_id, operation = _execute_fixed_operation(
                client,
                sandbox_image,
                build_sdk_installation_kwargs(requirements_text, python_target, retain_artifact=retain_artifact),
                disposable=not retain_artifact,
                set_phase=lambda value: phase.__setitem__(0, value),
                on_submitted=on_operation_submitted,
            )
        except Exception as exc:
            diagnostics = _safe_sdk_failure_diagnostics(exc, phase[0])
            _close_contree_client(client)
            return {
                "status": "unknown", "phase": phase[0], **diagnostics,
                "failure_category": _classify_sdk_exception(exc),
                "environment": {}, "target_python": python_target, "uv_version": None,
                "exit_code": None, "stdout": "", "stderr": "",
                "limitation": f"ConTree installation result is unavailable ({type(exc).__name__}); it will not be retried automatically.",
            }
        operation_status = _operation_status(operation)
        if operation_status != "SUCCESS":
            _close_contree_client(client)
            return {
                "status": "failed" if operation_status in {"FAILED", "CANCELLED"} else "unknown",
                "phase": "operation_tracking", "failure_phase": "operation_tracking",
                "failure_detail": "ConTree returned a non-success terminal operation status; no installation success is claimed.",
                "failure_category": "sandbox_failure" if operation_status in {"FAILED", "CANCELLED"} else "unknown_failure",
                "environment": {}, "target_python": python_target, "uv_version": None,
                "exit_code": None, "stdout": "", "stderr": "",
                "limitation": "The fixed installation operation did not return a successful structured result.",
            }
        result_stdout, result_stderr, sdk_exit_code = _operation_process_result(operation)
        parsed = _parse_installation_result(result_stdout, sdk_exit_code, python_target)
        parsed["stderr"] = result_stderr[-6000:]
        if parsed.get("exit_code") != sdk_exit_code:
            parsed["status"] = "unknown"
            parsed["failure_category"] = "unknown_failure"
            parsed["limitation"] = "Structured installation status and Sandbox exit status disagree; outcome is unknown."
            parsed.pop("sandbox_image_ref", None)
        parsed["exit_code"] = sdk_exit_code
        if retain_artifact and parsed.get("status") == "passed" and sdk_exit_code == 0 and parsed.get("environment", {}).get("target_matches_sandbox_python") is True:
            try:
                phase[0] = "image_validation"
                parsed["sandbox_image_ref"] = _checked_retained_image_uuid(client, operation)
                if parsed["sandbox_image_ref"] is None:
                    parsed["limitation"] = "Installation passed, but ConTree did not return a valid inspectable retained image UUID; imports remain unavailable."
            except Exception as exc:
                diagnostics = _safe_sdk_failure_diagnostics(exc, "image_validation")
                parsed["sandbox_image_ref"] = None
                parsed["failure_phase"] = diagnostics["failure_phase"]
                parsed["failure_detail"] = diagnostics["failure_detail"]
                parsed["limitation"] = "Installation passed, but retained image validation failed; imports remain unavailable."
        _close_contree_client(client)
        return parsed


class ContreeImportAdapter:
    """Runs only fixed allowlisted import checks against a retained install image."""

    def __init__(self, *, config=None):
        self.config = config or settings

    def verify(self, image_ref: str, python_target: str, import_modules: list[tuple[str, str]]) -> dict[str, Any]:
        config = self.config
        if not config.sandbox_enabled:
            raise RuntimeError("SANDBOX_DISABLED")
        if not config.nebius_api_key or not config.contree_base_url:
            raise RuntimeError("SANDBOX_NOT_CONFIGURED")
        try:
            image_uuid = UUID(image_ref)
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("CONTREE_IMAGE_REFERENCE_INVALID") from None
        phase = ["client_configuration"]
        client = None
        try:
            client = _create_contree_client(config)
            phase[0] = "image_validation"
            inspected = client.inspect_image(str(image_uuid))
            if str(UUID(str(getattr(inspected, "uuid", "")))) != str(image_uuid):
                raise RuntimeError("CONTREE_IMAGE_REFERENCE_INVALID")
            operation_id, operation = _execute_fixed_operation(
                client, str(image_uuid), build_sdk_import_kwargs(python_target, import_modules), disposable=True,
                set_phase=lambda value: phase.__setitem__(0, value),
            )
        except Exception as exc:
            diagnostics = _safe_sdk_failure_diagnostics(exc, phase[0])
            return {"status": "unknown", **diagnostics, "failure_category": _classify_sdk_exception(exc), "environment": {}, "exit_code": None, "stdout": "", "stderr": "", "limitation": f"ConTree import result is unavailable ({type(exc).__name__}); it will not be retried automatically."}
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if _operation_status(operation) != "SUCCESS":
            status = _operation_status(operation)
            failed = status in {"FAILED", "CANCELLED"}
            return {"status": "failed" if failed else "unknown", "failure_phase": "operation_tracking", "failure_detail": "ConTree returned a terminal failed operation status." if failed else "ConTree returned no successful structured import result.", "failure_category": "sandbox_failure" if failed else "unknown_failure", "environment": {}, "exit_code": None, "stdout": "", "stderr": "", "limitation": "Import verification result is unavailable."}
        stdout, result_stderr, result_exit_code = _operation_process_result(operation)
        data = next((json.loads(line.removeprefix("BUGSY_IMPORTS=")) for line in reversed(stdout.splitlines()) if line.startswith("BUGSY_IMPORTS=")), None)
        if not isinstance(data, dict) or data.get("status") not in {"passed", "failed"}:
            return {"status": "unknown", "environment": {}, "exit_code": result_exit_code, "stdout": stdout[-6000:], "stderr": result_stderr[-6000:], "failure_category": "unknown_failure", "limitation": "ConTree returned no structured import result; outcome is unknown."}
        target_parts = python_target.split(".")
        actual_parts = str(data.get("environment", {}).get("python_version", "")).split(".")
        if data["status"] == "passed" and (len(actual_parts) < len(target_parts) or actual_parts[:len(target_parts)] != target_parts):
            data["status"] = "incomplete"
            data["limitation"] = "Sandbox Python did not match the requested target. Import result does not establish the requested target."
        data["exit_code"] = result_exit_code if isinstance(result_exit_code, int) else data.get("exit_code")
        data["stdout"] = stdout[-6000:]
        data["stderr"] = result_stderr[-6000:]
        if data["status"] == "failed":
            data["failure_category"] = "import_failure"
        return data
