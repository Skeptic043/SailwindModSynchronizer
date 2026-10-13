from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sailwind_mod_sync.packs.plugin_files import FileConflict

KEEP_MINE = 0
USE_NEW = 1


class FileConflictDialog(QDialog):
    """Ask which version to keep of each file both the player and the new mod version changed."""

    def __init__(
        self,
        conflicts: list[FileConflict],
        names: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Changed mod files")
        count = len(conflicts)
        noun = "file was" if count == 1 else "files were"
        intro = QLabel(
            f"{count} {noun} changed in the pack's plugin folder, and the new mod version changes "
            "them too. Choose which version to keep. Closing this window keeps your versions."
        )
        intro.setWordWrap(True)

        self._choices: list[tuple[FileConflict, QComboBox]] = []
        body = QWidget()
        rows = QVBoxLayout(body)
        by_mod: dict[str, list[FileConflict]] = {}
        for conflict in conflicts:
            by_mod.setdefault(conflict.guid, []).append(conflict)
        for guid, items in sorted(by_mod.items(), key=lambda pair: names.get(pair[0], pair[0]).lower()):
            group = QGroupBox(names.get(guid, guid))
            group_layout = QVBoxLayout(group)
            for conflict in items:
                label = QLabel(_describe(conflict))
                label.setWordWrap(True)
                choice = QComboBox()
                keep = "Keep my version (new entries still added)" if conflict.merged else "Keep my version"
                choice.addItem(keep)
                choice.addItem("Use the mod's new version")
                choice.setCurrentIndex(KEEP_MINE)
                row = QHBoxLayout()
                row.addWidget(label, 1)
                row.addWidget(choice)
                group_layout.addLayout(row)
                self._choices.append((conflict, choice))
            rows.addWidget(group)
        rows.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)

        keep_all = QPushButton("Keep all mine")
        keep_all.setAutoDefault(False)
        keep_all.clicked.connect(lambda: self._set_all(KEEP_MINE))
        use_all = QPushButton("Use all new")
        use_all.setAutoDefault(False)
        use_all.clicked.connect(lambda: self._set_all(USE_NEW))
        buttons = QDialogButtonBox()
        continue_btn = buttons.addButton("Continue", QDialogButtonBox.ButtonRole.AcceptRole)
        continue_btn.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(keep_all)
        actions.addWidget(use_all)
        actions.addStretch()
        actions.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(scroll, 1)
        layout.addLayout(actions)
        self.resize(640, 420)

    def _set_all(self, index: int) -> None:
        for _, choice in self._choices:
            choice.setCurrentIndex(index)

    def use_new(self) -> set[FileConflict]:
        """Return the files to replace with the mod's new version."""
        return {conflict for conflict, choice in self._choices if choice.currentIndex() == USE_NEW}


def _describe(conflict: FileConflict) -> str:
    if not conflict.entries:
        return conflict.relpath
    shown = ", ".join(conflict.entries[:3])
    more = f" and {len(conflict.entries) - 3} more" if len(conflict.entries) > 3 else ""
    return f"{conflict.relpath}\nEntries changed on both sides: {shown}{more}"


def ask_file_conflicts(
    conflicts: list[FileConflict], names: dict[str, str], parent: QWidget | None = None,
) -> set[FileConflict]:
    """Show the dialog; cancelling or closing keeps the player's versions."""
    dialog = FileConflictDialog(conflicts, names, parent)
    if not dialog.exec():
        return set()
    return dialog.use_new()
