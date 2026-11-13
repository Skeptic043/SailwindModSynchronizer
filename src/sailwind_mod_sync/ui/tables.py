from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem

from sailwind_mod_sync.models import version_key


class SortableItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:  # type: ignore[override]
        if not isinstance(other, QTableWidgetItem):
            return super().__lt__(other)
        left = self.data(Qt.ItemDataRole.UserRole)
        right = other.data(Qt.ItemDataRole.UserRole)
        if left is not None and right is not None:
            try:
                return left < right
            except TypeError:
                pass
        return self.text().casefold() < other.text().casefold()


def sortable_item(text: str, key: object | None = None) -> SortableItem:
    item = SortableItem(text)
    item.setData(Qt.ItemDataRole.UserRole, text.casefold() if key is None else key)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


def version_sort_key(raw: str | None) -> tuple:
    return version_key(raw)


def latest_text(latest: str, prerelease: str, update: str = "") -> str:
    """Text of a Latest cell: the release, then any newer prerelease, marking the one that ``update`` names."""
    parts = []
    if latest:
        parts.append(f"{latest} (update)" if update == latest else latest)
    if prerelease:
        parts.append(f"{prerelease} (prerelease update)" if update == prerelease else f"{prerelease} (prerelease)")
    return " · ".join(parts)


def enable_column_resize(table: QTableWidget, widths: list[int] | None = None) -> None:
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(36)
    if widths:
        for index, width in enumerate(widths):
            if index < table.columnCount():
                table.setColumnWidth(index, width)


def enable_column_sort(table: QTableWidget, *, default_column: int | None = 0) -> None:
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)
    if default_column is None:
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    else:
        header.setSortIndicator(default_column, Qt.SortOrder.AscendingOrder)
    table.setSortingEnabled(False)


@contextmanager
def sorting_paused(table: QTableWidget) -> Iterator[None]:
    header = table.horizontalHeader()
    column = header.sortIndicatorSection()
    order = header.sortIndicatorOrder()
    table.setSortingEnabled(False)
    try:
        yield
    finally:
        table.setSortingEnabled(True)
        if column >= 0:
            table.sortItems(column, order)
