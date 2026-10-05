from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
)

from sailwind_mod_sync.config import AppConfig, load_config
from sailwind_mod_sync.models import CatalogEntry, LibraryEntry, ArtifactMeta, ModDetails, ModPack, PinnedMod
from sailwind_mod_sync.paths import AppPaths
from sailwind_mod_sync.updater import AppUpdate
from sailwind_mod_sync.ui.settings_dialog import SettingsDialog
from sailwind_mod_sync.ui.update_dialog import OPEN, SKIP, UPDATE, UpdateDialog
from sailwind_mod_sync.ui.associate_dialog import AssociateCatalogDialog, AssociateTarget
from sailwind_mod_sync.ui.catalog_view import CatalogView, catalog_pack_button
from sailwind_mod_sync.ui.hidden_mods_dialog import HiddenModsDialog
from sailwind_mod_sync.ui.library_view import LibraryView
from sailwind_mod_sync.ui.mod_details_dialog import ModDetailsDialog
from sailwind_mod_sync.ui.links import help_text_to_html, repo_button
from sailwind_mod_sync.ui.missing_mods_dialog import MissingModsWarningDialog
from sailwind_mod_sync.ui.pack_view import PackView
from sailwind_mod_sync.ui.progress_dialog import BusyDialog
from sailwind_mod_sync.ui.tables import enable_column_resize
from sailwind_mod_sync.ui.version_dialog import SelectVersionDialog


def test_busy_dialog_shows_status_text() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = BusyDialog(None, "Working", "Adding SailwindDifficulty to library…")
    try:
        assert dialog.windowTitle() == "Working"
        assert "SailwindDifficulty" in dialog._label.text()
        dialog.set_message("Downloading SailwindDifficulty.dll (40%)")
        assert "40%" in dialog._label.text()
        dialog.allow_close()
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_repo_button_opens_github_url() -> None:
    app = QApplication.instance() or QApplication([])
    button = repo_button("https://github.com/NANDbrew/StickyFix")
    assert button.text() == "Open GitHub in Browser"
    assert button.isEnabled()
    assert button.toolTip() == "https://github.com/NANDbrew/StickyFix"
    missing = repo_button("")
    assert not missing.isEnabled()
    app.processEvents()


def test_help_text_to_html_makes_release_url_clickable() -> None:
    html = help_text_to_html(
        "Could not finish downloading HugeMod.zip from GitHub.\n"
        "1. Open the GitHub release page:\n"
        "   https://github.com/example/mod/releases/tag/v1.0.0\n"
        "3. Import Mod DLL/ZIP"
    )
    assert '<a href="https://github.com/example/mod/releases/tag/v1.0.0">' in html
    assert "https://github.com/example/mod/releases/tag/v1.0.0</a>" in html
    assert "<br>" in html
    assert "&lt;" not in html


def test_table_columns_are_interactive() -> None:
    app = QApplication.instance() or QApplication([])
    table = QTableWidget(0, 3)
    enable_column_resize(table, [80, 120, 160])
    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Interactive
    assert table.columnWidth(1) == 120
    table.deleteLater()
    app.processEvents()


def test_missing_mods_warning_defaults_to_remind() -> None:
    app = QApplication.instance() or QApplication([])
    missing = [
        PinnedMod(guid="local.discord.mystery", version="1.0.0"),
        PinnedMod(guid="com.example.offline", version="2.0.0"),
    ]
    dialog = MissingModsWarningDialog(missing, "Test Pack")
    try:
        assert dialog.windowTitle() == "Missing mods"
        labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "2 missing mods" in labels
        assert "local.discord.mystery 1.0.0" in labels
        assert "com.example.offline 2.0.0" in labels
        assert dialog.remind_radio.isChecked()
        assert not dialog.stop_reminding
        dialog.stop_radio.setChecked(True)
        assert dialog.stop_reminding
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_catalog_view_has_add_repo_button() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    try:
        buttons = view.findChildren(QPushButton)
        labels = [button.text() for button in buttons]
        assert "Add GitHub repo" in labels
        assert "Refresh catalog" in labels
        boxes = [box.text() for box in view.findChildren(QCheckBox)]
        assert "Hide mods in current pack" in boxes
    finally:
        view.deleteLater()
    app.processEvents()


def _catalog_entry(guid: str = "com.example.mod", latest: str = "1.2.0") -> CatalogEntry:
    return CatalogEntry(
        repo=f"https://github.com/example/{guid.rsplit('.', 1)[-1]}",
        guids=[guid],
        primary_guid=guid,
        name="mod",
        latest_raw=f"v{latest}",
        latest_version=latest,
        available=True,
    )


def _shown_entry(view: CatalogView, guid: str) -> CatalogEntry:
    return next(entry for entry in view._entries if entry.primary_guid == guid)


def test_catalog_pack_button_labels() -> None:
    entry = _catalog_entry()
    assert catalog_pack_button(entry, None)[0] == "Add to pack"
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[PinnedMod(guid=entry.primary_guid, version="1.2.0")],
    )
    assert catalog_pack_button(entry, pack)[0] == "In pack"
    outdated = ModPack(
        id="crew",
        name="Crew",
        mods=[PinnedMod(guid=entry.primary_guid, version="1.0.0")],
    )
    assert catalog_pack_button(entry, outdated)[0] == "Update"


def test_catalog_view_shows_add_to_pack_button() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    pack = ModPack(id="crew", name="Crew", mods=[])

    def action_labels() -> list[str]:
        labels: list[str] = []
        for row in range(view.table.rowCount()):
            widget = view.table.cellWidget(row, 5)
            if widget is None:
                continue
            labels.extend(button.text() for button in widget.findChildren(QPushButton))
        return labels

    try:
        view.set_data([_catalog_entry()], pack)
        labels = action_labels()
        assert "Add to pack" in labels
        assert "Add to library" not in labels
        assert "In library" not in labels
        packed = ModPack(
            id="crew",
            name="Crew",
            mods=[PinnedMod(guid="com.example.mod", version="1.2.0")],
        )
        view.set_data([_catalog_entry()], packed)
        labels = action_labels()
        assert "In pack" in labels
        assert "Add to pack" not in labels
        caught: list[tuple[str, str]] = []
        view.install_requested.connect(lambda guid, repo: caught.append((guid, repo)))
        add_buttons = [
            button
            for button in view.table.cellWidget(0, 5).findChildren(QPushButton)
            if button.text() == "In pack"
        ]
        assert add_buttons
        add_buttons[0].click()
        assert caught == [("com.example.mod", "https://github.com/example/mod")]
    finally:
        view.deleteLater()
    app.processEvents()


