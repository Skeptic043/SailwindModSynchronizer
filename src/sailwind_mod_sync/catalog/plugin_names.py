from __future__ import annotations

import json
from dataclasses import dataclass, replace

from sailwind_mod_sync.catalog.custom import repo_key, same_repo
from sailwind_mod_sync.fileutil import atomic_write_json
from sailwind_mod_sync.models import CatalogEntry
from sailwind_mod_sync.paths import AppPaths


@dataclass(frozen=True)
class PluginName:
    name: str
    """The plugin's name, or "" when the library copy it was read from holds no plugin of the mod."""
    version: str
    """The version of the library copy the name was read from."""


PluginNames = dict[tuple[str, str], PluginName]


def load_plugin_names(paths: AppPaths) -> PluginNames:
    """Return the plugin names read from library copies, keyed by mod GUID and repository key."""
    try:
        data = json.loads(paths.plugin_names_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    names: PluginNames = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        guid = str(item.get("guid") or "").strip()
        repo = repo_key(str(item.get("repo") or ""))
        if guid and repo:
            names[(guid, repo)] = PluginName(
                name=str(item.get("name") or "").strip(),
                version=str(item.get("version") or "").strip(),
            )
    return names


def store_plugin_names(paths: AppPaths, names: PluginNames) -> None:
    payload = [
        {"guid": guid, "repo": repo, "name": record.name, "version": record.version}
        for (guid, repo), record in sorted(names.items())
    ]
    atomic_write_json(paths.plugin_names_file, payload)


def find_plugin_name(entry: CatalogEntry, names: PluginNames) -> PluginName | None:
    """Return the plugin name recorded for any GUID of ``entry`` from its repository, or None when none is."""
    repo = repo_key(entry.repo)
    for guid in [entry.primary_guid, *entry.guids]:
        record = names.get((guid, repo))
        if record is not None:
            return record
    return None


def name_entries(
    entries: list[CatalogEntry],
    supplemental: list[CatalogEntry],
    names: PluginNames,
) -> list[CatalogEntry]:
    """Return ``entries`` with each derived name replaced by the name ``supplemental`` gives the same mod from the same
    repository, or else by the mod's recorded plugin name."""
    return [_named_entry(entry, supplemental, names) for entry in entries]


def _named_entry(entry: CatalogEntry, supplemental: list[CatalogEntry], names: PluginNames) -> CatalogEntry:
    if not entry.derived_name:
        return entry
    catalog_name = _supplemental_name(entry, supplemental)
    if catalog_name:
        return replace(entry, name=catalog_name, derived_name=False)
    record = find_plugin_name(entry, names)
    if record is not None and record.name:
        return replace(entry, name=record.name)
    return entry


def _supplemental_name(entry: CatalogEntry, supplemental: list[CatalogEntry]) -> str:
    guids = set(entry.guids)
    return next(
        (
            other.name
            for other in supplemental
            if not other.derived_name and same_repo(other.repo, entry.repo) and guids.intersection(other.guids)
        ),
        "",
    )
