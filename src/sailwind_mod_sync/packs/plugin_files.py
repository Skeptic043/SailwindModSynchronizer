"""Keep files that players and mods change inside a mod's plugin folders.

Each pack records the last packaged version of every path it manages for a mod:
``installed-files/record.json`` holds its SHA-256, and ``installed-files/base/<guid>/`` holds a copy
of each such ``.json`` file for three-way merges. A path stays recorded while the player's edited
copy is kept, even when the pinned version no longer ships it; the record's optional ``kept`` list
names such paths, so they are never removed as stale package files. A file on disk is compared with
the new package and that record to decide whether it is copied, kept, merged, deleted, or asked about.
The record's ``artifact`` field only says where the files came from; classification uses the hashes alone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path

from sailwind_mod_sync.fileutil import atomic_write_bytes, atomic_write_json
from sailwind_mod_sync.library.hashing import sha256_file
from sailwind_mod_sync.paths import sanitize_segment

log = logging.getLogger(__name__)

RECORD_DIR = "installed-files"
RECORD_FILE = "record.json"
RECORD_VERSION = 1

COPY = "copy"
KEEP = "keep"
MERGE = "merge"
DELETE = "delete"
CONFLICT = "conflict"

_MISSING = object()


@dataclass(frozen=True)
class FileConflict:
    """A file both the player (or the mod) and the new package changed."""

    guid: str
    relpath: str
    # JSON entries that differ. Empty for a whole-file conflict.
    entries: tuple[str, ...] = ()
    # Keeping the player's version still adds the entries the package introduced.
    merged: bool = False


# Return the conflicts to replace with the mod's new version; every other file keeps the player's version.
ConflictResolver = Callable[[list[FileConflict]], Collection[FileConflict]]


@dataclass
class FileAction:
    relpath: str
    kind: str
    package: Path | None = None
    current: Path | None = None
    merged: bytes | None = None
    conflict: FileConflict | None = None


@dataclass
class FilePlan:
    guid: str
    actions: list[FileAction] = field(default_factory=list)
    # The package's own files, for the install record.
    package_hashes: dict[str, str] = field(default_factory=dict)
    package_json: dict[str, bytes] = field(default_factory=dict)
    # What the files were last installed from; conflicts nobody was asked about stay recorded this way.
    baseline: Baseline | None = None

    @property
    def conflicts(self) -> list[FileConflict]:
        return [action.conflict for action in self.actions if action.conflict is not None]


@dataclass
class Baseline:
    """The package files a mod's folders were last installed from."""

    files: dict[str, str]
    root: Path | None = None
    # Paths kept for the player while the pinned version does not ship them.
    kept: Collection[str] = ()

    def __post_init__(self) -> None:
        self._index = {relpath.casefold(): relpath for relpath in self.files}
        self._kept = {relpath.casefold() for relpath in self.kept}

    def is_kept(self, relpath: str) -> bool:
        return relpath.casefold() in self._kept

    def hash(self, relpath: str) -> str | None:
        recorded = self._index.get(relpath.casefold())
        return self.files[recorded] if recorded is not None else None

    def content(self, relpath: str) -> bytes | None:
        """The recorded copy, or None when it is missing or no longer matches its hash."""
        recorded = self._index.get(relpath.casefold())
        if recorded is None or self.root is None:
            return None
        try:
            data = (self.root / recorded).read_bytes()
        except OSError:
            return None
        return data if hashlib.sha256(data).hexdigest() == self.files[recorded] else None


def record_dir(pack_dir: Path) -> Path:
    return pack_dir / RECORD_DIR


def _base_dir(pack_dir: Path, guid: str) -> Path:
    return record_dir(pack_dir) / "base" / sanitize_segment(guid)


def _read_record(pack_dir: Path) -> dict:
    path = record_dir(pack_dir) / RECORD_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mods = data.get("mods") if isinstance(data, dict) else None
        if not isinstance(mods, dict):
            raise ValueError("record has no mods")
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable install record %s: %s", path, exc)
        return {}
    return mods


