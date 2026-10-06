from __future__ import annotations

import json
import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sailwind_mod_sync.catalog.custom import (
    load_custom_catalog,
    hidden_key,
    remove_custom_entry,
    repo_key,
    save_custom_catalog,
    same_repo,
    upsert_custom_entry,
)
from sailwind_mod_sync.catalog.github import (
    canonicalize_repo_url,
    download_release_asset,
    fetch_readme,
    fetch_release,
    list_releases,
    parse_repo_url,
    release_version,
)
from sailwind_mod_sync.catalog.mvc import build_catalog, find_entry, load_cached_catalog, load_shared_catalog, refresh_catalog
from sailwind_mod_sync.catalog.scanned import store_scanned_versions
from sailwind_mod_sync.config import AppConfig, load_config, save_config
from sailwind_mod_sync.constants import DEFAULT_BEPINEX_VERSION
from sailwind_mod_sync.game.backup import BackupResult, RestoreResult, backup_bepinex_folder, inspect_bepinex_zip, restore_bepinex_folder
from sailwind_mod_sync.game.saves import (
    SaveRestoreResult,
    backup_saves_folder,
    default_saves_dir,
    inspect_saves_zip,
    restore_saves_folder,
)
from sailwind_mod_sync.game.bepinex import doorstop_installed, install_doorstop, write_doorstop_config
from sailwind_mod_sync.game.detect import detect_game_path, resolve_game_dir
from sailwind_mod_sync.game.launch import launch_modded, launch_vanilla
from sailwind_mod_sync.game.scan_plugins import discover_local_file, scan_plugins_dir
from sailwind_mod_sync.http_util import HttpClient, HttpError, ProgressFn
from sailwind_mod_sync.library.aliases import load_aliases, save_aliases
from sailwind_mod_sync.library.download import ensure_bepinex, ensure_mod_artifact
from sailwind_mod_sync.library.hashing import dir_size
from sailwind_mod_sync.library.special_mods import artifact_ready, coop_dll_search_path, known_repo_for
from sailwind_mod_sync.library.store import LibraryStore, artifact_source, source_matches
from sailwind_mod_sync.logutil import log_duration, setup_logging
from sailwind_mod_sync.models import (
    CatalogEntry,
    LibraryEntry,
    ModDetails,
    ModPack,
    PinnedMod,
    RemoteModInfo,
    catalog_mod_name,
    display_mod_name,
    parse_mod_version,
    version_key,
)
from sailwind_mod_sync.packs.instance import (
    ensure_instance_bepinex,
    sync_pack_plugins,
)
from sailwind_mod_sync.packs.modpack import PackStore
from sailwind_mod_sync.packs.share import encode_pack_share, parse_share_text
from sailwind_mod_sync.paths import AppPaths

log = logging.getLogger(__name__)


class TokenAuthError(RuntimeError):
    """Raised when a configured GitHub token is rejected (HTTP 401)."""


class BulkRollbackError(RuntimeError):
    """An incomplete rollback retained recovery files for the user."""


@dataclass
class CacheClearResult:
    freed_bytes: int
    failures: list[str] = field(default_factory=list)


