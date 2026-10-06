from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class HiddenModsDialog(QDialog):
    unhide_requested = Signal(str)

    def __init__(
        self,
        hidden: list[tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hidden Mods")
        intro = QLabel(
            "These catalog mods are hidden from the Catalog tab. "
            "Unhide a mod to show it again."
        )
        intro.setWordWrap(True)

        self.mods = QListWidget()
        self.mods.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.mods.itemSelectionChanged.connect(self._sync_unhide)
        self.mods.itemDoubleClicked.connect(self._unhide_item)

        self.unhide = QPushButton("Unhide")
        self.unhide.setEnabled(False)
        self.unhide.clicked.connect(self._unhide_selected)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        actions = QHBoxLayout()
        actions.addWidget(self.unhide)
        actions.addStretch()
        actions.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.mods, 1)
        layout.addLayout(actions)
        self.resize(480, 360)
        self.set_hidden(hidden)

    def set_hidden(self, hidden: list[tuple[str, str]]) -> None:
        """Show ``(key, label)`` rows; Unhide emits the key of each chosen row."""
        self.mods.clear()
        for key, label in hidden:
            item = QListWidgetItem(label or key)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.mods.addItem(item)
        self._sync_unhide()

    def _sync_unhide(self) -> None:
        self.unhide.setEnabled(bool(self.mods.selectedItems()))

    def _unhide_selected(self) -> None:
        for item in list(self.mods.selectedItems()):
            self._unhide_item(item)

    def _unhide_item(self, item: QListWidgetItem) -> None:
        guid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        row = self.mods.row(item)
        if row >= 0:
            self.mods.takeItem(row)
        if guid:
            self.unhide_requested.emit(guid)
        self._sync_unhide()
