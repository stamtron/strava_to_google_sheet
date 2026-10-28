"""Tests for the atomic, owner-only JSON writer used for OAuth token files."""

import json
import stat

import pytest

from src.storage import atomic_file
from src.storage.atomic_file import write_json_atomic


def test_write_creates_owner_only_file(tmp_path):
    target = tmp_path / "token.json"
    write_json_atomic(str(target), {"k": "v"})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"k": "v"}


def test_overwrite_tightens_existing_permissive_file(tmp_path):
    target = tmp_path / "token.json"
    target.write_text("{}")
    target.chmod(0o644)
    write_json_atomic(str(target), {"k": 2})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"k": 2}


def test_failed_write_keeps_old_file_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "token.json"
    target.write_text('{"old": true}')

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_file.os, "replace", boom)
    with pytest.raises(OSError):
        write_json_atomic(str(target), {"new": True})

    assert json.loads(target.read_text()) == {"old": True}
    assert [p.name for p in tmp_path.iterdir()] == ["token.json"]