def test_catalog_context_menu_removes_custom_entry() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    removed: list[tuple[str, str]] = []
    view.remove_custom_requested.connect(lambda guid, repo: removed.append((guid, repo)))
    custom = CatalogEntry(
        repo="https://github.com/example/custom",
        guids=["com.example.custom"],
        primary_guid="com.example.custom",
        name="custom",
        latest_raw="v1.0.0",
        latest_version="1.0.0",
        available=True,
        custom=True,
    )
    try:
        view.set_data([_catalog_entry(), custom], None)
        labels = [
            button.text()
            for row in range(view.table.rowCount())
            for button in view.table.cellWidget(row, 5).findChildren(QPushButton)
        ]
        assert "Remove" not in labels
        assert view.table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
        builtin = view._menu_for_entry(_shown_entry(view, "com.example.mod"))
        assert builtin is not None
        assert [action.text() for action in builtin.actions() if not action.isSeparator()] == [
            "Hide Mod From Catalog"
        ]
        menu = view._menu_for_entry(_shown_entry(view, "com.example.custom"))
        assert menu is not None
        items = [action.text() for action in menu.actions() if not action.isSeparator()]
        assert items == ["Remove from Catalog"]
        menu.actions()[0].trigger()
        assert removed == [("com.example.custom", "https://github.com/example/custom")]
    finally:
        view.deleteLater()
    app.processEvents()


def test_catalog_hides_builtin_entry_from_context_menu() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    hidden: list[tuple[str, str]] = []
    view.hide_requested.connect(lambda guid, repo: hidden.append((guid, repo)))
    try:
        view.set_data(
            [_catalog_entry("com.example.mod"), _catalog_entry("com.example.other")],
            None,
        )
        menu = view._menu_for_entry(_shown_entry(view, "com.example.mod"))
        assert menu is not None
        next(action for action in menu.actions() if action.text() == "Hide Mod From Catalog").trigger()
        assert hidden == [("com.example.mod", "https://github.com/example/mod")]
        view.set_data(
            [_catalog_entry("com.example.mod"), _catalog_entry("com.example.other")],
            None,
            hidden_guids=["com.example.mod"],
        )
        assert view.table.rowCount() == 1
        assert view.table.item(0, 2).text() == "com.example.other"
        assert view._menu_for_entry(_shown_entry(view, "com.example.mod")) is not None
    finally:
        view.deleteLater()
    app.processEvents()


def test_hidden_mods_dialog_unhides_selected() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = HiddenModsDialog([("com.example.mod", "mod"), ("com.example.other", "other")])
    shown: list[str] = []
    dialog.unhide_requested.connect(shown.append)
    try:
        assert dialog.windowTitle() == "Hidden Mods"
        assert dialog.mods.count() == 2
        assert not dialog.unhide.isEnabled()
        dialog.mods.setCurrentRow(0)
        assert dialog.unhide.isEnabled()
        dialog.unhide.click()
        assert shown == ["com.example.mod"]
        assert dialog.mods.count() == 1
        assert dialog.mods.item(0).data(Qt.ItemDataRole.UserRole) == "com.example.other"
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_library_double_click_requests_details() -> None:
    app = QApplication.instance() or QApplication([])
    view = LibraryView()
    caught: list[tuple[str, str]] = []
    view.details_requested.connect(lambda guid, version: caught.append((guid, version)))
    entry = LibraryEntry(
        guid="com.example.mod",
        version="1.2.0",
        path=Path("."),
        zip_path=Path("."),
        extracted_dir=Path("."),
        meta=ArtifactMeta(
            guid="com.example.mod",
            version="1.2.0",
            version_raw="v1.2.0",
            repo="https://github.com/example/mod",
            source_url="https://example/mod.zip",
            sha256="abc",
            filename="mod.zip",
        ),
        size_bytes=12,
    )
    try:
        view.set_entries([entry])
        view._on_double_click(0, 0)
        assert caught == [("com.example.mod", "1.2.0")]
        assert view.table.item(0, 0).text() == "mod"
        assert view.table.horizontalHeaderItem(0).text() == "Name"
        assert "Rename" in [button.text() for button in view.findChildren(QPushButton)]
    finally:
        view.deleteLater()
    app.processEvents()


def test_library_context_menu_finds_in_catalog() -> None:
    app = QApplication.instance() or QApplication([])
    view = LibraryView()
    caught: list[str] = []
    view.find_in_catalog_requested.connect(caught.append)
    entry = LibraryEntry(
        guid="com.example.mod",
        version="1.2.0",
        path=Path("."),
        zip_path=Path("."),
        extracted_dir=Path("."),
        meta=ArtifactMeta(
            guid="com.example.mod",
            version="1.2.0",
            version_raw="v1.2.0",
            repo="https://github.com/example/mod",
            source_url="https://example/mod.zip",
            sha256="abc",
            filename="mod.zip",
        ),
        size_bytes=12,
    )
    try:
        view.set_entries([entry])
        assert view.table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
        assert view._context_menu_for_row(-1) is None
        menu = view._context_menu_for_row(0)
        assert menu is not None
        labels = [action.text() for action in menu.actions()]
        assert labels == ["Find in Catalog"]
        menu.actions()[0].trigger()
        assert caught == ["com.example.mod"]
    finally:
        view.deleteLater()
    app.processEvents()


