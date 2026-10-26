from app.adapters.contree import (
    _INSTALLATION_SCRIPT_TEMPLATE,
    ContreeInstallationAdapter,
    build_sdk_installation_kwargs,
    _parse_installation_result,
)
import hashlib
import sqlite3
import sys
import types
import pytest
from uuid import uuid4

from app.domain.evidence import RequirementSource
from app.config import Settings
from app.schemas.analysis import (
    AnalysisResponse, ParsedRequirement, ReleaseCandidate, RepairProposal,
    RepairResolverEvidence, ResolverResult,
)
from app.services.repair import requirements_diff
from app.services import workflow
from app.services.verification import execute_installation_stage, sanitize_installation_diagnostics


def test_installation_operation_is_fixed_transfers_bytes_and_does_not_run_other_stages():
    requirements = "requests==2.34.2\nurllib3==2.2.0\n"
    script = _INSTALLATION_SCRIPT_TEMPLATE.replace("__UV_RESOLVER_VERSION__", "0.12.19")
    compile(script, "<contree-installation>", "exec")
    kwargs = build_sdk_installation_kwargs(requirements, "3.12")

    assert kwargs["command"] == "/usr/local/bin/python"
    assert kwargs["args"] == ["-c", script]
    assert kwargs["env"] == {"BUGSY_PYTHON_TARGET": "3.12"}
    assert kwargs["cwd"] == "/tmp"
    assert kwargs["files"] == {"/tmp/requirements.txt": requirements.encode("utf-8")}
    assert kwargs["timeout"] == 180
    assert kwargs["truncate_output_at"] == 24000
    assert kwargs["disposable"] is True
    assert "uv==\" + PIN" in script
    assert "\"uv\", \"pip\", \"install\"" in script
    assert "-r\", \"/tmp/requirements.txt\"" in script
    assert "uv\", \"pip\", \"compile\"" not in script
    assert "importlib" not in script
    assert "pytest" not in script
    assert requirements not in script


def test_installation_input_limits_and_python_target_are_enforced():
    try:
        build_sdk_installation_kwargs("demo==1\n", "3;12")
    except ValueError as error:
        assert "Python target" in str(error)
    else:
        raise AssertionError("invalid Python target was accepted")


def test_installation_result_parser_reports_environment_and_bounded_diagnostics():
    output = (
        'BUGSY_INSTALLATION={"status":"passed","phase":"installation",'
        '"environment":{"os":"Linux","python_version":"3.12.13"},'
        '"uv_version":"0.12.19","exit_code":0,"stdout":"ok",'
        '"stderr":"","failure_category":null}\n'
    )
    result = _parse_installation_result(output, 0, "3.12")
    assert result["status"] == "passed"
    assert result["environment"]["target_matches_sandbox_python"] is True
    assert result["uv_version"] == "0.12.19"
    assert result["exit_code"] == 0

    missing = _parse_installation_result("no result marker", 1, "3.12")
    assert missing["status"] == "unknown"
    assert missing["failure_category"] == "unknown_failure"
    assert len(missing["stdout"]) <= 6000


def test_installation_result_parser_does_not_claim_pass_for_wrong_python():
    output = (
        'BUGSY_INSTALLATION={"status":"passed","phase":"installation",'
        '"environment":{"os":"Linux","python_version":"3.11.9"},'
        '"uv_version":"0.12.19","exit_code":0,"stdout":"ok","stderr":""}\n'
    )
    result = _parse_installation_result(output, 0, "3.12")
    assert result["environment"]["target_matches_sandbox_python"] is False
    assert result["status"] == "incomplete"


@pytest.mark.parametrize(
    ("failure", "expected_phase"),
    [("client", "client_configuration"), ("upload", "file_staging"),
     ("submit", "operation_submission"), ("track", "operation_tracking")],
)
def test_contree_transport_failure_is_unknown_with_safe_identifiable_phase(
    contree_client_fake, failure, expected_phase,
):
    calls, _ = contree_client_fake(failure=failure)

    result = ContreeInstallationAdapter(config=Settings(
        sandbox_enabled=True, contree_base_url="https://sandbox.example", nebius_api_key="test-only-secret",
    )).install("demo==1\n", "3.12")

    assert result["status"] == "unknown"
    assert result["failure_category"] == "network_failure"
    assert result["failure_phase"] == expected_phase
    assert "APIConnectionError" in result["failure_detail"]
    assert "Sandbox execution outcome is unknown" in result["failure_detail"]
    assert "secret" not in str(result)
    assert "user:password" not in str(result)
    assert result["environment"] == {}
    assert result["exit_code"] is None
    assert len(calls["submissions"]) <= 1
    assert len(calls["waits"]) <= 1


