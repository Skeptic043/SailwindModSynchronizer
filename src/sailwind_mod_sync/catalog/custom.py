from __future__ import annotations

import json
import logging
from dataclasses import replace

from sailwind_mod_sync.catalog.github import canonicalize_repo_url
from sailwind_mod_sync.fileutil import atomic_write_json
from sailwind_mod_sync.models import CatalogEntry, catalog_mod_name, guid_family, parse_mod_version
from sailwind_mod_sync.paths import AppPaths

log = logging.getLogger(__name__)


def load_custom_catalog(paths: AppPaths) -> list[CatalogEntry]:
    path = paths.custom_catalog_file
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    entries: list[CatalogEntry] = []
    for item in data:
        entry = _entry_from_dict(item)
        if entry is not None:
            entries.extend(_split_multi_mod_entry(entry))
    return entries


def save_custom_catalog(paths: AppPaths, entries: list[CatalogEntry]) -> None:
    atomic_write_json(paths.custom_catalog_file, [_entry_to_dict(entry) for entry in entries])


def store_custom_versions(paths: AppPaths, updated: list[CatalogEntry]) -> None:
    """Write the latest versions of ``updated`` into the custom catalog entries of the same mod and repository."""
    custom = load_custom_catalog(paths)
    changed = False
    for stored in custom:
        source = next((entry for entry in updated if _same_source(entry, stored)), None)
        if source is None or source.latest_raw == stored.latest_raw:
            continue
        stored.latest_raw = source.latest_raw
        stored.latest_version = source.latest_version
        stored.available = source.available
        changed = True
    if changed:
        save_custom_catalog(paths, custom)


def upsert_custom_entry(entries: list[CatalogEntry], incoming: CatalogEntry) -> list[CatalogEntry]:
    """Return ``entries`` with the entries of the same mod and repository as ``incoming`` replaced by it."""
    out: list[CatalogEntry] = []
    replaced = False
    incoming_guids = set(incoming.guids) | {incoming.primary_guid}
    incoming_repo = _repo_key(incoming.repo)
    for entry in entries:
        overlap = incoming_guids & (set(entry.guids) | {entry.primary_guid})
        if overlap and _repo_key(entry.repo) == incoming_repo:
            out.append(incoming)
            replaced = True
        else:
            out.append(entry)
    if not replaced:
        out.append(incoming)
    return out


def remove_custom_entry(entries: list[CatalogEntry], guid: str, repo: str = "") -> list[CatalogEntry]:
    """Return ``entries`` without those of ``guid``; with ``repo``, only those published from it are removed."""
    wanted = (guid or "").strip()
    return [
        entry
        for entry in entries
        if (wanted not in entry.guids and entry.primary_guid != wanted)
        or (repo and not same_repo(entry.repo, repo))
    ]


def overlay_entries(
    base: list[CatalogEntry],
    extra: list[CatalogEntry],
    *,
    mark_custom: bool = False,
) -> list[CatalogEntry]:
    """Return ``base`` plus the parts of ``extra`` it does not cover, sorted by name.

    Entries of ``extra`` are copied when trimmed or marked, so the caller's lists stay unchanged.
    """
    guids = {guid for entry in base for guid in entry.guids}
    added: list[CatalogEntry] = []
    for entry in extra:
        leftover = [guid for guid in entry.guids if guid not in guids]
        if not leftover:
            continue
        primary = entry.primary_guid if entry.primary_guid in leftover else leftover[0]
        added.append(replace(entry, guids=leftover, primary_guid=primary, custom=entry.custom or mark_custom))
        guids.update(leftover)
    combined = list(base) + added
    combined.sort(key=lambda item: item.name.lower())
    return combined


def merge_with_custom(mvc: list[CatalogEntry], custom: list[CatalogEntry]) -> list[CatalogEntry]:
    return overlay_entries(mvc, custom, mark_custom=True)


def same_repo(left: str, right: str) -> bool:
    return _repo_key(left) == _repo_key(right) and bool(_repo_key(left))


def _same_source(left: CatalogEntry, right: CatalogEntry) -> bool:
    shares_guid = left.primary_guid in right.guids or right.primary_guid in left.guids
    return shares_guid and same_repo(left.repo, right.repo)


def _repo_key(repo: str) -> str:
    text = (repo or "").strip().rstrip("/")
    if not text:
        return ""
    try:
        text = canonicalize_repo_url(text)
    except ValueError:
        pass
    return text.lower()


def _entry_from_dict(data: object) -> CatalogEntry | None:
    if not isinstance(data, dict):
        return None
    repo = str(data.get("repo") or "").strip()
    guids_raw = data.get("guids") or []
    if not isinstance(guids_raw, list):
        guids_raw = []
    guids = [str(item).strip() for item in guids_raw if str(item).strip()]
    primary = str(data.get("primary_guid") or "").strip() or (guids[0] if guids else "")
    if not primary:
        return None
    if primary not in guids:
        guids.insert(0, primary)
    raw = data.get("latest_raw")
    raw_text = None if raw is None else str(raw).strip()
    version = parse_mod_version(str(data.get("latest_version") or raw_text or ""))
    name = (
        str(data.get("name") or "").strip()
        or repo.rstrip("/").split("/")[-1]
        or catalog_mod_name(primary)
    )
    folders_raw = data.get("plugin_folders") or []
    if not isinstance(folders_raw, list):
        folders_raw = []
    folders = [str(item).strip() for item in folders_raw if str(item).strip()]
    return CatalogEntry(
        repo=repo,
        guids=guids,
        primary_guid=primary,
        name=name,
        latest_raw=raw_text,
        latest_version=version,
        available=bool(version),
        custom=True,
        plugin_folders=folders,
    )


def _entry_to_dict(entry: CatalogEntry) -> dict:
    return {
        "repo": entry.repo,
        "guids": list(entry.guids),
        "primary_guid": entry.primary_guid,
        "name": entry.name,
        "latest_raw": entry.latest_raw,
        "latest_version": entry.latest_version,
        "plugin_folders": list(entry.plugin_folders),
    }


def _split_multi_mod_entry(entry: CatalogEntry) -> list[CatalogEntry]:
    groups: dict[str, list[str]] = {}
    for guid in entry.guids:
        groups.setdefault(guid_family(guid), []).append(guid)
    if len(groups) <= 1:
        return [entry]
    split: list[CatalogEntry] = []
    for guids in groups.values():
        primary = guids[0]
        if entry.primary_guid in guids:
            primary = entry.primary_guid
        split.append(
            CatalogEntry(
                repo=entry.repo,
                guids=list(guids),
                primary_guid=primary,
                name=catalog_mod_name(primary),
                latest_raw=entry.latest_raw,
                latest_version=entry.latest_version,
                available=entry.available,
                custom=True,
                plugin_folders=[],
            )
        )
    return split
