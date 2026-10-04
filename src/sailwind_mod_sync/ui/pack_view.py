from __future__ import annotations

from contextlib import nullcontext

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from sailwind_mod_sync.catalog.custom import same_repo
from sailwind_mod_sync.catalog.github import repo_page_url
from sailwind_mod_sync.models import CatalogEntry, ModPack, PinnedMod, is_newer
from sailwind_mod_sync.ui.tables import (
    enable_column_resize,
    enable_column_sort,
    sortable_item,
    sorting_paused,
    version_sort_key,
)
from sailwind_mod_sync.ui.version_dialog import BROWSE_VERSIONS, pack_version_items


class _VersionCombo(QComboBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class PackView(QWidget):
    bulk_enabled = Signal(bool)
    toggle_enabled = Signal(str, bool)
    update_requested = Signal(str)
    import_requested = Signal(str)
    import_file_clicked = Signal()
    find_repo_requested = Signal(str)
    show_in_catalog_requested = Signal(str)
    remove_requested = Signal(str)
    version_requested = Signal(str, str, str)
    browse_versions_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shown_pack_id: str | None = None
        self._enabled_by_guid: dict[str, bool] = {}
        self._subtitle_tail = ""
        self._progress_prefix = ""
        self._bulk_busy = False
        self._actions_blocked = False
        self._freeze_bulk_actions = False
        self._bulk_available = (False, False)
        self.title = QLabel("No pack selected")
        self.subtitle = QLabel("")
        self.import_file = QPushButton("Import Mod DLL/ZIP")
        self.import_file.setToolTip("Import a .dll or .zip into the library and add it to this pack")
        self.import_file.setEnabled(False)
        self.import_file.clicked.connect(self.import_file_clicked.emit)
        self.check_all = QPushButton("Check All")
        self.uncheck_all = QPushButton("Uncheck All")
        self.check_all.clicked.connect(lambda: self.bulk_enabled.emit(True))
        self.uncheck_all.clicked.connect(lambda: self.bulk_enabled.emit(False))
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(3000)
        self._progress_timer.timeout.connect(self._reset_operation_progress)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["On", "Mod", "GUID", "Version", "Latest", ""])
        enable_column_resize(self.table, [48, 180, 220, 140, 110, 220])
        enable_column_sort(self.table, default_column=None)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self._row_state: dict[str, tuple[str, bool, bool, bool]] = {}

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        header.addWidget(self.check_all)
        header.addWidget(self.uncheck_all)
        header.addWidget(self.import_file)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.table)

    def set_pack(
        self,
        pack: ModPack | None,
        catalog: list[CatalogEntry],
        missing_guids: set[str] | None = None,
        library_versions: dict[str, list[tuple[str, str]]] | None = None,
        display_names: dict[str, str] | None = None,
    ) -> None:
        pack_id = pack.id if pack else None
        if self._shown_pack_id != pack_id:
            self._progress_timer.stop()
            self._reset_operation_progress()
        self._shown_pack_id = pack_id
        self._enabled_by_guid = {mod.guid: mod.enabled for mod in pack.mods} if pack else {}
        self._bulk_available = (
            bool(pack and any(not mod.enabled for mod in pack.mods)),
            bool(pack and any(mod.enabled for mod in pack.mods)),
        )
        self.set_actions_blocked(self._actions_blocked)
        if pack is None:
            self.title.setText("No pack selected")
            self.subtitle.setText("")
            self.import_file.setEnabled(False)
            self._row_state = {}
            with sorting_paused(self.table):
                self.table.setRowCount(0)
            return
        self.import_file.setEnabled(not self._bulk_busy)
        missing = missing_guids or set()
        versions = library_versions or {}
        latest_by_guid = {}
        latest_version_by_guid = {}
        names = {}
        repo_by_guid = {}
        catalog_guids: set[str] = set()
        for entry in catalog:
            names[entry.primary_guid] = entry.name
            catalog_guids.add(entry.primary_guid)
            catalog_guids.update(entry.guids)
            for guid in entry.guids:
                latest_by_guid[guid] = entry.latest_raw or ""
                latest_version_by_guid[guid] = entry.latest_version
                names.setdefault(guid, entry.name)
                repo_by_guid.setdefault(guid, entry.repo)
        self.title.setText(pack.name)
        missing_count = sum(1 for pinned in pack.mods if pinned.guid in missing)
        self._subtitle_tail = f"BepInEx {pack.bepinex or '—'}"
        if missing_count:
            self._subtitle_tail += f" · {missing_count} missing"
        self._update_subtitle()
        self._row_state = {}
        with sorting_paused(self.table):
            self.table.setRowCount(len(pack.mods))
            for index, pinned in enumerate(pack.mods):
                checkbox = QCheckBox()
                checkbox.setChecked(pinned.enabled)
                guid = pinned.guid
                checkbox.toggled.connect(
                    lambda checked, value=guid, widget=checkbox: self._checkbox_toggled(value, checked, widget)
                )
                wrap = QWidget()
                wrap_layout = QHBoxLayout(wrap)
                wrap_layout.setContentsMargins(8, 0, 0, 0)
                wrap_layout.addWidget(checkbox)
                wrap_layout.addStretch()
                self._enable_row_context_menu(wrap, guid)
                self.table.setItem(index, 0, sortable_item("", 1 if pinned.enabled else 0))
                self.table.setCellWidget(index, 0, wrap)

                name = (display_names or {}).get(pinned.guid) or names.get(pinned.guid, pinned.guid.split(".")[-1])
                self.table.setItem(index, 1, sortable_item(name))
                self.table.setItem(index, 2, sortable_item(pinned.guid))
                self.table.setItem(index, 3, sortable_item(pinned.version_raw or pinned.version, version_sort_key(pinned.version)))
                version_combo = self._version_combo(
                    pinned,
                    versions.get(guid, []),
                    catalog_latest_raw=latest_by_guid.get(guid, ""),
                    catalog_latest_version=latest_version_by_guid.get(guid),
                    has_repo=bool(pinned.repo or repo_by_guid.get(guid, "")),
                    missing=guid in missing,
                )
                self._enable_row_context_menu(version_combo, guid)
                self.table.setCellWidget(index, 3, version_combo)
                latest = latest_by_guid.get(pinned.guid, "")
                is_missing = guid in missing
                can_update = bool(latest and is_newer(latest, pinned.version))
                repo = pinned.repo or repo_by_guid.get(guid, "")
                in_catalog = guid in catalog_guids or any(
                    same_repo(entry.repo, repo) for entry in catalog if repo
                )
                self._row_state[guid] = (repo, is_missing, can_update, in_catalog)
                latest_label = "Missing" if is_missing else latest
                latest_key = (1, version_sort_key(None)) if is_missing else (0, version_sort_key(latest))
                latest_item = sortable_item(latest_label, latest_key)
                if is_missing:
                    latest_item.setToolTip("This version is not in the library. Import a .dll or .zip to add it.")
                elif can_update:
                    latest_item.setText(f"{latest} (update)")
                self.table.setItem(index, 4, latest_item)
                self.table.setItem(index, 5, sortable_item(""))

                actions = QWidget()
                actions_layout = QHBoxLayout(actions)
                actions_layout.setContentsMargins(4, 0, 4, 0)
                if is_missing:
                    import_btn = QPushButton("Import")
                    import_btn.setToolTip("Import a .dll or .zip for this missing mod")
                    import_btn.clicked.connect(lambda _=False, value=guid: self.import_requested.emit(value))
                    actions_layout.addWidget(import_btn)
                else:
                    update_btn = QPushButton("Update")
                    update_btn.setEnabled(can_update)
                    update_btn.clicked.connect(lambda _=False, value=guid: self.update_requested.emit(value))
                    actions_layout.addWidget(update_btn)
                remove_btn = QPushButton("Remove")
                remove_btn.clicked.connect(lambda _=False, value=guid: self.remove_requested.emit(value))
                actions_layout.addWidget(remove_btn)
                self._enable_row_context_menu(actions, guid)
                self.table.setCellWidget(index, 5, actions)

    def refresh_enabled(self, pack: ModPack) -> bool:
        """Update checkbox state without rebuilding rows or rescanning artifacts."""
        by_guid = {mod.guid: mod.enabled for mod in pack.mods}
        if self._shown_pack_id != pack.id or set(self._row_state) != set(by_guid):
            return False
        self._enabled_by_guid = by_guid
        with self._enabled_sorting_paused():
            for row in range(self.table.rowCount()):
                guid = self.table.item(row, 2).text()
                self._set_row_enabled(row, by_guid[guid])
        self._refresh_enabled_header()
        return True

    def mod_enabled_changed(self, guid: str, enabled: bool) -> None:
        """Receive committed state from the worker through a queued Qt signal."""
        if guid not in self._enabled_by_guid:
            return
        self._enabled_by_guid[guid] = enabled
        with self._enabled_sorting_paused():
            for row in range(self.table.rowCount()):
                if self.table.item(row, 2).text() == guid:
                    self._set_row_enabled(row, enabled)
                    break
        self._refresh_enabled_header()

    def _enabled_sorting_paused(self):
        # Enabled changes cannot affect sorting by another column. Avoid
        # toggling table sorting (and its layout) for those ordinary clicks.
        return sorting_paused(self.table) if self.table.horizontalHeader().sortIndicatorSection() == 0 else nullcontext()

    def mod_checkbox(self, guid: str) -> QCheckBox | None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 2).text() == guid:
                return self.table.cellWidget(row, 0).findChild(QCheckBox)
        return None

    def _checkbox_toggled(self, guid: str, enabled: bool, checkbox: QCheckBox) -> None:
        if self._bulk_busy:
            blocked = checkbox.blockSignals(True)
            checkbox.setChecked(self._enabled_by_guid[guid])
            checkbox.blockSignals(blocked)
            return
        self.toggle_enabled.emit(guid, enabled)

    def _set_row_enabled(self, row: int, enabled: bool) -> None:
        checkbox = self.table.cellWidget(row, 0).findChild(QCheckBox)
        blocked = checkbox.blockSignals(True)
        checkbox.setChecked(enabled)
        checkbox.blockSignals(blocked)
        self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, int(enabled))

    def _refresh_enabled_header(self) -> None:
        values = self._enabled_by_guid.values()
        self._bulk_available = (any(not value for value in values), any(self._enabled_by_guid.values()))
        self.set_actions_blocked(self._actions_blocked)
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        if self._shown_pack_id is None:
            self.subtitle.setText("")
            return
        count = f"{sum(self._enabled_by_guid.values())}/{len(self._enabled_by_guid)}"
        if self._progress_prefix in ("All mods enabled", "All mods disabled"):
            message = f"{self._progress_prefix} ({count})"
        else:
            message = f"{self._progress_prefix} {count} mods enabled".strip()
        self.subtitle.setText(f"{message} · {self._subtitle_tail}")

    def show_operation_progress(self, message: str) -> None:
        self._progress_timer.stop()
        self._progress_prefix = message
        color = "#885c00" if self.palette().color(QPalette.ColorRole.Window).lightness() > 128 else "#efc46d"
        self.subtitle.setStyleSheet(f"QLabel {{ color: {color}; }}")
        self._update_subtitle()

    def show_operation_result(self, message: str, *, success: bool) -> None:
        self._progress_timer.stop()
        self._progress_prefix = message
        light = self.palette().color(QPalette.ColorRole.Window).lightness() > 128
        color = ("#1b6e28" if light else "#79cd86") if success else ("#a34119" if light else "#f4a56d")
        self.subtitle.setStyleSheet(f"QLabel {{ color: {color}; }}")
        self._update_subtitle()
        if success:
            self._progress_timer.start()

    def _reset_operation_progress(self) -> None:
        self._progress_prefix = ""
        self.subtitle.setStyleSheet("")
        self.subtitle.setToolTip("")
        self._update_subtitle()

    def set_actions_blocked(self, blocked: bool) -> None:
        self._actions_blocked = blocked
        if self._freeze_bulk_actions:
            return
        self.check_all.setEnabled(self._bulk_available[0] and not self._bulk_busy and not blocked)
        self.uncheck_all.setEnabled(self._bulk_available[1] and not self._bulk_busy and not blocked)

    def clear_operation_status(self) -> None:
        self._progress_timer.stop()
        if self._progress_prefix or self.subtitle.styleSheet():
            self._reset_operation_progress()

    def set_bulk_busy(self, busy: bool, *, visual: bool = True) -> None:
        self._bulk_busy = busy
        self._freeze_bulk_actions = busy and not visual
        self.set_actions_blocked(self._actions_blocked)
        if visual:
            self.import_file.setEnabled(bool(self._row_state) and not busy)
            for row in range(self.table.rowCount()):
                for column in (0, 3, 5):
                    widget = self.table.cellWidget(row, column)
                    if widget is not None:
                        widget.setEnabled(not busy)

    def available_updates(self) -> int:
        """Number of mods in the current pack with a newer version in the catalog."""
        return sum(1 for _, _, can_update, _ in self._row_state.values() if can_update)

    def _version_combo(
        self,
        pinned: PinnedMod,
        library_versions: list[tuple[str, str]],
        *,
        catalog_latest_raw: str,
        catalog_latest_version: str | None,
        has_repo: bool,
        missing: bool,
    ) -> QComboBox:
        combo = _VersionCombo()
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setToolTip("Choose which version this pack uses")
        combo.setProperty("pinned_version", pinned.version)
        current_id = pinned.version
        current_index = 0
        for index, (label, data) in enumerate(
            pack_version_items(
                pinned,
                library_versions,
                catalog_latest_raw=catalog_latest_raw,
                catalog_latest_version=catalog_latest_version,
                has_repo=has_repo,
                missing=missing,
            )
        ):
            combo.addItem(label, data)
            if isinstance(data, tuple) and data and data[0] == current_id:
                current_index = index
        combo.blockSignals(True)
        combo.setCurrentIndex(current_index)
        combo.blockSignals(False)
        guid = pinned.guid
        combo.currentIndexChanged.connect(
            lambda index, widget=combo, value=guid: self._on_version_chosen(value, widget, index)
        )
        return combo

    def _on_version_chosen(self, guid: str, combo: QComboBox, index: int) -> None:
        if self._bulk_busy:
            self._restore_combo(combo)
            return
        if index < 0:
            return
        data = combo.itemData(index)
        if data == BROWSE_VERSIONS:
            self._restore_combo(combo)
            self.browse_versions_requested.emit(guid)
            return
        if not isinstance(data, tuple) or len(data) != 2:
            return
        version, version_raw = str(data[0]), str(data[1])
        self.version_requested.emit(guid, version, version_raw)

    def _restore_combo(self, combo: QComboBox) -> None:
        pinned_version = combo.property("pinned_version")
        combo.blockSignals(True)
        for index in range(combo.count()):
            data = combo.itemData(index)
            if isinstance(data, tuple) and data and data[0] == pinned_version:
                combo.setCurrentIndex(index)
                break
        combo.blockSignals(False)

    def _enable_row_context_menu(self, widget: QWidget, guid: str) -> None:
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, value=guid, host=widget: self._popup_menu_for_guid(value, host.mapToGlobal(pos))
        )

    def _show_table_context_menu(self, pos) -> None:
        row = self.table.indexAt(pos).row()
        item = self.table.item(row, 2)
        if item is None:
            return
        self.table.selectRow(row)
        self._popup_menu_for_guid(item.text(), self.table.viewport().mapToGlobal(pos))

    def _popup_menu_for_guid(self, guid: str, global_pos) -> None:
        menu = self._menu_for_guid(guid)
        if menu is None:
            return
        menu.exec(global_pos)

    def _menu_for_guid(self, guid: str) -> QMenu | None:
        state = self._row_state.get(guid)
        if state is None:
            return None
        repo, missing, can_update, in_catalog = state
        editable = not self._bulk_busy
        menu = QMenu(self)
        page = repo_page_url(repo)
        if page:
            label = "Open GitLab in Browser" if "gitlab.com" in page.lower() else "Open GitHub in Browser"
            open_repo = menu.addAction(label)
            open_repo.setToolTip(page)
            open_repo.triggered.connect(lambda _=False, url=page: QDesktopServices.openUrl(QUrl(url)))
        else:
            add_repo = menu.addAction("Add Repository")
            add_repo.setEnabled(editable)
            add_repo.triggered.connect(lambda _=False, value=guid: self.find_repo_requested.emit(value))
        if in_catalog:
            show_catalog = menu.addAction("Show in Catalog")
            show_catalog.triggered.connect(
                lambda _=False, value=guid: self.show_in_catalog_requested.emit(value)
            )
        menu.addSeparator()
        if missing:
            import_action = menu.addAction("Import")
            import_action.setEnabled(editable)
            import_action.triggered.connect(lambda _=False, value=guid: self.import_requested.emit(value))
        else:
            update_action = menu.addAction("Update")
            update_action.setEnabled(can_update and editable)
            update_action.triggered.connect(lambda _=False, value=guid: self.update_requested.emit(value))
        remove = menu.addAction("Remove")
        remove.setEnabled(editable)
        remove.triggered.connect(lambda _=False, value=guid: self.remove_requested.emit(value))
        return menu
