from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from packaging.version import InvalidVersion, Version

VERSION_RE = re.compile(r"\d+(?:\.\d+){0,3}")


def parse_mod_version(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == "none":
        return None
    match = VERSION_RE.search(text)
    return match.group(0) if match else None


def version_key(raw: str | None) -> Version:
    normalized = parse_mod_version(raw)
    if not normalized:
        return Version("0")
    try:
        return Version(normalized)
    except InvalidVersion:
        return Version("0")


def is_newer(latest_raw: str | None, current_raw: str | None) -> bool:
    return version_key(latest_raw) > version_key(current_raw)


def guid_family(guid: str) -> str:
    """Group alias GUIDs (sailadex / sailadex82) while keeping distinct mods separate."""
    tail = guid.rsplit(".", 1)[-1].lower()
    family = re.sub(r"\d+", "", tail)
    return family or tail


def catalog_mod_name(guid: str) -> str:
    tail = guid.rsplit(".", 1)[-1]
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", tail)
    spaced = re.sub(r"[_\-]+", " ", spaced)
    spaced = re.sub(r"\d+", " ", spaced)
    return " ".join(spaced.split()).title() or guid


def display_mod_name(
    guid: str,
    *,
    alias: str = "",
    catalog_name: str = "",
    catalog_shared: bool = False,
    plugin_folders: list[str] | None = None,
    repo: str = "",
) -> str:
    text = (alias or "").strip()
    if text:
        return text
    folders = [folder.strip() for folder in (plugin_folders or []) if folder and folder.strip()]
    catalog = (catalog_name or "").strip()
    if catalog and not catalog_shared:
        return catalog
    if folders:
        return folders[0]
    if catalog:
        return catalog
    repo_name = (repo or "").rstrip("/").split("/")[-1].strip()
    if repo_name:
        return repo_name
    parts = [part for part in guid.split(".") if part]
    return parts[-1] if parts else guid or "Unknown"


@dataclass
class CatalogEntry:
    repo: str
    guids: list[str]
    primary_guid: str
    name: str
    latest_raw: str | None
    latest_version: str | None
    available: bool
    custom: bool = False
    plugin_folders: list[str] = field(default_factory=list)
    alternate: bool = False
    """True when a higher-priority catalog already lists this mod from another repository."""

    @property
    def guid_label(self) -> str:
        if len(self.guids) <= 1:
            return self.primary_guid
        return f"{self.primary_guid} (+{len(self.guids) - 1})"


@dataclass
class PinnedMod:
    guid: str
    version: str
    repo: str = ""
    enabled: bool = True
    plugin_folders: list[str] = field(default_factory=list)
    version_raw: str = ""

    def to_dict(self) -> dict:
        return {
            "guid": self.guid,
            "version": self.version,
            "repo": self.repo,
            "enabled": self.enabled,
            "plugin_folders": list(self.plugin_folders),
            "version_raw": self.version_raw,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PinnedMod:
        folders = data.get("plugin_folders") or []
        if not isinstance(folders, list):
            folders = []
        return cls(
            guid=str(data.get("guid") or ""),
            version=str(data.get("version") or ""),
            repo=str(data.get("repo") or ""),
            enabled=bool(data.get("enabled", True)),
            plugin_folders=[str(item) for item in folders],
            version_raw=str(data.get("version_raw") or data.get("version") or ""),
        )


@dataclass
class ModPack:
    id: str
    name: str
    version: str = "1.0.0"
    bepinex: str = ""
    mods: list[PinnedMod] = field(default_factory=list)
    schema: int = 1

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "bepinex": self.bepinex,
            "mods": [mod.to_dict() for mod in self.mods],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ModPack:
        mods_raw = data.get("mods") or []
        mods = [PinnedMod.from_dict(item) for item in mods_raw if isinstance(item, dict)]
        return cls(
            schema=int(data.get("schema") or 1),
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            version=str(data.get("version") or "1.0.0"),
            bepinex=str(data.get("bepinex") or ""),
            mods=mods,
        )

    def find_mod(self, guid: str) -> PinnedMod | None:
        for mod in self.mods:
            if mod.guid == guid:
                return mod
        return None


@dataclass
class ArtifactMeta:
    guid: str
    version: str
    version_raw: str
    repo: str
    source_url: str
    sha256: str
    filename: str
    plugin_folders: list[str] = field(default_factory=list)
    downloaded_at: str = ""

    def to_dict(self) -> dict:
        return {
            "guid": self.guid,
            "version": self.version,
            "version_raw": self.version_raw,
            "repo": self.repo,
            "source_url": self.source_url,
            "sha256": self.sha256,
            "filename": self.filename,
            "plugin_folders": list(self.plugin_folders),
            "downloaded_at": self.downloaded_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ArtifactMeta:
        folders = data.get("plugin_folders") or []
        if not isinstance(folders, list):
            folders = []
        return cls(
            guid=str(data.get("guid") or ""),
            version=str(data.get("version") or ""),
            version_raw=str(data.get("version_raw") or ""),
            repo=str(data.get("repo") or ""),
            source_url=str(data.get("source_url") or ""),
            sha256=str(data.get("sha256") or ""),
            filename=str(data.get("filename") or ""),
            plugin_folders=[str(item) for item in folders],
            downloaded_at=str(data.get("downloaded_at") or ""),
        )


@dataclass
class LibraryEntry:
    guid: str
    version: str
    path: Path
    zip_path: Path
    extracted_dir: Path
    meta: ArtifactMeta
    size_bytes: int


@dataclass
class ModDetails:
    guid: str
    version: str
    name: str
    repo: str
    source_url: str
    filename: str
    sha256: str
    downloaded_at: str
    plugin_folders: list[str]
    size_bytes: int
    installed_versions: list[str]
    catalog_latest: str | None
    pack_pins: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RemoteModInfo:
    latest_tag: str = ""
    release_tags: list[str] = field(default_factory=list)
    readme: str = ""
    releases_error: str = ""
    readme_error: str = ""


@dataclass
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0
    api_url: str = ""


@dataclass
class RemoteRelease:
    tag: str
    name: str
    assets: list[ReleaseAsset]
    html_url: str = ""
    body: str = ""
