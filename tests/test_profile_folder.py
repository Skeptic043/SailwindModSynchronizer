import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

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


def _pump_until(app, condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return condition()


@pytest.mark.parametrize("shortcut,name,is_file", [
    ("plugins", "plugins", False),
    ("config", "config", False),
    ("log", "LogOutput.log", True),
])
def test_profile_shortcut_uses_selected_custom_root_and_handles_errors(
    window, manager, monkeypatch, shortcut, name, is_file,
):
    _, window = window
    selected = manager.packs.create("Selected")
    manager.packs.create("Other")
    window._reload_packs(selected.id)
    path = manager.packs.instance_dir(selected.id) / "BepInEx" / name
    if is_file:
        path.write_text("test log", encoding="utf-8")
    else:
        path.mkdir(exist_ok=True)
    action = getattr(window, f"open_profile_{shortcut}_action")
    open_path = getattr(window, f"_open_profile_{shortcut}")
    urls = []
    warnings = []
    monkeypatch.setattr("sailwind_mod_sync.ui.main_window.QDesktopServices.openUrl", lambda url: urls.append(url.toLocalFile()) or True)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    assert action.isEnabled()
    action.trigger()
    assert [Path(url) for url in urls] == [path]
    assert window.statusBar().currentMessage() == f"Opened {path}"
    monkeypatch.setattr("sailwind_mod_sync.ui.main_window.QDesktopServices.openUrl", lambda url: urls.append(url.toLocalFile()) or False)
    open_path()
    assert "Could not open" in warnings[-1]
    assert len(urls) == 2
    if is_file:
        path.unlink()
    else:
        path.rmdir()
    open_path()
    assert "does not exist" in warnings[-1]
    assert not path.exists()
    assert len(urls) == 2
    calls = len(warnings)
    window.pack_list.setCurrentRow(-1)
    assert not action.isEnabled()
    open_path()
    assert len(warnings) == calls


def test_export_logs_uses_selected_profile_and_reports_missing_sources(window, manager, monkeypatch, tmp_path):
    import zipfile

    app, window = window
    chosen = manager.packs.create("Chosen")
    manager.packs.create("Other")
    window._reload_packs(chosen.id)
    source = manager.packs.instance_dir(chosen.id) / "BepInEx" / "LogOutput.log"
    source.write_text("synthetic chosen profile")
    destination = tmp_path / "selected-export"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), "ZIP files (*.zip)"))
    monkeypatch.setattr("sailwind_mod_sync.log_export.latest_player_log", lambda: tmp_path / "absent-player.log")
    summaries = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: summaries.append(args[-1]))
    window.export_logs_action.trigger()
    assert _pump_until(app, lambda: not window._busy)
    dest = destination.with_suffix(".zip")
    with zipfile.ZipFile(dest) as archive:
        assert archive.read("LogOutput.log") == b"synthetic chosen profile"
        assert set(archive.namelist()) == {"LogOutput.log", "manager.log"}
    assert not summaries
    message = window.statusBar().currentMessage()
    assert "2 log(s)" in message and "Missing: Player.log" in message
    window.pack_list.setCurrentRow(-1)
    assert not window.export_logs_action.isEnabled()


@pytest.mark.parametrize("shortcut,name,is_file", [
    ("plugins", "plugins", False),
    ("config", "config", False),
    ("log", "LogOutput.log", True),
])
def test_profile_shortcuts_remain_available_during_background_work(
    window, manager, monkeypatch, shortcut, name, is_file,
):
    import threading

    app, window = window
    selected = manager.packs.get(window.current_pack_id())
    path = manager.packs.plugins_dir(selected.id).parent / name
    if is_file:
        path.write_text("test log", encoding="utf-8")
    else:
        path.mkdir(exist_ok=True)
    urls = []
    monkeypatch.setattr("sailwind_mod_sync.ui.main_window.QDesktopServices.openUrl",
                        lambda url: urls.append(Path(url.toLocalFile())) or True)
    release = threading.Event()
    window._run(lambda progress: release.wait(5), lambda result: None, "Background work…")
    try:
        action = getattr(window, f"open_profile_{shortcut}_action")
        assert action.isEnabled()
        action.trigger()
        assert urls == [path]
        assert not window.export_logs_action.isEnabled()
    finally:
        release.set()
    assert _pump_until(app, lambda: not window._busy)
