from __future__ import annotations

from typing import Protocol

from app.schemas.analysis import ResolverResult


class ResolverAdapter(Protocol):
    def resolve(self, requirements_text: str, python_target: str) -> ResolverResult: ...
