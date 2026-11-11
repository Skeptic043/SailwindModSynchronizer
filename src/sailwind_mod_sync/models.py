from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# A numeric core plus an optional suffix such as "F-4.1", "-beta.11" or "b2".
VERSION_RE = re.compile(r"(\d+(?:\.\d+){0,3})((?:[-_.]?[A-Za-z]+(?:[-_.]?\d+)*)?)")
_SUFFIX_PARTS = re.compile(r"[A-Za-z]+|\d+")
_FILE_EXTENSIONS = frozenset({"dll", "zip", "exe", "json", "txt", "pdb", "tar", "gz", "rar"})
# Words that mark a release before the plain version; any other suffix marks one after it.
PRERELEASE_ORDER = {"dev": 0, "alpha": 1, "beta": 2, "pre": 3, "preview": 3, "rc": 3}
_OPAQUE_UNSAFE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class VersionParts:
    core: tuple[int, ...]
    label: str = ""
    numbers: tuple[int, ...] = ()

    @property
    def prerelease(self) -> bool:
        return self.label in PRERELEASE_ORDER

    @property
    def core_text(self) -> str:
        return ".".join(str(part) for part in self.core)

    def same_core(self, other: VersionParts) -> bool:
        """Return whether both have the same numbers, counting 1.4.1 and 1.4.1.0 as the same."""
        width = max(len(self.core), len(other.core))
        return self.core + (0,) * (width - len(self.core)) == other.core + (0,) * (width - len(other.core))


def _clean(raw: str | None) -> str:
    text = "" if raw is None else str(raw).strip()
    return "" if text.lower() == "none" else text


def _suffix_label(suffix: str) -> str:
    return "".join(part for part in _SUFFIX_PARTS.findall(suffix) if part.isalpha()).lower()


def _match(text: str) -> re.Match[str] | None:
    """Find the first version in ``text``; a file extension after it, as in "Mod-1.2.3.dll", is not a suffix."""
    match = VERSION_RE.search(text)
    if match is not None and _suffix_label(match.group(2)) in _FILE_EXTENSIONS:
        return VERSION_RE.match(match.group(1))
    return match


def version_text(raw: str | None) -> str | None:
    """Return the version exactly as written in ``raw`` ("2.3.0F-4.1" from "Realistic Skies 2.3.0F-4.1")."""
    match = _match(_clean(raw))
    return match.group(0) if match else None


def version_parts(raw: str | None) -> VersionParts | None:
    match = _match(_clean(raw))
    if match is None:
        return None
    core = tuple(int(part) for part in match.group(1).split("."))
    numbers = tuple(int(part) for part in _SUFFIX_PARTS.findall(match.group(2)) if part.isdigit())
    return VersionParts(core, _suffix_label(match.group(2)), numbers)


def parse_mod_version(raw: str | None) -> str | None:
    """Return the canonical version in ``raw``, keeping its suffix: ``2.3.0F-2`` and ``2.3.0-F2`` -> ``2.3.0-f2``."""
    parts = version_parts(raw)
    if parts is None:
        return None
    if not parts.label:
        return parts.core_text
    return f"{parts.core_text}-{parts.label}{'.'.join(str(part) for part in parts.numbers)}"


def version_id(raw: str | None) -> str | None:
    """Return the canonical version in ``raw``, or for text without one, such as the release tag "textures", the text."""
    parsed = parse_mod_version(raw)
    if parsed:
        return parsed
    opaque = _OPAQUE_UNSAFE.sub("-", _clean(raw).lower()).strip("-.")
    return opaque[:64] or None


def stored_version(version: str, version_raw: str) -> str:
    """Return the canonical version of a saved pin or download, recovering a suffix older releases kept only in ``version_raw``."""
    exact = version_id(version_raw)
    if not exact:
        return version
    stored = version_parts(version)
    recovered = version_parts(exact)
    if not version or (stored is not None and recovered is not None and stored.same_core(recovered)):
        return exact
    return version


def version_key(raw: str | None) -> tuple:
    """Sort key: numbered versions above tags without one, prereleases below their plain release."""
    parts = version_parts(raw)
    if parts is None:
        return (0, version_id(raw) or "")
    core = parts.core + (0,) * (4 - len(parts.core))
    if not parts.label:
        rank = (1, 0, "")
    elif parts.prerelease:
        rank = (0, PRERELEASE_ORDER[parts.label], parts.label)
    else:
        rank = (2, 0, parts.label)
    return (1, core, rank, parts.numbers)


def is_newer(latest_raw: str | None, current_raw: str | None) -> bool:
    latest = version_id(latest_raw)
    if not latest:
        return False
    if version_parts(latest_raw) is None or version_parts(current_raw) is None:
        # Without a number neither tag can be ordered; a different latest release is the update.
        return latest != version_id(current_raw)
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
    derived_name: bool = False
    """True when no catalog names this mod and ``name`` comes from its plugin, repository or GUID."""

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
        version = str(data.get("version") or "")
        version_raw = str(data.get("version_raw") or version)
        return cls(
            guid=str(data.get("guid") or ""),
            version=stored_version(version, version_raw),
            repo=str(data.get("repo") or ""),
            enabled=bool(data.get("enabled", True)),
            plugin_folders=[str(item) for item in folders],
            version_raw=version_raw,
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
        version = str(data.get("version") or "")
        version_raw = str(data.get("version_raw") or "")
        return cls(
            guid=str(data.get("guid") or ""),
            version=stored_version(version, version_raw),
            version_raw=version_raw,
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
