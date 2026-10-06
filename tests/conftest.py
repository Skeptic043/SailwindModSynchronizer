from __future__ import annotations

import time
from pathlib import Path

import pytest

from sailwind_mod_sync.config import AppConfig
from sailwind_mod_sync.manager import Manager
from sailwind_mod_sync.models import CatalogEntry
from sailwind_mod_sync.paths import AppPaths


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


@pytest.fixture
def paths(data_root: Path) -> AppPaths:
    app_paths = AppPaths(data_root)
    app_paths.ensure()
    return app_paths


@pytest.fixture
def manager(paths):
    manager = Manager(
        paths=paths, config=AppConfig(game_path="missing", check_for_updates=False, auto_scan_mods=False)
    )
    manager.catalog = [CatalogEntry(
        name="Test", repo="https://github.com/example/test", guids=["example.test"], primary_guid="example.test",
        latest_raw="1.0.0", latest_version="1.0.0", available=True,
    )]
    yield manager
    manager.close()


@pytest.fixture
def window(manager, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sailwind_mod_sync.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_maybe_check_updates", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_auto_scan_mods", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_refresh_catalog", lambda self: None)
    window = MainWindow(manager)
    yield app, window
    assert window._bulk_pack_id is None
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.fixture
def pump_until():
    def wait(app, condition, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        return condition()

    return wait
