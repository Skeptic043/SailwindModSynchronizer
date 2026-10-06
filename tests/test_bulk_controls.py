from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt, QPoint, QPointF
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QShortcutEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QPushButton
from PySide6.QtTest import QTest

from sailwind_mod_sync.manager import Manager
from sailwind_mod_sync.models import CatalogEntry, PinnedMod
from sailwind_mod_sync.ui.main_window import MainWindow


def add_mod(manager, pack, guid, enabled=False, artifact=True):
    name = guid.rsplit(".", 1)[-1]
    pinned = PinnedMod(guid=guid, version="1.0.0", enabled=enabled, plugin_folders=[name])
    manager.packs.upsert_mod(pack.id, pinned)
    if artifact:
        folder = manager.library.mod_extracted(guid, pinned.version) / name
        folder.mkdir(parents=True)
        (folder / "mod.dll").write_bytes(b"test")
    if enabled:
        target = manager.packs.plugins_dir(pack.id) / name
        target.mkdir(parents=True)
        (target / "mod.dll").write_bytes(b"original")
    return pinned


def test_bulk_skips_unchanged_and_selects_missing_mods(manager):
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.on", enabled=True)
    add_mod(manager, pack, "example.off")
    add_mod(manager, pack, "example.missing", artifact=False)
    messages = []
    count, errors = manager.set_all_mods_enabled(pack.id, True, messages.append)
    assert count == 2 and not errors
    assert len(messages) == 2
    saved = manager.packs.get(pack.id)
    assert saved.find_mod("example.off").enabled
    assert saved.find_mod("example.missing").enabled
    assert saved.find_mod("example.missing").plugin_folders == ["missing"]
    # Unchanged pins do not recopy their library contents.
    assert (manager.packs.plugins_dir(pack.id) / "on/mod.dll").read_bytes() == b"original"
    count, errors = manager.set_all_mods_enabled(pack.id, False)
    assert count == 3 and not errors
    assert not any(mod.enabled for mod in manager.packs.get(pack.id).mods)
    assert not list(manager.packs.plugins_dir(pack.id).iterdir())
    assert manager.set_all_mods_enabled(pack.id, False) == (0, [])


@pytest.mark.parametrize("failure", ["copy", "rename", "save"])
def test_bulk_failure_preserves_files_and_manifest(manager, monkeypatch, failure):
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.off")
    plugins = manager.packs.plugins_dir(pack.id)
    (plugins / "off").mkdir()
    (plugins / "off/previous.txt").write_text("preserve")
    (plugins / "unrelated").mkdir()
    before = manager.packs.manifest_path(pack.id).read_bytes()
    original_replace = Path.replace

    def fail_replace(source, destination):
        if (failure == "save" and source.name == "modpack.json") or (
            failure == "rename" and source.parent.name == "staged"
        ):
            raise PermissionError("injected rename failure")
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_replace)
    if failure == "copy":
        def fail_copy(*args, **kwargs):
            raise OSError("injected copy failure")
        monkeypatch.setattr("sailwind_mod_sync.manager.shutil.copytree", fail_copy)
    count, errors = manager.set_all_mods_enabled(pack.id, True)
    assert count == 0 and len(errors) == 1
    assert manager.packs.manifest_path(pack.id).read_bytes() == before
    assert (plugins / "off/previous.txt").read_text() == "preserve"
    assert not (plugins / "off/mod.dll").exists()
    assert (plugins / "unrelated").is_dir()
    assert not list(manager.packs.pack_dir(pack.id).glob(".bulk-*"))


def test_bulk_failed_rollback_preserves_backup_and_stops(manager, monkeypatch):
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first", enabled=True)
    add_mod(manager, pack, "example.second", enabled=True)
    original_replace = Path.replace
    def fail_replace(source, destination):
        if source.name == "modpack.json" or source.parent.name == "backup":
            raise PermissionError("injected failure")
        return original_replace(source, destination)
    monkeypatch.setattr(Path, "replace", fail_replace)
    count, errors = manager.set_all_mods_enabled(pack.id, False)
    assert count == 0 and len(errors) == 1 and "Backup retained" in errors[0]
    backup = next(manager.packs.pack_dir(pack.id).glob(".bulk-*/backup/first/mod.dll"))
    assert backup.read_bytes() == b"original"
    assert (manager.packs.plugins_dir(pack.id) / "second/mod.dll").exists()
    assert manager.packs.get(pack.id).find_mod("example.first").enabled
    with pytest.raises(RuntimeError, match="needs recovery"):
        manager.prepare_pack(pack.id)
    with pytest.raises(RuntimeError, match="needs recovery"):
        manager.set_all_mods_enabled(pack.id, True)
    # Disk marker remains effective for a new Manager after a restart.
    restarted = Manager(paths=manager.paths, config=manager.config)
    try:
        with pytest.raises(RuntimeError, match="needs recovery"):
            restarted.prepare_pack(pack.id)
    finally:
        restarted.close()


