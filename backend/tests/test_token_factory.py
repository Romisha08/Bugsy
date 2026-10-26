from types import SimpleNamespace

import pytest
import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.adapters.token_factory import TokenFactoryAdapter, TokenFactoryError


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.call = None

    def create(self, **kwargs):
        self.call = kwargs
        if self.error:
            raise self.error
        return self.response


def response(content="{}", finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))])


def adapter(client):
    return TokenFactoryAdapter(api_key="secret-value", base_url="https://tokenfactory.invalid/v1/", model_id="nvidia/test", timeout_seconds=25, client=client)


def test_adapter_sends_configured_model_with_bounded_completion_settings():
    fake = FakeClient(response('{"explanation":"Evidence summary","evidence_refs":[]}'))
    content, model = adapter(fake).explain('{"evidence":[]}')
    assert model == "nvidia/test"
    assert content == '{"explanation":"Evidence summary","evidence_refs":[]}'
    assert fake.call["model"] == "nvidia/test"
    assert fake.call["temperature"] == 0.1
    assert fake.call["max_tokens"] == 2048
    assert "secret-value" not in repr(fake.call)


@pytest.mark.parametrize(("finish", "code"), [("length", "TOKEN_FACTORY_INCOMPLETE_RESPONSE"), ("content_filter", "TOKEN_FACTORY_INCOMPLETE_RESPONSE")])
def test_adapter_rejects_truncated_or_filtered_completion(finish, code):
    with pytest.raises(TokenFactoryError) as error:
        adapter(FakeClient(response("partial", finish))).explain("{}")
    assert error.value.code == code


def test_adapter_rejects_missing_content_and_hides_provider_error_text():
    with pytest.raises(TokenFactoryError) as empty:
        adapter(FakeClient(response(None))).explain("{}")
    assert empty.value.code == "TOKEN_FACTORY_EMPTY_RESPONSE"

    with pytest.raises(TokenFactoryError) as failed:
        adapter(FakeClient(error=RuntimeError("provider response contained secret-value"))).explain("{}")
    assert failed.value.code == "TOKEN_FACTORY_REQUEST_FAILED"
    assert "secret-value" not in str(failed.value)


def test_adapter_maps_timeout_to_safe_code():
    timeout = APITimeoutError(request=httpx.Request("POST", "https://tokenfactory.invalid/v1/chat/completions"))
    with pytest.raises(TokenFactoryError) as error:
        adapter(FakeClient(error=timeout)).explain("{}")
    assert error.value.code == "TOKEN_FACTORY_TIMEOUT"
    assert error.value.__cause__ is None


@pytest.mark.parametrize(("cause", "expected"), [
    (type("gaierror", (OSError,), {})("secret DNS detail"), "TOKEN_FACTORY_DNS_FAILURE"),
    (ConnectionRefusedError("secret connection detail"), "TOKEN_FACTORY_CONNECTION_REFUSED"),
    (type("SSLCertVerificationError", (OSError,), {})("secret TLS detail"), "TOKEN_FACTORY_TLS_FAILURE"),
    (OSError("unclassified private transport detail"), "TOKEN_FACTORY_UNAVAILABLE"),
])
def test_connection_failures_use_allowlisted_cause_names_only(cause, expected):
    request = httpx.Request("POST", "https://tokenfactory.invalid/v1/chat/completions")
    connection_error = APIConnectionError(request=request)
    connection_error.__cause__ = cause
    with pytest.raises(TokenFactoryError) as error:
        adapter(FakeClient(error=connection_error)).explain("{}")
    assert error.value.code == expected
    assert str(error.value) == expected
    assert error.value.__cause__ is None
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(("status", "expected"), [
    (401, "TOKEN_FACTORY_HTTP_401"),
    (403, "TOKEN_FACTORY_HTTP_403"),
    (500, "TOKEN_FACTORY_HTTP_5XX"),
    (503, "TOKEN_FACTORY_HTTP_5XX"),
])
def test_http_status_failures_use_numeric_status_without_response_body(status, expected):
    request = httpx.Request("POST", "https://tokenfactory.invalid/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": "private response body"})
    failure = APIStatusError("private provider message", response=response, body={"error": "private response body"})
    with pytest.raises(TokenFactoryError) as error:
        adapter(FakeClient(error=failure)).explain("{}")
    assert error.value.code == expected
    assert str(error.value) == expected
    assert error.value.__cause__ is None
    assert "private" not in str(error.value)


def test_nebius_base_url_default_and_explicit_override(monkeypatch):
    from app.config import DEFAULT_NEBIUS_BASE_URL, Settings

    monkeypatch.delenv("NEBIUS_BASE_URL", raising=False)
    assert Settings().token_factory_base_url == "https://api.tokenfactory.nebius.com/v1/"
    monkeypatch.setenv("NEBIUS_BASE_URL", "https://override.example/v1/")
    assert Settings().token_factory_base_url == "https://override.example/v1/"
    assert DEFAULT_NEBIUS_BASE_URL == "https://api.tokenfactory.nebius.com/v1/"


def test_sdk_client_uses_disabled_retries_and_bounded_timeout(monkeypatch):
    captured = {}
    fake = FakeClient(response())

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr("app.adapters.token_factory.OpenAI", fake_openai)
    TokenFactoryAdapter(api_key="do-not-log", base_url="https://tokenfactory.invalid/v1/", model_id="nvidia/test", timeout_seconds=17)
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 17
