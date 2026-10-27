from dataclasses import replace

from fastapi.testclient import TestClient

from app.config import Settings, parse_cors_origins, settings
from app.main import create_app


VERCEL_ORIGIN = "https://bugsy-l99mh8tjo-romisha-biswas-projects.vercel.app"


def test_parse_cors_origins_trims_whitespace_and_optional_trailing_slashes() -> None:
    assert parse_cors_origins(f"  {VERCEL_ORIGIN}/ , http://localhost:5173/ ") == (
        VERCEL_ORIGIN,
        "http://localhost:5173",
    )


def test_parse_cors_origins_deduplicates_and_ignores_empty_entries() -> None:
    assert parse_cors_origins(f"{VERCEL_ORIGIN},, {VERCEL_ORIGIN}/ ") == (VERCEL_ORIGIN,)


def test_settings_loads_normalized_cors_origins_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", f" {VERCEL_ORIGIN}/ , http://localhost:5173 ")

    assert Settings().cors_origins == (VERCEL_ORIGIN, "http://localhost:5173")


def test_parse_cors_origins_rejects_non_origins_and_unsafe_schemes() -> None:
    assert parse_cors_origins(
        "https://example.test/path, https://user@example.test, file://example.test, *, https://example.test/?q=1"
    ) == ()


def test_allowlisted_origin_receives_cors_headers_on_preflight() -> None:
    app = create_app(replace(settings, cors_origins=parse_cors_origins(f" {VERCEL_ORIGIN}/ ")))
    client = TestClient(app)
    health = client.get("/health", headers={"Origin": VERCEL_ORIGIN})
    preflight = client.options(
        "/api/v1/requirements/parse",
        headers={
            "Origin": VERCEL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert health.status_code == 200
    assert health.headers["access-control-allow-origin"] == VERCEL_ORIGIN
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == VERCEL_ORIGIN
    assert "POST" in preflight.headers["access-control-allow-methods"]


def test_unlisted_origin_is_rejected_during_preflight() -> None:
    app = create_app(replace(settings, cors_origins=(VERCEL_ORIGIN,)))
    response = TestClient(app).options(
        "/api/v1/requirements/parse",
        headers={
            "Origin": "https://other.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
