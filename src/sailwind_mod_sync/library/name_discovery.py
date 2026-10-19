from __future__ import annotations

import logging

from sailwind_mod_sync.catalog.custom import repo_key, same_repo
from sailwind_mod_sync.catalog.plugin_names import (
    PluginName,
    PluginNames,
    find_plugin_name,
    load_plugin_names,
    store_plugin_names,
)
from sailwind_mod_sync.game.scan_plugins import DiscoveredPlugin, scan_plugins_dir
from sailwind_mod_sync.http_util import ProgressFn
from sailwind_mod_sync.models import CatalogEntry, LibraryEntry, version_key
from sailwind_mod_sync.paths import AppPaths

log = logging.getLogger(__name__)


def record_library_plugin_names(
    paths: AppPaths,
    catalog: list[CatalogEntry],
    library: list[LibraryEntry],
    progress: ProgressFn | None = None,
) -> bool:
    """Record the plugin names of the catalog mods no catalog names, read from their newest library copies.

    A library copy already read is not read again.

    Returns:
        Whether any recorded name changed.
    """
    names = load_plugin_names(paths)
    unread = _unread_copies(catalog, library, names)
    changed = False
    for index, (entry, copy) in enumerate(unread, 1):
        if progress:
            progress(f"Reading plugin names ({index}/{len(unread)}): {entry.name}…")
        changed |= _read_library_copy(entry, copy, catalog, names)
    if changed:
        store_plugin_names(paths, names)
    return changed


def _unread_copies(
    catalog: list[CatalogEntry],
    library: list[LibraryEntry],
    names: PluginNames,
) -> list[tuple[CatalogEntry, LibraryEntry]]:
    unread: list[tuple[CatalogEntry, LibraryEntry]] = []
    for entry in catalog:
        if not entry.derived_name or entry.custom:
            continue
        copy = _newest_library_copy(entry, library)
        if copy is not None and not _already_read(entry, copy, names):
            unread.append((entry, copy))
    return unread


def _newest_library_copy(entry: CatalogEntry, library: list[LibraryEntry]) -> LibraryEntry | None:
    copies = [
        item
        for item in library
        if item.guid in entry.guids and (not item.meta.repo or same_repo(item.meta.repo, entry.repo))
    ]
    return max(copies, key=lambda item: version_key(item.meta.version or item.version), default=None)


def _copy_version(copy: LibraryEntry) -> str:
    return copy.meta.version_raw or copy.meta.version or copy.version


def _already_read(entry: CatalogEntry, copy: LibraryEntry, names: PluginNames) -> bool:
    record = find_plugin_name(entry, names)
    return record is not None and record.version == _copy_version(copy)


def _read_library_copy(
    entry: CatalogEntry,
    copy: LibraryEntry,
    catalog: list[CatalogEntry],
    names: PluginNames,
) -> bool:
    try:
        plugins = list(scan_plugins_dir(copy.extracted_dir, catalog=catalog).discovered)
    except (OSError, ValueError) as exc:
        log.warning("Could not read the plugin name of %s from the library: %s", entry.primary_guid, exc)
        return False
    return _record(entry, plugins, _copy_version(copy), names)


def _record(entry: CatalogEntry, plugins: list[DiscoveredPlugin], version: str, names: PluginNames) -> bool:
    """Record the name of the plugin of ``entry`` among ``plugins``.

    Returns:
        Whether the recorded name changed.
    """
    own = next((plugin for plugin in plugins if plugin.guid in entry.guids), None)
    if own is None and len(plugins) == 1:
        own = plugins[0]
    record = PluginName(own.name.strip() if own else "", version)
    key = (entry.primary_guid, repo_key(entry.repo))
    changed = names.get(key) != record
    names[key] = record
    if own is not None:
        log.info("Read plugin name %r of %s from library copy %s", own.name, entry.primary_guid, version)
    return changed
