from __future__ import annotations

import zipfile
from pathlib import Path

from unittest.mock import MagicMock, patch

import pytest

from sailwind_mod_sync.catalog.mvc import find_entry
from sailwind_mod_sync.config import AppConfig, load_config
from sailwind_mod_sync.http_util import HttpClient, HttpError
from sailwind_mod_sync.library.special_mods import COOP_GUID
from sailwind_mod_sync.manager import Manager, TokenAuthError
from sailwind_mod_sync.models import CatalogEntry, PinnedMod, RemoteRelease
from sailwind_mod_sync.paths import AppPaths


class _NoHttp(HttpClient):
    def __init__(self) -> None:
        self.token = ""
        self._owns_client = False
        self._client = None

    def close(self) -> None:
        return None

    def get_json(self, *args, **kwargs):
        raise AssertionError("HTTP should not be used when artifacts are cached")

    def download(self, *args, **kwargs):
        raise AssertionError("HTTP should not be used when artifacts are cached")


def _zip_with(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def test_prepare_pack_from_library(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    bepinex_zip = _zip_with(
        tmp_path / "bx.zip",
        {
            "BepInExPack/winhttp.dll": b"dll",
            "BepInExPack/BepInEx/core/BepInEx.Preloader.dll": b"MZ",
            "BepInExPack/BepInEx/patchers/.keep": b"",
        },
    )
    manager.library.ingest_bepinex_zip("5.4.2305", bepinex_zip)
    mod_zip = _zip_with(tmp_path / "gamma.zip", {"Dizzy.Gamma/Dizzy.Gamma.dll": b"MZ"})
    manager.library.ingest_mod_zip(
        "com.dizzy.sailwind.gamma",
        "0.3.3",
        mod_zip,
        version_raw="v0.3.3",
        repo="https://github.com/foxyv/dizzy_sailwind_mods",
        source_url="https://example/gamma.zip",
    )
    pack = manager.packs.list_packs()[0]
    pack.bepinex = "5.4.2305"
    manager.packs.save(pack)
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(
            guid="com.dizzy.sailwind.gamma",
            version="0.3.3",
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
            plugin_folders=["Dizzy.Gamma"],
        ),
    )
    preloader = manager.prepare_pack(pack.id)
    assert preloader.exists()
    assert (manager.packs.plugins_dir(pack.id) / "Dizzy.Gamma" / "Dizzy.Gamma.dll").exists()
    manager.close()


def _ingest_bepinex(manager: Manager, tmp_path: Path) -> None:
    bepinex_zip = _zip_with(
        tmp_path / "bx.zip",
        {
            "BepInExPack/winhttp.dll": b"dll",
            "BepInExPack/BepInEx/core/BepInEx.Preloader.dll": b"MZ",
            "BepInExPack/BepInEx/patchers/.keep": b"",
        },
    )
    manager.library.ingest_bepinex_zip("5.4.2305", bepinex_zip)


def test_prepare_pack_leaves_unknown_mod_missing(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    _ingest_bepinex(manager, tmp_path)
    pack = manager.packs.list_packs()[0]
    pack.bepinex = "5.4.2305"
    manager.packs.save(pack)
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(guid="local.discord.mystery", version="1.0.0", repo=""),
    )
    preloader = manager.prepare_pack(pack.id)
    assert preloader.exists()
    refreshed = manager.packs.get(pack.id)
    missing = manager.missing_mods(refreshed)
    assert [mod.guid for mod in missing] == ["local.discord.mystery"]
    manager.close()


def test_prepare_pack_leaves_unfetchable_repo_missing(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    _ingest_bepinex(manager, tmp_path)
    pack = manager.packs.list_packs()[0]
    pack.bepinex = "5.4.2305"
    manager.packs.save(pack)
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(
            guid="com.example.offline",
            version="1.0.0",
            repo="https://github.com/example/offline-mod",
        ),
    )
    preloader = manager.prepare_pack(pack.id)
    assert preloader.exists()
    assert [mod.guid for mod in manager.missing_mods(manager.packs.get(pack.id))] == [
        "com.example.offline"
    ]
    manager.close()


def test_import_pack_does_not_abort_on_missing_mod(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    _ingest_bepinex(manager, tmp_path)
    source = manager.packs.create("Shared Pack")
    manager.packs.upsert_mod(
        source.id,
        PinnedMod(guid="local.discord.mystery", version="2.0.0", repo=""),
    )
    dest = tmp_path / "shared.json"
    manager.export_pack(source.id, dest, bundle=False)
    manager.packs.delete(source.id)
    imported = manager.import_pack(dest)
    assert imported.name == "Shared Pack"
    assert imported.mods[0].guid == "local.discord.mystery"
    assert manager.missing_mods(imported)[0].guid == "local.discord.mystery"
    catalog = find_entry(manager.catalog, "local.discord.mystery")
    assert catalog is not None
    assert catalog.custom
    assert catalog.latest_version == "2.0.0"
    manager.close()


def test_import_pack_json_does_not_need_bepinex(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    source = manager.packs.create("Recipe Only")
    manager.packs.upsert_mod(
        source.id,
        PinnedMod(guid="Fake.Mod", version="1.1.6", repo=""),
    )
    dest = tmp_path / "importtest.json"
    manager.export_pack(source.id, dest, bundle=False)
    manager.packs.delete(source.id)
    imported = manager.import_pack(dest)
    assert imported.name == "Recipe Only"
    assert [mod.guid for mod in manager.missing_mods(imported)] == ["Fake.Mod"]
    catalog = find_entry(manager.catalog, "Fake.Mod")
    assert catalog is not None
    assert catalog.custom
    assert catalog.latest_version == "1.1.6"
    manager.close()


def test_import_pack_adds_unknown_mod_with_repo_to_catalog(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    source = manager.packs.create("Custom Repo Pack")
    manager.packs.upsert_mod(
        source.id,
        PinnedMod(
            guid="com.example.unlisted",
            version="3.1.0",
            repo="https://github.com/example/unlisted",
            version_raw="v3.1.0",
            plugin_folders=["UnlistedMod"],
        ),
    )
    dest = tmp_path / "unlisted.json"
    manager.export_pack(source.id, dest, bundle=False)
    manager.packs.delete(source.id)
    imported = manager.import_pack(dest)
    catalog = find_entry(manager.catalog, "com.example.unlisted")
    assert catalog is not None
    assert catalog.custom
    assert catalog.repo == "https://github.com/example/unlisted"
    assert catalog.name == "UnlistedMod"
    assert catalog.latest_raw == "v3.1.0"
    assert imported.mods[0].guid == "com.example.unlisted"
    manager.close()


def test_import_pack_text_creates_new_pack(paths: AppPaths) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    source = manager.packs.create("Clipboard Crew")
    manager.packs.upsert_mod(
        source.id,
        PinnedMod(
            guid="com.example.unlisted",
            version="3.1.0",
            repo="https://github.com/example/unlisted",
            enabled=False,
        ),
    )
    text = manager.share_pack_text(source.id)
    manager.packs.delete(source.id)
    imported = manager.import_pack_text(text)
    assert imported.name == "Clipboard Crew"
    assert imported.id
    assert imported.mods[0].guid == "com.example.unlisted"
    assert imported.mods[0].enabled is False
    catalog = find_entry(manager.catalog, "com.example.unlisted")
    assert catalog is not None
    assert catalog.custom
    manager.close()


def test_import_pack_does_not_duplicate_existing_catalog_mod(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    manager.catalog = [
        CatalogEntry(
            repo="https://github.com/NANDBrew/StickyFix",
            guids=["com.nandbrew.stickyfix"],
            primary_guid="com.nandbrew.stickyfix",
            name="StickyFix",
            latest_raw="v1.0.0",
            latest_version="1.0.0",
            available=True,
        )
    ]
    source = manager.packs.create("Known Mod")
    manager.packs.upsert_mod(
        source.id,
        PinnedMod(guid="com.nandbrew.stickyfix", version="1.0.0", repo=""),
    )
    dest = tmp_path / "known.json"
    manager.export_pack(source.id, dest, bundle=False)
    imported = manager.import_pack(dest)
    catalog = find_entry(manager.catalog, "com.nandbrew.stickyfix")
    assert catalog is not None
    assert not catalog.custom
    assert imported.mods[0].guid == "com.nandbrew.stickyfix"
    manager.close()


def test_add_library_mod_to_pack(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    mod_zip = _zip_with(tmp_path / "gamma.zip", {"Dizzy.Gamma/Dizzy.Gamma.dll": b"MZ"})
    manager.library.ingest_mod_zip(
        "com.dizzy.sailwind.gamma",
        "0.3.3",
        mod_zip,
        version_raw="v0.3.3",
        repo="https://github.com/foxyv/dizzy_sailwind_mods",
        source_url="https://example/gamma.zip",
    )
    pack = manager.packs.create("From Library")
    pinned = manager.add_library_mod_to_pack(pack.id, "com.dizzy.sailwind.gamma", "0.3.3")
    assert pinned.guid == "com.dizzy.sailwind.gamma"
    assert pinned.version == "0.3.3"
    refreshed = manager.packs.get(pack.id)
    assert refreshed.find_mod(pinned.guid).version == "0.3.3"
    assert (manager.packs.plugins_dir(pack.id) / "Dizzy.Gamma" / "Dizzy.Gamma.dll").exists()
    manager.close()


def test_add_library_mod_replaces_other_version(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    for version in ("0.3.3", "0.4.0"):
        archive = _zip_with(tmp_path / f"gamma-{version}.zip", {"Dizzy.Gamma/Dizzy.Gamma.dll": version.encode()})
        manager.library.ingest_mod_zip(
            "com.dizzy.sailwind.gamma",
            version,
            archive,
            version_raw=f"v{version}",
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
            source_url=f"https://example/gamma-{version}.zip",
        )
    pack = manager.packs.create("Swap")
    manager.add_library_mod_to_pack(pack.id, "com.dizzy.sailwind.gamma", "0.3.3")
    pinned = manager.add_library_mod_to_pack(pack.id, "com.dizzy.sailwind.gamma", "0.4.0")
    assert pinned.version == "0.4.0"
    refreshed = manager.packs.get(pack.id)
    assert [mod.version for mod in refreshed.mods if mod.guid == pinned.guid] == ["0.4.0"]
    plugin = manager.packs.plugins_dir(pack.id) / "Dizzy.Gamma" / "Dizzy.Gamma.dll"
    assert plugin.read_bytes() == b"0.4.0"
    manager.close()


def test_set_pack_mod_version_switches_library_copy(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    guid = "com.dizzy.sailwind.gamma"
    for version in ("0.3.3", "0.4.0"):
        archive = _zip_with(tmp_path / f"gamma-{version}.zip", {"Dizzy.Gamma/Dizzy.Gamma.dll": version.encode()})
        manager.library.ingest_mod_zip(
            guid,
            version,
            archive,
            version_raw=f"v{version}",
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
            source_url=f"https://example/gamma-{version}.zip",
        )
    pack = manager.packs.create("Version pick")
    manager.set_pack_mod_version(pack.id, guid, "0.3.3", "v0.3.3")
    manager.set_mod_enabled(pack.id, guid, False)
    pinned = manager.set_pack_mod_version(pack.id, guid, "0.4.0", "v0.4.0")
    assert pinned.version == "0.4.0"
    assert not pinned.enabled
    plugin = manager.packs.plugins_dir(pack.id) / "Dizzy.Gamma" / "Dizzy.Gamma.dll"
    assert not plugin.exists()
    manager.close()


def test_list_remote_mod_versions_skips_unparsed_and_duplicates(paths: AppPaths) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    releases = [
        RemoteRelease(tag="v1.2.0", name="1.2.0", assets=[]),
        RemoteRelease(tag="v1.2.0", name="again", assets=[]),
        RemoteRelease(tag="nightly", name="nightly", assets=[]),
        RemoteRelease(tag="v1.0.0", name="old", assets=[]),
    ]
    with patch("sailwind_mod_sync.manager.list_releases", return_value=releases):
        rows = manager.list_remote_mod_versions("https://github.com/example/mod")
    assert rows == [("1.2.0", "v1.2.0"), ("1.0.0", "v1.0.0")]
    manager.close()


def test_list_remote_mod_versions_extracts_version_from_name(paths: AppPaths) -> None:
    """A release tagged with a literal word (e.g. "release") whose version
    only lives in the release name still appears in the version picker.
    """
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    releases = [
        RemoteRelease(tag="release", name="0.0.1", assets=[]),
        RemoteRelease(tag="v1.0.0", name="v1.0.0", assets=[]),
    ]
    with patch("sailwind_mod_sync.manager.list_releases", return_value=releases):
        rows = manager.list_remote_mod_versions("https://github.com/BryanP-JP19/SailwindSeaLifeMod")
    assert ("0.0.1", "0.0.1") in rows
    assert ("1.0.0", "v1.0.0") in rows
    manager.close()


def test_set_mod_repo_updates_pack_and_library(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    archive = _zip_with(tmp_path / "mod.zip", {"Mod/Mod.dll": b"MZ"})
    manager.library.ingest_mod_zip(
        "local.discord.mystery",
        "1.0.0",
        archive,
        version_raw="1.0.0",
        repo="",
        source_url="discord",
    )
    pack = manager.packs.create("Repo Pack")
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(guid="local.discord.mystery", version="1.0.0", repo=""),
    )
    page = manager.set_mod_repo(
        "local.discord.mystery",
        "https://github.com/example/mystery/releases/tag/v1.0.0",
    )
    assert page == "https://github.com/example/mystery"
    assert manager.packs.get(pack.id).find_mod("local.discord.mystery").repo == page
    meta = manager.library.read_mod_meta("local.discord.mystery", "1.0.0")
    assert meta is not None
    assert meta.repo == page
    manager.close()


def test_associate_mod_remaps_local_guid(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    archive = _zip_with(tmp_path / "mod.zip", {"StickyFix/StickyFix.dll": b"MZ"})
    manager.library.ingest_mod_zip(
        "local.stickyfix",
        "1.2.0",
        archive,
        version_raw="1.2.0",
        repo="",
        source_url="bepinex",
    )
    pack = manager.packs.create("Game")
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(guid="local.stickyfix", version="1.2.0", repo="", plugin_folders=["StickyFix"]),
    )
    entry = CatalogEntry(
        repo="https://github.com/NANDbrew/StickyFix",
        guids=["com.nandbrew.stickyfix"],
        primary_guid="com.nandbrew.stickyfix",
        name="StickyFix",
        latest_raw="v1.3.0",
        latest_version="1.3.0",
        available=True,
    )
    manager.catalog = [entry]
    new_guid = manager.associate_mod("local.stickyfix", "1.2.0", catalog_entry=entry)
    assert new_guid == "com.nandbrew.stickyfix"
    assert manager.library.has_mod("com.nandbrew.stickyfix", "1.2.0")
    assert not manager.library.has_mod("local.stickyfix", "1.2.0")
    pinned = manager.packs.get(pack.id).find_mod("com.nandbrew.stickyfix")
    assert pinned is not None
    assert pinned.repo == "https://github.com/NANDbrew/StickyFix"
    assert manager.packs.get(pack.id).find_mod("local.stickyfix") is None
    meta = manager.library.read_mod_meta("com.nandbrew.stickyfix", "1.2.0")
    assert meta is not None
    assert meta.guid == "com.nandbrew.stickyfix"
    assert meta.repo == "https://github.com/NANDbrew/StickyFix"
    manager.close()


def test_play_coop_pack_sets_dll_search_path(paths: AppPaths, tmp_path: Path) -> None:
    game = tmp_path / "Sailwind"
    game.mkdir()
    (game / "Sailwind.exe").write_bytes(b"MZ")
    manager = Manager(paths=paths, config=AppConfig(game_path=str(game)), http=_NoHttp())
    _ingest_bepinex(manager, tmp_path)
    overlay = _zip_with(
        tmp_path / "coop.zip",
        {
            "steam_api64.dll": b"steam-api",
            "winhttp.dll": b"doorstop",
            "BepInEx/plugins/SailwindCoop/SailwindCoop.dll": b"MZ",
            "BepInEx/plugins/SailwindCoop/Facepunch.Steamworks.Win64.dll": b"MZ",
        },
    )
    manager.library.ingest_mod_zip(
        COOP_GUID,
        "0.3.2",
        overlay,
        version_raw="v0.3.2",
        repo="https://github.com/DiamondMiner99/sailwind-coop",
        source_url="https://example/coop.zip",
    )
    pack = manager.packs.list_packs()[0]
    pack.bepinex = "5.4.2305"
    manager.packs.save(pack)
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(
            guid=COOP_GUID,
            version="0.3.2",
            repo="https://github.com/DiamondMiner99/sailwind-coop",
            plugin_folders=["SailwindCoop"],
        ),
    )
    with patch("sailwind_mod_sync.manager.launch_modded") as launch:
        launch.return_value = MagicMock()
        manager.play(pack.id)
    search = manager.packs.plugins_dir(pack.id) / "SailwindCoop"
    assert launch.call_args.kwargs["dll_search_path"] == search
    config = (game / "doorstop_config.ini").read_text(encoding="utf-8")
    assert f"dll_search_path_override = {search}" in config
    assert (search / "steam_api64.dll").exists()
    assert not (game / "steam_api64.dll").exists()
    manager.close()


def test_play_non_coop_pack_clears_dll_search_path(paths: AppPaths, tmp_path: Path) -> None:
    game = tmp_path / "Sailwind"
    game.mkdir()
    (game / "Sailwind.exe").write_bytes(b"MZ")
    manager = Manager(paths=paths, config=AppConfig(game_path=str(game)), http=_NoHttp())
    _ingest_bepinex(manager, tmp_path)
    mod_zip = _zip_with(tmp_path / "gamma.zip", {"Dizzy.Gamma/Dizzy.Gamma.dll": b"MZ"})
    manager.library.ingest_mod_zip(
        "com.dizzy.sailwind.gamma",
        "0.3.3",
        mod_zip,
        version_raw="v0.3.3",
        repo="https://github.com/foxyv/dizzy_sailwind_mods",
        source_url="https://example/gamma.zip",
    )
    pack = manager.packs.list_packs()[0]
    pack.bepinex = "5.4.2305"
    manager.packs.save(pack)
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(
            guid="com.dizzy.sailwind.gamma",
            version="0.3.3",
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
            plugin_folders=["Dizzy.Gamma"],
        ),
    )
    with patch("sailwind_mod_sync.manager.launch_modded") as launch:
        launch.return_value = MagicMock()
        manager.play(pack.id)
    assert launch.call_args.kwargs["dll_search_path"] is None
    config = (game / "doorstop_config.ini").read_text(encoding="utf-8")
    assert "dll_search_path_override = \n" in config
    manager.close()


def test_add_catalog_repo_is_kept_after_reload(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    from sailwind_mod_sync.catalog.mvc import find_entry, load_cached_catalog
    from sailwind_mod_sync.models import ReleaseAsset, RemoteRelease

    archive = tmp_path / "CoolMod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("CoolMod/CoolMod.dll", b"MZ" + b"\0" * 16 + b"com.example.coolmod\0")

    class _Http(_NoHttp):
        def download(self, url, dest, progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read_bytes())

    def fake_fetch(*args, **kwargs):
        return RemoteRelease(
            tag="v1.2.0",
            name="v1.2.0",
            assets=[ReleaseAsset("CoolMod.zip", "https://example/CoolMod.zip")],
        )

    monkeypatch.setattr("sailwind_mod_sync.manager.fetch_release", fake_fetch)
    manager = Manager(paths=paths, config=AppConfig(), http=_Http())
    entries = manager.add_catalog_repo("https://github.com/example/coolmod")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.custom
    assert entry.primary_guid == "com.example.coolmod"
    assert entry.latest_version == "1.2.0"
    assert entry.repo == "https://github.com/example/coolmod"
    assert entry.name == "CoolMod"
    manager.close()

    reloaded = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    found = find_entry(reloaded.catalog, "com.example.coolmod")
    assert found is not None
    assert found.custom
    assert found.latest_raw == "v1.2.0"
    cached = load_cached_catalog(paths)
    assert cached is not None
    assert find_entry(cached, "com.example.coolmod") is not None
    reloaded.remove_catalog_repo("com.example.coolmod")
    assert find_entry(reloaded.catalog, "com.example.coolmod") is None
    reloaded.close()


def test_hide_catalog_mod_persists(paths: AppPaths) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    manager.hide_catalog_mod("com.example.mod")
    manager.hide_catalog_mod("com.example.mod")
    assert manager.config.hidden_catalog_mods == ["com.example.mod"]
    manager.close()

    reloaded = Manager(paths=paths, config=load_config(paths), http=_NoHttp())
    assert reloaded.config.hidden_catalog_mods == ["com.example.mod"]
    reloaded.unhide_catalog_mod("com.example.mod")
    assert reloaded.config.hidden_catalog_mods == []
    reloaded.close()


def test_add_catalog_repo_uses_release_name_when_tag_is_not_a_version(
    paths: AppPaths, tmp_path: Path, monkeypatch
) -> None:
    """Release with a non-numeric tag (e.g. "release") and version in the name
    should still yield an available catalog entry. Regression: https://github.com/foxyv/SailwindModSynchronizer
    mod BryanP-JP19/SailwindSeaLifeMod was added as "Unavailable" because
    release.tag ("release") was used verbatim and parse_mod_version returned None.
    """
    from sailwind_mod_sync.models import ReleaseAsset, RemoteRelease

    archive = tmp_path / "SeaLifeMod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "SeaLifeMod/SeaLifeMod.dll",
            b"MZ" + b"\0" * 16 + b"com.yourname.sailwind.sealifeplugin\0",
        )

    class _Http(_NoHttp):
        def download(self, url, dest, progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read_bytes())

    def fake_fetch(*args, **kwargs):
        return RemoteRelease(
            tag="release",
            name="0.0.1",
            assets=[ReleaseAsset("SeaLifeMod.zip", "https://example/SeaLifeMod.zip")],
        )

    monkeypatch.setattr("sailwind_mod_sync.manager.fetch_release", fake_fetch)
    manager = Manager(paths=paths, config=AppConfig(), http=_Http())
    entries = manager.add_catalog_repo("https://github.com/BryanP-JP19/SailwindSeaLifeMod")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.primary_guid == "com.yourname.sailwind.sealifeplugin"
    assert entry.latest_raw == "0.0.1"
    assert entry.latest_version == "0.0.1"
    assert entry.available
    assert entry.repo == "https://github.com/BryanP-JP19/SailwindSeaLifeMod"
    manager.close()


def test_add_catalog_repo_splits_multi_plugin_release(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    from sailwind_mod_sync.models import ReleaseAsset, RemoteRelease

    archive = tmp_path / "ShatteredSeas.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "Shattered Seas Small/ShroudSmall.dll",
            b"MZ" + b"\0" * 16 + b"com.TheOriginOfAllEvil.riverSloop\0",
        )
        zf.writestr(
            "Shattered Seas Large/Clipper.dll",
            b"MZ" + b"\0" * 16 + b"com.TheOriginOfAllEvil.clipper\0",
        )

    class _Http(_NoHttp):
        def download(self, url, dest, progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read_bytes())

    def fake_fetch(*args, **kwargs):
        return RemoteRelease(
            tag="b1.2.8",
            name="b1.2.8",
            assets=[ReleaseAsset("ShatteredSeas.zip", "https://example/ShatteredSeas.zip")],
        )

    monkeypatch.setattr("sailwind_mod_sync.manager.fetch_release", fake_fetch)
    manager = Manager(paths=paths, config=AppConfig(), http=_Http())
    entries = manager.add_catalog_repo("https://github.com/TheOriginOfAllEvil/Shattered-Seas-Expansion")
    names = sorted(entry.name for entry in entries)
    assert names == ["Shattered Seas Large", "Shattered Seas Small"]
    by_name = {entry.name: entry for entry in entries}
    assert by_name["Shattered Seas Small"].primary_guid == "com.TheOriginOfAllEvil.riverSloop"
    assert by_name["Shattered Seas Small"].plugin_folders == ["Shattered Seas Small"]
    assert by_name["Shattered Seas Large"].primary_guid == "com.TheOriginOfAllEvil.clipper"
    assert by_name["Shattered Seas Large"].repo.endswith("Shattered-Seas-Expansion")
    manager.close()


def test_add_catalog_repo_generic_plugins_folder_uses_dll_stem(
    paths: AppPaths, tmp_path: Path, monkeypatch
) -> None:
    """Archive with a generic 'plugins' container folder gets the main DLL's
    name and folder recorded instead of 'plugins'.
    """
    from sailwind_mod_sync.models import ReleaseAsset, RemoteRelease

    archive = tmp_path / "SailwindModdingHelper.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "plugins/SailwindModdingHelper.dll",
            b"MZ" + b"\0" * 16 + b"com.app24.sailwindmoddinghelper\0",
        )

    class _Http(_NoHttp):
        def download(self, url, dest, progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read_bytes())

    def fake_fetch(*args, **kwargs):
        return RemoteRelease(
            tag="v2.1.1",
            name="v2.1.1",
            assets=[ReleaseAsset("SailwindModdingHelper.zip", "https://example/SailwindModdingHelper.zip")],
        )

    monkeypatch.setattr("sailwind_mod_sync.manager.fetch_release", fake_fetch)
    manager = Manager(paths=paths, config=AppConfig(), http=_Http())
    entries = manager.add_catalog_repo("https://github.com/AppSailwindMods/SailwindModdingHelper")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == "SailwindModdingHelper"
    assert entry.plugin_folders == ["SailwindModdingHelper"]
    manager.close()


def test_add_catalog_repo_rejects_mvc_duplicate(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    from sailwind_mod_sync.models import CatalogEntry, ReleaseAsset, RemoteRelease

    archive = tmp_path / "StickyFix.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("StickyFix/StickyFix.dll", b"MZ" + b"\0" * 16 + b"com.nandbrew.stickyfix\0")

    class _Http(_NoHttp):
        def download(self, url, dest, progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read_bytes())

    def fake_fetch(*args, **kwargs):
        return RemoteRelease(
            tag="v1.0.0",
            name="v1.0.0",
            assets=[ReleaseAsset("StickyFix.zip", "https://example/StickyFix.zip")],
        )

    monkeypatch.setattr("sailwind_mod_sync.manager.fetch_release", fake_fetch)
    manager = Manager(paths=paths, config=AppConfig(), http=_Http())
    manager.catalog = [
        CatalogEntry(
            repo="https://github.com/NANDbrew/StickyFix",
            guids=["com.nandbrew.stickyfix"],
            primary_guid="com.nandbrew.stickyfix",
            name="StickyFix",
            latest_raw="v1.0.0",
            latest_version="1.0.0",
            available=True,
        )
    ]
    try:
        manager.add_catalog_repo("https://github.com/NANDbrew/StickyFix")
        raise AssertionError("expected duplicate catalog plugin to fail")
    except ValueError as exc:
        message = str(exc)
        assert "already in the catalog" in message
        assert "as StickyFix" in message
    manager.close()


def test_add_catalog_repo_keeps_custom_island_api_off_better_ports(
    paths: AppPaths, tmp_path: Path, monkeypatch
) -> None:
    from sailwind_mod_sync.models import ReleaseAsset, RemoteRelease

    archive = tmp_path / "CustomIslandAPI.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "CustomIslandAPI/CustomIslandAPI.dll",
            b"MZ" + b"\0" * 16 + b"com.winter.customislandapi\0",
        )

    class _Http(_NoHttp):
        def download(self, url, dest, progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read_bytes())

    def fake_fetch(*args, **kwargs):
        return RemoteRelease(
            tag="v1.0.1",
            name="v1.0.1",
            assets=[ReleaseAsset("CustomIslandAPI.zip", "https://example/CustomIslandAPI.zip")],
        )

    monkeypatch.setattr("sailwind_mod_sync.manager.fetch_release", fake_fetch)
    manager = Manager(paths=paths, config=AppConfig(), http=_Http())
    manager.catalog = [
        CatalogEntry(
            repo="https://github.com/winterspices/CustomIslandAPI",
            guids=["com.winter.betterports"],
            primary_guid="com.winter.betterports",
            name="Better Ports",
            latest_raw="v1.0.1",
            latest_version="1.0.1",
            available=True,
        )
    ]
    entries = manager.add_catalog_repo("https://github.com/winterspices/CustomIslandAPI")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.primary_guid == "com.winter.customislandapi"
    assert entry.name == "CustomIslandAPI"
    assert entry.repo.endswith("/CustomIslandAPI")
    assert find_entry(manager.catalog, "com.winter.customislandapi") is not None
    manager.close()


def test_local_mod_details_lists_installed_versions(paths: AppPaths, tmp_path: Path) -> None:
    from sailwind_mod_sync.models import CatalogEntry

    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    for version in ("1.0.0", "1.1.0"):
        archive = _zip_with(tmp_path / f"{version}.zip", {"Mod/Mod.dll": version.encode()})
        manager.library.ingest_mod_zip(
            "com.example.mod",
            version,
            archive,
            version_raw=f"v{version}",
            repo="https://github.com/example/mod",
            source_url=f"https://example/{version}.zip",
        )
    manager.catalog = [
        CatalogEntry(
            repo="https://github.com/example/mod",
            guids=["com.example.mod"],
            primary_guid="com.example.mod",
            name="Example Mod",
            latest_raw="v1.2.0",
            latest_version="1.2.0",
            available=True,
        )
    ]
    pack = manager.packs.create("Crew")
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(guid="com.example.mod", version="1.1.0", repo="https://github.com/example/mod"),
    )
    details = manager.local_mod_details("com.example.mod", "1.1.0")
    assert details.name == "Example Mod"
    assert details.installed_versions == ["1.1.0", "1.0.0"]
    assert details.catalog_latest == "v1.2.0"
    assert details.pack_pins == [("Crew", "1.1.0")]
    manager.close()


def test_catalog_mod_details_without_download(paths: AppPaths) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    manager.catalog = [
        CatalogEntry(
            repo="https://github.com/example/mod",
            guids=["com.example.mod"],
            primary_guid="com.example.mod",
            name="Example Mod",
            latest_raw="v1.2.0",
            latest_version="1.2.0",
            available=True,
        )
    ]
    pack = manager.packs.create("Crew")
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(guid="com.example.mod", version="1.0.0", repo="https://github.com/example/mod"),
    )
    details = manager.catalog_mod_details("com.example.mod")
    assert details.name == "Example Mod"
    assert details.version == "1.2.0"
    assert details.repo == "https://github.com/example/mod"
    assert details.catalog_latest == "v1.2.0"
    assert details.installed_versions == []
    assert details.pack_pins == [("Crew", "1.0.0")]
    manager.close()


def test_catalog_mod_details_uses_downloaded_copy(paths: AppPaths, tmp_path: Path) -> None:
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    archive = _zip_with(tmp_path / "mod.zip", {"Mod/Mod.dll": b"MZ"})
    manager.library.ingest_mod_zip(
        "com.example.mod",
        "1.1.0",
        archive,
        version_raw="v1.1.0",
        repo="https://github.com/example/mod",
        source_url="https://example/mod.zip",
    )
    manager.catalog = [
        CatalogEntry(
            repo="https://github.com/example/mod",
            guids=["com.example.mod"],
            primary_guid="com.example.mod",
            name="Example Mod",
            latest_raw="v1.2.0",
            latest_version="1.2.0",
            available=True,
        )
    ]
    details = manager.catalog_mod_details("com.example.mod")
    assert details.version == "1.1.0"
    assert details.installed_versions == ["1.1.0"]
    assert details.catalog_latest == "v1.2.0"
    assert details.filename
    manager.close()


def test_set_mod_alias_persists_and_overrides_name(paths: AppPaths, tmp_path: Path) -> None:
    from sailwind_mod_sync.library.aliases import load_aliases
    from sailwind_mod_sync.models import CatalogEntry

    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    archive = _zip_with(tmp_path / "gamma.zip", {"Dizzy.Gamma/Dizzy.Gamma.dll": b"MZ"})
    manager.library.ingest_mod_zip(
        "com.dizzy.sailwind.gamma",
        "0.3.3",
        archive,
        version_raw="v0.3.3",
        repo="https://github.com/foxyv/dizzy_sailwind_mods",
        source_url="https://example/gamma.zip",
    )
    manager.catalog = [
        CatalogEntry(
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
            guids=["com.dizzy.sailwind.gamma", "com.dizzy.sailwind.calendar"],
            primary_guid="com.dizzy.sailwind.gamma",
            name="dizzy_sailwind_mods",
            latest_raw="v0.3.3",
            latest_version="0.3.3",
            available=True,
        )
    ]
    assert manager.mod_display_name("com.dizzy.sailwind.gamma") == "Dizzy.Gamma"
    shown = manager.set_mod_alias("com.dizzy.sailwind.gamma", "Dizzy Gamma")
    assert shown == "Dizzy Gamma"
    assert load_aliases(paths)["com.dizzy.sailwind.gamma"] == "Dizzy Gamma"
    details = manager.local_mod_details("com.dizzy.sailwind.gamma", "0.3.3")
    assert details.name == "Dizzy Gamma"
    cleared = manager.set_mod_alias("com.dizzy.sailwind.gamma", "")
    assert cleared == "Dizzy.Gamma"
    assert "com.dizzy.sailwind.gamma" not in load_aliases(paths)
    manager.close()


def _catalog_with_repo() -> list[CatalogEntry]:
    return [
        CatalogEntry(
            repo="https://github.com/example/mymod",
            guids=["com.example.mymod"],
            primary_guid="com.example.mymod",
            name="MyMod",
            latest_raw="v1.0.0",
            latest_version="1.0.0",
            available=True,
        )
    ]


def test_scan_updates_raises_token_auth_error_on_401_with_token(
    paths: AppPaths, monkeypatch
) -> None:
    def bad_fetch(*args, **kwargs):
        raise HttpError("HTTP 401 for https://api.github.com", status_code=401)

    monkeypatch.setattr("sailwind_mod_sync.manager.fetch_release", bad_fetch)
    manager = Manager(
        paths=paths,
        config=AppConfig(github_token="ghp_fake"),
        http=_NoHttp(),
    )
    manager.catalog = _catalog_with_repo()
    try:
        with pytest.raises(TokenAuthError):
            manager.scan_updates(live=True)
    finally:
        manager.close()


def test_scan_updates_without_token_ignores_401(paths: AppPaths, monkeypatch) -> None:
    def bad_fetch(*args, **kwargs):
        raise HttpError("HTTP 401 for https://api.github.com", status_code=401)

    monkeypatch.setattr("sailwind_mod_sync.manager.fetch_release", bad_fetch)
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    manager.catalog = _catalog_with_repo()
    try:
        latest = manager.scan_updates(live=True)
    finally:
        manager.close()
    assert latest["com.example.mymod"] == "v1.0.0"


def _custom_fork_entry() -> CatalogEntry:
    return CatalogEntry(
        repo="https://github.com/me/mymod-fork",
        guids=["com.example.mymod"],
        primary_guid="com.example.mymod",
        name="MyMod fork",
        latest_raw="v2.0.0",
        latest_version="2.0.0",
        available=True,
        custom=True,
    )


def test_scan_updates_keeps_custom_entries_the_shared_catalog_covers(paths: AppPaths, monkeypatch) -> None:
    from sailwind_mod_sync.catalog.custom import load_custom_catalog, merge_with_custom, save_custom_catalog

    monkeypatch.setattr(
        "sailwind_mod_sync.manager.fetch_release",
        lambda *args, **kwargs: RemoteRelease(tag="v1.1.0", name="v1.1.0", assets=[]),
    )
    other = CatalogEntry(
        repo="https://github.com/me/other",
        guids=["com.me.other"],
        primary_guid="com.me.other",
        name="Other",
        latest_raw="v1.0.0",
        latest_version="1.0.0",
        available=True,
        custom=True,
    )
    save_custom_catalog(paths, [_custom_fork_entry(), other])
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    manager.catalog = merge_with_custom(_catalog_with_repo(), load_custom_catalog(paths))
    try:
        manager.scan_updates(live=True)
    finally:
        manager.close()

    stored = load_custom_catalog(paths)
    fork = next(entry for entry in stored if entry.primary_guid == "com.example.mymod")
    assert fork.repo == "https://github.com/me/mymod-fork"
    assert fork.latest_raw == "v2.0.0"
    assert len(stored) == 2


def test_scan_updates_stores_live_versions_of_custom_entries(paths: AppPaths, monkeypatch) -> None:
    from sailwind_mod_sync.catalog.custom import load_custom_catalog, merge_with_custom, save_custom_catalog

    monkeypatch.setattr(
        "sailwind_mod_sync.manager.fetch_release",
        lambda *args, **kwargs: RemoteRelease(tag="v2.1.0", name="v2.1.0", assets=[]),
    )
    save_custom_catalog(paths, [_custom_fork_entry()])
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    manager.catalog = merge_with_custom([], load_custom_catalog(paths))
    try:
        manager.scan_updates(live=True)
    finally:
        manager.close()

    stored = load_custom_catalog(paths)
    assert stored[0].latest_raw == "v2.1.0"
    assert stored[0].latest_version == "2.1.0"
