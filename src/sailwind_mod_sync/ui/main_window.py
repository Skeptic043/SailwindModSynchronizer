from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import wraps
import logging
from pathlib import Path
import subprocess
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QEvent, Signal
from shiboken6 import isValid

from sailwind_mod_sync.catalog.custom import same_repo
from sailwind_mod_sync.catalog.github import GitHubDownloadError
from sailwind_mod_sync.catalog.mvc import find_entry
from sailwind_mod_sync.constants import APP_NAME, APP_REPO, APP_VERSION, CATALOG_REFRESH_HOURS
from sailwind_mod_sync.game.backup import BackupError, inspect_bepinex_zip
from sailwind_mod_sync.game.saves import inspect_saves_zip
from sailwind_mod_sync.library.store import artifact_source
from sailwind_mod_sync.manager import Manager
from sailwind_mod_sync.models import PinnedMod, parse_mod_version
from sailwind_mod_sync.packs.share import DISCORD_MESSAGE_LIMIT, parse_share_text
from sailwind_mod_sync.ui.associate_dialog import AssociateCatalogDialog, AssociateTarget
from sailwind_mod_sync.ui.catalog_view import CatalogView
from sailwind_mod_sync.ui.downloads_window import DownloadsWindow
from sailwind_mod_sync.ui.hidden_mods_dialog import HiddenModsDialog
from sailwind_mod_sync.ui.changelog_dialog import ChangelogDialog
from sailwind_mod_sync.ui.export_dialog import ExportDialog, ExportKind
from sailwind_mod_sync.ui.import_plugins_dialog import ImportPluginsDialog
from sailwind_mod_sync.ui.launch_splash import LaunchSplash
from sailwind_mod_sync.ui.library_view import format_size
from sailwind_mod_sync.ui.links import help_text_to_html
from sailwind_mod_sync.ui.mod_details_dialog import ModDetailsDialog
from sailwind_mod_sync.ui.missing_mods_dialog import MissingModsWarningDialog
from sailwind_mod_sync.ui.pack_view import PackView
from sailwind_mod_sync.ui.progress_dialog import BusyDialog
from sailwind_mod_sync.ui.repo_dialog import RepoUrlDialog
from sailwind_mod_sync.ui.settings_dialog import SettingsDialog
from sailwind_mod_sync.ui.update_dialog import OPEN, SKIP, UPDATE, UpdateDialog
from sailwind_mod_sync.ui.version_dialog import SelectVersionDialog
from sailwind_mod_sync.ui.window_state import restore_window_state, save_window_state
from sailwind_mod_sync.ui.workers import TaskBridge, run_background
from sailwind_mod_sync.updater import (
    AppUpdate,
    download_and_stage_update,
    find_app_update,
    launch_apply_and_exit,
    update_check_due,
    utc_now_iso,
)

log = logging.getLogger(__name__)

PROGRESS_DIALOG_DELAY_MS = 250

# Delay after startup before the catalog is scanned in the background for
# updates, so the window can appear and stay responsive.
AUTO_SCAN_START_DELAY_MS = 2000
# If an automatic scan cannot start because another task is busy, retry after
# this delay instead of dropping the scan.
AUTO_SCAN_RETRY_DELAY_MS = 10000
# A manual "scan updates" click within this window after a scan (automatic or
# manual) just finished asks the user whether they really want to rerun it.
MOD_SCAN_RECENT_SECONDS = 300.0
# Delay after startup before a due catalog refresh starts in the background.
CATALOG_REFRESH_START_DELAY_MS = 1000
# How often an open window checks whether the daily catalog refresh or the app
# update check is due.
PERIODIC_CHECK_INTERVAL_MS = 60 * 60 * 1000

PLAY_BUTTON_STYLE = """
QPushButton {
    background-color: #2e7d32;
    color: white;
    font-weight: 600;
    border: none;
    border-radius: 4px;
    padding: 8px 12px;
}
QPushButton:hover {
    background-color: #388e3c;
}
QPushButton:pressed {
    background-color: #1b5e20;
}
QPushButton:disabled {
    background-color: #81c784;
    color: #e8f5e9;
}
"""

UPDATES_HINT_STYLE = """
QLabel {
    color: #ffffff;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 4px;
    background-color: #2e7d32;
    margin-right: 2px;
    margin-bottom: 1px;
}
"""


