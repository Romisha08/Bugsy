import hashlib
import json

import pytest

from app.schemas.analysis import MetadataResult
from app.services import captured_analysis as service
from app.services.captured_analysis import CaptureInputError, reconstruct_analysis
from app.services.workflow import clear_workflow_state

LEGACY = "requests==2.25.0\nurllib3==2.2.0\n"
CAPTURE_ID = "resolver-result-requests-urllib3-conflict-20261018"


@pytest.fixture(autouse=True)
def clean_persisted_workflow(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.workflow.DB_PATH", tmp_path / "bugsy.sqlite3")
    clear_workflow_state()
    yield
    clear_workflow_state()


def write_legacy(directory, **changes):
    payload = {
        "status": "unresolved", "execution_mode": "real", "failure_category": "resolver_failure",
        "resolver": "uv", "resolver_version": "0.12.19", "python_target": "3.12",
        "sandbox_os": "Linux-7.0.6-x86_64-with-glibc2.41", "sandbox_python": "3.12.13",
        "target_matches_sandbox_python": True, "exit_code": 1, "stdout": "",
        "stderr": "error: No solution found when resolving dependencies\n  cause: Because requests==2.25.0 depends on urllib3>=1.21.1,<1.27 and you require requests==2.25.0, we can conclude that you require urllib3>=1.21.1,<1.27.\n         And because you require urllib3==2.2.0, we can conclude that your requirements are unsatisfiable.\n",
        "resolved_requirements": "", "limitation": "", "capture_truncated": False,
    }
    payload.update(changes)
    (directory / f"{CAPTURE_ID}.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory / f"{CAPTURE_ID}.json"


def test_legacy_capture_matches_exact_fixture_and_keeps_unresolved_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "RESULTS_DIR", tmp_path)
    write_legacy(tmp_path)
    monkeypatch.setattr(service, "fetch_package_metadata", lambda name, spec: MetadataResult(package=name, status="available", release="2.25.0"))
    result = reconstruct_analysis(CAPTURE_ID, LEGACY, "3.12")
    assert result.resolver.status == "unresolved"
    assert "legacy_diagnostic_match" in result.resolver.limitation
    assert "not requirements-hash-bound" in result.resolver.limitation
    assert result.compatibility_report.status == "confirmed_conflict"
    assert result.provenance["capture_id"] == CAPTURE_ID
    assert result.provenance["classification"] == "legacy_diagnostic_match; not requirements-hash-bound"


def test_legacy_capture_rejects_other_fixture_or_diagnostic_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "RESULTS_DIR", tmp_path)
    path = write_legacy(tmp_path)
    with pytest.raises(CaptureInputError, match="exact known fixture"):
        reconstruct_analysis(CAPTURE_ID, "requests==2.25.0\nurllib3==2.3.0\n", "3.12")
    payload = json.loads(path.read_text())
    payload["stderr"] = "No solution found"
    path.write_text(json.dumps(payload))
    with pytest.raises(CaptureInputError, match="diagnostic"):
        reconstruct_analysis(CAPTURE_ID, LEGACY, "3.12")


def test_capture_path_schema_and_total_size_are_restricted(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "RESULTS_DIR", tmp_path)
    with pytest.raises(CaptureInputError): reconstruct_analysis("../outside", LEGACY, "3.12")
    path = write_legacy(tmp_path)
    payload = json.loads(path.read_text())
    payload["api_key"] = "never"
    path.write_text(json.dumps(payload))
    with pytest.raises(CaptureInputError, match="schema"):
        reconstruct_analysis(CAPTURE_ID, LEGACY, "3.12")
    path.write_bytes(b" " * (service.MAX_CAPTURE_BYTES + 1))
    with pytest.raises(CaptureInputError, match="size"):
        reconstruct_analysis(CAPTURE_ID, LEGACY, "3.12")


def test_versioned_capture_requires_exact_requirements_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "RESULTS_DIR", tmp_path)
    capture_id = "versioned"
    payload = {
        "schema_version": 2, "requirements_sha256": hashlib.sha256(LEGACY.encode()).hexdigest(),
        "python_target": "3.12", "sandbox_image": "python:3.12-slim", "status": "unresolved",
        "execution_mode": "real", "failure_category": "resolver_failure", "resolver": "uv",
        "resolver_version": "0.12.19", "python_target": "3.12", "sandbox_os": "Linux",
        "sandbox_python": "3.12.13", "target_matches_sandbox_python": True, "exit_code": 1,
        "stdout": "", "stderr": "No solution found when resolving dependencies", "resolved_requirements": "", "limitation": "", "capture_truncated": False,
    }
    (tmp_path / f"{capture_id}.json").write_text(json.dumps(payload))
    monkeypatch.setattr(service, "fetch_package_metadata", lambda name, spec: MetadataResult(package=name, status="available"))
    assert reconstruct_analysis(capture_id, LEGACY, "3.12").resolver.status == "unresolved"
    with pytest.raises(CaptureInputError, match="not bound"):
        reconstruct_analysis(capture_id, LEGACY.replace("2.2.0", "2.3.0"), "3.12")
