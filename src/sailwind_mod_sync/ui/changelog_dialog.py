from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

from sailwind_mod_sync.constants import APP_REPO
from sailwind_mod_sync.resources import changelog_path


def load_changelog() -> str:
    path = changelog_path()
    if path is None:
        return f"# Change Log\n\nThe change log is missing from this install. See the release notes at {APP_REPO}/releases."
    return path.read_text(encoding="utf-8")


class ChangelogDialog(QDialog):
    """Scrollable, read-only view of the bundled CHANGELOG.md."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change log")
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setMarkdown(load_changelog())
        layout.addWidget(self.browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(640, 560)