def test_contree_installation_persists_returned_operation_id_before_wait(contree_client_fake):
    calls, _ = contree_client_fake()
    observed = []

    def submitted(operation_id):
        # The callback runs after spawn_instance returned, before wait_operation.
        assert calls["submissions"]
        assert calls["waits"] == []
        observed.append(operation_id)
        return True

    result = ContreeInstallationAdapter(config=Settings(
        sandbox_enabled=True, contree_base_url="https://sandbox.example", nebius_api_key="test-only-secret",
    )).install("demo==1\n", "3.12", on_operation_submitted=submitted)

    assert len(observed) == 1
    assert calls["waits"][0][0] == observed[0]
    assert result["status"] in {"unknown", "incomplete", "passed", "failed"}


def _ready_installation_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "installation.sqlite3")
    original = "demo==1\n"
    proposed = "demo==2\n"
    diff = requirements_diff(original, proposed)
    source_hash = hashlib.sha256(original.encode()).hexdigest()
    candidate = ReleaseCandidate(candidate_id="C001", package="demo", version="2", source_url="https://pypi.org/pypi/demo/2/json", requires_python=">=3.10", requires_dist=[], yanked=False)
    proposal = RepairProposal(
        status="ready", proposal_id="proposal-install", summary="Resolver accepted.",
        original_sha256=source_hash, diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
        proposed_requirements=proposed, diff=diff, candidate_evidence=[candidate],
        resolver_evidence=RepairResolverEvidence(status="accepted", resolver="uv", resolver_version="0.12.19", exit_code=0, python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13"),
        resolver_diagnostics=ResolverResult(status="resolved", execution_mode="real", resolver="uv", resolver_version="0.12.19", exit_code=0, python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13", target_matches_sandbox_python=True, resolved_requirements=proposed),
    )
    source = RequirementSource(source_id="source-install", original_text=original, source_sha256=source_hash, python_version="3.12", requirements=({"line":1,"original":"demo==1","name":"demo","specifier":"==1","extras":[],"marker":None},))
    analysis = AnalysisResponse(analysis_id="analysis-install", status="complete", source_id=source.source_id, python_target="3.12", provenance={"source":"saved_resolver_capture","capture_id":"capture-install","classification":"requirements_sha256"}, requirements=[ParsedRequirement(line=1,original="demo==1",name="demo",specifier="==1",extras=[],marker=None)], repair_proposal=proposal)
    workflow.save_source(source)
    workflow.save_analysis(analysis)
    claim = workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "stage-install")
    assert claim is not None
    return analysis, source, proposal, claim


def test_installation_service_invokes_only_install_adapter_once_and_persists_result(tmp_path, monkeypatch):
    analysis, source, proposal, _ = _ready_installation_fixture(tmp_path, monkeypatch)

    class InstallOnlyFake:
        def __init__(self):
            self.calls = []

        def install(self, requirements, target, *, on_operation_submitted=None):
            self.calls.append((requirements, target))
            if on_operation_submitted is not None:
                assert on_operation_submitted(str(uuid4())) is True
            return {"status":"passed","environment":{"os":"Linux","python_version":"3.12.13","target_matches_sandbox_python":True},"uv_version":"0.12.19","exit_code":0,"stdout":"installed","stderr":"","failure_category":None}

    adapter = InstallOnlyFake()
    result = execute_installation_stage("stage-install", adapter_factory=lambda: adapter)
    assert result.status == "passed"
    assert result.execution_mode == "real"
    assert adapter.calls == [(proposal.proposed_requirements, "3.12")]
    assert workflow.get_installation_stage(result.stage_id).status == "passed"
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)
    assert workflow.get_source(source.source_id).original_text == "demo==1\n"
    assert result.contree_operation_id is not None
    assert workflow.get_installation_stage(result.stage_id).contree_operation_id == result.contree_operation_id


