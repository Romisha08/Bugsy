from __future__ import annotations

from app.schemas.analysis import ResolverResult


class StubResolverAdapter:
    """Explicit test/UI stub. Results from this adapter are never real evidence."""

    def __init__(self, status: str = "resolved"):
        if status not in {"resolved", "unresolved", "error"}:
            raise ValueError("Stub status must be resolved, unresolved, or error.")
        self.status = status

    def resolve(self, requirements_text: str, python_target: str) -> ResolverResult:
        del requirements_text
        return ResolverResult(
            status=self.status,
            execution_mode="simulated",
            resolver="uv",
            resolver_version="simulated",
            python_target=python_target,
            stdout=f"SIMULATED resolver outcome: {self.status}",
            limitation="Simulated test result only. It is not a real dependency resolution and cannot support a compatibility claim.",
        )