def test_catalog_reveal_mod_selects_matching_row(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    scrolled: list[object] = []
    monkeypatch.setattr(
        view.table,
        "scrollToItem",
        lambda item, hint=QAbstractItemView.ScrollHint.EnsureVisible: scrolled.append(hint) or True,
    )
    try:
        view.set_data(
            [
                _catalog_entry("com.example.zebra", "1.0.0"),
                CatalogEntry(
                    repo="https://github.com/example/apple",
                    guids=["com.example.apple"],
                    primary_guid="com.example.apple",
                    name="apple",
                    latest_raw="v2.0.0",
                    latest_version="2.0.0",
                    available=True,
                ),
            ],
            None,
        )
        view.hide_in_pack.setChecked(True)
        view._filter.setText("zebra")
        assert view.table.rowCount() == 1
        assert view.reveal_mod("com.example.apple")
        assert view._filter.text() == ""
        assert not view.hide_in_pack.isChecked()
        assert view.table.rowCount() == 2
        selected = [
            view.table.item(index.row(), 2).text()
            for index in view.table.selectionModel().selectedRows()
        ]
        assert selected == ["com.example.apple"]
        assert view.table.item(view.table.currentRow(), 2).text() == "com.example.apple"
        highlight = view.table.palette().color(QPalette.ColorRole.Highlight)
        assert view.table.item(view.table.currentRow(), 0).background().color() == highlight
        assert QAbstractItemView.ScrollHint.PositionAtCenter in scrolled
        assert not view.reveal_mod("com.missing.mod")
    finally:
        view.deleteLater()
    app.processEvents()


def test_catalog_reveal_clears_when_another_row_selected() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[PinnedMod(guid="com.example.apple", version="2.0.0")],
    )
    try:
        view.set_data(
            [
                _catalog_entry("com.example.zebra", "1.0.0"),
                CatalogEntry(
                    repo="https://github.com/example/apple",
                    guids=["com.example.apple"],
                    primary_guid="com.example.apple",
                    name="apple",
                    latest_raw="v2.0.0",
                    latest_version="2.0.0",
                    available=True,
                ),
            ],
            pack,
        )
        assert view.reveal_mod("com.example.apple")
        highlight = view.table.palette().color(QPalette.ColorRole.Highlight)
        apple_row = view.table.currentRow()
        assert view.table.item(apple_row, 0).background().color() == highlight
        zebra_row = next(
            row
            for row in range(view.table.rowCount())
            if view.table.item(row, 2).text() == "com.example.zebra"
        )
        view.table.selectRow(zebra_row)
        assert view._revealed_key == ""
        apple_bg = view.table.item(apple_row, 0).background().color()
        zebra_bg = view.table.item(zebra_row, 0).background().color()
        assert apple_bg != highlight
        assert apple_bg != zebra_bg
    finally:
        view.deleteLater()
    app.processEvents()


def test_catalog_double_click_requests_details() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    caught: list[tuple[str, str]] = []
    view.details_requested.connect(lambda guid, repo: caught.append((guid, repo)))
    try:
        view.set_data([_catalog_entry("com.example.mod", "1.2.0")], None)
        view._on_double_click(0, 0)
        assert caught == [("com.example.mod", "https://github.com/example/mod")]
    finally:
        view.deleteLater()
    app.processEvents()


def test_catalog_hides_in_pack_rows_and_tints_them() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[PinnedMod(guid="com.example.mod", version="1.2.0")],
    )
    try:
        view.set_data(
            [_catalog_entry("com.example.mod"), _catalog_entry("com.example.other")],
            pack,
        )
        assert view.table.rowCount() == 2

        def color_for(guid: str):
            for row in range(view.table.rowCount()):
                if view.table.item(row, 2).text() == guid:
                    return view.table.item(row, 0).background().color()
            raise AssertionError(guid)

        assert color_for("com.example.mod") != color_for("com.example.other")
        view.hide_in_pack.setChecked(True)
        view._filter.setText("other")
        assert view.table.rowCount() == 1
        assert view.table.item(0, 2).text() == "com.example.other"
        assert view.reveal_mod("com.example.mod")
        assert not view.hide_in_pack.isChecked()
        assert view._filter.text() == ""
        selected = [
            view.table.item(index.row(), 2).text()
            for index in view.table.selectionModel().selectedRows()
        ]
        assert selected == ["com.example.mod"]
    finally:
        view.deleteLater()
    app.processEvents()


def test_mod_details_dialog_shows_installed_versions() -> None:
    app = QApplication.instance() or QApplication([])
    details = ModDetails(
        guid="com.example.mod",
        version="1.1.0",
        name="Example Mod",
        repo="https://github.com/example/mod",
        source_url="https://example/mod.zip",
        filename="mod.zip",
        sha256="abc",
        downloaded_at="2026-01-01",
        plugin_folders=["Mod"],
        size_bytes=2048,
        installed_versions=["1.1.0", "1.0.0"],
        catalog_latest="v1.2.0",
        pack_pins=[("Crew", "1.1.0")],
    )
    dialog = ModDetailsDialog(details)
    try:
        assert dialog.windowTitle() == "Example Mod"
        texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "1.1.0 (this row)" in texts
        assert "1.0.0" in texts
        assert "v1.2.0" in texts
        assert "Crew (1.1.0)" in texts
        assert "Loading README" in dialog.readme.toPlainText()
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_library_view_uses_supplied_name_and_emits_rename() -> None:
    app = QApplication.instance() or QApplication([])
    view = LibraryView()
    renamed: list[str] = []
    view.rename_requested.connect(renamed.append)
    entry = LibraryEntry(
        guid="com.dizzy.sailwind.gamma",
        version="0.3.3",
        path=Path("."),
        zip_path=Path("."),
        extracted_dir=Path("."),
        meta=ArtifactMeta(
            guid="com.dizzy.sailwind.gamma",
            version="0.3.3",
            version_raw="v0.3.3",
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
            source_url="https://example/gamma.zip",
            sha256="abc",
            filename="gamma.zip",
            plugin_folders=["Dizzy.Gamma"],
        ),
        size_bytes=12,
    )
    try:
        view.set_entries([entry], names={"com.dizzy.sailwind.gamma": "Dizzy Gamma"})
        assert view.table.item(0, 0).text() == "Dizzy Gamma"
        assert view.table.item(0, 1).text() == "com.dizzy.sailwind.gamma"
        rename = next(button for button in view.findChildren(QPushButton) if button.text() == "Rename")
        rename.click()
        assert renamed == ["com.dizzy.sailwind.gamma"]
    finally:
        view.deleteLater()
    app.processEvents()


