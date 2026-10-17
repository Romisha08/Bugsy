from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_api_process_without_claiming_sandbox_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "bugsy-api"}
    assert "sandbox" not in response.json()
