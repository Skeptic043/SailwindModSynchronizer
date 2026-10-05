from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sailwind_mod_sync.http_util import ProgressFn
from sailwind_mod_sync.models import PinnedMod, parse_mod_version, version_key
from sailwind_mod_sync.ui.workers import TaskBridge, run_background

BROWSE_VERSIONS = "__browse_versions__"


@dataclass(frozen=True)
class VersionRow:
    version: str
    version_raw: str
    in_library: bool
    current: bool = False

    @property
    def label(self) -> str:
        extras = ["downloaded"] if self.in_library else ["download"]
        if self.current:
            extras.append("current")
        return f"{self.version_raw or self.version}  ({', '.join(extras)})"


def _version_id(version: str) -> str:
    return parse_mod_version(version) or version.strip()


def pack_version_items(
    pinned: PinnedMod,
    library_versions: list[tuple[str, str]] | None,
    *,
    catalog_latest_raw: str = "",
    catalog_latest_version: str | None = None,
    has_repo: bool = False,
    missing: bool = False,
) -> list[tuple[str, object]]:
    items: list[tuple[str, tuple[str, str]]] = []
    seen: set[str] = set()

    def add(version: str, raw: str, suffix: str = "") -> None:
        key = _version_id(version)
        if not key or key in seen:
            return
        seen.add(key)
        label = raw or version
        if suffix:
            label = f"{label} {suffix}"
        items.append((label, (version, raw or version)))

    for version, raw in library_versions or ():
        add(version, raw)
    add(
        pinned.version,
        pinned.version_raw or pinned.version,
        "(not downloaded)" if missing else "",
    )
    latest_version = catalog_latest_version or parse_mod_version(catalog_latest_raw)
    if latest_version:
        add(latest_version, catalog_latest_raw or latest_version, "(download)")
    items.sort(key=lambda item: version_key(item[1][0]), reverse=True)
    result: list[tuple[str, object]] = list(items)
    if has_repo:
        result.append(("More versions…", BROWSE_VERSIONS))
    return result


def merge_version_rows(
    library_versions: list[tuple[str, str]] | None,
    remote_versions: list[tuple[str, str]] | None,
    current_version: str,
) -> list[VersionRow]:
    rows: dict[str, VersionRow] = {}
    current_id = _version_id(current_version)
    for version, raw in library_versions or ():
        key = _version_id(version)
        if not key:
            continue
        rows[key] = VersionRow(version, raw or version, in_library=True, current=key == current_id)
    for version, raw in remote_versions or ():
        key = _version_id(version)
        if not key or key in rows:
            continue
        rows[key] = VersionRow(version, raw or version, in_library=False, current=key == current_id)
    if current_id and current_id not in rows:
        rows[current_id] = VersionRow(
            current_version,
            current_version,
            in_library=False,
            current=True,
        )
    return sorted(rows.values(), key=lambda row: version_key(row.version), reverse=True)


class SelectVersionDialog(QDialog):
    def __init__(
        self,
        *,
        guid: str,
        name: str,
        current_version: str,
        library_versions: list[tuple[str, str]] | None = None,
        repo: str = "",
        parent: QWidget | None = None,
        adding: bool = False,
    ) -> None:
        super().__init__(parent)
        self._closed = False
        self._bridge: TaskBridge | None = None
        self._library = list(library_versions or [])
        self._remote: list[tuple[str, str]] = []
        self._current_version = current_version
        self._selected: tuple[str, str] | None = None
        title = name or guid
        self.setWindowTitle(f"Select version — {title}")
        self.resize(460, 420)

        if adding:
            hint = QLabel(
                f"Choose the version of {title} to add to the pack. "
                "It downloads automatically if it is not already cached."
            )
        else:
            hint = QLabel(
                f"Choose the version of {title} this pack should use. "
                "Cached versions apply immediately; others are downloaded."
            )
        hint.setWordWrap(True)

        self.status = QLabel("" if repo else "No repository URL. Use Add Repository to download other releases.")
        self.status.setWordWrap(True)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemDoubleClicked.connect(self.accept)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.status)
        layout.addWidget(buttons)
        self._rebuild()

    def start_remote(self, fetch: Callable[[ProgressFn], object]) -> None:
        self.status.setText("Loading releases…")
        bridge = TaskBridge(self)
        self._bridge = bridge
        queued = Qt.ConnectionType.QueuedConnection
        bridge.progress.connect(self._on_progress, queued)
        bridge.finished.connect(self._on_remote, queued)
        bridge.failed.connect(self._on_remote_fail, queued)
        run_background(fetch, bridge)

    def selected(self) -> tuple[str, str] | None:
        return self._selected

    def accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return
        self._selected = (str(data[0]), str(data[1]))
        super().accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closed = True
        super().closeEvent(event)

    def _rebuild(self) -> None:
        current_id = _version_id(self._current_version)
        selected_id = ""
        item = self.list.currentItem()
        if item is not None:
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and data:
                selected_id = _version_id(str(data[0]))
        rows = merge_version_rows(self._library, self._remote, self._current_version)
        self.list.clear()
        current_item: QListWidgetItem | None = None
        selected_item: QListWidgetItem | None = None
        for row in rows:
            item = QListWidgetItem(row.label)
            item.setData(Qt.ItemDataRole.UserRole, (row.version, row.version_raw))
            self.list.addItem(item)
            key = _version_id(row.version)
            if key == current_id:
                current_item = item
            if selected_id and key == selected_id:
                selected_item = item
        self.list.setCurrentItem(selected_item or current_item or self.list.item(0))

    @Slot(str)
    def _on_progress(self, message: str) -> None:
        if self._closed:
            return
        self.status.setText(message)

    @Slot(object)
    def _on_remote(self, result: object) -> None:
        if self._closed:
            return
        rows: list[tuple[str, str]] = []
        if isinstance(result, list):
            for item in result:
                if isinstance(item, tuple) and len(item) == 2:
                    rows.append((str(item[0]), str(item[1])))
        self._remote = rows
        self.status.setText("" if rows else "No releases found")
        self._rebuild()

    @Slot(str)
    def _on_remote_fail(self, message: str) -> None:
        if self._closed:
            return
        self.status.setText(message)