def test_pack_view_version_combo_emits_choice() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    chosen: list[tuple[str, str, str]] = []
    browsed: list[str] = []
    view.version_requested.connect(lambda guid, version, raw: chosen.append((guid, version, raw)))
    view.browse_versions_requested.connect(browsed.append)
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[
            PinnedMod(
                guid="com.example.mod",
                version="1.0.0",
                repo="https://github.com/example/mod",
                version_raw="v1.0.0",
            )
        ],
    )
    try:
        view.set_pack(
            pack,
            [_catalog_entry()],
            set(),
            {"com.example.mod": [("1.1.0", "v1.1.0"), ("1.0.0", "v1.0.0")]},
        )
        assert view.import_file.isEnabled()
        assert view.import_file.text() == "Import Mod DLL/ZIP"
        combo = view.table.cellWidget(0, 3)
        assert isinstance(combo, QComboBox)
        labels = [combo.itemText(index) for index in range(combo.count())]
        assert combo.currentText() == "v1.0.0"
        assert "v1.1.0" in labels
        assert "More versions…" in labels
        combo.setCurrentIndex(labels.index("More versions…"))
        assert browsed == ["com.example.mod"]
        assert combo.currentText() == "v1.0.0"
        combo.setCurrentIndex(labels.index("v1.1.0"))
        assert chosen == [("com.example.mod", "1.1.0", "v1.1.0")]
    finally:
        view.deleteLater()
    app.processEvents()


def test_pack_view_import_dll_zip_emits_when_pack_selected() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    clicked: list[int] = []
    view.import_file_clicked.connect(lambda: clicked.append(1))
    try:
        assert not view.import_file.isEnabled()
        view.set_pack(
            ModPack(id="crew", name="Crew", mods=[]),
            [],
        )
        assert view.import_file.isEnabled()
        view.import_file.click()
        assert clicked == [1]
        view.set_pack(None, [])
        assert not view.import_file.isEnabled()
    finally:
        view.deleteLater()
    app.processEvents()


def test_select_version_dialog_lists_library_and_remote() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SelectVersionDialog(
        guid="com.example.mod",
        name="Example",
        current_version="1.0.0",
        library_versions=[("1.0.0", "v1.0.0"), ("0.9.0", "v0.9.0")],
    )
    try:
        labels = [dialog.list.item(index).text() for index in range(dialog.list.count())]
        assert any("v1.0.0" in label and "current" in label for label in labels)
        assert any("v0.9.0" in label and "downloaded" in label for label in labels)
        dialog._on_remote([("1.2.0", "v1.2.0")])
        labels = [dialog.list.item(index).text() for index in range(dialog.list.count())]
        assert any("v1.2.0" in label and "download" in label for label in labels)
        dialog.list.setCurrentRow(0)
        dialog.accept()
        selected = dialog.selected()
        assert selected is not None
        assert selected[0] == "1.2.0"
        assert selected[1] == "v1.2.0"
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_select_version_dialog_add_hint() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SelectVersionDialog(
        guid="com.example.mod",
        name="Example",
        current_version="1.2.0",
        adding=True,
    )
    try:
        labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "add to the pack" in labels
        assert "downloads automatically" in labels
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_play_button_style_is_green() -> None:
    from sailwind_mod_sync.ui.main_window import PLAY_BUTTON_STYLE

    assert "#2e7d32" in PLAY_BUTTON_STYLE
    assert "color: white" in PLAY_BUTTON_STYLE


def _sample_update(*, installable: bool) -> AppUpdate:
    return AppUpdate(
        version="0.2.0",
        version_raw="v0.2.0",
        tag="v0.2.0",
        html_url="https://github.com/foxyv/SailwindModSynchronizer/releases/tag/v0.2.0",
        notes="Bug fixes",
        asset_name="SailwindModSynchronizer-0.2.0-windows.zip",
        download_url="https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.2.0/app.zip",
        installable=installable,
    )


def test_update_dialog_installable_buttons() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = UpdateDialog(_sample_update(installable=True))
    try:
        labels = [button.text() for button in dialog.findChildren(QPushButton)]
        assert "Update" in labels
        assert "Skip this version" in labels
        assert "Later" in labels
        assert "Open GitHub" not in labels
        update_btn = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Update")
        update_btn.click()
        assert dialog.choice() == UPDATE
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_update_dialog_opens_github_when_not_installable() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = UpdateDialog(_sample_update(installable=False))
    try:
        labels = [button.text() for button in dialog.findChildren(QPushButton)]
        assert "Open GitHub" in labels
        assert "Update" not in labels
        open_btn = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Open GitHub")
        open_btn.click()
        assert dialog.choice() == OPEN
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_update_dialog_skip_choice() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = UpdateDialog(_sample_update(installable=True))
    try:
        skip = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Skip this version")
        skip.click()
        assert dialog.choice() == SKIP
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_settings_has_update_checkbox(paths: AppPaths) -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(AppConfig(check_for_updates=False), paths)
    try:
        assert not dialog.check_updates.isChecked()
        dialog.check_updates.setChecked(True)
        config = AppConfig(check_for_updates=False)
        dialog.apply_to(config)
        assert config.check_for_updates is True
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_settings_has_daily_catalog_refresh_checkbox(paths: AppPaths) -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(AppConfig(), paths)
    try:
        assert dialog.auto_refresh_catalog.isChecked()
        dialog.auto_refresh_catalog.setChecked(False)
        config = AppConfig()
        dialog.apply_to(config)
        assert config.auto_refresh_catalog is False
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_settings_has_create_github_token_button(paths: AppPaths) -> None:
    from sailwind_mod_sync.constants import GITHUB_NEW_TOKEN_URL

    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(AppConfig(), paths)
    try:
        assert dialog.create_token.isEnabled()
        assert dialog.create_token.text() == "Create…"
        assert "personal access token" in dialog.create_token.toolTip().lower()
        assert "tokens/new" in GITHUB_NEW_TOKEN_URL
        assert "github.com" in GITHUB_NEW_TOKEN_URL
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_help_menu_has_check_for_updates(paths: AppPaths) -> None:
    from sailwind_mod_sync.http_util import HttpClient
    from sailwind_mod_sync.manager import Manager
    from sailwind_mod_sync.ui.main_window import MainWindow

    class _NoHttp(HttpClient):
        def __init__(self) -> None:
            self.token = ""
            self._owns_client = False
            self._client = None

        def close(self) -> None:
            return None

    app = QApplication.instance() or QApplication([])
    manager = Manager(paths=paths, config=AppConfig(check_for_updates=False), http=_NoHttp())
    manager.catalog = [
        CatalogEntry(
            repo="https://github.com/example/mod",
            guids=["com.example.mod"],
            primary_guid="com.example.mod",
            name="mod",
            latest_raw="v1.0.0",
            latest_version="1.0.0",
            available=True,
        )
    ]
    window = MainWindow(manager)
    try:
        help_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.menu() and action.menu().title().replace("&", "") == "Help"
        )
        items = [action.text().replace("&", "") for action in help_menu.actions()]
        assert "Check for updates…" in items
        assert "Test splash screen" in items
        assert "About" in items
        test_splash = next(
            action
            for action in help_menu.actions()
            if action.text().replace("&", "") == "Test splash screen"
        )
        test_splash.trigger()
        preview = window._launch_splash
        assert preview is not None
        assert preview._preview is True
        assert preview._opened is False
        preview.close()
        preview.deleteLater()
        window._launch_splash = None
        assert window.windowTitle().startswith("Sailwind Mod Synchronizer")
        assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
            "Pack",
            "Catalog",
        ]
        downloads_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.menu() and action.menu().title().replace("&", "") == "Tools"
        )
        download_items = [action.text().replace("&", "") for action in downloads_menu.actions()]
        assert download_items == [
            "Manage downloads…",
            "Import mod file…",
            "Scan updates",
            "Open AppData",
            "Open Plugins Folder",
            "Hidden Mods",
            "",
            "Clear cache…",
        ]
        labels = [button.text() for button in window.findChildren(QPushButton)]
        assert "Copy" in labels
        assert "Paste" in labels
        top_level = [action.text().replace("&", "") for action in window.menuBar().actions()]
        assert "Scan updates" not in top_level
        assert window._downloads.windowTitle() == "Download Management"
        assert not window._downloads.isVisible()
        window._open_downloads()
        assert window._downloads.isVisible()
        window._downloads.hide()
    finally:
        window.close()
        window.deleteLater()
        manager.close()
    app.processEvents()


