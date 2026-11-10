from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

from sailwind_mod_sync.game.plugin_metadata import read_plugin_metadata
from sailwind_mod_sync.library.main_dll import is_generic_folder_name, pick_main_dll
from sailwind_mod_sync.library.special_mods import COOP_PLUGIN_FOLDER, STEAM_API_DLL

JUNK_NAMES = {".ds_store", "thumbs.db"}
JUNK_DIRS = {"__macosx"}

# The temporary folder an archive is extracted into; its name says nothing about the mod.
_EXTRACT_ROOT = "extracted"

log = logging.getLogger(__name__)


class ExtractError(ValueError):
    pass


def normalize_plugin_archive(
    archive: Path,
    dest: Path,
    keep_folders: list[str] | None = None,
) -> list[str]:
    """Extract a mod zip into dest as plugin folders ready for BepInEx/plugins."""
    dest.mkdir(parents=True, exist_ok=True)
    _clear_dir(dest)
    with tempfile.TemporaryDirectory(prefix="sms-mod-") as tmp:
        root = Path(tmp) / _EXTRACT_ROOT
        root.mkdir()
        _safe_extract(archive, root)
        _strip_junk(root)
        overlay_root = _unwrap_single_root(root)
        source = overlay_root
        plugins = _find_bepinex_plugins(overlay_root)
        if plugins is not None:
            source = plugins
            _log_left_out(archive, overlay_root, plugins)
        folders = _copy_plugin_contents(source, dest, archive)
        folders = _rename_generic_folders(dest, folders, keep_folders)
        _copy_overlay_steam_api(overlay_root, dest, folders)
        return _retain_folders(dest, folders, keep_folders)


