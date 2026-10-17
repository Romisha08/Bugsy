import io
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.adapters.stub import StubResolverAdapter
from app.schemas.analysis import MetadataResult
from app.services.workflow import clear_workflow_state

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    clear_workflow_state()
    monkeypatch.setattr("app.adapters.contree.settings", replace(settings, sandbox_enabled=False))
    yield
    clear_workflow_state()


def test_paste_returns_read_only_parsed_requirements_and_hash() -> None:
    response = client.post("/api/v1/requirements/parse", json={
        "text": "requests>=2,<3\nimportlib-metadata; python_version < '3.10'\n",
        "python_version": "3.12",
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["parse_status"] == "parsed"
    assert payload["requirements"][0]["line"] == 1
    assert payload["requirements"][1]["marker"] is not None
    assert payload["source_sha256"]


def test_invalid_lines_return_422_and_block_analysis() -> None:
    response = client.post("/api/v1/requirements/parse", json={
        "text": "requests>=2\n-c constraints.txt\n",
        "python_version": "3.12",
    })

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "UNSUPPORTED_REQUIREMENTS"
    assert body["details"][0]["line"] == 2
    assert body["details"][0]["original"] == "-c constraints.txt"
    assert client.post("/api/v1/analyses", json={"source_id": "not-created"}).status_code == 404


def test_upload_accepts_requirements_txt_and_rejects_other_names() -> None:
    good = client.post(
        "/api/v1/requirements/parse",
        data={"python_version": "3.12"},
        files={"file": ("requirements.txt", io.BytesIO(b"requests==2.32.4\n"), "text/plain")},
    )
    bad = client.post(
        "/api/v1/requirements/parse",
        data={"python_version": "3.12"},
        files={"file": ("deps.txt", io.BytesIO(b"requests\n"), "text/plain")},
    )

    assert good.status_code == 200
    assert good.json()["requirements"][0]["name"] == "requests"
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "UNSUPPORTED_FILE"


def test_file_upload_enforces_size_limit(monkeypatch) -> None:
    monkeypatch.setattr("app.api.routes.requirements.settings", replace(settings, max_requirements_bytes=4))
    response = client.post(
        "/api/v1/requirements/parse",
        data={"python_version": "3.12"},
        files={"file": ("requirements.txt", io.BytesIO(b"requests\n"), "text/plain")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUIREMENTS_TOO_LARGE"


def test_analysis_keeps_pypi_unavailable_separate_from_simulated_resolution(monkeypatch) -> None:
    source = client.post("/api/v1/requirements/parse", json={"text": "missing-demo-pkg>=1\n", "python_version": "3.12"}).json()
    simulated = StubResolverAdapter("unresolved").resolve("missing-demo-pkg>=1\n", "3.12")
    monkeypatch.setattr(
        "app.api.routes.analyses.analyze_requirements",
        lambda *args: ([MetadataResult(package="missing-demo-pkg", status="unavailable", error_code="PACKAGE_NOT_FOUND", error_message="PyPI project not found")], simulated),
    )

    started = client.post("/api/v1/analyses", json={"source_id": source["source_id"]})
    finished = client.get(f"/api/v1/analyses/{started.json()['analysis_id']}")

    assert started.status_code == 202
    assert finished.status_code == 200
    report = finished.json()
    assert report["metadata"][0]["status"] == "unavailable"
    assert report["metadata"][0]["error_code"] == "PACKAGE_NOT_FOUND"
    assert report["resolver"]["status"] == "unresolved"
    assert report["resolver"]["execution_mode"] == "simulated"
