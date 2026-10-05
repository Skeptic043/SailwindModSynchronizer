from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from sailwind_mod_sync.catalog.custom import same_repo, source_key
from sailwind_mod_sync.catalog.github import repo_short_name
from sailwind_mod_sync.catalog.mvc import find_entry
from sailwind_mod_sync.models import CatalogEntry, ModPack, PinnedMod, is_newer
from sailwind_mod_sync.ui.links import repo_button
from sailwind_mod_sync.ui.tables import (
    enable_column_resize,
    enable_column_sort,
    sortable_item,
    sorting_paused,
    version_sort_key,
)

NAME_COLUMN, SOURCE_COLUMN, GUID_COLUMN, LATEST_COLUMN, STATUS_COLUMN, ACTIONS_COLUMN = range(6)
_SOURCE_KEY_ROLE = Qt.ItemDataRole.UserRole + 1


class CatalogView(QWidget):
    install_requested = Signal(str, str)
    remove_custom_requested = Signal(str, str)
    hide_requested = Signal(str)
    details_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[CatalogEntry] = []
        self._entries_by_key: dict[str, CatalogEntry] = {}
        self._pack: ModPack | None = None
        self._hidden_guids: set[str] = set()
        self._revealed_key = ""
        self._syncing_table = False
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter catalog…")
        self._filter.textChanged.connect(self._apply_filter)
        self.hide_in_pack = QCheckBox("Hide mods in current pack")
        self.hide_in_pack.setToolTip("Hide catalog rows that are already pinned on the selected pack")
        self.hide_in_pack.toggled.connect(self._apply_filter)

        refresh = QPushButton("Refresh catalog")
        self.refresh_clicked = refresh.clicked
        add_repo = QPushButton("Add GitHub repo")
        add_repo.setToolTip(
            "Add a GitHub or GitLab repository that is not in ModVersionChecker, "
            "or another source of a mod that is"
        )
        self.add_repo_clicked = add_repo.clicked

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Source", "GUID", "Latest", "Status", ""])
        enable_column_resize(self.table, [180, 170, 240, 90, 140, 260])
        enable_column_sort(self.table, default_column=NAME_COLUMN)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setToolTip("Double-click a mod for details")
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        top = QHBoxLayout()
        top.addWidget(self._filter, 1)
        top.addWidget(self.hide_in_pack)
        top.addWidget(add_repo)
        top.addWidget(refresh)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)

    def set_data(
        self,
        entries: list[CatalogEntry],
        pack: ModPack | None,
        hidden_guids: set[str] | list[str] | None = None,
    ) -> None:
        self._entries = entries
        self._entries_by_key = {source_key(entry): entry for entry in entries}
        self._pack = pack
        self._hidden_guids = {guid for guid in (hidden_guids or []) if guid}
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._filter.text().strip().lower()
        hide_in_pack = self.hide_in_pack.isChecked()
        rows = [
            entry
            for entry in self._entries
            if _entry_matches_filter(entry, query)
            and not _entry_is_hidden(entry, self._hidden_guids)
            and not (hide_in_pack and _pinned_from_entry(entry, self._pack) is not None)
        ]
        in_pack_bg = _in_pack_row_background(self.table)
        palette = self.table.palette()
        revealed_bg = palette.color(QPalette.ColorRole.Highlight)
        revealed_fg = palette.color(QPalette.ColorRole.HighlightedText)
        self._syncing_table = True
        try:
            with sorting_paused(self.table):
                self.table.setRowCount(len(rows))
                for index, entry in enumerate(rows):
                    key = source_key(entry)
                    name_item = sortable_item(entry.name)
                    name_item.setData(_SOURCE_KEY_ROLE, key)
                    self.table.setItem(index, NAME_COLUMN, name_item)
                    source_item = sortable_item(repo_short_name(entry.repo))
                    source_item.setToolTip(entry.repo)
                    self.table.setItem(index, SOURCE_COLUMN, source_item)
                    guid_item = sortable_item(entry.guid_label, entry.primary_guid.casefold())
                    guid_item.setToolTip("\n".join(entry.guids))
                    self.table.setItem(index, GUID_COLUMN, guid_item)
                    latest = entry.latest_raw or ("none" if not entry.available else "")
                    latest_key = (0 if entry.available else 1, version_sort_key(entry.latest_raw))
                    self.table.setItem(index, LATEST_COLUMN, sortable_item(latest, latest_key))
                    self.table.setItem(index, STATUS_COLUMN, sortable_item(_catalog_status(entry, self._pack)))
                    self.table.setItem(index, ACTIONS_COLUMN, sortable_item(""))
                    actions = self._row_actions(entry)
                    self.table.setCellWidget(index, ACTIONS_COLUMN, actions)
                    if key == self._revealed_key:
                        _paint_row(self.table, index, revealed_bg, actions, revealed_fg)
                    elif _pinned_from_entry(entry, self._pack) is not None:
                        _paint_row(self.table, index, in_pack_bg, actions)
        finally:
            self._syncing_table = False

    def _row_actions(self, entry: CatalogEntry) -> QWidget:
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(4, 0, 4, 0)
        actions_layout.addWidget(repo_button(entry.repo, actions))
        label, tip = catalog_pack_button(entry, self._pack)
        button = QPushButton(label)
        button.setEnabled(entry.available and self._pack is not None)
        button.setToolTip("Select a ModPack first" if self._pack is None else tip)
        button.clicked.connect(
            lambda _=False, value=entry: self.install_requested.emit(value.primary_guid, value.repo)
        )
        actions_layout.addWidget(button)
        self._enable_row_context_menu(actions, entry)
        return actions

    def reveal_mod(self, guid: str, repo: str = "") -> bool:
        target = _entry_for_mod(self._entries, guid, repo)
        if target is None:
            return False
        self._revealed_key = source_key(target)
        self._hidden_guids.discard(target.primary_guid)
        self._hidden_guids.difference_update(guid for guid in target.guids if guid)
        self._clear_filters()
        if not self._select_revealed_row():
            return False
        QTimer.singleShot(0, self, self._select_revealed_row)
        return True

    def _clear_filters(self) -> None:
        self.hide_in_pack.blockSignals(True)
        self._filter.blockSignals(True)
        self.hide_in_pack.setChecked(False)
        self._filter.clear()
        self.hide_in_pack.blockSignals(False)
        self._filter.blockSignals(False)
        self._apply_filter()

    def _select_revealed_row(self) -> bool:
        row = self._row_for_key(self._revealed_key)
        if row < 0:
            return False
        self._syncing_table = True
        try:
            self.table.setFocus(Qt.FocusReason.OtherFocusReason)
            self.table.clearSelection()
            self.table.selectRow(row)
            self.table.setCurrentCell(row, NAME_COLUMN)
            item = self.table.item(row, NAME_COLUMN)
            if item is not None:
                self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        finally:
            self._syncing_table = False
        return True

    def _on_selection_changed(self) -> None:
        if self._syncing_table or not self._revealed_key:
            return
        row = self.table.currentRow()
        if row >= 0 and self._key_at_row(row) == self._revealed_key:
            return
        self._clear_revealed_highlight()

    def _clear_revealed_highlight(self) -> None:
        key = self._revealed_key
        if not key:
            return
        row = self._row_for_key(key)
        self._revealed_key = ""
        if row < 0:
            return
        actions = self.table.cellWidget(row, ACTIONS_COLUMN)
        _clear_row_paint(self.table, row, actions)
        entry = self._entries_by_key.get(key)
        if entry is not None and _pinned_from_entry(entry, self._pack) is not None and actions is not None:
            _paint_row(self.table, row, _in_pack_row_background(self.table), actions)

    def _row_for_key(self, key: str) -> int:
        if not key:
            return -1
        for row in range(self.table.rowCount()):
            if self._key_at_row(row) == key:
                return row
        return -1

    def _key_at_row(self, row: int) -> str:
        item = self.table.item(row, NAME_COLUMN)
        if item is None:
            return ""
        return str(item.data(_SOURCE_KEY_ROLE) or "")

    def _entry_at_row(self, row: int) -> CatalogEntry | None:
        return self._entries_by_key.get(self._key_at_row(row))

    def _enable_row_context_menu(self, widget: QWidget, entry: CatalogEntry) -> None:
        for host in (widget, *widget.findChildren(QPushButton)):
            host.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            host.customContextMenuRequested.connect(
                lambda pos, value=entry, owner=host: self._popup_menu_for_entry(value, owner.mapToGlobal(pos))
            )

    def _show_table_context_menu(self, pos) -> None:
        row = self.table.indexAt(pos).row()
        entry = self._entry_at_row(row)
        if entry is None:
            return
        self.table.selectRow(row)
        self._popup_menu_for_entry(entry, self.table.viewport().mapToGlobal(pos))

    def _popup_menu_for_entry(self, entry: CatalogEntry, global_pos) -> None:
        self._menu_for_entry(entry).exec(global_pos)

    def _menu_for_entry(self, entry: CatalogEntry) -> QMenu:
        menu = QMenu(self)
        if entry.custom:
            remove = menu.addAction("Remove from Catalog")
            remove.setToolTip("Remove this repository from your catalog")
            remove.triggered.connect(
                lambda _=False, value=entry: self.remove_custom_requested.emit(value.primary_guid, value.repo)
            )
        else:
            hide = menu.addAction("Hide Mod From Catalog")
            hide.setToolTip(
                "Hide this mod from the Catalog tab. Restore it from Download Management → Hidden Mods."
            )
            hide.triggered.connect(
                lambda _=False, value=entry.primary_guid: self.hide_requested.emit(value)
            )
        return menu

    def _on_double_click(self, row: int, _column: int) -> None:
        entry = self._entry_at_row(row)
        if entry is not None:
            self.details_requested.emit(entry.primary_guid, entry.repo)


