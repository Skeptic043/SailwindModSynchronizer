from pathlib import Path

from PySide6.QtWidgets import QApplication

from sailwind_mod_sync.resources import icon_path, load_icon_pixmap, raster_icon_path


def test_icon_path_finds_repo_png() -> None:
    found = icon_path()
    assert found is not None
    assert found.name in {"icon.ico", "icon.png"}
    assert found.is_file()
    assert found.stat().st_size > 0
    repo_assets = Path(__file__).resolve().parents[1] / "assets"
    assert found.parent == repo_assets


def test_raster_icon_path_prefers_png() -> None:
    found = raster_icon_path()
    assert found is not None
    assert found.name == "icon.png"
    assert found.parent == Path(__file__).resolve().parents[1] / "assets"


def test_load_icon_pixmap_downscales_png() -> None:
    app = QApplication.instance() or QApplication([])
    pix = load_icon_pixmap(72, 1.0)
    assert pix is not None
    assert not pix.isNull()
    assert pix.width() == 72
    assert pix.height() == 72
    hidpi = load_icon_pixmap(72, 2.0)
    assert hidpi is not None
    assert hidpi.width() == 144
    assert hidpi.devicePixelRatio() == 2.0
    app.processEvents()


def test_changelog_has_an_entry_for_the_current_version() -> None:
    from sailwind_mod_sync.constants import APP_VERSION
    from sailwind_mod_sync.resources import changelog_path
    from sailwind_mod_sync.ui.changelog_dialog import load_changelog

    found = changelog_path()
    assert found is not None
    assert found.parent == Path(__file__).resolve().parents[1] / "assets"
    assert f"## {APP_VERSION} " in load_changelog()


def test_changelog_dialog_renders_markdown() -> None:
    from sailwind_mod_sync.ui.changelog_dialog import ChangelogDialog

    app = QApplication.instance() or QApplication([])
    dialog = ChangelogDialog()
    text = dialog.browser.toPlainText()
    assert "Change Log" in text
    assert "0.1.0" in text
    assert "##" not in text
    dialog.deleteLater()
