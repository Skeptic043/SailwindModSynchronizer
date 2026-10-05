from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.models import PinnedMod
from sailwind_mod_sync.packs.modpack import PackStore
from sailwind_mod_sync.paths import AppPaths


def test_create_export_import_json(paths: AppPaths) -> None:
    store = PackStore(paths)
    pack = store.create("Dizzy Sailing")
    store.upsert_mod(
        pack.id,
        PinnedMod(
            guid="com.dizzy.sailwind.gamma",
            version="0.3.3",
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
        ),
    )
    dest = paths.root / "export.json"
    store.export_json(pack.id, dest)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["name"] == "Dizzy Sailing"
    assert payload["mods"][0]["guid"] == "com.dizzy.sailwind.gamma"
    imported = store.import_file(dest, LibraryStore(paths))
    assert imported.id != pack.id
    assert imported.name == "Dizzy Sailing"
    assert imported.mods[0].version == "0.3.3"


def test_export_import_bundle(paths: AppPaths, tmp_path: Path) -> None:
    library = LibraryStore(paths)
    archive = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Dizzy.Gamma/Dizzy.Gamma.dll", b"MZ")
    library.ingest_mod_zip(
        "com.dizzy.sailwind.gamma",
        "0.3.3",
        archive,
        version_raw="v0.3.3",
        repo="https://github.com/foxyv/dizzy_sailwind_mods",
        source_url="https://example/gamma.zip",
        filename="Dizzy.Gamma-0.3.3.zip",
    )
    packs = PackStore(paths)
    pack = packs.create("Bundle Pack")
    packs.upsert_mod(
        pack.id,
        PinnedMod(guid="com.dizzy.sailwind.gamma", version="0.3.3", repo="https://github.com/foxyv/dizzy_sailwind_mods"),
    )
    dest = tmp_path / "pack.zip"
    packs.export_bundle(pack.id, dest, library)
    packs.delete(pack.id)
    library.delete_mod("com.dizzy.sailwind.gamma", "0.3.3")
    imported = packs.import_file(dest, library)
    assert imported.mods[0].guid == "com.dizzy.sailwind.gamma"
    assert library.has_mod("com.dizzy.sailwind.gamma", "0.3.3")


def test_bundle_carries_bepinex_configs_and_extracted_only_mods(paths: AppPaths, tmp_path: Path) -> None:
    library = LibraryStore(paths)
    bx_archive = tmp_path / "bx.zip"
    with zipfile.ZipFile(bx_archive, "w") as zf:
        zf.writestr("BepInExPack/BepInEx/core/BepInEx.Preloader.dll", b"MZ")
        zf.writestr("BepInExPack/winhttp.dll", b"MZ")
    library.ingest_bepinex_zip("5.4.2100", bx_archive)
    plugin = tmp_path / "Local.Mod"
    plugin.mkdir()
    (plugin / "Local.Mod.dll").write_bytes(b"MZ")
    library.ingest_plugin_paths("local.mod", "1.0.0", [plugin], version_raw="1.0.0", repo="", source_url="")
    for stray in library.mod_dir("local.mod", "1.0.0").glob("*.zip"):
        stray.unlink()
    packs = PackStore(paths)
    pack = packs.create("Offline", bepinex="5.4.2100")
    packs.upsert_mod(pack.id, PinnedMod(guid="local.mod", version="1.0.0"))
    config = packs.instance_dir(pack.id) / "BepInEx" / "config" / "local.mod.cfg"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("speed = 3", encoding="utf-8")
    dest = tmp_path / "offline.zip"
    messages: list[str] = []
    packs.export_bundle(pack.id, dest, library, include_context=True, progress=messages.append)
    assert any("BepInEx 5.4.2100" in m for m in messages)
    assert any("(1/1)" in m for m in messages)
    assert any("local.mod.cfg" in m for m in messages)
    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
    assert "bepinex/BepInExPack-5.4.2100.zip" in names
    assert "config/local.mod.cfg" in names

    packs.delete(pack.id)
    library.delete_mod("local.mod", "1.0.0")
    shutil.rmtree(library.bepinex_dir("5.4.2100"))
    imported = packs.import_file(dest, library)
    assert library.has_bepinex("5.4.2100")
    assert library.has_mod("local.mod", "1.0.0")
    restored = packs.instance_dir(imported.id) / "BepInEx" / "config" / "local.mod.cfg"
    assert restored.read_text(encoding="utf-8") == "speed = 3"


def test_bundle_import_ignores_config_path_traversal(paths: AppPaths, tmp_path: Path) -> None:
    dest = tmp_path / "evil.zip"
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("modpack.json", json.dumps({"name": "Evil", "mods": []}))
        zf.writestr("config/../../escaped.cfg", "x")
    packs = PackStore(paths)
    imported = packs.import_file(dest, LibraryStore(paths))
    assert not (packs.instance_dir(imported.id) / "escaped.cfg").exists()
    assert not (packs.pack_dir(imported.id) / "escaped.cfg").exists()


def test_duplicate_and_delete(paths: AppPaths) -> None:
    store = PackStore(paths)
    original = store.create("Alpha")
    copy = store.duplicate(original.id, "Alpha Two")
    assert copy.id != original.id
    assert copy.name == "Alpha Two"
    store.delete(original.id)
    ids = {pack.id for pack in store.list_packs()}
    assert original.id not in ids
    assert copy.id in ids


def test_duplicate_copies_config_and_skips_logs(paths: AppPaths) -> None:
    store = PackStore(paths)
    original = store.create("Crew")
    instance = store.instance_dir(original.id)
    config = instance / "BepInEx" / "config" / "mod.cfg"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("enabled = true", encoding="utf-8")
    log_file = instance / "BepInEx" / "LogOutput.log"
    log_file.write_text("busy log", encoding="utf-8")
    copy = store.duplicate(original.id, "Crew copy")
    dest = store.instance_dir(copy.id)
    assert (dest / "BepInEx" / "config" / "mod.cfg").read_text(encoding="utf-8") == "enabled = true"
    assert not (dest / "BepInEx" / "LogOutput.log").exists()
    assert {pack.name for pack in store.list_packs()} == {"Crew", "Crew copy"}


def test_duplicate_keeps_pack_when_instance_copy_fails(paths: AppPaths, monkeypatch) -> None:
    import shutil

    store = PackStore(paths)
    original = store.create("Crew")
    (store.instance_dir(original.id) / "BepInEx" / "config").mkdir(parents=True, exist_ok=True)

    def boom(*_args, **_kwargs):
        raise PermissionError("LogOutput.log is locked")

    monkeypatch.setattr(shutil, "copytree", boom)
    copy = store.duplicate(original.id, "Crew copy")
    assert copy.name == "Crew copy"
    assert {pack.name for pack in store.list_packs()} == {"Crew", "Crew copy"}


def test_rename_keeps_id(paths: AppPaths) -> None:
    store = PackStore(paths)
    pack = store.create("Old Name")
    pack_id = pack.id
    renamed = store.rename(pack_id, "New Name")
    assert renamed.id == pack_id
    assert renamed.name == "New Name"
    assert store.get(pack_id).name == "New Name"
