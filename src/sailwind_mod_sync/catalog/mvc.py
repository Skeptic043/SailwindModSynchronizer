from __future__ import annotations

import json
import logging

from sailwind_mod_sync.catalog.custom import load_custom_catalog, merge_with_custom, overlay_entries, same_repo
from sailwind_mod_sync.catalog.scanned import apply_scanned_versions, load_scanned_versions
from sailwind_mod_sync.constants import (
    GITHUB_RAW_APP_MODLIST,
    GITHUB_RAW_APP_VERSIONS,
    GITHUB_RAW_MODLIST,
    GITHUB_RAW_VERSIONS,
    JSDELIVR_APP_MODLIST,
    JSDELIVR_APP_VERSIONS,
    JSDELIVR_MODLIST,
    JSDELIVR_VERSIONS,
)
from sailwind_mod_sync.fileutil import atomic_write_json
from sailwind_mod_sync.http_util import HttpClient, HttpError, ProgressFn
from sailwind_mod_sync.models import CatalogEntry, catalog_mod_name, guid_family, parse_mod_version
from sailwind_mod_sync.paths import AppPaths

log = logging.getLogger(__name__)


def refresh_catalog(
    paths: AppPaths,
    http: HttpClient,
    progress: ProgressFn | None = None,
) -> list[CatalogEntry]:
    if progress:
        progress("Fetching ModVersionChecker catalog…")
    mod_list = _cache_list(paths.modlist_file, _fetch_json_list(http, JSDELIVR_MODLIST, GITHUB_RAW_MODLIST))
    versions = _cache_list(paths.versions_file, _fetch_json_list(http, JSDELIVR_VERSIONS, GITHUB_RAW_VERSIONS))
    mvc = merge_catalog(mod_list, versions)

    if progress:
        progress("Fetching Sailwind Mod Synchronizer catalog…")
    extra_list = _cache_list(
        paths.extra_modlist_file,
        _fetch_json_list(http, JSDELIVR_APP_MODLIST, GITHUB_RAW_APP_MODLIST, optional=True),
    )
    extra_versions = _cache_list(
        paths.extra_versions_file,
        _fetch_json_list(http, JSDELIVR_APP_VERSIONS, GITHUB_RAW_APP_VERSIONS, optional=True),
    )
    extra = merge_catalog(extra_list, extra_versions)
    return build_catalog(paths, overlay_entries(mvc, extra), load_custom_catalog(paths))


def build_catalog(paths: AppPaths, shared: list[CatalogEntry], custom: list[CatalogEntry]) -> list[CatalogEntry]:
    """Merge the shared and custom entries and apply the release versions update scans found."""
    return apply_scanned_versions(merge_with_custom(shared, custom), load_scanned_versions(paths))


def load_mvc_entries(paths: AppPaths) -> list[CatalogEntry]:
    return merge_catalog(_read_json_list(paths.modlist_file), _read_json_list(paths.versions_file))


def load_extra_entries(paths: AppPaths) -> list[CatalogEntry]:
    return merge_catalog(
        _read_json_list(paths.extra_modlist_file),
        _read_json_list(paths.extra_versions_file),
    )


def load_shared_catalog(paths: AppPaths) -> list[CatalogEntry]:
    return overlay_entries(load_mvc_entries(paths), load_extra_entries(paths))


def load_cached_catalog(paths: AppPaths) -> list[CatalogEntry] | None:
    shared = load_shared_catalog(paths)
    custom = load_custom_catalog(paths)
    if not shared and not custom:
        return None
    return build_catalog(paths, shared, custom)


