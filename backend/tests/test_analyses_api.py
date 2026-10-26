from dataclasses import replace
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.adapters.stub import StubResolverAdapter
from app.config import settings
from app.main import app
from app.schemas.analysis import ExplanationResult, MetadataResult, RepairProposal, RepairRequestPreview, RepairResolverEvidence, ReleaseCandidate, ResolverResult
from app.services.workflow import clear_workflow_state
from app.services.repair import requirements_diff

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.workflow.DB_PATH", tmp_path / "bugsy.sqlite3")
    clear_workflow_state()
    monkeypatch.setattr("app.adapters.contree.settings", replace(settings, sandbox_enabled=False))
    yield
    clear_workflow_state()


def _analyze(monkeypatch, resolver):
    source = client.post("/api/v1/requirements/parse", json={"text": "demo>=1\n", "python_version": "3.12"}).json()
    monkeypatch.setattr("app.api.routes.analyses.analyze_requirements", lambda *_: ([MetadataResult(package="demo", status="available", source_url="https://pypi.org/pypi/demo/json", requires_python=">=3.10")], resolver))
    monkeypatch.setattr("app.api.routes.analyses.explain_report", lambda _report: ExplanationResult(status="unavailable", error_code="TOKEN_FACTORY_DISABLED"))
    started = client.post("/api/v1/analyses", json={"source_id": source["source_id"]})
    return client.get(started.json()["poll_url"])


def test_analysis_response_includes_evidence_report_without_changing_endpoint_contract(monkeypatch):
    resolver = ResolverResult(status="unresolved", execution_mode="real", failure_category="resolver_failure", resolver="uv", resolver_version="0.12.19", python_target="3.12", exit_code=1, stdout="No solution found when resolving dependencies")
    response = _analyze(monkeypatch, resolver)
    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "complete"
    assert payload["compatibility_report"]["status"] == "confirmed_conflict"
    assert payload["compatibility_report"]["findings"][0]["evidence"][0]["source"] == "uv"
    assert payload["resolver"]["status"] == "unresolved"
    assert payload["explanation"]["status"] == "unavailable"


def test_api_does_not_promote_stub_failure_to_real_conflict(monkeypatch):
    simulated = StubResolverAdapter("unresolved").resolve("demo>=1\n", "3.12")
    response = _analyze(monkeypatch, simulated)
    report = response.json()["compatibility_report"]
    assert report["status"] == "simulated"
    assert all(finding["classification"] != "confirmed_conflict" for finding in report["findings"])
    assert "SIMULATED" in report["summary"]


def test_api_attaches_explanation_without_changing_deterministic_report(monkeypatch):
    resolver = ResolverResult(status="unresolved", execution_mode="real", failure_category="resolver_failure", resolver="uv", resolver_version="0.12.19", python_target="3.12", exit_code=1, stdout="No solution found when resolving dependencies")
    original_report = None

    def explain(report):
        nonlocal original_report
        original_report = report.model_copy(deep=True)
        return ExplanationResult(status="available", text="The resolver found no solution.", evidence_refs=["E001"])

    monkeypatch.setattr("app.api.routes.analyses.explain_report", explain)
    source = client.post("/api/v1/requirements/parse", json={"text": "demo>=1\n", "python_version": "3.12"}).json()
    monkeypatch.setattr("app.api.routes.analyses.analyze_requirements", lambda *_: ([MetadataResult(package="demo", status="available")], resolver))
    started = client.post("/api/v1/analyses", json={"source_id": source["source_id"]})
    payload = client.get(started.json()["poll_url"]).json()
    assert payload["explanation"]["status"] == "available"
    assert payload["compatibility_report"] == original_report.model_dump(mode="json")


