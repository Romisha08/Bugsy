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
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


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
    assert result.error_message == "Bugsy could not communicate with PyPI because a network error interrupted the request."


def test_metadata_timeout_is_reported_separately_from_network_failure(monkeypatch):
    client = FakeClient([
        httpx.ReadTimeout("slow response", request=httpx.Request("GET", "https://pypi.org/pypi/requests/json")),
        httpx.ReadTimeout("slow response", request=httpx.Request("GET", "https://pypi.org/pypi/requests/json")),
    ])
    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: client)
    result = pypi_metadata.fetch_package_metadata("requests", ">=2")

    assert result.status == "error"
    assert result.error_code == "PYPI_TIMEOUT"
    assert result.error_message == "The PyPI metadata request exceeded Bugsy's configured request timeout."
    assert client.calls == 2


def test_metadata_timeout_get_retries_once_then_succeeds_with_existing_timeout_values(monkeypatch):
    project = FakeResponse(200, {"info": {"version": "3.0"}, "releases": {"3.0": []}})
    release = FakeResponse(200, {"info": {"version": "3.0", "requires_python": ">=3.8"}})
    client = FakeClient([
        httpx.ReadTimeout("transient timeout", request=httpx.Request("GET", "https://pypi.org/pypi/demo/json")),
        project,
        release,
    ])
    timeout_values = []

    def make_client(**kwargs):
        timeout_values.append(kwargs["timeout"])
        return client

    monkeypatch.setattr(pypi_metadata.httpx, "Client", make_client)
    result = pypi_metadata.fetch_package_metadata("demo", "")

    assert result.status == "available"
    assert client.calls == 3  # one timed-out GET, its single retry, then the release GET
    assert timeout_values[0].connect == pypi_metadata.PYPI_CONNECT_TIMEOUT_SECONDS
    assert timeout_values[0].read == pypi_metadata.PYPI_REQUEST_TIMEOUT_SECONDS


def test_non_timeout_network_error_is_not_retried(monkeypatch):
    client = FakeClient([
        httpx.ConnectError("offline", request=httpx.Request("GET", "https://pypi.org/pypi/requests/json")),
    ])
    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: client)

    result = pypi_metadata.fetch_package_metadata("requests", ">=2")

    assert result.status == "error"
    assert result.error_code == "PYPI_UNAVAILABLE"
    assert client.calls == 1


def test_metadata_second_timeout_remains_incomplete_safe_error(monkeypatch):
    client = FakeClient([
        httpx.TimeoutException("first sensitive timeout"),
        httpx.TimeoutException("second sensitive timeout"),
    ])
    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: client)

    result = pypi_metadata.fetch_package_metadata("idna", "")

    assert result.status == "error"
    assert result.error_code == "PYPI_TIMEOUT"
    assert client.calls == 2
    assert "sensitive timeout" not in (result.error_message or "")


def test_release_candidates_distinguish_timeout_and_connection_error(monkeypatch):
    class TimeoutClient(FakeClient):
        def get(self, url):
            raise httpx.ConnectTimeout("slow connection", request=httpx.Request("GET", url))

    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: TimeoutClient([]))
    timeout_result = pypi_metadata.fetch_release_candidates("requests", "3.12")
    assert timeout_result.status == "error"
    assert timeout_result.error_code == "PYPI_TIMEOUT"

    class ConnectionErrorClient(FakeClient):
        def get(self, url):
            raise httpx.ConnectError("offline", request=httpx.Request("GET", url))

    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: ConnectionErrorClient([]))
    network_result = pypi_metadata.fetch_release_candidates("requests", "3.12")
    assert network_result.status == "error"
    assert network_result.error_code == "PYPI_UNAVAILABLE"


def test_release_candidate_metadata_get_retries_a_timeout_only_once(monkeypatch):
    project = FakeResponse(200, {
        "info": {"version": "2.0"},
        "releases": {"2.0": [{"yanked": False}]},
    })
    release = FakeResponse(200, {
        "info": {"version": "2.0", "requires_python": ">=3.8", "requires_dist": []},
    })
    client = FakeClient([
        httpx.ReadTimeout("temporary metadata timeout"),
        project,
        release,
    ])
    monkeypatch.setattr(pypi_metadata.httpx, "Client", lambda **kwargs: client)

    result = pypi_metadata.fetch_release_candidates("demo", "3.12")

    assert result.status == "available"
    assert [candidate.version for candidate in result.candidates] == ["2.0"]
    assert client.calls == 3