def _unless_bulk_running(method):
    """Guard alternate entry points as well as disabled widgets."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if self._bulk_pack_id is not None:
            self.statusBar().showMessage("Wait for the mod changes to finish.")
            return
        return method(self, *args, **kwargs)
    return guarded


class _ModChangeBridge(TaskBridge):
    enabled_changed = Signal(str, bool)


class MainWindow(QMainWindow):
    def __init__(self, manager: Manager) -> None:
        super().__init__()
        self.manager = manager
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1200, 760)
        self._single_input_filter_installed = False
        self._bulk_pack_id: str | None = None
        self._bulk_disabled = []
        self._busy = False
        self._bridge: TaskBridge | None = None
        self._update_bridge: TaskBridge | None = None
        self._on_ok: Callable | None = None
        self._progress_dialog: BusyDialog | None = None
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.timeout.connect(self._show_progress_dialog)
        self._launch_splash: LaunchSplash | None = None
        self._mod_scan_running = False
        self._mod_scan_done_at = float("-inf")
        self._mod_scan_bridge: TaskBridge | None = None
        self._catalog_bridge: TaskBridge | None = None
        self._pending_catalog: list | None = None

        self._updates_hint = QLabel(self.statusBar())
        self._updates_hint.setStyleSheet(UPDATES_HINT_STYLE)
        self._updates_hint.hide()
        self.statusBar().addPermanentWidget(self._updates_hint)

        self.pack_list = QListWidget()
        self.pack_list.currentItemChanged.connect(self._on_pack_selected)
        self.pack_list.itemDoubleClicked.connect(lambda _item: self._rename_pack())
        self.pack_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pack_list.customContextMenuRequested.connect(self._pack_context_menu)

        self.play_button = QPushButton("Play")
        self.play_button.setMinimumHeight(48)
        self.play_button.setStyleSheet(PLAY_BUTTON_STYLE)
        self.play_button.setToolTip("Launch Sailwind with the selected ModPack")
        self.play_button.clicked.connect(self._play)
        self.vanilla_button = QPushButton("Launch Vanilla")
        self.vanilla_button.setToolTip("Start Sailwind without mods (Doorstop disabled)")
        self.vanilla_button.clicked.connect(self._play_vanilla)

        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._new_pack)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._duplicate_pack)
        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._rename_pack)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete_pack)
        export_btn = QPushButton("Export")
        export_btn.clicked.connect(self._export_pack)
        import_btn = QPushButton("Import")
        import_btn.clicked.connect(self._import_pack)
        copy_btn = QPushButton("Copy")
        copy_btn.setToolTip("Copy ModPack to clipboard for Discord or chat")
        copy_btn.clicked.connect(self._copy_pack)
        paste_btn = QPushButton("Paste")
        paste_btn.setToolTip("Paste a ModPack from clipboard")
        paste_btn.clicked.connect(self._paste_pack)

        pack_buttons = QHBoxLayout()
        pack_buttons.addWidget(new_btn)
        pack_buttons.addWidget(dup_btn)
        pack_buttons.addWidget(rename_btn)
        pack_buttons.addWidget(del_btn)

        io_buttons = QHBoxLayout()
        io_buttons.addWidget(export_btn)
        io_buttons.addWidget(import_btn)

        share_buttons = QHBoxLayout()
        share_buttons.addWidget(copy_btn)
        share_buttons.addWidget(paste_btn)

        left = QWidget()
        self._pack_panel = left
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("ModPacks"))
        left_layout.addWidget(self.pack_list, 1)
        left_layout.addLayout(pack_buttons)
        left_layout.addLayout(io_buttons)
        left_layout.addLayout(share_buttons)
        left_layout.addWidget(self.play_button)
        left_layout.addWidget(self.vanilla_button)

        self.pack_view = PackView()
        self.catalog_view = CatalogView()
        self.catalog_view._filter.textChanged.connect(self._disable_bulk_catalog_buttons)
        self.catalog_view.hide_in_pack.toggled.connect(self._disable_bulk_catalog_buttons)
        self._downloads = DownloadsWindow(self)
        self.library_view = self._downloads.view
        self.pack_view.bulk_enabled.connect(self._set_all_mods_enabled)
        self.pack_view.toggle_enabled.connect(self._toggle_mod)
        self.pack_view.update_requested.connect(self._update_mod)
        self.pack_view.update_all_requested.connect(self._update_all_mods)
        self.pack_view.import_requested.connect(self._import_missing_mod)
        self.pack_view.import_file_clicked.connect(self._import_local_mod)
        self.pack_view.find_repo_requested.connect(self._find_pack_repo)
        self.pack_view.show_in_catalog_requested.connect(self._find_library_in_catalog)
        self.pack_view.remove_requested.connect(self._remove_mod)
        self.pack_view.version_requested.connect(self._set_pack_mod_version)
        self.pack_view.browse_versions_requested.connect(self._browse_pack_mod_versions)
        self.catalog_view.install_requested.connect(self._install_from_catalog)
        self.catalog_view.refresh_clicked.connect(self._refresh_catalog)
        self.catalog_view.add_repo_clicked.connect(self._add_catalog_repo)
        self.catalog_view.remove_custom_requested.connect(self._remove_custom_catalog)
        self.catalog_view.hide_requested.connect(self._hide_catalog_mod)
        self.catalog_view.details_requested.connect(self._show_catalog_details)
        self.library_view.add_to_pack_requested.connect(self._add_library_mod)
        self.library_view.find_repo_requested.connect(self._find_library_repo)
        self.library_view.details_requested.connect(self._show_mod_details)
        self.library_view.delete_requested.connect(self._delete_artifact)
        self.library_view.rename_requested.connect(self._rename_library_mod)
        self.library_view.prune_requested.connect(self._prune_library)
        self.library_view.import_clicked.connect(self._import_local_mod)
        self.library_view.find_in_catalog_requested.connect(self._find_library_in_catalog)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.pack_view, "Pack")
        self.tabs.addTab(self.catalog_view, "Catalog")

        self.splitter = QSplitter()
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([280, 920])
        self.setCentralWidget(self.splitter)

        settings_action = self.menuBar().addAction("Settings")
        settings_action.triggered.connect(self._open_settings)
        backup_menu = self.menuBar().addMenu("Backup")
        self.backup_action = backup_menu.addAction("Backup BepInEx")
        self.backup_action.setStatusTip(
            "Zip the current BepInEx folder (game install, or this pack if the game has none)"
        )
        self.backup_action.triggered.connect(self._backup_bepinex)
        self.restore_action = backup_menu.addAction("Restore BepInEx")
        self.restore_action.setStatusTip("Replace the current BepInEx folder from a backup zip")
        self.restore_action.triggered.connect(self._restore_bepinex)
        backup_menu.addSeparator()
        self.backup_saves_action = backup_menu.addAction("Backup saves")
        self.backup_saves_action.setStatusTip(
            "Zip Sailwind save slots from AppData (skips Unity logs)"
        )
        self.backup_saves_action.triggered.connect(self._backup_saves)
        self.restore_saves_action = backup_menu.addAction("Restore saves")
        self.restore_saves_action.setStatusTip(
            "Replace current Sailwind saves from a backup zip (backs up existing saves first)"
        )
        self.restore_saves_action.triggered.connect(self._restore_saves)
        backup_menu.addSeparator()
        import_game_action = backup_menu.addAction("Import game plugins")
        import_game_action.setStatusTip("Create a ModPack from plugins currently in the game BepInEx folder")
        import_game_action.triggered.connect(self._import_game_plugins)
        downloads_menu = self.menuBar().addMenu("Tools")
        manage_downloads = downloads_menu.addAction("Manage downloads…")
        manage_downloads.setStatusTip("Open cached mod downloads")
        manage_downloads.triggered.connect(self._open_downloads)
        import_mod_action = downloads_menu.addAction("Import mod file…")
        import_mod_action.triggered.connect(self._import_local_mod)
        scan_action = downloads_menu.addAction("Scan updates")
        scan_action.setStatusTip("Check GitHub and GitLab for newer catalog versions")
        scan_action.triggered.connect(self._scan_updates)
        open_appdata = downloads_menu.addAction("Open AppData")
        open_appdata.setStatusTip("Open the Sailwind Mod Synchronizer data folder in File Explorer")
        open_appdata.triggered.connect(self._open_appdata)
        self.open_profile_plugins_action = downloads_menu.addAction("Open Plugins Folder")
        self.open_profile_plugins_action.triggered.connect(self._open_profile_plugins)
        self.open_profile_plugins_action.setStatusTip("Open the selected ModPack's BepInEx plugins folder")
        hidden_mods = downloads_menu.addAction("Hidden Mods")
        hidden_mods.setStatusTip("Show catalog mods you hid, and unhide them")
        hidden_mods.triggered.connect(self._manage_hidden_mods)
        downloads_menu.addSeparator()
        clear_cache = downloads_menu.addAction("Clear cache…")
        clear_cache.setStatusTip("Delete downloaded catalogs, mods, BepInEx packs and app updates")
        clear_cache.triggered.connect(self._clear_cache)
        help_menu = self.menuBar().addMenu("Help")
        check_updates = help_menu.addAction("Check for updates…")
        check_updates.triggered.connect(self._check_for_updates)
        changelog = help_menu.addAction("Change log")
        changelog.setStatusTip("See what changed in each version")
        changelog.triggered.connect(self._show_changelog)
        test_splash = help_menu.addAction("Test splash screen")
        test_splash.setStatusTip("Show the Play splash without launching Sailwind")
        test_splash.triggered.connect(self._test_splash)
        about = help_menu.addAction("About")
        about.triggered.connect(self._about)
        self._bulk_actions = [
            settings_action, self.backup_action, self.restore_action,
            self.backup_saves_action, self.restore_saves_action, import_game_action,
            import_mod_action, scan_action, check_updates, hidden_mods, clear_cache,
        ]
        self._pack_buttons = [
            layout.itemAt(index).widget()
            for layout in (pack_buttons, io_buttons, share_buttons)
            for index in range(layout.count())
        ]
        self.statusBar().showMessage("Ready")

        self._reload_packs()
        self._reload_views()
        restore_window_state(self, self.splitter, paths=self.manager.paths)
        if not self.manager.catalog:
            self._refresh_catalog()
        else:
            QTimer.singleShot(CATALOG_REFRESH_START_DELAY_MS, self._maybe_refresh_catalog)
        QTimer.singleShot(4000, self._maybe_check_updates)
        QTimer.singleShot(AUTO_SCAN_START_DELAY_MS, self._maybe_auto_scan_mods)
        self._periodic_checks = QTimer(self)
        self._periodic_checks.setInterval(PERIODIC_CHECK_INTERVAL_MS)
        self._periodic_checks.timeout.connect(self._run_periodic_checks)
        self._periodic_checks.start()

    def current_pack_id(self) -> str | None:
        item = self.pack_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._bulk_pack_id is not None:
            event.ignore()
            self.statusBar().showMessage("Wait for the mod changes to finish before closing.")
            return
        self._downloads._allow_close = True
        self._downloads.close()
        save_window_state(self, self.splitter, paths=self.manager.paths)
        super().closeEvent(event)

    def _reload_packs(self, select_id: str | None = None) -> None:
        current = select_id or self.current_pack_id() or self.manager.config.last_pack_id
        self.pack_list.blockSignals(True)
        self.pack_list.clear()
        selected = None
        for pack in self.manager.packs.list_packs():
            item = QListWidgetItem(pack.name)
            item.setData(Qt.ItemDataRole.UserRole, pack.id)
            self.pack_list.addItem(item)
            if pack.id == current:
                selected = item
        self.pack_list.blockSignals(False)
        if selected:
            self.pack_list.setCurrentItem(selected)
            self.pack_list.scrollToItem(selected)
        elif self.pack_list.count():
            self.pack_list.setCurrentRow(0)

    def _reload_views(self) -> None:
        pack_id = self.current_pack_id()
        if self._bulk_pack_id is not None:
            return
        pack = self.manager.packs.get(pack_id) if pack_id else None
        missing = {mod.guid for mod in self.manager.missing_mods(pack)}
        library = self.manager.library.list_mods()
        library_version_rows = {
            pinned.guid: self.manager.library_versions(pinned.guid, pinned.repo, library)
            for pinned in (pack.mods if pack else [])
        }
        all_packs = self.manager.packs.list_packs()
        display_names = {
            item.guid: self.manager.mod_display_name(
                item.guid,
                plugin_folders=list(item.meta.plugin_folders),
                repo=item.meta.repo,
                library_entries=library,
                packs=all_packs,
            )
            for item in library
        }
        if pack:
            for pinned in pack.mods:
                if pinned.guid in display_names:
                    continue
                display_names[pinned.guid] = self.manager.mod_display_name(
                    pinned.guid,
                    plugin_folders=list(pinned.plugin_folders),
                    repo=pinned.repo,
                    library_entries=library,
                    packs=all_packs,
                )
        self.pack_view.set_pack(pack, self.manager.catalog, missing, library_version_rows, display_names)
        self.catalog_view.set_data(self.manager.catalog, pack, self.manager.config.hidden_catalog_mods)
        pinned_keys = {pinned.guid: self.manager.library.pinned_key(pinned) for pinned in (pack.mods if pack else [])}
        self.library_view.set_entries(library, pack, display_names, pinned_keys)
        game = self.manager.game_dir()
        self.open_profile_plugins_action.setEnabled(pack is not None)
        if game:
            self.statusBar().showMessage(f"Game: {game}")
        else:
            self.statusBar().showMessage("Set the Sailwind folder in Settings")
        self._update_pack_updates_hint()
        self._update_bulk_actions_availability()
        recovery = self.manager.bulk_recovery_path(pack_id) if pack_id else None
        self.play_button.setEnabled(pack is not None and recovery is None and not self._busy)
        self.play_button.setToolTip(
            f"Profile recovery required: {recovery}" if recovery else "Launch Sailwind with the selected ModPack"
        )

    def _update_bulk_actions_availability(self) -> None:
        self.pack_view.set_actions_blocked(
            self._busy or self._mod_scan_running,
            "Checking for updates…" if self._mod_scan_running else "",
        )

    def _update_pack_updates_hint(self) -> None:
        count = self.pack_view.available_updates()
        if count:
            label = "1 mod update available" if count == 1 else f"{count} mod updates available"
            self._updates_hint.setText(label)
            self._updates_hint.show()
        else:
            self._updates_hint.hide()

    def _on_pack_selected(self) -> None:
        if self._bulk_pack_id is not None:
            # Also reject programmatic/accessibility changes; do not save the
            # rejected selection or show a pack different from the active task.
            blocked = self.pack_list.blockSignals(True)
            for row in range(self.pack_list.count()):
                item = self.pack_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == self._bulk_pack_id:
                    self.pack_list.setCurrentItem(item)
                    break
            self.pack_list.blockSignals(blocked)
            return
        pack_id = self.current_pack_id()
        if pack_id:
            self.manager.config.last_pack_id = pack_id
            self.manager.save_config()
        self._reload_views()

    @_unless_bulk_running
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.manager.config, self.manager.paths, self)
        if dialog.exec():
            dialog.apply_to(self.manager.config)
            self.manager.save_config()
            self.manager.reload_http()
            self._reload_views()

    def _show_changelog(self) -> None:
        ChangelogDialog(self).exec()

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            (
                f"{APP_NAME} {APP_VERSION}\n\n"
                "Keeps Sailwind vanilla and launches isolated Doorstop ModPacks.\n\n"
                "MIT License — copy, modify, redistribute, or sell freely.\n\n"
                f"{APP_REPO}"
            ),
        )

    def _maybe_check_updates(self) -> None:
        config = self.manager.config
        if not config.check_for_updates:
            return
        if not update_check_due(config.last_update_check):
            return
        if self._busy:
            QTimer.singleShot(4000, self._maybe_check_updates)
            return
        self._start_silent_update_check()

    def _start_silent_update_check(self) -> None:
        if self._update_bridge is not None:
            return
        log.info("Checking GitHub for app updates")
        bridge = TaskBridge(self)
        self._update_bridge = bridge
        queued = Qt.ConnectionType.QueuedConnection
        bridge.finished.connect(self._silent_update_found, queued)
        bridge.failed.connect(self._silent_update_failed, queued)
        run_background(lambda progress: self._lookup_app_update(False, progress), bridge)

    def _lookup_app_update(self, ignore_skipped: bool, progress) -> object:
        return find_app_update(
            self.manager.http,
            current_version=APP_VERSION,
            skipped_version=self.manager.config.skipped_update_version,
            ignore_skipped=ignore_skipped,
            paths=self.manager.paths,
            progress=progress,
        )

    @Slot(object)
    def _silent_update_found(self, result: object) -> None:
        self._update_bridge = None
        self.manager.config.last_update_check = utc_now_iso()
        self.manager.save_config()
        if isinstance(result, AppUpdate):
            self._offer_app_update(result)

    @Slot(str)
    def _silent_update_failed(self, message: str) -> None:
        self._update_bridge = None
        log.warning("App update check failed: %s", message)

    def _check_for_updates(self) -> None:
        self._run(
            lambda progress: self._lookup_app_update(True, progress),
            self._manual_update_checked,
            "Checking for updates…",
        )

    def _manual_update_checked(self, result: object) -> None:
        self.manager.config.last_update_check = utc_now_iso()
        self.manager.save_config()
        if isinstance(result, AppUpdate):
            self._offer_app_update(result)
            return
        QMessageBox.information(
            self,
            "Up to date",
            f"{APP_NAME} {APP_VERSION} is the latest release on GitHub.",
        )

    def _offer_app_update(self, update) -> None:
        if self._bulk_pack_id is not None:
            QTimer.singleShot(1000, lambda: self._offer_app_update(update))
            return
        dialog = UpdateDialog(update, self)
        dialog.exec()
        choice = dialog.choice()
        if choice == SKIP:
            self.manager.config.skipped_update_version = update.version
            self.manager.save_config()
            self.statusBar().showMessage(f"Skipping {update.version_raw}")
            return
        if choice == OPEN:
            QDesktopServices.openUrl(QUrl(update.html_url))
            return
        if choice != UPDATE:
            return
        self._run(
            lambda progress: download_and_stage_update(
                self.manager.http, update, self.manager.paths, progress=progress
            ),
            self._install_app_update,
            f"Downloading {update.version_raw}…",
        )

    def _install_app_update(self, payload: object) -> None:
        if not isinstance(payload, Path):
            QMessageBox.warning(self, "Update failed", "The update payload was not a folder.")
            return
        try:
            launch_apply_and_exit(payload)
        except Exception as exc:
            QMessageBox.critical(self, "Update failed", str(exc))
            return
        self.statusBar().showMessage("Installing update…")
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(300, app.quit)

    @_unless_bulk_running
    def _new_pack(self) -> None:
        name, ok = QInputDialog.getText(self, "New ModPack", "Name:")
        if not ok or not name.strip():
            return
        pack = self.manager.packs.create(name.strip())
        self.manager.config.last_pack_id = pack.id
        self.manager.save_config()
        self._reload_packs(select_id=pack.id)
        self._reload_views()

    @_unless_bulk_running
    def _duplicate_pack(self) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            return
        source = self.manager.packs.get(pack_id)
        name, ok = QInputDialog.getText(self, "Duplicate ModPack", "Name:", text=f"{source.name} copy")
        if not ok or not name.strip():
            return
        try:
            pack = self.manager.packs.duplicate(pack_id, name.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Could not duplicate ModPack", str(exc))
            self._reload_packs()
            self._reload_views()
            return
        self.manager.config.last_pack_id = pack.id
        self.manager.save_config()
        self._reload_packs(select_id=pack.id)
        self._reload_views()
        self.statusBar().showMessage(f"Duplicated as {pack.name}")

    def _rename_pack(self) -> None:
        pack_id = self.current_pack_id()
        if self._bulk_pack_id is not None:
            self.statusBar().showMessage("Wait for the mod changes to finish.")
            return
        if not pack_id:
            return
        pack = self.manager.packs.get(pack_id)
        name, ok = QInputDialog.getText(self, "Rename ModPack", "Name:", text=pack.name)
        if not ok:
            return
        new_name = name.strip()
        if not new_name or new_name == pack.name:
            return
        self.manager.packs.rename(pack_id, new_name)
        self._reload_packs()
        self._reload_views()
        self.statusBar().showMessage(f"Renamed to {new_name}")

    @_unless_bulk_running
    def _delete_pack(self) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            return
        pack = self.manager.packs.get(pack_id)
        if QMessageBox.question(self, "Delete ModPack", f"Delete {pack.name}?") != QMessageBox.StandardButton.Yes:
            return
        self.manager.packs.delete(pack_id)
        if not self.manager.packs.list_packs():
            created = self.manager.packs.ensure_default()
            self.manager.config.last_pack_id = created.id
        self.manager.save_config()
        self._reload_packs()
        self._reload_views()

    @_unless_bulk_running
    def _pack_context_menu(self, pos) -> None:
        item = self.pack_list.itemAt(pos)
        if item is not None:
            self.pack_list.setCurrentItem(item)
        menu = QMenu(self)
        copy_action = menu.addAction("Copy ModPack to clipboard")
        paste_action = menu.addAction("Paste ModPack from clipboard")
        copy_action.setEnabled(self.current_pack_id() is not None)
        chosen = menu.exec(self.pack_list.mapToGlobal(pos))
        if chosen == copy_action:
            self._copy_pack()
        elif chosen == paste_action:
            self._paste_pack()

    @_unless_bulk_running
    def _copy_pack(self) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            QMessageBox.warning(self, "No pack", "Select a ModPack to copy.")
            return
        pack = self.manager.packs.get(pack_id)
        try:
            text = self.manager.share_pack_text(pack_id)
        except Exception as exc:
            QMessageBox.critical(self, "Copy failed", str(exc))
            return
        QApplication.clipboard().setText(text)
        chars = len(text)
        self.statusBar().showMessage(f"Copied {pack.name} to clipboard ({chars} characters)")
        if chars > DISCORD_MESSAGE_LIMIT:
            QMessageBox.information(
                self,
                "Copied ModPack",
                (
                    f"{pack.name} is on the clipboard ({chars} characters).\n\n"
                    "That is over Discord's usual 2000-character message limit. "
                    "Paste into a Discord code snippet, or use Export for a file."
                ),
            )

    @_unless_bulk_running
    def _paste_pack(self) -> None:
        text = QApplication.clipboard().text()
        try:
            parse_share_text(text)
        except ValueError as exc:
            QMessageBox.warning(self, "Paste ModPack", str(exc))
            return

        def work(progress):
            return self.manager.import_pack_text(text, progress=progress)

        self._run(work, self._imported, "Importing ModPack…")

    @_unless_bulk_running
    def _export_pack(self) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            return
        pack = self.manager.packs.get(pack_id)
        dialog = ExportDialog(pack.name, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind = dialog.kind()
        file_filter = "ModPack JSON (*.json)" if kind is ExportKind.RECIPE else "ModPack bundle (*.zip)"
        path, _ = QFileDialog.getSaveFileName(self, "Export ModPack", f"{pack.id}{kind.suffix}", file_filter)
        if not path:
            return
        dest = Path(path)
        if dest.suffix.lower() != kind.suffix:
            dest = dest.with_suffix(kind.suffix)
        if kind is ExportKind.RECIPE:
            try:
                self.manager.export_pack(pack_id, dest)
            except Exception as exc:
                QMessageBox.critical(self, "Export failed", str(exc))
                return
            self.statusBar().showMessage(f"Exported {dest}")
            return

        def work(progress):
            return self.manager.export_pack(
                pack_id, dest, bundle=True, include_context=kind is ExportKind.FULL, progress=progress
            )

        self._run(work, lambda path: self.statusBar().showMessage(f"Exported {path}"), "Exporting ModPack bundle…")

    @_unless_bulk_running
    def _import_pack(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import ModPack",
            "",
            "ModPack (*.json *.zip)",
        )
        if not path:
            return
        log.info("User chose pack import %s", path)

        def work(progress):
            return self.manager.import_pack(Path(path), progress=progress)

        self._run(work, self._imported, "Importing ModPack…")

    def _imported(self, pack) -> None:
        self.manager.config.last_pack_id = pack.id
        self.manager.save_config()
        self._reload_packs(select_id=pack.id)
        self._reload_views()
        missing = self.manager.missing_mods(pack)
        if missing:
            self.statusBar().showMessage(
                f"Imported {pack.name} · {len(missing)} missing mod(s) — use Import on the Pack tab"
            )
        else:
            self.statusBar().showMessage(f"Imported {pack.name}")

    @_unless_bulk_running
    def _import_game_plugins(self) -> None:
        game = self.manager.game_dir()
        default_path = str(game / "BepInEx" / "plugins") if game is not None else ""
        dialog = ImportPluginsDialog(default_path, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.pack_name()
        plugins_dir = dialog.plugins_dir()

        def work(progress):
            return self.manager.import_game_plugins(
                pack_name=name,
                plugins_dir=plugins_dir,
                progress=progress,
            )

        self._run(work, self._game_plugins_imported, "Importing installed plugins…")

    def _game_plugins_imported(self, pack) -> None:
        self._imported(pack)
        pack = self.manager.packs.get(pack.id)
        self._offer_catalog_association(list(pack.mods), pack_id=pack.id)

    @_unless_bulk_running
    def _backup_bepinex(self) -> None:
        pack_id = self.current_pack_id()
        try:
            source, kind = self.manager.resolve_bepinex_folder(pack_id)
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "No BepInEx folder", str(exc))
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        label = "game" if kind == "game" else "pack"
        default = self.manager.paths.backups_dir / f"BepInEx-{label}-{stamp}.zip"
        self.manager.paths.backups_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Backup BepInEx folder",
            str(default),
            "Zip archive (*.zip)",
        )
        if not path:
            return
        dest = Path(path)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")

        def work(progress):
            return self.manager.backup_bepinex(dest, pack_id=pack_id, source=source, progress=progress)

        self._run(work, self._bepinex_backed_up, f"Backing up {source}…")

    def _bepinex_backed_up(self, result) -> None:
        extra = f" ({result.skipped} skipped)" if result.skipped else ""
        self.statusBar().showMessage(f"Backed up {result.file_count} files{extra} to {result.dest}")
        QMessageBox.information(
            self,
            "Backup complete",
            f"Saved {result.file_count} files from\n{result.source}\n\nto\n{result.dest}{extra}",
        )

    @_unless_bulk_running
    def _restore_bepinex(self) -> None:
        start = str(self.manager.paths.backups_dir)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Restore BepInEx backup",
            start,
            "Zip archive (*.zip)",
        )
        if not path:
            return
        archive = Path(path)
        try:
            inspect_bepinex_zip(archive)
        except BackupError as exc:
            QMessageBox.critical(self, "Not a BepInEx backup", str(exc))
            return
        pack_id = self.current_pack_id()
        try:
            dest, kind = self.manager.resolve_bepinex_restore_target(pack_id)
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "No restore location", str(exc))
            return
        where = "the Sailwind BepInEx folder" if kind == "game" else "this ModPack's BepInEx folder"
        if (
            QMessageBox.question(
                self,
                "Restore BepInEx",
                f"Replace {where}?\n\n{dest}\n\nClose Sailwind first. This cannot be undone except by restoring another backup.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def work(progress):
            return self.manager.restore_bepinex(archive, pack_id=pack_id, dest=dest, progress=progress)

        self._run(work, self._bepinex_restored, f"Restoring {archive.name}…")

    def _bepinex_restored(self, result) -> None:
        self.statusBar().showMessage(f"Restored {result.file_count} files to {result.dest}")
        QMessageBox.information(
            self,
            "Restore complete",
            f"Restored {result.file_count} files to\n{result.dest}",
        )

    @_unless_bulk_running
    def _backup_saves(self) -> None:
        source = self.manager.saves_dir()
        if not source.is_dir():
            QMessageBox.warning(
                self,
                "No save folder",
                f"Sailwind save folder not found:\n{source}",
            )
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        default = self.manager.paths.backups_dir / f"Saves-{stamp}.zip"
        self.manager.paths.backups_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Backup Sailwind saves",
            str(default),
            "Zip archive (*.zip)",
        )
        if not path:
            return
        dest = Path(path)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")

        def work(progress):
            return self.manager.backup_saves(dest, source=source, progress=progress)

        self._run(work, self._saves_backed_up, f"Backing up {source}…")

    def _saves_backed_up(self, result) -> None:
        extra = f" ({result.skipped} skipped)" if result.skipped else ""
        self.statusBar().showMessage(f"Backed up {result.file_count} save files{extra} to {result.dest}")
        QMessageBox.information(
            self,
            "Backup complete",
            f"Saved {result.file_count} files from\n{result.source}\n\nto\n{result.dest}{extra}",
        )

    @_unless_bulk_running
    def _restore_saves(self) -> None:
        start = str(self.manager.paths.backups_dir)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Restore Sailwind saves",
            start,
            "Zip archive (*.zip)",
        )
        if not path:
            return
        archive = Path(path)
        try:
            inspect_saves_zip(archive)
        except BackupError as exc:
            QMessageBox.critical(self, "Not a save backup", str(exc))
            return
        dest = self.manager.saves_dir()
        if (
            QMessageBox.question(
                self,
                "Restore saves",
                (
                    "Replace the current Sailwind saves?\n\n"
                    f"{dest}\n\n"
                    "Existing saves will be backed up automatically first. "
                    "Close Sailwind first."
                ),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def work(progress):
            return self.manager.restore_saves(archive, dest=dest, progress=progress)

        self._run(work, self._saves_restored, f"Restoring {archive.name}…")

    def _saves_restored(self, result) -> None:
        safety = ""
        if result.safety_backup is not None:
            safety = f"\n\nPrevious saves were backed up to\n{result.safety_backup}"
        self.statusBar().showMessage(f"Restored {result.file_count} save files to {result.dest}")
        QMessageBox.information(
            self,
            "Restore complete",
            f"Restored {result.file_count} files to\n{result.dest}{safety}",
        )

    def _refresh_catalog(self) -> None:
        self._run(lambda progress: self.manager.refresh_catalog(progress=progress), self._catalog_loaded, "Refreshing catalog…")

    def _catalog_loaded(self, _result) -> None:
        self._mark_catalog_refreshed()
        self._reload_views()
        self.statusBar().showMessage(f"Catalog: {len(self.manager.catalog)} mods")

    def _mark_catalog_refreshed(self) -> None:
        self.manager.config.last_catalog_refresh = utc_now_iso()
        self.manager.save_config()

    def _run_periodic_checks(self) -> None:
        self._maybe_refresh_catalog()
        self._maybe_check_updates()

    def _catalog_refresh_running(self) -> bool:
        return self._catalog_bridge is not None or self._pending_catalog is not None

    def _catalog_refresh_blocked(self) -> bool:
        return self._busy or self._mod_scan_running or self._bulk_pack_id is not None

    def _maybe_refresh_catalog(self) -> None:
        """Start a quiet background catalog refresh when the daily one is due."""
        config = self.manager.config
        if not config.auto_refresh_catalog or self._catalog_refresh_running():
            return
        if not update_check_due(config.last_catalog_refresh, CATALOG_REFRESH_HOURS):
            return
        if self._catalog_refresh_blocked():
            QTimer.singleShot(AUTO_SCAN_RETRY_DELAY_MS, self._maybe_refresh_catalog)
            return
        log.info("Refreshing the catalog in the background")
        bridge = TaskBridge(self)
        self._catalog_bridge = bridge
        queued = Qt.ConnectionType.QueuedConnection
        bridge.finished.connect(self._background_catalog_fetched, queued)
        bridge.failed.connect(self._background_catalog_failed, queued)
        run_background(lambda progress: self.manager.fetch_catalog(progress=progress), bridge)

    def _clear_catalog_bridge(self) -> None:
        if self._catalog_bridge is not None:
            self._catalog_bridge.deleteLater()
            self._catalog_bridge = None

    @Slot(object)
    def _background_catalog_fetched(self, entries: object) -> None:
        self._clear_catalog_bridge()
        self._pending_catalog = entries if isinstance(entries, list) else None
        self._apply_pending_catalog()

    @Slot(str)
    def _background_catalog_failed(self, message: str) -> None:
        self._clear_catalog_bridge()
        log.warning("Background catalog refresh failed: %s", message)

    def _apply_pending_catalog(self) -> None:
        """Apply a background refresh once no task that reads or changes the catalog is running."""
        entries = self._pending_catalog
        if entries is None:
            return
        if self._catalog_refresh_blocked():
            QTimer.singleShot(AUTO_SCAN_RETRY_DELAY_MS, self._apply_pending_catalog)
            return
        self._pending_catalog = None
        previous = self.manager.catalog
        self.manager.catalog = entries
        self._mark_catalog_refreshed()
        log.info("Background catalog refresh applied: %s mods", len(self.manager.catalog))
        if self.manager.catalog != previous:
            self._reload_views()
            self.statusBar().showMessage(f"Catalog refreshed: {len(self.manager.catalog)} mods")

    @_unless_bulk_running
    def _add_catalog_repo(self) -> None:
        dialog = RepoUrlDialog(
            "",
            self,
            title="Add repository",
            hint=(
                "Paste a GitHub or GitLab repository URL. owner/repo also works. "
                "Each plugin in the latest release is added as its own catalog item, "
                "even when one repository ships several mods."
            ),
        )
        if not dialog.exec():
            return
        url = dialog.repo_url()

        def work(progress):
            return self.manager.add_catalog_repo(url, progress=progress)

        self._run(work, self._catalog_repo_added, "Adding repository…")

    def _catalog_repo_added(self, result) -> None:
        self._reload_views()
        entries = result if isinstance(result, list) else [result]
        names = [getattr(entry, "name", None) or getattr(entry, "primary_guid", "") for entry in entries if entry]
        if not names:
            self.statusBar().showMessage("Catalog: repository added")
            return
        self.tabs.setCurrentWidget(self.catalog_view)
        self.raise_()
        self.activateWindow()
        for entry in entries:
            if entry is None:
                continue
            if self.catalog_view.reveal_mod(
                getattr(entry, "primary_guid", ""), getattr(entry, "repo", "")
            ):
                break
        if len(names) == 1:
            guid = getattr(entries[0], "primary_guid", "")
            extra = f" ({guid})" if guid else ""
            self.statusBar().showMessage(f"Catalog: added {names[0]}{extra}")
            return
        self.statusBar().showMessage(f"Catalog: added {len(names)} mods ({', '.join(names)})")

    @_unless_bulk_running
    def _remove_custom_catalog(self, guid: str, repo: str = "") -> None:
        self.manager.remove_catalog_repo(guid, repo)
        self._reload_views()
        self.statusBar().showMessage(f"Removed {guid} from the catalog")

    @_unless_bulk_running
    def _hide_catalog_mod(self, guid: str) -> None:
        self.manager.hide_catalog_mod(guid)
        self._reload_views()
        self.statusBar().showMessage(f"Hidden {guid} from the catalog")

    @_unless_bulk_running
    def _unhide_catalog_mod(self, guid: str) -> None:
        self.manager.unhide_catalog_mod(guid)
        self._reload_views()
        self.statusBar().showMessage(f"Showing {guid} in the catalog")

    @_unless_bulk_running
    def _manage_hidden_mods(self) -> None:
        rows: list[tuple[str, str]] = []
        for guid in self.manager.config.hidden_catalog_mods:
            entry = find_entry(self.manager.catalog, guid)
            rows.append((guid, entry.name if entry else guid))
        dialog = HiddenModsDialog(rows, self)
        dialog.unhide_requested.connect(self._unhide_catalog_mod)
        dialog.exec()

    @_unless_bulk_running
    def _scan_updates(self) -> None:
        if self._mod_scan_running:
            QMessageBox.information(
                self,
                "Scan in progress",
                "A mod update scan is already running. Please wait for it to finish.",
            )
            return
        if time.monotonic() - self._mod_scan_done_at < MOD_SCAN_RECENT_SECONDS:
            answer = QMessageBox.question(
                self,
                "Scan again?",
                "A mod update scan just finished. Do you still want to run it again?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._run(lambda progress: self.manager.scan_updates(live=True, progress=progress), self._updates_scanned, "Scanning repositories…")

    def _maybe_auto_scan_mods(self) -> None:
        """Kick off a quiet background mod-update scan after startup."""
        if not self.manager.config.auto_scan_mods:
            return
        if not self.manager.config.token():
            log.info("Skipping automatic mod update scan: no GitHub token configured")
            return
        if self._busy or self._mod_scan_running or self._catalog_refresh_running():
            log.info("Deferring automatic mod update scan: another task is running")
            QTimer.singleShot(AUTO_SCAN_RETRY_DELAY_MS, self._maybe_auto_scan_mods)
            return
        log.info("Starting background mod update scan")
        self._start_background_mod_scan()

    def _start_background_mod_scan(self) -> None:
        if self._mod_scan_running or self._busy:
            return
        self._mod_scan_running = True
        self._update_bulk_actions_availability()
        self.statusBar().showMessage("Scanning repositories for updates…")
        bridge = TaskBridge(self)
        self._mod_scan_bridge = bridge
        queued = Qt.ConnectionType.QueuedConnection
        bridge.finished.connect(self._mod_scan_finished, queued)
        bridge.failed.connect(self._mod_scan_failed, queued)
        work = lambda progress: self.manager.scan_updates(live=True, progress=progress)
        QTimer.singleShot(0, lambda: run_background(work, bridge))

    def _clear_mod_scan(self) -> None:
        self._mod_scan_running = False
        self._update_bulk_actions_availability()
        self._mod_scan_done_at = time.monotonic()
        if self._mod_scan_bridge is not None:
            self._mod_scan_bridge.deleteLater()
            self._mod_scan_bridge = None

    def _mod_scan_finished(self, _result) -> None:
        self._clear_mod_scan()
        self._reload_views()
        self.statusBar().showMessage("Update scan complete")

    def _mod_scan_failed(self, message: str) -> None:
        self._clear_mod_scan()
        text = (message or "").strip() or "The task failed."
        if text.startswith("TokenAuthError: "):
            detail = text.removeprefix("TokenAuthError: ")
            log.error("Mod update scan rejected token: %s", detail)
            QMessageBox.warning(self, "Invalid GitHub token", detail)
            self.statusBar().showMessage("Update scan skipped: invalid GitHub token")
        else:
            log.error("Background mod scan failed: %s", text)
            self.statusBar().showMessage(text)
        self._reload_views()

    def _updates_scanned(self, _result) -> None:
        self._reload_views()
        self.statusBar().showMessage("Update scan complete")

    @_unless_bulk_running
    def _install_from_catalog(self, guid: str, repo: str = "") -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            QMessageBox.warning(self, "No pack", "Create or select a ModPack first.")
            return
        pack = self.manager.packs.get(pack_id)
        entry = find_entry(self.manager.catalog, guid, repo)
        pinned = None
        if pack is not None:
            search = list(entry.guids) if entry else [guid]
            if guid not in search:
                search.insert(0, guid)
            for candidate in search:
                pinned = pack.find_mod(candidate)
                if pinned is not None:
                    break
        source = (entry.repo if entry else "") or repo or (pinned.repo if pinned else "")
        name = (entry.name if entry else "") or guid
        current = pinned.version if pinned else ((entry.latest_version if entry else "") or "")
        target_guid = pinned.guid if pinned is not None else (entry.primary_guid if entry else guid)
        chosen = self._choose_mod_version(
            target_guid,
            name=name,
            current_version=current,
            repo=source,
            adding=pinned is None,
        )
        if chosen is None:
            return
        self._set_pack_mod_version(target_guid, chosen[0], chosen[1], repo=source)

    @_unless_bulk_running
    def _update_mod(self, guid: str) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            return
        self._run(
            lambda progress: self.manager.update_mod(pack_id, guid, progress=progress),
            lambda _r: QTimer.singleShot(0, self._reload_views),
            f"Updating {guid}…",
        )

    @_unless_bulk_running
    def _update_all_mods(self, guids: list[str]) -> None:
        pack_id = self.current_pack_id()
        if not pack_id or not guids:
            return
        self._run(
            lambda progress: self.manager.update_mods(pack_id, guids, progress=progress),
            self._mods_updated,
            f"Updating {len(guids)} mods…",
        )

    def _mods_updated(self, result) -> None:
        updated, failures = result
        self._reload_views()
        summary = f"Updated {len(updated)} mod(s)"
        if failures:
            summary = f"{summary}, {len(failures)} could not be updated"
            box = QMessageBox(
                QMessageBox.Icon.Warning,
                "Some mods could not be updated",
                f"{summary}.",
                QMessageBox.StandardButton.Ok,
                self,
            )
            box.setDetailedText("\n\n".join(failures))
            box.exec()
        self.statusBar().showMessage(summary)

    @_unless_bulk_running
    def _remove_mod(self, guid: str) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            return
        self.manager.packs.remove_mod(pack_id, guid)
        self._reload_views()

    @_unless_bulk_running
    def _toggle_mod(self, guid: str, enabled: bool) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            return
        self._start_mod_changes(pack_id, enabled, guid=guid)

    @_unless_bulk_running
    def _add_library_mod(self, guid: str, key: str) -> None:
        meta = self.manager.library.read_mod_meta(guid, key)
        if meta is None:
            self._set_pack_mod_version(guid, key, key)
            return
        self._set_pack_mod_version(
            guid, meta.version, meta.version_raw or meta.version, repo=artifact_source(meta)
        )

    @_unless_bulk_running
    def _set_pack_mod_version(self, guid: str, version: str, version_raw: str = "", *, repo: str = "") -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            QMessageBox.warning(self, "No pack", "Create or select a ModPack first.")
            return
        pack = self.manager.packs.get(pack_id)
        pinned = pack.find_mod(guid) if pack else None
        current = parse_mod_version(pinned.version) if pinned else None
        chosen = parse_mod_version(version) or version
        library = self.manager.library
        same_source = pinned is not None and (not repo or not pinned.repo or same_repo(repo, pinned.repo))
        if same_source and current == chosen and library.has_mod(guid, library.pinned_key(pinned)):
            return
        if library.has_mod(guid, library.artifact_key(guid, version, repo or (pinned.repo if pinned else ""))):
            try:
                pinned = self.manager.set_pack_mod_version(
                    pack_id, guid, version, version_raw or version, repo=repo
                )
            except Exception as exc:
                QMessageBox.warning(self, "Could not change version", str(exc))
                self._reload_views()
                return
            self._reload_views()
            self.statusBar().showMessage(f"Pinned {pinned.guid} {pinned.version} on the pack")
            return
        self._run(
            lambda progress: self.manager.set_pack_mod_version(
                pack_id,
                guid,
                version,
                version_raw or version,
                repo=repo,
                progress=progress,
            ),
            lambda _r: QTimer.singleShot(0, self._reload_views),
            f"Downloading {guid} {version_raw or version}…",
        )

    def _choose_mod_version(
        self,
        guid: str,
        *,
        name: str,
        current_version: str,
        repo: str,
        adding: bool = False,
    ) -> tuple[str, str] | None:
        dialog = SelectVersionDialog(
            guid=guid,
            name=name,
            current_version=current_version,
            library_versions=self.manager.library_versions(guid, repo),
            repo=repo,
            parent=self,
            adding=adding,
        )
        if repo:
            dialog.start_remote(
                lambda progress: self.manager.list_remote_mod_versions(repo, progress=progress)
            )
        if not dialog.exec():
            return None
        return dialog.selected()

    @_unless_bulk_running
    def _browse_pack_mod_versions(self, guid: str) -> None:
        pack_id = self.current_pack_id()
        pack = self.manager.packs.get(pack_id) if pack_id else None
        pinned = pack.find_mod(guid) if pack else None
        if pack is None or pinned is None:
            return
        entry = find_entry(self.manager.catalog, guid, pinned.repo)
        repo = pinned.repo or (entry.repo if entry else "")
        name = self.manager.mod_display_name(
            guid,
            plugin_folders=list(pinned.plugin_folders),
            repo=repo,
        )
        chosen = self._choose_mod_version(
            guid,
            name=name,
            current_version=pinned.version,
            repo=repo,
        )
        if chosen is None:
            return
        self._set_pack_mod_version(guid, chosen[0], chosen[1])

    @_unless_bulk_running
    def _find_pack_repo(self, guid: str) -> None:
        pack_id = self.current_pack_id()
        pack = self.manager.packs.get(pack_id) if pack_id else None
        pinned = pack.find_mod(guid) if pack else None
        if pinned is None:
            return
        name = self.manager.mod_display_name(
            guid,
            plugin_folders=list(pinned.plugin_folders),
            repo=pinned.repo,
        )
        self._offer_catalog_association(
            [pinned],
            names={guid: name},
            force=True,
            pack_id=pack_id,
        )

    @_unless_bulk_running
    def _find_library_repo(self, guid: str, version: str) -> None:
        meta = self.manager.library.read_mod_meta(guid, version)
        pinned = PinnedMod(
            guid=guid,
            version=version,
            repo=meta.repo if meta else "",
            plugin_folders=list(meta.plugin_folders) if meta else [],
            version_raw=meta.version_raw if meta else version,
        )
        name = self.manager.mod_display_name(
            guid,
            plugin_folders=list(pinned.plugin_folders),
            repo=pinned.repo,
        )
        self._offer_catalog_association([pinned], names={guid: name}, force=True)

    @_unless_bulk_running
    def _offer_catalog_association(
        self,
        pins,
        *,
        names: dict[str, str] | None = None,
        force: bool = False,
        pack_id: str | None = None,
    ) -> None:
        targets: list[AssociateTarget] = []
        labels = names or {}
        for pin in pins:
            catalog_hit = find_entry(self.manager.catalog, pin.guid)
            if not force:
                if catalog_hit:
                    if not pin.repo:
                        self.manager.set_mod_repo(pin.guid, catalog_hit.repo, pack_id)
                    continue
            name = labels.get(pin.guid) or self.manager.mod_display_name(
                pin.guid,
                plugin_folders=list(pin.plugin_folders),
                repo=pin.repo,
            )
            targets.append(
                AssociateTarget(
                    guid=pin.guid,
                    version=self.manager.library.pinned_key(pin),
                    name=name,
                    repo=pin.repo,
                )
            )
        if not targets:
            self._reload_views()
            return
        dialog = AssociateCatalogDialog(targets, self.manager.catalog, self)
        if not dialog.exec():
            self._reload_views()
            return
        associated = 0
        for choice in dialog.choices():
            try:
                self.manager.associate_mod(
                    choice.guid,
                    choice.version,
                    catalog_entry=choice.entry,
                    repo=choice.repo,
                    pack_id=pack_id,
                )
                associated += 1
            except Exception as exc:
                QMessageBox.warning(self, "Could not associate", str(exc))
        self._reload_views()
        if associated:
            self.statusBar().showMessage(f"Associated {associated} plugin(s) with GitHub")

    @_unless_bulk_running
    def _rename_library_mod(self, guid: str) -> None:
        current = self.manager.mod_display_name(guid)
        default = self.manager.mod_display_name(guid, alias="")
        name, ok = QInputDialog.getText(
            self,
            "Rename mod",
            (
                f"Display name for {guid}.\n"
                f"Leave empty to use the default name ({default})."
            ),
            text=current,
        )
        if not ok:
            return
        shown = self.manager.set_mod_alias(guid, name)
        self._reload_views()
        self.statusBar().showMessage(f"Showing {guid} as {shown}")

    def _find_library_in_catalog(self, guid: str) -> None:
        repo = ""
        pack_id = self.current_pack_id()
        pack = self.manager.packs.get(pack_id) if pack_id else None
        pinned = pack.find_mod(guid) if pack else None
        if pinned and pinned.repo:
            repo = pinned.repo
        if not repo:
            for entry in self.manager.library.list_mods():
                if entry.guid == guid:
                    repo = entry.meta.repo
                    break
        catalog_entry = find_entry(self.manager.catalog, guid, repo) or find_entry(self.manager.catalog, guid)
        if catalog_entry is None and repo:
            catalog_entry = next(
                (item for item in self.manager.catalog if same_repo(item.repo, repo)),
                None,
            )
        if catalog_entry is None:
            QMessageBox.information(
                self,
                "Not in catalog",
                f"{guid} is not in the catalog. Use Add Repository to link it to a GitHub repository.",
            )
            return
        self.manager.unhide_catalog_mod(catalog_entry.primary_guid)
        self._reload_views()
        self.tabs.setCurrentWidget(self.catalog_view)
        self.raise_()
        self.activateWindow()
        if self.catalog_view.reveal_mod(catalog_entry.primary_guid, catalog_entry.repo):
            self.statusBar().showMessage(f"Showing {catalog_entry.name} in Catalog")
            return
        QMessageBox.information(
            self,
            "Not in catalog",
            f"{guid} is not in the catalog. Use Add Repository to link it to a GitHub repository.",
        )

    def _show_catalog_details(self, guid: str, repo: str = "") -> None:
        try:
            details = self.manager.catalog_mod_details(guid, repo)
        except Exception as exc:
            QMessageBox.warning(self, "Mod details", str(exc))
            return
        self._open_mod_details(details)

    def _show_mod_details(self, guid: str, version: str) -> None:
        try:
            details = self.manager.local_mod_details(guid, version)
        except Exception as exc:
            QMessageBox.warning(self, "Mod details", str(exc))
            return
        self._open_mod_details(details)

    def _open_mod_details(self, details) -> None:
        dialog = ModDetailsDialog(details, self)
        repo = details.repo

        def work(progress):
            return self.manager.fetch_remote_mod_info(repo, progress=progress)

        if repo:
            dialog.start_remote(work)
        dialog.exec()

    @_unless_bulk_running
    def _delete_artifact(self, guid: str, version: str) -> None:
        self.manager.library.delete_mod(guid, version)
        self._reload_views()

    @_unless_bulk_running
    def _prune_library(self) -> None:
        removed = self.manager.prune_library()
        self._reload_views()
        self.statusBar().showMessage(f"Pruned {removed} unused artifact(s)")

    @_unless_bulk_running
    def _import_local_mod(self) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            QMessageBox.warning(self, "No pack", "Create or select a ModPack first.")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import mod file",
            "",
            "Mod files (*.dll *.zip);;DLL (*.dll);;Zip (*.zip)",
        )
        if not paths:
            return
        files = [Path(item) for item in paths]

        def work(progress):
            imported = []
            for path in files:
                imported.append(self.manager.import_local_mod(path, pack_id, progress=progress))
            return imported

        self._run(work, self._local_mods_imported, f"Importing {files[0].name}…")

    @_unless_bulk_running
    def _import_missing_mod(self, guid: str) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            QMessageBox.warning(self, "No pack", "Create or select a ModPack first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Import file for {guid}",
            "",
            "Mod files (*.dll *.zip);;DLL (*.dll);;Zip (*.zip)",
        )
        if not path:
            return
        file_path = Path(path)

        def work(progress):
            return [self.manager.import_local_mod(file_path, pack_id, progress=progress, guid=guid)]

        self._run(work, self._local_mods_imported, f"Importing {file_path.name}…")

    def _local_mods_imported(self, imported) -> None:
        self._reload_views()
        if not imported:
            return
        names = ", ".join(f"{item.guid} {item.version}" for item in imported)
        self.statusBar().showMessage(f"Imported {names}")
        self._offer_catalog_association(list(imported), pack_id=self.current_pack_id())

    @_unless_bulk_running
    def _play(self) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            return
        recovery = self.manager.bulk_recovery_path(pack_id)
        if recovery is not None:
            QMessageBox.warning(self, "Profile recovery required", f"Repair this profile before playing. See {recovery}")
            return
        if not self._confirm_missing_mods(pack_id):
            return

        def work(progress):
            return self.manager.play(pack_id, progress=progress)

        self._run(
            work,
            lambda proc: self._sailwind_started(proc, pack_id=pack_id),
            "Preparing ModPack…",
        )

    def _confirm_missing_mods(self, pack_id: str) -> bool:
        if not self.manager.config.warn_missing_mods:
            return True
        pack = self.manager.packs.get(pack_id)
        missing = self.manager.undownloadable_mods(pack)
        if not missing:
            return True
        dialog = MissingModsWarningDialog(missing, pack.name, self)
        if not dialog.exec():
            return False
        if dialog.stop_reminding:
            self.manager.config.warn_missing_mods = False
            self.manager.save_config()
        return True

    @_unless_bulk_running
    def _play_vanilla(self) -> None:
        try:
            proc = self.manager.play_vanilla()
        except Exception as exc:
            QMessageBox.critical(self, "Launch failed", str(exc))
            return
        self._sailwind_started(proc, pack_id=None, vanilla=True)

    def _sailwind_started(self, result: object, pack_id: str | None, *, vanilla: bool = False) -> None:
        process = result if isinstance(result, subprocess.Popen) else None
        heading = "Starting Sailwind (vanilla)" if vanilla else "Starting Sailwind"
        pack = self.manager.packs.get(pack_id) if pack_id else None
        if pack is not None:
            heading = f"Starting Sailwind — {pack.name}"
        missing = self.manager.missing_mods(pack) if pack is not None else []
        status = "Waiting for Steam to open Sailwind…"
        if missing:
            noun = "mod" if len(missing) == 1 else "mods"
            status = f"{status} {len(missing)} {noun} could not be downloaded and will not load."
            self._reload_views()
        self._open_launch_splash(LaunchSplash(self, process, heading=heading), status=status)

    def _test_splash(self) -> None:
        heading = "Starting Sailwind"
        pack_id = self.current_pack_id()
        pack = self.manager.packs.get(pack_id) if pack_id else None
        if pack is not None:
            heading = f"Starting Sailwind — {pack.name}"
        self._open_launch_splash(
            LaunchSplash(self, None, heading=heading, preview=True),
            status="Splash preview — click Hide to close",
        )

    def _open_launch_splash(self, splash: LaunchSplash, *, status: str) -> None:
        previous = self._launch_splash
        if previous is not None:
            previous.close()
            previous.deleteLater()
        self._launch_splash = splash
        splash.finished.connect(lambda _=0: self._launch_splash_closed(splash))
        splash.show()
        splash.raise_()
        splash.activateWindow()
        self.statusBar().showMessage(status)

    def _launch_splash_closed(self, splash: LaunchSplash) -> None:
        if self._launch_splash is splash:
            self._launch_splash = None
            if splash._preview:
                self.statusBar().showMessage("Ready")
            else:
                self.statusBar().showMessage("Sailwind launched")

    def _run(self, fn, on_ok, busy_message: str) -> None:
        if self._busy:
            self.statusBar().showMessage("Already working…")
            log.info("Ignored overlapping task: %s", busy_message)
            return
        log.info("Starting UI task: %s", busy_message)
        self._busy = True
        self._update_bulk_actions_availability()
        self._on_ok = on_ok
        self.play_button.setEnabled(False)
        self.vanilla_button.setEnabled(False)
        self.backup_action.setEnabled(False)
        self.restore_action.setEnabled(False)
        self.backup_saves_action.setEnabled(False)
        self.restore_saves_action.setEnabled(False)
        self._set_views_enabled(False)
        self.statusBar().showMessage(busy_message)

        # Only show the dialog for tasks that outlast PROGRESS_DIALOG_DELAY_MS,
        # so quick ones (like updating to an already-downloaded mod) don't flash it.
        dialog = BusyDialog(self, "Working", busy_message)
        self._progress_dialog = dialog
        self._progress_timer.start(PROGRESS_DIALOG_DELAY_MS)
        QApplication.processEvents()

        bridge = TaskBridge(self)
        self._bridge = bridge
        queued = Qt.ConnectionType.QueuedConnection
        bridge.progress.connect(self._on_progress, queued)
        bridge.finished.connect(self._on_task_ok, queued)
        bridge.failed.connect(self._on_fail, queued)
        QTimer.singleShot(0, lambda: run_background(fn, bridge))

    def _set_all_mods_enabled(self, enabled: bool) -> None:
        pack_id = self.current_pack_id()
        if pack_id:
            self._start_mod_changes(pack_id, enabled)

    def _start_mod_changes(self, pack_id: str, enabled: bool, *, guid: str | None = None) -> None:
        if self._busy or self._mod_scan_running:
            # A clicked checkbox has already changed visually. Restore the
            # persisted state when a scan or another operation refuses the click.
            self.pack_view.refresh_enabled(self.manager.packs.get(pack_id))
            self._update_bulk_actions_availability()
            self.statusBar().showMessage("Wait for the current operation to finish.")
            return
        pack = self.manager.packs.get(pack_id)
        if not any(mod.enabled != enabled and (guid is None or mod.guid == guid) for mod in pack.mods):
            self.pack_view.refresh_enabled(pack)
            return
        if guid is None:
            self.pack_view.refresh_enabled(pack)
        else:
            self.pack_view.clear_operation_status()
        self._busy = True
        self._bulk_pack_id = pack_id
        self._bulk_target_enabled = enabled
        self._bulk_guid = guid
        self.pack_view.set_bulk_busy(True, visual=guid is None)
        self._update_bulk_actions_availability()
        controls = []
        if guid is None:
            controls.extend([self.pack_list, self.play_button, self.vanilla_button])
            controls.extend([*self._pack_buttons, *self._bulk_actions])
            for view in (self.catalog_view, self.library_view):
                controls.extend(view.findChildren(QPushButton))
                controls.extend(view.findChildren(QComboBox))
        else:
            self._set_single_input_blocked(True)
        self._bulk_disabled = [(control, control.isEnabled()) for control in controls]
        for control, _ in self._bulk_disabled:
            control.setEnabled(False)
        if guid is None:
            message = "Enabling…" if enabled else "Disabling…"
            self.pack_view.show_operation_progress(message)
        bridge = _ModChangeBridge(self)
        self._bridge = bridge
        queued = Qt.ConnectionType.QueuedConnection
        if guid is None:
            bridge.progress.connect(self.pack_view.subtitle.setToolTip, queued)
        bridge.enabled_changed.connect(self.pack_view.mod_enabled_changed, queued)
        bridge.finished.connect(self._bulk_finished, queued)
        bridge.failed.connect(self._bulk_failed, queued)
        if guid is None:
            work = lambda progress: self.manager.set_all_mods_enabled(
                pack_id, enabled, progress, on_changed=bridge.enabled_changed.emit
            )
        else:
            work = lambda progress: self.manager.set_mods_enabled(
                pack_id, enabled, guids={guid}, progress=progress, on_changed=bridge.enabled_changed.emit
            )
        try:
            self._bulk_thread = run_background(work, bridge)
        except Exception as exc:
            self._bulk_failed(str(exc))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if self._single_input_filter_installed:
            kind = event.type()
            if kind == QEvent.Type.Shortcut:
                # QAction and QShortcut can activate without a mouse event on
                # a widget. Limit interception to objects owned by this window.
                owner = watched
                while owner is not None:
                    if owner is self:
                        event.accept()
                        return True
                    owner = owner.parent()
            blocked_events = (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick, QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride,
                QEvent.Type.ContextMenu, QEvent.Type.Wheel,
                QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd,
                QEvent.Type.InputMethod,
            )
            if kind in blocked_events and isinstance(watched, QWidget):
                blocked = watched is self._pack_panel or self._pack_panel.isAncestorOf(watched)
                if not blocked:
                    # Leave the table viewport and scrollbars usable. Its cell
                    # controls remain visible but cannot accept mutation input.
                    in_table = self.pack_view.table.isAncestorOf(watched)
                    if kind == QEvent.Type.Wheel and in_table:
                        return super().eventFilter(watched, event)
                    roots = (
                        self.pack_view.update_all,
                        self.pack_view.check_all,
                        self.pack_view.uncheck_all,
                        self.pack_view.import_file,
                    )
                    blocked = any(watched is root or root.isAncestorOf(watched) for root in roots)
                    cell = watched
                    while not blocked and cell is not None:
                        if cell.parentWidget() is self.pack_view.table.viewport():
                            blocked = True
                            break
                        cell = cell.parentWidget()
                if blocked:
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def _set_single_input_blocked(self, blocked: bool) -> None:
        app = QApplication.instance()
        if app is None or blocked == self._single_input_filter_installed:
            return
        self._single_input_filter_installed = blocked
        if blocked:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)


    def _disable_bulk_catalog_buttons(self, *_args) -> None:
        if self._bulk_pack_id is not None and self._bulk_guid is None:
            # Filtering recreates row buttons. Capture their own enabled state
            # before disabling them so completion does not need a table rebuild.
            captured = {id(control) for control, _ in self._bulk_disabled}
            for button in self.catalog_view.findChildren(QPushButton):
                if id(button) not in captured:
                    self._bulk_disabled.append((button, button.isEnabled()))
                button.setEnabled(False)

    def _finish_bulk(self):
        pack_id = self._bulk_pack_id
        self._set_single_input_blocked(False)
        self._busy = False
        self._bulk_pack_id = None
        for control, enabled in self._bulk_disabled:
            if isValid(control):
                control.setEnabled(enabled)
        self._bulk_disabled = []
        self._update_bulk_actions_availability()
        self.pack_view.set_bulk_busy(False, visual=self._bulk_guid is None)
        if self._bridge is not None:
            self._bridge.deleteLater()
            self._bridge = None
        pack = self.manager.packs.get(pack_id)
        if self.current_pack_id() == pack_id and self.pack_view.refresh_enabled(pack):
            self.catalog_view._pack = pack
            recovery = self.manager.bulk_recovery_path(pack_id)
            self.play_button.setEnabled(recovery is None)
            self.play_button.setToolTip(
                f"Profile recovery required: {recovery}" if recovery else "Launch Sailwind with the selected ModPack"
            )
        else:
            self._reload_views()
        return pack

    @Slot(object)
    def _bulk_finished(self, result) -> None:
        changed, failures = result
        pack = self._finish_bulk()
        requested = [mod for mod in pack.mods if self._bulk_guid is None or mod.guid == self._bulk_guid]
        success = not failures and bool(requested) and all(mod.enabled == self._bulk_target_enabled for mod in requested)
        summary = f"Changed {changed} mod(s)."
        if success:
            state = "enabled" if self._bulk_target_enabled else "disabled"
            summary = f"All mods {state}" if self._bulk_guid is None else f"Mod {state}"
        elif not failures:
            failures = ["The requested enabled state was not reached. Check the profile before trying again."]
        result_message = (summary if self._bulk_guid is None else "") if success else "Some mods could not be changed"
        if self._bulk_guid is None:
            self.pack_view.show_operation_result(result_message, success=success)
        if failures:
            summary = (f"{summary} {len(failures)} failed." if self._bulk_guid is None
                       else "The mod could not be changed. Check the details before trying again.")
            title = "Some mods could not be changed" if self._bulk_guid is None else "Could not change mod"
            box = QMessageBox(QMessageBox.Icon.Warning, title, summary,
                              QMessageBox.StandardButton.Ok, self)
            box.setDetailedText("\n".join(failures))
            box.exec()
        self.statusBar().showMessage(summary)

    @Slot(str)
    def _bulk_failed(self, message: str) -> None:
        self._finish_bulk()
        if self._bulk_guid is None:
            self.pack_view.show_operation_result("Could not change mods", success=False)
        title = "Could not change mods" if self._bulk_guid is None else "Could not change mod"
        QMessageBox.warning(self, title, message)
        self.statusBar().showMessage(message)

    def _open_downloads(self) -> None:
        self._downloads.show()
        self._downloads.raise_()
        self._downloads.activateWindow()

    def _open_profile_plugins(self) -> None:
        pack_id = self.current_pack_id()
        if not pack_id:
            return
        folder = self.manager.packs.plugins_dir(pack_id)
        if not folder.is_dir():
            QMessageBox.warning(self, "Open Plugins Folder", f"The plugin folder does not exist: {folder}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "Open Plugins Folder", f"Could not open {folder}")
            return
        self.statusBar().showMessage(f"Opened {folder}")


    @_unless_bulk_running
    def _clear_cache(self) -> None:
        if self._busy or self._mod_scan_running or self._catalog_refresh_running():
            QMessageBox.information(
                self,
                "Clear cache",
                "Wait for the current task, update check or catalog refresh to finish, then try again.",
            )
            return
        choice = self._confirm_clear_cache(self.manager.cache_size(), self.manager.imported_mods_size())
        if choice is None:
            return
        include_imported = choice
        self._run(
            lambda progress: self.manager.clear_cache(progress=progress, include_imported=include_imported),
            self._cache_cleared,
            "Clearing cache…",
        )

    def _confirm_clear_cache(self, size: int, imported_size: int) -> bool | None:
        """Ask whether to clear the cache; return None to cancel, otherwise whether to delete imported mods too."""
        box = QMessageBox(
            QMessageBox.Icon.Question,
            "Clear cache",
            (
                f"Delete {format_size(size)} of downloaded data?\n\n"
                "This removes the cached catalogs, update check results, downloaded mods, "
                "BepInEx packs and app updates. Packs and their mod settings, your own catalog "
                "entries and backups are kept.\n\n"
                "The catalog downloads again right away. Packs show their downloaded mods as not "
                "downloaded until you play them, which downloads the mods again."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
        )
        if imported_size:
            imported = QCheckBox(f"Also delete mods imported from files ({format_size(imported_size)})")
            imported.setToolTip(
                "Imported mods may have no release to download them from again, such as your own builds. "
                "Packs that use them then need the files imported again."
            )
            box.setCheckBox(imported)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return None
        return bool(box.checkBox() and box.checkBox().isChecked())

    def _cache_cleared(self, result) -> None:
        self._reload_views()
        summary = f"Cleared {format_size(result.freed_bytes)} of cached data"
        if result.failures:
            box = QMessageBox(
                QMessageBox.Icon.Warning,
                "Some files could not be deleted",
                f"{summary}. {len(result.failures)} item(s) are in use or protected and were kept.",
                QMessageBox.StandardButton.Ok,
                self,
            )
            box.setDetailedText("\n".join(result.failures))
            box.exec()
        self.statusBar().showMessage(summary)
        self._run(
            lambda progress: self.manager.refresh_catalog(progress=progress),
            lambda loaded: self._catalog_reloaded_after_clear(loaded, summary),
            "Downloading the catalog…",
        )

    def _catalog_reloaded_after_clear(self, loaded, summary: str) -> None:
        self._catalog_loaded(loaded)
        self.statusBar().showMessage(f"{summary}. Catalog: {len(self.manager.catalog)} mods")

    def _open_appdata(self) -> None:
        self.manager.paths.ensure()
        folder = self.manager.paths.root
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self, "Open AppData", f"Could not open {folder}")
            return
        self.statusBar().showMessage(f"Opened {folder}")

    def _set_views_enabled(self, enabled: bool) -> None:
        self.catalog_view.setEnabled(enabled)
        self.pack_view.setEnabled(enabled)
        self._downloads.setEnabled(enabled)

    def _show_progress_dialog(self) -> None:
        dialog = self._progress_dialog
        if dialog is None:
            return
        dialog.show()
        dialog.raise_()

    def _close_progress(self) -> None:
        self._progress_timer.stop()
        dialog = self._progress_dialog
        self._progress_dialog = None
        if dialog is None:
            return
        dialog.allow_close()
        dialog.hide()
        dialog.deleteLater()

    def _clear_busy(self) -> None:
        self._busy = False
        self._on_ok = None
        if self._bridge is not None:
            self._bridge.deleteLater()
            self._bridge = None
        pack_id = self.current_pack_id()
        recovery = self.manager.bulk_recovery_path(pack_id) if pack_id else None
        self.play_button.setEnabled(bool(pack_id) and recovery is None)
        self.play_button.setToolTip(
            f"Profile recovery required: {recovery}" if recovery else "Launch Sailwind with the selected ModPack"
        )
        self.vanilla_button.setEnabled(True)
        self.backup_action.setEnabled(True)
        self.restore_action.setEnabled(True)
        self.backup_saves_action.setEnabled(True)
        self.restore_saves_action.setEnabled(True)
        self._set_views_enabled(True)
        self._update_bulk_actions_availability()

    @Slot(str)
    def _on_progress(self, message: str) -> None:
        self.statusBar().showMessage(message)
        if self._progress_dialog is not None:
            self._progress_dialog.set_message(message)

    @Slot(object)
    def _on_task_ok(self, result: object) -> None:
        callback = self._on_ok
        self._close_progress()
        self._clear_busy()
        log.info("UI task finished")
        if callback is not None:
            callback(result)

    @Slot(str)
    def _on_fail(self, message: str) -> None:
        self._close_progress()
        self._clear_busy()
        text = (message or "").strip() or "The task failed."
        log.error("UI task failed: %s", text)
        if GitHubDownloadError.is_help_text(text):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(GitHubDownloadError.title)
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setText(help_text_to_html(text))
            for label in box.findChildren(QLabel):
                label.setOpenExternalLinks(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            box.exec()
        else:
            QMessageBox.critical(self, "Error", text)
        self.statusBar().showMessage(text)
        self._reload_views()