def load_baselines(pack_dir: Path) -> dict[str, Baseline]:
    baselines: dict[str, Baseline] = {}
    for guid, entry in _read_record(pack_dir).items():
        files = entry.get("files") if isinstance(entry, dict) else None
        if not isinstance(files, dict) or not all(isinstance(value, str) for value in files.values()):
            log.warning("Ignoring malformed install record for %s in %s", guid, pack_dir)
            continue
        kept = entry.get("kept")
        kept = [relpath for relpath in kept if isinstance(relpath, str)] if isinstance(kept, list) else []
        baselines[guid] = Baseline(dict(files), _base_dir(pack_dir, guid), kept)
    return baselines


def baseline_from_package(extracted: Path, folders: list[str]) -> Baseline:
    """Treat a library artifact as what was last installed (packs prepared before the record existed)."""
    files = {relpath: sha256_file(path) for relpath, path in _list_files(extracted, folders).values()}
    return Baseline(files, extracted)


def save_records(pack_dir: Path, installed: list[tuple[FilePlan, str]], *, asked: bool) -> None:
    """Record each plan's package files as installed from artifact ``key``; logs instead of raising.

    A kept file the package does not ship keeps its previous record, as does, unless the player was
    ``asked``, a conflicting file, so it is asked about later.
    """
    try:
        mods = _read_record(pack_dir)
        changed = False
        for plan, key in installed:
            hashes, snapshots = dict(plan.package_hashes), dict(plan.package_json)
            kept = [action.relpath for action in plan.actions
                    if action.kind == KEEP and action.relpath not in plan.package_hashes]
            carried = kept + ([] if asked else [conflict.relpath for conflict in plan.conflicts])
            for relpath in carried:
                previous = plan.baseline.hash(relpath) if plan.baseline is not None else None
                content = plan.baseline.content(relpath) if previous is not None else None
                if previous is None:
                    hashes.pop(relpath, None)
                else:
                    hashes[relpath] = previous
                snapshots.pop(relpath, None)
                if content is not None and _is_json(relpath):
                    snapshots[relpath] = content
            entry = {"artifact": key, "files": dict(sorted(hashes.items()))}
            if kept := sorted(relpath for relpath in kept if relpath in hashes):
                entry["kept"] = kept
            if mods.get(plan.guid) == entry:
                continue
            base = _base_dir(pack_dir, plan.guid)
            if base.exists():
                shutil.rmtree(base)
            for relpath, data in snapshots.items():
                target = base / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            mods[plan.guid] = entry
            changed = True
        if changed:
            atomic_write_json(record_dir(pack_dir) / RECORD_FILE, {"version": RECORD_VERSION, "mods": mods})
    except OSError:
        log.warning("Could not update the install record in %s", pack_dir, exc_info=True)


def drop_record(pack_dir: Path, guid: str) -> None:
    try:
        mods = _read_record(pack_dir)
        if mods.pop(guid, None) is not None:
            atomic_write_json(record_dir(pack_dir) / RECORD_FILE, {"version": RECORD_VERSION, "mods": mods})
        base = _base_dir(pack_dir, guid)
        if base.exists():
            shutil.rmtree(base)
    except OSError:
        log.warning("Could not remove the install record of %s in %s", guid, pack_dir, exc_info=True)


