from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from sailwind_mod_sync.models import PinnedMod


class MissingModsWarningDialog(QDialog):
    def __init__(
        self,
        missing: list[PinnedMod],
        pack_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Missing mods")
        count = len(missing)
        noun = "mod" if count == 1 else "mods"
        where = f" in {pack_name}" if pack_name else ""
        intro = QLabel(
            f"This pack{where} has {count} missing {noun} with no repository to download them from. "
            "Those plugins will not load. Import a file or Add Repository on the Pack tab, "
            "or continue without them. Other missing mods download when the pack starts."
        )
        intro.setWordWrap(True)

        names = "\n".join(
            f"• {mod.guid} {mod.version_raw or mod.version}".strip() for mod in missing
        )
        listing = QLabel(names)
        listing.setWordWrap(True)
        listing.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.remind_radio = QRadioButton("Remind me next time")
        self.stop_radio = QRadioButton("Stop reminding")
        self.remind_radio.setChecked(True)

        buttons = QDialogButtonBox()
        stop_btn = buttons.addButton("Stop", QDialogButtonBox.ButtonRole.RejectRole)
        continue_btn = buttons.addButton("Continue", QDialogButtonBox.ButtonRole.AcceptRole)
        continue_btn.setDefault(True)
        stop_btn.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(listing)
        layout.addWidget(self.remind_radio)
        layout.addWidget(self.stop_radio)
        layout.addWidget(buttons)
        self.resize(480, 240)

    @property
    def stop_reminding(self) -> bool:
        return self.stop_radio.isChecked()
