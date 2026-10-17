
"""Run a harmless, explicitly enabled ConTree environment smoke check."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


def _stop(message: str, exit_code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(exit_code)


if os.getenv("SANDBOX_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
    _stop("ConTree smoke test not run: set SANDBOX_ENABLED=true to enable the remote call.")

api_key = os.getenv("NEBIUS_API_KEY")
base_url = os.getenv("CONTREE_BASE_URL")
missing = [name for name, value in (("NEBIUS_API_KEY", api_key), ("CONTREE_BASE_URL", base_url)) if not value]
if missing:
    _stop(f"ConTree smoke test not run: missing configuration: {', '.join(missing)}.")

try:
    from contree_sdk import ContreeSync
    from contree_sdk.auth import IAMAuth
    from contree_sdk.config import ContreeConfig
except ImportError as exc:
    _stop(f"ConTree smoke test could not start: {type(exc).__name__}: {exc}")

config = ContreeConfig(
    auth=IAMAuth(token=api_key, base_url=base_url),
    transport_timeout=30.0,
)

# Fixed, harmless program: it only reports the remote runtime identity.
smoke_command = (
    'python -c "import platform,sys; '
    "print('BUGSY_SANDBOX_SMOKE_OK'); "
    "print(platform.platform()); "
    'print(sys.version.split()[0])"'
)

try:
    image_name = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
    result = (
        ContreeSync(config)
        .images.use(image_name)
        .run(shell=smoke_command, timeout=30)
        .wait()
    )
except Exception as exc:  # Report SDK/network failures without leaking credentials.
    details = str(exc)
    if api_key:
        details = details.replace(api_key, "[REDACTED]")
    details = re.sub(r"(?i)(bearer\s+)[^\s'\"]+", r"\1[REDACTED]", details)
    _stop(f"ConTree smoke test failed: {type(exc).__name__}: {details}")

stdout = result.stdout or ""
stderr = result.stderr or ""
exit_code = result.exit_code
print(f"Sandbox exit code: {exit_code}")
if stdout.strip():
    print(f"Sandbox output: {stdout.strip()}")
if stderr.strip():
    print(f"Sandbox stderr: {stderr.strip()}")

if exit_code != 0:
    _stop("ConTree smoke test failed: the remote command returned a non-zero exit code.")

try:
    marker, sandbox_os, sandbox_python = stdout.strip().splitlines()[-3:]
except (ValueError, TypeError) as exc:
    _stop(f"ConTree smoke test failed: could not parse remote runtime details ({type(exc).__name__}).")

if marker != "BUGSY_SANDBOX_SMOKE_OK":
    _stop("ConTree smoke test failed: the remote success marker did not match.")

print("ConTree harmless command succeeded.")
print(f"Sandbox marker: {marker}")
print(f"Sandbox OS: {sandbox_os}")
print(f"Sandbox Python: {sandbox_python}")
print("This confirms remote command execution only; no dependency compatibility check was performed.")
