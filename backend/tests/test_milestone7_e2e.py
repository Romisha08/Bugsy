from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas.analysis import MetadataResult, ReleaseCandidate, ReleaseCandidateSet, ResolverResult
from app.services import repair, verification, workflow


def test_mocked_analysis_to_repair_to_install_and_import_journey(monkeypatch, tmp_path):
    """Exercise the persisted API workflow without contacting any external service."""
    monkeypatch.setattr(workflow, "DB_PATH", tmp_path / "milestone7.sqlite3")
    workflow.clear_workflow_state()
    test_config = replace(
        settings,
        sandbox_enabled=True,
        token_factory_enabled=True,
        nebius_api_key="test-only-key",
        token_factory_base_url="https://tokenfactory.example/v1/",
        token_factory_model_id="nvidia/test-model",
        sandbox_retain_installation_artifact=True,
    )
    monkeypatch.setattr("app.api.routes.analyses.settings", test_config)
    monkeypatch.setattr("app.api.routes.verifications.settings", test_config)

    original_resolver = ResolverResult(
        status="unresolved", execution_mode="real", failure_category="resolver_failure",
        resolver="uv", resolver_version="0.12.19", python_target="3.12",
        sandbox_os="Linux", sandbox_python="3.12.13", target_matches_sandbox_python=True,
        exit_code=1, stdout="No solution found when resolving dependencies",
    )
    monkeypatch.setattr(
        "app.api.routes.analyses.analyze_requirements",
        lambda *_: ([MetadataResult(package="requests", status="available", release="2.31.0",
                                    source_url="https://pypi.org/pypi/requests/json", requires_python=">=3.8")], original_resolver),
    )
    monkeypatch.setattr("app.api.routes.analyses.explain_report", lambda _report: None)

    def fetch_candidates(package, _target, _limit):
        return ReleaseCandidateSet(package=package, status="available", candidates=[
            ReleaseCandidate(candidate_id="", package=package, version="2.32.0",
                             source_url="https://pypi.org/pypi/requests/2.32.0/json",
                             requires_python=">=3.8", requires_dist=["urllib3<3"]),
        ])

    original_preview = repair.build_repair_request_preview

    def preview_builder(source, report, metadata, resolver, **kwargs):
        return original_preview(source, report, metadata, resolver, config=test_config,
                                candidate_fetcher=fetch_candidates, **kwargs)

    monkeypatch.setattr("app.api.routes.analyses.build_repair_request_preview", preview_builder)

    class FakeModel:
        def __init__(self, **_kwargs):
            pass

        def complete_json(self, _system, _evidence, *, max_tokens):
            assert max_tokens == 2048
            return ('{"changes":[{"package":"requests","candidate_id":"C001",'
                    '"finding_code":"RESOLVER_UNSATISFIABLE"}]}', {})

    def proposal_generator(source, report, preview, candidates, **_kwargs):
        return repair.generate_repair_proposal(
            source, report, preview, candidates, config=test_config, adapter_factory=FakeModel,
        )

    monkeypatch.setattr("app.api.routes.analyses.generate_repair_proposal", proposal_generator)

    class FakeResolver:
        def resolve(self, requirements, target):
            assert requirements == "requests==2.32.0\n"
            assert target == "3.12"
            return ResolverResult(
                status="resolved", execution_mode="real", resolver="uv", resolver_version="0.12.19",
                python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13",
                target_matches_sandbox_python=True, exit_code=0,
                resolved_requirements="requests==2.32.0\n",
            )

    original_resolve = repair.resolve_repair_proposal

    def resolver_gate(proposal, target, **_kwargs):
        return original_resolve(proposal, target, resolver_factory=FakeResolver)

    monkeypatch.setattr("app.api.routes.analyses.resolve_repair_proposal", resolver_gate)

    image_ref = str(uuid4())
    calls = {"install": 0, "imports": 0}

    class FakeInstaller:
        def install(self, requirements, target, *, retain_artifact, on_operation_submitted=None):
            calls["install"] += 1
            assert requirements == "requests==2.32.0\n"
            assert target == "3.12" and retain_artifact is True
            return {"status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"},
                    "exit_code": 0, "uv_version": "0.12.19", "sandbox_image_ref": image_ref,
                    "stdout": "Installation completed."}

    class FakeImporter:
        def verify(self, actual_image, target, modules):
            calls["imports"] += 1
            assert actual_image == image_ref and target == "3.12"
            assert modules == [("requests", "requests")]
            return {"status": "passed", "environment": {"os": "Linux", "python_version": "3.12.13"},
                    "exit_code": 0, "stdout": "Allowlisted import passed."}

    def execute_fake(stage_id):
        return verification.execute_verification_attempt(
            stage_id, config=test_config, installation_adapter_factory=FakeInstaller,
            import_adapter_factory=FakeImporter,
        )

    monkeypatch.setattr("app.api.routes.verifications._run_verification_attempt", execute_fake)

    client = TestClient(app)
    parsed = client.post("/api/v1/requirements/parse", json={"text": "requests==2.31.0\n", "python_version": "3.12"})
    assert parsed.status_code == 200
    source = parsed.json()
    started = client.post("/api/v1/analyses", json={"source_id": source["source_id"]})
    assert started.status_code == 202
    analysis = client.get(started.json()["poll_url"]).json()
    assert analysis["resolver"]["status"] == "unresolved"
    assert analysis["compatibility_report"]["status"] == "confirmed_conflict"

    preview = client.post(f"/api/v1/analyses/{analysis['analysis_id']}/repair-request-preview")
    assert preview.status_code == 200
    proposal_response = client.post(
        f"/api/v1/analyses/{analysis['analysis_id']}/repair-proposal",
        json={"approved": True, "preview_sha256": preview.json()["preview_sha256"]},
    )
    assert proposal_response.status_code == 200
    proposal = proposal_response.json()["repair_proposal"]
    assert proposal["status"] == "awaiting_resolution"
    assert proposal["proposed_requirements"] == "requests==2.32.0\n"
    assert proposal["verified"] is False

    resolved = client.post(
        f"/api/v1/analyses/{analysis['analysis_id']}/repair-proposal/{proposal['proposal_id']}/resolve",
        json={"approved": True, "proposal_id": proposal["proposal_id"], "diff_sha256": proposal["diff_sha256"]},
    )
    assert resolved.status_code == 200
    ready = resolved.json()["repair_proposal"]
    assert ready["status"] == "ready" and ready["resolver_evidence"]["status"] == "accepted"
    assert ready["verified"] is False

    install_url = f"/api/v1/analyses/{analysis['analysis_id']}/verification/installation/attempts"
    install_body = {"approved": True, "stage": "installation", "proposal_id": proposal["proposal_id"],
                    "diff_sha256": proposal["diff_sha256"], "request_id": "e2e-install-request-001"}
    install = client.post(install_url, json=install_body)
    assert install.status_code == 202 and install.json()["status"] == "running"
    install_result = client.get(f"/api/v1/verification-attempts/{install.json()['stage_id']}").json()
    assert install_result["status"] == "passed"
    assert install_result["artifact_available"] is True
    assert client.post(install_url, json=install_body).status_code == 409
    assert client.get(install_url, params={"proposal_id": proposal["proposal_id"]}).json()[0]["status"] == "passed"

    import_url = f"/api/v1/analyses/{analysis['analysis_id']}/verification/imports/attempts"
    import_preview = client.get(import_url.replace("/attempts", "/preview"), params={"proposal_id": proposal["proposal_id"]})
    assert import_preview.status_code == 200
    assert import_preview.json()["sandbox_image_reference"] == image_ref
    import_body = {"approved": True, "stage": "imports", "proposal_id": proposal["proposal_id"],
                   "diff_sha256": proposal["diff_sha256"], "request_id": "e2e-import-request-001"}
    imported = client.post(import_url, json=import_body)
    assert imported.status_code == 202 and imported.json()["status"] == "running"
    import_result = client.get(f"/api/v1/verification-attempts/{imported.json()['stage_id']}").json()
    assert import_result["status"] == "passed"
    assert client.post(import_url, json=import_body).status_code == 409
    assert calls == {"install": 1, "imports": 1}

    # Stage history remains readable after constructing a fresh application client.
    restarted_client = TestClient(app)
    install_history = restarted_client.get(install_url, params={"proposal_id": proposal["proposal_id"]}).json()
    import_history = restarted_client.get(
        f"/api/v1/analyses/{analysis['analysis_id']}/verification/imports/attempts",
        params={"proposal_id": proposal["proposal_id"]},
    ).json()
    assert len(install_history) == len(import_history) == 1
    assert install_history[0]["approval_state"] == import_history[0]["approval_state"] == "consumed"
    assert install_history[0]["environment"]["python_version"] == "3.12.13"
    assert import_history[0]["status"] == "passed"
    assert workflow.get_source(source["source_id"]).original_text == "requests==2.31.0\n"

    workflow.clear_workflow_state()
