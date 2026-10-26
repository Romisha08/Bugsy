from dataclasses import replace

import pytest

from app.config import settings
from app.schemas.analysis import CompatibilityEvidence, CompatibilityFinding, CompatibilityReport
from app.services.diagnosis import explain_report


def report(status="confirmed_conflict"):
    return CompatibilityReport(
        status=status,
        summary="The resolver explicitly reported no solution.",
        findings=[CompatibilityFinding(
            classification="confirmed_conflict", code="RESOLVER_UNSATISFIABLE", title="No solution found",
            explanation="The resolver reported no solution.", packages=[],
            evidence=[CompatibilityEvidence(source="uv", label="Resolver output", value="No solution found", package=None, source_url=None)],
        )],
    )


class FakeAdapter:
    def __init__(self, content, **kwargs):
        self.content = content
        self.kwargs = kwargs
        self.payload = None
        calls.append(self)

    def explain(self, payload):
        self.payload = payload
        return self.content, self.kwargs["model_id"]


def configured(**updates):
    return replace(settings, nebius_api_key="backend-secret", token_factory_enabled=True, **updates)


@pytest.fixture(autouse=True)
def clear_calls():
    calls.clear()


calls = []


def test_explanation_is_built_only_from_report_and_validates_evidence_references():
    content = '{"explanation":"The resolver could not find a solution.","evidence_refs":["E001"]}'
    result = explain_report(report(), config=configured(), adapter_factory=lambda **kw: FakeAdapter(content, **kw))
    assert result.status == "available"
    assert result.evidence_refs == ["E001"]
    assert "No solution found" in calls[0].payload
    assert "backend-secret" not in calls[0].payload
    assert "backend-secret" not in result.model_dump_json()


def test_missing_credentials_and_simulated_results_skip_provider():
    missing = explain_report(report(), config=replace(configured(), nebius_api_key=None), adapter_factory=FakeAdapter)
    disabled = explain_report(report(), config=replace(configured(), token_factory_enabled=False), adapter_factory=FakeAdapter)
    simulated = explain_report(report("simulated"), config=configured(), adapter_factory=FakeAdapter)
    assert missing.status == "unavailable"
    assert missing.error_code == "TOKEN_FACTORY_NOT_CONFIGURED"
    assert disabled.error_code == "TOKEN_FACTORY_DISABLED"
    assert simulated.status == "not_requested"
    assert simulated.error_code == "SIMULATED_EVIDENCE"
    assert calls == []


@pytest.mark.parametrize("content", [
    "not json",
    '{"explanation":"claim","evidence_refs":["E999"]}',
    '{"explanation":"claim","evidence_refs":[]}',
    '{"explanation":"claim","evidence_refs":[],"findings":[{}]}',
    '{"explanation":"backend-secret","evidence_refs":[]}',
    '{"explanation":"Run pip install demo","evidence_refs":[]}',
])
def test_malformed_unsupported_or_sensitive_model_output_is_unavailable(content):
    result = explain_report(report(), config=configured(), adapter_factory=lambda **kw: FakeAdapter(content, **kw))
    assert result.status == "unavailable"
    assert result.error_code == "TOKEN_FACTORY_INVALID_OUTPUT"
    assert result.text is None


def test_deterministic_report_is_immutable_and_evidence_bundle_is_bounded():
    original = report()
    expected = original.model_copy(deep=True)
    result = explain_report(original, config=configured(), adapter_factory=lambda **kw: FakeAdapter('{"explanation":"The resolver found no solution.","evidence_refs":["E001"]}', **kw))
    assert result.status == "available"
    assert original == expected

    huge = CompatibilityReport(status="unknown", summary="uncertain", findings=[
        CompatibilityFinding(classification="unknown", code=f"F{i}", title="x", explanation="y", evidence=[
            CompatibilityEvidence(source="pypi", label="metadata", value="z" * 1_200)
        ]) for i in range(50)
    ])
    not_sent = explain_report(huge, config=configured(), adapter_factory=lambda **kw: FakeAdapter("{}", **kw))
    assert not_sent.status == "unavailable"
    assert not_sent.error_code == "EVIDENCE_TOO_LARGE"
