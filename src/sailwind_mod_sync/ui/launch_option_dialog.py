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

        # Opt-in alternative for people who'd rather not touch launch options.
        self.chose_prefix = False
        self.prefix_button = QPushButton("Set it in Proton instead…")
        self.prefix_button.setToolTip("Change Sailwind's own Proton setting instead of its launch options")
        self.prefix_button.setAutoDefault(False)
        self.prefix_button.clicked.connect(self._choose_prefix)

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
        layout.addWidget(self.prefix_button)
        layout.addWidget(buttons)
        self.resize(560, 200)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.option.text())
        self.copy_button.setText("Copied")

    def _choose_prefix(self) -> None:
        self.chose_prefix = True
        self.accept()

    @property
    def stop_reminding(self) -> bool:
        return self.stop_reminding_box.isChecked()


class ProtonPrefixConsentDialog(QDialog):
    """Explains exactly what changing Sailwind's Proton prefix does before the user agrees to it."""

    def __init__(self, registry: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change Sailwind's Proton setting?")
        text = QLabel(
            "<p>Instead of a launch option, Sailwind Mod Synchronizer can turn on the same setting "
            "inside Sailwind's own Proton files.</p>"
            "<p><b>What changes:</b> one Wine setting, <code>winhttp = native,builtin</code>, in "
            f"<code>{registry}</code>. The original file is backed up next to it as "
            "<code>user.reg.sms-backup</code>.</p>"
            "<p><b>What it does:</b> Wine loads Doorstop's <code>winhttp.dll</code> from the game folder first, "
            "so BepInEx can load your mods. Only Sailwind is affected.</p>"
            "<p><b>Keep in mind:</b></p><ul>"
            "<li>This edits files that Steam and Proton normally manage.</li>"
            "<li>Sailwind has to be closed while it's changed.</li>"
            "<li>If Sailwind's Proton files are reset or deleted, the setting is lost and you'll be asked again.</li>"
            "<li>Launch Vanilla still starts the plain game: Doorstop stays switched off.</li>"
            "</ul>"
            "<p>You can undo it any time in <b>Settings</b> with <b>Remove Proton setting</b>.</p>"
        )
        text.setWordWrap(True)

        self.understand = QCheckBox("I understand this changes Sailwind's Proton files")
        buttons = QDialogButtonBox()
        cancel = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.change_button = buttons.addButton("Change setting", QDialogButtonBox.ButtonRole.AcceptRole)
        self.change_button.setEnabled(False)
        cancel.setDefault(True)
        self.understand.toggled.connect(self.change_button.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(self.understand)
        layout.addWidget(buttons)
        self.resize(560, 360)
