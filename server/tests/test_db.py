"""Database layer smoke tests (no GPU / Qdrant dependencies)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def app_dirs(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as data:
        monkeypatch.setenv("STATE_DIR", state)
        monkeypatch.setenv("DATA_DIR", data)
        # Re-import so config picks up temp paths.
        import importlib

        import app.config as config
        import app.db as db

        importlib.reload(config)
        importlib.reload(db)
        yield Path(state), Path(data)


def test_register_and_lookup_client(app_dirs: tuple[Path, Path]) -> None:
    import app.db as db

    db.init_db()
    reg = db.register_client("Test Mac", "test-host.local")
    assert reg["name"] == "Test Mac"
    assert reg["token"]

    found = db.get_client_by_token(reg["token"])
    assert found is not None
    assert found["id"] == reg["id"]


def test_schedule_crud(app_dirs: tuple[Path, Path]) -> None:
    import app.db as db

    db.init_db()
    client = db.register_client("Schedule Mac", "sched-host.local")

    created = db.create_schedule(client["id"], "Nightly", True, [0, 1, 2, 3, 4], 3, 30)
    assert created["name"] == "Nightly"
    assert created["days"] == [0, 1, 2, 3, 4]

    updated = db.update_schedule(created["id"], None, False, None, None, None)
    assert updated is not None
    assert updated["enabled"] is False

    assert db.delete_schedule(created["id"]) is True
    assert db.get_schedule(created["id"]) is None
