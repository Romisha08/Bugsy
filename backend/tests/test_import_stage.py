import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.adapters.contree import (
    _IMPORT_SCRIPT_TEMPLATE, ContreeImportAdapter, ContreeInstallationAdapter, build_sdk_import_kwargs,
    build_sdk_installation_kwargs,
)
from app.config import Settings, _read_bool
from app.domain.evidence import RequirementSource
from app.schemas.analysis import (
    AnalysisResponse, ParsedRequirement, ReleaseCandidate, RepairProposal,
    RepairResolverEvidence, ResolverResult,
)
from app.services import workflow
from app.services.verification import execute_verification_attempt
from app.services.verification import sanitize_installation_diagnostics
from app.services.repair import requirements_diff


def ready_analysis():
    original = "requests==2.31.0\n"
    proposed = "requests==2.32.0\n"
    diff = requirements_diff(original, proposed)
    source_hash = hashlib.sha256(original.encode()).hexdigest()
    source = RequirementSource(
        source_id="source-import-stage", original_text=original, source_sha256=source_hash,
        python_version="3.12", requirements=({
            "line": 1, "original": original.strip(), "name": "requests",
            "specifier": "==2.31.0", "extras": [], "marker": None,
        },),
    )
    candidate = ReleaseCandidate(
        candidate_id="C001", package="requests", version="2.32.0",
        source_url="https://pypi.org/pypi/requests/2.32.0/json",
        requires_python=">=3.8", requires_dist=["urllib3<3"], yanked=False,
    )
    proposal = RepairProposal(
        status="ready", proposal_id="proposal-import-stage", summary="Resolver accepted.",
        original_sha256=source_hash, diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
        proposed_requirements=proposed, diff=diff, candidate_evidence=[candidate],
        resolver_evidence=RepairResolverEvidence(
            status="accepted", resolver="uv", resolver_version="0.12.19",
            exit_code=0, python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13",
        ),
        resolver_diagnostics=ResolverResult(
            status="resolved", execution_mode="real", resolver="uv", resolver_version="0.12.19",
            exit_code=0, python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13",
            target_matches_sandbox_python=True, resolved_requirements=proposed,
        ),
    )
    analysis = AnalysisResponse(
        analysis_id="analysis-import-stage", status="complete", source_id=source.source_id,
        python_target="3.12", requirements=[ParsedRequirement(
            line=1, original=original.strip(), name="requests", specifier="==2.31.0", extras=[], marker=None,
        )], provenance={"capture_id": "fixture", "source": "test", "classification": "test"},
        repair_proposal=proposal,
    )
    workflow.save_source(source)
    workflow.save_analysis(analysis)
    return analysis, source, proposal


def test_installation_attempts_are_additive_and_approval_is_bound(tmp_path, monkeypatch):
    # Use a private SQLite file so this test never touches Bugsy's working database.
    path = tmp_path / "attempts.sqlite3"
    monkeypatch.setattr(workflow, "DB_PATH", path)
    workflow.clear_workflow_state()
    analysis, source, proposal = ready_analysis()
    assert workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, "0" * 64, "bad", "request-bad-0001", stage="installation") is None
    assert workflow.claim_verification_attempt(analysis.analysis_id, "different-proposal", proposal.diff_sha256, "bad-id", "request-bad-0002", stage="installation") is None
    assert workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "bad-stage", "request-bad-0003", stage="resolver") is None
    first = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "attempt-1", "request-first-0001", stage="installation")
    assert first is not None and first.status == "running"
    assert workflow.verification_attempt_approval_state("attempt-1") == ("consumed", proposal.diff_sha256, "installation")
    assert workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "attempt-duplicate", "request-first-0001", stage="installation") is None
    assert workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "attempt-concurrent", "request-other-0002", stage="installation") is None
    failed = first.model_copy(update={"status": "unknown", "failure_category": "unknown_failure", "limitation": "No structured result."})
    assert workflow.save_verification_attempt_result(failed)
    second = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "attempt-2", "request-second-0002", stage="installation")
    assert second is not None and second.stage_id != first.stage_id
    assert source.original_text == "requests==2.31.0\n"
    assert len(workflow.list_verification_attempts(analysis.analysis_id, proposal.proposal_id, "installation")) == 2
    workflow.clear_workflow_state()


