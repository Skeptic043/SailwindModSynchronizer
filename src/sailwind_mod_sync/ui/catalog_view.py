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

from sailwind_mod_sync.catalog.custom import same_repo
from sailwind_mod_sync.models import CatalogEntry, ModPack, PinnedMod, is_newer
from sailwind_mod_sync.ui.links import repo_button
from sailwind_mod_sync.ui.tables import (
    enable_column_resize,
    enable_column_sort,
    sortable_item,
    sorting_paused,
    version_sort_key,
)


class CatalogView(QWidget):
    install_requested = Signal(str, str)
    remove_custom_requested = Signal(str, str)
    hide_requested = Signal(str)
    details_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[CatalogEntry] = []
        self._pack: ModPack | None = None
        self._hidden_guids: set[str] = set()
        self._revealed_guid = ""
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
        add_repo.setToolTip("Add a GitHub or GitLab repository that is not in ModVersionChecker")
        self.add_repo_clicked = add_repo.clicked

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "GUID", "Latest", "Status", ""])
        enable_column_resize(self.table, [180, 240, 90, 120, 260])
        enable_column_sort(self.table, default_column=0)
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
            and not (hide_in_pack and _pinned_for_entry(entry, self._pack) is not None)
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
                    status = _catalog_status(entry, self._pack)
                    self.table.setItem(index, 0, sortable_item(entry.name))
                    guid_item = sortable_item(entry.guid_label, entry.primary_guid.casefold())
                    guid_item.setToolTip("\n".join(entry.guids))
                    self.table.setItem(index, 1, guid_item)
                    latest = entry.latest_raw or ("none" if not entry.available else "")
                    latest_key = (0 if entry.available else 1, version_sort_key(entry.latest_raw))
                    self.table.setItem(index, 2, sortable_item(latest, latest_key))
                    self.table.setItem(index, 3, sortable_item(status))
                    self.table.setItem(index, 4, sortable_item(""))
                    actions = QWidget()
                    actions_layout = QHBoxLayout(actions)
                    actions_layout.setContentsMargins(4, 0, 4, 0)
                    actions_layout.addWidget(repo_button(entry.repo, actions))
                    label, tip = catalog_pack_button(entry, self._pack)
                    button = QPushButton(label)
                    button.setEnabled(entry.available and self._pack is not None)
                    if self._pack is None:
                        button.setToolTip("Select a ModPack first")
                    else:
                        button.setToolTip(tip)
                    guid = entry.primary_guid
                    button.clicked.connect(
                        lambda _=False, value=guid, repo=entry.repo: self.install_requested.emit(value, repo)
                    )
                    actions_layout.addWidget(button)
                    self.table.setCellWidget(index, 4, actions)
                    self._enable_row_context_menu(actions, guid)
                    if entry.primary_guid == self._revealed_guid or self._revealed_guid in entry.guids:
                        _paint_row(
                            self.table,
                            index,
                            revealed_bg,
                            actions,
                            revealed_fg,
                        )
                    elif _pinned_for_entry(entry, self._pack) is not None:
                        _paint_row(self.table, index, in_pack_bg, actions)
        finally:
            self._syncing_table = False

    def reveal_mod(self, guid: str, repo: str = "") -> bool:
        target = _entry_for_mod(self._entries, guid, repo)
        if target is None:
            return False
        self._revealed_guid = target.primary_guid
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
        row = self._row_for_guid(self._revealed_guid)
        if row < 0:
            return False
        self._syncing_table = True
        try:
            self.table.setFocus(Qt.FocusReason.OtherFocusReason)
            self.table.clearSelection()
            self.table.selectRow(row)
            self.table.setCurrentCell(row, 0)
            item = self.table.item(row, 0) or self.table.item(row, 1)
            if item is not None:
                self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        finally:
            self._syncing_table = False
        return True

    def _on_selection_changed(self) -> None:
        if self._syncing_table or not self._revealed_guid:
            return
        row = self.table.currentRow()
        if row >= 0 and (
            self._guid_at_row(row) == self._revealed_guid
            or self._row_for_guid(self._revealed_guid) == row
        ):
            return
        self._clear_revealed_highlight()

    def _clear_revealed_highlight(self) -> None:
        guid = self._revealed_guid
        if not guid:
            return
        row = self._row_for_guid(guid)
        self._revealed_guid = ""
        if row < 0:
            return
        actions = self.table.cellWidget(row, 4)
        _clear_row_paint(self.table, row, actions)
        entry = _entry_for_mod(self._entries, guid)
        if entry is not None and _pinned_for_entry(entry, self._pack) is not None and actions is not None:
            _paint_row(self.table, row, _in_pack_row_background(self.table), actions)

    def _row_for_guid(self, guid: str) -> int:
        if not guid:
            return -1
        key = guid.casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is None:
                continue
            stored = str(item.data(Qt.ItemDataRole.UserRole) or "")
            tooltip = item.toolTip().splitlines()
            if stored == key or guid in tooltip or key in {line.casefold() for line in tooltip}:
                return row
        return -1

    def _enable_row_context_menu(self, widget: QWidget, guid: str) -> None:
        for host in (widget, *widget.findChildren(QPushButton)):
            host.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            host.customContextMenuRequested.connect(
                lambda pos, value=guid, owner=host: self._popup_menu_for_guid(value, owner.mapToGlobal(pos))
            )

    def _show_table_context_menu(self, pos) -> None:
        row = self.table.indexAt(pos).row()
        guid = self._guid_at_row(row)
        if not guid:
            return
        self.table.selectRow(row)
        self._popup_menu_for_guid(guid, self.table.viewport().mapToGlobal(pos))

    def _popup_menu_for_guid(self, guid: str, global_pos) -> None:
        menu = self._menu_for_guid(guid)
        if menu is None:
            return
        menu.exec(global_pos)

    def _menu_for_guid(self, guid: str) -> QMenu | None:
        entry = _entry_for_mod(self._entries, guid)
        if entry is None:
            return None
        menu = QMenu(self)
        if entry.custom:
            remove = menu.addAction("Remove from Catalog")
            remove.setToolTip("Remove this repository from your catalog")
            remove.triggered.connect(
                lambda _=False, value=entry.primary_guid, repo=entry.repo: self.remove_custom_requested.emit(value, repo)
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
        entry = _entry_for_mod(self._entries, self._guid_at_row(row))
        if entry is not None:
            self.details_requested.emit(entry.primary_guid, entry.repo)

    def _guid_at_row(self, row: int) -> str:
        item = self.table.item(row, 1)
        if item is None:
            return ""
        key = str(item.data(Qt.ItemDataRole.UserRole) or "")
        for entry in self._entries:
            if entry.primary_guid.casefold() == key:
                return entry.primary_guid
            if entry.primary_guid in item.toolTip().splitlines():
                return entry.primary_guid
        return ""


def catalog_pack_button(entry: CatalogEntry, pack: ModPack | None) -> tuple[str, str]:
    pinned = _pinned_for_entry(entry, pack)
    if pinned is None:
        return (
            "Add to pack",
            "Download this mod and add it to the selected pack. You can choose a version.",
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


def _catalog_status(entry: CatalogEntry, pack: ModPack | None) -> str:
    if not entry.available:
        return "Unavailable"
    if pack is None:
        return "Custom" if entry.custom else "Available"
    pinned = _pinned_for_entry(entry, pack)
    if pinned is None:
        return "Custom" if entry.custom else "Available"
    if entry.latest_raw and is_newer(entry.latest_raw, pinned.version):
        return "Update"
    return f"Installed {pinned.version}"


def _entry_is_hidden(entry: CatalogEntry, hidden: set[str]) -> bool:
    if not hidden:
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
    for entry in entries:
        if guid and (guid == entry.primary_guid or guid in entry.guids):
            return entry
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