def test_installation_service_revalidates_bytes_before_adapter(tmp_path, monkeypatch):
    analysis, _source, proposal, _ = _ready_installation_fixture(tmp_path, monkeypatch)
    with sqlite3.connect(workflow.DB_PATH) as connection:
        connection.execute("UPDATE analyses SET proposed_requirements_bytes=? WHERE analysis_id=?", (b"demo==3\n", analysis.analysis_id))

    class MustNotRun:
        calls = 0
        def install(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("Sandbox adapter must not run for changed bytes")

    adapter = MustNotRun()
    result = execute_installation_stage("stage-install", adapter_factory=lambda: adapter)
    assert result.status == "incomplete"
    assert result.execution_mode == "not_run"
    assert adapter.calls == 0
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)


def test_installation_service_persists_unknown_adapter_result_without_retry(tmp_path, monkeypatch):
    analysis, _source, proposal, _ = _ready_installation_fixture(tmp_path, monkeypatch)

    class LostResult:
        calls = 0
        def install(self, *_args, **_kwargs):
            self.calls += 1
            return {"status":"unknown","environment":{},"exit_code":None,"stdout":"","stderr":"","failure_category":"unknown_failure","limitation":"Sandbox result unavailable."}

    adapter = LostResult()
    result = execute_installation_stage("stage-install", adapter_factory=lambda: adapter)
    assert result.status == "unknown"
    assert result.failure_category == "unknown_failure"
    assert adapter.calls == 1
    assert workflow.get_installation_stage(result.stage_id).status == "unknown"
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)


def test_installation_service_persists_safe_transport_diagnostics_as_unknown(tmp_path, monkeypatch):
    analysis, _source, proposal, _ = _ready_installation_fixture(tmp_path, monkeypatch)

    class TransportFailure:
        def install(self, *_args, **_kwargs):
            return {
                "status": "unknown", "environment": {}, "exit_code": None, "stdout": "", "stderr": "",
                "failure_category": "network_failure", "failure_phase": "operation_wait",
                "failure_detail": "SDK transport error; Sandbox execution outcome is unknown.",
            }

    result = execute_installation_stage("stage-install", adapter_factory=TransportFailure)
    assert result.status == "unknown"
    assert result.failure_phase == "operation_wait"
    assert "outcome is unknown" in result.failure_detail
    stored = workflow.get_installation_stage(result.stage_id)
    assert stored.status == "unknown"
    assert stored.failure_phase == "operation_wait"
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)


def test_installation_service_validates_phase_and_redacts_details_before_persistence(tmp_path, monkeypatch):
    _analysis, _source, _proposal, _ = _ready_installation_fixture(tmp_path, monkeypatch)
    phase, detail = sanitize_installation_diagnostics(
        "https://attacker.example/?token=secret",
        "Request https://user:password@example.test/ with Authorization: Bearer abc123 and NEBIUS_API_KEY=key-value",
    )
    assert phase == "unknown"
    assert "?token=secret" not in detail and "user:password" not in detail and "abc123" not in detail and "key-value" not in detail
    assert "[redacted URL]" in detail

    class UntrustedDiagnostics:
        def install(self, *_args, **_kwargs):
            return {
                "status": "unknown", "environment": {}, "exit_code": None, "stdout": "", "stderr": "",
                "failure_category": "network_failure", "failure_phase": "not_a_real_phase",
                "failure_detail": "Authorization: Bearer top-secret-value",
            }

    result = execute_installation_stage("stage-install", adapter_factory=UntrustedDiagnostics)
    assert result.status == "unknown"
    assert result.failure_phase == "unknown"
    assert "top-secret-value" not in (result.failure_detail or "")
    stored = workflow.get_installation_stage(result.stage_id)
    assert stored.failure_phase == "unknown"
    assert "top-secret-value" not in (stored.failure_detail or "")


def test_installation_service_uses_explicit_one_operation_sandbox_config(tmp_path, monkeypatch):
    _analysis, _source, _proposal, _ = _ready_installation_fixture(tmp_path, monkeypatch)
    observed = {}

    class ConfiguredInstallOnlyFake:
        def __init__(self, *, config):
            observed["sandbox_enabled"] = config.sandbox_enabled

        def install(self, _requirements, _target, *, on_operation_submitted=None):
            return {"status":"unknown","environment":{},"exit_code":None,"stdout":"","stderr":"","failure_category":"unknown_failure"}

    monkeypatch.setattr("app.services.verification.ContreeInstallationAdapter", ConfiguredInstallOnlyFake)
    result = execute_installation_stage("stage-install", config=Settings(sandbox_enabled=False))
    assert result.status == "unknown"
    assert observed == {"sandbox_enabled": False}
