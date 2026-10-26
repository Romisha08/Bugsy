import json
import platform
import sqlite3
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from contree_client.exceptions import APIConnectionError

from app.adapters.stub import StubResolverAdapter
from app.adapters.contree import ContreeResolverAdapter, _parse_result, _resolver_script, build_sdk_run_kwargs
from app.config import settings
from app.schemas.analysis import AnalysisResponse, MetadataResult
from app.services import compatibility


def test_conflicting_input_fixture_is_only_a_simulated_unresolved_result(monkeypatch):
    requirements = (
        {"name": "demo-a", "specifier": "==1", "line": 1, "original": "demo-a==1", "extras": [], "marker": None},
        {"name": "demo-b", "specifier": "==1", "line": 2, "original": "demo-b==1", "extras": [], "marker": None},
    )
    monkeypatch.setattr(compatibility, "inspect_metadata", lambda _req: [
        MetadataResult(package="demo-a", status="available"),
        MetadataResult(package="demo-b", status="available"),
    ])
    monkeypatch.setattr(compatibility, "ContreeResolverAdapter", lambda: StubResolverAdapter("unresolved"))

    metadata, resolver = compatibility.analyze_requirements("demo-a==1\ndemo-b==1\n", requirements, "3.12")

    assert len(metadata) == 2
    assert resolver.status == "unresolved"
    assert resolver.execution_mode == "simulated"
    assert "not a real dependency resolution" in resolver.limitation
    assert "SIMULATED" in resolver.stdout


def test_sdk_request_uses_only_fixed_argv_and_supported_file_input():
    original = "dangerous; text\n"

    request = build_sdk_run_kwargs(original, "3.12")

    assert request["command"] == "/usr/local/bin/python"
    assert request["args"][0] == "-c"
    assert "shell" not in request
    assert request["files"] == {"/tmp/requirements.txt": original.encode()}
    assert original not in request["args"][1]
    assert request["env"] == {"BUGSY_PYTHON_TARGET": "3.12"}


def test_target_image_mapping_ignores_global_image_override(monkeypatch):
    from app.config import Settings, sandbox_image_for_python_target

    monkeypatch.setenv("SANDBOX_IMAGE", "python:3.11-slim")
    for target in ("3.10", "3.11", "3.12", "3.13", "3.14"):
        assert sandbox_image_for_python_target(target) == f"python:{target}-slim"
    assert not hasattr(Settings(), "sandbox_image")
    for unsupported in ("3.9", "3.15", "3.12.14", "4.0", "abc"):
        with pytest.raises(ValueError, match="supported Python targets"):
            sandbox_image_for_python_target(unsupported)
        with pytest.raises(ValueError):
            build_sdk_run_kwargs("idna==3.10\n", unsupported)


