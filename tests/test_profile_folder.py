from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from sailwind_mod_sync.config import AppConfig
from sailwind_mod_sync.manager import Manager
from sailwind_mod_sync.models import CatalogEntry
from sailwind_mod_sync.ui.main_window import MainWindow


@pytest.fixture
def manager(paths):
    manager = Manager(paths=paths, config=AppConfig(game_path="missing", check_for_updates=False, auto_scan_mods=False))
    manager.catalog = [CatalogEntry(name="Test", repo="https://github.com/example/test", guids=["example.test"], primary_guid="example.test", latest_raw="1.0.0", latest_version="1.0.0", available=True)]
    yield manager
    manager.close()


@pytest.fixture
def window(manager, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_maybe_check_updates", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_auto_scan_mods", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_refresh_catalog", lambda self: None)
    window = MainWindow(manager)
    yield app, window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_profile_plugins_uses_selected_custom_root_and_handles_errors(window, manager, monkeypatch):
    _, window = window
    selected = manager.packs.create("Selected")
    window._reload_packs(selected.id)
    urls = []
    warnings = []
    monkeypatch.setattr("sailwind_mod_sync.ui.main_window.QDesktopServices.openUrl", lambda url: urls.append(url.toLocalFile()) or True)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window._open_profile_plugins()
    assert Path(urls[0]) == manager.packs.plugins_dir(selected.id)
    monkeypatch.setattr("sailwind_mod_sync.ui.main_window.QDesktopServices.openUrl", lambda url: False)
    window._open_profile_plugins()
    assert "Could not open" in warnings[-1]
    manager.packs.plugins_dir(selected.id).rmdir()
    window._open_profile_plugins()
    assert "does not exist" in warnings[-1]
    assert not manager.packs.plugins_dir(selected.id).exists()
    window.pack_list.setCurrentRow(-1)
    assert not window.open_profile_plugins_action.isEnabled()
    window._open_profile_plugins()
