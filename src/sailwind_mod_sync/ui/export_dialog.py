from __future__ import annotations

from enum import Enum

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class ExportKind(Enum):
    RECIPE = "recipe"
    BUNDLE = "bundle"
    FULL = "full"

    @property
    def suffix(self) -> str:
        return ".json" if self is ExportKind.RECIPE else ".zip"


_CHOICES = [
    (
        ExportKind.FULL,
        "Full offline pack (recommended for sharing)",
        "BepInEx, every mod, and your mod settings in one zip. "
        "Friends can import it without downloading anything from GitHub or Thunderstore.",
    ),
    (
        ExportKind.BUNDLE,
        "Mods only",
        "A zip with the mod files you already have downloaded. "
        "BepInEx and any missing mods are downloaded on import.",
    ),
    (
        ExportKind.RECIPE,
        "Recipe only",
        "A small .json list of mods and versions. Everything is downloaded on import.",
    ),
]


class ExportDialog(QDialog):
    """Asks what kind of ModPack export to make before the save-file picker opens."""

    def __init__(self, pack_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export ModPack")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"How do you want to export <b>{pack_name}</b>?"))
        self._group = QButtonGroup(self)
        self._kinds: dict[int, ExportKind] = {}
        for index, (kind, title, detail) in enumerate(_CHOICES):
            radio = QRadioButton(title)
            radio.setChecked(index == 0)
            self._group.addButton(radio, index)
            self._kinds[index] = kind
            hint = QLabel(detail)
            hint.setWordWrap(True)
            hint.setContentsMargins(24, 0, 0, 8)
            hint.setStyleSheet("color: palette(placeholder-text);")
            layout.addWidget(radio)
            layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Choose file…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)

    def kind(self) -> ExportKind:
        return self._kinds[self._group.checkedId()]
