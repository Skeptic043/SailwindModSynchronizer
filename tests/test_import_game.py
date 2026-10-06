from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from plugin_metadata_fixture import managed_dll

from sailwind_mod_sync.catalog.mvc import merge_catalog
from sailwind_mod_sync.config import AppConfig
from sailwind_mod_sync.http_util import HttpClient
from sailwind_mod_sync.manager import Manager
from sailwind_mod_sync.paths import AppPaths


class _NoHttp(HttpClient):
    def __init__(self) -> None:
        self.token = ""
        self._owns_client = False
        self._client = None

    def close(self) -> None:
        return None

    def get_json(self, *args, **kwargs):
        raise AssertionError("HTTP should not be used for local plugin import")

    def download(self, *args, **kwargs):
        raise AssertionError("HTTP should not be used for local plugin import")


def _cache_bepinex(manager: Manager, tmp_path: Path) -> None:
    bx = tmp_path / "bx.zip"
    with zipfile.ZipFile(bx, "w") as zf:
        zf.writestr("BepInExPack/winhttp.dll", b"dll")
        zf.writestr("BepInExPack/BepInEx/core/BepInEx.Preloader.dll", b"MZ")
        zf.writestr("BepInExPack/BepInEx/patchers/.keep", b"")
    manager.library.ingest_bepinex_zip("5.4.2305", bx)


def test_import_game_plugins(paths: AppPaths, tmp_path: Path) -> None:
    game = tmp_path / "Sailwind"
    plugins = game / "BepInEx" / "plugins" / "Dizzy.Gamma"
    plugins.mkdir(parents=True)
    (plugins / "Dizzy.Gamma.dll").write_bytes(b"MZ" + b"com.dizzy.sailwind.gamma")
    (game / "Sailwind.exe").write_bytes(b"MZ")
    (game / "BepInEx" / "LogOutput.log").write_text(
        "[Info   :   BepInEx] Loading [Dizzy Gamma 0.3.3]\n",
        encoding="utf-8",
    )
    (game / "BepInEx" / "config").mkdir(parents=True)
    (game / "BepInEx" / "config" / "com.dizzy.sailwind.gamma.cfg").write_text("gamma=1\n", encoding="utf-8")
    manager = Manager(paths=paths, config=AppConfig(game_path=str(game)), http=_NoHttp())
    _cache_bepinex(manager, tmp_path)
    result = manager.import_game_plugins("From game", plugins_dir=game / "BepInEx" / "plugins")
    assert result.skipped_plugins == ()
    pack = result.pack
    assert pack.name == "From game"
    assert len(pack.mods) == 1
    assert pack.mods[0].guid == "com.dizzy.sailwind.gamma"
    assert pack.mods[0].version == "0.3.3"
    assert (manager.packs.plugins_dir(pack.id) / "Dizzy.Gamma" / "Dizzy.Gamma.dll").exists()
    assert (manager.packs.instance_dir(pack.id) / "BepInEx" / "config" / "com.dizzy.sailwind.gamma.cfg").exists()
    manager.close()


def test_import_game_plugins_links_catalog_by_folder_name(paths: AppPaths, tmp_path: Path) -> None:
    game = tmp_path / "Sailwind"
    plugins = game / "BepInEx" / "plugins" / "Dizzy.Gamma"
    plugins.mkdir(parents=True)
    (plugins / "Dizzy.Gamma.dll").write_bytes(b"MZ" + b"\0" * 64)
    (game / "Sailwind.exe").write_bytes(b"MZ")
    (game / "BepInEx" / "LogOutput.log").write_text(
        "[Info   :   BepInEx] Loading [Dizzy Gamma 0.3.3]\n",
        encoding="utf-8",
    )
    manager = Manager(paths=paths, config=AppConfig(game_path=str(game)), http=_NoHttp())
    manager.catalog = merge_catalog(
        [{"guid": "com.dizzy.sailwind.gamma", "repo": "https://github.com/foxyv/dizzy_sailwind_mods"}],
        [{"guid": "com.dizzy.sailwind.gamma", "repo": "https://github.com/foxyv/dizzy_sailwind_mods", "version": "v0.3.3"}],
    )
    _cache_bepinex(manager, tmp_path)
    result = manager.import_game_plugins("From game", plugins_dir=game / "BepInEx" / "plugins")
    assert result.skipped_plugins == ()
    pack = result.pack
    assert pack.mods[0].guid == "com.dizzy.sailwind.gamma"
    assert pack.mods[0].repo.endswith("dizzy_sailwind_mods")
    assert manager.library.has_mod("com.dizzy.sailwind.gamma", "0.3.3")
    manager.close()


