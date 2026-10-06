from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from sailwind_mod_sync.models import LibraryEntry, ModPack, display_mod_name
from sailwind_mod_sync.ui.links import repo_or_find_button
from sailwind_mod_sync.ui.tables import (
    enable_column_resize,
    enable_column_sort,
    sortable_item,
    sorting_paused,
    version_sort_key,
)


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class LibraryView(QWidget):
    add_to_pack_requested = Signal(str, str)
    find_repo_requested = Signal(str, str)
    details_requested = Signal(str, str)
    delete_requested = Signal(str, str)
    rename_requested = Signal(str)
    prune_requested = Signal()
    import_clicked = Signal()
    find_in_catalog_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "GUID", "Version", "Size", "Source", ""])
        enable_column_resize(self.table, [180, 200, 90, 80, 180, 420])
        enable_column_sort(self.table, default_column=0)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self._entries: list[LibraryEntry] = []

        import_btn = QPushButton("Import file…")
        import_btn.setToolTip("Add a .dll or .zip you downloaded (for example from Discord)")
        import_btn.clicked.connect(self.import_clicked.emit)
        prune = QPushButton("Prune unused")
        prune.clicked.connect(self.prune_requested.emit)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(import_btn)
        top.addStretch()
        top.addWidget(prune)
        layout.addLayout(top)
        layout.addWidget(self.table)

    def set_entries(
        self,
        entries: list[LibraryEntry],
        pack: ModPack | None = None,
        names: dict[str, str] | None = None,
        pinned_keys: dict[str, str] | None = None,
    ) -> None:
        """Show ``entries``; ``pinned_keys`` maps each GUID pinned on ``pack`` to the library key it uses."""
        labels = names or {}
        keys = pinned_keys or {}
        self._entries = list(entries)
        pack_name = pack.name if pack else ""
        with sorting_paused(self.table):
            self.table.setRowCount(len(self._entries))
            for index, entry in enumerate(self._entries):
                name = labels.get(entry.guid) or _fallback_name(entry)
                name_item = sortable_item(
                    name,
                    (name.casefold(), entry.guid.casefold(), tuple(-part for part in version_sort_key(entry.version))),
                )
                name_item.setToolTip(entry.guid)
                self.table.setItem(index, 0, name_item)
                self.table.setItem(index, 1, sortable_item(entry.guid))
                self.table.setItem(index, 2, sortable_item(entry.version, version_sort_key(entry.version)))
                self.table.setItem(index, 3, sortable_item(format_size(entry.size_bytes), entry.size_bytes))
                self.table.setItem(
                    index,
                    4,
                    sortable_item(entry.meta.source_url or entry.meta.repo),
                )
                self.table.setItem(index, 5, sortable_item(""))
                guid, version = entry.guid, entry.version
                actions = QWidget()
                actions_layout = QHBoxLayout(actions)
                actions_layout.setContentsMargins(4, 0, 4, 0)
                actions_layout.addWidget(
                    repo_or_find_button(
                        entry.meta.repo,
                        actions,
                        lambda g=guid, v=version: self.find_repo_requested.emit(g, v),
                    )
                )
                add_btn = QPushButton("Add to pack")
                pinned = pack.find_mod(guid) if pack else None
                if pack is None:
                    add_btn.setEnabled(False)
                    add_btn.setToolTip("Select a ModPack first")
                elif pinned and keys.get(guid, pinned.version) == version:
                    add_btn.setEnabled(False)
                    add_btn.setText("In pack")
                    add_btn.setToolTip(f"Already in {pack_name}")
                else:
                    if pinned:
                        add_btn.setToolTip(
                            f"Replace {pinned.version} with {version} on {pack_name}"
                        )
                    else:
                        add_btn.setToolTip(f"Add this version to {pack_name}")
                    add_btn.clicked.connect(
                        lambda _=False, g=guid, v=version: self.add_to_pack_requested.emit(g, v)
                    )
                actions_layout.addWidget(add_btn)
                rename = QPushButton("Rename")
                rename.setToolTip("Set a display name for this mod")
                rename.clicked.connect(lambda _=False, g=guid: self.rename_requested.emit(g))
                actions_layout.addWidget(rename)
                delete = QPushButton("Delete")
                delete.clicked.connect(lambda _=False, g=guid, v=version: self.delete_requested.emit(g, v))
                actions_layout.addWidget(delete)
                self.table.setCellWidget(index, 5, actions)

    def _on_double_click(self, row: int, _column: int) -> None:
        guid_item = self.table.item(row, 1)
        version_item = self.table.item(row, 2)
        if guid_item is None or version_item is None:
            return
        self.details_requested.emit(guid_item.text(), version_item.text())

    def _show_context_menu(self, pos) -> None:
        row = self.table.indexAt(pos).row()
        menu = self._context_menu_for_row(row)
        if menu is None:
            return
        self.table.selectRow(row)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _context_menu_for_row(self, row: int) -> QMenu | None:
        guid_item = self.table.item(row, 1)
        if guid_item is None:
            return None
        guid = guid_item.text()
        menu = QMenu(self)
        find_action = QAction("Find in Catalog", menu)
        find_action.triggered.connect(lambda: self.find_in_catalog_requested.emit(guid))
        menu.addAction(find_action)
        return menu


def _fallback_name(entry: LibraryEntry) -> str:
    return display_mod_name(
        entry.guid,
        plugin_folders=list(entry.meta.plugin_folders),
        repo=entry.meta.repo,
    )
