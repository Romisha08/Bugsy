from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from app.config import settings

_PYTHON_TARGET = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class ParseIssue:
    line: int
    original: str
    reason: str


@dataclass(frozen=True)
class ParseResult:
    requirements: tuple[dict[str, object], ...]
    issues: tuple[ParseIssue, ...]
    source_sha256: str


class RequirementsInputError(ValueError):
    def __init__(self, code: str, message: str, details: list[dict[str, object]] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


def validate_python_version(value: str) -> str:
    normalized = value.strip()
    if not _PYTHON_TARGET.fullmatch(normalized):
        raise RequirementsInputError(
            "INVALID_PYTHON_VERSION",
            "Choose Python 3.10, 3.11, 3.12, 3.13, or 3.14.",
            [{"field": "python_version", "reason": "Choose one of the supported major.minor versions."}],
        )
    if normalized not in {"3.10", "3.11", "3.12", "3.13", "3.14"}:
        raise RequirementsInputError(
            "UNSUPPORTED_PYTHON_VERSION",
            "Choose Python 3.10, 3.11, 3.12, 3.13, or 3.14.",
            [{"field": "python_version", "reason": "Only Python 3.10 through 3.14 are supported."}],
        )
    return normalized


def parse_requirements(text: str) -> ParseResult:
    encoded = text.encode("utf-8")
    if len(encoded) > settings.max_requirements_bytes:
        raise RequirementsInputError(
            "REQUIREMENTS_TOO_LARGE",
            f"The requirements file exceeds the {settings.max_requirements_bytes // 1024} KiB limit.",
        )
    lines = text.splitlines()
    if len(lines) > settings.max_requirements_lines:
        raise RequirementsInputError(
            "TOO_MANY_LINES",
            f"The requirements file exceeds the {settings.max_requirements_lines} line limit.",
        )

    parsed: list[dict[str, object]] = []
    issues: list[ParseIssue] = []
    for line_number, raw in enumerate(lines, start=1):
        original = raw.rstrip("\r")
        stripped = original.strip().removeprefix("\ufeff")
        if not stripped or stripped.startswith("#"):
            continue
        if re.search(r"\s--(?:hash|global-option|install-option)(?:=|\s)", stripped):
            issues.append(ParseIssue(line_number, original, "Per-requirement installation options such as --hash are not supported in this MVP."))
            continue
        if stripped.startswith(("-", "\\")) or stripped.startswith(("git+", "hg+", "svn+", "bzr+")):
            issues.append(ParseIssue(
                line_number,
                original,
                "Requirement-file options, includes, editable/local paths, and VCS references are not supported. Use direct package requirements only.",
            ))
            continue
        try:
            requirement = Requirement(stripped)
        except InvalidRequirement as exc:
            line_content = re.sub(r"\s+#.*$", "", stripped).rstrip()
            if line_content and line_content != stripped:
                try:
                    requirement = Requirement(line_content)
                except InvalidRequirement:
                    issues.append(ParseIssue(line_number, original, f"This line is not a supported PEP 508 requirement: {exc}"))
                    continue
            else:
                issues.append(ParseIssue(line_number, original, f"This line is not a supported PEP 508 requirement: {exc}"))
                continue
        if requirement.url:
            issues.append(ParseIssue(
                line_number,
                original,
                "Direct URL, local, and VCS package references are not supported. Use a package name with a version constraint.",
            ))
            continue
        parsed.append({
            "line": line_number,
            "original": original,
            "name": canonicalize_name(requirement.name),
            "specifier": str(requirement.specifier),
            "extras": sorted(requirement.extras),
            "marker": str(requirement.marker) if requirement.marker else None,
        })

    if not parsed and not issues:
        raise RequirementsInputError(
            "EMPTY_REQUIREMENTS",
            "Add at least one supported package requirement before parsing.",
        )
    if len(parsed) > settings.max_requirement_count:
        raise RequirementsInputError(
            "TOO_MANY_REQUIREMENTS",
            f"The file contains more than the {settings.max_requirement_count} direct requirement limit.",
        )

    return ParseResult(tuple(parsed), tuple(issues), hashlib.sha256(encoded).hexdigest())
