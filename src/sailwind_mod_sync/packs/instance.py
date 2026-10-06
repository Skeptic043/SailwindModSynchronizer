from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.models import ModPack, PinnedMod

log = logging.getLogger(__name__)


def ensure_instance_bepinex(instance_dir: Path, bepinex_extracted: Path) -> Path:
    log.info("Copying BepInEx core into %s", instance_dir)
    src = bepinex_extracted / "BepInEx"
    if not src.exists():
        raise FileNotFoundError(f"BepInEx core missing at {src}")
    dst = instance_dir / "BepInEx"
    for sub in ("core", "patchers"):
        src_sub = src / sub
        if not src_sub.exists():
            continue
        dst_sub = dst / sub
        if dst_sub.exists():
            shutil.rmtree(dst_sub)
        shutil.copytree(src_sub, dst_sub)
    (dst / "plugins").mkdir(parents=True, exist_ok=True)
    (dst / "config").mkdir(parents=True, exist_ok=True)
    for extra in ("unity-libs", "cache"):
        src_extra = src / extra
        if src_extra.exists() and not (dst / extra).exists():
            shutil.copytree(src_extra, dst / extra)
    preloader = dst / "core" / "BepInEx.Preloader.dll"
    if not preloader.exists():
        raise FileNotFoundError(f"BepInEx.Preloader.dll missing in {dst / 'core'}")
    log.info("BepInEx instance ready at %s", preloader)
    return preloader


def sync_pack_plugins(pack: ModPack, plugins_dir: Path, store: LibraryStore) -> None:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    for pinned in pack.mods:
        if not pinned.enabled:
            remove_plugin_folders(plugins_dir, pinned.plugin_folders)
            continue
        key = store.pinned_key(pinned)
        if not store.has_mod(pinned.guid, key):
            continue
        extracted = store.mod_extracted(pinned.guid, key)
        folders = pinned.plugin_folders or [p.name for p in extracted.iterdir() if p.is_dir()]
        for folder in folders:
            src = extracted / folder
            dst = plugins_dir / folder
            if not src.exists():
                continue
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)


def install_pinned_into_plugins(pinned: PinnedMod, extracted: Path, plugins_dir: Path) -> list[str]:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    remove_plugin_folders(plugins_dir, pinned.plugin_folders)
    folders = [p.name for p in extracted.iterdir() if p.is_dir()]
    for folder in folders:
        src = extracted / folder
        dst = plugins_dir / folder
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    return folders


def remove_plugin_folders(plugins_dir: Path, folders: list[str]) -> None:
    if not plugins_dir.exists():
        return
    for folder in folders:
        target = plugins_dir / folder
        if target.exists():
            shutil.rmtree(target)
