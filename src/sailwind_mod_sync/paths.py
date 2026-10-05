from __future__ import annotations

import os
from pathlib import Path


def default_data_root() -> Path:
    env = os.environ.get("SAILWIND_MOD_SYNC_HOME")
    if env:
        return Path(env).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "SailwindModSynchronizer"
    return Path.home() / "AppData" / "Local" / "SailwindModSynchronizer"


class AppPaths:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_data_root()).resolve()
        self.config_file = self.root / "config.json"
        self.catalog_dir = self.root / "catalog"
        self.modlist_file = self.catalog_dir / "ModList.json"
        self.versions_file = self.catalog_dir / "release_versions.json"
        self.extra_modlist_file = self.catalog_dir / "sms_ModList.json"
        self.extra_versions_file = self.catalog_dir / "sms_release_versions.json"
        self.custom_catalog_file = self.catalog_dir / "custom.json"
        self.scanned_versions_file = self.catalog_dir / "scanned_versions.json"
        self.etag_dir = self.catalog_dir / "etags"
        self.library_mods = self.root / "library" / "mods"
        self.library_bepinex = self.root / "library" / "bepinex"
        self.aliases_file = self.root / "library" / "aliases.json"
        self.packs_dir = self.root / "packs"
        self.backups_dir = self.root / "backups"
        self.updates_dir = self.root / "updates"
        self.log_file = self.root / "manager.log"

    def ensure(self) -> None:
        for path in (
            self.root,
            self.catalog_dir,
            self.etag_dir,
            self.library_mods,
            self.library_bepinex,
            self.packs_dir,
            self.backups_dir,
            self.updates_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def mod_artifact_dir(self, guid: str, version: str) -> Path:
        return self.library_mods / sanitize_segment(guid) / sanitize_segment(version)

    def bepinex_dir(self, version: str) -> Path:
        return self.library_bepinex / sanitize_segment(version)

    def pack_dir(self, pack_id: str) -> Path:
        return self.packs_dir / sanitize_segment(pack_id)


def sanitize_segment(value: str) -> str:
    cleaned = value.strip().replace("\\", "_").replace("/", "_")
    cleaned = cleaned.replace(":", "_")
    return cleaned or "_"