def test_open_appdata_opens_data_root(paths: AppPaths, monkeypatch) -> None:
    from sailwind_mod_sync.http_util import HttpClient
    from sailwind_mod_sync.manager import Manager
    from sailwind_mod_sync.ui.main_window import MainWindow

    class _NoHttp(HttpClient):
        def __init__(self) -> None:
            self.token = ""
            self._owns_client = False
            self._client = None

        def close(self) -> None:
            return None

    opened: list[str] = []

    def fake_open(url) -> bool:
        opened.append(url.toLocalFile())
        return True

    app = QApplication.instance() or QApplication([])
    manager = Manager(paths=paths, config=AppConfig(check_for_updates=False), http=_NoHttp())
    manager.catalog = [_catalog_entry()]
    window = MainWindow(manager)
    try:
        monkeypatch.setattr("sailwind_mod_sync.ui.main_window.QDesktopServices.openUrl", fake_open)
        window._open_appdata()
        assert [Path(item).resolve() for item in opened] == [paths.root.resolve()]
        assert "Opened" in window.statusBar().currentMessage()
    finally:
        window.close()
        window.deleteLater()
        manager.close()
    app.processEvents()


def test_duplicate_pack_appears_and_is_selected(paths: AppPaths, monkeypatch) -> None:
    from sailwind_mod_sync.http_util import HttpClient
    from sailwind_mod_sync.manager import Manager
    from sailwind_mod_sync.ui.main_window import MainWindow

    class _NoHttp(HttpClient):
        def __init__(self) -> None:
            self.token = ""
            self._owns_client = False
            self._client = None

        def close(self) -> None:
            return None

    app = QApplication.instance() or QApplication([])
    manager = Manager(paths=paths, config=AppConfig(check_for_updates=False), http=_NoHttp())
    manager.catalog = [_catalog_entry()]
    window = MainWindow(manager)
    try:
        assert window.pack_list.count() >= 1
        source = window.pack_list.currentItem().text()
        monkeypatch.setattr(
            "sailwind_mod_sync.ui.main_window.QInputDialog.getText",
            lambda *_args, **_kwargs: (f"{source} copy", True),
        )
        window._duplicate_pack()
        names = [window.pack_list.item(index).text() for index in range(window.pack_list.count())]
        assert f"{source} copy" in names
        assert window.pack_list.currentItem().text() == f"{source} copy"
    finally:
        window.close()
        window.deleteLater()
        manager.close()
    app.processEvents()


def test_copy_pack_puts_share_text_on_clipboard(paths: AppPaths) -> None:
    from sailwind_mod_sync.http_util import HttpClient
    from sailwind_mod_sync.manager import Manager
    from sailwind_mod_sync.packs.share import parse_share_text
    from sailwind_mod_sync.ui.main_window import MainWindow

    class _NoHttp(HttpClient):
        def __init__(self) -> None:
            self.token = ""
            self._owns_client = False
            self._client = None

        def close(self) -> None:
            return None

    app = QApplication.instance() or QApplication([])
    manager = Manager(paths=paths, config=AppConfig(check_for_updates=False), http=_NoHttp())
    manager.catalog = [_catalog_entry()]
    pack = manager.packs.create("Clipboard Crew")
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(guid="com.example.mod", version="1.2.0", repo="https://github.com/example/mod"),
    )
    window = MainWindow(manager)
    try:
        window._reload_packs(select_id=pack.id)
        window._copy_pack()
        parsed = parse_share_text(QApplication.clipboard().text())
        assert parsed["name"] == "Clipboard Crew"
        assert parsed["mods"][0]["guid"] == "com.example.mod"
        assert "Copied Clipboard Crew" in window.statusBar().currentMessage()
    finally:
        window.close()
        window.deleteLater()
        manager.close()
    app.processEvents()