def catalog_pack_button(entry: CatalogEntry, pack: ModPack | None) -> tuple[str, str]:
    pinned = _pinned_for_entry(entry, pack)
    if pinned is None:
        return (
            "Add to pack",
            "Download this mod and add it to the selected pack. You can choose a version.",
        )
    if not _pinned_from_source(entry, pinned):
        return (
            "Switch source",
            f"The pack has this mod from {repo_short_name(pinned.repo)}. "
            f"Choose a version from {repo_short_name(entry.repo)} to use instead.",
        )
    if entry.latest_raw and is_newer(entry.latest_raw, pinned.version):
        return (
            "Update",
            "A newer version is available. Choose a version to download and pin on the pack.",
        )
    return (
        "In pack",
        f"Already in the pack at {pinned.version_raw or pinned.version}. Click to choose a different version.",
    )


def _pinned_for_entry(entry: CatalogEntry, pack: ModPack | None) -> PinnedMod | None:
    if pack is None:
        return None
    for guid in entry.guids:
        pinned = pack.find_mod(guid)
        if pinned is not None:
            return pinned
    return None


def _pinned_from_source(entry: CatalogEntry, pinned: PinnedMod) -> bool:
    return not pinned.repo or same_repo(pinned.repo, entry.repo)


def _pinned_from_entry(entry: CatalogEntry, pack: ModPack | None) -> PinnedMod | None:
    pinned = _pinned_for_entry(entry, pack)
    if pinned is None or not _pinned_from_source(entry, pinned):
        return None
    return pinned


