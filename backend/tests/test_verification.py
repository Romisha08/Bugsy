from dataclasses import replace
import hashlib
from app.adapters.contree import _VERIFICATION_SCRIPT_TEMPLATE

from app.adapters.contree import build_sdk_verification_kwargs
from app.config import settings
from app.domain.evidence import RequirementSource
from app.schemas.analysis import AnalysisResponse, ParsedRequirement, RepairProposal
from app.services.verification import import_modules_for, new_verification, run_verification


def fixture_analysis():
    diff = "--- requirements.txt\n-demo==1\n+demo==2\n"
    return AnalysisResponse(
        analysis_id="analysis-1", status="complete", source_id="source-1", python_target="3.12",
        requirements=[
            ParsedRequirement(line=1, original="requests>=2", name="requests", specifier=">=2", extras=[], marker=None),
            ParsedRequirement(line=2, original="mystery-package==1", name="mystery-package", specifier="==1", extras=[], marker=None),
        ],
        repair_proposal=RepairProposal(
            status="ready", proposal_id="proposal-1", summary="Evidence-backed proposal.",
            original_sha256="a" * 64, diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
            proposed_requirements="requests>=2\nmystery-package==1\n",
            diff=diff, verified=False,
        ),
    )


def fixture_source():
    return RequirementSource(
        source_id="source-1", original_text="demo==1\n", source_sha256="a" * 64,
        python_version="3.12", requirements=(),
    )


def enabled():
    return replace(settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid")


def passed_output(**updates):
    output = {
        "status": "complete",
        "environment": {"os": "Linux-6.1", "python_version": "3.12.13"},
        "resolver_version": "0.12.19",
        "checks": {
            "resolution": {"status": "passed", "exit_code": 0},
            "installation": {"status": "passed", "exit_code": 0},
            "imports": {"status": "passed", "exit_code": 0, "stdout": "requests"},
        },
    }
    output.update(updates)
    return output


class FakeAdapter:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def verify(self, *args):
        self.calls.append(args)
        return self.output


def test_import_names_use_only_reliable_allowlist():
    assert import_modules_for(fixture_analysis()) == [("requests", "requests")]


def test_success_reports_checks_environment_and_project_tests_not_run():
    analysis = fixture_analysis()
    job = new_verification(analysis)
    adapter = FakeAdapter(passed_output())
    result = run_verification(analysis, fixture_source(), job, config=enabled(), adapter=adapter)
    assert result.status == "complete"
    assert result.execution_mode == "real"
    assert result.environment.os == "Linux-6.1"
    assert result.environment.python_version == "3.12.13"
    assert result.checks.resolution.status == "passed"
    assert result.checks.installation.status == "passed"
    assert result.checks.imports.status == "passed"
    assert result.checks.project_tests.status == "not_run"
    assert result.checks.project_tests.reason == "Project code not provided."
    assert adapter.calls[0][0] == analysis.repair_proposal.proposed_requirements
    assert adapter.calls[0][2] == [("requests", "requests")]


def test_python_target_mismatch_limits_install_and_import_claims():
    analysis = fixture_analysis()
    job = new_verification(analysis)
    output = passed_output(environment={"os": "Linux", "python_version": "3.11.9"})
    result = run_verification(analysis, fixture_source(), job, config=enabled(), adapter=FakeAdapter(output))
    assert result.status == "incomplete"
    assert result.environment.target_matches_sandbox_python is False
    assert result.checks.resolution.status == "passed"
    assert result.checks.installation.status == "not_run"
    assert result.checks.imports.status == "not_run"
    assert "differs" in result.limitation


def test_disabled_sandbox_fails_closed_without_adapter_call():
    analysis = fixture_analysis()
    job = new_verification(analysis)
    adapter = FakeAdapter(passed_output())
    result = run_verification(analysis, fixture_source(), job, config=replace(settings, sandbox_enabled=False), adapter=adapter)
    assert result.status == "unavailable"
    assert result.execution_mode == "not_run"
    assert all(check.status == "not_run" for check in (result.checks.resolution, result.checks.installation, result.checks.imports))
    assert adapter.calls == []


def test_adapter_failure_is_reported_without_automatic_retry():
    class BrokenAdapter:
        def __init__(self):
            self.calls = 0

        def verify(self, *_args):
            self.calls += 1
            raise RuntimeError("CONTREE_NETWORK_FAILURE:TimeoutError")

    analysis = fixture_analysis()
    job = new_verification(analysis)
    adapter = BrokenAdapter()
    result = run_verification(analysis, fixture_source(), job, config=enabled(), adapter=adapter)
    assert result.status == "incomplete"
    assert result.failure_category == "network_failure"
    assert all(check.status == "not_run" for check in (result.checks.resolution, result.checks.installation, result.checks.imports))
    assert adapter.calls == 1


def test_remote_command_uses_fixed_script_and_transfers_requirement_bytes():
    compile(_VERIFICATION_SCRIPT_TEMPLATE.replace("__UV_RESOLVER_VERSION__", "0.12.19"), "<contree-verification>", "exec")
    requirements = "requests>=2\n"
    kwargs = build_sdk_verification_kwargs(requirements, "3.12", [("requests", "requests")])
    assert kwargs["command"] == "/usr/local/bin/python"
    assert kwargs["files"]["/tmp/requirements.txt"] == requirements.encode()
    assert "requests>=2" not in kwargs["args"][1]
    assert kwargs["env"]["BUGSY_PYTHON_TARGET"] == "3.12"
    assert 'PIN = "0.12.19"' in kwargs["args"][1]
    try:
        build_sdk_verification_kwargs(requirements, "3.12", [("demo", "os")])
    except ValueError as error:
        assert "invalid application-selected name" in str(error)
    else:
        raise AssertionError("non-allowlisted import name was accepted")


def test_changed_diff_or_original_source_cannot_be_verified():
    analysis = fixture_analysis()
    bad_source = fixture_source().__class__(
        source_id="source-1", original_text="different\n", source_sha256="b" * 64,
        python_version="3.12", requirements=(),
    )
    adapter = FakeAdapter(passed_output())
    result = run_verification(analysis, bad_source, new_verification(analysis), config=enabled(), adapter=adapter)
    assert result.status == "incomplete"
    assert adapter.calls == []


def test_failed_resolution_and_installation_keep_later_checks_not_run():
    analysis = fixture_analysis()
    job = new_verification(analysis)
    resolution_failure = passed_output(checks={
        "resolution": {"status": "failed", "exit_code": 1, "stderr": "No solution found when resolving dependencies"},
        "installation": {"status": "not_run", "reason": "An earlier check failed."},
        "imports": {"status": "not_run", "reason": "An earlier check failed."},
    })
    result = run_verification(analysis, fixture_source(), job, config=enabled(), adapter=FakeAdapter(resolution_failure))
    assert result.status == "incomplete"
    assert result.checks.resolution.status == "failed"
    assert result.checks.installation.status == "not_run"
    assert result.checks.imports.status == "not_run"

    install_failure = passed_output(checks={
        "resolution": {"status": "passed", "exit_code": 0},
        "installation": {"status": "failed", "exit_code": 1, "stderr": "package build failed"},
        "imports": {"status": "not_run", "reason": "An earlier check failed."},
    })
    result = run_verification(analysis, fixture_source(), job, config=enabled(), adapter=FakeAdapter(install_failure))
    assert result.status == "incomplete"
    assert result.checks.resolution.status == "passed"
    assert result.checks.installation.status == "failed"
    assert result.checks.imports.status == "not_run"