def test_game_import_reports_all_ambiguous_units_without_creating_pack(paths: AppPaths, tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    bundle = plugins / "Bundle"
    bundle.mkdir(parents=True)
    original = managed_dll((("a.one", "One", "1.0"), ("b.two", "Two", "2.0")))
    (bundle / "Bundle.dll").write_bytes(original)
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    original_packs = manager.packs.list_packs()
    try:
        with pytest.raises(ValueError, match="No plugins imported") as exc:
            manager.import_game_plugins("From game", plugins_dir=plugins)
        assert "Bundle" in str(exc.value)
        assert "a.one" in str(exc.value) and "b.two" in str(exc.value)
        assert manager.packs.list_packs() == original_packs
        assert (bundle / "Bundle.dll").read_bytes() == original
    finally:
        manager.close()


@pytest.mark.parametrize("include_ambiguous", [False, True])
def test_game_import_ui_reports_skipped_units(
    paths: AppPaths, tmp_path: Path, monkeypatch, include_ambiguous: bool,
) -> None:
    from PySide6.QtWidgets import QApplication, QDialog

    from sailwind_mod_sync.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "Single.dll").write_bytes(managed_dll())
    bundle = plugins / "Bundle"
    if include_ambiguous:
        bundle.mkdir()
        (bundle / "Bundle.dll").write_bytes(
            managed_dll((("a.one", "One", "1.0"), ("b.two", "Two", "2.0")))
        )
    manager = Manager(paths=paths, config=AppConfig(check_for_updates=False), http=_NoHttp())
    _cache_bepinex(manager, tmp_path)
    manager.catalog = merge_catalog(
        [{"guid": "private.example.plugin", "repo": "https://github.com/example/plugin"}], [],
    )
    window = MainWindow(manager)
    warnings: list[str] = []
    try:
        monkeypatch.setattr("sailwind_mod_sync.ui.main_window.ImportPluginsDialog.exec", lambda _: QDialog.Accepted)
        monkeypatch.setattr("sailwind_mod_sync.ui.main_window.ImportPluginsDialog.pack_name", lambda _: "From game")
        monkeypatch.setattr("sailwind_mod_sync.ui.main_window.ImportPluginsDialog.plugins_dir", lambda _: plugins)
        monkeypatch.setattr(
            "sailwind_mod_sync.ui.main_window.QMessageBox.warning", lambda _w, _t, text: warnings.append(text),
        )
        monkeypatch.setattr(window, "_run", lambda work, on_ok, _message: on_ok(work(lambda _progress: None)))
        monkeypatch.setattr(window, "_offer_catalog_association", lambda _mods, **_kwargs: None)
        window._import_game_plugins()
        imported = next(pack for pack in manager.packs.list_packs() if pack.name == "From game")
        assert [mod.guid for mod in imported.mods] == ["private.example.plugin"]
        assert (manager.packs.plugins_dir(imported.id) / "Single" / "Single.dll").exists()
        assert not (manager.packs.plugins_dir(imported.id) / "Bundle").exists()
        assert len(warnings) == int(include_ambiguous)
        if include_ambiguous:
            assert "From game" in warnings[0] and "Bundle" in warnings[0]
            assert "a.one" in warnings[0] and "b.two" in warnings[0]
            assert (bundle / "Bundle.dll").exists()
    finally:
        window.close()
        window.deleteLater()
        manager.close()
    app.processEvents()