def test_service_flags_default_off_without_environment_overrides(monkeypatch):
    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)
    monkeypatch.delenv("TOKEN_FACTORY_ENABLED", raising=False)
    assert _read_bool("SANDBOX_ENABLED", False) is False
    assert _read_bool("TOKEN_FACTORY_ENABLED", False) is False


def test_v6_attempt_migration_preserves_legacy_unknown_and_consumed_rows(tmp_path, monkeypatch):
    path = tmp_path / "migration.sqlite3"
    monkeypatch.setattr(workflow, "DB_PATH", path)
    connection = workflow._connect()
    now = "2026-10-19T00:00:00+00:00"
    connection.execute(
        "INSERT INTO verification_stage_history(stage_id,analysis_id,proposal_id,diff_sha256,stage,status,result_json,created_at,updated_at,owner_pid,owner_instance_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy-stage", "legacy-analysis", "legacy-proposal", "d" * 64, "installation", "unknown", "{}", now, now, 1, "old"),
    )
    connection.execute(
        "INSERT INTO verification_stage_approvals(stage_id,analysis_id,proposal_id,diff_sha256,stage,state,consumed_at) VALUES (?,?,?,?,?,'consumed',?)",
        ("legacy-stage", "legacy-analysis", "legacy-proposal", "d" * 64, "installation", now),
    )
    connection.execute("DROP TABLE verification_attempt_approvals")
    connection.execute("DROP TABLE verification_attempt_history")
    connection.execute("PRAGMA user_version=4")
    connection.close()
    migrated = workflow._connect()
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 6
    assert tuple(migrated.execute("SELECT stage_id,status,diff_sha256 FROM verification_stage_history").fetchone()) == ("legacy-stage", "unknown", "d" * 64)
    assert tuple(migrated.execute("SELECT stage_id,state,diff_sha256 FROM verification_stage_approvals").fetchone()) == ("legacy-stage", "consumed", "d" * 64)
    assert "contree_operation_id" in {row[1] for row in migrated.execute("PRAGMA table_info(verification_stage_history)")}
    assert migrated.execute("SELECT contree_operation_id FROM verification_stage_history").fetchone()[0] is None
    assert migrated.execute("SELECT name FROM sqlite_master WHERE name='verification_attempt_history'").fetchone()
    migrated.close()


def test_v6_adds_nullable_operation_id_without_changing_legacy_unknown_stage(tmp_path, monkeypatch):
    path = tmp_path / "pre-v6.sqlite3"
    monkeypatch.setattr(workflow, "DB_PATH", path)
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    claimed = workflow.claim_installation_stage(
        analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "legacy-unknown-no-id",
    )
    legacy_unknown = claimed.model_copy(update={"status": "unknown", "failure_category": "unknown_failure"})
    legacy_json = legacy_unknown.model_dump(exclude={"contree_operation_id"})
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE verification_stage_history SET status='unknown',result_json=? WHERE stage_id=?",
            (json.dumps(legacy_json), claimed.stage_id),
        )
        connection.execute("ALTER TABLE verification_stage_history DROP COLUMN contree_operation_id")
        connection.execute("ALTER TABLE verification_attempt_history DROP COLUMN contree_operation_id")
        connection.execute("PRAGMA user_version=5")

    migrated = workflow._connect()
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 6
    migrated.close()
    recovered = workflow.get_installation_stage(claimed.stage_id)
    assert recovered.status == "unknown"
    assert recovered.contree_operation_id is None
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)