def test_bulk_worker_keeps_events_running_and_blocks_alternate_mutations(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.off")
    other = manager.packs.create("Other pack")
    add_mod(manager, other, "example.other")
    window._reload_views()
    entered = threading.Event()
    release = threading.Event()
    original = manager.set_all_mods_enabled
    captured = []
    def slow(pack_id, enabled, progress, **callbacks):
        captured.append(pack_id)
        entered.set()
        assert release.wait(5)
        return original(pack_id, enabled, progress, **callbacks)
    monkeypatch.setattr(manager, "set_all_mods_enabled", slow)
    launched = []
    monkeypatch.setattr(manager, "play", lambda *args, **kwargs: launched.append("modded"))
    monkeypatch.setattr(manager, "play_vanilla", lambda: launched.append("vanilla"))
    ticks = []
    try:
        window._set_all_mods_enabled(True)
        assert entered.wait(2)
        assert window._progress_dialog is None
        assert not window.play_button.isEnabled()
        assert not window.vanilla_button.isEnabled()
        assert not window.pack_list.isEnabled()
        assert window.tabs.isEnabled() and window.pack_view.table.isEnabled()
        assert not window.pack_view.table.cellWidget(0, 0).findChild(QCheckBox).isEnabled()
        assert not window.pack_view.table.cellWidget(0, 3).isEnabled()
        window.tabs.setCurrentIndex(1)
        QTimer.singleShot(0, lambda: ticks.append(True))
        app.processEvents()
        assert ticks and window.tabs.currentIndex() == 1
        window.catalog_view._filter.setText("does not match")
        window.catalog_view._filter.setText("")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        window._play()
        window._play_vanilla()
        window._delete_pack()
        window._toggle_mod("example.off", False)
        window._set_all_mods_enabled(False)
        window._delete_artifact("example.off", "1.0.0")
        assert not launched
        assert manager.packs.exists(pack.id)
        assert manager.library.mod_extracted("example.off", "1.0.0").is_dir()
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
    finally:
        release.set()
        deadline = time.monotonic() + 5
        while window._bulk_pack_id is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
    assert window._bulk_pack_id is None
    assert captured == [pack.id]
    assert window.play_button.isEnabled() and window.pack_list.isEnabled()
    assert window.pack_view.table.cellWidget(0, 0).findChild(QCheckBox).isEnabled()
    assert window.pack_view.table.cellWidget(0, 3).isEnabled()
    assert not window.pack_view.check_all.isEnabled()
    assert window.pack_view.uncheck_all.isEnabled()
    assert not manager.packs.get(other.id).find_mod("example.other").enabled



@pytest.mark.parametrize("enabled", [True, False])
def test_bulk_refuses_shared_folders_without_mutating_other_pin(manager, enabled):
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first", enabled=not enabled)
    second = add_mod(manager, pack, "example.second", enabled=True)
    second.plugin_folders = ["first"]
    manager.packs.upsert_mod(pack.id, second)
    before = manager.packs.manifest_path(pack.id).read_bytes()
    with pytest.raises(ValueError, match="shared"):
        manager._set_bulk_mod_enabled(pack.id, "example.first", enabled)
    assert manager.packs.manifest_path(pack.id).read_bytes() == before
    assert (manager.packs.plugins_dir(pack.id) / "second/mod.dll").read_bytes() == b"original"


def test_bulk_checks_extra_archive_folders_for_overlap(manager):
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    add_mod(manager, pack, "example.second", enabled=True)
    extra = manager.library.mod_extracted("example.first", "1.0.0") / "second"
    extra.mkdir()
    (extra / "extra.dll").write_bytes(b"do not install")
    with pytest.raises(ValueError, match="shared"):
        manager._set_bulk_mod_enabled(pack.id, "example.first", True)
    assert not (manager.packs.plugins_dir(pack.id) / "second/extra.dll").exists()


@pytest.mark.parametrize("redirect", ["pack", "plugins"])
def test_bulk_rejects_resolved_profile_escape(manager, monkeypatch, tmp_path, redirect):
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    source = manager.packs.pack_dir(pack.id) if redirect == "pack" else manager.packs.plugins_dir(pack.id)
    destination = tmp_path / "outside"
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == source:
            return destination
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="Linked profile"):
        manager._set_bulk_mod_enabled(pack.id, "example.first", True)
    assert not destination.exists()
    assert not list(manager.packs.pack_dir(pack.id).glob(".bulk-*"))



def test_bulk_partial_failure_restores_controls_and_actual_checkboxes(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.good")
    add_mod(manager, pack, "example.missing", artifact=False)
    original_change = manager._set_bulk_mod_enabled
    def fail_one(pack_id, guid, enabled):
        if guid == "example.missing":
            raise PermissionError("injected profile write failure")
        return original_change(pack_id, guid, enabled)
    monkeypatch.setattr(manager, "_set_bulk_mod_enabled", fail_one)
    window._reload_views()
    messages = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: messages.append(box.detailedText()))
    window._set_all_mods_enabled(True)
    deadline = time.monotonic() + 5
    while window._bulk_pack_id is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert window._bulk_pack_id is None
    assert len(messages) == 1 and "example.missing" in messages[0]
    states = {
        window.pack_view.table.item(row, 2).text():
        window.pack_view.table.cellWidget(row, 0).findChild(QCheckBox).isChecked()
        for row in range(window.pack_view.table.rowCount())
    }
    assert states == {"example.good": True, "example.missing": False}
    assert "1/2 mods enabled" in window.pack_view.subtitle.text()
    assert "1 not downloaded" in window.pack_view.subtitle.text()
    assert "All mods enabled" not in window.pack_view.subtitle.text()
    assert not window.pack_view._progress_timer.isActive()
    assert window.play_button.isEnabled()
    assert window.pack_view.check_all.isEnabled() and window.pack_view.uncheck_all.isEnabled()


def test_recovery_marker_disables_play_and_guards_direct_handler(window, manager, monkeypatch):
    _, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    folder = manager.packs.pack_dir(pack.id) / ".bulk-test"
    folder.mkdir()
    marker = folder / "recovery-required.txt"
    marker.write_text("Recovery required")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window._reload_views()
    assert not window.play_button.isEnabled()
    window._play()
    assert len(warnings) == 1 and str(marker) in warnings[0]



def wait_for_changes(app, window):
    deadline = time.monotonic() + 5
    while window._bulk_pack_id is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert window._bulk_pack_id is None