def test_repair_workflow_has_separate_preview_model_and_resolution_approvals(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.analyses.settings",
        replace(
            settings,
            sandbox_enabled=True,
            token_factory_enabled=True,
            nebius_api_key="test-only-key",
            contree_base_url="https://sandbox.invalid",
            contree_project_id="test-project",
        ),
    )
    resolver = ResolverResult(status="unresolved", execution_mode="real", failure_category="resolver_failure", resolver="uv", resolver_version="0.12.19", python_target="3.12", exit_code=1, stdout="No solution found when resolving dependencies")
    analyzed = _analyze(monkeypatch, resolver).json()
    assert analyzed["repair_proposal"] is None
    preview = RepairRequestPreview(preview_id="p1", preview_sha256="c" * 64, model_id="nvidia/test", base_url="https://tokenfactory.example/v1/", system_prompt="Choose supplied candidate IDs", evidence_json='{"candidates":[{"candidate_id":"C001"}]}', temperature=0.1, max_tokens=2048, timeout_seconds=25)
    candidate = ReleaseCandidate(candidate_id="C001", package="demo", version="2", source_url="https://pypi.org/pypi/demo/2/json")
    original_requirements = "demo>=1\n"
    proposed_requirements = "demo==2\n"
    proposal_diff = requirements_diff(original_requirements, proposed_requirements)
    monkeypatch.setattr("app.api.routes.analyses.build_repair_request_preview", lambda *_args, **_kwargs: (preview, {"C001": candidate}, None))
    monkeypatch.setattr("app.api.routes.analyses.generate_repair_proposal", lambda *_args, **_kwargs: RepairProposal(
        status="awaiting_resolution", proposal_id="proposal1", summary="Candidate ready for resolver review.",
        original_sha256=hashlib.sha256(original_requirements.encode("utf-8")).hexdigest(),
        diff_sha256=hashlib.sha256(proposal_diff.encode("utf-8")).hexdigest(),
        proposed_requirements=proposed_requirements, diff=proposal_diff,
        resolver_evidence={"status": "not_run", "python_target": "3.12"}, candidate_evidence=[candidate],
        verified=False,
    ))
    analysis_id = analyzed["analysis_id"]
    preview_response = client.post(f"/api/v1/analyses/{analysis_id}/repair-request-preview")
    assert preview_response.status_code == 200 and preview_response.json()["preview_sha256"] == "c" * 64
    body = {"approved": True, "preview_sha256": "c" * 64}
    prepared = client.post(f"/api/v1/analyses/{analysis_id}/repair-proposal", json=body)
    assert prepared.status_code == 200
    assert prepared.json()["repair_proposal"]["status"] == "awaiting_resolution"
    assert prepared.json()["repair_proposal"]["diff"] == proposal_diff
    assert client.post(f"/api/v1/analyses/{analysis_id}/repair-proposal", json=body).status_code == 409
    resolution_calls = []
    def fake_resolve(proposal, _target, **_kw):
        resolution_calls.append(proposal.proposal_id)
        return proposal.model_copy(update={"status": "ready", "resolver_evidence": RepairResolverEvidence(status="accepted", python_target="3.12", exit_code=0, resolver="uv", resolver_version="0.12.19", sandbox_python="3.12.13", sandbox_os="Linux")})
    monkeypatch.setattr("app.api.routes.analyses.resolve_repair_proposal", fake_resolve)
    diff_hash = hashlib.sha256(proposal_diff.encode("utf-8")).hexdigest()
    assert client.post(f"/api/v1/analyses/{analysis_id}/repair-proposal/proposal1/resolve", json={"approved": True, "proposal_id": "other", "diff_sha256": diff_hash}).status_code == 409
    approval = {"approved": True, "proposal_id": "proposal1", "diff_sha256": diff_hash}
    resolved = client.post(f"/api/v1/analyses/{analysis_id}/repair-proposal/proposal1/resolve", json=approval)
    assert resolved.status_code == 200 and resolved.json()["repair_proposal"]["status"] == "ready"
    assert client.post(f"/api/v1/analyses/{analysis_id}/repair-proposal/proposal1/resolve", json=approval).status_code == 409
    assert resolution_calls == ["proposal1"]
    assert client.get(f"/api/v1/analyses/{analyzed['analysis_id']}/download").status_code == 403
