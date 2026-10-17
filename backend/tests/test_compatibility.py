from app.adapters.stub import StubResolverAdapter
from app.adapters.contree import build_sdk_run_kwargs
from app.schemas.analysis import MetadataResult
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

    assert request["command"] == "python"
    assert request["args"][0] == "-c"
    assert "shell" not in request
    assert request["files"] == {"/tmp/requirements.txt": original.encode()}
    assert original not in request["args"][1]
    assert request["env"] == {"BUGSY_PYTHON_TARGET": "3.12"}


def test_contree_adapter_stays_disabled_by_default(monkeypatch):
    from dataclasses import replace
    from app.adapters.contree import ContreeResolverAdapter
    from app.config import settings

    monkeypatch.setattr("app.adapters.contree.settings", replace(settings, sandbox_enabled=False))

    result = ContreeResolverAdapter().resolve("requests>=2\n", "3.12")

    assert result.status == "not_run"
    assert result.execution_mode == "not_run"
    assert result.sandbox_python is None


def test_stub_cannot_be_mistaken_for_real_resolution():
    result = StubResolverAdapter("resolved").resolve("requests\n", "3.12")

    assert result.status == "resolved"
    assert result.execution_mode == "simulated"
    assert result.resolver_version == "simulated"
