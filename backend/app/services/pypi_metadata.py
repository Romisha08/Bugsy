from __future__ import annotations

from urllib.parse import quote

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from app.schemas.analysis import MetadataResult, ReleaseCandidate, ReleaseCandidateSet

PYPI_JSON_ROOT = "https://pypi.org/pypi"
PYPI_REQUEST_TIMEOUT_SECONDS = 8.0
PYPI_CONNECT_TIMEOUT_SECONDS = 3.0


def _get_with_single_timeout_retry(client: httpx.Client, url: str) -> httpx.Response:
    """Retry one idempotent PyPI GET once, and only when the first attempt times out."""
    try:
        return client.get(url)
    except httpx.TimeoutException:
        return client.get(url)


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
    timeout = httpx.Timeout(PYPI_REQUEST_TIMEOUT_SECONDS, connect=PYPI_CONNECT_TIMEOUT_SECONDS)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"}) as client:
            project_response = _get_with_single_timeout_retry(client, project_url)
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
                release_response = _get_with_single_timeout_retry(client, release_url)
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
    except httpx.TimeoutException:
        return MetadataResult(
            package=project_name,
            status="error",
            source_url=project_url,
            error_code="PYPI_TIMEOUT",
            error_message="The PyPI metadata request exceeded Bugsy's configured request timeout.",
        )
    except httpx.NetworkError:
        return MetadataResult(
            package=project_name,
            status="error",
            source_url=project_url,
            error_code="PYPI_UNAVAILABLE",
            error_message="Bugsy could not communicate with PyPI because a network error interrupted the request.",
        )
    except (ValueError, TypeError, KeyError):
        return MetadataResult(
            package=project_name,
            status="error",
            source_url=project_url,
            error_code="INVALID_PYPI_RESPONSE",
            error_message="PyPI returned metadata that Bugsy could not interpret.",
        )


def fetch_release_candidates(name: str, python_target: str, limit: int = 3) -> ReleaseCandidateSet:
    """Return a small list of stable PyPI releases whose metadata supports the target Python."""
    project_name = canonicalize_name(name)
    project_url = f"{PYPI_JSON_ROOT}/{quote(project_name, safe='')}/json"
    timeout = httpx.Timeout(PYPI_REQUEST_TIMEOUT_SECONDS, connect=PYPI_CONNECT_TIMEOUT_SECONDS)
    if not 1 <= limit <= 3:
        raise ValueError("Candidate limit must be between one and three.")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"}) as client:
            response = _get_with_single_timeout_retry(client, project_url)
            if response.status_code == 404:
                return ReleaseCandidateSet(package=project_name, status="unavailable", error_code="PACKAGE_NOT_FOUND")
            response.raise_for_status()
            project = response.json()
            versions: list[Version] = []
            for raw in (project.get("releases") or {}):
                try:
                    version = Version(raw)
                except InvalidVersion:
                    continue
                if not version.is_prerelease and not version.is_devrelease:
                    versions.append(version)
            candidates: list[ReleaseCandidate] = []
            for version in sorted(versions, reverse=True):
                version_text = str(version)
                release_url = f"{PYPI_JSON_ROOT}/{quote(project_name, safe='')}/{quote(version_text, safe='')}/json"
                release_response = _get_with_single_timeout_retry(client, release_url)
                if release_response.status_code == 404:
                    continue
                release_response.raise_for_status()
                info = (release_response.json().get("info") or {})
                requires_python = info.get("requires_python")
                if requires_python:
                    try:
                        if Version(python_target) not in SpecifierSet(requires_python):
                            continue
                    except (InvalidSpecifier, InvalidVersion):
                        continue
                candidates.append(ReleaseCandidate(
                    candidate_id="", package=project_name, version=version_text,
                    source_url=release_url, requires_python=requires_python,
                    requires_dist=info.get("requires_dist") or [],
                    yanked=any(bool(item.get("yanked")) for item in (project.get("releases") or {}).get(version_text, [])),
                ))
                if len(candidates) >= limit:
                    break
            if not candidates:
                return ReleaseCandidateSet(package=project_name, status="unavailable", error_code="NO_PYTHON_COMPATIBLE_RELEASES")
            return ReleaseCandidateSet(package=project_name, status="available", candidates=candidates)
    except httpx.HTTPStatusError as exc:
        return ReleaseCandidateSet(package=project_name, status="error", error_code=f"PYPI_HTTP_{exc.response.status_code}")
    except httpx.TimeoutException:
        return ReleaseCandidateSet(package=project_name, status="error", error_code="PYPI_TIMEOUT")
    except httpx.NetworkError:
        return ReleaseCandidateSet(package=project_name, status="error", error_code="PYPI_UNAVAILABLE")
    except (ValueError, TypeError, KeyError):
        return ReleaseCandidateSet(package=project_name, status="error", error_code="INVALID_PYPI_RESPONSE")
