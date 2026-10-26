import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.domain.evidence import RequirementSource
from app.schemas.analysis import (
    AnalysisResponse, ParsedRequirement, ReleaseCandidate, RepairProposal,
    RepairResolverEvidence, ResolverResult,
)
from app.schemas.verification import InstallationStageEnvironment
from app.services.repair import requirements_diff
from app.services import workflow


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "bugsy.sqlite3")
    workflow.clear_workflow_state()
    yield workflow.DB_PATH
    workflow.clear_workflow_state()


def persistable_analysis():
    original = "demo==1\n"
    proposed = "demo==2\n"
    diff = requirements_diff(original, proposed)
    original_bytes, proposed_bytes, diff_bytes = original.encode(), proposed.encode(), diff.encode()
    source = RequirementSource(
        source_id="source-persist", original_text=original,
        source_sha256=hashlib.sha256(original_bytes).hexdigest(), python_version="3.12",
        requirements=({"line": 1, "original": "demo==1", "name": "demo", "specifier": "==1", "extras": [], "marker": None},),
    )
    candidate = ReleaseCandidate(
        candidate_id="C001", package="demo", version="2",
        source_url="https://pypi.org/pypi/demo/2/json", requires_python=">=3.10",
        requires_dist=["urllib3<3"], yanked=False,
    )
    proposal = RepairProposal(
        status="awaiting_resolution", proposal_id="proposal-persist",
        summary="Selected candidate; resolution not run.", original_sha256=source.source_sha256,
        diff_sha256=hashlib.sha256(diff_bytes).hexdigest(), proposed_requirements=proposed,
        diff=diff, candidate_evidence=[candidate],
        resolver_evidence=RepairResolverEvidence(status="not_run", python_target="3.12"),
    )
    analysis = AnalysisResponse(
        analysis_id="analysis-persist", status="complete", source_id=source.source_id,
        python_target="3.12", provenance={
            "source": "saved_resolver_capture", "capture_id": "capture-123",
            "classification": "legacy_diagnostic_match; not requirements-hash-bound",
        },
        requirements=[ParsedRequirement(line=1, original="demo==1", name="demo", specifier="==1", extras=[], marker=None)],
        repair_proposal=proposal,
    )
    return analysis, source, original_bytes, proposed_bytes, diff_bytes


def save_fixture():
    analysis, source, *_ = persistable_analysis()
    workflow.save_source(source)
    workflow.save_analysis(analysis)
    return analysis


def test_proposal_bytes_metadata_and_provenance_survive_state_reload(database):
    expected, source, original, proposed, diff = persistable_analysis()
    workflow.save_source(source)
    workflow.save_analysis(expected)

    # Workflow access opens a new SQLite connection each time; no Python cache is authoritative.
    loaded = workflow.get_analysis(expected.analysis_id)
    loaded_source = workflow.get_source(source.source_id)
    assert loaded is not None and loaded_source is not None
    assert loaded.repair_proposal.proposal_id == "proposal-persist"
    assert loaded.repair_proposal.candidate_evidence[0].version == "2"
    assert loaded.provenance["capture_id"] == "capture-123"
    assert loaded.provenance["classification"].startswith("legacy_diagnostic_match")
    assert loaded_source.original_text.encode("utf-8") == original
    assert loaded.repair_proposal.proposed_requirements.encode("utf-8") == proposed
    assert loaded.repair_proposal.diff.encode("utf-8") == diff
    assert loaded.python_target == "3.12"

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT original_requirements_bytes, proposed_requirements_bytes, diff_bytes, diff_sha256, candidate_metadata_json, provenance_json FROM analyses WHERE analysis_id=?",
            (expected.analysis_id,),
        ).fetchone()
    assert row[0:3] == (original, proposed, diff)
    assert row[3] == expected.repair_proposal.diff_sha256
    assert json.loads(row[4])[0]["candidate_id"] == "C001"
    assert json.loads(row[5])["capture_id"] == "capture-123"


def test_approval_is_bound_consumed_once_and_outcome_persists(database):
    analysis = save_fixture()
    proposal = analysis.repair_proposal
    assert workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256)
    assert workflow.resolution_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)
    assert workflow.get_analysis(analysis.analysis_id).repair_proposal.status == "running"
    assert not workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256)

    resolved = workflow.get_analysis(analysis.analysis_id).repair_proposal.model_copy(update={"status": "ready"})
    workflow.update_analysis(analysis.analysis_id, repair_proposal=resolved)
    assert workflow.get_analysis(analysis.analysis_id).repair_proposal.status == "ready"
    assert workflow.resolution_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)


