from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sailwind_mod_sync.game.backup import (
    BackupError,
    BackupResult,
    RestoreResult,
    _extract_prefix,
    _remove_tree,
    _zip_file_names,
    zip_folder,
)
from sailwind_mod_sync.constants import STEAM_APP_ID
from sailwind_mod_sync.http_util import ProgressFn

SAVES_COMPANY = "Raw Lion Workshop"
SAVES_PRODUCT = "Sailwind"
SAVE_SUFFIX = ".save"
SKIP_TOP_DIRS = {"unity"}


@dataclass
class SaveRestoreResult(RestoreResult):
    safety_backup: Path | None = None


def default_player_data_dir() -> Path:
    if os.name != "nt":
        candidates = proton_saves_candidates()
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        if candidates:
            return candidates[0]
    return Path.home() / "AppData" / "LocalLow" / SAVES_COMPANY / SAVES_PRODUCT


def default_saves_dir() -> Path:
    env = os.environ.get("SAILWIND_SAVES_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return default_player_data_dir()


def proton_saves_candidates(libraries: list[Path] | None = None) -> list[Path]:
    """Where Sailwind keeps saves when it runs through Proton, one per Steam library.

    Proton gives the game a Windows-style prefix under compatdata. It isn't always
    in the same library as the game itself (e.g. the game on an SD card, the
    prefix in the main Steam folder), so every library is checked.
    """
    if libraries is None:
        from sailwind_mod_sync.game.detect import steam_libraries

        libraries = steam_libraries()
    relative = (
        Path("steamapps") / "compatdata" / str(STEAM_APP_ID) / "pfx" / "drive_c" / "users" / "steamuser"
        / "AppData" / "LocalLow" / SAVES_COMPANY / SAVES_PRODUCT
    )
    return [library / relative for library in libraries]


def saves_dir_has_files(path: Path) -> bool:
    return path.is_dir() and any(_iter_save_backup_files(path))


def backup_saves_folder(source: Path, dest: Path, progress: ProgressFn | None = None) -> BackupResult:
    source = source.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Sailwind save folder not found: {source}")
    files = list(_iter_save_backup_files(source))
    if not files:
        raise FileNotFoundError(f"Sailwind save folder has no files to back up: {source}")
    if not any(_is_save_file(path) for path in files):
        raise FileNotFoundError(f"Sailwind save folder has no .save files: {source}")
    result = zip_folder(source, dest, files, progress=progress)
    if not _zip_has_save(result.dest):
        result.dest.unlink(missing_ok=True)
        raise BackupError(
            "Could not read any .save files. Close Sailwind and try again."
        )
    return result


def inspect_saves_zip(archive: Path) -> str:
    """Return the zip prefix of the Sailwind save folder ('' if saves are at the zip root)."""
    names = _zip_file_names(archive)
    save_names = [name for name in names if name.lower().endswith(SAVE_SUFFIX)]
    if not save_names:
        raise BackupError(
            "This zip is not a Sailwind save backup. It needs at least one .save file."
        )
    for name in save_names:
        parts = name.split("/")
        for index, part in enumerate(parts[:-1]):
            if part.lower() == SAVES_PRODUCT.lower():
                return "/".join(parts[: index + 1])
    parents = {"/".join(name.split("/")[:-1]) for name in save_names}
    if len(parents) == 1:
        parent = next(iter(parents))
        if parent:
            return parent
    return ""


def restore_saves_folder(
    archive: Path,
    dest: Path,
    *,
    safety_dest: Path | None = None,
    progress: ProgressFn | None = None,
) -> SaveRestoreResult:
    archive = archive.resolve()
    dest = dest.resolve()
    if progress:
        progress("Checking save backup…")
    prefix = inspect_saves_zip(archive)
    safety: Path | None = None
    if saves_dir_has_files(dest):
        if safety_dest is None:
            raise BackupError("Restore needs a path for the automatic backup of current saves.")
        if progress:
            progress("Backing up current saves…")
        backed = backup_saves_folder(dest, safety_dest, progress=progress)
        safety = backed.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f"{dest.name}.restore-tmp"
    _remove_tree(tmp)
    tmp.mkdir(parents=True)
    try:
        if progress:
            progress("Extracting save backup…")
        count = _extract_prefix(archive, tmp, prefix, progress)
        if not any(_is_save_file(path) for path in tmp.rglob("*") if path.is_file()):
            raise BackupError("The extracted files are not Sailwind saves (no .save files).")
        if progress:
            progress(f"Replacing saves in {dest}…")
        _replace_saves(dest, tmp)
    except Exception:
        _remove_tree(tmp)
        raise
    return SaveRestoreResult(dest=dest, archive=archive, file_count=count, safety_backup=safety)


def _iter_save_backup_files(source: Path):
    for path in source.rglob("*"):
        if path.is_file() and not _skip_save_backup(path, source):
            yield path


def _skip_save_backup(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if rel.parts and rel.parts[0].lower() in SKIP_TOP_DIRS:
        return True
    return path.suffix.lower() == ".log"


def _is_save_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == SAVE_SUFFIX


def _zip_has_save(archive: Path) -> bool:
    return any(name.lower().endswith(SAVE_SUFFIX) for name in _zip_file_names(archive))


def _keep_existing(path: Path) -> bool:
    if path.is_dir():
        return path.name.lower() in SKIP_TOP_DIRS
    return path.suffix.lower() == ".log"


def _replace_saves(dest: Path, incoming: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in list(dest.iterdir()):
        if _keep_existing(child):
            continue
        if child.is_dir():
            _remove_tree(child)
        else:
            try:
                child.unlink()
            except OSError as exc:
                raise BackupError(
                    f"Could not replace {child}. Close Sailwind and try again. ({exc})"
                ) from exc
    for path in incoming.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(incoming)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except OSError as exc:
            raise BackupError(
                f"Could not write {target}. Close Sailwind and try again. ({exc})"
            ) from exc
    _remove_tree(incoming)
