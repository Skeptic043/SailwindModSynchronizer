"""Update this repo's catalog versions from the latest releases of its mods' repositories.

Usage (from the repo root, with the project venv active):

    python scripts/update_catalog_versions.py
    python scripts/update_catalog_versions.py --dry-run

Each mod in catalog/ModList.json takes the latest release of its repository as its version in
catalog/release_versions.json when that release is newer and, downloaded the way the app downloads the mod, extracts to
the mod's plugin folders and holds a plugin declaring the mod's GUID. Newer releases that do not are reported and left
out. A GitHub token from GITHUB_TOKEN, GH_TOKEN or the app's settings raises the GitHub API rate limit.
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
    first_line,
    github_token,
    print_version_updates,
    raise_if_blocking,
    read_catalog_file,
    release_files,
    release_version_raw,
    write_catalog_file,
)
from sailwind_mod_sync.catalog.custom import repo_key
from sailwind_mod_sync.catalog.github import fetch_release
from sailwind_mod_sync.http_util import HttpClient, HttpError
from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.models import RemoteRelease, is_newer
from sailwind_mod_sync.paths import AppPaths


@dataclass(frozen=True)
class CatalogMod:
    guid: str
    repo: str
    plugin_folders: list[str]
    version: str


@dataclass(frozen=True)
class CheckedUpdate:
    update: VersionUpdate
    asset: str
    files: list[str]


@dataclass(frozen=True)
class RejectedRelease:
    guid: str
    repo: str
    reason: str


@dataclass
class ScanResult:
    updated: list[CheckedUpdate] = field(default_factory=list)
    rejected: list[RejectedRelease] = field(default_factory=list)
    unchanged: int = 0
    stopped: str = ""
    """Why the scan stopped before every mod was checked, or "" when it did not."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report the changes without writing the catalog")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    own_versions = read_catalog_file(VERSIONS_FILE)
    mods = catalog_mods(read_catalog_file(MODLIST_FILE), own_versions)
    http = HttpClient(github_token())
    try:
        result = scan_versions(http, mods, progress=print)
    finally:
        http.close()
    print_report(result)
    updates = [checked.update for checked in result.updated]
    if updates and not args.dry_run:
        write_catalog_file(VERSIONS_FILE, apply_version_updates(own_versions, updates))
        print(f"Updated {VERSIONS_FILE.relative_to(REPO_ROOT)}")
    return 1 if result.stopped else 0


def catalog_mods(own_mods: list[dict], own_versions: list[dict]) -> list[CatalogMod]:
    """Return the mods of the catalog lists that have a version."""
    versions = {
        (str(item.get("guid")), repo_key(str(item.get("repo") or ""))): str(item.get("version") or "")
        for item in own_versions
    }
    mods: list[CatalogMod] = []
    for item in own_mods:
        guid = str(item.get("guid") or "")
        repo = str(item.get("repo") or "")
        version = versions.get((guid, repo_key(repo)))
        if guid and repo and version is not None:
            mods.append(CatalogMod(guid, repo, list(item.get("plugin_folders") or []), version))
    return mods


def scan_versions(
    http: HttpClient,
    mods: list[CatalogMod],
    progress: Callable[[str], None] | None = None,
) -> ScanResult:
    """Check the latest release of each of ``mods`` and keep the newer ones a download confirms."""
    result = ScanResult()
    releases: dict[str, RemoteRelease | str] = {}
    with tempfile.TemporaryDirectory(prefix="sms-catalog-versions-") as tmp:
        paths = AppPaths(Path(tmp))
        paths.ensure()
        store = LibraryStore(paths)
        for index, mod in enumerate(mods, 1):
            if progress:
                progress(f"[{index}/{len(mods)}] {mod.guid}")
            try:
                version = release_version_raw(latest_release(http, mod.repo, paths, releases))
                if not is_newer(version, mod.version):
                    result.unchanged += 1
                    continue
                result.updated.append(check_release(store, http, mod, version))
            except BlockedError as exc:
                result.stopped = str(exc)
                break
            except ReleaseError as exc:
                result.rejected.append(RejectedRelease(mod.guid, mod.repo, str(exc)))
    return result


def latest_release(
    http: HttpClient,
    repo: str,
    paths: AppPaths,
    releases: dict[str, RemoteRelease | str],
) -> RemoteRelease:
    """Return the latest release of ``repo``, checking each repository once per ``releases``.

    Raises:
        BlockedError: GitHub rejected the token or rate-limited the request.
        ReleaseError: The latest release could not be checked.
    """
    key = repo_key(repo)
    if key not in releases:
        try:
            releases[key] = fetch_release(http, repo, tag=None, paths=paths)
        except HttpError as exc:
            raise_if_blocking(exc)
            releases[key] = f"could not check the latest release: {exc}"
        except ValueError as exc:
            releases[key] = first_line(exc)
    release = releases[key]
    if isinstance(release, str):
        raise ReleaseError(release)
    return release


def check_release(store: LibraryStore, http: HttpClient, mod: CatalogMod, version: str) -> CheckedUpdate:
    """Download ``version`` of ``mod`` into ``store`` and return the update when it still installs that mod.

    Raises:
        BlockedError: GitHub rejected the token or rate-limited the request.
        ReleaseError: The release could not be downloaded, extracts to other plugin folders or declares no plugin with
            the mod's GUID.
    """
    meta = download_release(store, http, mod.guid, mod.repo, version_raw=version, plugin_folders=mod.plugin_folders)
    if mod.plugin_folders and list(meta.plugin_folders) != mod.plugin_folders:
        raise ReleaseError(
            f"{version}: {meta.filename} extracts to {', '.join(meta.plugin_folders) or 'no folder'} "
            f"instead of {', '.join(mod.plugin_folders)}"
        )
    plugins = downloaded_plugins(store, meta, mod.repo)
    if declared_plugin(plugins, [mod.guid]) is None:
        raise ReleaseError(f"{version}: no plugin declares {mod.guid} in {meta.filename}; found: {describe_plugins(plugins)}")
    return CheckedUpdate(VersionUpdate(mod.guid, mod.repo, mod.version, version), meta.filename, release_files(store, meta, mod.repo))


def print_report(result: ScanResult) -> None:
    print(f"\n{result.unchanged} mod(s) are up to date.")
    print_version_updates([checked.update for checked in result.updated])
    for checked in result.updated:
        print(f"    {checked.update.guid}: {checked.asset} holds {', '.join(checked.files)}")
    if result.rejected:
        print(f"\nLeft out {len(result.rejected)} mod(s):")
        for rejected in result.rejected:
            print(f"  {rejected.guid}  {rejected.repo}: {rejected.reason}")
    if result.stopped:
        print(f"\nStopped early: {result.stopped}")


if __name__ == "__main__":
    sys.exit(main())
