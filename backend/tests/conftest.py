from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def contree_client_fake(monkeypatch):
    """Install a public-client-shaped fake; all interactions remain local."""
    def install(*, failure=None, operation=None, inspected_uuid=None):
        calls = {"uploads": [], "submissions": [], "waits": [], "inspections": [], "clients": []}
        image_uuid = str(uuid4())
        operation_uuid = str(uuid4())

        class RetryPolicy:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FileSpec:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class APIConnectionError(Exception):
            pass

        class Client:
            def __init__(self, token, **kwargs):
                calls["clients"].append((token, kwargs))
                if failure == "client":
                    raise APIConnectionError("Authorization: Bearer fake-secret")

            def upload_file(self, content):
                calls["uploads"].append(content)
                if failure == "upload":
                    raise APIConnectionError("upload transport unavailable")
                return SimpleNamespace(uuid="staged-file-uuid")

            def spawn_instance(self, command, image, **kwargs):
                calls["submissions"].append((command, image, kwargs))
                if failure == "submit":
                    raise APIConnectionError("ambiguous submission timeout")
                return SimpleNamespace(uuid=operation_uuid)

            def wait_operation(self, operation_id, **kwargs):
                calls["waits"].append((operation_id, kwargs))
                if failure == "track":
                    raise APIConnectionError("tracking timeout")
                return operation or SimpleNamespace(status="SUCCESS")

            def inspect_image(self, uuid):
                calls["inspections"].append(uuid)
                if failure == "inspect":
                    raise APIConnectionError("image lookup timeout")
                return SimpleNamespace(uuid=inspected_uuid or uuid)

            def close(self):
                calls.setdefault("closed", 0)
                calls["closed"] += 1

        root = types.ModuleType("contree_client")
        root.FileSpec = FileSpec
        httpx = types.ModuleType("contree_client.httpx")
        httpx.ContreeClient = Client
        runtime = types.ModuleType("contree_client.runtime")
        runtime.RetryPolicy = RetryPolicy
        monkeypatch.setitem(sys.modules, "contree_client", root)
        monkeypatch.setitem(sys.modules, "contree_client.httpx", httpx)
        monkeypatch.setitem(sys.modules, "contree_client.runtime", runtime)
        return calls, image_uuid

    return install
