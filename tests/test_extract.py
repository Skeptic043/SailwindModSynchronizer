from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from sailwind_mod_sync.library.extract import ExtractError, extract_bepinex_pack, normalize_plugin_archive


def _dll_bytes() -> bytes:
    return b"MZ" + b"\0" * 64


def _write_zip(path: Path, mapping: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in mapping.items():
            zf.writestr(name, data)
    return path


def test_normalize_named_plugin_folder(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "gamma.zip",
        {"Dizzy.Gamma/Dizzy.Gamma.dll": _dll_bytes()},
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest)
    assert folders == ["Dizzy.Gamma"]
    assert (dest / "Dizzy.Gamma" / "Dizzy.Gamma.dll").exists()


def test_normalize_keeps_only_requested_plugin_folder(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "seas.zip",
        {
            "Shattered Seas Small/Small.dll": _dll_bytes(),
            "Shattered Seas Large/Large.dll": _dll_bytes(),
        },
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest, keep_folders=["Shattered Seas Small"])
    assert folders == ["Shattered Seas Small"]
    assert (dest / "Shattered Seas Small" / "Small.dll").exists()
    assert not (dest / "Shattered Seas Large").exists()


def test_normalize_version_wrapper_folder(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "mvc.zip",
        {"ModVersionChecker-1.3.3/ModVersionChecker/ModVersionChecker.dll": _dll_bytes()},
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest)
    assert folders == ["ModVersionChecker"]
    assert (dest / "ModVersionChecker" / "ModVersionChecker.dll").exists()


def test_normalize_bepinex_plugins_layout(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "nested.zip",
        {"BepInEx/plugins/CoolMod/CoolMod.dll": _dll_bytes()},
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest)
    assert folders == ["CoolMod"]


def test_normalize_loose_dll(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "loose.zip", {"Widget.dll": _dll_bytes()})
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest)
    assert folders == ["Widget"]
    assert (dest / "Widget" / "Widget.dll").exists()


def test_normalize_loose_dll_keeps_asset_folders(tmp_path: Path) -> None:
    """Root DLL + texture folders → textures stay inside the DLL's plugin folder."""
    archive = _write_zip(
        tmp_path / "paint.zip",
        {
            "CustomBrigPaint.dll": _dll_bytes(),
            "Continental/Continental.png": b"png",
            "Common/piece_map.png": b"png",
        },
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest, keep_folders=["CustomBrigPaint"])
    assert folders == ["CustomBrigPaint"]
    plugin = dest / "CustomBrigPaint"
    assert (plugin / "CustomBrigPaint.dll").exists()
    assert (plugin / "Continental" / "Continental.png").exists()
    assert (plugin / "Common" / "piece_map.png").exists()
    assert not (dest / "Continental").exists()


def test_normalize_rejects_empty_zip(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "empty.zip", {"readme.txt": b"hi"})
    with pytest.raises(ExtractError):
        normalize_plugin_archive(archive, tmp_path / "out")


def test_normalize_renames_generic_plugins_folder(tmp_path: Path) -> None:
    """plugins/SomeMod.dll → SomeMod/SomeMod.dll (generic folder renamed via main DLL stem)."""
    archive = _write_zip(
        tmp_path / "helper.zip",
        {"plugins/SailwindModdingHelper.dll": _dll_bytes()},
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest)
    assert folders == ["SailwindModdingHelper"]
    assert (dest / "SailwindModdingHelper" / "SailwindModdingHelper.dll").exists()
    assert not (dest / "plugins").exists()


def test_normalize_renames_generic_folder_to_expected(tmp_path: Path) -> None:
    """Caller's expected folder name is used when a generic folder is extracted."""
    archive = _write_zip(
        tmp_path / "m.zip",
        {"plugins/Whatever.dll": _dll_bytes()},
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest, keep_folders=["ExpectedMod"])
    assert folders == ["ExpectedMod"]
    assert (dest / "ExpectedMod" / "Whatever.dll").exists()
    assert not (dest / "plugins").exists()


def test_extract_bepinex_pack(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "bx.zip",
        {
            "BepInExPack/winhttp.dll": b"dll",
            "BepInExPack/doorstop_config.ini": b"[General]\n",
            "BepInExPack/BepInEx/core/BepInEx.Preloader.dll": _dll_bytes(),
            "BepInExPack/changelog.txt": b"notes",
        },
    )
    dest = tmp_path / "bx"
    extract_bepinex_pack(archive, dest)
    assert (dest / "winhttp.dll").exists()
    assert (dest / "BepInEx" / "core" / "BepInEx.Preloader.dll").exists()


def test_normalize_coop_overlay_zip_copies_steam_api(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "SailwindCoop-v0.3.2.zip",
        {
            "steam_api64.dll": b"steam-api",
            "winhttp.dll": b"doorstop",
            "doorstop_config.ini": b"[General]\n",
            "INSTALL.txt": b"extract over Sailwind",
            "BepInEx/core/BepInEx.Preloader.dll": _dll_bytes(),
            "BepInEx/plugins/SailwindCoop/SailwindCoop.dll": _dll_bytes(),
            "BepInEx/plugins/SailwindCoop/Facepunch.Steamworks.Win64.dll": _dll_bytes(),
        },
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest)
    assert folders == ["SailwindCoop"]
    plugin = dest / "SailwindCoop"
    assert (plugin / "SailwindCoop.dll").exists()
    assert (plugin / "Facepunch.Steamworks.Win64.dll").exists()
    assert (plugin / "steam_api64.dll").read_bytes() == b"steam-api"
    assert not (dest / "winhttp.dll").exists()
    assert not any(dest.rglob("BepInEx.Preloader.dll"))
    assert not any(path.name == "winhttp.dll" for path in dest.rglob("*"))


def test_normalize_coop_thunderstore_zip_keeps_steam_api(tmp_path: Path) -> None:
    archive = _write_zip(
        tmp_path / "SailwindCoop.zip",
        {
            "manifest.json": b"{}",
            "BepInEx/plugins/SailwindCoop/SailwindCoop.dll": _dll_bytes(),
            "BepInEx/plugins/SailwindCoop/Facepunch.Steamworks.Win64.dll": _dll_bytes(),
            "BepInEx/plugins/SailwindCoop/steam_api64.dll": b"steam-api",
        },
    )
    dest = tmp_path / "out"
    folders = normalize_plugin_archive(archive, dest)
    assert folders == ["SailwindCoop"]
    plugin = dest / "SailwindCoop"
    assert (plugin / "steam_api64.dll").read_bytes() == b"steam-api"
    assert (plugin / "Facepunch.Steamworks.Win64.dll").exists()
