from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sailwind_mod_sync.config import AppConfig
from sailwind_mod_sync.constants import GITHUB_NEW_TOKEN_URL
from sailwind_mod_sync.paths import AppPaths


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, paths: AppPaths, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._config = config

        self.game_path = QLineEdit(config.game_path)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_game)
        game_row = QWidget()
        game_layout = QHBoxLayout(game_row)
        game_layout.setContentsMargins(0, 0, 0, 0)
        game_layout.addWidget(self.game_path)
        game_layout.addWidget(browse)

        self.token = QLineEdit(config.github_token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_token = QCheckBox("Show")
        self.show_token.toggled.connect(self._toggle_token)
        self.create_token = QPushButton("Create…")
        self.create_token.setToolTip(
            "Open GitHub to create a personal access token. No scopes are needed for public repositories."
        )
        self.create_token.clicked.connect(self._open_github_token_page)
        token_row = QWidget()
        token_layout = QHBoxLayout(token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.addWidget(self.token)
        token_layout.addWidget(self.show_token)
        token_layout.addWidget(self.create_token)

        data_label = QLabel(str(paths.root))
        data_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        log_row = QWidget()
        log_layout = QHBoxLayout(log_row)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_label = QLabel(str(paths.log_file))
        log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_log = QPushButton("Open")
        open_log.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.log_file))))
        log_layout.addWidget(log_label, 1)
        log_layout.addWidget(open_log)

        form = QFormLayout()
        form.addRow("Sailwind folder", game_row)
        form.addRow("GitHub token", token_row)
        form.addRow("Data folder", data_label)
        form.addRow("Log file", log_row)

        self.warn_missing = QCheckBox("Warn when starting a pack with missing mods")
        self.warn_missing.setChecked(config.warn_missing_mods)
        form.addRow("Missing mods", self.warn_missing)
        self.warn_launch_option = QCheckBox("Remind me when Sailwind's Steam launch options can't load mods")
        self.warn_launch_option.setChecked(config.warn_proton_launch_option)
        if os.name != "nt":
            form.addRow("Proton", self.warn_launch_option)
        self.check_updates = QCheckBox("Check GitHub for Sailwind Mod Synchronizer updates")
        self.check_updates.setChecked(config.check_for_updates)
        form.addRow("App updates", self.check_updates)
        self.auto_scan = QCheckBox("Automatically check for mod updates")
        self.auto_scan.setToolTip(
            "Scans your catalog repositories for new releases after startup. "
            "Requires a GitHub token."
        )
        self.auto_scan.setChecked(config.auto_scan_mods)
        self.auto_scan.setEnabled(bool(config.token()))
        self.token.textChanged.connect(self._sync_auto_scan_enabled)
        form.addRow("Mod updates", self.auto_scan)
        self.auto_refresh_catalog = QCheckBox("Refresh the catalog once a day")
        self.auto_refresh_catalog.setToolTip(
            "Downloads the ModVersionChecker and Sailwind Mod Synchronizer catalogs in the background "
            "at startup and while the app stays open."
        )
        self.auto_refresh_catalog.setChecked(config.auto_refresh_catalog)
        form.addRow("Catalog", self.auto_refresh_catalog)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        hint = QLabel(
            "A GitHub token is optional but recommended. Unauthenticated API calls are limited to 60/hour. "
            "Create a classic token; no scopes are required for public repositories."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self.resize(640, 320)

    def _toggle_token(self, checked: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.token.setEchoMode(mode)

    def _sync_auto_scan_enabled(self, text: str) -> None:
        self.auto_scan.setEnabled(bool(text.strip()))

    def _open_github_token_page(self) -> None:
        QDesktopServices.openUrl(QUrl(GITHUB_NEW_TOKEN_URL))

    def _browse_game(self) -> None:
        start = self.game_path.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select Sailwind folder", start)
        if chosen:
            self.game_path.setText(chosen)

    def apply_to(self, config: AppConfig) -> None:
        config.game_path = self.game_path.text().strip()
        config.github_token = self.token.text().strip()
        config.warn_missing_mods = self.warn_missing.isChecked()
        config.warn_proton_launch_option = self.warn_launch_option.isChecked()
        config.check_for_updates = self.check_updates.isChecked()
        config.auto_scan_mods = self.auto_scan.isChecked()
        config.auto_refresh_catalog = self.auto_refresh_catalog.isChecked()
