import httpx

from app.services import pypi_metadata


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}
        self.request = httpx.Request("GET", "https://pypi.org/test")
        self.response = httpx.Response(status_code, request=self.request)

    def json(self):
        return self._data

    def raise_for_status(self):
        self.response.raise_for_status()


class FakeClient:
    def __init__(self, responses, **kwargs):
        self.responses = iter(responses)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url):
        return next(self.responses)


def test_metadata_fetches_release_matching_version_constraint(monkeypatch):
    project = FakeResponse(200, {"info": {"version": "3.0", "requires_python": ">=3.8"}, "releases": {"2.2": [], "2.1": []}})
    release = FakeResponse(200, {"info": {"version": "2.2", "requires_python": ">=3.9", "requires_dist": ["numpy>=1.20"]}})
    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: FakeClient([project, release]))

    result = pypi_metadata.fetch_package_metadata("Demo_Pkg", "<3")

    assert result.status == "available"
    assert result.package == "demo-pkg"
    assert result.release == "2.2"
    assert result.requires_python == ">=3.9"
    assert result.requires_dist == ["numpy>=1.20"]


def test_missing_project_is_reported_as_metadata_unavailable(monkeypatch):
    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: FakeClient([FakeResponse(404)]))

    result = pypi_metadata.fetch_package_metadata("not-a-real-package-bugsy-test", "")

    assert result.status == "unavailable"
    assert result.error_code == "PACKAGE_NOT_FOUND"
    assert "dependency conflict" not in (result.error_message or "").lower()


def test_network_failure_is_metadata_error_not_resolver_conflict(monkeypatch):
    class NetworkErrorClient(FakeClient):
        def get(self, url):
            raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: NetworkErrorClient([]))

    result = pypi_metadata.fetch_package_metadata("requests", ">=2")

    assert result.status == "error"
    assert result.error_code == "PYPI_UNAVAILABLE"