def test_submitted_operation_id_survives_restart_unknown_recovery_without_resubmit(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "submitted-operation.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    claimed = workflow.claim_verification_attempt(
        analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256,
        "submitted-before-interrupt", "request-submitted-before-interrupt", stage="installation",
    )
    operation_id = str(uuid4())
    assert workflow.persist_installation_operation_id(claimed.stage_id, operation_id)
    with sqlite3.connect(workflow.DB_PATH) as connection:
        assert connection.execute(
            "SELECT contree_operation_id FROM verification_attempt_history WHERE stage_id=?",
            (claimed.stage_id,),
        ).fetchone()[0] == operation_id

    monkeypatch.setattr(workflow, "_PROCESS_INSTANCE_ID", "restarted-worker")
    recovered = workflow.get_verification_attempt(claimed.stage_id)
    assert recovered.status == "unknown"
    assert recovered.contree_operation_id == operation_id
    assert workflow.verification_attempt_approval_state(claimed.stage_id) == (
        "consumed", proposal.diff_sha256, "installation",
    )

    class MustNotResubmit:
        calls = 0
        def install(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("recovered unknown stage must not be submitted again")

    adapter = MustNotResubmit()
    result = execute_verification_attempt(
        claimed.stage_id, config=Settings(sandbox_enabled=True),
        installation_adapter_factory=lambda: adapter,
    )
    assert result.status == "unknown" and result.contree_operation_id == operation_id
    assert adapter.calls == 0


def test_installation_service_keeps_submitted_id_when_tracking_result_is_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "tracking-loss.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    claimed = workflow.claim_verification_attempt(
        analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256,
        "tracking-loss", "request-tracking-loss", stage="installation",
    )
    operation_id = str(uuid4())

    class TrackingLost:
        calls = 0
        def install(self, _requirements, _target, *, retain_artifact, on_operation_submitted):
            self.calls += 1
            assert on_operation_submitted(operation_id) is True
            return {"status": "unknown", "environment": {}, "exit_code": None,
                    "failure_category": "network_failure", "failure_phase": "operation_tracking",
                    "limitation": "Tracking response unavailable."}

    adapter = TrackingLost()
    result = execute_verification_attempt(
        claimed.stage_id, config=Settings(sandbox_enabled=True),
        installation_adapter_factory=lambda: adapter,
    )
    assert result.status == "unknown"
    assert result.contree_operation_id == operation_id
    assert result.failure_phase == "operation_tracking"
    assert workflow.get_verification_attempt(claimed.stage_id).contree_operation_id == operation_id
    assert adapter.calls == 1
    assert workflow.verification_attempt_approval_state(claimed.stage_id) == (
        "consumed", proposal.diff_sha256, "installation",
    )


def test_missing_operation_id_remains_valid_and_does_not_enable_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "missing-operation.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    claimed = workflow.claim_verification_attempt(
        analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256,
        "missing-operation-id", "request-missing-operation-id", stage="installation",
    )
    class SubmittedWithoutTrackedId:
        calls = 0
        def install(self, _requirements, _target, *, retain_artifact, on_operation_submitted):
            self.calls += 1
            return {"status": "unknown", "environment": {}, "exit_code": None,
                    "failure_category": "unknown_failure", "limitation": "No structured result."}

    adapter = SubmittedWithoutTrackedId()
    result = execute_verification_attempt(
        claimed.stage_id, config=Settings(sandbox_enabled=True),
        installation_adapter_factory=lambda: adapter,
    )
    assert result.status == "unknown" and result.contree_operation_id is None
    assert workflow.get_verification_attempt(claimed.stage_id).contree_operation_id is None
    assert adapter.calls == 1
    assert execute_verification_attempt(
        claimed.stage_id, config=Settings(sandbox_enabled=True), installation_adapter_factory=lambda: adapter,
    ).status == "unknown"
    assert adapter.calls == 1


def test_concurrent_fresh_approvals_only_claim_one_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "concurrent.sqlite3")
    workflow.clear_workflow_state()


