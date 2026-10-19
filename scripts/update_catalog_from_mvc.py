"""Update this repo's catalog from ModVersionChecker: names for its mods and their newer versions.

Usage (from the repo root, with the project venv active):

    python scripts/update_catalog_from_mvc.py
    python scripts/update_catalog_from_mvc.py --dry-run

Each ModVersionChecker mod this catalog does not name is downloaded the way the app downloads it and added to
catalog/ModList.json and catalog/release_versions.json, named after the ``[BepInPlugin]`` attribute of the plugin with
its GUID. Mods this catalog already names keep their names. Every mod both lists give from the same repository takes the
ModVersionChecker version when it is newer than the catalog's. A GitHub token from GITHUB_TOKEN, GH_TOKEN or the app's
settings raises the GitHub API rate limit.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from catalog_tools import (
    MODLIST_FILE,
    REPO_ROOT,
    VERSIONS_FILE,
    BlockedError,
    ReleaseError,
    VersionUpdate,
    apply_version_updates,
    declared_plugin,
    describe_plugins,
    download_release,
    downloaded_plugins,
    find_item,
    github_token,
    print_version_updates,
    read_catalog_file,
    write_catalog_file,
)
from sailwind_mod_sync.catalog.custom import same_repo
from sailwind_mod_sync.catalog.github import parse_repo_url
from sailwind_mod_sync.catalog.mvc import merge_catalog
from sailwind_mod_sync.catalog.plugin_names import name_entries
from sailwind_mod_sync.constants import GITHUB_RAW_MODLIST, GITHUB_RAW_VERSIONS, JSDELIVR_MODLIST, JSDELIVR_VERSIONS
from sailwind_mod_sync.http_util import HttpClient, HttpError
from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.models import CatalogEntry, is_newer
from sailwind_mod_sync.paths import AppPaths


@dataclass(frozen=True)
class NamedMod:
    guid: str
    repo: str
    name: str
    plugin_folders: list[str]
    version: str


@dataclass(frozen=True)
class SkippedMod:
    guid: str
    repo: str
    reason: str


@dataclass
class NamingResult:
    named: list[NamedMod] = field(default_factory=list)
    skipped: list[SkippedMod] = field(default_factory=list)
    stopped: str = ""
    """Why reading stopped before every mod was read, or "" when it did not."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report the changes without writing the catalog")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    own_mods = read_catalog_file(MODLIST_FILE)
    own_versions = read_catalog_file(VERSIONS_FILE)
    http = HttpClient(github_token())
    try:
        mvc = merge_catalog(
            fetch_list(http, GITHUB_RAW_MODLIST, JSDELIVR_MODLIST),
            fetch_list(http, GITHUB_RAW_VERSIONS, JSDELIVR_VERSIONS),
        )
        targets, skipped = unnamed_mods(mvc, own_mods)
        result = name_mods(http, targets, progress=print)
    except HttpError as exc:
        print(f"Could not read the ModVersionChecker lists: {exc}", file=sys.stderr)
        return 2
    finally:
        http.close()
    result.skipped = skipped + result.skipped
    mods, versions = add_named_mods(own_mods, own_versions, result.named)
    updates = newer_versions(mvc, versions)
    print_report(result, updates)
    if (result.named or updates) and not args.dry_run:
        write_catalog_file(MODLIST_FILE, mods)
        write_catalog_file(VERSIONS_FILE, apply_version_updates(versions, updates))
        print(f"Updated {MODLIST_FILE.relative_to(REPO_ROOT)} and {VERSIONS_FILE.relative_to(REPO_ROOT)}")
    return 1 if result.stopped else 0


def fetch_list(http: HttpClient, primary: str, fallback: str) -> list:
    """Return the JSON list at ``primary``, or at ``fallback`` when ``primary`` cannot be read.

    Raises:
        HttpError: Neither URL returned a JSON list.
    """
    errors: list[str] = []
    for url in (primary, fallback):
        try:
            data, _, _ = http.get_json(url)
        except HttpError as exc:
            errors.append(f"{url}: {exc}")
            continue
        if isinstance(data, list):
            return data
        errors.append(f"{url}: not a JSON list")
    raise HttpError("; ".join(errors))


def unnamed_mods(mvc: list[CatalogEntry], own_mods: list[dict]) -> tuple[list[CatalogEntry], list[SkippedMod]]:
    """Return the ModVersionChecker entries this catalog does not name, and those it cannot name.

    An entry cannot be named when its repository is not on GitHub or GitLab, or when this catalog lists one of its GUIDs
    from another repository.
    """
    entries = name_entries(mvc, merge_catalog(own_mods, []), {})
    targets: list[CatalogEntry] = []
    skipped: list[SkippedMod] = []
    for entry in entries:
        if not entry.derived_name:
            continue
        reason = _unnameable_reason(entry, own_mods)
        if reason:
            skipped.append(SkippedMod(entry.primary_guid, entry.repo, reason))
        else:
            targets.append(entry)
    return targets, skipped


