"""Backend environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _read_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Values read by the API without exposing secrets to the browser."""

    sandbox_enabled: bool = _read_bool("SANDBOX_ENABLED", False)
    sandbox_image: str = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
    max_requirements_bytes: int = 65536
    max_requirements_lines: int = 500
    max_requirement_count: int = 100
    sandbox_operation_timeout: int = 180
    contree_base_url: str | None = os.getenv("CONTREE_BASE_URL")
    nebius_api_key: str | None = os.getenv("NEBIUS_API_KEY")
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if origin.strip()
    )


settings = Settings()