def plan_mod_files(
    guid: str,
    package_root: Path | None,
    package_folders: list[str],
    plugins_dir: Path,
    current_folders: list[str],
    baseline: Baseline | None,
    claimed: Collection[str] = (),
) -> FilePlan:
    """Classify each file of the package and of the mod's current folders.

    With no package folders (a disabled mod), only unmodified package files and DLLs are removed.
    Files other mods ``claimed`` (case-folded relative paths) are left to them unless this package ships them too.
    """
    package = _list_files(package_root, package_folders) if package_root is not None else {}
    spelling = {name.casefold(): name for name in package_folders}
    current = {key: value for key, value in _list_files(plugins_dir, current_folders, spelling).items()
               if key not in claimed or key in package}
    plan = FilePlan(guid, baseline=baseline)
    for relpath, path in package.values():
        plan.package_hashes[relpath] = sha256_file(path)
        if _is_json(relpath):
            plan.package_json[relpath] = path.read_bytes()
    for key in sorted(package.keys() | current.keys()):
        relpath, source = package.get(key, (None, None))
        disk_relpath, disk = current.get(key, (None, None))
        relpath = relpath or disk_relpath
        recorded = baseline.hash(relpath) if baseline is not None else None
        is_dll = relpath.lower().endswith(".dll")
        if source is None:
            disk_hash = sha256_file(disk)
            installing = package_root is not None
            if is_dll:
                if installing and disk_hash != recorded:
                    log.info("Removing %s of %s: the package has no such DLL", relpath, guid)
                plan.actions.append(FileAction(relpath, DELETE, current=disk))
            elif recorded is not None and disk_hash == recorded and not baseline.is_kept(relpath):
                plan.actions.append(FileAction(relpath, DELETE, current=disk))
            else:
                if installing and recorded is not None:
                    # Repeats on every launch while such a version is pinned.
                    log.debug("Keeping edited %s of %s, which the package no longer ships", relpath, guid)
                plan.actions.append(FileAction(relpath, KEEP, current=disk))
            continue
        if disk is None:
            plan.actions.append(FileAction(relpath, COPY, package=source))
            continue
        package_hash = plan.package_hashes[relpath]
        disk_hash = sha256_file(disk)
        if disk_hash == package_hash:
            continue
        if is_dll:
            log.info("Replacing modified %s of %s with the package's DLL", relpath, guid)
            plan.actions.append(FileAction(relpath, COPY, package=source))
        elif recorded is not None and disk_hash == recorded:
            plan.actions.append(FileAction(relpath, COPY, package=source))
        elif recorded is not None and package_hash == recorded:
            plan.actions.append(FileAction(relpath, KEEP, current=disk))
        else:
            plan.actions.append(_both_changed(guid, relpath, source, disk, baseline))
    return plan


def _both_changed(guid: str, relpath: str, source: Path, disk: Path, baseline: Baseline | None) -> FileAction:
    if _is_json(relpath):
        recorded = baseline.hash(relpath) if baseline is not None else None
        # Without a recorded version every entry counts as added; a lost copy of one cannot be guessed.
        base_bytes = baseline.content(relpath) if recorded is not None else b"{}"
        base = _load_json_object(base_bytes) if base_bytes is not None else None
        user = _load_json_object(disk.read_bytes())
        new = _load_json_object(source.read_bytes())
        if base is not None and user is not None and new is not None:
            conflicts: list[str] = []
            merged = _merge(base, user, new, (), conflicts)
            data = None if _same(merged, user) else (
                json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
            ).encode("utf-8")
            if not conflicts:
                if data is None:
                    return FileAction(relpath, KEEP, current=disk)
                log.info("Merged the package's new entries into %s of %s", relpath, guid)
                return FileAction(relpath, MERGE, package=source, current=disk, merged=data)
            conflict = FileConflict(guid, relpath, tuple(conflicts), merged=data is not None)
            return FileAction(relpath, CONFLICT, package=source, current=disk, merged=data, conflict=conflict)
    return FileAction(relpath, CONFLICT, package=source, current=disk, conflict=FileConflict(guid, relpath))