@pytest.mark.parametrize("mutation", [
    "original_bytes", "proposed_bytes", "diff_bytes", "diff_hash",
    "proposal_id", "target", "candidate_metadata", "provenance", "status",
])
def test_modified_persisted_proposal_data_is_rejected_before_claim(database, mutation):
    analysis = save_fixture()
    with sqlite3.connect(database) as connection:
        if mutation == "original_bytes":
            connection.execute("UPDATE analyses SET original_requirements_bytes=? WHERE analysis_id=?", (b"demo==0\n", analysis.analysis_id))
        elif mutation == "proposed_bytes":
            connection.execute("UPDATE analyses SET proposed_requirements_bytes=? WHERE analysis_id=?", (b"demo==3\n", analysis.analysis_id))
        elif mutation == "diff_bytes":
            connection.execute("UPDATE analyses SET diff_bytes=? WHERE analysis_id=?", (b"tampered", analysis.analysis_id))
        elif mutation == "diff_hash":
            connection.execute("UPDATE analyses SET diff_sha256=? WHERE analysis_id=?", ("0" * 64, analysis.analysis_id))
        elif mutation == "proposal_id":
            connection.execute("UPDATE analyses SET proposal_id=? WHERE analysis_id=?", ("other-proposal", analysis.analysis_id))
        elif mutation == "target":
            connection.execute("UPDATE analyses SET target_python=? WHERE analysis_id=?", ("3.11", analysis.analysis_id))
        elif mutation == "candidate_metadata":
            connection.execute("UPDATE analyses SET candidate_metadata_json=? WHERE analysis_id=?", ("[]", analysis.analysis_id))
        elif mutation == "status":
            connection.execute("UPDATE analyses SET proposal_status=? WHERE analysis_id=?", ("ready", analysis.analysis_id))
        else:
            connection.execute("UPDATE analyses SET provenance_json=? WHERE analysis_id=?", ("{}", analysis.analysis_id))
    proposal = analysis.repair_proposal
    assert not workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256)
    assert workflow.resolution_approval_state(analysis.analysis_id, proposal.proposal_id) is None


def test_missing_stale_and_modified_approval_values_are_rejected(database):
    analysis = save_fixture()
    proposal = analysis.repair_proposal
    assert not workflow.claim_resolution("missing-analysis", proposal.proposal_id, proposal.diff_sha256)
    assert not workflow.claim_resolution(analysis.analysis_id, "other-proposal", proposal.diff_sha256)
    assert not workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, "0" * 64)
    assert workflow.resolution_approval_state(analysis.analysis_id, proposal.proposal_id) is None


def test_concurrent_approval_submissions_claim_only_once(database):
    analysis = save_fixture()
    proposal = analysis.repair_proposal
    def submit():
        return workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _index: submit(), range(8)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7
    assert workflow.resolution_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)


def test_database_schema_is_versioned(database):
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6


def test_restart_marks_lost_running_result_unknown_without_restoring_approval(database, monkeypatch):
    analysis = save_fixture()
    proposal = analysis.repair_proposal
    assert workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256)
    monkeypatch.setattr(workflow, "_process_is_alive", lambda _pid: False)

    restored = workflow.get_analysis(analysis.analysis_id)
    assert restored.repair_proposal.status == "unavailable"
    assert restored.repair_proposal.error_code == "RESOLVER_OUTCOME_UNKNOWN"
    assert restored.repair_proposal.resolver_evidence.status == "unknown"
    assert restored.repair_proposal.resolver_evidence.failure_category == "unknown_failure"
    assert "outcome unknown/incomplete" in restored.repair_proposal.summary
    assert "no automatic retry will occur" in restored.repair_proposal.summary
    assert workflow.resolution_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)
    assert not workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256)
    assert workflow.get_analysis(analysis.analysis_id).repair_proposal.error_code == "RESOLVER_OUTCOME_UNKNOWN"


def test_v1_approval_table_migrates_owner_pid(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE resolver_approvals (analysis_id TEXT NOT NULL, proposal_id TEXT NOT NULL, diff_sha256 TEXT NOT NULL, state TEXT NOT NULL, consumed_at TEXT NOT NULL, PRIMARY KEY (analysis_id, proposal_id))")
        connection.execute("PRAGMA user_version = 1")
    monkeypatch.setattr(workflow, "DB_PATH", path)
    assert workflow.resolution_approval_state("a", "p") is None
    with sqlite3.connect(path) as connection:
        names = {row[1] for row in connection.execute("PRAGMA table_info(resolver_approvals)")}
        assert "owner_pid" in names
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6


def test_approval_remains_consumed_after_resolver_failure_and_cannot_be_restored(database):
    analysis = save_fixture()
    proposal = analysis.repair_proposal
    assert workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256)
    failed = proposal.model_copy(update={"status": "unavailable", "error_code": "CONTREE_RESOLUTION_FAILED"})
    workflow.update_analysis(analysis.analysis_id, repair_proposal=failed)
    assert workflow.get_analysis(analysis.analysis_id).repair_proposal.error_code == "CONTREE_RESOLUTION_FAILED"
    assert workflow.resolution_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)
    assert not workflow.claim_resolution(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256)


