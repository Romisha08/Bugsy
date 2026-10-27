import tempfile
from pathlib import Path

from app.config import _database_path


def test_database_path_defaults_to_system_temporary_directory(monkeypatch):
    monkeypatch.delenv("BUGSY_DATABASE_PATH", raising=False)

    assert _database_path() == Path(tempfile.gettempdir()) / "bugsy.sqlite3"


def test_database_path_preserves_explicit_override(monkeypatch, tmp_path):
    configured_path = tmp_path / "persistent" / "bugsy.sqlite3"
    monkeypatch.setenv("BUGSY_DATABASE_PATH", f"  {configured_path}  ")

    assert _database_path() == configured_path