def test_pack_view_shows_associate_when_repo_missing() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    pack = ModPack(
        id="game",
        name="Game",
        mods=[PinnedMod(guid="local.stickyfix", version="1.2.0", repo="")],
    )
    try:
        view.set_pack(pack, [], {"local.stickyfix"})
        labels = [button.text() for button in view.findChildren(QPushButton)]
        assert "Add Repository" not in labels
        assert "GitHub" not in labels
        menu = view._menu_for_guid("local.stickyfix")
        assert menu is not None
        items = [action.text() for action in menu.actions() if not action.isSeparator()]
        assert items == ["Add Repository", "Import", "Remove"]
        found: list[str] = []
        view.find_repo_requested.connect(found.append)
        next(action for action in menu.actions() if action.text() == "Add Repository").trigger()
        assert found == ["local.stickyfix"]
    finally:
        view.deleteLater()
    app.processEvents()


def test_pack_view_context_menu_moves_github_off_row(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[
            PinnedMod(
                guid="com.example.mod",
                version="1.0.0",
                repo="https://github.com/example/mod",
            )
        ],
    )
    opened: list[str] = []
    monkeypatch.setattr(
        "sailwind_mod_sync.ui.pack_view.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    try:
        view.set_pack(pack, [_catalog_entry("com.example.mod", "1.2.0")], set())
        labels = [button.text() for button in view.table.cellWidget(0, 5).findChildren(QPushButton)]
        assert labels == ["Update", "Remove"]
        menu = view._menu_for_guid("com.example.mod")
        assert menu is not None
        items = [action.text() for action in menu.actions() if not action.isSeparator()]
        assert items == ["Open GitHub in Browser", "Show in Catalog", "Update", "Remove"]
        github = next(action for action in menu.actions() if action.text() == "Open GitHub in Browser")
        update = next(action for action in menu.actions() if action.text() == "Update")
        assert update.isEnabled()
        github.trigger()
        assert opened and "github.com/example/mod" in opened[0]
    finally:
        view.deleteLater()
    app.processEvents()


def test_pack_view_context_menu_shows_catalog_when_present() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    shown: list[str] = []
    view.show_in_catalog_requested.connect(shown.append)
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[
            PinnedMod(guid="com.example.mod", version="1.0.0", repo="https://github.com/example/mod"),
            PinnedMod(guid="local.orphan", version="1.0.0", repo=""),
        ],
    )
    try:
        view.set_pack(pack, [_catalog_entry("com.example.mod", "1.2.0")], set())
        catalog_menu = view._menu_for_guid("com.example.mod")
        assert catalog_menu is not None
        catalog_items = [action.text() for action in catalog_menu.actions() if not action.isSeparator()]
        assert "Show in Catalog" in catalog_items
        next(action for action in catalog_menu.actions() if action.text() == "Show in Catalog").trigger()
        assert shown == ["com.example.mod"]

        orphan_menu = view._menu_for_guid("local.orphan")
        assert orphan_menu is not None
        orphan_items = [action.text() for action in orphan_menu.actions() if not action.isSeparator()]
        assert "Show in Catalog" not in orphan_items
    finally:
        view.deleteLater()
    app.processEvents()


def test_associate_dialog_records_catalog_choice() -> None:
    app = QApplication.instance() or QApplication([])
    entry = CatalogEntry(
        repo="https://github.com/NANDbrew/StickyFix",
        guids=["com.nandbrew.stickyfix"],
        primary_guid="com.nandbrew.stickyfix",
        name="StickyFix",
        latest_raw="v1.3.0",
        latest_version="1.3.0",
        available=True,
    )
    dialog = AssociateCatalogDialog(
        [AssociateTarget(guid="local.stickyfix", version="1.2.0", name="StickyFix")],
        [entry],
    )
    try:
        assert dialog.catalog_list.count() == 1
        dialog.associate_btn.click()
        choices = dialog.choices()
        assert len(choices) == 1
        assert choices[0].guid == "local.stickyfix"
        assert choices[0].entry is not None
        assert choices[0].entry.primary_guid == "com.nandbrew.stickyfix"
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def _library_entry(guid: str, version: str, size: int, name: str | None = None) -> LibraryEntry:
    return LibraryEntry(
        guid=guid,
        version=version,
        path=Path("."),
        zip_path=Path("."),
        extracted_dir=Path("."),
        meta=ArtifactMeta(
            guid=guid,
            version=version,
            version_raw=version,
            repo=f"https://github.com/example/{guid.split('.')[-1]}",
            source_url="",
            sha256="abc",
            filename="mod.zip",
            plugin_folders=[name] if name else [],
        ),
        size_bytes=size,
    )


def test_catalog_header_click_sorts_rows() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    try:
        view.set_data(
            [
                _catalog_entry("com.example.zebra", "1.0.0"),
                CatalogEntry(
                    repo="https://github.com/example/apple",
                    guids=["com.example.apple"],
                    primary_guid="com.example.apple",
                    name="apple",
                    latest_raw="v2.0.0",
                    latest_version="2.0.0",
                    available=True,
                ),
            ],
            None,
        )
        assert view.table.isSortingEnabled()
        assert view.table.horizontalHeader().isSortIndicatorShown()
        assert view.table.horizontalHeader().sectionsClickable()
        names = [view.table.item(row, 0).text() for row in range(view.table.rowCount())]
        assert names == ["apple", "mod"]
        view.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        names = [view.table.item(row, 0).text() for row in range(view.table.rowCount())]
        assert names == ["mod", "apple"]
        view.table.sortItems(3, Qt.SortOrder.DescendingOrder)
        latests = [view.table.item(row, 3).text() for row in range(view.table.rowCount())]
        assert latests[0].startswith("v2.0.0")
    finally:
        view.deleteLater()
    app.processEvents()


def test_library_header_sorts_by_size_and_keeps_double_click() -> None:
    app = QApplication.instance() or QApplication([])
    view = LibraryView()
    caught: list[tuple[str, str]] = []
    view.details_requested.connect(lambda guid, version: caught.append((guid, version)))
    try:
        view.set_entries(
            [
                _library_entry("com.example.big", "1.0.0", 5000, "Big"),
                _library_entry("com.example.small", "2.0.0", 50, "Small"),
            ]
        )
        view.table.sortItems(3, Qt.SortOrder.AscendingOrder)
        assert view.table.item(0, 1).text() == "com.example.small"
        view._on_double_click(0, 0)
        assert caught == [("com.example.small", "2.0.0")]
        view.table.sortItems(2, Qt.SortOrder.DescendingOrder)
        assert view.table.item(0, 2).text() == "2.0.0"
    finally:
        view.deleteLater()
    app.processEvents()


