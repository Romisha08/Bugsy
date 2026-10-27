import json
from pathlib import Path

import scripts.capture_resolver_result as driver
from app.adapters.contree import _classify_outcome, _classify_sdk_exception, _parse_result
from app.adapters.resolver_capture import MAX_CAPTURE_BYTES, _payload, capture_before_print
from app.schemas.analysis import ResolverResult


def _result(**kwargs):
    values = {
        "status": "resolved",
        "execution_mode": "real",
        "python_target": "3.12",
        "exit_code": 0,
    }
    values.update(kwargs)
    return ResolverResult(**values)


def test_successful_resolver_result_is_resolved():
    assert _classify_outcome(
        "resolution", 0, "", "warning: Temporary failure in name resolution"
    ) is None
    result = _parse_result(
        'BUGSY_RESULT={"status":"resolved","phase":"resolution","environment":'
        '{"sandbox_os":"Linux","sandbox_python":"3.12.13"},"resolver_version":'
        '"0.12.19","exit_code":0,"stdout":"","stderr":""}',
        0,
        "3.12",
    )
    assert result.status == "resolved"
    assert result.failure_category is None


def test_explicit_uv_conflict_is_unresolved():
    result = _parse_result(
        'BUGSY_RESULT={"status":"unresolved","phase":"resolution","environment":{},'
        '"exit_code":1,"stdout":"","stderr":"No solution found when resolving dependencies"}',
        1,
        "3.12",
    )
    assert result.status == "unresolved"
    assert result.failure_category == "resolver_failure"


def test_nonzero_without_conflict_or_network_evidence_is_unknown():
    assert _classify_outcome("resolution", 1, "", "unexpected uv error") == "unknown_failure"
    result = _parse_result(
        'BUGSY_RESULT={"status":"unresolved","phase":"resolution","environment":{},'
        '"exit_code":1,"stdout":"","stderr":"unexpected uv error"}',
        1,
        "3.12",
    )
    assert result.status == "error"
    assert result.failure_category == "unknown_failure"


def test_network_and_sandbox_errors_are_distinguished_from_evidence():
    assert (
        _classify_outcome(
            "resolution", 1, "", "Temporary failure in name resolution"
        )
        == "network_failure"
    )
    assert _classify_outcome("resolver_setup", 1, "", "pip setup failed") == "unknown_failure"
    assert _classify_outcome("resolver_version", 1, "", "uv version mismatch") == "sandbox_failure"

    class ApiTimeoutError(Exception):
        pass

    class OperationTimedOutError(Exception):
        pass

    assert _classify_sdk_exception(ApiTimeoutError()) == "network_failure"
    # A client timeout does not establish whether ConTree executed the operation.
    assert _classify_sdk_exception(OperationTimedOutError()) == "unknown_failure"


def test_capture_is_saved_before_console_output(tmp_path):
    path = tmp_path / "capture.json"
    result = _result()

    def printer(payload):
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == json.loads(payload)

    assert capture_before_print(result, path, printer) is True
    assert path.stat().st_size <= MAX_CAPTURE_BYTES


def test_capture_write_failure_is_reported_without_changing_resolver_status(
    tmp_path, monkeypatch
):
    result = _result()
    messages = []

    def fail_open(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", fail_open)
    saved = capture_before_print(result, tmp_path / "capture.json", messages.append)

    assert saved is False
    assert result.status == "resolved"
    assert "CAPTURE_FAILED=PermissionError" in messages[0]
    assert "result was not saved" in messages[0]
    assert json.loads(messages[1])["status"] == "resolved"


def test_serialized_capture_obeys_total_size_limit_and_truncates_diagnostics():
    result = _result(
        stdout="out" * 20_000,
        stderr="err" * 20_000,
        resolved_requirements="pkg==1\n" * 20_000,
        limitation="why" * 5_000,
    )
    payload = _payload(result)
    record = json.loads(payload)
    assert len(payload.encode("utf-8")) <= MAX_CAPTURE_BYTES
    assert len(payload.encode("utf-8")) + 1 <= MAX_CAPTURE_BYTES
    assert record["capture_truncated"] is True


def test_capture_redacts_secrets_and_excludes_environment_fields():
    payload = _payload(
        _result(
            stderr=(
                "Bearer secret-token NEBIUS_API_KEY=private-value "
                "https://user:password@example.invalid/simple"
            )
        )
    )
    assert "secret-token" not in payload
    assert "private-value" not in payload
    assert "user:password" not in payload
    assert "NEBIUS_API_KEY" not in payload
    assert '"env"' not in payload
    assert '"nebius_api_key"' not in payload


def test_driver_requires_explicit_process_flag_before_adapter_construction(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(driver, "_DESIGNATED_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(driver, "RESULTS_DIR", tmp_path)
    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)

    result, saved = driver.run_once(
        adapter_factory=lambda: calls.append("constructed"),
        printer=lambda _message: None,
    )

    assert result is None
    assert saved is False
    assert calls == []


def test_driver_validates_designated_output_path_before_adapter(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(driver, "_DESIGNATED_RESULTS_DIR", tmp_path / "approved")
    monkeypatch.setattr(driver, "RESULTS_DIR", tmp_path / "other")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")

    result, saved = driver.run_once(
        adapter_factory=lambda: calls.append("constructed"),
        printer=lambda _message: None,
    )

    assert result is None
    assert saved is False
    assert calls == []


def test_driver_invokes_adapter_once_and_does_not_retry_if_capture_fails(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(driver, "_DESIGNATED_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(driver, "RESULTS_DIR", tmp_path)
    monkeypatch.setenv("SANDBOX_ENABLED", "true")

    class FakeAdapter:
        def resolve(self, *_args):
            calls.append("resolve")
            return _result()

    def fail_open(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", fail_open)
    messages = []
    result, saved = driver.run_once(
        adapter_factory=lambda: FakeAdapter(),
        printer=messages.append,
    )

    assert result.status == "resolved"
    assert saved is False
    assert calls == ["resolve"]
    assert any("CAPTURE_FAILED=PermissionError" in message for message in messages)
