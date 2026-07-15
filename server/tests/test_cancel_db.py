"""Cancel / stop helper smoke tests."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def app_dirs(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as data:
        monkeypatch.setenv("STATE_DIR", state)
        monkeypatch.setenv("DATA_DIR", data)
        import importlib

        import app.config as config
        import app.db as db

        importlib.reload(config)
        importlib.reload(db)
        yield Path(state), Path(data)


def test_request_and_pop_cancel(app_dirs: tuple[Path, Path]) -> None:
    import app.db as db

    db.init_db()
    client = db.register_client("Cancel Mac", "cancel-host.local")
    assert db.pop_cancel(client["id"]) is False
    db.request_cancel(client["id"])
    assert db.pop_cancel(client["id"]) is True
    assert db.pop_cancel(client["id"]) is False


def test_cancel_running_runs(app_dirs: tuple[Path, Path]) -> None:
    import app.db as db

    db.init_db()
    client = db.register_client("Stop Mac", "stop-host.local")
    run_id = db.create_backup_run(client["id"], "manual", None)
    n = db.cancel_running_runs(client["id"], "Stopped from dashboard")
    assert n >= 1
    run = db.get_backup_run(run_id)
    assert run is not None
    assert run["status"] == "error"
    assert run["phase"] == "cancelled"
