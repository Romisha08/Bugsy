import hashlib
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.domain.evidence import RequirementSource
from app.main import app
from app.schemas.analysis import (
    AnalysisResponse, ParsedRequirement, ReleaseCandidate, RepairProposal,
    RepairResolverEvidence, ResolverResult,
)
from app.services.repair import requirements_diff
from app.services.workflow import (
    clear_workflow_state, installation_stage_approval_state,
    save_analysis, save_source,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.workflow.DB_PATH", tmp_path / "bugsy.sqlite3")
    clear_workflow_state()
    monkeypatch.setattr("app.services.verification.settings", replace(settings, sandbox_enabled=False))
    yield
    clear_workflow_state()


def create_analysis():
    original = "demo==1\n"
    diff = "--- requirements.txt (original)\n+++ requirements.txt (proposed)\n-demo==1\n+demo==2\n"
    source = RequirementSource(
        source_id="source-1", original_text=original, source_sha256="a" * 64,
        python_version="3.12",
        requirements=({"line": 1, "original": "demo==1", "name": "demo", "specifier": "==1", "extras": [], "marker": None},),
    )
    analysis = AnalysisResponse(
        analysis_id="analysis-1", status="complete", source_id=source.source_id, python_target="3.12",
        requirements=[ParsedRequirement(line=1, original="demo==1", name="demo", specifier="==1", extras=[], marker=None)],
        repair_proposal=RepairProposal(
            status="ready", proposal_id="proposal-1", summary="A proposed change.",
            original_sha256=source.source_sha256, diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
            proposed_requirements="demo==2\n", diff=diff, verified=False,
        ),
    )
    save_source(source)
    save_analysis(analysis)
    return analysis, source


def test_verification_requires_explicit_approval_and_exact_diff_hash():
    analysis, _ = create_analysis()
    response = client.post(f"/api/v1/analyses/{analysis.analysis_id}/verification", json={
        "proposal_id": "proposal-1", "approved": False, "diff_sha256": analysis.repair_proposal.diff_sha256,
    })
    assert response.status_code == 422
    mismatch = client.post(f"/api/v1/analyses/{analysis.analysis_id}/verification", json={
        "proposal_id": "proposal-1", "approved": True, "diff_sha256": "0" * 64,
    })
    assert mismatch.status_code == 409


def test_disabled_sandbox_returns_not_run_checks_and_does_not_claim_success(monkeypatch):
    analysis, source = create_analysis()
    monkeypatch.setattr("app.adapters.contree.ContreeVerificationAdapter.verify", lambda *_: (_ for _ in ()).throw(AssertionError("must not call sandbox")))
    started = client.post(f"/api/v1/analyses/{analysis.analysis_id}/verification", json={
        "proposal_id": "proposal-1", "approved": True, "diff_sha256": analysis.repair_proposal.diff_sha256,
    })
    assert started.status_code == 202
    verification_id = started.json()["verification_id"]
    result = client.get(f"/api/v1/verifications/{verification_id}").json()
    assert result["status"] == "unavailable"
    assert result["execution_mode"] == "not_run"
    assert result["environment"]["os"] is None
    assert all(result["checks"][key]["status"] == "not_run" for key in ("resolution", "installation", "imports"))
    assert result["checks"]["project_tests"]["status"] == "not_run"
    assert result["checks"]["project_tests"]["reason"] == "Project code not provided."
    assert source.original_text == "demo==1\n"


def test_download_requires_separate_exact_diff_approval_and_preserves_source():
    analysis, source = create_analysis()
    path = f"/api/v1/analyses/{analysis.analysis_id}"
    assert client.get(path + "/download").status_code == 403
    bad = client.post(path + "/approval", json={
        "proposal_id": "proposal-1", "approved": True, "diff_sha256": "0" * 64,
    })
    assert bad.status_code == 409
    approved = client.post(path + "/approval", json={
        "proposal_id": "proposal-1", "approved": True, "diff_sha256": analysis.repair_proposal.diff_sha256,
    })
    assert approved.status_code == 200
    download = client.get(path + "/download")
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="requirements.txt"'
    assert download.text == "demo==2\n"
    assert source.original_text == "demo==1\n"


def create_resolver_accepted_analysis():
    original = "demo==1\n"
    proposed = "demo==2\n"
    diff = requirements_diff(original, proposed)
    source_hash = hashlib.sha256(original.encode()).hexdigest()
    source = RequirementSource(
        source_id="source-install", original_text=original, source_sha256=source_hash,
        python_version="3.12", requirements=({"line":1,"original":original.strip(),"name":"demo","specifier":"==1","extras":[],"marker":None},),
    )
    candidate = ReleaseCandidate(candidate_id="C001", package="demo", version="2", source_url="https://pypi.org/pypi/demo/2/json", requires_python=">=3.10", requires_dist=[], yanked=False)
    proposal = RepairProposal(
        status="ready", proposal_id="proposal-install", summary="Resolver accepted.",
        original_sha256=source_hash, diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
        proposed_requirements=proposed, diff=diff, candidate_evidence=[candidate],
        resolver_evidence=RepairResolverEvidence(status="accepted", resolver="uv", resolver_version="0.12.19", exit_code=0, python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13"),
        resolver_diagnostics=ResolverResult(status="resolved", execution_mode="real", resolver="uv", resolver_version="0.12.19", exit_code=0, python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13", target_matches_sandbox_python=True, resolved_requirements=proposed),
    )
    analysis = AnalysisResponse(
        analysis_id="analysis-install", status="complete", source_id=source.source_id,
        python_target="3.12", provenance={"source":"saved_resolver_capture","capture_id":"capture-1","classification":"requirements_sha256"},
        requirements=[ParsedRequirement(line=1, original="demo==1", name="demo", specifier="==1", extras=[], marker=None)],
        repair_proposal=proposal,
    )
    save_source(source)
    save_analysis(analysis)
    return analysis


def test_unsupported_persisted_target_is_rejected_before_installation_approval(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    analysis = analysis.model_copy(update={"python_target": "3.9"})
    save_analysis(analysis)
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(
        settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid",
    ))
    monkeypatch.setattr(
        "app.api.routes.verifications.claim_verification_attempt",
        lambda *_args, **_kwargs: pytest.fail("unsupported target must be rejected before claiming approval"),
    )

    response = client.post("/api/v1/analyses/analysis-install/verification/installation/attempts", json={
        "proposal_id": "proposal-install", "approved": True,
        "diff_sha256": analysis.repair_proposal.diff_sha256,
        "request_id": "request-unsupported-target",
    })

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_PYTHON_TARGET"


def test_installation_stage_approval_is_persisted_and_duplicate_rejected(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    proposal = analysis.repair_proposal
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(
        settings, sandbox_enabled=True, token_factory_enabled=False,
        nebius_api_key="test-only", contree_base_url="https://sandbox.invalid",
    ))
    monkeypatch.setattr("app.api.routes.verifications._run_installation_stage", lambda _stage_id: None)
    url = f"/api/v1/analyses/{analysis.analysis_id}/verification/installation"
    body = {"approved": True, "stage": "installation", "proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256}

    started = client.post(url, json=body)
    assert started.status_code == 202
    stage = started.json()
    assert stage["status"] == "running"
    assert stage["approval_state"] == "consumed"
    assert stage["stage"] == "installation"
    assert installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)
    assert client.get(f"/api/v1/verification-stages/{stage['stage_id']}").json()["status"] == "running"

    duplicate = client.post(url, json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "INSTALLATION_STAGE_STALE_OR_CONSUMED"


def test_sandbox_disabled_cannot_consume_legacy_or_fresh_install_approval(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    proposal = analysis.repair_proposal
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(
        settings, sandbox_enabled=False, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid",
    ))
    legacy = client.post(f"/api/v1/analyses/{analysis.analysis_id}/verification/installation", json={
        "approved": True, "stage": "installation", "proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256,
    })
    fresh = client.post(f"/api/v1/analyses/{analysis.analysis_id}/verification/installation/attempts", json={
        "approved": True, "stage": "installation", "proposal_id": proposal.proposal_id,
        "diff_sha256": proposal.diff_sha256, "request_id": "sandbox-disabled-001",
    })
    assert legacy.status_code == fresh.status_code == 503
    assert installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) is None


def test_fresh_install_preview_exposes_exact_bytes_and_retention_setting(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    proposal = analysis.repair_proposal
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(settings, sandbox_enabled=False, sandbox_retain_installation_artifact=False))
    preview = client.get(f"/api/v1/analyses/{analysis.analysis_id}/verification/installation/preview", params={"proposal_id": proposal.proposal_id})
    assert preview.status_code == 200
    body = preview.json()
    assert body["requirements_bytes_base64"]
    assert body["requirements_sha256"] == hashlib.sha256(proposal.proposed_requirements.encode()).hexdigest()
    assert body["sandbox_enabled"] is False
    assert body["retain_image"] is False and body["image_disposable"] is True
    assert body["timeout_seconds"] == 180 and body["output_limit_bytes"] == 24000


def test_fresh_install_attempt_consumes_new_approval_and_preserves_legacy_unknown(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    proposal = analysis.repair_proposal
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(
        settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid",
    ))
    monkeypatch.setattr("app.api.routes.verifications._run_verification_attempt", lambda _stage_id: None)
    from app.services import workflow
    legacy = workflow.claim_installation_stage(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "legacy-unknown")
    workflow.save_installation_stage_result(legacy.model_copy(update={"status": "unknown", "failure_category": "unknown_failure"}))
    endpoint = f"/api/v1/analyses/{analysis.analysis_id}/verification/installation/attempts"
    body = {"approved": True, "stage": "installation", "proposal_id": proposal.proposal_id,
            "diff_sha256": proposal.diff_sha256, "request_id": "fresh-install-approval-001"}
    started = client.post(endpoint, json=body)
    assert started.status_code == 202
    duplicate = client.post(endpoint, json=body)
    assert duplicate.status_code == 409
    rows = client.get(endpoint, params={"proposal_id": proposal.proposal_id}).json()
    assert [row["stage_id"] for row in rows] == ["legacy-unknown", started.json()["stage_id"]]
    assert rows[0]["status"] == "unknown" and rows[0]["approval_state"] == "consumed"
    from app.services.workflow import verification_attempt_approval_state
    assert verification_attempt_approval_state(started.json()["stage_id"]) == ("consumed", proposal.diff_sha256, "installation")


def test_import_approval_requires_retained_success_and_has_separate_stage(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    proposal = analysis.repair_proposal
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(
        settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid",
    ))
    path = f"/api/v1/analyses/{analysis.analysis_id}/verification/imports"
    preview = client.get(path + "/preview", params={"proposal_id": proposal.proposal_id})
    assert preview.status_code == 409
    denied = client.post(path + "/attempts", json={
        "approved": True, "stage": "imports", "proposal_id": proposal.proposal_id,
        "diff_sha256": proposal.diff_sha256, "request_id": "import-before-install-001",
    })
    assert denied.status_code == 409


def test_import_preview_and_approval_bind_to_successful_retained_image(monkeypatch):
    from uuid import uuid4
    from app.services import workflow
    from app.services.workflow import verification_attempt_approval_state

    analysis = create_resolver_accepted_analysis()
    analysis = analysis.model_copy(update={"requirements": [
        ParsedRequirement(line=1, original="requests==1", name="requests", specifier="==1", extras=[], marker=None),
    ]})
    save_analysis(analysis)
    proposal = analysis.repair_proposal
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(
        settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid",
    ))
    monkeypatch.setattr("app.api.routes.verifications._run_verification_attempt", lambda _stage_id: None)
    install = workflow.claim_verification_attempt(analysis.analysis_id, proposal.proposal_id, proposal.diff_sha256, "passed-install", "request-passed-install-01", stage="installation")
    image_ref = str(uuid4())
    passed = install.model_copy(update={
        "status": "passed", "execution_mode": "real", "exit_code": 0, "artifact_available": True,
        "environment": install.environment.model_copy(update={
            "sandbox_provider": "contree", "os": "Linux", "python_version": "3.12.13", "target_matches_sandbox_python": True,
        }),
    })
    assert workflow.save_verification_attempt_result(passed, image_ref)
    path = f"/api/v1/analyses/{analysis.analysis_id}/verification/imports"
    preview = client.get(path + "/preview", params={"proposal_id": proposal.proposal_id})
    assert preview.status_code == 200
    body = preview.json()
    assert body["sandbox_image_reference"] == image_ref
    assert body["import_mappings"] == [{"package": "requests", "module": "requests"}]
    assert "uv" not in body["args"][1] and "pip" not in body["args"][1]
    started = client.post(path + "/attempts", json={
        "approved": True, "stage": "imports", "proposal_id": proposal.proposal_id,
        "diff_sha256": proposal.diff_sha256, "request_id": "import-approval-0001",
    })
    assert started.status_code == 202
    assert started.json()["stage"] == "imports"
    assert verification_attempt_approval_state(started.json()["stage_id"]) == ("consumed", proposal.diff_sha256, "imports")
    duplicate = client.post(path + "/attempts", json={
        "approved": True, "stage": "imports", "proposal_id": proposal.proposal_id,
        "diff_sha256": proposal.diff_sha256, "request_id": "import-approval-0001",
    })
    assert duplicate.status_code == 409


def test_latest_installation_stage_lookup_is_read_only_and_returns_consumed_record(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    proposal = analysis.repair_proposal
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(
        settings, sandbox_enabled=True, token_factory_enabled=False,
        nebius_api_key="test-only", contree_base_url="https://sandbox.invalid",
    ))
    monkeypatch.setattr("app.api.routes.verifications._run_installation_stage", lambda _stage_id: None)
    endpoint = f"/api/v1/analyses/{analysis.analysis_id}/verification/installation"
    empty = client.get(endpoint, params={"proposal_id": proposal.proposal_id})
    assert empty.status_code == 200 and empty.json() is None
    assert installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) is None

    started = client.post(endpoint, json={
        "approved": True, "stage": "installation", "proposal_id": proposal.proposal_id,
        "diff_sha256": proposal.diff_sha256,
    })
    assert started.status_code == 202
    lookup = client.get(endpoint, params={"proposal_id": proposal.proposal_id})
    assert lookup.status_code == 200
    assert lookup.json()["stage_id"] == started.json()["stage_id"]
    assert lookup.json()["approval_state"] == "consumed"
    assert installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) == ("consumed", proposal.diff_sha256)
    assert client.get(endpoint, params={"proposal_id": "stale-proposal"}).status_code == 404


def test_installation_stage_rejects_hash_mismatch_without_consuming_approval(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(
        settings, sandbox_enabled=False, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid",
    ))
    url = f"/api/v1/analyses/{analysis.analysis_id}/verification/installation"
    response = client.post(url, json={
        "approved": True, "stage": "installation", "proposal_id": "proposal-install", "diff_sha256": "0" * 64,
    })
    assert response.status_code == 409
    assert installation_stage_approval_state(analysis.analysis_id, "proposal-install") is None


def test_installation_stage_requires_backend_configuration_before_consuming_approval(monkeypatch):
    analysis = create_resolver_accepted_analysis()
    proposal = analysis.repair_proposal
    monkeypatch.setattr("app.api.routes.verifications.settings", replace(settings, sandbox_enabled=False, nebius_api_key=None, contree_base_url=None))
    response = client.post(f"/api/v1/analyses/{analysis.analysis_id}/verification/installation", json={
        "approved": True, "stage": "installation", "proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256,
    })
    assert response.status_code == 503
    assert installation_stage_approval_state(analysis.analysis_id, proposal.proposal_id) is None