def _catalog_status(entry: CatalogEntry, pack: ModPack | None) -> str:
    if not entry.available:
        return "Unavailable"
    pinned = _pinned_for_entry(entry, pack)
    if pinned is None:
        return "Custom" if entry.custom else "Available"
    if not _pinned_from_source(entry, pinned):
        return "Other source in pack"
    if entry.latest_raw and is_newer(entry.latest_raw, pinned.version):
        return "Update"
    return f"Installed {pinned.version}"


def _entry_is_hidden(entry: CatalogEntry, hidden: set[str]) -> bool:
    if not hidden or entry.custom:
        return False
    keys = {guid.casefold() for guid in hidden}
    if entry.primary_guid.casefold() in keys:
        return True
    return any(guid.casefold() in keys for guid in entry.guids)


def _entry_matches_filter(entry: CatalogEntry, query: str) -> bool:
    if not query:
        return True
    return (
        query in entry.name.lower()
        or query in entry.primary_guid.lower()
        or query in entry.repo.lower()
        or any(query in guid.lower() for guid in entry.guids)
    )


def _in_pack_row_background(widget: QWidget) -> QColor:
    base = widget.palette().color(QPalette.ColorRole.Base)
    tint = base.lighter(118)
    if tint == base:
        tint = base.darker(104)
    return tint


def _entry_for_mod(entries: list[CatalogEntry], guid: str, repo: str = "") -> CatalogEntry | None:
    if guid:
        found = (find_entry(entries, guid, repo) if repo else None) or find_entry(entries, guid)
        if found is not None:
            return found
    if repo:
        for entry in entries:
            if same_repo(entry.repo, repo):
                return entry
    return None


def _clear_row_paint(table: QTableWidget, row: int, actions: QWidget | None) -> None:
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is not None:
            item.setData(Qt.ItemDataRole.BackgroundRole, None)
            item.setData(Qt.ItemDataRole.ForegroundRole, None)
    if actions is not None:
        actions.setAutoFillBackground(False)


def _paint_row(
    table: QTableWidget,
    row: int,
    color: QColor,
    actions: QWidget,
    text: QColor | None = None,
) -> None:
    brush = QBrush(color)
    text_brush = QBrush(text) if text is not None else None
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is not None:
            item.setBackground(brush)
            if text_brush is not None:
                item.setForeground(text_brush)
    actions.setAutoFillBackground(True)
    palette = actions.palette()
    palette.setColor(QPalette.ColorRole.Window, color)
    actions.setPalette(palette)