def test_fresh_attempt_rejects_while_legacy_installation_is_still_running(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "legacy-running.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()

    legacy = workflow.claim_installation_stage(
        analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "legacy-running-install",
    )
    assert legacy is not None and legacy.status == "running"
    assert workflow.claim_verification_attempt(
        analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256,
        "fresh-overlap", "request-fresh-overlap-01", stage="installation",
    ) is None

    # The guard is read-only: preserve the legacy stage and its consumed approval.
    assert workflow.get_installation_stage(legacy.stage_id).status == "running"
    assert workflow.installation_stage_approval_state(
        analysis.analysis_id, proposal.proposal_id,
    ) == ("consumed", proposal.diff_sha256)
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    def claim(index):
        return workflow.claim_verification_attempt(
            analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256,
            "concurrent-" + str(index), "request-concurrent-" + str(index), stage="installation",
        )
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(claim, range(6)))
    assert sum(item is not None for item in results) == 1
    workflow.clear_workflow_state()


def test_interrupted_attempt_stays_unknown_and_consumed(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "restart.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    claimed = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "interrupted", "request-interrupt-1", stage="installation")
    monkeypatch.setattr(workflow, "_PROCESS_INSTANCE_ID", "simulated-restart")
    recovered = workflow.get_verification_attempt("interrupted")
    assert recovered.status == "unknown"
    assert "approval remains consumed" in recovered.limitation.lower()
    assert workflow.verification_attempt_approval_state("interrupted") == ("consumed", proposal.diff_sha256, "installation")
    workflow.clear_workflow_state()


def test_import_attempt_requires_passed_installation_and_valid_retained_image(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "imports.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    assert workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "imports-too-early", "request-early-001", stage="imports") is None
    install = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "install-success", "request-install-001", stage="installation")
    image_ref = str(uuid4())
    passed = install.model_copy(update={
        "status": "passed", "execution_mode": "real",
        "environment": install.environment.model_copy(update={
            "sandbox_provider": "contree", "os": "Linux", "python_version": "3.12.13",
            "target_matches_sandbox_python": True,
        }),
        "exit_code": 0, "artifact_available": True,
    })
    assert workflow.save_verification_attempt_result(passed, image_ref)
    assert workflow.retained_installation_image("install-success") == image_ref
    imported = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "import-run", "request-import-001", stage="imports")
    assert imported is not None
    context = workflow.verification_attempt_context("import-run")
    assert context is not None and context[2] == proposal.proposed_requirements and context[3] == "imports" and context[4] == image_ref
    workflow.clear_workflow_state()


def test_non_uuid_or_non_success_install_result_never_persists_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "artifact.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    stage = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "bad-artifact", "request-artifact-001", stage="installation")
    passed = stage.model_copy(update={
        "status": "passed", "environment": stage.environment.model_copy(update={"python_version": "3.12.13", "target_matches_sandbox_python": True}),
        "exit_code": 0,
    })
    assert workflow.save_verification_attempt_result(passed, "not-an-image-id")
    stored = workflow.get_verification_attempt("bad-artifact")
    assert stored.artifact_available is False
    assert workflow.retained_installation_image("bad-artifact") is None
    workflow.clear_workflow_state()


