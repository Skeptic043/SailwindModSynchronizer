from __future__ import annotations

import zipfile
from pathlib import Path

from sailwind_mod_sync.config import AppConfig
from sailwind_mod_sync.game.scan_plugins import discover_local_file
from sailwind_mod_sync.http_util import HttpClient
from sailwind_mod_sync.manager import Manager
from sailwind_mod_sync.models import PinnedMod
from sailwind_mod_sync.paths import AppPaths


class _NoHttp(HttpClient):
    def __init__(self) -> None:
        self.token = ""
        self._owns_client = False
        self._client = None

    def close(self) -> None:
        return None


def _dll_bytes(guid: str = "com.nandbrew.savecleaner", version: str = "1.2.3") -> bytes:
    return b"MZ" + b"\0" * 32 + guid.encode("ascii") + b"\0" + version.encode("ascii") + b"\0" * 16


def test_discover_local_dll_reads_guid_and_version(tmp_path: Path) -> None:
    dll = tmp_path / "SaveCleaner.dll"
    dll.write_bytes(_dll_bytes())
    found = discover_local_file(dll)
    assert len(found) == 1
    assert found[0].guid == "com.nandbrew.savecleaner"
    assert found[0].version == "1.2.3"


def test_discover_local_zip(tmp_path: Path) -> None:
    archive = tmp_path / "CoolMod-0.4.0.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("CoolMod/CoolMod.dll", _dll_bytes("com.example.coolmod", "0.4.0"))
    found = discover_local_file(archive)
    assert found[0].guid == "com.example.coolmod"
    assert found[0].version == "0.4.0"


def test_discover_rejects_non_plugin(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    try:
        discover_local_file(txt)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Unsupported" in str(exc)


def test_manager_imports_dll_into_pack(paths: AppPaths, tmp_path: Path) -> None:
    dll = tmp_path / "SaveCleaner-1.1.1.dll"
    dll.write_bytes(_dll_bytes("com.nandbrew.savecleaner", "1.1.1"))
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    pack = manager.packs.create("Local")
    pinned = manager.import_local_mod(dll, pack.id)
    assert pinned.guid == "com.nandbrew.savecleaner"
    assert pinned.version == "1.1.1"
    assert manager.library.has_mod(pinned.guid, pinned.version)
    plugin = manager.packs.plugins_dir(pack.id) / "SaveCleaner" / "SaveCleaner-1.1.1.dll"
    assert plugin.exists() or (manager.packs.plugins_dir(pack.id) / "SaveCleaner-1.1.1" / "SaveCleaner-1.1.1.dll").exists()


def test_import_local_fills_missing_pin(paths: AppPaths, tmp_path: Path) -> None:
    dll = tmp_path / "Mystery.dll"
    dll.write_bytes(_dll_bytes("com.nandbrew.savecleaner", "1.1.1"))
    manager = Manager(paths=paths, config=AppConfig(), http=_NoHttp())
    pack = manager.packs.create("Local")
    manager.packs.upsert_mod(
        pack.id,
        PinnedMod(guid="local.discord.mystery", version="1.0.0", repo=""),
    )
    assert manager.missing_mods(manager.packs.get(pack.id))
    pinned = manager.import_local_mod(dll, pack.id, guid="local.discord.mystery")
    assert pinned.guid == "local.discord.mystery"
    assert pinned.version == "1.1.1"
    assert not manager.missing_mods(manager.packs.get(pack.id))
    manager.close()


def test_local_zip_keeps_prerelease_from_file_name(tmp_path: Path) -> None:
    archive = tmp_path / "BetterDrag-1.4.1-beta.11.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("BetterDrag/BetterDrag.dll", _dll_bytes("com.AugSphere.BetterDrag", "1.4.1"))
    found = discover_local_file(archive)
    assert found[0].version_raw == "1.4.1-beta.11"


def test_declared_plugin_keeps_prerelease_from_file_name(tmp_path: Path) -> None:
    from plugin_metadata_fixture import managed_dll

    # Real plugins declare their version in BepInPlugin metadata, which takes a different path.
    archive = tmp_path / "BetterDrag-1.4.1-beta.11.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("BetterDrag/BetterDrag.dll", managed_dll((("com.AugSphere.BetterDrag", "BetterDrag", "1.4.1.0"),)))
    found = discover_local_file(archive)
    assert (found[0].guid, found[0].version_raw) == ("com.AugSphere.BetterDrag", "1.4.1-beta.11")


def test_declared_plugin_keeps_its_version_when_the_file_name_differs(tmp_path: Path) -> None:
    from plugin_metadata_fixture import managed_dll

    archive = tmp_path / "BetterDrag-1.5.0-beta.1.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("BetterDrag/BetterDrag.dll", managed_dll((("com.AugSphere.BetterDrag", "BetterDrag", "1.4.1"),)))
    assert discover_local_file(archive)[0].version_raw == "1.4.1"


def test_local_zip_ignores_non_version_words_in_file_name(tmp_path: Path) -> None:
    archive = tmp_path / "CustomPaintings-0.1.0-thunderstore-20261106-164336521.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("CustomPaintings/CustomPaintings.dll", _dll_bytes("com.example.paintings", "0.1.0"))
    found = discover_local_file(archive)
    assert found[0].version == "0.1.0"


def test_import_summary_names_replaced_version() -> None:
    from sailwind_mod_sync.ui.main_window import _import_summary

    pinned = PinnedMod(guid="com.AugSphere.BetterDrag", version="1.4.1-beta11", version_raw="1.4.1-beta.11")
    assert _import_summary(pinned, None) == "Added com.AugSphere.BetterDrag 1.4.1-beta.11"
    assert _import_summary(pinned, "1.4.1-beta.11") == "Reinstalled com.AugSphere.BetterDrag 1.4.1-beta.11"
    assert _import_summary(pinned, "1.4.1-beta.10") == (
        "Replaced com.AugSphere.BetterDrag 1.4.1-beta.10 with 1.4.1-beta.11"
    )