class Manager:
    def __init__(
        self,
        paths: AppPaths | None = None,
        config: AppConfig | None = None,
        http: HttpClient | None = None,
    ) -> None:
        self.paths = paths or AppPaths()
        self.paths.ensure()
        self._setup_logging()
        self.config = config or load_config(self.paths)
        if not self.config.game_path:
            detected = detect_game_path()
            if detected:
                self.config.game_path = str(detected)
                self.save_config()
        self._owns_http = http is None
        self.http = http or HttpClient(self.config.token())
        self.library = LibraryStore(self.paths)
        self.packs = PackStore(self.paths)
        self.aliases = load_aliases(self.paths)
        self.catalog: list[CatalogEntry] = load_cached_catalog(self.paths) or []
        default_pack = self.packs.ensure_default()
        if not self.config.last_pack_id or not self.packs.exists(self.config.last_pack_id):
            self.config.last_pack_id = default_pack.id
            self.save_config()
        if not self.config.catalog_sources_restored:
            self.restore_catalog_sources()
            self.config.catalog_sources_restored = True
            self.save_config()

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def save_config(self) -> None:
        save_config(self.paths, self.config)

    def reload_http(self) -> None:
        if self._owns_http:
            self.http.close()
            self.http = HttpClient(self.config.token())

    def game_dir(self) -> Path | None:
        return resolve_game_dir(self.config.game_path)

    def resolve_bepinex_folder(self, pack_id: str | None = None) -> tuple[Path, str]:
        game = self.game_dir()
        if game is not None:
            game_bepinex = game / "BepInEx"
            if game_bepinex.is_dir() and any(game_bepinex.iterdir()):
                return game_bepinex, "game"
        if pack_id and self.packs.exists(pack_id):
            pack_bepinex = self.packs.instance_dir(pack_id) / "BepInEx"
            if pack_bepinex.is_dir() and any(pack_bepinex.iterdir()):
                return pack_bepinex, "pack"
        raise FileNotFoundError(
            "No BepInEx folder found in the Sailwind directory or the selected ModPack."
        )

    def backup_bepinex(
        self,
        dest: Path,
        pack_id: str | None = None,
        source: Path | None = None,
        progress: ProgressFn | None = None,
    ) -> BackupResult:
        if source is None:
            source, _kind = self.resolve_bepinex_folder(pack_id)
        if progress:
            progress(f"Zipping {source}…")
        return backup_bepinex_folder(source, dest, progress=progress)

    def resolve_bepinex_restore_target(self, pack_id: str | None = None) -> tuple[Path, str]:
        game = self.game_dir()
        if game is not None:
            return game / "BepInEx", "game"
        if pack_id and self.packs.exists(pack_id):
            return self.packs.instance_dir(pack_id) / "BepInEx", "pack"
        raise FileNotFoundError("Set the Sailwind folder in Settings, or select a ModPack.")

    def restore_bepinex(
        self,
        archive: Path,
        pack_id: str | None = None,
        dest: Path | None = None,
        progress: ProgressFn | None = None,
    ) -> RestoreResult:
        inspect_bepinex_zip(archive)
        if dest is None:
            dest, _kind = self.resolve_bepinex_restore_target(pack_id)
        return restore_bepinex_folder(archive, dest, progress=progress)

    def saves_dir(self) -> Path:
        return default_saves_dir()

    def backup_saves(
        self,
        dest: Path,
        source: Path | None = None,
        progress: ProgressFn | None = None,
    ) -> BackupResult:
        if source is None:
            source = self.saves_dir()
        if progress:
            progress(f"Zipping {source}…")
        return backup_saves_folder(source, dest, progress=progress)

    def restore_saves(
        self,
        archive: Path,
        dest: Path | None = None,
        safety_dest: Path | None = None,
        progress: ProgressFn | None = None,
    ) -> SaveRestoreResult:
        inspect_saves_zip(archive)
        if dest is None:
            dest = self.saves_dir()
        if safety_dest is None:
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safety_dest = self.paths.backups_dir / f"Saves-before-restore-{stamp}.zip"
        return restore_saves_folder(archive, dest, safety_dest=safety_dest, progress=progress)

    def refresh_catalog(self, progress: ProgressFn | None = None) -> list[CatalogEntry]:
        self.catalog = self.fetch_catalog(progress=progress)
        return self.catalog

    def fetch_catalog(self, progress: ProgressFn | None = None) -> list[CatalogEntry]:
        """Download the shared catalogs into the cache and return the merged catalog without applying it."""
        return refresh_catalog(self.paths, self.http, progress=progress)

    def _rebuild_catalog(self, custom: list[CatalogEntry]) -> None:
        self.catalog = build_catalog(self.paths, load_shared_catalog(self.paths), custom)

    def scan_updates(self, live: bool = False, progress: ProgressFn | None = None) -> dict[str, str]:
        if not self.catalog:
            self.refresh_catalog(progress=progress)
        latest: dict[str, str] = {}
        for entry in self.catalog:
            if entry.latest_raw:
                latest[entry.primary_guid] = entry.latest_raw
                for guid in entry.guids:
                    latest[guid] = entry.latest_raw
        if not live:
            return latest

        seen_repos: set[str] = set()
        updated: list[CatalogEntry] = []
        try:
            for entry in self.catalog:
                if entry.repo in seen_repos:
                    continue
                seen_repos.add(entry.repo)
                raw = self._scan_latest_release(entry, progress)
                if not raw:
                    continue
                latest[entry.primary_guid] = raw
                for guid in entry.guids:
                    latest[guid] = raw
                entry.latest_raw = raw
                entry.latest_version = parse_mod_version(raw)
                entry.available = bool(entry.latest_version)
                updated.append(entry)
        finally:
            if updated:
                store_scanned_versions(self.paths, updated)
        return latest

    def _scan_latest_release(self, entry: CatalogEntry, progress: ProgressFn | None) -> str:
        """Return the latest release version of the entry's repository, or "" when it cannot be checked.

        Raises:
            TokenAuthError: GitHub rejected the configured token.
        """
        try:
            parse_repo_url(entry.repo)
        except ValueError:
            return ""
        try:
            release = fetch_release(self.http, entry.repo, tag=None, paths=self.paths, progress=progress)
        except HttpError as exc:
            if exc.status_code == 401 and self.config.token():
                raise TokenAuthError(
                    "Your GitHub token is invalid or expired (GitHub returned HTTP 401). "
                    "Fix it in Settings."
                ) from exc
            log.warning("Live update check failed for %s: %s", entry.repo, exc)
            return ""
        except Exception as exc:
            log.warning("Live update check failed for %s: %s", entry.repo, exc)
            return ""
        return release.tag if parse_mod_version(release.tag) else release_version(release) or release.tag

    def add_catalog_repo(self, repo_url: str, progress: ProgressFn | None = None) -> list[CatalogEntry]:
        repo = canonicalize_repo_url(repo_url)
        ref = parse_repo_url(repo)
        if progress:
            progress(f"Checking {ref.full_path}…")
        release = fetch_release(self.http, repo, tag=None, paths=self.paths, progress=progress)
        version_raw = release.tag if parse_mod_version(release.tag) else release_version(release) or release.tag
        version = parse_mod_version(version_raw)
        assets = [
            asset
            for asset in release.assets
            if asset.name
            and asset.download_url
            and asset.name.lower().endswith((".zip", ".dll"))
            and "source" not in asset.name.lower()
        ]
        if not assets:
            names = ", ".join(asset.name for asset in release.assets if asset.name) or "none"
            raise FileNotFoundError(
                f"Release has no zip or dll download (files: {names}). "
                "This GitHub release only has source code, or no files at all."
            )
        discovered = []
        with tempfile.TemporaryDirectory(prefix="sms-catalog-") as tmp:
            for index, asset in enumerate(assets):
                dest = Path(tmp) / (asset.name or f"artifact-{index}")
                if progress:
                    progress(f"Reading {asset.name}…")
                download_release_asset(
                    self.http,
                    asset,
                    dest,
                    progress=progress,
                    release_url=release.html_url,
                    repo_url=repo,
                )
                hints = (ref.repo, asset.name or "")
                discovered.extend(discover_local_file(dest, catalog=self.catalog, hints=hints))
        custom = load_custom_catalog(self.paths)
        added: list[CatalogEntry] = []
        seen: set[str] = set()
        skipped: list[str] = []
        for unit in discovered:
            guid = (getattr(unit, "guid", None) or "").strip()
            if not guid or guid in seen:
                continue
            existing = find_entry(self.catalog, guid, repo)
            if existing is not None and not existing.custom:
                skipped.append(existing.name or existing.primary_guid)
                continue
            seen.add(guid)
            folder = (getattr(unit, "name", None) or "").strip()
            entry = CatalogEntry(
                repo=repo,
                guids=[guid],
                primary_guid=guid,
                name=folder or catalog_mod_name(guid),
                latest_raw=version_raw,
                latest_version=version,
                available=bool(version),
                custom=True,
                plugin_folders=[folder] if folder else [],
            )
            custom = upsert_custom_entry(custom, entry)
            added.append(entry)
        if not added and not discovered:
            fallback = _guids_from_discovered([], ref)
            guid = fallback[0]
            entry = CatalogEntry(
                repo=repo,
                guids=fallback,
                primary_guid=guid,
                name=ref.repo,
                latest_raw=version_raw,
                latest_version=version,
                available=bool(version),
                custom=True,
            )
            custom = upsert_custom_entry(custom, entry)
            added.append(entry)
        if not added:
            known = ", ".join(dict.fromkeys(skipped))
            extra = f" as {known}" if known else ""
            raise ValueError(f"All plugins from {repo} are already in the catalog{extra}")
        save_custom_catalog(self.paths, custom)
        self._rebuild_catalog(custom)
        resolved: list[CatalogEntry] = []
        for entry in added:
            found = find_entry(self.catalog, entry.primary_guid, entry.repo)
            resolved.append(found or entry)
        log.info("Added %s catalog plugin(s) from %s", len(resolved), repo)
        return resolved

    def remove_catalog_repo(self, guid: str, repo: str = "") -> None:
        custom = remove_custom_entry(load_custom_catalog(self.paths), guid, repo)
        save_custom_catalog(self.paths, custom)
        self._rebuild_catalog(custom)
        log.info("Removed custom catalog entry %s", guid)

    def hide_catalog_mod(self, guid: str, repo: str = "") -> None:
        """Hide the catalog row of ``guid`` from ``repo``; without ``repo``, hide every shared row of the mod."""
        text = guid.strip()
        if not text:
            return
        key = hidden_key(text, repo)
        if key in self.config.hidden_catalog_mods:
            return
        self.config.hidden_catalog_mods.append(key)
        self.save_config()
        log.info("Hidden catalog mod %s", key)

    def unhide_catalog_mod(self, key: str) -> None:
        """Remove ``key``, a GUID or a GUID with its repository, from the hidden catalog mods."""
        hidden = [item for item in self.config.hidden_catalog_mods if item != key]
        if hidden == self.config.hidden_catalog_mods:
            return
        self.config.hidden_catalog_mods = hidden
        self.save_config()
        log.info("Unhid catalog mod %s", key)

    def restore_catalog_sources(self) -> list[CatalogEntry]:
        """Add custom catalog entries for the sources of catalog mods that packs or downloads use but the catalog lacks.

        Return the added entries, at most one per mod and repository, each at the newest version in use.
        """
        newest: dict[tuple[str, str], PinnedMod] = {}
        for pinned in self._pins_and_downloads():
            if not pinned.repo or find_entry(self.catalog, pinned.guid) is None:
                continue
            if find_entry(self.catalog, pinned.guid, pinned.repo) is not None:
                continue
            key = (pinned.guid, repo_key(pinned.repo))
            current = newest.get(key)
            if current is None or version_key(pinned.version) > version_key(current.version):
                newest[key] = pinned
        if not newest:
            return []
        custom = load_custom_catalog(self.paths)
        added = [self._catalog_entry_from_pin(pinned) for pinned in newest.values()]
        for entry in added:
            custom = upsert_custom_entry(custom, entry)
        save_custom_catalog(self.paths, custom)
        self._rebuild_catalog(custom)
        sources = ", ".join(f"{entry.primary_guid} from {entry.repo}" for entry in added)
        log.info("Restored %s catalog source(s): %s", len(added), sources)
        return added

    def _pins_and_downloads(self) -> list[PinnedMod]:
        pins = [pinned for pack in self.packs.list_packs() for pinned in pack.mods]
        for item in self.library.list_mods():
            source = artifact_source(item.meta)
            if source:
                pins.append(
                    PinnedMod(
                        guid=item.guid,
                        version=item.meta.version,
                        repo=source,
                        plugin_folders=list(item.meta.plugin_folders),
                        version_raw=item.meta.version_raw,
                    )
                )
        return pins

    def ensure_catalog_mods(self, mods: list[PinnedMod]) -> list[CatalogEntry]:
        if not self.catalog:
            self.catalog = load_cached_catalog(self.paths) or []
        custom = load_custom_catalog(self.paths)
        working = list(self.catalog)
        added: list[CatalogEntry] = []
        for pinned in mods:
            guid = (pinned.guid or "").strip()
            if not guid or find_entry(working, guid, pinned.repo) is not None:
                continue
            entry = self._catalog_entry_from_pin(pinned)
            custom = upsert_custom_entry(custom, entry)
            working.append(entry)
            added.append(entry)
        if not added:
            return []
        save_custom_catalog(self.paths, custom)
        self._rebuild_catalog(custom)
        resolved: list[CatalogEntry] = []
        for entry in added:
            resolved.append(find_entry(self.catalog, entry.primary_guid, entry.repo) or entry)
        log.info("Added %s imported mod(s) to the catalog", len(resolved))
        return resolved

    def _catalog_entry_from_pin(self, pinned: PinnedMod) -> CatalogEntry:
        meta = self.library.read_mod_meta(pinned.guid, self.library.pinned_key(pinned))
        repo = (pinned.repo or (meta.repo if meta else "") or "").strip()
        if repo:
            try:
                repo = canonicalize_repo_url(repo)
            except ValueError:
                pass
        folders = list(pinned.plugin_folders)
        if not folders and meta:
            folders = list(meta.plugin_folders)
        raw = (pinned.version_raw or pinned.version or "").strip() or None
        version = parse_mod_version(raw)
        name = folders[0] if folders else catalog_mod_name(pinned.guid)
        return CatalogEntry(
            repo=repo,
            guids=[pinned.guid],
            primary_guid=pinned.guid,
            name=name,
            latest_raw=raw,
            latest_version=version,
            available=bool(version),
            custom=True,
            plugin_folders=folders,
        )

    def mod_display_name(
        self,
        guid: str,
        *,
        alias: str | None = None,
        plugin_folders: list[str] | None = None,
        repo: str = "",
        library_entries: list[LibraryEntry] | None = None,
        packs: list[ModPack] | None = None,
    ) -> str:
        """Pass ``library_entries`` and ``packs`` when naming many mods, so the
        library and pack manifests are read once instead of once per mod."""
        catalog = find_entry(self.catalog, guid)
        folders = plugin_folders
        repo_url = repo
        if folders is None or not repo_url:
            entries = self.library.list_mods() if library_entries is None else library_entries
            for entry in entries:
                if entry.guid != guid:
                    continue
                if folders is None:
                    folders = list(entry.meta.plugin_folders)
                repo_url = repo_url or entry.meta.repo
                break
        if not repo_url:
            for pack in self.packs.list_packs() if packs is None else packs:
                pinned = pack.find_mod(guid)
                if pinned is None:
                    continue
                if folders is None:
                    folders = list(pinned.plugin_folders)
                repo_url = repo_url or pinned.repo
                break
        return display_mod_name(
            guid,
            alias=self.aliases.get(guid, "") if alias is None else alias,
            catalog_name=catalog.name if catalog else "",
            catalog_shared=bool(catalog and len(catalog.guids) > 1),
            plugin_folders=folders,
            repo=repo_url or (catalog.repo if catalog else ""),
        )

    def set_mod_alias(self, guid: str, alias: str) -> str:
        guid = (guid or "").strip()
        if not guid:
            raise ValueError("Mod GUID is empty")
        text = (alias or "").strip()
        default = self.mod_display_name(guid, alias="")
        if not text or text == default:
            self.aliases.pop(guid, None)
        else:
            self.aliases[guid] = text
        save_aliases(self.paths, self.aliases)
        log.info("Set display alias for %s to %r", guid, self.aliases.get(guid, ""))
        return self.mod_display_name(guid)

    def local_mod_details(self, guid: str, version: str) -> ModDetails:
        meta = self.library.read_mod_meta(guid, version)
        if meta is None:
            raise FileNotFoundError(f"{guid} {version} is not in the library")
        installed = sorted(
            {entry.version for entry in self.library.list_mods() if entry.guid == guid},
            key=version_key,
            reverse=True,
        )
        catalog = find_entry(self.catalog, guid, meta.repo)
        name = self.mod_display_name(
            guid,
            plugin_folders=list(meta.plugin_folders),
            repo=meta.repo or (catalog.repo if catalog else ""),
        )
        pack_pins: list[tuple[str, str]] = []
        for pack in self.packs.list_packs():
            pinned = pack.find_mod(guid)
            if pinned:
                pack_pins.append((pack.name, pinned.version))
        size = 0
        for entry in self.library.list_mods():
            if entry.guid == guid and entry.version == version:
                size = entry.size_bytes
                break
        return ModDetails(
            guid=guid,
            version=version,
            name=name,
            repo=meta.repo or (catalog.repo if catalog else ""),
            source_url=meta.source_url,
            filename=meta.filename,
            sha256=meta.sha256,
            downloaded_at=meta.downloaded_at,
            plugin_folders=list(meta.plugin_folders),
            size_bytes=size,
            installed_versions=installed,
            catalog_latest=(catalog.latest_raw if catalog else None),
            pack_pins=pack_pins,
        )

    def catalog_mod_details(self, guid: str, repo: str = "") -> ModDetails:
        catalog = find_entry(self.catalog, guid, repo)
        if catalog is None:
            raise FileNotFoundError(f"{guid} is not in the catalog")
        guids = set(catalog.guids) | {catalog.primary_guid, guid}
        matches = [
            item
            for item in self.library.list_mods()
            if item.guid in guids and source_matches(item.meta, catalog.repo)
        ]
        if matches:
            newest = max(matches, key=lambda item: version_key(item.version))
            return self.local_mod_details(newest.guid, newest.version)
        pack_pins: list[tuple[str, str]] = []
        for pack in self.packs.list_packs():
            pinned = None
            for candidate in catalog.guids:
                pinned = pack.find_mod(candidate)
                if pinned is not None:
                    break
            if pinned:
                pack_pins.append((pack.name, pinned.version))
        return ModDetails(
            guid=catalog.primary_guid,
            version=catalog.latest_version or catalog.latest_raw or "",
            name=self.mod_display_name(catalog.primary_guid, repo=catalog.repo),
            repo=catalog.repo,
            source_url="",
            filename="",
            sha256="",
            downloaded_at="",
            plugin_folders=[],
            size_bytes=0,
            installed_versions=[],
            catalog_latest=catalog.latest_raw,
            pack_pins=pack_pins,
        )

    def fetch_remote_mod_info(self, repo: str, progress: ProgressFn | None = None) -> RemoteModInfo:
        info = RemoteModInfo()
        try:
            releases = list_releases(self.http, repo, limit=12, progress=progress)
            tags = [
                item.tag if parse_mod_version(item.tag) else (item.name or item.tag)
                for item in releases
                if item.tag or item.name
            ]
            info.release_tags = tags
            info.latest_tag = tags[0] if tags else ""
        except Exception as exc:
            log.warning("Could not list releases for %s: %s", repo, exc)
            info.releases_error = str(exc).strip() or repr(exc)
        try:
            info.readme = fetch_readme(self.http, repo, progress=progress)
        except Exception as exc:
            log.warning("Could not fetch README for %s: %s", repo, exc)
            info.readme_error = str(exc).strip() or repr(exc)
        return info

    def install_mod(
        self,
        pack_id: str,
        guid: str,
        repo: str | None = None,
        version: str | None = None,
        version_raw: str | None = None,
        progress: ProgressFn | None = None,
    ) -> PinnedMod:
        self._check_bulk_recovery(pack_id)
        entry = find_entry(self.catalog, guid, repo or "")
        repo = repo or (entry.repo if entry else "") or (known_repo_for(guid) or "")
        if not repo:
            raise ValueError(f"No repository known for {guid}")
        if version is None and entry:
            version = entry.latest_version
            version_raw = version_raw or entry.latest_raw
        if not version and version_raw:
            version = parse_mod_version(version_raw)
        if progress:
            progress(f"Fetching {guid} from {repo}…")
        folders = list(entry.plugin_folders) if entry and entry.plugin_folders else None
        meta = ensure_mod_artifact(
            self.library,
            self.http,
            guid=guid,
            repo=repo,
            version=version,
            version_raw=version_raw,
            plugin_folders=folders,
            progress=progress,
        )
        return self.add_library_mod_to_pack(
            pack_id,
            guid,
            self.library.artifact_key(guid, meta.version, repo),
            repo=repo,
            progress=progress,
        )

    def list_remote_mod_versions(
        self,
        repo: str,
        progress: ProgressFn | None = None,
    ) -> list[tuple[str, str]]:
        releases = list_releases(self.http, repo, limit=40, progress=progress)
        rows: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in releases:
            version = release_version(item)
            if not version or version in seen:
                continue
            seen.add(version)
            raw = item.tag if parse_mod_version(item.tag) else (item.name or item.tag)
            rows.append((version, raw))
        return rows

    def set_pack_mod_version(
        self,
        pack_id: str,
        guid: str,
        version: str,
        version_raw: str | None = None,
        *,
        repo: str = "",
        progress: ProgressFn | None = None,
    ) -> PinnedMod:
        pack = self.packs.get(pack_id)
        pinned = pack.find_mod(guid)
        repo = repo or (pinned.repo if pinned else "") or None
        key = self.library.artifact_key(guid, version, repo or "")
        if self.library.has_mod(guid, key):
            return self.add_library_mod_to_pack(
                pack_id,
                guid,
                key,
                repo=repo,
                progress=progress,
            )
        return self.install_mod(
            pack_id,
            guid,
            repo=repo,
            version=version,
            version_raw=version_raw or version,
            progress=progress,
        )

    def add_library_mod_to_pack(
        self,
        pack_id: str,
        guid: str,
        key: str,
        *,
        repo: str | None = None,
        progress: ProgressFn | None = None,
    ) -> PinnedMod:
        """Pin the library artifact ``key`` of ``guid`` on the pack and copy it into the pack's plugins.

        The pin's repository is ``repo``, or else the repository the artifact was downloaded from.
        """
        self._check_bulk_recovery(pack_id)
        meta = self.library.read_mod_meta(guid, key)
        if meta is None:
            raise FileNotFoundError(f"{guid} {key} is not in the library")
        if progress:
            progress(f"Adding {guid} {meta.version} to the pack…")
        pack = self.packs.get(pack_id)
        previous = pack.find_mod(guid)
        pinned = PinnedMod(
            guid=guid,
            version=meta.version,
            repo=repo or artifact_source(meta) or meta.repo,
            enabled=previous.enabled if previous else True,
            plugin_folders=list(meta.plugin_folders),
            version_raw=meta.version_raw,
        )
        self._change_pack_mod(
            pack_id, pinned, self.library.mod_extracted(guid, key),
            install=pinned.enabled, require_artifact=True,
        )
        return pinned

    def set_mod_repo(self, guid: str, repo: str, pack_id: str | None = None) -> str:
        """Set the repository of ``guid`` and return its canonical URL.

        With ``pack_id``, the pin of that pack changes and the pins of other packs change only when they have
        no repository; without it, every pin changes. Library artifacts downloaded from a repository keep it.
        """
        page = canonicalize_repo_url(repo)
        for pack in self.packs.list_packs():
            pinned = pack.find_mod(guid)
            if pinned is None or not _takes_repo(pack, pinned, pack_id):
                continue
            pinned.repo = page
            self.packs.save(pack)
        for entry in self.library.list_mods():
            if entry.guid != guid or artifact_source(entry.meta):
                continue
            meta = self.library.read_mod_meta(entry.guid, entry.version)
            if meta is None:
                continue
            meta.repo = page
            self.library.write_mod_meta(meta, entry.version)
        return page

    def associate_mod(
        self,
        guid: str,
        version: str,
        *,
        catalog_entry: CatalogEntry | None = None,
        repo: str = "",
        pack_id: str | None = None,
    ) -> str:
        """Link the library artifact ``guid`` ``version`` and its pins to a catalog entry or repository.

        Pins in other packs than ``pack_id`` keep a repository they already have. Return the mod's GUID,
        which becomes the catalog entry's when the artifact had a different one.
        """
        entry = catalog_entry
        page = ""
        if entry is None and repo:
            page = canonicalize_repo_url(repo)
            entry = next((item for item in self.catalog if same_repo(item.repo, page)), None)
            if entry is None:
                return self.set_mod_repo(guid, page, pack_id)
        if entry is None:
            raise ValueError("No catalog entry or repository to associate")
        page = canonicalize_repo_url(entry.repo)
        new_guid = guid if guid in entry.guids or guid == entry.primary_guid else entry.primary_guid
        if self.library.has_mod(guid, version):
            self.library.rekey_mod(guid, version, new_guid, repo=page)
        elif self.library.has_mod(new_guid, version):
            meta = self.library.read_mod_meta(new_guid, version)
            if meta is not None:
                meta.repo = page
                self.library.write_mod_meta(meta, version)
        else:
            self.set_mod_repo(guid, page, pack_id)
        if new_guid != guid and guid in self.aliases:
            self.aliases[new_guid] = self.aliases.pop(guid)
            save_aliases(self.paths, self.aliases)
        for pack in self.packs.list_packs():
            pinned = pack.find_mod(guid)
            if pinned is None:
                continue
            takes_repo = _takes_repo(pack, pinned, pack_id)
            if new_guid == guid:
                if takes_repo:
                    pinned.repo = page
                    self.packs.save(pack)
                continue
            existing = pack.find_mod(new_guid)
            remaining = [mod for mod in pack.mods if mod.guid != guid]
            pinned.guid = new_guid
            if takes_repo:
                pinned.repo = page
            if existing is None:
                remaining.append(pinned)
            else:
                remaining = [mod for mod in remaining if mod.guid != new_guid]
                existing.version = pinned.version
                existing.version_raw = pinned.version_raw or existing.version_raw
                existing.repo = page if takes_repo else existing.repo or pinned.repo
                existing.enabled = pinned.enabled
                existing.plugin_folders = list(pinned.plugin_folders or existing.plugin_folders)
                remaining.append(existing)
            remaining.sort(key=lambda mod: mod.guid.lower())
            pack.mods = remaining
            self.packs.save(pack)
        return new_guid

    def missing_mods(self, pack: ModPack | None) -> list[PinnedMod]:
        if pack is None:
            return []
        return [mod for mod in pack.mods if not artifact_ready(self.library, mod.guid, self.library.pinned_key(mod))]

    def undownloadable_mods(self, pack: ModPack | None) -> list[PinnedMod]:
        """Return the pack's missing mods that preparing the pack cannot download, as no repository is known."""
        return [mod for mod in self.missing_mods(pack) if not self._download_repo(mod)]

    def _download_repo(self, pinned: PinnedMod) -> str:
        entry = find_entry(self.catalog, pinned.guid)
        return pinned.repo or (entry.repo if entry else "") or (known_repo_for(pinned.guid) or "")

    def library_versions(
        self,
        guid: str,
        repo: str = "",
        entries: list[LibraryEntry] | None = None,
        *,
        strict: bool = False,
    ) -> list[tuple[str, str]]:
        """Return ``(version, raw version)`` of the library artifacts of ``guid`` usable for ``repo``, newest first.

        ``entries`` is the library listing to search, read from the library when omitted. With ``strict``, only
        artifacts downloaded from ``repo`` count, leaving out files imported from elsewhere.
        """
        rows = [
            (item.meta.version, item.meta.version_raw or item.meta.version)
            for item in (self.library.list_mods() if entries is None else entries)
            if item.guid == guid
            and item.version == self.library.artifact_key(guid, item.meta.version, repo)
            and (not strict or same_repo(artifact_source(item.meta), repo))
        ]
        rows.sort(key=lambda pair: version_key(pair[0]), reverse=True)
        return rows

    def import_local_mod(
        self,
        path: Path,
        pack_id: str,
        progress: ProgressFn | None = None,
        *,
        guid: str | None = None,
    ) -> PinnedMod:
        self._check_bulk_recovery(pack_id)
        path = Path(path)
        if progress:
            progress(f"Reading {path.name}…")
        found = discover_local_file(path, self.catalog)
        if not found:
            raise ValueError(f"No plugin DLL found in {path.name}")
        plugin = found[0]
        pack = self.packs.get(pack_id)
        use_guid = guid or plugin.guid
        use_version = plugin.version
        use_raw = plugin.version_raw or plugin.version
        previous = pack.find_mod(use_guid)
        if previous and (not use_version or use_version in ("0", "0.0.0")):
            use_version = previous.version
            use_raw = previous.version_raw or previous.version
        source = str(path)
        if path.suffix.lower() == ".dll":
            if progress:
                progress(f"Importing {use_guid} {use_version}…")
            meta = self.library.ingest_plugin_paths(
                use_guid,
                use_version,
                [path],
                version_raw=use_raw,
                repo=plugin.repo or (previous.repo if previous else ""),
                source_url=source,
            )
        elif path.suffix.lower() == ".zip":
            if progress:
                progress(f"Importing {use_guid} {use_version}…")
            meta = self.library.ingest_mod_zip(
                use_guid,
                use_version,
                path,
                version_raw=use_raw,
                repo=plugin.repo or (previous.repo if previous else ""),
                source_url=source,
                filename=path.name,
            )
        else:
            raise ValueError(f"Unsupported file type: {path.suffix}. Use a .dll or .zip.")
        pinned = PinnedMod(
            guid=meta.guid,
            version=meta.version,
            repo=meta.repo,
            enabled=previous.enabled if previous else True,
            plugin_folders=list(meta.plugin_folders),
            version_raw=meta.version_raw,
        )
        self._change_pack_mod(
            pack_id, pinned, self.library.mod_extracted(meta.guid, meta.version),
            install=pinned.enabled, require_artifact=True,
        )
        return pinned

    def update_mod(self, pack_id: str, guid: str, progress: ProgressFn | None = None) -> PinnedMod:
        pack = self.packs.get(pack_id)
        pinned = pack.find_mod(guid)
        entry = find_entry(self.catalog, guid, pinned.repo if pinned else "")
        repo = (pinned.repo if pinned else "") or (entry.repo if entry else "")
        version_raw = entry.latest_raw if entry else None
        version = entry.latest_version if entry else None
        return self.install_mod(
            pack_id,
            guid,
            repo=repo,
            version=version,
            version_raw=version_raw,
            progress=progress,
        )

    def update_mods(
        self,
        pack_id: str,
        guids: list[str],
        progress: ProgressFn | None = None,
    ) -> tuple[list[PinnedMod], list[str]]:
        """Update each mod of ``guids`` in the pack, carrying on past mods that fail.

        Return the updated pins and a ``"guid: reason"`` line for each mod that could not be updated.
        """
        updated: list[PinnedMod] = []
        failures: list[str] = []
        for index, guid in enumerate(guids, start=1):
            if progress:
                progress(f"Updating {guid} ({index}/{len(guids)})…")
            try:
                updated.append(self.update_mod(pack_id, guid, progress=progress))
            except Exception as exc:
                log.warning("Could not update %s in pack %s: %s", guid, pack_id, exc)
                failures.append(f"{guid}: {str(exc).strip() or type(exc).__name__}")
        return updated, failures

    def set_mod_enabled(self, pack_id: str, guid: str, enabled: bool) -> ModPack:
        self._check_bulk_recovery(pack_id)
        self._set_bulk_mod_enabled(pack_id, guid, enabled)
        return self.packs.get(pack_id)

    def set_all_mods_enabled(
        self, pack_id: str, enabled: bool, progress: ProgressFn | None = None,
        *, on_changed: Callable[[str, bool], None] | None = None,
    ) -> tuple[int, list[str]]:
        """Change only differing pins, committing each mod after its files succeed."""
        return self.set_mods_enabled(pack_id, enabled, progress=progress, on_changed=on_changed)

    def set_mods_enabled(
        self, pack_id: str, enabled: bool, *, guids: set[str] | None = None,
        progress: ProgressFn | None = None,
        on_changed: Callable[[str, bool], None] | None = None,
    ) -> tuple[int, list[str]]:
        self._check_bulk_recovery(pack_id)
        pack = self.packs.get(pack_id)
        pending = [mod.guid for mod in pack.mods
                   if mod.enabled != enabled and (guids is None or mod.guid in guids)]
        failures: list[str] = []
        changed = 0
        verb = "Enabling" if enabled else "Disabling"
        for index, guid in enumerate(pending, 1):
            if progress:
                progress(f"{verb} mods: {index}/{len(pending)} — {pack.name}")
            try:
                self._set_bulk_mod_enabled(pack_id, guid, enabled)
                changed += 1
                if on_changed:
                    on_changed(guid, enabled)
            except Exception as exc:
                log.exception("Could not change %s in %s", guid, pack_id)
                failures.append(f"{guid}: {exc}")
                # A failed rollback needs manual recovery; preserve its files and
                # stop instead of performing further writes to this profile.
                if isinstance(exc, BulkRollbackError):
                    break
        return changed, failures

    def _set_bulk_mod_enabled(self, pack_id: str, guid: str, enabled: bool) -> None:
        pack = self.packs.get(pack_id)
        pinned = pack.find_mod(guid)
        if pinned is None:
            raise FileNotFoundError(guid)
        if pinned.enabled == enabled:
            return
        extracted = self.library.mod_extracted(guid, self.library.pinned_key(pinned))
        # A selection can precede installation. Only usable extracted files
        # can be staged here; retain missing selections and their folder names.
        install = enabled and extracted.is_dir() and any(extracted.rglob("*.dll"))
        pinned.enabled = enabled
        self._change_pack_mod(pack_id, pinned, extracted, install=install)

    def _change_pack_mod(
        self, pack_id: str, pinned: PinnedMod, extracted: Path, *,
        install: bool, require_artifact: bool = False,
    ) -> None:
        """Stage files before replacing a pin, retaining backups if rollback fails."""
        self._check_bulk_recovery(pack_id)
        pack = self.packs.get(pack_id)
        previous = pack.find_mod(pinned.guid)
        artifact_folders = (
            [item.name for item in extracted.iterdir() if item.is_dir()]
            if install or require_artifact else []
        )
        if require_artifact and not any(any((extracted / name).rglob("*.dll")) for name in artifact_folders):
            raise FileNotFoundError(f"No extracted plugin folders for {pinned.guid} {pinned.version}")
        folders = artifact_folders if install else []
        affected = set(previous.plugin_folders if previous else []) | set(folders)
        if any(not name or name in (".", "..") or Path(name).name != name
               or ":" in name or "\\" in name or "/" in name for name in affected):
            raise ValueError("Invalid plugin folder name.")
        plugins = self.packs.plugins_dir(pack_id)
        pack_dir = self.packs.pack_dir(pack_id)
        if (pack_dir.resolve() != self.paths.packs_dir.resolve() / pack_dir.name
                or plugins.resolve() != pack_dir.resolve() / "instance" / "BepInEx" / "plugins"):
            raise ValueError("Linked profile folders cannot be changed.")
        affected_names = {folder.casefold() for folder in affected}
        overlaps = sorted({name for other in pack.mods if other.guid != pinned.guid
                           for name in other.plugin_folders
                           if name.casefold() in affected_names})
        if overlaps:
            raise ValueError("Plugin folders are shared with another mod: " + ", ".join(overlaps))
        if any((plugins / name).resolve() != plugins.resolve() / name for name in affected):
            raise ValueError("Linked plugin folders cannot be changed.")
        # Use the actual old spelling once when a new artifact changes only case.
        present = sorted(
            item.name for item in plugins.iterdir() if item.name.casefold() in affected_names
        ) if plugins.is_dir() else []
        work = Path(tempfile.mkdtemp(prefix=".bulk-", dir=self.packs.pack_dir(pack_id)))
        staged = work / "staged"
        backup = work / "backup"
        moved: list[str] = []
        installed: list[str] = []
        preserve = False
        try:
            staged.mkdir()
            backup.mkdir()
            # Copy before touching the profile. Large files stay off the UI thread.
            for name in folders:
                shutil.copytree(extracted / name, staged / name)
            if folders:
                plugins.mkdir(parents=True, exist_ok=True)
            manifest = work / "modpack.json"
            original_manifest = self.packs.manifest_path(pack_id).read_bytes()
            (work / "original-modpack.json").write_bytes(original_manifest)
            if install or require_artifact:
                pinned.plugin_folders = artifact_folders
            pack.mods = [mod for mod in pack.mods if mod.guid != pinned.guid] + [pinned]
            pack.mods.sort(key=lambda mod: mod.guid.lower())
            planned_manifest = (json.dumps(pack.to_dict(), indent=2) + "\n").encode("utf-8")
            manifest.write_bytes(planned_manifest)
            (work / "planned-modpack.json").write_bytes(planned_manifest)
            # Write all recovery facts before the first profile mutation. File
            # presence remains meaningful even if interrupted between renames.
            plan = {
                "pack_id": pack_id, "guid": pinned.guid, "enabled": pinned.enabled,
                "profile_plugins": str(plugins),
                "profile_manifest": str(self.packs.manifest_path(pack_id)),
                "originally_present": present,
                "affected_folders": sorted(affected), "install_folders": folders,
            }
            (work / "transaction.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            marker = work / "recovery-required.txt"
            marker.write_text(
                f"Interrupted mod change: {pinned.guid} in profile {pack_id}.\n"
                "Do not play or change this profile until recovery is complete. Close the synchronizer first.\n"
                f"Before recovery, make a separate copy of this entire profile, including {work.name}.\n"
                "transaction.json records affected folders, which existed before the change, and which were to be installed.\n"
                "original-modpack.json and planned-modpack.json are the exact before/after manifest snapshots.\n"
                "Compare the profile's modpack.json with both snapshots: matching the original means its selection was not committed; "
                "matching the planned means it was committed. Neither match means later changes or damage: stop and seek help. "
                "A manifest match alone does not prove the plugin files are consistent.\n"
                "To restore the original state, inspect each affected folder in transaction.json:\n"
                "Case-only renames alias one folder on Windows. Remove a replacement spelling only when its "
                "original backup exists, before restoring that backup. Never remove an alias already restored.\n"
                "- If originally present and backup/<folder> exists, preserve any current plugin folder separately, "
                "then restore that backup folder to profile_plugins/<folder>.\n"
                "- If originally present but no backup exists, do not delete or replace the current folder. "
                "It may not have been moved yet, or may already have been restored. Verify it against a known-good copy; "
                "if missing or uncertain, stop and seek help.\n"
                "- If originally absent, preserve and remove any newly created profile_plugins/<folder>.\n"
                "Leave every unaffected folder alone. After the original folders are restored and verified, "
                "replace the profile manifest with original-modpack.json.\n"
                "Only after verifying both files and manifest may you remove recovery-required.txt and restart the synchronizer. "
                "Keep the separate copy until the recovered profile has been tested. Deleting the marker alone is not a repair.\n",
                encoding="utf-8",
            )
            preserve = True
            try:
                for name in present:
                    target = plugins / name
                    if target.exists():
                        target.replace(backup / name)
                        moved.append(name)
                for name in folders:
                    (staged / name).replace(plugins / name)
                    installed.append(name)
                # Atomic replacement also avoids a truncated manifest on save failure.
                manifest.replace(self.packs.manifest_path(pack_id))
                preserve = False
            except Exception as original:
                try:
                    for name in installed:
                        shutil.rmtree(plugins / name)
                    for name in moved:
                        (backup / name).replace(plugins / name)
                    active_manifest = self.packs.manifest_path(pack_id)
                    if active_manifest.read_bytes() != original_manifest:
                        restored_manifest = work / "restore-modpack.json"
                        restored_manifest.write_bytes(original_manifest)
                        restored_manifest.replace(active_manifest)
                except Exception as rollback:
                    preserve = True
                    raise BulkRollbackError(
                        f"{original}; recovery failed: {rollback}. Backup retained at {work}. "
                        f"Close the synchronizer and follow {marker} before playing."
                    ) from rollback
                preserve = False
                raise
        finally:
            if not preserve:
                # A cleanup failure must not undo an already committed change.
                try:
                    (work / "recovery-required.txt").unlink(missing_ok=True)
                    shutil.rmtree(work)
                except OSError:
                    log.warning("Could not remove bulk staging folder %s", work, exc_info=True)

    def bulk_recovery_path(self, pack_id: str) -> Path | None:
        return next(self.packs.pack_dir(pack_id).glob(".bulk-*/recovery-required.txt"), None)

    def _check_bulk_recovery(self, pack_id: str) -> None:
        marker = self.bulk_recovery_path(pack_id)
        if marker is not None:
            raise BulkRollbackError(f"This profile needs recovery before playing or changing mods. See {marker}")

    def prepare_pack(self, pack_id: str, progress: ProgressFn | None = None) -> Path:
        self._check_bulk_recovery(pack_id)
        pack = self.packs.get(pack_id)
        with log_duration(log, f"prepare pack {pack_id} ({pack.name})"):
            if progress:
                progress("Preparing BepInEx…")
            bx_version, bx_extracted = ensure_bepinex(
                self.library,
                self.http,
                version=pack.bepinex or DEFAULT_BEPINEX_VERSION,
                progress=progress,
            )
            if pack.bepinex != bx_version:
                pack.bepinex = bx_version
                self.packs.save(pack)
            if progress:
                progress("Installing BepInEx into the pack…")
            instance = self.packs.instance_dir(pack_id)
            preloader = ensure_instance_bepinex(instance, bx_extracted)
            self.resolve_pack_artifacts(pack_id, progress=progress)
            return preloader

    def resolve_pack_artifacts(self, pack_id: str, progress: ProgressFn | None = None) -> None:
        pack = self.packs.get(pack_id)
        total = len(pack.mods)
        missing = 0
        log.info("Resolving %s mod(s) for pack %s (%s)", total, pack_id, pack.name)
        for index, pinned in enumerate(pack.mods, start=1):
            if progress:
                progress(f"Resolving {pinned.guid} ({index}/{total})…")
            if not pinned.repo:
                pinned.repo = self._download_repo(pinned)
                if pinned.repo:
                    log.info("Filled repo for %s: %s", pinned.guid, pinned.repo)
            key = self.library.pinned_key(pinned)
            if artifact_ready(self.library, pinned.guid, key):
                log.info("Library hit %s %s", pinned.guid, key)
                meta = self.library.read_mod_meta(pinned.guid, key)
                if meta and not pinned.plugin_folders:
                    pinned.plugin_folders = list(meta.plugin_folders)
                continue
            if not pinned.repo:
                missing += 1
                log.warning(
                    "Leaving %s %s as missing: not in the library and no repo",
                    pinned.guid,
                    pinned.version,
                )
                if progress:
                    progress(f"Missing {pinned.guid} — import a file later")
                continue
            try:
                log.info(
                    "Fetching %s %s from %s",
                    pinned.guid,
                    pinned.version,
                    pinned.repo,
                )
                ensure_mod_artifact(
                    self.library,
                    self.http,
                    guid=pinned.guid,
                    repo=pinned.repo,
                    version=pinned.version,
                    version_raw=pinned.version_raw or pinned.version,
                    progress=progress,
                )
            except Exception as exc:
                missing += 1
                log.warning(
                    "Leaving %s %s as missing: %s",
                    pinned.guid,
                    pinned.version,
                    exc,
                )
                if progress:
                    progress(f"Missing {pinned.guid} — import a file later")
                continue
            meta = self.library.read_mod_meta(pinned.guid, self.library.pinned_key(pinned))
            if meta and not pinned.plugin_folders:
                pinned.plugin_folders = list(meta.plugin_folders)
        self.packs.save(pack)
        plugins = self.packs.plugins_dir(pack_id)
        plugins.mkdir(parents=True, exist_ok=True)
        sync_pack_plugins(pack, plugins, self.library)
        log.info("Resolved pack %s: %s missing of %s", pack_id, missing, total)

    def play(self, pack_id: str, progress: ProgressFn | None = None):
        game_dir = self.game_dir()
        if game_dir is None:
            raise FileNotFoundError("Sailwind.exe not found. Set the game path in Settings.")
        log.info("Play pack %s from %s", pack_id, game_dir)
        preloader = self.prepare_pack(pack_id, progress=progress)
        pack = self.packs.get(pack_id)
        _, bx_extracted = ensure_bepinex(
            self.library,
            self.http,
            version=pack.bepinex or DEFAULT_BEPINEX_VERSION,
            progress=progress,
        )
        search_path = coop_dll_search_path(pack, self.packs.plugins_dir(pack_id))
        if progress:
            progress("Installing Doorstop into the game folder…")
        if not doorstop_installed(game_dir):
            install_doorstop(game_dir, bx_extracted, preloader, dll_search_path=search_path)
        else:
            write_doorstop_config(game_dir, preloader, dll_search_path=search_path)
        if progress:
            progress("Launching Sailwind…")
        self.config.last_pack_id = pack_id
        self.save_config()
        log.info("Launching Sailwind with pack %s preloader %s", pack_id, preloader)
        return launch_modded(game_dir, preloader, dll_search_path=search_path)

    def play_vanilla(self):
        game_dir = self.game_dir()
        if game_dir is None:
            raise FileNotFoundError("Sailwind.exe not found. Set the game path in Settings.")
        log.info("Launching vanilla Sailwind from %s via Steam", game_dir)
        write_doorstop_config(game_dir, enabled=False)
        return launch_vanilla(game_dir)

    def export_pack(
        self,
        pack_id: str,
        dest: Path,
        bundle: bool = False,
        include_context: bool = False,
        progress: ProgressFn | None = None,
    ) -> Path:
        if bundle and not include_context:
            return self.packs.export_bundle(pack_id, dest, self.library, progress=progress)
        if bundle:
            # Cache everything first so the bundle installs without GitHub or Thunderstore.
            pack = self.packs.get(pack_id)
            bx_version, _ = ensure_bepinex(
                self.library,
                self.http,
                version=pack.bepinex or DEFAULT_BEPINEX_VERSION,
                progress=progress,
            )
            if pack.bepinex != bx_version:
                pack.bepinex = bx_version
                self.packs.save(pack)
            self.resolve_pack_artifacts(pack_id, progress=progress)
            return self.packs.export_bundle(pack_id, dest, self.library, include_context=True, progress=progress)
        return self.packs.export_json(pack_id, dest)

    def share_pack_text(self, pack_id: str) -> str:
        return encode_pack_share(self.packs.get(pack_id))

    def import_pack(self, path: Path, progress: ProgressFn | None = None) -> ModPack:
        path = Path(path)
        with log_duration(log, f"import pack {path}"):
            if progress:
                progress("Reading pack file…")
            pack = self.packs.import_file(path, self.library)
            log.info(
                "Imported recipe %s (%s) with %s mod(s) from %s",
                pack.id,
                pack.name,
                len(pack.mods),
                path,
            )
            return self._finish_imported_pack(pack, progress)

    def import_pack_text(self, text: str, progress: ProgressFn | None = None) -> ModPack:
        payload = parse_share_text(text)
        with log_duration(log, "import pack from clipboard"):
            if progress:
                progress("Reading pack…")
            pack = self.packs.import_manifest(payload)
            log.info(
                "Imported recipe %s (%s) with %s mod(s) from clipboard",
                pack.id,
                pack.name,
                len(pack.mods),
            )
            return self._finish_imported_pack(pack, progress)

    def _finish_imported_pack(self, pack: ModPack, progress: ProgressFn | None) -> ModPack:
        if progress:
            progress(f"Resolving {len(pack.mods)} mods…")
        self.resolve_pack_artifacts(pack.id, progress=progress)
        pack = self.packs.get(pack.id)
        added = self.ensure_catalog_mods(pack.mods)
        if added and progress:
            progress(f"Added {len(added)} catalog item(s) from the pack…")
        return pack

    def import_game_plugins(
        self,
        pack_name: str = "Current game",
        plugins_dir: Path | None = None,
        progress: ProgressFn | None = None,
    ) -> ModPack:
        game_dir = self.game_dir()
        if plugins_dir is None:
            if game_dir is None:
                raise FileNotFoundError("Sailwind.exe not found. Set the game path in Settings.")
            plugins_dir = game_dir / "BepInEx" / "plugins"
        if not plugins_dir.is_dir():
            raise FileNotFoundError(f"No plugins folder at {plugins_dir}")
        if not self.catalog:
            self.catalog = load_cached_catalog(self.paths) or []
        log_path = plugins_dir.parent / "LogOutput.log"
        if progress:
            progress(f"Scanning {plugins_dir}…")
        discovered = scan_plugins_dir(plugins_dir, catalog=self.catalog, log_path=log_path)
        if not discovered:
            raise FileNotFoundError(f"No BepInEx plugins found in {plugins_dir}")
        pack = self.packs.create(pack_name)
        for plugin in discovered:
            if progress:
                progress(f"Importing {plugin.name} ({plugin.guid} {plugin.version})…")
            if not self.library.has_mod(plugin.guid, plugin.version):
                self.library.ingest_plugin_paths(
                    plugin.guid,
                    plugin.version,
                    plugin.plugin_paths,
                    version_raw=plugin.version_raw,
                    repo=plugin.repo,
                    source_url=plugin.source,
                )
            meta = self.library.read_mod_meta(plugin.guid, plugin.version)
            folders = list(meta.plugin_folders) if meta else [path.name for path in plugin.plugin_paths]
            self.packs.upsert_mod(
                pack.id,
                PinnedMod(
                    guid=plugin.guid,
                    version=plugin.version,
                    repo=plugin.repo,
                    enabled=True,
                    plugin_folders=folders,
                    version_raw=plugin.version_raw,
                ),
            )
        self.prepare_pack(pack.id, progress=progress)
        if game_dir is not None:
            _copy_matching_configs(
                game_dir / "BepInEx" / "config",
                self.packs.instance_dir(pack.id) / "BepInEx" / "config",
                {plugin.guid for plugin in discovered},
            )
        self.config.last_pack_id = pack.id
        self.save_config()
        return self.packs.get(pack.id)

    def prune_library(self) -> int:
        pinned: set[tuple[str, str]] = set()
        for pack in self.packs.list_packs():
            for mod in pack.mods:
                pinned.add((mod.guid, self.library.pinned_key(mod)))
        return self.library.prune_unused(pinned)

    def cache_targets(self, *, include_imported: bool = False) -> list[Path]:
        """Return the existing files and folders of data the app can download again.

        Packs, settings, custom catalog entries, backups and display names are never included. Mods imported
        from files are included only with ``include_imported``, as they may not be downloadable.
        """
        paths = self.paths
        targets = [
            paths.modlist_file,
            paths.versions_file,
            paths.extra_modlist_file,
            paths.extra_versions_file,
            paths.scanned_versions_file,
            paths.etag_dir,
            paths.library_bepinex,
            paths.updates_dir,
        ]
        targets.extend(
            entry.path for entry in self.library.list_mods() if include_imported or artifact_source(entry.meta)
        )
        return [target for target in targets if target.exists()]

    def cache_size(self, *, include_imported: bool = False) -> int:
        return sum(dir_size(target) for target in self.cache_targets(include_imported=include_imported))

    def imported_mods_size(self) -> int:
        """Return the size of the library's mods that were imported from files rather than downloaded."""
        return sum(entry.size_bytes for entry in self.library.list_mods() if not artifact_source(entry.meta))

    def clear_cache(self, progress: ProgressFn | None = None, *, include_imported: bool = False) -> CacheClearResult:
        """Delete the data the app can download again and rebuild the catalog from what is left.

        With ``include_imported``, mods imported from files are deleted too. Files that cannot be deleted are
        skipped and reported in the result.
        """
        result = CacheClearResult(freed_bytes=0)
        for target in self.cache_targets(include_imported=include_imported):
            if progress:
                progress(f"Deleting {target.relative_to(self.paths.root)}…")
            size = dir_size(target)
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            except OSError as exc:
                log.warning("Could not delete cached %s: %s", target, exc)
                result.failures.append(f"{target}: {exc}")
                continue
            result.freed_bytes += size
        self._remove_empty_mod_folders()
        self.paths.ensure()
        self.catalog = load_cached_catalog(self.paths) or []
        self.config.last_catalog_refresh = ""
        self.save_config()
        log.info("Cleared %s bytes of cached data, %s failure(s)", result.freed_bytes, len(result.failures))
        return result

    def _remove_empty_mod_folders(self) -> None:
        if not self.paths.library_mods.is_dir():
            return
        for guid_dir in self.paths.library_mods.iterdir():
            if guid_dir.is_dir() and not any(guid_dir.iterdir()):
                guid_dir.rmdir()

    def _setup_logging(self) -> None:
        setup_logging(self.paths.log_file)


def _takes_repo(pack: ModPack, pinned: PinnedMod, pack_id: str | None) -> bool:
    return pack_id is None or pack.id == pack_id or not pinned.repo


def _copy_matching_configs(source: Path, dest: Path, guids: set[str]) -> None:
    if not source.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    bepinex_cfg = source / "BepInEx.cfg"
    if bepinex_cfg.exists():
        shutil.copy2(bepinex_cfg, dest / "BepInEx.cfg")
    wanted = {guid.lower() for guid in guids}
    for cfg in source.glob("*.cfg"):
        stem = cfg.stem.lower()
        if stem in wanted:
            shutil.copy2(cfg, dest / cfg.name)


def _guids_from_discovered(discovered, ref) -> list[str]:
    guids: list[str] = []
    for unit in discovered:
        guid = (getattr(unit, "guid", None) or "").strip()
        if guid and guid not in guids:
            guids.append(guid)
    real = [guid for guid in guids if not guid.lower().startswith("local.")]
    if real:
        return real
    if guids:
        return guids
    host = "github" if getattr(ref, "is_github", True) else "gitlab"
    return [f"{host}.{ref.full_path.replace('/', '.')}"]