def test_attempt_persistence_redacts_diagnostics_and_allowlists_phases(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "sanitize.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    stage = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "sanitize-stage", "request-sanitize-001", stage="installation")
    unsafe = stage.model_copy(update={
        "status": "unknown", "failure_phase": "invented_phase",
        "failure_detail": "Authorization: Bearer very-secret",
        "stdout": "url https://user:pass@example.invalid/path?token=secret TOKEN_VALUE=secret-value",
        "limitation": "safe result; NEBIUS_API_KEY=secret-value",
    })
    assert workflow.save_verification_attempt_result(unsafe)
    stored = workflow.get_verification_attempt("sanitize-stage")
    combined = str(stored.model_dump())
    assert stored.failure_phase == "unknown"
    assert "very-secret" not in combined and "secret-value" not in combined and "user:pass" not in combined
    assert "[redacted" in combined
    workflow.clear_workflow_state()


def test_import_operation_is_fixed_and_cannot_install_or_resolve():
    script = _IMPORT_SCRIPT_TEMPLATE
    compile(script, "<bugsy-imports>", "exec")
    kwargs = build_sdk_import_kwargs("3.12", [("requests", "requests")])
    assert kwargs["command"] == "/usr/local/bin/python"
    assert kwargs["args"] == ["-c", script]
    assert kwargs["env"]["BUGSY_IMPORT_MODULES"] == '[["requests","requests"]]'
    assert kwargs["timeout"] == 180 and kwargs["truncate_output_at"] == 24000
    assert kwargs["disposable"] is True
    assert "importlib.import_module" in script
    assert "pip" not in script and "uv" not in script and "subprocess" not in script
    try:
        build_sdk_import_kwargs("3.12", [("requests", "arbitrary_module")])
    except ValueError:
        pass
    else:
        raise AssertionError("Non-allowlisted import module was accepted.")
    assert sanitize_installation_diagnostics("import_operation_wait", "transport unavailable") == ("import_operation_wait", "transport unavailable")


def test_installation_artifact_retention_is_explicit_in_sdk_kwargs():
    requirements = "requests==2.32.0\n"
    assert build_sdk_installation_kwargs(requirements, "3.12")["disposable"] is True
    retained = build_sdk_installation_kwargs(requirements, "3.12", retain_artifact=True)
    assert retained["disposable"] is False
    assert retained["files"]["/tmp/requirements.txt"] == requirements.encode()


def _operation_result(marker, *, image_uuid=None, status="SUCCESS", exit_code=0):
    class Stream:
        def __init__(self, value):
            self.value = value
        def as_text(self):
            return self.value

    process = type("ProcessResult", (), {
        "stdout": Stream(marker), "stderr": Stream(""),
        "state": type("State", (), {"exit_code": exit_code})(),
    })()
    return type("Operation", (), {
        "status": status, "result_image_uuid": image_uuid,
        "metadata": type("Metadata", (), {"result": process})(),
    })()


def test_installation_adapter_returns_image_only_after_inspected_success(contree_client_fake):
    marker = "BUGSY_INSTALLATION=" + json.dumps({
        "status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"},
        "exit_code": 0, "uv_version": "0.12.19", "stdout": "installed", "stderr": "",
    })
    operation = _operation_result(marker)
    calls, image_uuid = contree_client_fake(operation=operation)
    operation.result_image_uuid = image_uuid
    result = ContreeInstallationAdapter(config=Settings(
        sandbox_enabled=True, contree_base_url="https://api.tokenfactory.nebius.com/sandboxes",
        contree_project_id="aiproject-e00z54tbndczy12c4z", nebius_api_key="fake",
    )).install("requests==2.32.0\n", "3.12", retain_artifact=True)
    assert result["status"] == "passed" and result["sandbox_image_ref"] == image_uuid
    assert calls["clients"][0][1]["base_url"] == "https://api.tokenfactory.nebius.com/sandboxes"
    assert calls["clients"][0][1]["project"] == "aiproject-e00z54tbndczy12c4z"
    assert calls["clients"][0][1]["timeout"] == 30.0
    assert calls["clients"][0][1]["retry"].kwargs == {"max_attempts": 1, "retry_unsafe": False}
    assert calls["uploads"] == [b"requests==2.32.0\n"]
    assert len(calls["submissions"]) == len(calls["waits"]) == 1
    command, image, kwargs = calls["submissions"][0]
    assert command == "/usr/local/bin/python" and image == "tag:python:3.12-slim"
    assert kwargs["shell"] is False and kwargs["disposable"] is False
    assert kwargs["timeout"] == 180 and kwargs["truncate_output_at"] == 24000
    assert kwargs["files"]["/tmp/requirements.txt"].kwargs == {"uuid": "staged-file-uuid", "mode": "0644"}
    assert calls["inspections"] == [image_uuid]


