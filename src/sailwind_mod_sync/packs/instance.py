from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.models import ModPack, PinnedMod
from sailwind_mod_sync.packs.plugin_files import (
    ConflictResolver,
    FilePlan,
    apply_file_plan,
    ask_conflicts,
    baseline_from_package,
    load_baselines,
    package_paths,
    plan_mod_files,
    save_records,
)

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


def sync_pack_plugins(
    pack: ModPack, plugins_dir: Path, store: LibraryStore, pack_dir: Path,
    resolve_conflicts: ConflictResolver | None = None,
) -> None:
    """Bring each mod's folders to its pinned package, keeping files players and mods changed."""
    plugins_dir.mkdir(parents=True, exist_ok=True)
    baselines = load_baselines(pack_dir)
    mods = []
    claims: dict[str, set[str]] = {}
    for pinned in pack.mods:
        key = store.pinned_key(pinned)
        available = store.has_mod(pinned.guid, key)
        extracted = store.mod_extracted(pinned.guid, key)
        folders = pinned.plugin_folders
        if pinned.enabled and available:
            folders = pinned.plugin_folders or [p.name for p in extracted.iterdir() if p.is_dir()]
            folders = [folder for folder in folders if (extracted / folder).exists()]
        # A pin can also own folders it keeps only for players' files; inspect those too.
        owned = pinned.plugin_folders or folders
        mods.append((pinned, key, available, extracted, folders, owned))
        # Mods can share a folder; each one only manages the files its package or record names.
        recorded = baselines.get(pinned.guid)
        claims[pinned.guid] = (package_paths(extracted, folders) if available else set()) | {
            relpath.casefold() for relpath in (recorded.files if recorded is not None else ())
        }
    plans: list[tuple[FilePlan, str | None]] = []
    for pinned, key, available, extracted, folders, owned in mods:
        claimed = set().union(*(paths for guid, paths in claims.items() if guid != pinned.guid))
        baseline = baselines.get(pinned.guid)
        if not pinned.enabled:
            # BepInEx loads only DLLs, so edited and runtime files can stay for re-enabling.
            if baseline is None and available:
                baseline = baseline_from_package(extracted, folders)
            plans.append((plan_mod_files(pinned.guid, None, [], plugins_dir, folders, baseline, claimed), None))
            continue
        if not available:
            continue
        # Packs prepared before the record existed were reset to the package on every launch.
        baseline = baseline or baseline_from_package(extracted, folders)
        plans.append((plan_mod_files(pinned.guid, extracted, folders, plugins_dir, owned, baseline, claimed), key))
    use_new = ask_conflicts([plan for plan, _ in plans], resolve_conflicts)
    for plan, _ in plans:
        apply_file_plan(plan, use_new, plugins_dir)
    save_records(pack_dir, [(plan, key) for plan, key in plans if key is not None],
                 asked=resolve_conflicts is not None)


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