def test_resolver_uses_target_image_and_rejects_unsupported_before_client(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    observed = {}
    monkeypatch.setenv("SANDBOX_IMAGE", "python:3.11-slim")
    monkeypatch.setattr("app.adapters.contree._create_contree_client", lambda _config: object())

    def execute(_client, image, _kwargs, *, set_phase):
        observed["image"] = image
        set_phase("operation_tracking")
        return "operation-1", SimpleNamespace(status="SUCCESS")

    result_json = {
        "status": "resolved", "phase": "resolution",
        "environment": {"sandbox_os": "Linux", "sandbox_python": "3.12.14"},
        "resolver_version": "0.12.19", "exit_code": 0, "stdout": "", "stderr": "",
    }
    monkeypatch.setattr("app.adapters.contree._execute_fixed_operation", execute)
    monkeypatch.setattr("app.adapters.contree._operation_process_result", lambda _op: (
        "BUGSY_RESULT=" + json.dumps(result_json), "", 0,
    ))
    config = replace(settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid")

    result = ContreeResolverAdapter(config=config).resolve("idna==3.10\n", "3.12")
    assert observed["image"] == "python:3.12-slim"
    assert result.sandbox_image == "python:3.12-slim"
    assert result.sandbox_python == "3.12.14"
    assert result.target_matches_sandbox_python is True

    def should_not_create_client(_config):
        pytest.fail("unsupported targets must be rejected before client construction")

    monkeypatch.setattr("app.adapters.contree._create_contree_client", should_not_create_client)
    unsupported = ContreeResolverAdapter(config=config).resolve("idna==3.10\n", "3.9")
    assert unsupported.status == "not_run"
    assert unsupported.execution_mode == "not_run"
    assert "supported Python targets" in (unsupported.limitation or "")


@pytest.mark.parametrize("target", ["3.10", "3.11", "3.12", "3.13", "3.14"])
def test_resolver_script_runtime_mismatch_never_reports_success(target, monkeypatch, capsys):
    monkeypatch.setenv("BUGSY_PYTHON_TARGET", target)
    monkeypatch.setattr(platform, "platform", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("uv must not run on mismatch"))
    actual = "3.11.15" if target != "3.11" else "3.10.15"
    script = _resolver_script().replace("sys.version.split()[0]", repr(actual), 1)
    preflight = script.split("install = subprocess.run(", maxsplit=1)[0]

    with pytest.raises(SystemExit):
        exec(compile(preflight, "<bugsy-resolver-preflight>", "exec"), {})
    payload = json.loads(capsys.readouterr().out.removeprefix("BUGSY_RESULT="))
    assert payload["phase"] == "python_target_validation"
    assert payload["exit_code"] is None
    assert "dependency resolution was not run" in payload["stderr"]
    parsed = _parse_result("BUGSY_RESULT=" + json.dumps(payload), 0, target, sandbox_image=f"python:{target}-slim")
    assert parsed.status == "error"
    assert parsed.failure_phase == "python_target_validation"
    assert parsed.target_matches_sandbox_python is False


def test_resolver_script_stops_before_uv_when_sandbox_python_mismatches(monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("BUGSY_PYTHON_TARGET", "3.12")
    monkeypatch.setattr(platform, "platform", lambda: "Linux")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    script = _resolver_script().replace("sys.version.split()[0]", "'3.11.15'", 1)
    preflight = script.split("install = subprocess.run(", maxsplit=1)[0]

    with pytest.raises(SystemExit) as stopped:
        exec(compile(preflight, "<bugsy-resolver-preflight>", "exec"), {})

    assert stopped.value.code == 0
    assert calls == []
    payload = json.loads(capsys.readouterr().out.removeprefix("BUGSY_RESULT="))
    assert payload["phase"] == "python_target_validation"
    assert payload["failure_category"] == "sandbox_failure"
    assert payload["environment"]["sandbox_python"] == "3.11.15"
    assert payload["exit_code"] is None
    assert "dependency resolution was not run" in payload["stderr"]


def test_resolver_result_persists_safe_selected_image_and_runtime_mismatch(tmp_path, monkeypatch):
    from app.services.workflow import get_analysis, save_analysis

    output = {
        "phase": "python_target_validation",
        "failure_category": "sandbox_failure",
        "environment": {"sandbox_os": "Linux", "sandbox_python": "3.11.15"},
        "exit_code": None,
        "stdout": "",
        "stderr": "Sandbox Python 3.11.15 does not match requested target 3.12; dependency resolution was not run.",
    }
    result = _parse_result("BUGSY_RESULT=" + json.dumps(output), 0, "3.12", sandbox_image="python:3.12-slim")
    assert result.status == "error"
    assert result.failure_category == "sandbox_failure"
    assert result.target_matches_sandbox_python is False
    assert result.sandbox_image == "python:3.12-slim"
    assert result.exit_code is None
    assert "dependency resolution was not run" in (result.limitation or "")

    # Image references containing URL credentials or other unsafe characters are not persisted.
    unsafe = _parse_result("BUGSY_RESULT=" + json.dumps(output), 0, "3.12", sandbox_image="https://user:secret@host/image")
    assert unsafe.sandbox_image is None

    database = tmp_path / "resolver-image.sqlite3"
    monkeypatch.setattr("app.services.workflow.DB_PATH", database)
    save_analysis(AnalysisResponse(
        analysis_id="resolver-image-analysis", status="analysis_incomplete", source_id="synthetic-source",
        python_target="3.12", requirements=[], resolver=result,
    ))
    loaded = get_analysis("resolver-image-analysis")
    assert loaded is not None
    assert loaded.resolver is not None
    assert loaded.resolver.sandbox_image == "python:3.12-slim"
    assert "user:secret" not in loaded.model_dump_json()


@pytest.mark.parametrize(("target", "actual"), [
    ("3.10", "3.10.14"), ("3.11", "3.11.9"), ("3.12", "3.12.7"),
    ("3.13", "3.13.2"), ("3.14", "3.14.0"), ("3.12.7", "3.12.7"),
])
def test_parse_result_accepts_zero_exit_only_when_runtime_matches(target, actual):
    result = _parse_result("BUGSY_RESULT=" + json.dumps({
        "status": "resolved", "phase": "resolution",
        "environment": {"sandbox_os": "Linux", "sandbox_python": actual},
        "resolver_version": "0.12.19", "exit_code": 0, "stdout": "Resolved", "stderr": "",
        "resolved_requirements": "idna==3.10\n",
    }), 0, target, sandbox_image=f"python:{target.split('.')[0]}.{target.split('.')[1]}-slim")

    assert result.status == "resolved"
    assert result.failure_phase is None
    assert result.target_matches_sandbox_python is True
    assert result.sandbox_python == actual
    assert result.resolved_requirements == "idna==3.10\n"


@pytest.mark.parametrize("exit_code", [0, 1])
def test_parse_result_rejects_runtime_mismatch_even_with_resolver_output(exit_code):
    result = _parse_result("BUGSY_RESULT=" + json.dumps({
        "status": "resolved" if exit_code == 0 else "unresolved", "phase": "resolution",
        "environment": {"sandbox_os": "Linux", "sandbox_python": "3.11.15"},
        "resolver_version": "0.12.19", "exit_code": exit_code,
        "stdout": "No solution found when resolving dependencies" if exit_code else "Resolved",
        "stderr": "", "resolved_requirements": "must-not-be-accepted\n",
    }), exit_code, "3.12", sandbox_image="python:3.12-slim")

    assert result.status == "error"
    assert result.failure_phase == "python_target_validation"
    assert result.failure_category == "sandbox_failure"
    assert result.target_matches_sandbox_python is False
    assert result.resolved_requirements is None
    assert "does not match requested target 3.12" in (result.limitation or "")
    assert "must-not-be-accepted" not in result.model_dump_json()


@pytest.mark.parametrize("runtime_value", [None, "", "not-a-version Bearer private-value", "3.12"])
def test_parse_result_rejects_missing_or_malformed_runtime_information(runtime_value):
    environment = {"sandbox_os": "Linux"}
    if runtime_value is not None:
        environment["sandbox_python"] = runtime_value
    result = _parse_result("BUGSY_RESULT=" + json.dumps({
        "status": "resolved", "phase": "resolution", "environment": environment,
        "resolver_version": "0.12.19", "exit_code": 0, "stdout": "Resolved", "stderr": "",
        "resolved_requirements": "idna==3.10\n",
    }), 0, "3.12", sandbox_image="python:3.12-slim")

    assert result.status == "error"
    assert result.failure_phase == "python_target_validation"
    assert result.failure_category == "sandbox_failure"
    assert result.target_matches_sandbox_python is None
    assert result.sandbox_python is None
    assert "valid Sandbox Python version" in (result.limitation or "")
    assert "private-value" not in result.model_dump_json()
    assert result.resolved_requirements is None


def test_contree_adapter_stays_disabled_by_default(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr("app.adapters.contree.settings", replace(settings, sandbox_enabled=False))

    result = ContreeResolverAdapter().resolve("requests>=2\n", "3.12")

    assert result.status == "not_run"
    assert result.execution_mode == "not_run"
    assert result.sandbox_python is None


def test_bad_request_persists_only_allowlisted_structured_diagnostics(monkeypatch, caplog, tmp_path):
    from dataclasses import replace
    import sqlite3
    from contree_client.exceptions import BadRequestError
    from app.schemas.analysis import AnalysisResponse
    from app.services.workflow import save_analysis

    secret = "Bearer test-secret-value"
    error = BadRequestError(400, {
        "code": "INVALID_REQUEST", "field": "content_type", "message": secret,
        "authorization": secret, "unexpected": "do-not-persist",
    })
    monkeypatch.setattr("app.adapters.contree._create_contree_client", lambda _config: object())

    def fail_during_staging(_client, _image, _kwargs, *, set_phase):
        set_phase("file_staging")
        raise error

    monkeypatch.setattr("app.adapters.contree._execute_fixed_operation", fail_during_staging)
    config = replace(settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid")
    result = ContreeResolverAdapter(config=config).resolve("idna==3.10\n", "3.12")
    saved = result.model_dump_json()

    assert result.status == "error"
    assert result.failure_category == "unknown_failure"
    assert result.http_status == 400
    assert result.server_error_code == "INVALID_REQUEST"
    assert result.server_error_field == "content_type"
    assert "file_staging" in (result.limitation or "")
    assert secret not in saved and "do-not-persist" not in saved
    assert secret not in caplog.text and "do-not-persist" not in caplog.text

    database = tmp_path / "diagnostics.sqlite3"
    monkeypatch.setattr("app.services.workflow.DB_PATH", database)
    save_analysis(AnalysisResponse(
        analysis_id="diagnostics-analysis", status="analysis_incomplete", source_id="synthetic-source",
        python_target="3.12", requirements=[], resolver=result,
    ))
    with sqlite3.connect(database) as connection:
        persisted = connection.execute(
            "SELECT analysis_json FROM analyses WHERE analysis_id=?", ("diagnostics-analysis",)
        ).fetchone()[0]
    assert secret not in persisted and "do-not-persist" not in persisted
    assert '"http_status":400' in persisted
    assert '"server_error_code":"INVALID_REQUEST"' in persisted


def test_bad_request_without_structured_details_persists_only_status(monkeypatch):
    from dataclasses import replace
    from contree_client.exceptions import BadRequestError

    monkeypatch.setattr("app.adapters.contree._create_contree_client", lambda _config: object())
    monkeypatch.setattr(
        "app.adapters.contree._execute_fixed_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BadRequestError(400, "raw server response")),
    )
    config = replace(settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid")

    result = ContreeResolverAdapter(config=config).resolve("idna==3.10\n", "3.12")

    assert result.http_status == 400
    assert result.server_error_code is None
    assert result.server_error_field is None
    assert "raw server response" not in result.model_dump_json()


def test_bad_request_malformed_or_unexpected_details_are_discarded(monkeypatch):
    from dataclasses import replace
    from contree_client.exceptions import BadRequestError

    monkeypatch.setattr("app.adapters.contree._create_contree_client", lambda _config: object())
    monkeypatch.setattr(
        "app.adapters.contree._execute_fixed_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BadRequestError(
            400, {"code": ["INVALID_REQUEST"], "field": "Authorization", "request_id": "secret-id"}
        )),
    )
    config = replace(settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid")

    result = ContreeResolverAdapter(config=config).resolve("idna==3.10\n", "3.12")

    assert result.http_status == 400
    assert result.server_error_code is None
    assert result.server_error_field is None
    assert "secret-id" not in result.model_dump_json()


def _connection_error(cause=None, *, timed_out=False):
    error = APIConnectionError("Bearer fake-credential; https://private.invalid/path", timed_out=timed_out)
    if cause is not None:
        try:
            raise cause
        except Exception as underlying:
            try:
                raise error from underlying
            except APIConnectionError as wrapped:
                return wrapped
    return error


def _resolve_connection_failure(monkeypatch, error, *, failure_phase="file_staging"):
    monkeypatch.setattr("app.adapters.contree._create_contree_client", lambda _config: object())

    def fail_during_upload(_client, _image, _kwargs, *, set_phase):
        set_phase(failure_phase)
        raise error

    monkeypatch.setattr("app.adapters.contree._execute_fixed_operation", fail_during_upload)
    config = replace(settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid")
    return ContreeResolverAdapter(config=config).resolve("idna==3.10\n", "3.12")


@pytest.mark.parametrize("phase", [
    "file_staging", "operation_submission", "operation_tracking",
])
def test_sdk_failure_phase_is_structured_and_serializable(monkeypatch, phase):
    result = _resolve_connection_failure(monkeypatch, _connection_error(), failure_phase=phase)
    restored = type(result).model_validate_json(result.model_dump_json())

    assert result.failure_phase == phase
    assert restored.failure_phase == phase
    assert restored.failure_category == "network_failure"


def test_client_configuration_failure_phase_is_structured(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.contree._create_contree_client",
        lambda _config: (_ for _ in ()).throw(RuntimeError("sensitive message")),
    )
    config = replace(settings, sandbox_enabled=True, nebius_api_key="test-only", contree_base_url="https://sandbox.invalid")
    result = ContreeResolverAdapter(config=config).resolve("idna==3.10\n", "3.12")

    assert result.failure_phase == "client_configuration"
    assert result.failure_category == "unknown_failure"
    assert "sensitive message" not in result.model_dump_json()


@pytest.mark.parametrize("phase", [
    "python_target_validation", "resolver_setup", "resolver_version", "resolution",
])
def test_script_failure_phases_are_structured(phase):
    result = _parse_result(
        'BUGSY_RESULT=' + json.dumps({
            "status": "error", "phase": phase, "failure_category": "unknown_failure",
            "environment": {"sandbox_os": "Linux", "sandbox_python": "3.12.1"},
            "exit_code": 1, "stdout": "", "stderr": "fixed diagnostic",
        }),
        1, "3.12", sandbox_image="python:3.12-slim",
    )
    restored = type(result).model_validate_json(result.model_dump_json())

    assert restored.failure_phase == phase


def test_unparseable_resolver_result_has_result_parsing_phase():
    result = _parse_result("no structured record", 1, "3.12")
    assert result.failure_phase == "result_parsing"


@pytest.mark.parametrize("error_type", [
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.LocalProtocolError,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
])
def test_api_connection_error_persists_only_allowlisted_httpx_cause_type(monkeypatch, error_type):
    cause = error_type("Bearer underlying-secret", request=httpx.Request("POST", "https://private.invalid/files"))
    result = _resolve_connection_failure(monkeypatch, _connection_error(cause, timed_out=False))

    assert result.status == "error"
    assert result.failure_category == "network_failure"
    assert result.transport_error_type == error_type.__name__
    assert result.transport_cause_type == error_type.__name__
    assert result.transport_timed_out is False
    assert "file_staging" in (result.limitation or "")
    serialized = result.model_dump_json()
    assert "underlying-secret" not in serialized
    assert "private.invalid" not in serialized
    assert "fake-credential" not in serialized


def test_api_connection_error_unknown_cause_and_missing_cause_are_not_persisted(monkeypatch):
    unknown = _resolve_connection_failure(
        monkeypatch, _connection_error(RuntimeError("raw-sensitive-message"), timed_out=False)
    )
    assert unknown.transport_error_type is None
    assert unknown.transport_timed_out is False
    assert "raw-sensitive-message" not in unknown.model_dump_json()

    missing = _resolve_connection_failure(monkeypatch, _connection_error(timed_out=False))
    assert missing.transport_error_type is None
    assert missing.transport_timed_out is False


def test_nested_transport_cause_records_allowlisted_type_and_os_code_only(monkeypatch):
    import socket

    nested = httpx.ConnectError("Bearer host-secret path-secret")
    try:
        raise socket.gaierror(socket.EAI_AGAIN, "private-hostname")
    except socket.gaierror as os_cause:
        try:
            raise nested from os_cause
        except httpx.ConnectError as http_cause:
            result = _resolve_connection_failure(monkeypatch, _connection_error(http_cause))

    assert result.transport_error_type == "ConnectError"
    assert result.transport_cause_type == "gaierror"
    assert result.transport_os_error_code == socket.EAI_AGAIN
    encoded = result.model_dump_json()
    for sensitive in ("Bearer", "host-secret", "path-secret", "private-hostname"):
        assert sensitive not in encoded


def test_unallowlisted_nested_transport_type_and_os_code_are_omitted(monkeypatch):
    import errno

    class FakeSocketError(OSError):
        pass

    FakeSocketError.__module__ = "untrusted_module"
    cause = FakeSocketError(errno.ECONNREFUSED, "secret endpoint")
    result = _resolve_connection_failure(monkeypatch, _connection_error(cause))

    assert result.transport_cause_type is None
    assert result.transport_os_error_code is None
    assert "secret endpoint" not in result.model_dump_json()


def test_api_connection_error_timeout_flag_and_safe_details_are_persisted(monkeypatch, caplog, tmp_path):
    cause = httpx.ReadTimeout(
        "authorization: Bearer response-secret",
        request=httpx.Request("POST", "https://credential.invalid/files"),
    )
    result = _resolve_connection_failure(monkeypatch, _connection_error(cause, timed_out=True))
    assert result.transport_error_type == "ReadTimeout"
    assert result.transport_timed_out is True
    assert result.failure_category == "network_failure"

    database = tmp_path / "transport-diagnostics.sqlite3"
    monkeypatch.setattr("app.services.workflow.DB_PATH", database)
    from app.services.workflow import save_analysis

    save_analysis(AnalysisResponse(
        analysis_id="transport-diagnostics-analysis", status="analysis_incomplete",
        source_id="synthetic-source", python_target="3.12", requirements=[], resolver=result,
    ))
    with sqlite3.connect(database) as connection:
        persisted = connection.execute(
            "SELECT analysis_json FROM analyses WHERE analysis_id=?",
            ("transport-diagnostics-analysis",),
        ).fetchone()[0]
    payload = json.loads(persisted)
    saved = payload["resolver"]
    assert saved["transport_error_type"] == "ReadTimeout"
    assert saved["transport_timed_out"] is True
    for secret in ("response-secret", "credential.invalid", "fake-credential", "Bearer"):
        assert secret not in persisted
        assert secret not in caplog.text


def test_stub_cannot_be_mistaken_for_real_resolution():
    result = StubResolverAdapter("resolved").resolve("requests\n", "3.12")

    assert result.status == "resolved"
    assert result.execution_mode == "simulated"
    assert result.resolver_version == "simulated"