@pytest.mark.parametrize("image_uuid", [None, "not-a-uuid"])
def test_installation_does_not_retain_missing_or_invalid_image(contree_client_fake, image_uuid):
    marker = "BUGSY_INSTALLATION=" + json.dumps({
        "status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"},
        "exit_code": 0, "uv_version": "0.12.19", "stdout": "installed", "stderr": "",
    })
    calls, _ = contree_client_fake(operation=_operation_result(marker, image_uuid=image_uuid))
    result = ContreeInstallationAdapter(config=Settings(
        sandbox_enabled=True, contree_base_url="https://sandbox.invalid", nebius_api_key="fake",
    )).install("requests==2.32.0\n", "3.12", retain_artifact=True)
    assert result["status"] == "passed"
    assert result.get("sandbox_image_ref") is None
    assert calls["inspections"] == []


def test_installation_uses_validated_nested_image_fallback(contree_client_fake):
    image_uuid = str(uuid4())
    marker = "BUGSY_INSTALLATION=" + json.dumps({
        "status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"},
        "exit_code": 0, "uv_version": "0.12.19", "stdout": "installed", "stderr": "",
    })
    operation = _operation_result(marker, image_uuid="malformed")
    operation.result = type("Result", (), {"image": image_uuid})()
    calls, _ = contree_client_fake(operation=operation)
    result = ContreeInstallationAdapter(config=Settings(
        sandbox_enabled=True, contree_base_url="https://sandbox.invalid", nebius_api_key="fake",
    )).install("requests==2.32.0\n", "3.12", retain_artifact=True)
    assert result["sandbox_image_ref"] == image_uuid
    assert calls["inspections"] == [image_uuid]


def test_installation_requires_inspection_to_confirm_retained_image(contree_client_fake):
    image_uuid = str(uuid4())
    marker = "BUGSY_INSTALLATION=" + json.dumps({
        "status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"},
        "exit_code": 0, "uv_version": "0.12.19", "stdout": "installed", "stderr": "",
    })
    calls, _ = contree_client_fake(
        operation=_operation_result(marker, image_uuid=image_uuid), inspected_uuid="not-a-uuid",
    )
    result = ContreeInstallationAdapter(config=Settings(
        sandbox_enabled=True, contree_base_url="https://sandbox.invalid", nebius_api_key="fake",
    )).install("requests==2.32.0\n", "3.12", retain_artifact=True)
    assert result["status"] == "passed"
    assert result.get("sandbox_image_ref") is None
    assert calls["inspections"] == [image_uuid]


def test_installation_does_not_inspect_or_retain_when_operation_is_not_success(contree_client_fake):
    image_uuid = str(uuid4())
    calls, _ = contree_client_fake(
        operation=_operation_result("", image_uuid=image_uuid, status="FAILED", exit_code=1),
    )
    result = ContreeInstallationAdapter(config=Settings(
        sandbox_enabled=True, contree_base_url="https://sandbox.invalid", nebius_api_key="fake",
    )).install("requests==2.32.0\n", "3.12", retain_artifact=True)
    assert result["status"] == "failed"
    assert calls["inspections"] == []


def test_import_adapter_uses_retained_image_and_fixed_import_only_command(contree_client_fake):
    image_ref = uuid4()
    marker = "BUGSY_IMPORTS=" + json.dumps({
        "status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"},
        "checks": [["requests", "requests"]], "failures": [], "exit_code": 0,
    })
    calls, _ = contree_client_fake(
        operation=_operation_result(marker), inspected_uuid=str(image_ref),
    )
    result = ContreeImportAdapter(config=Settings(
        sandbox_enabled=True, contree_base_url="https://sandbox.invalid", nebius_api_key="fake",
    )).verify(str(image_ref), "3.12", [("requests", "requests")])
    assert result["status"] == "passed"
    assert calls["inspections"] == [str(image_ref)]
    assert len(calls["submissions"]) == len(calls["waits"]) == 1
    command, image, kwargs = calls["submissions"][0]
    assert command == "/usr/local/bin/python" and image == str(image_ref)
    assert kwargs["args"] == ["-c", _IMPORT_SCRIPT_TEMPLATE]
    assert kwargs["disposable"] is True and kwargs["files"] == {}
    script = kwargs["args"][1]
    assert "importlib.import_module" in script and "uv" not in script and "pip" not in script