def _unnameable_reason(entry: CatalogEntry, own_mods: list[dict]) -> str:
    try:
        parse_repo_url(entry.repo)
    except ValueError:
        return "not a GitHub or GitLab repository"
    for item in own_mods:
        if item.get("guid") in entry.guids and not same_repo(str(item.get("repo") or ""), entry.repo):
            return f"catalog/ModList.json lists {item['guid']} from {item.get('repo')}"
    return ""


def name_mods(
    http: HttpClient,
    targets: list[CatalogEntry],
    progress: Callable[[str], None] | None = None,
) -> NamingResult:
    """Name each of ``targets`` after its plugin in a download of its latest release."""
    result = NamingResult()
    with tempfile.TemporaryDirectory(prefix="sms-catalog-names-") as tmp:
        paths = AppPaths(Path(tmp))
        paths.ensure()
        store = LibraryStore(paths)
        for index, entry in enumerate(targets, 1):
            if progress:
                progress(f"[{index}/{len(targets)}] {entry.name} ({entry.primary_guid})")
            try:
                result.named.append(read_named_mod(store, http, entry))
            except BlockedError as exc:
                result.stopped = str(exc)
                break
            except ReleaseError as exc:
                result.skipped.append(SkippedMod(entry.primary_guid, entry.repo, str(exc)))
    return result


def read_named_mod(store: LibraryStore, http: HttpClient, entry: CatalogEntry) -> NamedMod:
    """Download the latest release of ``entry`` into ``store`` and return the mod named after its plugin.

    Raises:
        BlockedError: GitHub rejected the token or rate-limited the request.
        ReleaseError: The release could not be downloaded or holds no readable plugin with a GUID of ``entry``.
    """
    meta = download_release(store, http, entry.primary_guid, entry.repo)
    plugins = downloaded_plugins(store, meta, entry.repo)
    own = declared_plugin(plugins, entry.guids)
    if own is None or not own.name.strip():
        raise ReleaseError(
            f"no plugin declares {' or '.join(entry.guids)} in {meta.filename}; found: {describe_plugins(plugins)}"
        )
    return NamedMod(
        guid=own.guid,
        repo=entry.repo,
        name=own.name.strip(),
        plugin_folders=list(meta.plugin_folders),
        version=meta.version_raw or meta.version,
    )


def add_named_mods(
    own_mods: list[dict],
    own_versions: list[dict],
    named: list[NamedMod],
) -> tuple[list[dict], list[dict]]:
    """Return copies of the catalog lists with ``named`` added.

    A mod the catalog lists from the same repository without a name gets the name, and the plugin folders when it lists
    none. Any other mod is appended to both lists, in name order.
    """
    mods = [dict(item) for item in own_mods]
    versions = [dict(item) for item in own_versions]
    for mod in sorted(named, key=lambda item: item.name.lower()):
        existing = find_item(mods, mod.guid, mod.repo)
        if existing is None:
            mods.append(_mod_item(mod))
        else:
            existing["name"] = mod.name
            existing["plugin_folders"] = existing.get("plugin_folders") or list(mod.plugin_folders)
        if find_item(versions, mod.guid, mod.repo) is None:
            versions.append({"guid": mod.guid, "repo": mod.repo, "version": mod.version})
    return [_ordered(item) for item in mods], versions


def newer_versions(mvc: list[CatalogEntry], own_versions: list[dict]) -> list[VersionUpdate]:
    """Return the ModVersionChecker versions newer than those ``own_versions`` gives the same mods from the same
    repositories."""
    updates: list[VersionUpdate] = []
    for item in own_versions:
        guid = str(item.get("guid") or "")
        repo = str(item.get("repo") or "")
        current = str(item.get("version") or "")
        entry = next((entry for entry in mvc if guid in entry.guids and same_repo(entry.repo, repo)), None)
        if entry is not None and entry.available and entry.latest_raw and is_newer(entry.latest_raw, current):
            updates.append(VersionUpdate(guid, repo, current, entry.latest_raw))
    return updates


def _mod_item(mod: NamedMod) -> dict:
    return {"guid": mod.guid, "repo": mod.repo, "name": mod.name, "plugin_folders": list(mod.plugin_folders)}


def _ordered(item: dict) -> dict:
    keys = ["guid", "repo", "name", "plugin_folders"]
    return {key: item[key] for key in keys if key in item} | {key: value for key, value in item.items() if key not in keys}


def print_report(result: NamingResult, updates: list[VersionUpdate]) -> None:
    print(f"\nNamed {len(result.named)} mod(s):")
    for mod in sorted(result.named, key=lambda item: item.name.lower()):
        print(f"  {mod.name}  ({mod.guid}, {mod.version}, folders {', '.join(mod.plugin_folders) or '-'})")
    if result.skipped:
        print(f"\nSkipped {len(result.skipped)} mod(s):")
        for mod in result.skipped:
            print(f"  {mod.guid}  {mod.repo}: {mod.reason}")
    print_version_updates(updates)
    if result.stopped:
        print(f"\nStopped early: {result.stopped}")


if __name__ == "__main__":
    sys.exit(main())
