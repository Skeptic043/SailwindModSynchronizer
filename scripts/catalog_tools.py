"""Catalog file, GitHub and download helpers shared by the catalog maintenance scripts."""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sailwind_mod_sync.catalog.custom import same_repo
from sailwind_mod_sync.catalog.github import GitHubDownloadError, release_version
from sailwind_mod_sync.config import load_config
from sailwind_mod_sync.game.scan_plugins import DiscoveredPlugin, scan_plugins_dir
from sailwind_mod_sync.http_util import HttpClient, HttpError
from sailwind_mod_sync.library.download import ensure_mod_artifact
from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.models import ArtifactMeta, RemoteRelease, parse_mod_version
from sailwind_mod_sync.paths import AppPaths

REPO_ROOT = Path(__file__).resolve().parents[1]
MODLIST_FILE = REPO_ROOT / "catalog" / "ModList.json"
VERSIONS_FILE = REPO_ROOT / "catalog" / "release_versions.json"
BLOCKING_STATUS_CODES = (401, 403, 429)


@dataclass(frozen=True)
class VersionUpdate:
    guid: str
    repo: str
    old: str
    new: str


class BlockedError(RuntimeError):
    """GitHub rejected the token or rate-limited the requests."""


class ReleaseError(RuntimeError):
    """A release could not be checked, downloaded or read."""


def github_token() -> str:
    """Return the token from GITHUB_TOKEN, GH_TOKEN or the app's settings, or "" when none is set."""
    for variable in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(variable, "").strip()
        if token:
            return token
    return load_config(AppPaths()).token()


def raise_if_blocking(exc: HttpError) -> None:
    """Raise BlockedError when ``exc`` is GitHub rejecting the token or rate-limiting.

    Raises:
        BlockedError: ``exc`` has status 401, 403 or 429.
    """
    if exc.status_code in BLOCKING_STATUS_CODES:
        raise BlockedError(f"GitHub stopped the requests: {exc}") from exc


def first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def release_version_raw(release: RemoteRelease) -> str:
    """Return the version the app records for ``release``: its tag, or the version in its name when the tag has none."""
    return release.tag if parse_mod_version(release.tag) else release_version(release) or release.tag


def download_release(
    store: LibraryStore,
    http: HttpClient,
    guid: str,
    repo: str,
    *,
    version_raw: str | None = None,
    plugin_folders: list[str] | None = None,
) -> ArtifactMeta:
    """Download ``guid`` from ``repo`` into ``store`` the way the app does: the release of ``version_raw``, or the
    latest release without it.

    Raises:
        BlockedError: GitHub rejected the token or rate-limited the request.
        ReleaseError: The release could not be checked, downloaded or extracted.
    """
    try:
        return ensure_mod_artifact(
            store,
            http,
            guid=guid,
            repo=repo,
            version=parse_mod_version(version_raw) if version_raw else None,
            version_raw=version_raw,
            plugin_folders=plugin_folders or None,
        )
    except HttpError as exc:
        raise_if_blocking(exc)
        raise ReleaseError(f"could not check the release: {exc}") from exc
    except GitHubDownloadError as exc:
        if isinstance(exc.__cause__, HttpError):
            raise_if_blocking(exc.__cause__)
        raise ReleaseError(f"could not download the release: {first_line(exc.__cause__ or exc)}") from exc
    except (FileNotFoundError, RuntimeError, ValueError, OSError, zipfile.BadZipFile) as exc:
        raise ReleaseError(first_line(exc)) from exc


def downloaded_plugins(store: LibraryStore, meta: ArtifactMeta, repo: str) -> list[DiscoveredPlugin]:
    """Return the plugins ``store`` extracted from the download ``meta`` describes.

    Raises:
        ReleaseError: The extracted plugins could not be read.
    """
    extracted = store.mod_extracted(meta.guid, store.artifact_key(meta.guid, meta.version, repo))
    try:
        return list(scan_plugins_dir(extracted).discovered)
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"could not read the plugins: {exc}") from exc


def release_files(store: LibraryStore, meta: ArtifactMeta, repo: str) -> list[str]:
    """Return the file paths in the download ``meta`` describes: the entries of a zip, or the name of a DLL."""
    stored = store.mod_dir(meta.guid, store.artifact_key(meta.guid, meta.version, repo)) / meta.filename
    if stored.suffix.lower() != ".zip":
        return [meta.filename]
    with zipfile.ZipFile(stored) as archive:
        return sorted(name for name in archive.namelist() if not name.endswith("/"))


def declared_plugin(plugins: list[DiscoveredPlugin], guids: list[str]) -> DiscoveredPlugin | None:
    """Return the plugin whose BepInPlugin attribute declares one of ``guids``, or None when none does."""
    return next((plugin for plugin in plugins if plugin.declared_identity and plugin.guid in guids), None)


def describe_plugins(plugins: list[DiscoveredPlugin]) -> str:
    return ", ".join(f"{plugin.name} ({plugin.guid})" for plugin in plugins) or "no BepInEx plugin"


def apply_version_updates(versions: list[dict], updates: list[VersionUpdate]) -> list[dict]:
    """Return a copy of ``versions`` with the versions of ``updates``."""
    updated = [dict(item) for item in versions]
    for update in updates:
        item = find_item(updated, update.guid, update.repo)
        if item is not None:
            item["version"] = update.new
    return updated


def print_version_updates(updates: list[VersionUpdate]) -> None:
    if updates:
        print(f"\nUpdated {len(updates)} version(s):")
        for update in updates:
            print(f"  {update.guid}  {update.old} -> {update.new}")


def find_item(items: list[dict], guid: str, repo: str) -> dict | None:
    return next(
        (item for item in items if item.get("guid") == guid and same_repo(str(item.get("repo") or ""), repo)),
        None,
    )


def read_catalog_file(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a JSON list")
    return data


def format_catalog(items: list[dict]) -> str:
    """Return ``items`` as JSON in the layout of the catalog files: one key per line and lists on one line."""
    blocks = []
    for item in items:
        lines = [f"    {json.dumps(key)}: {_format_value(value)}" for key, value in item.items()]
        blocks.append("  {\n" + ",\n".join(lines) + "\n  }")
    return "[\n" + ",\n".join(blocks) + "\n]\n"


def _format_value(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def write_catalog_file(path: Path, items: list[dict]) -> None:
    """Write ``items`` to ``path`` in the catalog layout, keeping the file's line endings."""
    newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    path.write_text(format_catalog(items), encoding="utf-8", newline=newline)
