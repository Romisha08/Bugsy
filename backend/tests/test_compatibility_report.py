from app.schemas.analysis import MetadataResult, ResolverResult
from app.services.compatibility_report import build_compatibility_report


REQ = [{"name": "demo", "specifier": "", "line": 1, "original": "demo", "extras": [], "marker": None}]


def metadata(**updates):
    values = {"package": "demo", "status": "available", "source_url": "https://pypi.org/pypi/demo/json", "release": "1.0", "requires_python": ">=3.10", "requires_dist": []}
    values.update(updates)
    return [MetadataResult(**values)]


def resolver(**updates):
    values = {"status": "resolved", "execution_mode": "real", "resolver": "uv", "resolver_version": "0.12.19", "python_target": "3.12", "sandbox_os": "Linux", "sandbox_python": "3.12.13", "target_matches_sandbox_python": True, "exit_code": 0, "stdout": "Resolved 1 package", "stderr": ""}
    values.update(updates)
    return ResolverResult(**values)


def test_success_is_limited_to_resolver_environment_and_deterministic():
    first = build_compatibility_report(REQ, "3.12", metadata(), resolver())
    second = build_compatibility_report(REQ, "3.12", metadata(), resolver())
    assert first == second
    assert first.status == "resolved_in_environment"
    assert first.findings[0].classification == "resolved"
    assert "installation, imports, and project tests were not run" in first.findings[0].explanation


def test_only_explicit_real_unsatisfiable_diagnostic_confirms_conflict():
    result = build_compatibility_report(REQ, "3.12", metadata(), resolver(status="unresolved", exit_code=1, failure_category="resolver_failure", stdout="No solution found when resolving dependencies"))
    assert result.status == "confirmed_conflict"
    assert result.findings[-1].code == "RESOLVER_UNSATISFIABLE"


def test_unclassified_nonzero_exit_is_unknown_not_conflict():
    result = build_compatibility_report(REQ, "3.12", metadata(), resolver(status="unresolved", exit_code=2, failure_category="unknown_failure", stderr="unexpected tool error"))
    assert result.status == "unknown"
    assert all(item.classification != "confirmed_conflict" for item in result.findings)


def test_metadata_python_constraint_is_potential_issue_with_source():
    result = build_compatibility_report(REQ, "3.9", metadata(requires_python=">=3.10"), resolver())
    finding = next(item for item in result.findings if item.code == "PYTHON_TARGET_OUTSIDE_METADATA")
    assert finding.classification == "potential_issue"
    assert finding.evidence[0].source_url == "https://pypi.org/pypi/demo/json"
    assert result.status == "potential_issues"


def test_unavailable_metadata_is_unknown_not_a_conflict():
    result = build_compatibility_report(REQ, "3.12", metadata(status="unavailable", error_message="404 Not Found"), resolver())
    assert result.status == "unknown"
    assert result.findings[0].classification == "unknown"


def test_network_and_sandbox_failures_are_incomplete_not_conflicts():
    for category in ("network_failure", "sandbox_failure"):
        result = build_compatibility_report(REQ, "3.12", metadata(), resolver(status="error", execution_mode="real", failure_category=category, exit_code=1, stderr="connection failed"))
        assert result.status == "incomplete"
        assert result.findings[-1].code == f"RESOLVER_{category.upper()}"
        assert all(item.classification != "confirmed_conflict" for item in result.findings)


def test_target_mismatch_and_missing_metadata_are_incomplete():
    target = build_compatibility_report(REQ, "3.12", metadata(), resolver(target_matches_sandbox_python=False, sandbox_python="3.11"))
    missing = build_compatibility_report(REQ, "3.12", [], resolver(status="not_run", execution_mode="not_run"))
    assert target.status == "incomplete"
    assert missing.status == "incomplete"


def test_resolved_status_requires_zero_exit_and_reported_sandbox_python():
    nonzero = build_compatibility_report(REQ, "3.12", metadata(), resolver(exit_code=1))
    no_environment = build_compatibility_report(REQ, "3.12", metadata(), resolver(sandbox_python=None, target_matches_sandbox_python=None))
    assert nonzero.status == "unknown"
    assert all(item.classification != "resolved" for item in nonzero.findings)
    assert no_environment.status == "incomplete"


def test_simulated_results_are_visibly_simulated_and_never_confirm_conflict():
    result = build_compatibility_report(REQ, "3.12", metadata(), resolver(status="unresolved", execution_mode="simulated", exit_code=1, stdout="SIMULATED unresolved"))
    assert result.status == "simulated"
    assert any("SIMULATED" in item.title or "SIMULATED" in item.evidence[0].label for item in result.findings)
    assert all(item.classification != "confirmed_conflict" for item in result.findings)
