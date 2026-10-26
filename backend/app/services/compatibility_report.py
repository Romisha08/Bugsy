from __future__ import annotations

import re
from collections.abc import Sequence

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from app.schemas.analysis import (
    CompatibilityEvidence,
    CompatibilityFinding,
    CompatibilityReport,
    MetadataResult,
    ResolverResult,
)


def _value(item: dict | object, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def build_compatibility_report(
    requirements: Sequence[dict | object],
    python_target: str,
    metadata: Sequence[MetadataResult],
    resolver: ResolverResult,
) -> CompatibilityReport:
    """Create a deterministic summary from observed metadata and resolver output."""
    findings: list[CompatibilityFinding] = []
    by_name = {canonicalize_name(item.package): item for item in metadata}

    for requirement in requirements:
        name = str(_value(requirement, "name", ""))
        item = by_name.get(canonicalize_name(name))
        if item is None:
            findings.append(CompatibilityFinding(
                classification="incomplete", code="METADATA_MISSING", title=f"Metadata missing for {name}",
                explanation="No PyPI metadata result was returned for this parsed requirement.", packages=[name],
            ))
            continue
        if item.status == "error":
            findings.append(CompatibilityFinding(
                classification="incomplete", code=item.error_code or "METADATA_ERROR", title=f"Metadata could not be checked for {name}",
                explanation=item.error_message or "The PyPI metadata request failed; this is not evidence of a dependency conflict.", packages=[name],
                evidence=[CompatibilityEvidence(source="pypi", label="Metadata request", value=item.error_message or item.error_code or "Request failed", package=name, source_url=item.source_url)],
            ))
            continue
        if item.status == "unavailable":
            findings.append(CompatibilityFinding(
                classification="unknown", code=item.error_code or "PACKAGE_UNAVAILABLE", title=f"PyPI metadata unavailable for {name}",
                explanation="Bugsy could not confirm package metadata. This does not by itself prove a dependency conflict.", packages=[name],
                evidence=[CompatibilityEvidence(source="pypi", label="Metadata status", value=item.error_message or "Package was unavailable", package=name, source_url=item.source_url)],
            ))
            continue
        if item.error_code == "NO_RELEASE_MATCHES_SPECIFIER":
            findings.append(CompatibilityFinding(
                classification="potential_issue", code="NO_MATCHING_RELEASE", title=f"No listed release matches the requested constraint for {name}",
                explanation="The PyPI release metadata checked by Bugsy did not contain a release satisfying this direct requirement. Resolver output remains the authority for the complete dependency set.", packages=[name],
                evidence=[CompatibilityEvidence(source="pypi", label="Requested constraint", value=str(_value(requirement, "specifier", "")) or "unconstrained", package=name), CompatibilityEvidence(source="pypi", label="Metadata result", value=item.error_message or item.error_code, package=name, source_url=item.source_url)],
            ))
        if item.requires_python:
            try:
                supported = Version(python_target) in SpecifierSet(item.requires_python)
            except (InvalidVersion, InvalidSpecifier):
                findings.append(CompatibilityFinding(
                    classification="unknown", code="REQUIRES_PYTHON_UNPARSEABLE", title=f"Python compatibility is unclear for {name}",
                    explanation="The retrieved Requires-Python value or selected target could not be interpreted reliably.", packages=[name],
                    evidence=[CompatibilityEvidence(source="pypi", label="Requires-Python", value=item.requires_python, package=name, source_url=item.source_url)],
                ))
            else:
                if not supported:
                    findings.append(CompatibilityFinding(
                        classification="potential_issue", code="PYTHON_TARGET_OUTSIDE_METADATA", title=f"Python target is outside {name}'s declared range",
                        explanation=f"PyPI declares {item.requires_python}; requested Python {python_target} does not satisfy that metadata. This is package metadata evidence, not an installation test.", packages=[name],
                        evidence=[CompatibilityEvidence(source="pypi", label="Requires-Python", value=item.requires_python, package=name, source_url=item.source_url), CompatibilityEvidence(source="analysis", label="Requested Python", value=python_target, package=name)],
                    ))

    simulated = resolver.execution_mode == "simulated"
    real_evidence = resolver.execution_mode == "real"
    output = f"{resolver.stdout}\n{resolver.stderr}"
    explicit_unsat = bool(re.search(r"No solution found when resolving dependencies", output, re.IGNORECASE))
    if simulated:
        findings.append(CompatibilityFinding(
            classification="unknown", code="SIMULATED_RESOLVER", title="Resolver result is simulated",
            explanation="This resolver outcome is for UI or automated testing only and is not evidence about package compatibility.",
            evidence=[CompatibilityEvidence(source="uv", label="SIMULATED resolver output", value=(resolver.stdout or resolver.stderr or "No simulated output")[:1000])],
        ))
    elif real_evidence and resolver.status == "unresolved" and resolver.exit_code not in (None, 0) and explicit_unsat and resolver.failure_category == "resolver_failure":
        findings.append(CompatibilityFinding(
            classification="confirmed_conflict", code="RESOLVER_UNSATISFIABLE", title="Resolver confirmed this dependency set has no solution",
            explanation="The real resolver exited unsuccessfully and explicitly reported that it could not find a solution. The output below is the supporting evidence; no specific conflicting package pair is inferred.",
            evidence=[CompatibilityEvidence(source="uv", label="Resolver diagnostic", value=(resolver.stdout or resolver.stderr)[-2000:])],
        ))
    elif resolver.failure_category in {"network_failure", "sandbox_failure"}:
        findings.append(CompatibilityFinding(
            classification="incomplete", code=f"RESOLVER_{resolver.failure_category.upper()}", title="Dependency resolution did not produce a compatibility result",
            explanation=resolver.limitation or f"The resolver reported a {resolver.failure_category.replace('_', ' ')}; this is not evidence of a dependency conflict.",
            evidence=[CompatibilityEvidence(source="analysis", label="Failure category", value=resolver.failure_category), CompatibilityEvidence(source="uv", label="Observed error", value=(resolver.stderr or resolver.stdout or "No diagnostic returned")[-2000:])],
        ))
    elif resolver.status in {"not_run", "error"} or resolver.execution_mode == "not_run":
        findings.append(CompatibilityFinding(
            classification="incomplete", code="RESOLVER_NOT_COMPLETED", title="Dependency resolution is incomplete",
            explanation=resolver.limitation or "The real resolver did not complete, so compatibility is unknown.",
            evidence=[CompatibilityEvidence(source="analysis", label="Resolver status", value=resolver.status)],
        ))
    elif resolver.status == "unresolved":
        findings.append(CompatibilityFinding(
            classification="unknown", code="UNCLASSIFIED_RESOLVER_FAILURE", title="Resolver outcome is unknown",
            explanation="The resolver did not resolve the requirements, but its output does not contain the recognized explicit unsatisfiable-dependency diagnostic. No conflict is inferred.",
            evidence=[CompatibilityEvidence(source="uv", label="Resolver output", value=(resolver.stderr or resolver.stdout or "No diagnostic returned")[-2000:])],
        ))
    elif resolver.status == "resolved" and real_evidence:
        mismatch = resolver.target_matches_sandbox_python is False
        missing_environment_evidence = resolver.target_matches_sandbox_python is None or not resolver.sandbox_python
        if resolver.exit_code != 0:
            findings.append(CompatibilityFinding(
                classification="unknown", code="RESOLVER_SUCCESS_STATUS_UNCONFIRMED", title="Resolver success could not be confirmed",
                explanation="The adapter reported a resolved status without a zero exit code; Bugsy does not treat that as successful resolution.",
                evidence=[CompatibilityEvidence(source="uv", label="Resolver exit code", value=str(resolver.exit_code))],
            ))
        elif mismatch or missing_environment_evidence:
            findings.append(CompatibilityFinding(
                classification="incomplete", code="TARGET_ENVIRONMENT_MISMATCH", title="Requested Python target was not verified",
                explanation=(f"Resolution succeeded in the reported Sandbox environment, but that environment does not match requested Python {python_target}." if mismatch else "Resolution succeeded, but Sandbox Python details are insufficient to confirm the requested target."),
                evidence=[CompatibilityEvidence(source="analysis", label="Requested Python", value=python_target), CompatibilityEvidence(source="analysis", label="Sandbox Python", value=resolver.sandbox_python or "not reported")],
            ))
        else:
            findings.append(CompatibilityFinding(
                classification="resolved", code="RESOLUTION_SUCCEEDED", title="Dependency resolution succeeded in the tested environment",
                explanation="The resolver found a solution in this environment. This confirms resolution only; package installation, imports, and project tests were not run.",
                evidence=[CompatibilityEvidence(source="uv", label="Resolver exit code", value=str(resolver.exit_code)), CompatibilityEvidence(source="analysis", label="Tested environment", value=f"{resolver.sandbox_os or 'OS not reported'}; Python {resolver.sandbox_python or python_target}")],
            ))

    if simulated:
        status = "simulated"
        summary = "SIMULATED resolver output is not real compatibility evidence. PyPI metadata, when present, is reported separately."
    elif any(f.classification == "confirmed_conflict" for f in findings):
        status, summary = "confirmed_conflict", "The real resolver explicitly reported that this dependency set has no solution."
    elif any(f.classification == "incomplete" for f in findings):
        status, summary = "incomplete", "Some checks did not complete or the requested target was not verified; compatibility remains incomplete."
    elif any(f.classification == "potential_issue" for f in findings):
        status, summary = "potential_issues", "PyPI metadata indicates potential compatibility issues; review the cited evidence and resolver result."
    elif any(f.classification == "unknown" for f in findings):
        status, summary = "unknown", "Available evidence does not establish compatibility or a confirmed dependency conflict."
    else:
        status, summary = "resolved_in_environment", "Dependency resolution succeeded in the tested environment. Installation, imports, and project tests were not run."

    return CompatibilityReport(status=status, summary=summary, findings=findings)