def ask_conflicts(plans: list[FilePlan], resolver: ConflictResolver | None) -> set[FileConflict]:
    """Ask ``resolver`` once for all conflicts; without one, keep the player's files."""
    conflicts = [conflict for plan in plans for conflict in plan.conflicts]
    chosen = set(resolver(conflicts) or ()) if conflicts and resolver is not None else set()
    for conflict in conflicts:
        outcome = "the mod's new version" if conflict in chosen else "your version"
        log.info("Conflict in %s of %s: using %s", conflict.relpath, conflict.guid, outcome)
    return chosen


def apply_file_plan(plan: FilePlan, use_new: Collection[FileConflict], plugins_dir: Path) -> None:
    """Change the mod's folders in place."""
    emptied: set[Path] = set()
    for action in plan.actions:
        target = action.current or plugins_dir / action.relpath
        if action.kind == COPY or (action.kind == CONFLICT and action.conflict in use_new):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(action.package, target)
        elif action.merged is not None and action.kind in (MERGE, CONFLICT):
            # The player's own file: a failed write must not leave it truncated.
            atomic_write_bytes(target, action.merged)
        elif action.kind == DELETE:
            target.unlink()
            emptied.add(target.parent)
    _remove_empty_dirs(emptied, plugins_dir)


def overlay_kept_files(plan: FilePlan, use_new: Collection[FileConflict], staged: Path) -> None:
    """Carry kept and merged files into ``staged``, which already holds a copy of the package."""
    for action in plan.actions:
        target = staged / action.relpath
        if action.kind == CONFLICT and action.conflict in use_new:
            continue
        if action.merged is not None and action.kind in (MERGE, CONFLICT):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(action.merged)
        elif action.kind in (KEEP, CONFLICT):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(action.current, target)


def merge_json(base: dict, user: dict, new: dict) -> tuple[dict, list[str]]:
    """Three-way merge of JSON objects; return the result and the entries both sides changed differently."""
    conflicts: list[str] = []
    return _merge(base, user, new, (), conflicts), conflicts


def _merge(base, user, new, path: tuple[str, ...], conflicts: list[str]):
    if _same(user, new):
        return user
    if _same(user, base):
        return new
    if _same(new, base):
        return user
    if isinstance(user, dict) and isinstance(new, dict):
        base = base if isinstance(base, dict) else {}
        merged = {}
        for key in [*user, *(key for key in new if key not in user)]:
            value = _merge(base.get(key, _MISSING), user.get(key, _MISSING), new.get(key, _MISSING),
                           (*path, key), conflicts)
            if value is not _MISSING:
                merged[key] = value
        return merged
    if new is _MISSING:
        return user
    conflicts.append("/".join(path))
    return user


def _same(a, b) -> bool:
    """JSON equality that tells ``true`` from ``1`` and ``1`` from ``1.0``."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[key], b[key]) for key in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(map(_same, a, b))
    return a == b


def package_paths(extracted: Path, folders: list[str]) -> set[str]:
    """Case-folded relative paths of a package's files, as ``plan_mod_files`` compares them."""
    return set(_list_files(extracted, folders))


def _load_json_object(data: bytes) -> dict | None:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _is_json(relpath: str) -> bool:
    return relpath.lower().endswith(".json")


def _list_files(
    root: Path, folders: list[str], spelling: dict[str, str] | None = None,
) -> dict[str, tuple[str, Path]]:
    """Index files under ``root/<folder>`` by case-insensitive relative path.

    ``spelling`` names a folder as the package does when only its case differs on disk.
    """
    files: dict[str, tuple[str, Path]] = {}
    for folder in folders:
        directory = root / folder
        if not directory.is_dir():
            continue
        name = (spelling or {}).get(folder.casefold(), folder)
        for path in directory.rglob("*"):
            if path.is_file():
                relpath = f"{name}/{path.relative_to(directory).as_posix()}"
                files[relpath.casefold()] = (relpath, path)
    return files


def _remove_empty_dirs(directories: set[Path], plugins_dir: Path) -> None:
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        while directory != plugins_dir and directory.is_relative_to(plugins_dir):
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent
