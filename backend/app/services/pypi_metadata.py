from __future__ import annotations

from urllib.parse import quote

import httpx
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from app.schemas.analysis import MetadataResult

PYPI_JSON_ROOT = "https://pypi.org/pypi"


def _candidate_version(project: dict, specifier_text: str) -> str | None:
    info = project.get("info") or {}
    latest = info.get("version")
    specifier = SpecifierSet(specifier_text)
    if latest:
        try:
            if specifier.contains(latest, prereleases=False):
                return latest
        except InvalidVersion:
            pass
    candidates: list[Version] = []
    for raw_version in (project.get("releases") or {}):
        try:
            version = Version(raw_version)
        except InvalidVersion:
            continue
        if version.is_prerelease or not specifier.contains(version, prereleases=False):
            continue
        candidates.append(version)
    return str(max(candidates)) if candidates else None


def fetch_package_metadata(name: str, specifier_text: str) -> MetadataResult:
    project_name = canonicalize_name(name)
    project_url = f"{PYPI_JSON_ROOT}/{quote(project_name, safe='')}/json"
    timeout = httpx.Timeout(8.0, connect=3.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"}) as client:
            project_response = client.get(project_url)
            if project_response.status_code == 404:
                return MetadataResult(
                    package=project_name,
                    status="unavailable",
                    source_url=project_url,
                    error_code="PACKAGE_NOT_FOUND",
                    error_message="PyPI has no project with this name.",
                )
            project_response.raise_for_status()
            project = project_response.json()
            selected = _candidate_version(project, specifier_text)
            latest_info = project.get("info") or {}
            latest = latest_info.get("version")
            if selected:
                release_url = f"{PYPI_JSON_ROOT}/{quote(project_name, safe='')}/{quote(selected, safe='')}/json"
                release_response = client.get(release_url)
                if release_response.status_code == 404:
                    raise httpx.HTTPStatusError("The selected PyPI release metadata was not found.", request=release_response.request, response=release_response)
                release_response.raise_for_status()
                release_info = release_response.json().get("info") or {}
                return MetadataResult(
                    package=project_name,
                    status="available",
                    source_url=release_url,
                    release=selected,
                    latest_version=latest,
                    requires_python=release_info.get("requires_python"),
                    requires_dist=release_info.get("requires_dist") or [],
                )
            return MetadataResult(
                package=project_name,
                status="available",
                source_url=project_url,
                latest_version=latest,
                requires_python=latest_info.get("requires_python"),
                requires_dist=latest_info.get("requires_dist") or [],
                error_code="NO_RELEASE_MATCHES_SPECIFIER",
                error_message="PyPI has no stable release matching this version constraint. This metadata result is not a resolver result.",
            )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        return MetadataResult(
            package=project_name,
            status="error",
            source_url=project_url,
            error_code="PYPI_HTTP_ERROR",
            error_message=f"PyPI returned HTTP {status} while metadata was retrieved.",
        )
    except (httpx.TimeoutException, httpx.NetworkError):
        return MetadataResult(
            package=project_name,
            status="error",
            source_url=project_url,
            error_code="PYPI_UNAVAILABLE",
            error_message="PyPI could not be reached before the metadata request timed out.",
        )
    except (ValueError, TypeError, KeyError):
        return MetadataResult(
            package=project_name,
            status="error",
            source_url=project_url,
            error_code="INVALID_PYPI_RESPONSE",
            error_message="PyPI returned metadata that Bugsy could not interpret.",
        )
