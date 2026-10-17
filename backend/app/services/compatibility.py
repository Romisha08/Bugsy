from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.adapters.contree import ContreeResolverAdapter
from app.schemas.analysis import MetadataResult, ResolverResult
from app.services.pypi_metadata import fetch_package_metadata


def inspect_metadata(requirements: tuple[dict[str, object], ...]) -> list[MetadataResult]:
    unique: dict[str, str] = {}
    for item in requirements:
        name = str(item["name"])
        unique.setdefault(name, str(item["specifier"]))
    ordered = list(unique.items())
    if not ordered:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(ordered))) as pool:
        futures = [pool.submit(fetch_package_metadata, name, specifier) for name, specifier in ordered]
        return [future.result() for future in futures]


def analyze_requirements(
    requirements_text: str,
    requirements: tuple[dict[str, object], ...],
    python_target: str,
) -> tuple[list[MetadataResult], ResolverResult]:
    metadata = inspect_metadata(requirements)
    resolver = ContreeResolverAdapter().resolve(requirements_text, python_target)
    notes = ["Resolver-only check: submitted packages were not installed and import checks were not run."]
    if resolver.limitation:
        notes.append(resolver.limitation)
    resolver = resolver.model_copy(update={"limitation": " ".join(notes)})
    return metadata, resolver
