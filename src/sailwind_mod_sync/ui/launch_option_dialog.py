from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LaunchOptionDialog(QDialog):
    """Shown before Play on Linux when Sailwind's Steam launch options won't let Doorstop load."""

    def __init__(self, suggested: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("One Steam setting for mods")
        intro = QLabel(
            "On Linux, Sailwind needs one Steam launch option so BepInEx can load your mods. "
            "In Steam, open Sailwind's Properties, and under Launch Options paste:"
        )
        intro.setWordWrap(True)

        self.option = QLineEdit(suggested)
        self.option.setReadOnly(True)
        self.option.setCursorPosition(0)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy)
        option_row = QHBoxLayout()
        option_row.addWidget(self.option, 1)
        option_row.addWidget(self.copy_button)

        note = QLabel("Without it, Sailwind still starts, but without mods.")
        note.setWordWrap(True)

        self.stop_reminding_box = QCheckBox("Don't remind me again")

        buttons = QDialogButtonBox()
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        play = buttons.addButton("Play anyway", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel.setDefault(True)
        play.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(option_row)
        layout.addWidget(note)
        layout.addWidget(self.stop_reminding_box)
        layout.addWidget(buttons)
        self.resize(560, 200)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.option.text())
        self.copy_button.setText("Copied")

    @property
    def stop_reminding(self) -> bool:
        return self.stop_reminding_box.isChecked()