def test_single_toggle_runs_in_background_and_preserves_rows(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    add_mod(manager, pack, "example.second", enabled=True)
    window._reload_views()
    window.pack_view.table.sortItems(2, Qt.SortOrder.DescendingOrder)
    rows = {window.pack_view.table.item(row, 2).text(): window.pack_view.table.cellWidget(row, 0)
            for row in range(window.pack_view.table.rowCount())}
    entered, release = threading.Event(), threading.Event()
    original = manager.set_mods_enabled
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(manager, "set_mods_enabled", slow)
    def no_rebuild(*args, **kwargs):
        raise AssertionError("Enabled-only updates must not rescan or rebuild")
    monkeypatch.setattr(manager.library, "list_mods", no_rebuild)
    monkeypatch.setattr(window.pack_view, "set_pack", no_rebuild)
    try:
        rows["example.first"].findChild(QCheckBox).setChecked(True)
        assert entered.wait(2)
        assert rows["example.first"].findChild(QCheckBox).isChecked()
        assert window.play_button.isEnabled()
        assert window._single_input_filter_installed
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        app.processEvents()
        assert ticks and "Enabling" not in window.pack_view.subtitle.text()
        assert window.pack_view.subtitle.styleSheet() == ""
        assert "1/2 mods enabled" in window.pack_view.subtitle.text()
    finally:
        release.set()
        wait_for_changes(app, window)
    assert all(widget is window.pack_view.table.cellWidget(row, 0)
               for row in range(window.pack_view.table.rowCount())
               for guid, widget in rows.items() if window.pack_view.table.item(row, 2).text() == guid)
    assert rows["example.first"].findChild(QCheckBox).isChecked()
    assert "2/2 mods enabled" in window.pack_view.subtitle.text()
    assert not window.pack_view._progress_timer.isActive()


def test_bulk_subtitle_counts_only_committed_mods_while_running(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    add_mod(manager, pack, "example.second")
    window._reload_views()
    reached_second, release = threading.Event(), threading.Event()
    original = manager._set_bulk_mod_enabled
    def delayed(pack_id, guid, enabled):
        if guid == "example.second":
            reached_second.set()
            assert release.wait(5)
        return original(pack_id, guid, enabled)
    monkeypatch.setattr(manager, "_set_bulk_mod_enabled", delayed)
    try:
        window._set_all_mods_enabled(True)
        assert reached_second.wait(2)
        app.processEvents()
        assert window._bulk_pack_id == pack.id
        assert "Enabling" in window.pack_view.subtitle.text()
        assert "1/2 mods enabled" in window.pack_view.subtitle.text()
        assert not manager.packs.get(pack.id).find_mod("example.second").enabled
        assert not window.pack_view._progress_timer.isActive()
    finally:
        release.set()
        wait_for_changes(app, window)
    assert "All mods enabled (2/2)" in window.pack_view.subtitle.text()
    assert window.pack_view._progress_timer.interval() == 3000


def test_refused_single_toggle_restores_checkbox_during_scan(window, manager):
    _, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    window._reload_views()
    window._mod_scan_running = True
    checkbox = window.pack_view.table.cellWidget(0, 0).findChild(QCheckBox)
    checkbox.setChecked(True)
    assert not checkbox.isChecked()
    assert "0/1 mods enabled" in window.pack_view.subtitle.text()
    assert window._bulk_pack_id is None
    window._mod_scan_running = False


def test_filter_rebuild_restores_available_and_unavailable_buttons(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    manager.catalog.append(CatalogEntry(name="Unavailable", repo="https://github.com/example/no-release",
        guids=["example.none"], primary_guid="example.none", latest_raw="", latest_version=None, available=False))
    window._reload_views()
    entered, release = threading.Event(), threading.Event()
    original = manager.set_all_mods_enabled
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(manager, "set_all_mods_enabled", slow)
    try:
        window._set_all_mods_enabled(True)
        assert entered.wait(2)
        window.catalog_view._filter.setText("nothing")
        window.catalog_view._filter.setText("")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        for row in range(window.catalog_view.table.rowCount()):
            assert all(not button.isEnabled() for button in window.catalog_view.table.cellWidget(row, 5).findChildren(QPushButton))
    finally:
        release.set()
        wait_for_changes(app, window)
    for row in range(window.catalog_view.table.rowCount()):
        buttons = window.catalog_view.table.cellWidget(row, 5).findChildren(QPushButton)
        assert buttons[0].isEnabled()
        assert buttons[1].isEnabled() == (window.catalog_view.table.item(row, 2).text() == "example.test")


def test_success_subtitle_expires_and_next_progress_cancels_old_timeout(window):
    app, window = window
    view = window.pack_view
    assert "0/0 mods enabled" in view.subtitle.text()
    view.show_operation_result("All mods disabled", success=True)
    assert "All mods disabled (0/0)" in view.subtitle.text()
    deadline = time.monotonic() + 4
    while view._progress_timer.isActive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert "All mods" not in view.subtitle.text()
    assert "0/0 mods enabled" in view.subtitle.text()
    view.show_operation_result("All mods disabled", success=True)
    view.show_operation_progress("Enabling…")
    assert not view._progress_timer.isActive()
    assert "Enabling" in view.subtitle.text()


def test_no_false_success_if_worker_result_does_not_match_persisted_state(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    window._reload_views()
    monkeypatch.setattr(manager, "set_all_mods_enabled", lambda *args, **kwargs: (1, []))
    messages = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: messages.append(box.detailedText()))
    window._set_all_mods_enabled(True)
    wait_for_changes(app, window)
    assert messages and "not reached" in messages[0]
    assert "All mods enabled" not in window.pack_view.subtitle.text()
    assert "0/1 mods enabled" in window.pack_view.subtitle.text()
    assert not window.pack_view._progress_timer.isActive()



def test_single_toggle_save_failure_rolls_back_and_restores_checkbox(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    old = manager.packs.plugins_dir(pack.id) / "first/old.txt"
    old.parent.mkdir()
    old.write_text("preserve")
    window._reload_views()
    original_manifest = manager.packs.manifest_path(pack.id).read_bytes()
    original_replace = Path.replace
    def fail_save(source, target):
        if source.name == "modpack.json" and source.parent.name.startswith(".bulk-"):
            raise PermissionError("Injected manifest replacement failure")
        return original_replace(source, target)
    monkeypatch.setattr(Path, "replace", fail_save)
    warnings = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: warnings.append(box.detailedText()))
    checkbox = window.pack_view.table.cellWidget(0, 0).findChild(QCheckBox)
    checkbox.setChecked(True)
    wait_for_changes(app, window)
    assert warnings and "Injected" in warnings[0]
    assert not checkbox.isChecked()
    assert old.read_text() == "preserve"
    assert not old.with_name("mod.dll").exists()
    assert manager.packs.manifest_path(pack.id).read_bytes() == original_manifest
    assert "0/1 mods enabled" in window.pack_view.subtitle.text()
    assert "All mods enabled" not in window.pack_view.subtitle.text()
    assert not window.pack_view._progress_timer.isActive()


def test_on_column_sort_stays_matched_to_guids_after_individual_change(window, manager):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    add_mod(manager, pack, "example.second", enabled=True)
    add_mod(manager, pack, "example.third")
    window._reload_views()
    table = window.pack_view.table
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    row = next(row for row in range(table.rowCount()) if table.item(row, 2).text() == "example.first")
    table.cellWidget(row, 0).findChild(QCheckBox).setChecked(True)
    wait_for_changes(app, window)
    persisted = {mod.guid: mod.enabled for mod in manager.packs.get(pack.id).mods}
    keys = []
    for row in range(table.rowCount()):
        guid = table.item(row, 2).text()
        checked = table.cellWidget(row, 0).findChild(QCheckBox).isChecked()
        key = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        assert checked == persisted[guid] and key == int(checked)
        keys.append(key)
    assert keys == sorted(keys)
    assert "2/3 mods enabled" in window.pack_view.subtitle.text()



def test_single_pending_blocks_other_inputs_and_all_launch_paths_without_graying_view(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    add_mod(manager, pack, "example.second", enabled=True)
    window._reload_views()
    entered, release = threading.Event(), threading.Event()
    original = manager.set_mods_enabled
    calls = []
    def delayed(*args, **kwargs):
        calls.append(kwargs["guids"])
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(manager, "set_mods_enabled", delayed)
    launches = []
    monkeypatch.setattr(manager, "play", lambda *args, **kwargs: launches.append("modded"))
    monkeypatch.setattr(manager, "play_vanilla", lambda: launches.append("vanilla"))
    first = window.pack_view.mod_checkbox("example.first")
    second = window.pack_view.mod_checkbox("example.second")
    row = next(row for row in range(window.pack_view.table.rowCount())
               if window.pack_view.table.item(row, 2).text() == "example.second")
    combo = window.pack_view.table.cellWidget(row, 3)
    combo.addItem("2.0.0", ("2.0.0", "2.0.0"))
    initial_index = combo.currentIndex()
    subtitle_font = window.pack_view.subtitle.font()
    try:
        first.setChecked(True)
        assert entered.wait(2)
        assert first.isChecked() and first.isEnabled()
        assert second.isEnabled() and combo.isEnabled()
        assert window.pack_view.table.isEnabled() and window.catalog_view.isEnabled()
        assert window.pack_list.isEnabled()
        assert window.play_button.isEnabled()
        assert window.vanilla_button.isEnabled()
        assert window.pack_view.check_all.isEnabled() and window.pack_view.uncheck_all.isEnabled()
        second.setChecked(False)
        assert second.isChecked()
        combo.setCurrentIndex(combo.count() - 1)
        assert combo.currentIndex() == initial_index
        window._play()
        window._play_vanilla()
        window._delete_pack()
        window._remove_mod("example.second")
        assert not launches
        assert manager.packs.get(pack.id).find_mod("example.second").enabled
        assert window.pack_view.subtitle.styleSheet() == ""
        assert window.pack_view.subtitle.font() == subtitle_font
        assert "1/2 mods enabled" in window.pack_view.subtitle.text()
        assert "Enabling" not in window.pack_view.subtitle.text()
    finally:
        release.set()
        wait_for_changes(app, window)
    assert calls == [{"example.first"}]
    assert first.isChecked() and first.isEnabled()
    assert not launches
    assert window.pack_view.subtitle.styleSheet() == ""
    assert window.pack_view.subtitle.font() == subtitle_font
    assert "2/2 mods enabled" in window.pack_view.subtitle.text()


@pytest.mark.parametrize("fail", [False, True])
def test_background_scan_blocks_bulk_buttons_through_refresh_until_done(window, manager, monkeypatch, fail):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    add_mod(manager, pack, "example.second", enabled=True)
    window._reload_views()
    monkeypatch.setattr("sailwind_mod_sync.ui.main_window.run_background", lambda *args: None)
    assert window.pack_view.check_all.isEnabled() and window.pack_view.uncheck_all.isEnabled()
    window._start_background_mod_scan()
    app.processEvents()
    assert not window.pack_view.check_all.isEnabled() and not window.pack_view.uncheck_all.isEnabled()
    window._reload_views()
    window.pack_view.refresh_enabled(manager.packs.get(pack.id))
    assert not window.pack_view.check_all.isEnabled() and not window.pack_view.uncheck_all.isEnabled()
    if fail:
        window._mod_scan_failed("Synthetic scan failure")
    else:
        window._mod_scan_finished(None)
    assert window.pack_view.check_all.isEnabled() and window.pack_view.uncheck_all.isEnabled()


@pytest.mark.parametrize("fail", [False, True])
def test_modal_operation_blocks_bulk_buttons_until_done(window, manager, monkeypatch, fail):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    add_mod(manager, pack, "example.second", enabled=True)
    window._reload_views()
    monkeypatch.setattr("sailwind_mod_sync.ui.main_window.run_background", lambda *args: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    window._run(lambda progress: None, lambda result: None, "Synthetic task")
    app.processEvents()
    assert not window.pack_view.check_all.isEnabled() and not window.pack_view.uncheck_all.isEnabled()
    if fail:
        window._on_fail("Synthetic failure")
    else:
        window._on_task_ok(None)
    assert window.pack_view.check_all.isEnabled() and window.pack_view.uncheck_all.isEnabled()


def test_bulk_subtitle_color_preserves_normal_font_metrics(window):
    _, window = window
    subtitle = window.pack_view.subtitle
    original = subtitle.font()
    for method, args in [(window.pack_view.show_operation_progress, ("Enabling…",))]:
        method(*args)
        assert subtitle.font() == original
        assert "font-size" not in subtitle.styleSheet() and "font-weight" not in subtitle.styleSheet()
    window.pack_view.show_operation_result("All mods enabled", success=True)
    assert subtitle.font() == original
    assert subtitle.styleSheet()
    window.pack_view.clear_operation_status()
    assert subtitle.font() == original and not subtitle.styleSheet()



def test_single_input_filter_blocks_actual_input_without_repainting_controls(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    for index in range(70):
        add_mod(manager, pack, f"example.mod{index:02}", enabled=index == 1)
    other = manager.packs.create("Other")
    window._reload_packs(pack.id)
    window._reload_views()
    window.resize(1000, 520)
    window.show()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    first = window.pack_view.mod_checkbox("example.mod00")
    second = window.pack_view.mod_checkbox("example.mod01")
    combo = window.pack_view.table.cellWidget(1, 3)
    combo.addItem("2.0.0", ("2.0.0", "2.0.0"))
    initial_index = combo.currentIndex()
    controls = [window.pack_list, window.play_button, window.vanilla_button,
                window.pack_view.check_all, window.pack_view.uncheck_all, first, second, combo]
    appearance = [(widget.isEnabled(), widget.palette(), widget.font(), widget.styleSheet()) for widget in controls]
    entered, release = threading.Event(), threading.Event()
    calls, launches, saves, shortcuts = [], [], [], []
    original = manager.set_mods_enabled
    def delayed(*args, **kwargs):
        calls.append(kwargs["guids"])
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(manager, "set_mods_enabled", delayed)
    monkeypatch.setattr(manager, "play", lambda *args, **kwargs: launches.append("modded"))
    monkeypatch.setattr(manager, "play_vanilla", lambda: launches.append("vanilla"))
    monkeypatch.setattr(manager, "save_config", lambda: saves.append(manager.config.last_pack_id))
    shortcut = QShortcut(QKeySequence("Ctrl+L"), window)
    shortcut.activated.connect(lambda: shortcuts.append(True))
    other_row = next(row for row in range(window.pack_list.count())
                     if window.pack_list.item(row).data(Qt.ItemDataRole.UserRole) == other.id)
    try:
        QTest.mouseClick(first, Qt.MouseButton.LeftButton)
        assert entered.wait(2)
        assert window._single_input_filter_installed
        app.processEvents()
        assert appearance == [(widget.isEnabled(), widget.palette(), widget.font(), widget.styleSheet()) for widget in controls]
        QTest.mouseClick(window.play_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(window.vanilla_button, Qt.MouseButton.LeftButton)
        window._play()
        window._play_vanilla()
        QTest.mouseClick(window.pack_view.check_all, Qt.MouseButton.LeftButton)
        QTest.mouseClick(window.pack_view.uncheck_all, Qt.MouseButton.LeftButton)
        QTest.mouseClick(second, Qt.MouseButton.LeftButton)
        QTest.keyClick(second, Qt.Key.Key_Space)
        assert second.isChecked()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentIndex() == initial_index
        combo.showPopup()
        QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton,
                         pos=combo.view().visualRect(combo.model().index(combo.count() - 1, 0)).center())
        assert combo.currentIndex() == initial_index
        combo.hidePopup()
        QTest.mouseClick(window.pack_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.pack_list.visualItemRect(window.pack_list.item(other_row)).center())
        QTest.keyClick(window.pack_list, Qt.Key.Key_Down)
        window.pack_list.setCurrentRow(other_row)
        assert window.current_pack_id() == pack.id and manager.config.last_pack_id == pack.id
        assert not saves
        app.sendEvent(shortcut, QShortcutEvent(QKeySequence("Ctrl+L"), shortcut.id()))
        assert not shortcuts and not launches
        scrollbar = window.pack_view.table.verticalScrollBar()
        before = scrollbar.value()
        viewport = window.pack_view.table.viewport()
        point = QPointF(viewport.rect().center())
        wheel = QWheelEvent(point, QPointF(viewport.mapToGlobal(point.toPoint())), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(viewport, wheel)
        assert scrollbar.value() > before
        assert "1/70 mods enabled" in window.pack_view.subtitle.text()
        assert window.pack_view.check_all.isEnabled() and window.pack_view.uncheck_all.isEnabled()
    finally:
        release.set()
        wait_for_changes(app, window)
    assert not window._single_input_filter_installed
    assert calls == [{"example.mod00"}] and not launches
    app.sendEvent(shortcut, QShortcutEvent(QKeySequence("Ctrl+L"), shortcut.id()))
    assert shortcuts == [True]
    window.pack_list.setCurrentRow(other_row)
    assert window.current_pack_id() == other.id and saves == [other.id]


def test_single_start_failure_removes_input_filter_and_restores_availability(window, manager, monkeypatch):
    _, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    add_mod(manager, pack, "example.first")
    window._reload_views()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    def cannot_start(*args):
        raise RuntimeError("Synthetic worker startup failure")
    monkeypatch.setattr("sailwind_mod_sync.ui.main_window.run_background", cannot_start)
    checkbox = window.pack_view.mod_checkbox("example.first")
    checkbox.setChecked(True)
    assert warnings and "startup failure" in warnings[0]
    assert not window._single_input_filter_installed and window._bulk_pack_id is None
    assert not checkbox.isChecked()
    assert window.pack_view.check_all.isEnabled() and not window.pack_view.uncheck_all.isEnabled()
    assert window.play_button.isEnabled()


@pytest.mark.parametrize("artifact", ["absent", "zip_only", "empty_extracted"])
def test_missing_artifact_enable_removes_stale_files_but_keeps_selection(manager, artifact):
    pack = manager.packs.get(manager.config.last_pack_id)
    pin = add_mod(manager, pack, "example.missing", artifact=False)
    if artifact != "absent":
        directory = manager.library.mod_dir(pin.guid, pin.version)
        directory.mkdir(parents=True)
        (directory / "artifact.zip").write_bytes(b"cached archive pending extraction")
        if artifact == "empty_extracted":
            (directory / "extracted/empty").mkdir(parents=True)
        assert manager.library.has_mod(pin.guid, pin.version)
    stale = manager.packs.plugins_dir(pack.id) / "missing/stale.dll"
    stale.parent.mkdir()
    stale.write_bytes(b"stale")
    unaffected = stale.parent.parent / "unrelated"
    unaffected.mkdir()
    result = manager.set_mod_enabled(pack.id, pin.guid, True)
    saved = result.find_mod(pin.guid)
    assert saved.enabled and saved.plugin_folders == ["missing"]
    assert not stale.parent.exists() and unaffected.exists()
    assert manager.bulk_recovery_path(pack.id) is None


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("save_failure", [True, False])
def test_empty_folder_list_uses_atomic_manifest_only_change(manager, monkeypatch, enabled, save_failure):
    pack = manager.packs.get(manager.config.last_pack_id)
    pin = PinnedMod(guid="example.missing", version="1.0.0", enabled=not enabled, plugin_folders=[])
    manager.packs.upsert_mod(pack.id, pin)
    manifest = manager.packs.manifest_path(pack.id)
    before = manifest.read_bytes()
    plugins = manager.packs.plugins_dir(pack.id)
    plugins.rmdir()
    original_replace = Path.replace
    replacements = []
    def replace(source, target):
        if target == manifest:
            replacements.append(source)
            if save_failure:
                raise PermissionError("injected manifest commit failure")
        return original_replace(source, target)
    monkeypatch.setattr(Path, "replace", replace)
    if save_failure:
        with pytest.raises(PermissionError, match="commit failure"):
            manager.set_mod_enabled(pack.id, pin.guid, enabled)
        assert manifest.read_bytes() == before
    else:
        result = manager.set_mod_enabled(pack.id, pin.guid, enabled)
        assert result.find_mod(pin.guid).enabled == enabled
        assert result.find_mod(pin.guid).plugin_folders == []
    assert len(replacements) == 1
    assert not plugins.exists()
    assert not list(manager.packs.pack_dir(pack.id).glob(".bulk-*"))


@pytest.mark.parametrize("phase", ["before_backup", "after_backup", "after_install", "before_commit", "after_commit"])
def test_interrupted_transaction_retains_recovery_facts_before_and_after_commit(manager, monkeypatch, phase):
    class Interrupted(BaseException):
        pass
    pack = manager.packs.get(manager.config.last_pack_id)
    pin = add_mod(manager, pack, "example.change")
    plugins = manager.packs.plugins_dir(pack.id)
    (plugins / "change").mkdir()
    (plugins / "change/original.dll").write_bytes(b"original")
    # Include an originally absent folder so recovery can distinguish it.
    extra = manager.library.mod_extracted(pin.guid, pin.version) / "extra"
    extra.mkdir()
    (extra / "extra.dll").write_bytes(b"extra")
    manifest = manager.packs.manifest_path(pack.id)
    before = manifest.read_bytes()
    original_replace = Path.replace
    def interrupted_replace(source, target):
        operation = ("backup" if target.parent.name == "backup" else
                     "install" if source.parent.name == "staged" else
                     "commit" if target == manifest else "other")
        if phase == "before_" + operation:
            raise Interrupted()
        result = original_replace(source, target)
        if phase == "after_" + operation:
            raise Interrupted()
        return result
    monkeypatch.setattr(Path, "replace", interrupted_replace)
    with pytest.raises(Interrupted):
        manager.set_mod_enabled(pack.id, pin.guid, True)
    marker = manager.bulk_recovery_path(pack.id)
    assert marker is not None
    transaction = marker.parent
    plan = json.loads((transaction / "transaction.json").read_text(encoding="utf-8"))
    assert plan["guid"] == pin.guid and plan["pack_id"] == pack.id
    assert plan["originally_present"] == ["change"]
    assert plan["affected_folders"] == ["change", "extra"]
    assert (transaction / "original-modpack.json").read_bytes() == before
    planned = (transaction / "planned-modpack.json").read_bytes()
    assert json.loads(planned)["mods"][0]["enabled"] is True
    assert manifest.read_bytes() == (planned if phase == "after_commit" else before)
    instructions = marker.read_text(encoding="utf-8")
    assert "Deleting the marker alone is not a repair" in instructions
    assert "do not delete or replace the current folder" in instructions
    assert "stop and seek help" in instructions
    assert "manifest match alone does not prove" in instructions
    with pytest.raises(RuntimeError, match="needs recovery"):
        manager.set_mod_enabled(pack.id, pin.guid, False)
    restarted = Manager(paths=manager.paths, config=manager.config)
    try:
        with pytest.raises(RuntimeError, match="needs recovery"):
            restarted.prepare_pack(pack.id)
    finally:
        restarted.close()


@pytest.mark.parametrize("state", ["normal", "recovery", "no_selection"])
def test_clear_busy_keeps_play_gated_by_selected_profile_and_recovery(window, manager, state):
    _, window = window
    pack_id = window.current_pack_id()
    if state == "recovery":
        transaction = manager.packs.pack_dir(pack_id) / ".bulk-test"
        transaction.mkdir()
        (transaction / "recovery-required.txt").write_text("Recovery required", encoding="utf-8")
    elif state == "no_selection":
        window.pack_list.setCurrentRow(-1)
    window._busy = True
    window.play_button.setEnabled(False)
    window._clear_busy()
    assert window.play_button.isEnabled() == (state == "normal")
    if state == "recovery":
        assert "recovery-required.txt" in window.play_button.toolTip()


def test_single_public_api_preserves_missing_guid_error(manager):
    with pytest.raises(FileNotFoundError, match="example.unknown"):
        manager.set_mod_enabled(manager.config.last_pack_id, "example.unknown", True)



def test_partial_rollback_retains_already_restored_original_and_remaining_backup(manager, monkeypatch):
    pack = manager.packs.get(manager.config.last_pack_id)
    pin = add_mod(manager, pack, "example.first", enabled=True)
    pin.plugin_folders.append("second")
    manager.packs.upsert_mod(pack.id, pin)
    plugins = manager.packs.plugins_dir(pack.id)
    (plugins / "second").mkdir()
    (plugins / "second/original.dll").write_bytes(b"second original")
    before = manager.packs.manifest_path(pack.id).read_bytes()
    original_replace = Path.replace
    def fail_replace(source, target):
        if source.name == "modpack.json" or (source.parent.name == "backup" and source.name == "second"):
            raise PermissionError("injected partial rollback failure")
        return original_replace(source, target)
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="Backup retained"):
        manager.set_mod_enabled(pack.id, pin.guid, False)
    marker = manager.bulk_recovery_path(pack.id)
    assert marker is not None
    work = marker.parent
    plan = json.loads((work / "transaction.json").read_text(encoding="utf-8"))
    assert plan["originally_present"] == ["first", "second"]
    assert (plugins / "first/mod.dll").read_bytes() == b"original"
    assert not (work / "backup/first").exists()
    assert not (plugins / "second").exists()
    assert (work / "backup/second/original.dll").read_bytes() == b"second original"
    assert manager.packs.manifest_path(pack.id).read_bytes() == before
    assert (work / "original-modpack.json").read_bytes() == before
    assert (work / "planned-modpack.json").exists()
    assert "do not delete or replace the current folder" in marker.read_text(encoding="utf-8")


def test_switching_packs_scans_library_once(window, manager, monkeypatch, tmp_path):
    import zipfile

    app, window = window
    first = manager.packs.ensure_default()
    second = manager.packs.create("Second")
    for index in range(5):
        guid = f"example.repoless{index}"
        archive = tmp_path / f"{guid}.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(f"Repoless{index}/Repoless{index}.dll", b"MZ")
        manager.library.ingest_mod_zip(guid, "1.0.0", archive, version_raw="1.0.0", repo="", source_url="")
        manager.packs.upsert_mod(second.id, PinnedMod(guid=guid, version="1.0.0"))
    window._reload_packs(select_id=first.id)
    calls = []
    original = manager.library.list_mods
    monkeypatch.setattr(manager.library, "list_mods", lambda: calls.append(1) or original())
    rows = {window.pack_list.item(row).data(Qt.ItemDataRole.UserRole): row for row in range(window.pack_list.count())}
    window.pack_list.setCurrentRow(rows[second.id])
    assert len(calls) == 1
    assert window.current_pack_id() == second.id


def _pump_until(app, condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return condition()


def test_quick_task_never_shows_progress_dialog(window, monkeypatch):
    from sailwind_mod_sync.ui.progress_dialog import BusyDialog

    app, window = window
    shown = []
    original_show = BusyDialog.show
    monkeypatch.setattr(BusyDialog, "show", lambda self: shown.append(self) or original_show(self))
    results = []
    window._run(lambda progress: "done", results.append, "Updating mod…")
    assert _pump_until(app, lambda: results == ["done"])
    assert not window._busy
    deadline = time.monotonic() + 0.4
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert shown == []


def test_slow_task_shows_progress_dialog_after_delay(window):
    from sailwind_mod_sync.ui.main_window import PROGRESS_DIALOG_DELAY_MS

    app, window = window
    release = threading.Event()
    results = []
    window._run(lambda progress: release.wait(5) and "done", results.append, "Downloading mod…")
    try:
        assert window._progress_dialog is not None
        assert not window._progress_dialog.isVisible()
        assert _pump_until(app, lambda: window._progress_dialog.isVisible(), timeout=PROGRESS_DIALOG_DELAY_MS / 1000 + 2)
    finally:
        release.set()
    assert _pump_until(app, lambda: results == ["done"])
    assert window._progress_dialog is None


def test_update_all_updates_pack_mods_and_reports_failures(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    calls: list[tuple[str, list[str]]] = []

    def fake_update_mods(pack_id, guids, progress=None):
        calls.append((pack_id, list(guids)))
        return [PinnedMod(guid="example.test", version="1.0.0")], ["example.broken: No GitHub release"]

    warnings: list[str] = []
    monkeypatch.setattr(manager, "update_mods", fake_update_mods)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: warnings.append(box.detailedText()) or 0)

    window._update_all_mods(["example.test", "example.broken"])
    deadline = time.monotonic() + 5
    while window._busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)

    app.processEvents()
    assert calls == [(pack.id, ["example.test", "example.broken"])]
    assert warnings == ["example.broken: No GitHub release"]
    assert window.statusBar().currentMessage() == "Updated 1 mod(s), 1 could not be updated"


def test_update_all_names_the_running_update_scan(window, manager):
    app, window = window
    window._mod_scan_running = True
    window._update_bulk_actions_availability()
    assert not window.pack_view.update_all.isEnabled()
    assert window.pack_view.update_all.text() == "Checking for updates…"
    window._mod_scan_running = False
    window._update_bulk_actions_availability()
    assert window.pack_view.update_all.isEnabled()
    assert window.pack_view.update_all.text() == "Update all"


def test_clear_cache_asks_first_then_clears_and_downloads_the_catalog(window, manager, monkeypatch):
    from sailwind_mod_sync.manager import CacheClearResult

    app, window = window
    calls: list[object] = []
    answers = [None, True]
    monkeypatch.setattr(window, "_confirm_clear_cache", lambda size, imported: answers.pop(0))
    monkeypatch.setattr(manager, "cache_size", lambda **kwargs: 3 * 1024 * 1024)
    monkeypatch.setattr(manager, "imported_mods_size", lambda: 1024)
    monkeypatch.setattr(
        manager,
        "clear_cache",
        lambda progress=None, include_imported=False: calls.append(("clear", include_imported)) or CacheClearResult(1024),
    )
    monkeypatch.setattr(manager, "refresh_catalog", lambda progress=None: calls.append("refresh") or manager.catalog)

    window._clear_cache()
    assert calls == []

    window._clear_cache()
    deadline = time.monotonic() + 5
    while (window._busy or len(calls) < 2) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert calls == [("clear", True), "refresh"]
    assert window.statusBar().currentMessage().startswith("Cleared 1.0 KB of cached data. Catalog:")


def test_clear_cache_dialog_offers_imported_mods_only_when_there_are_some(window, monkeypatch):
    app, window = window
    seen: list[str | None] = []

    def fake_exec(box):
        check = box.checkBox()
        seen.append(check.text() if check else None)
        if check:
            check.setChecked(True)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert window._confirm_clear_cache(2048, 0) is False
    assert window._confirm_clear_cache(2048, 3 * 1024 * 1024) is True
    assert seen == [None, "Also delete mods imported from files (3.0 MB)"]


def test_clear_cache_waits_for_a_running_update_check(window, manager, monkeypatch):
    app, window = window
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: shown.append(text))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: pytest.fail("asked while busy"))
    window._mod_scan_running = True
    window._clear_cache()
    window._mod_scan_running = False
    assert shown and "update check" in shown[0]


def test_play_warns_only_about_mods_that_cannot_be_downloaded(window, manager, monkeypatch):
    from sailwind_mod_sync.ui import main_window as main_window_module

    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    manager.packs.upsert_mod(pack.id, PinnedMod(guid="example.test", version="1.0.0"))
    warned: list[list[str]] = []

    class _Dialog:
        def __init__(self, missing, pack_name, parent):
            warned.append([mod.guid for mod in missing])
            self.stop_reminding = False

        def exec(self):
            return 1

    monkeypatch.setattr(main_window_module, "MissingModsWarningDialog", _Dialog)
    assert window._confirm_missing_mods(pack.id)
    assert warned == []

    manager.packs.upsert_mod(pack.id, PinnedMod(guid="local.mystery", version="1.0.0"))
    assert window._confirm_missing_mods(pack.id)
    assert warned == [["local.mystery"]]


def test_download_fetches_the_pinned_version(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    manager.packs.upsert_mod(pack.id, PinnedMod(guid="example.test", version="1.0.0", version_raw="v1.0.0"))
    calls: list[tuple] = []
    monkeypatch.setattr(
        manager,
        "set_pack_mod_version",
        lambda *args, **kwargs: calls.append(args) or PinnedMod(guid="example.test", version="1.0.0"),
    )
    window._download_mod("example.test")
    deadline = time.monotonic() + 5
    while window._busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert calls == [(pack.id, "example.test", "1.0.0", "v1.0.0")]


def test_play_refreshes_pack_view_when_every_mod_downloaded(window, manager, monkeypatch):
    app, window = window
    pack = manager.packs.get(manager.config.last_pack_id)
    manager.packs.upsert_mod(pack.id, PinnedMod(guid="example.test", version="1.0.0", plugin_folders=["test"]))
    window._reload_views()
    assert "1 not downloaded" in window.pack_view.subtitle.text()

    # What Play's background download leaves behind: the artifact is now in the library.
    folder = manager.library.mod_extracted("example.test", "1.0.0") / "test"
    folder.mkdir(parents=True)
    (folder / "mod.dll").write_bytes(b"MZ")
    assert manager.missing_mods(manager.packs.get(pack.id)) == []

    monkeypatch.setattr(window, "_open_launch_splash", lambda splash, *, status: None)
    window._sailwind_started(None, pack_id=pack.id)
    assert "not downloaded" not in window.pack_view.subtitle.text()


def test_background_mod_scan_shows_spinner_until_done(window, manager, monkeypatch):
    app, window = window
    release = threading.Event()
    monkeypatch.setattr(manager, "scan_updates", lambda live=True, progress=None: release.wait(5))
    indicator = window._background_indicator
    assert indicator.isHidden()
    window._start_background_mod_scan()
    try:
        assert not indicator.isHidden()
        assert indicator.spinner.is_spinning()
        assert indicator.label.text() == "Checking for mod updates…"
    finally:
        release.set()
    assert _pump_until(app, lambda: not window._mod_scan_running)
    assert indicator.isHidden()
    assert not indicator.spinner.is_spinning()


def test_background_catalog_refresh_shows_spinner(window):
    from sailwind_mod_sync.ui.workers import TaskBridge

    app, window = window
    indicator = window._background_indicator
    # The window fixture stubs _maybe_refresh_catalog, so set the bridge it would create.
    window._catalog_bridge = TaskBridge(window)
    window._update_background_indicator()
    assert indicator.label.text() == "Refreshing catalog…"
    assert not indicator.isHidden()
    window._background_catalog_failed("offline")
    assert indicator.isHidden()


def test_background_indicator_lists_every_running_task():
    from sailwind_mod_sync.ui.background_indicator import BackgroundIndicator

    QApplication.instance() or QApplication([])
    indicator = BackgroundIndicator()
    indicator.set_tasks(["Checking for mod updates…", "Refreshing catalog…"])
    assert indicator.label.text() == "Checking for mod updates… (+1 more)"
    assert "Refreshing catalog…" in indicator.toolTip()
    indicator.set_tasks([])
    assert indicator.isHidden() and not indicator.spinner.is_spinning()


def test_launch_option_dialog_copies_and_remembers_choice():
    from sailwind_mod_sync.ui.launch_option_dialog import LaunchOptionDialog

    app = QApplication.instance() or QApplication([])
    dialog = LaunchOptionDialog('WINEDLLOVERRIDES="winhttp=n,b" %command%')
    dialog.copy_button.click()
    assert app.clipboard().text() == 'WINEDLLOVERRIDES="winhttp=n,b" %command%'
    assert dialog.copy_button.text() == "Copied"
    assert not dialog.stop_reminding
    dialog.stop_reminding_box.setChecked(True)
    assert dialog.stop_reminding
    dialog.deleteLater()


def test_play_asks_about_proton_launch_option_only_when_needed(window, manager, monkeypatch):
    from sailwind_mod_sync.ui import main_window as main_window_module

    app, window = window
    shown: list[str] = []

    class _Dialog:
        answer = 1
        stop = False

        def __init__(self, suggested, parent):
            shown.append(suggested)
            self.stop_reminding = _Dialog.stop
            self.chose_prefix = False

        def exec(self):
            return _Dialog.answer

    monkeypatch.setattr(main_window_module, "LaunchOptionDialog", _Dialog)

    # Nothing to fix (Windows, or the option is already set): no dialog.
    monkeypatch.setattr(main_window_module, "needs_winhttp_override", lambda: None)
    assert window._confirm_proton_launch_option()
    assert shown == []

    # Missing: the dialog shows the suggestion; Cancel stops Play, Play anyway continues.
    monkeypatch.setattr(main_window_module, "needs_winhttp_override", lambda: "SUGGESTED")
    _Dialog.answer = 0
    assert not window._confirm_proton_launch_option()
    _Dialog.answer = 1
    assert window._confirm_proton_launch_option()
    assert shown == ["SUGGESTED", "SUGGESTED"]

    # "Don't remind me again" sticks, and the check is skipped afterwards.
    _Dialog.stop = True
    assert window._confirm_proton_launch_option()
    assert manager.config.warn_proton_launch_option is False
    assert window._confirm_proton_launch_option()
    assert len(shown) == 3


def test_play_can_set_the_proton_prefix_override_after_consent(window, manager, monkeypatch, tmp_path):
    from sailwind_mod_sync.ui import main_window as main_window_module

    app, window = window
    registry = tmp_path / "user.reg"
    registry.write_text("WINE REGISTRY Version 2\n", encoding="utf-8")
    calls: list[str] = []

    class _Choice:
        def __init__(self, suggested, parent):
            self.stop_reminding = False
            self.chose_prefix = True

        def exec(self):
            return 1

    class _Consent:
        answer = 0

        def __init__(self, path, parent):
            calls.append(f"consent {path}")

        def exec(self):
            return _Consent.answer

    monkeypatch.setattr(main_window_module, "needs_winhttp_override", lambda: "SUGGESTED")
    monkeypatch.setattr(main_window_module, "LaunchOptionDialog", _Choice)
    monkeypatch.setattr(main_window_module, "ProtonPrefixConsentDialog", _Consent)
    monkeypatch.setattr(main_window_module, "sailwind_prefix_registry", lambda: registry)
    monkeypatch.setattr(main_window_module, "set_prefix_winhttp_override", lambda path: calls.append(f"set {path}"))

    # Declining consent changes nothing and doesn't start the game.
    assert not window._confirm_proton_launch_option()
    assert calls == [f"consent {registry}"]

    # Agreeing changes the prefix, then Play continues.
    _Consent.answer = 1
    assert window._confirm_proton_launch_option()
    assert calls[-1] == f"set {registry}"


def test_proton_prefix_consent_dialog_requires_understanding():
    from sailwind_mod_sync.ui.launch_option_dialog import ProtonPrefixConsentDialog

    QApplication.instance() or QApplication([])
    dialog = ProtonPrefixConsentDialog("/home/deck/.../pfx/user.reg")
    assert not dialog.change_button.isEnabled()
    dialog.understand.setChecked(True)
    assert dialog.change_button.isEnabled()
    dialog.deleteLater()
