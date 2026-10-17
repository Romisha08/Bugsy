from __future__ import annotations

from threading import Lock

from app.domain.evidence import RequirementSource
from app.schemas.analysis import AnalysisResponse

_sources: dict[str, RequirementSource] = {}
_analyses: dict[str, AnalysisResponse] = {}
_lock = Lock()


def save_source(source: RequirementSource) -> None:
    with _lock:
        _sources[source.source_id] = source


def get_source(source_id: str) -> RequirementSource | None:
    with _lock:
        return _sources.get(source_id)


def save_analysis(analysis: AnalysisResponse) -> None:
    with _lock:
        _analyses[analysis.analysis_id] = analysis


def get_analysis(analysis_id: str) -> AnalysisResponse | None:
    with _lock:
        return _analyses.get(analysis_id)


def update_analysis(analysis_id: str, **updates) -> AnalysisResponse | None:
    with _lock:
        existing = _analyses.get(analysis_id)
        if existing is None:
            return None
        updated = existing.model_copy(update=updates)
        _analyses[analysis_id] = updated
        return updated


def clear_workflow_state() -> None:
    """Test helper; app data is otherwise process-local and short-lived."""
    with _lock:
        _sources.clear()
        _analyses.clear()
