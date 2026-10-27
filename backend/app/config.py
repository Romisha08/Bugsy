"""Backend environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)
DEFAULT_NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# Keep image selection tied to the requested interpreter instead of allowing a
# process-wide image override. Provider availability for these tags is unverified
# until separately approved live tests confirm each image's actual interpreter.
SANDBOX_IMAGES_BY_PYTHON_TARGET = {
    "3.10": "python:3.10-slim",
    "3.11": "python:3.11-slim",
    "3.12": "python:3.12-slim",
    "3.13": "python:3.13-slim",
    "3.14": "python:3.14-slim",
}


def sandbox_image_for_python_target(python_target: str) -> str:
    """Return the approved Sandbox image for a supported Python target."""
    if not isinstance(python_target, str) or not re.fullmatch(r"3\.(?:10|11|12|13|14)", python_target):
        raise ValueError("Choose one of the supported Python targets: 3.10, 3.11, 3.12, 3.13, or 3.14.")
    image = SANDBOX_IMAGES_BY_PYTHON_TARGET.get(python_target)
    if image is None:
        raise ValueError("Choose one of the supported Python targets: 3.10, 3.11, 3.12, 3.13, or 3.14.")
    return image


def _read_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _database_path() -> Path:
    configured = os.getenv("BUGSY_DATABASE_PATH")
    if configured and configured.strip():
        return Path(configured.strip()).expanduser()
    return Path(tempfile.gettempdir()) / "bugsy.sqlite3"


def parse_cors_origins(value: str | None) -> tuple[str, ...]:
    """Parse a comma-separated allowlist of HTTP(S) origins safely.

    Whitespace and an optional trailing slash are normalized. Entries with a
    path, credentials, query, fragment, invalid port, or non-HTTP scheme are
    discarded instead of broadening the allowlist.
    """
    origins: list[str] = []
    for raw_origin in (value or "").split(","):
        candidate = raw_origin.strip()
        if not candidate or any(character.isspace() for character in candidate):
            continue
        try:
            parsed = urlsplit(candidate)
            port = parsed.port  # Access validates malformed/out-of-range ports.
        except ValueError:
            continue
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            continue
        # Normalize only scheme/host casing and the optional slash; retain a
        # valid explicit port exactly as parsed.
        host = parsed.hostname.lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        normalized = f"{parsed.scheme.lower()}://{host}"
        if port is not None:
            normalized += f":{port}"
        if normalized not in origins:
            origins.append(normalized)
    return tuple(origins)


@dataclass(frozen=True)
class Settings:
    """Values read by the API without exposing secrets to the browser."""

    sandbox_enabled: bool = _read_bool("SANDBOX_ENABLED", False)
    max_requirements_bytes: int = 65536
    max_requirements_lines: int = 500
    max_requirement_count: int = 100
    sandbox_operation_timeout: int = 180
    # Retaining a successful install image enables a later import-only run.
    # It is opt-in because artifact cost/lifetime are account-specific and unknown here.
    sandbox_retain_installation_artifact: bool = _read_bool("SANDBOX_RETAIN_INSTALLATION_ARTIFACT", False)
    contree_base_url: str | None = os.getenv("CONTREE_BASE_URL")
    contree_project_id: str | None = os.getenv("CONTREE_PROJECT_ID")
    nebius_api_key: str | None = os.getenv("NEBIUS_API_KEY")
    token_factory_base_url: str = field(default_factory=lambda: os.getenv("NEBIUS_BASE_URL", DEFAULT_NEBIUS_BASE_URL))
    token_factory_model_id: str = os.getenv(
        "NEBIUS_MODEL_ID", "nvidia/nemotron-3-super-120b-a12b"
    )
    token_factory_timeout_seconds: float = 25.0
    token_factory_enabled: bool = _read_bool("TOKEN_FACTORY_ENABLED", False)
    cors_origins: tuple[str, ...] = field(default_factory=lambda: parse_cors_origins(
        os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    ))
    database_path: Path = field(default_factory=_database_path)


settings = Settings()
