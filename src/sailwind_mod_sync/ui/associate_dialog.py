from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sailwind_mod_sync.game.scan_plugins import AUTO_MATCH_EXACT, AUTO_MATCH_MARGIN, rank_catalog_matches
from sailwind_mod_sync.models import CatalogEntry
from sailwind_mod_sync.ui.repo_dialog import RepoUrlDialog


@dataclass
class AssociateTarget:
    guid: str
    version: str
    name: str
    repo: str = ""


@dataclass
class AssociateChoice:
    guid: str
    version: str
    entry: CatalogEntry | None = None
    repo: str = ""


class AssociateCatalogDialog(QDialog):
    def __init__(
        self,
        targets: list[AssociateTarget],
        catalog: list[CatalogEntry],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Associate with GitHub")
        self._targets = list(targets)
        self._catalog = list(catalog)
        self._index = 0
        self._choices: dict[str, AssociateChoice] = {}
        self._rows: list[CatalogEntry] = []

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter catalog…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._refresh_catalog)
        self.catalog_list = QListWidget()
        self.catalog_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.catalog_list.itemDoubleClicked.connect(lambda _item: self._associate())
        self.catalog_list.currentRowChanged.connect(lambda _row: self._update_associate_enabled())

        self.skip_btn = QPushButton("Skip")
        self.skip_btn.clicked.connect(self._skip)
        self.url_btn = QPushButton("Paste URL…")
        self.url_btn.clicked.connect(self._paste_url)
        buttons = QDialogButtonBox()
        self.associate_btn = buttons.addButton("Associate", QDialogButtonBox.ButtonRole.ActionRole)
        self.done_btn = buttons.addButton("Done", QDialogButtonBox.ButtonRole.ActionRole)
        # Enter skips: linking the wrong entry renames the mod, so it takes a deliberate pick.
        self.associate_btn.setAutoDefault(False)
        self.done_btn.setAutoDefault(False)
        self.url_btn.setAutoDefault(False)
        self.skip_btn.setDefault(True)
        self.associate_btn.clicked.connect(self._associate)
        self.done_btn.clicked.connect(self.accept)

        extras = QHBoxLayout()
        extras.addWidget(self.skip_btn)
        extras.addWidget(self.url_btn)
        extras.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.filter)
        layout.addWidget(self.catalog_list, 1)
        layout.addLayout(extras)
        layout.addWidget(buttons)
        self.resize(560, 460)
        self._show_current()

    def choices(self) -> list[AssociateChoice]:
        return list(self._choices.values())

    def _current(self) -> AssociateTarget | None:
        if 0 <= self._index < len(self._targets):
            return self._targets[self._index]
        return None

    def _show_current(self) -> None:
        target = self._current()
        if target is None:
            self.accept()
            return
        remaining = len(self._targets) - self._index
        prefix = f"{self._index + 1} of {len(self._targets)}. " if len(self._targets) > 1 else ""
        self.summary.setText(
            f"{prefix}Link {target.name} ({target.guid} {target.version}) to a GitHub catalog "
            "entry so this pack can check for updates."
        )
        self.filter.setText("")
        self._refresh_catalog()
        self.done_btn.setText("Skip remaining" if remaining > 1 else "Cancel")

    def _refresh_catalog(self) -> None:
        target = self._current()
        self.catalog_list.clear()
        self._rows = []
        if target is None:
            return
        query = self.filter.text().strip().lower()
        ranked = rank_catalog_matches(target.name, target.guid, self._catalog)
        confident = bool(ranked) and ranked[0][0] >= AUTO_MATCH_EXACT and (
            len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= AUTO_MATCH_MARGIN
        )
        seen: set[str] = set()
        ordered: list[CatalogEntry] = []
        for _score, entry in ranked:
            ordered.append(entry)
            seen.add(entry.repo)
        for entry in self._catalog:
            if entry.repo not in seen:
                ordered.append(entry)
        for entry in ordered:
            hay = " ".join([entry.name, entry.primary_guid, entry.repo, *entry.guids]).lower()
            if query and query not in hay:
                continue
            latest = entry.latest_raw or entry.latest_version or "—"
            item = QListWidgetItem(f"{entry.name}  ·  {entry.primary_guid}  ·  {latest}")
            item.setData(Qt.ItemDataRole.UserRole, entry.repo)
            item.setToolTip(entry.repo)
            self.catalog_list.addItem(item)
            self._rows.append(entry)
        # Preselect only a clear match, and only while the list is unfiltered and starts with it.
        if confident and not query and self._rows and self._rows[0] is ranked[0][1]:
            self.catalog_list.setCurrentRow(0)
        else:
            self.catalog_list.setCurrentRow(-1)
        self._update_associate_enabled()

    def _update_associate_enabled(self) -> None:
        self.associate_btn.setEnabled(self._selected_entry() is not None)

    def _selected_entry(self) -> CatalogEntry | None:
        row = self.catalog_list.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def _associate(self) -> None:
        target = self._current()
        entry = self._selected_entry()
        if target is None or entry is None:
            return
        self._choices[target.guid] = AssociateChoice(
            guid=target.guid,
            version=target.version,
            entry=entry,
            repo=entry.repo,
        )
        self._advance()

    def _skip(self) -> None:
        self._advance()

    def _paste_url(self) -> None:
        target = self._current()
        if target is None:
            return
        dialog = RepoUrlDialog(
            target.guid,
            self,
            target.repo,
            title="Paste repository URL",
            hint=f"Paste the GitHub or GitLab URL for {target.name}.",
        )
        if not dialog.exec():
            return
        self._choices[target.guid] = AssociateChoice(
            guid=target.guid,
            version=target.version,
            repo=dialog.repo_url(),
        )
        self._advance()

    def _advance(self) -> None:
        self._index += 1
        if self._index >= len(self._targets):
            self.accept()
            return
        self._show_current()
