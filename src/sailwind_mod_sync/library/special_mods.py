from __future__ import annotations

from pathlib import Path

from sailwind_mod_sync.models import ModPack, PinnedMod

COOP_GUID = "com.sailwindcoop.mod"
COOP_REPO = "https://github.com/DiamondMiner99/sailwind-coop"
COOP_PLUGIN_FOLDER = "SailwindCoop"
STEAM_API_DLL = "steam_api64.dll"

# UUVR is only downloaded for the mods that need it, so it isn't listed in the catalog where anyone could add it.
# Its releases have one zip per Unity backend; Sailwind is Unity 2019 Mono, which needs the legacy Mono build.
UUVR_GUID = "raicuparta.uuvr-legacy"
UUVR_REPO = "https://github.com/Raicuparta/uuvr"
RELEASE_ASSET_HINTS = {UUVR_GUID: "uuvr-mono-legacy"}
# Mods downloaded only for the mods that need them, by lowercase GUID; they never get catalog entries.
UNLISTED_MOD_NAMES = {UUVR_GUID: "UUVR"}

FAIL_DOWNLOAD_GUID = "com.sms.test.faildownload"
FAIL_DOWNLOAD_REPO = "https://github.com/foxyv/SailwindModSynchronizer"
FAIL_DOWNLOAD_ASSET = "BrokenDownloadTest.zip"
FAIL_DOWNLOAD_VERSION = "0.0.1"

KNOWN_MOD_REPOS = {
    COOP_GUID: COOP_REPO,
    UUVR_GUID: UUVR_REPO,
    FAIL_DOWNLOAD_GUID: FAIL_DOWNLOAD_REPO,
}


def known_repo_for(guid: str) -> str | None:
    key = (guid or "").strip().lower()
    for known, repo in KNOWN_MOD_REPOS.items():
        if known.lower() == key:
            return repo
    return None


def release_asset_hints(guid: str) -> tuple[str, ...]:
    """Names that pick a mod's file among several downloads of one release."""
    hint = RELEASE_ASSET_HINTS.get((guid or "").strip().lower())
    return (hint,) if hint else ()


def unlisted_mod_name(guid: str) -> str:
    """Name of a mod kept out of the catalog, or "" for any other mod."""
    return UNLISTED_MOD_NAMES.get((guid or "").strip().lower(), "")


def is_coop_guid(guid: str) -> bool:
    return (guid or "").strip().lower() == COOP_GUID.lower()


def is_fail_download_guid(guid: str) -> bool:
    return (guid or "").strip().lower() == FAIL_DOWNLOAD_GUID.lower()


def coop_steam_api_present(extracted: Path) -> bool:
    if not extracted.exists():
        return False
    return any(path.is_file() and path.name.lower() == STEAM_API_DLL for path in extracted.rglob("*"))


def artifact_needs_refetch(store, guid: str, version: str) -> bool:
    if not is_coop_guid(guid) or not store.has_mod(guid, version):
        return False
    return not coop_steam_api_present(store.mod_extracted(guid, version))


def artifact_ready(store, guid: str, version: str) -> bool:
    return store.has_mod(guid, version) and not artifact_needs_refetch(store, guid, version)


def enabled_coop_pin(pack: ModPack) -> PinnedMod | None:
    for mod in pack.mods:
        if mod.enabled and is_coop_guid(mod.guid):
            return mod
    return None


def coop_dll_search_path(pack: ModPack, plugins_dir: Path) -> Path | None:
    pinned = enabled_coop_pin(pack)
    if pinned is None:
        return None
    folders = list(pinned.plugin_folders) if pinned.plugin_folders else []
    if COOP_PLUGIN_FOLDER not in folders:
        folders.append(COOP_PLUGIN_FOLDER)
    for folder in folders:
        candidate = plugins_dir / folder
        if candidate.is_dir():
            return candidate
    return None
