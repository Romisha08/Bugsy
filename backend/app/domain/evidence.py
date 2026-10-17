from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequirementSource:
    source_id: str
    original_text: str
    source_sha256: str
    python_version: str
    requirements: tuple[dict[str, object], ...]