def test_pack_header_sorts_by_mod_name() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[
            PinnedMod(guid="com.example.zebra", version="1.0.0", repo="https://github.com/example/zebra"),
            PinnedMod(guid="com.example.apple", version="2.0.0", repo="https://github.com/example/apple"),
        ],
    )
    catalog = [
        CatalogEntry(
            repo="https://github.com/example/zebra",
            guids=["com.example.zebra"],
            primary_guid="com.example.zebra",
            name="Zebra",
            latest_raw="v1.0.0",
            latest_version="1.0.0",
            available=True,
        ),
        CatalogEntry(
            repo="https://github.com/example/apple",
            guids=["com.example.apple"],
            primary_guid="com.example.apple",
            name="Apple",
            latest_raw="v2.0.0",
            latest_version="2.0.0",
            available=True,
        ),
    ]
    try:
        view.set_pack(pack, catalog)
        assert view.table.isSortingEnabled()
        assert view.table.item(0, 1).text() == "Zebra"
        view.table.sortItems(1, Qt.SortOrder.AscendingOrder)
        assert view.table.item(0, 1).text() == "Apple"
        assert view.table.item(1, 1).text() == "Zebra"
        view.table.sortItems(2, Qt.SortOrder.AscendingOrder)
        assert view.table.item(0, 2).text() == "com.example.apple"
    finally:
        view.deleteLater()
    app.processEvents()



def test_pack_view_reports_available_updates() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    try:
        pack = ModPack(
            id="crew",
            name="Crew",
            mods=[
                PinnedMod(guid="com.example.mod", version="1.0.0", repo="https://github.com/example/mod"),
                PinnedMod(guid="com.example.old", version="1.0.0", repo="https://github.com/example/old"),
                PinnedMod(guid="com.example.locked", version="1.2.0", repo="https://github.com/example/locked"),
            ],
        )
        view.set_pack(
            pack,
            [
                _catalog_entry("com.example.mod", "1.2.0"),
                _catalog_entry("com.example.old", "2.0.0"),
                _catalog_entry("com.example.locked", "1.2.0"),
            ],
        )
        assert view.available_updates() == 2
        view.set_pack(None, [])
        assert view.available_updates() == 0
    finally:
        view.deleteLater()
    app.processEvents()


def _fork_entry(guid: str = "com.example.mod", latest: str = "2.0.0") -> CatalogEntry:
    return CatalogEntry(
        repo="https://github.com/me/mod-fork",
        guids=[guid],
        primary_guid=guid,
        name="mod",
        latest_raw=f"v{latest}",
        latest_version=latest,
        available=True,
        custom=True,
        alternate=True,
    )


def test_catalog_lists_each_source_and_offers_switching() -> None:
    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[PinnedMod(guid="com.example.mod", version="1.2.0", repo="https://github.com/example/mod")],
    )
    caught: list[tuple[str, str]] = []
    view.install_requested.connect(lambda guid, repo: caught.append((guid, repo)))
    try:
        view.set_data([_catalog_entry(), _fork_entry()], pack, hidden_guids=["com.example.mod"])
        rows = {
            view.table.item(row, 1).text(): (
                view.table.item(row, 4).text(),
                view.table.cellWidget(row, 5).findChildren(QPushButton)[1],
            )
            for row in range(view.table.rowCount())
        }
        assert list(rows) == ["me/mod-fork"]
        view.set_data([_catalog_entry(), _fork_entry()], pack)
        rows = {
            view.table.item(row, 1).text(): (
                view.table.item(row, 4).text(),
                view.table.cellWidget(row, 5).findChildren(QPushButton)[1],
            )
            for row in range(view.table.rowCount())
        }
        assert rows["example/mod"][0] == "Installed 1.2.0"
        assert rows["example/mod"][1].text() == "In pack"
        assert rows["me/mod-fork"][0] == "Other source in pack"
        switch = rows["me/mod-fork"][1]
        assert switch.text() == "Switch source"
        switch.click()
        assert caught == [("com.example.mod", "https://github.com/me/mod-fork")]

        view.hide_in_pack.setChecked(True)
        assert [view.table.item(row, 1).text() for row in range(view.table.rowCount())] == ["me/mod-fork"]
        assert view.reveal_mod("com.example.mod", "https://github.com/example/mod")
        assert view.table.item(view.table.currentRow(), 1).text() == "example/mod"
    finally:
        view.deleteLater()
    app.processEvents()


def test_pack_view_names_the_source_of_mods_with_several_sources() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[PinnedMod(guid="com.example.mod", version="1.5.0", repo="https://github.com/me/mod-fork")],
    )
    try:
        view.set_pack(pack, [_catalog_entry("com.example.mod", "1.8.0"), _fork_entry()])
        assert view.table.item(0, 1).text() == "mod · me/mod-fork"
        assert view.table.item(0, 4).text() == "v2.0.0 (update)"
        view.set_pack(pack, [_fork_entry()])
        assert view.table.item(0, 1).text() == "mod"
    finally:
        view.deleteLater()
    app.processEvents()