def extract_bepinex_pack(archive: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    _clear_dir(dest)
    with tempfile.TemporaryDirectory(prefix="sms-bx-") as tmp:
        root = Path(tmp) / "extracted"
        root.mkdir()
        _safe_extract(archive, root)
        _strip_junk(root)
        pack_root = _find_bepinex_root(root)
        for item in pack_root.iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    return dest


def safe_extract_zip(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    _safe_extract(archive, dest)


def _safe_extract(archive: Path, dest: Path) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest)):
                raise ExtractError(f"Zip slip blocked: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)


def _strip_junk(root: Path) -> None:
    for path in list(root.rglob("*")):
        if path.is_dir() and path.name.lower() in JUNK_DIRS:
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file() and path.name.lower() in JUNK_NAMES:
            path.unlink(missing_ok=True)


def _unwrap_single_root(root: Path) -> Path:
    current = root
    while True:
        children = _visible_children(current)
        if len(children) != 1 or not children[0].is_dir():
            return current
        inner = children[0]
        immediate_dlls = [p for p in inner.iterdir() if p.is_file() and p.suffix.lower() == ".dll"]
        if immediate_dlls:
            return current
        current = inner


def _find_bepinex_plugins(root: Path) -> Path | None:
    for path in [root, *root.rglob("*")]:
        if not path.is_dir():
            continue
        if path.name.lower() == "plugins" and path.parent.name.lower() == "bepinex":
            return path
    return None


def _log_left_out(archive: Path, overlay_root: Path, plugins: Path) -> None:
    """Log files of a BepInEx overlay that are outside ``BepInEx/plugins`` and so are not installed."""
    installed_steam_api = _find_overlay_steam_api(overlay_root)
    left = sorted(
        path.relative_to(overlay_root).as_posix()
        for path in overlay_root.rglob("*")
        if path.is_file() and plugins not in path.parents and path != installed_steam_api
    )
    if left:
        log.warning("Not installed from %s (outside BepInEx/plugins): %s", archive.name, ", ".join(left))


def _find_bepinex_root(root: Path) -> Path:
    for dll in root.rglob("winhttp.dll"):
        if (dll.parent / "BepInEx").is_dir():
            return dll.parent
    for path in root.rglob("*"):
        if path.is_dir() and path.name == "BepInEx" and (path / "core").exists():
            return path.parent
    raise ExtractError("Archive is not a BepInExPack (missing BepInEx/core or winhttp.dll)")


def _rename_generic_folders(dest: Path, folders: list[str], ready_names: list[str] | None) -> list[str]:
    """Rename folders with generic container names (e.g. 'plugins') to a mod identity.

    Prefers the caller-supplied expected folder name when one is given,
    otherwise picks the main DLL stem from the folder contents via the
    shared library.main_dll.pick_main_dll.
    """
    ready = [name.strip() for name in (ready_names or []) if name and name.strip()]
    expected = ready[0] if len(ready) == 1 else None
    result: list[str] = []
    for name in folders:
        folder = dest / name
        if folder.is_dir() and is_generic_folder_name(name):
            target = None
            if expected and not is_generic_folder_name(expected):
                target = expected
            else:
                main = pick_main_dll(list(folder.rglob("*.dll")))
                if main and not is_generic_folder_name(main.stem):
                    target = main.stem
            if target and not (dest / target).exists():
                folder.rename(dest / target)
                result.append(target)
                continue
        result.append(name)
    return result


def _loose_folder_name(source: Path, dlls: list[Path], archive: Path, taken: set[str]) -> str:
    """Name the folder for several loose DLLs: their wrapper, or for an archive root the plugin they declare."""
    if source.name != _EXTRACT_ROOT:
        return source.name
    declared = [dll for dll in dlls if read_plugin_metadata(dll).plugins]
    main = declared[0] if len(declared) == 1 else pick_main_dll(declared or dlls, hints=(archive.stem,))
    for name in (main.stem if main else "", archive.stem):
        # A folder of the archive with that name is copied separately and would replace these files.
        if name and name.casefold() not in taken:
            return name
    return source.name


def _copy_plugin_contents(source: Path, dest: Path, archive: Path) -> list[str]:
    folders: list[str] = []
    dirs = [p for p in _visible_children(source) if p.is_dir()]
    files = [p for p in _visible_children(source) if p.is_file()]
    dll_files = [p for p in files if p.suffix.lower() == ".dll"]

    plugin_dirs = [d for d in dirs if any(d.rglob("*.dll"))]
    if plugin_dirs and not dll_files:
        for folder in plugin_dirs:
            _copy_tree(folder, dest / folder.name)
            folders.append(folder.name)
        left = [item.name for item in _visible_children(source) if item not in plugin_dirs]
        if left:
            log.info("Left out of %s beside its plugin folders: %s", archive.name, ", ".join(sorted(left)))
        if folders:
            return folders

    if dll_files:
        if len(dll_files) == 1:
            name = dll_files[0].stem
        else:
            name = _loose_folder_name(source, dll_files, archive, {item.name.casefold() for item in dirs})
        target = dest / name
        target.mkdir(parents=True, exist_ok=True)
        for item in _visible_children(source):
            if item.is_file():
                shutil.copy2(item, target / item.name)
            elif item.is_dir() and any(item.rglob("*.dll")):
                _copy_tree(item, dest / item.name)
                folders.append(item.name)
            elif item.is_dir():
                # Asset-only folders (textures, bundles) stay beside the DLL that loads them.
                _copy_tree(item, target / item.name)
        folders.insert(0, name)
        return _unique(folders)

    if any(source.rglob("*.dll")):
        name = source.name if source.name != _EXTRACT_ROOT else "plugin"
        _copy_tree(source, dest / name)
        return [name]

    raise ExtractError("No plugin DLLs found in archive")


def _copy_overlay_steam_api(overlay_root: Path, dest: Path, folders: list[str]) -> None:
    src = _find_overlay_steam_api(overlay_root)
    if src is None:
        return
    target = _steam_api_plugin_dir(dest, folders)
    target.mkdir(parents=True, exist_ok=True)
    dest_dll = target / STEAM_API_DLL
    if dest_dll.exists():
        return
    shutil.copy2(src, dest_dll)


def _steam_api_plugin_dir(dest: Path, folders: list[str]) -> Path:
    for name in folders:
        if name.lower() == COOP_PLUGIN_FOLDER.lower():
            return dest / name
    if folders:
        return dest / folders[0]
    return dest / COOP_PLUGIN_FOLDER


def _find_overlay_steam_api(overlay_root: Path) -> Path | None:
    """Locate steam_api64.dll at the game-root of a BepInEx overlay zip."""
    if not overlay_root.exists():
        return None
    for dll in overlay_root.rglob("*"):
        if not dll.is_file() or dll.name.lower() != STEAM_API_DLL:
            continue
        relative = dll.relative_to(overlay_root).parts
        lowered = [part.lower() for part in relative[:-1]]
        if "plugins" in lowered or "core" in lowered:
            continue
        parent_names = {child.name.lower() for child in dll.parent.iterdir()}
        if (
            dll.parent == overlay_root
            or "bepinex" in parent_names
            or "winhttp.dll" in parent_names
            or "sailwind.exe" in parent_names
        ):
            return dll
    return None


def _copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def _retain_folders(dest: Path, folders: list[str], keep_folders: list[str] | None) -> list[str]:
    wanted = {name.strip().lower() for name in (keep_folders or []) if name and name.strip()}
    if not wanted:
        return folders
    kept = [name for name in folders if name.lower() in wanted]
    if not kept:
        return folders
    dropped = sorted(child.name for child in dest.iterdir() if child.name.lower() not in wanted)
    if dropped:
        log.warning("Keeping only %s; left out %s", ", ".join(kept), ", ".join(dropped))
    for child in list(dest.iterdir()):
        if child.name.lower() in wanted:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    return kept


def _visible_children(path: Path) -> list[Path]:
    if not path.exists():
        return []
    result = []
    for child in path.iterdir():
        if child.name.lower() in JUNK_DIRS or child.name.lower() in JUNK_NAMES:
            continue
        if child.name.startswith(".") and child.name.lower() not in {".doorstop_version"}:
            continue
        result.append(child)
    return result


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
