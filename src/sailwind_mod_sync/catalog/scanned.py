from __future__ import annotations

import json
from dataclasses import replace

from sailwind_mod_sync.catalog.custom import repo_key
from sailwind_mod_sync.fileutil import atomic_write_json
from sailwind_mod_sync.models import CatalogEntry, is_newer, parse_mod_version
from sailwind_mod_sync.paths import AppPaths

ScannedVersions = dict[tuple[str, str], str]


def load_scanned_versions(paths: AppPaths) -> ScannedVersions:
    """Return the release versions update scans found, keyed by mod GUID and repository key."""
    try:
        data = json.loads(paths.scanned_versions_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    scanned: ScannedVersions = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        guid = str(item.get("guid") or "").strip()
        repo = repo_key(str(item.get("repo") or ""))
        version = str(item.get("version") or "").strip()
        if guid and repo and version:
            scanned[(guid, repo)] = version
    return scanned


def store_scanned_versions(paths: AppPaths, entries: list[CatalogEntry]) -> None:
    """Record the latest versions of ``entries`` as found by an update scan, replacing earlier records of them."""
    scanned = load_scanned_versions(paths)
    for entry in entries:
        if entry.latest_raw:
            scanned[(entry.primary_guid, repo_key(entry.repo))] = entry.latest_raw
    payload = [{"guid": guid, "repo": repo, "version": version} for (guid, repo), version in sorted(scanned.items())]
    atomic_write_json(paths.scanned_versions_file, payload)


def apply_scanned_versions(entries: list[CatalogEntry], scanned: ScannedVersions) -> list[CatalogEntry]:
    """Return ``entries`` with the scanned version of each source where the catalog has none or an older one."""
    if not scanned:
        return entries
    return [_with_scanned_version(entry, scanned) for entry in entries]


def _with_scanned_version(entry: CatalogEntry, scanned: ScannedVersions) -> CatalogEntry:
    repo = repo_key(entry.repo)
    raw = next((scanned[(guid, repo)] for guid in entry.guids if (guid, repo) in scanned), None)
    if raw is None:
        return entry
    if entry.available and entry.latest_raw and not is_newer(raw, entry.latest_raw):
        return entry
    version = parse_mod_version(raw)
    return replace(entry, latest_raw=raw, latest_version=version, available=bool(version))