def merge_catalog(mod_list: list, versions: list) -> list[CatalogEntry]:
    version_by_guid: dict[str, str] = {}
    for item in versions:
        if not isinstance(item, dict):
            continue
        guid = str(item.get("guid") or "").strip()
        raw = item.get("version")
        if guid:
            version_by_guid[guid] = "" if raw is None else str(raw).strip()

    by_key: dict[tuple[str, str], dict] = {}
    families_for_repo: dict[str, set[str]] = {}
    for item in mod_list:
        if not isinstance(item, dict):
            continue
        guid = str(item.get("guid") or "").strip()
        repo = str(item.get("repo") or "").strip().rstrip("/")
        if not guid or not repo:
            continue
        family = guid_family(guid)
        bucket = by_key.setdefault(
            (repo, family),
            {"guids": [], "raw": None, "unavailable": False, "name": "", "plugin_folders": []},
        )
        families_for_repo.setdefault(repo, set()).add(family)
        if guid not in bucket["guids"]:
            bucket["guids"].append(guid)
        name = str(item.get("name") or "").strip()
        if name and not bucket["name"]:
            bucket["name"] = name
        folders_raw = item.get("plugin_folders") or []
        if isinstance(folders_raw, list):
            for folder in folders_raw:
                text = str(folder).strip()
                if text and text not in bucket["plugin_folders"]:
                    bucket["plugin_folders"].append(text)
        raw = version_by_guid.get(guid)
        if raw is None:
            inline = str(item.get("version") or "").strip()
            raw = inline or None
        if raw is None:
            continue
        if raw.lower() == "none":
            if bucket["raw"] is None:
                bucket["unavailable"] = True
            continue
        if raw:
            bucket["raw"] = raw
            bucket["unavailable"] = False

    entries: list[CatalogEntry] = []
    for (repo, _family), bucket in by_key.items():
        guids: list[str] = bucket["guids"]
        primary = _pick_primary_guid(guids)
        raw = bucket["raw"]
        normalized = parse_mod_version(raw)
        available = bool(normalized) and not bucket["unavailable"]
        split_repo = len(families_for_repo.get(repo, ())) > 1
        name = bucket["name"] or (
            _name_from_guid(primary) if split_repo else _name_from_repo(repo)
        )
        entries.append(
            CatalogEntry(
                repo=repo,
                guids=list(guids),
                primary_guid=primary,
                name=name,
                latest_raw=raw,
                latest_version=normalized,
                available=available,
                plugin_folders=list(bucket["plugin_folders"]),
            )
        )
    entries.sort(key=lambda entry: entry.name.lower())
    return entries


def find_entry(entries: list[CatalogEntry], guid: str, repo: str = "") -> CatalogEntry | None:
    """Return the entry of ``guid`` published from ``repo``.

    Without ``repo``, return the default entry of ``guid``: the one from the highest-priority catalog that
    lists it. Return None when no entry matches.
    """
    matches = [entry for entry in entries if guid in entry.guids or entry.primary_guid == guid]
    if repo:
        return next((entry for entry in matches if same_repo(entry.repo, repo)), None)
    return next((entry for entry in matches if not entry.alternate), matches[0] if matches else None)


def _read_json_list(path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _cache_list(path, fetched: list | None) -> list:
    """Return ``fetched`` after caching it at ``path``, or the cached list when nothing usable was fetched.

    An empty download never replaces a non-empty cache.
    """
    cached = _read_json_list(path)
    if fetched is None:
        return cached
    if not fetched and cached:
        log.warning("Keeping cached %s: the downloaded list is empty", path.name)
        return cached
    atomic_write_json(path, fetched)
    return fetched


def _fetch_json_list(http: HttpClient, primary: str, fallback: str, *, optional: bool = False) -> list | None:
    try:
        data, _, _ = http.get_json(primary)
    except HttpError as exc:
        log.info("Catalog fetch failed %s: %s", primary, exc)
        try:
            data, _, _ = http.get_json(fallback)
        except HttpError as exc:
            log.info("Catalog fetch failed %s: %s", fallback, exc)
            if optional:
                return None
            raise
    if not isinstance(data, list):
        if optional:
            log.info("Catalog JSON was not a list: %s", primary)
            return None
        raise HttpError("Catalog JSON was not a list")
    return data


def _name_from_repo(repo: str) -> str:
    return repo.rstrip("/").split("/")[-1] or repo


def _name_from_guid(guid: str) -> str:
    return catalog_mod_name(guid)


def _pick_primary_guid(guids: list[str]) -> str:
    if not guids:
        return ""
    ranked = sorted(guids, key=lambda g: (0 if "82" in g else 1, -len(g), g))
    return ranked[0]