def ready_analysis_for_installation():
    analysis, source, *_ = persistable_analysis()
    old = analysis.repair_proposal
    proposal = old.model_copy(update={
        "status": "ready",
        "resolver_evidence": RepairResolverEvidence(
            status="accepted", resolver="uv", resolver_version="0.12.19", exit_code=0,
            python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13",
        ),
        "resolver_diagnostics": ResolverResult(
            status="resolved", execution_mode="real", resolver="uv", resolver_version="0.12.19",
            exit_code=0, python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13",
            target_matches_sandbox_python=True, stdout="", stderr="", resolved_requirements="demo==2\n",
        ),
    })
    analysis = analysis.model_copy(update={"repair_proposal": proposal})
    workflow.save_source(source)
    workflow.save_analysis(analysis)
    return analysis


def test_installation_stage_approval_and_result_persist_across_database_reload(database):
    analysis = ready_analysis_for_installation()
    proposal = analysis.repair_proposal
    claimed = workflow.claim_installation_stage(
        analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "stage-pass",
    )
    assert claimed is not None and claimed.status == "running"
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)
    context = workflow.installation_stage_context("stage-pass")
    assert context is not None and context[2] == proposal.proposed_requirements

    result = claimed.model_copy(update={
        "status": "passed", "execution_mode": "real", "uv_version": "0.12.19",
        "exit_code": 0, "stdout": "installed", "environment": InstallationStageEnvironment(
            sandbox_provider="contree", os="Linux", python_version="3.12.13",
            requested_python="3.12", target_matches_sandbox_python=True,
        ),
    })
    assert workflow.save_installation_stage_result(result)
    # A fresh database read is authoritative; the stage result is not a Python cache.
    loaded = workflow.get_installation_stage("stage-pass")
    assert loaded is not None and loaded.status == "passed"
    assert loaded.proposal_id == proposal.proposal_id
    assert loaded.diff_sha256 == proposal.diff_sha256
    assert loaded.stdout == "installed"
    latest = workflow.latest_installation_stage(analysis.analysis_id, proposal.proposal_id)
    assert latest is not None and latest.stage_id == "stage-pass" and latest.status == "passed"
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)


def test_installation_stage_rejects_wrong_hash_modified_proposal_and_duplicate(database):
    analysis = ready_analysis_for_installation()
    proposal = analysis.repair_proposal
    assert workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, "0" * 64, "wrong-hash") is None
    assert workflow.claim_installation_stage(analysis.analysis_id, "other", proposal.diff_sha256, "wrong-proposal") is None
    assert workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "stage-once") is not None
    assert workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "stage-twice") is None

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE analyses SET proposed_requirements_bytes=? WHERE analysis_id=?", (b"demo==3\n", analysis.analysis_id))
    assert workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "stage-modified") is None


def test_concurrent_installation_approval_can_be_claimed_only_once(database):
    analysis = ready_analysis_for_installation()
    proposal = analysis.repair_proposal

    def claim(index):
        return workflow.claim_installation_stage(
            analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, f"concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(result is not None for result in results) == 1
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)


def test_interrupted_installation_is_recovered_as_unknown_without_retry(database, monkeypatch):
    analysis = ready_analysis_for_installation()
    proposal = analysis.repair_proposal
    assert workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "stage-interrupted")
    monkeypatch.setattr(workflow, "_PROCESS_INSTANCE_ID", "restarted-process")

    recovered = workflow.get_installation_stage("stage-interrupted")
    assert recovered is not None and recovered.status == "unknown"
    assert recovered.failure_category == "unknown_failure"
    assert "no automatic retry" in recovered.limitation
    assert workflow.installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)
    latest = workflow.latest_installation_stage(analysis.analysis_id, proposal.proposal_id)
    assert latest is not None and latest.status == "unknown"
    assert workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "stage-retry") is None


def test_failed_installation_stage_result_is_persisted(database):
    analysis = ready_analysis_for_installation()
    proposal = analysis.repair_proposal
    claimed = workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "stage-failed")
    failed = claimed.model_copy(update={
        "status": "failed", "execution_mode": "real", "failure_category": "installation_failure",
        "exit_code": 1, "stderr": "wheel build failed",
    })
    assert workflow.save_installation_stage_result(failed)
    loaded = workflow.get_installation_stage("stage-failed")
    assert loaded.status == "failed"
    assert loaded.failure_category == "installation_failure"
    assert loaded.exit_code == 1


def test_v2_database_migrates_installation_stage_tables(tmp_path, monkeypatch):
    path = tmp_path / "v2.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 2")
    monkeypatch.setattr(workflow, "DB_PATH", path)
    assert workflow.installation_stage_approval_state("a", "p") is None
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"verification_stage_history", "verification_stage_approvals"} <= tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert "owner_instance_id" in {row[1] for row in connection.execute("PRAGMA table_info(verification_stage_history)")}