def test_import_service_calls_only_import_adapter_and_persists_result(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "service.sqlite3")
    workflow.clear_workflow_state()
    analysis, source, proposal = ready_analysis()
    install = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "install-service", "request-install-service-01", stage="installation")
    image_ref = str(uuid4())
    passed = install.model_copy(update={
        "status": "passed", "execution_mode": "real", "exit_code": 0, "artifact_available": True,
        "environment": install.environment.model_copy(update={"sandbox_provider": "contree", "os": "Linux", "python_version": "3.12.13", "target_matches_sandbox_python": True}),
    })
    assert workflow.save_verification_attempt_result(passed, image_ref)
    imported = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "import-service", "request-import-service-01", stage="imports")
    observed = []

    class ImportsOnly:
        def verify(self, ref, target, modules):
            observed.append((ref, target, modules))
            return {"status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"}, "exit_code": 0, "stdout": "requests imported"}

    result = execute_verification_attempt(
        imported.stage_id,
        config=Settings(sandbox_enabled=True, contree_base_url="https://sandbox.invalid", nebius_api_key="fake", sandbox_retain_installation_artifact=True),
        import_adapter_factory=ImportsOnly,
    )
    assert result.status == "passed"
    assert observed == [(image_ref, "3.12", [("requests", "requests")])]
    assert workflow.get_verification_attempt("import-service").status == "passed"
    assert workflow.verification_attempt_approval_state("import-service") == ("consumed", proposal.diff_sha256, "imports")
    assert workflow.get_source(source.source_id).original_text == "requests==2.31.0\n"
    workflow.clear_workflow_state()


def test_installation_service_persists_artifact_only_after_confirmed_success(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "install-service.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    install = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "install-service-artifact", "request-install-artifact-01", stage="installation")
    image_ref = str(uuid4())

    class InstallationOnly:
        def install(self, requirements, target, *, retain_artifact, on_operation_submitted=None):
            assert requirements == proposal.proposed_requirements and target == "3.12" and retain_artifact is True
            return {"status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"}, "target_matches_sandbox_python": True, "uv_version": "0.12.19", "exit_code": 0, "sandbox_image_ref": image_ref}

    result = execute_verification_attempt(
        install.stage_id,
        config=Settings(sandbox_enabled=True, contree_base_url="https://sandbox.invalid", nebius_api_key="fake", sandbox_retain_installation_artifact=True),
        installation_adapter_factory=InstallationOnly,
    )
    assert result.status == "passed" and result.artifact_available is True
    assert workflow.retained_installation_image(install.stage_id) == image_ref
    workflow.clear_workflow_state()


def test_unknown_installation_result_is_persisted_once_without_artifact_or_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "unknown-install.sqlite3")
    workflow.clear_workflow_state()
    analysis, _, proposal = ready_analysis()
    install = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "unknown-install", "request-unknown-install-01", stage="installation")
    class TransportLost:
        calls = 0
        def install(self, *_args, **_kwargs):
            self.calls += 1
            return {"status": "unknown", "environment": {}, "exit_code": None,
                    "failure_category": "network_failure", "failure_phase": "operation_wait",
                    "failure_detail": "Sandbox result unavailable."}
    adapter = TransportLost()
    result = execute_verification_attempt(
        install.stage_id,
        config=Settings(sandbox_enabled=True, contree_base_url="https://sandbox.invalid", nebius_api_key="fake"),
        installation_adapter_factory=lambda: adapter,
    )
    assert result.status == "unknown" and result.failure_phase == "operation_wait"
    assert adapter.calls == 1 and result.artifact_available is False
    assert workflow.retained_installation_image(install.stage_id) is None
    assert workflow.verification_attempt_approval_state(install.stage_id) == ("consumed", proposal.diff_sha256, "installation")
    workflow.clear_workflow_state()
