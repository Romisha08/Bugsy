from dataclasses import replace
import base64

from app.config import settings
from app.domain.evidence import RequirementSource
from app.schemas.analysis import CompatibilityEvidence, CompatibilityFinding, CompatibilityReport, MetadataResult, ReleaseCandidate, ReleaseCandidateSet, ResolverResult
from app.services.repair import build_repair_request_preview, generate_repair_proposal, resolve_repair_proposal


def source():
    text = "demo==1.0\nother==5\n"
    return RequirementSource("source", text, "a" * 64, "3.12", (
        {"line": 1, "original": "demo==1.0", "name": "demo", "specifier": "==1.0", "extras": [], "marker": None},
        {"line": 2, "original": "other==5", "name": "other", "specifier": "==5", "extras": [], "marker": None},
    ))


def report():
    return CompatibilityReport(status="confirmed_conflict", summary="Unresolved", findings=[CompatibilityFinding(
        classification="confirmed_conflict", code="RESOLVER_UNSATISFIABLE", title="No solution",
        explanation="Explicit resolver diagnostic", packages=["demo"],
        evidence=[CompatibilityEvidence(source="uv", label="Diagnostic", value="No solution found when resolving dependencies")],
    )])


def original():
    return ResolverResult(status="unresolved", execution_mode="real", failure_category="resolver_failure", resolver="uv", resolver_version="0.12.19", python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13", target_matches_sandbox_python=True, exit_code=1, stderr="No solution found when resolving dependencies")


def cfg(**kwargs):
    return replace(settings, token_factory_enabled=True, sandbox_enabled=True, nebius_api_key="test-secret", contree_base_url="https://example.invalid", **kwargs)


def candidates(_name, _target, _limit):
    return ReleaseCandidateSet(package="demo", status="available", candidates=[
        ReleaseCandidate(candidate_id="", package="demo", version="2.0", source_url="https://pypi.org/pypi/demo/2.0/json", requires_python=">=3.10", requires_dist=["urllib3<3"]),
    ])


class Model:
    def __init__(self, content): self.content, self.payload = content, None
    def complete_json(self, _system, payload, **_kwargs): self.payload = payload; return self.content, "test-model"


def make_preview():
    return build_repair_request_preview(source(), report(), [MetadataResult(package="demo", status="available", release="1.0")], original(), config=cfg(), candidate_fetcher=candidates, preview_id="preview")


def test_model_request_preview_is_exact_and_proposal_waits_for_resolution():
    preview, by_id, error = make_preview()
    assert error is None and preview is not None
    assert preview.preview_sha256
    assert "requires_dist" in preview.evidence_json
    assert "test-secret" not in preview.evidence_json
    model = Model('{"changes":[{"package":"demo","candidate_id":"C001","finding_code":"RESOLVER_UNSATISFIABLE"}]}')
    proposal = generate_repair_proposal(source(), report(), preview, by_id, config=cfg(), adapter_factory=lambda **_: model)
    assert proposal.status == "awaiting_resolution"
    assert proposal.proposed_requirements == "demo==2.0\nother==5\n"
    assert proposal.diff_sha256 and proposal.diff
    assert proposal.candidate_evidence[0].requires_dist == ["urllib3<3"]
    assert proposal.resolver_evidence.status == "not_run"
    assert proposal.verified is False
    operation = proposal.resolver_operation
    file = operation["files"][0]
    assert operation["sandbox_image"] == "python:3.12-slim"
    assert operation["resolver_version"] == "0.12.19"
    assert operation["operation_count"] == 1 and operation["automatic_retries"] is False
    assert file["content"] == proposal.proposed_requirements
    assert base64.b64decode(file["base64"]).decode("utf-8") == proposal.proposed_requirements
    assert operation["args"][0] == "-c"
    assert "test-secret" not in model.payload


def test_model_output_must_select_supplied_candidate_and_finding():
    preview, by_id, _ = make_preview()
    invalid = Model('{"changes":[{"package":"demo","candidate_id":"C999","finding_code":"RESOLVER_UNSATISFIABLE"}]}')
    proposal = generate_repair_proposal(source(), report(), preview, by_id, config=cfg(), adapter_factory=lambda **_: invalid)
    assert proposal.status == "unavailable" and proposal.error_code == "REPAIR_MODEL_OUTPUT_INVALID"


def test_model_request_preview_hash_binds_model_endpoint_and_limits():
    preview, by_id, _ = make_preview()
    altered = preview.model_copy(update={"model_id": "nvidia/other"})
    called = []
    proposal = generate_repair_proposal(source(), report(), altered, by_id, config=cfg(), adapter_factory=lambda **_: called.append(True))
    assert proposal.error_code == "REPAIR_PREVIEW_INTEGRITY_FAILED"
    assert called == []


def test_preview_rejects_simulated_or_non_unresolved_evidence():
    simulated = original().model_copy(update={"execution_mode": "simulated"})
    assert build_repair_request_preview(source(), report(), [], simulated, preview_id="x")[2] == "SIMULATED_EVIDENCE"
    assert build_repair_request_preview(source(), report(), [], original().model_copy(update={"status": "resolved"}), preview_id="x")[2] == "REAL_UNRESOLVED_RESOLVER_EVIDENCE_REQUIRED"


def test_preview_redacts_candidate_metadata_before_display_and_model_call():
    def unsafe_candidate(*_args):
        return ReleaseCandidateSet(package="demo", status="available", candidates=[ReleaseCandidate(
            candidate_id="", package="demo", version="2.0", source_url="https://user:pass@example.invalid/json",
            requires_dist=["Bearer private-token", "API_KEY=private-value"],
        )])
    preview, _, error = build_repair_request_preview(source(), report(), [], original(), config=cfg(), candidate_fetcher=unsafe_candidate, preview_id="safe")
    assert error is None
    assert "private-token" not in preview.evidence_json
    assert "private-value" not in preview.evidence_json
    assert "user:pass" not in preview.evidence_json
    assert "[REDACTED]" in preview.evidence_json


def test_only_matching_real_zero_exit_resolution_marks_ready():
    preview, by_id, _ = make_preview()
    proposal = generate_repair_proposal(source(), report(), preview, by_id, config=cfg(), adapter_factory=lambda **_: Model('{"changes":[{"package":"demo","candidate_id":"C001","finding_code":"RESOLVER_UNSATISFIABLE"}]}'))
    class FakeResolver:
        def __init__(self, result): self.result = result; self.calls = 0
        def resolve(self, text, target): self.calls += 1; assert text == proposal.proposed_requirements and target == "3.12"; return self.result
    success = FakeResolver(ResolverResult(status="resolved", execution_mode="real", resolver="uv", resolver_version="0.12.19", python_target="3.12", sandbox_os="Linux", sandbox_python="3.12.13", target_matches_sandbox_python=True, exit_code=0))
    ready = resolve_repair_proposal(proposal, "3.12", resolver_factory=lambda: success)
    assert ready.status == "ready" and ready.verified is False and success.calls == 1
    assert "not run" in ready.summary
    mismatch = FakeResolver(ResolverResult(status="resolved", execution_mode="real", resolver="uv", python_target="3.12", sandbox_python="3.11.9", target_matches_sandbox_python=False, exit_code=0))
    assert resolve_repair_proposal(proposal, "3.12", resolver_factory=lambda: mismatch).status == "unavailable"
    failed = FakeResolver(ResolverResult(status="error", execution_mode="real", failure_category="unknown_failure", python_target="3.12", exit_code=1))
    assert resolve_repair_proposal(proposal, "3.12", resolver_factory=lambda: failed).status == "unavailable"