def _refresh_window(paths: AppPaths, monkeypatch, **config):
    from sailwind_mod_sync.http_util import HttpClient
    from sailwind_mod_sync.manager import Manager
    from sailwind_mod_sync.ui.main_window import MainWindow

    class _NoHttp(HttpClient):
        def __init__(self) -> None:
            self.token = ""
            self._owns_client = False
            self._client = None

        def close(self) -> None:
            return None

    monkeypatch.setattr(MainWindow, "_maybe_check_updates", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_auto_scan_mods", lambda self: None)
    manager = Manager(paths=paths, config=AppConfig(check_for_updates=False, **config), http=_NoHttp())
    manager.catalog = [_catalog_entry()]
    return manager, MainWindow(manager)


def test_background_catalog_refresh_applies_when_due(paths: AppPaths, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    manager, window = _refresh_window(paths, monkeypatch, last_catalog_refresh="2026-01-01T00:00:00+00:00")
    fetched = [_catalog_entry(), _catalog_entry("com.example.other")]
    monkeypatch.setattr(manager, "fetch_catalog", lambda progress=None: fetched)
    try:
        window._maybe_refresh_catalog()
        assert window._catalog_bridge is not None
        deadline = time.monotonic() + 5
        while window._catalog_refresh_running() and time.monotonic() < deadline:
            app.processEvents()
        assert manager.catalog is fetched
        assert window.catalog_view.table.rowCount() == 2
        assert load_config(paths).last_catalog_refresh > "2026-01-01"
        window._maybe_refresh_catalog()
        assert window._catalog_bridge is None
    finally:
        window.close()
        window.deleteLater()
        manager.close()
    app.processEvents()


def test_background_catalog_refresh_waits_for_running_tasks_and_setting(paths: AppPaths, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    manager, window = _refresh_window(paths, monkeypatch)
    fetches: list[object] = []
    monkeypatch.setattr(manager, "fetch_catalog", lambda progress=None: fetches.append(progress) or [])
    try:
        window._busy = True
        window._maybe_refresh_catalog()
        assert window._catalog_bridge is None
        window._busy = False
        manager.config.auto_refresh_catalog = False
        window._maybe_refresh_catalog()
        assert window._catalog_bridge is None
        assert fetches == []
    finally:
        window.close()
        window.deleteLater()
        manager.close()
    app.processEvents()


def test_pack_view_offers_update_all_for_updatable_mods() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[
            PinnedMod(guid="com.example.mod", version="1.0.0", repo="https://github.com/example/mod"),
            PinnedMod(guid="com.example.old", version="1.0.0", repo="https://github.com/example/old"),
            PinnedMod(guid="com.example.gone", version="1.0.0", repo="https://github.com/example/gone"),
            PinnedMod(guid="com.example.locked", version="1.2.0", repo="https://github.com/example/locked"),
        ],
    )
    catalog = [
        _catalog_entry("com.example.mod", "1.2.0"),
        _catalog_entry("com.example.old", "2.0.0"),
        _catalog_entry("com.example.gone", "2.0.0"),
        _catalog_entry("com.example.locked", "1.2.0"),
    ]
    caught: list[list[str]] = []
    view.update_all_requested.connect(caught.append)
    try:
        view.show()
        view.set_pack(pack, catalog, {"com.example.gone"})
        assert view.update_all.isVisible()
        assert view.available_updates() == 3
        view.update_all.click()
        assert caught == [["com.example.mod", "com.example.old", "com.example.gone"]]
        view.set_actions_blocked(True)
        assert not view.update_all.isEnabled()
        assert view.update_all.text() == "Update all"
        view.set_actions_blocked(True, "Checking for updates…")
        assert not view.update_all.isEnabled()
        assert view.update_all.text() == "Checking for updates…"
        assert "becomes available when it finishes" in view.update_all.toolTip()
        view.set_actions_blocked(False)
        assert view.update_all.isEnabled()
        assert view.update_all.text() == "Update all"
        assert view.update_all.toolTip() == "Update 3 mod(s) to the latest version from their source"
        view.set_pack(pack, [_catalog_entry("com.example.locked", "1.2.0")], set())
        assert not view.update_all.isVisible()
        view.set_pack(None, [])
        assert not view.update_all.isVisible()
    finally:
        view.deleteLater()
    app.processEvents()


def test_pack_view_keeps_latest_version_and_offers_download_for_mods_not_downloaded() -> None:
    app = QApplication.instance() or QApplication([])
    view = PackView()
    pack = ModPack(
        id="crew",
        name="Crew",
        mods=[
            PinnedMod(guid="com.example.mod", version="1.2.0", repo="https://github.com/example/mod"),
            PinnedMod(guid="com.example.old", version="1.0.0", repo="https://github.com/example/old"),
            PinnedMod(guid="local.discord.mystery", version="1.0.0"),
        ],
    )
    catalog = [_catalog_entry("com.example.mod", "1.2.0"), _catalog_entry("com.example.old", "2.0.0")]
    caught: list[tuple[str, str]] = []
    view.download_requested.connect(lambda guid: caught.append(("download", guid)))
    view.update_requested.connect(lambda guid: caught.append(("update", guid)))
    view.import_requested.connect(lambda guid: caught.append(("import", guid)))
    try:
        view.set_pack(pack, catalog, {"com.example.mod", "com.example.old", "local.discord.mystery"})
        rows = {
            view.table.item(row, 2).text(): (
                view.table.item(row, 4).text(),
                view.table.cellWidget(row, 5).findChildren(QPushButton)[0],
            )
            for row in range(view.table.rowCount())
        }
        assert rows["com.example.mod"][0] == "v1.2.0"
        assert rows["com.example.old"][0] == "v2.0.0 (update)"
        assert [rows[guid][1].text() for guid in sorted(rows)] == ["Download", "Update", "Import"]
        for guid in sorted(rows):
            rows[guid][1].click()
        assert caught == [
            ("download", "com.example.mod"),
            ("update", "com.example.old"),
            ("import", "local.discord.mystery"),
        ]
        assert "3 not downloaded" in view.subtitle.text()
        menu = view._menu_for_guid("com.example.mod")
        assert "Download" in [action.text() for action in menu.actions()]
    finally:
        view.deleteLater()
    app.processEvents()


def test_catalog_hides_one_source_and_bare_guids_hide_every_shared_source() -> None:
    from dataclasses import replace

    app = QApplication.instance() or QApplication([])
    view = CatalogView()
    original = _catalog_entry()
    app_fork = replace(_catalog_entry(), repo="https://github.com/foxyv/mod", alternate=True)
    own_fork = _fork_entry()
    entries = [original, app_fork, own_fork]

    def sources() -> list[str]:
        return sorted(view.table.item(row, 1).text() for row in range(view.table.rowCount()))

    try:
        view.set_data(entries, None, hidden_guids=["com.example.mod|https://github.com/foxyv/mod"])
        assert sources() == ["example/mod", "me/mod-fork"]
        view.set_data(entries, None, hidden_guids=["com.example.mod"])
        assert sources() == ["me/mod-fork"]
        assert view.reveal_mod("com.example.mod", "https://github.com/foxyv/mod")
        assert "foxyv/mod" in sources()
    finally:
        view.deleteLater()
    app.processEvents()
