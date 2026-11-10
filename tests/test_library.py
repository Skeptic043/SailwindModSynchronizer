from __future__ import annotations

import zipfile
from pathlib import Path

from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.paths import AppPaths


def test_ingest_and_list_mod(paths: AppPaths, tmp_path: Path) -> None:
    archive = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Dizzy.Gamma/Dizzy.Gamma.dll", b"MZ" + b"\0" * 32)
    store = LibraryStore(paths)
    meta = store.ingest_mod_zip(
        "com.dizzy.sailwind.gamma",
        "0.3.3",
        archive,
        version_raw="v0.3.3",
        repo="https://github.com/foxyv/dizzy_sailwind_mods",
        source_url="https://example/gamma.zip",
        filename="Dizzy.Gamma-0.3.3.zip",
    )
    assert meta.plugin_folders == ["Dizzy.Gamma"]
    assert store.has_mod("com.dizzy.sailwind.gamma", "0.3.3")
    entries = store.list_mods()
    assert len(entries) == 1
    assert entries[0].guid == "com.dizzy.sailwind.gamma"
    store.delete_mod("com.dizzy.sailwind.gamma", "0.3.3")
    assert store.list_mods() == []


def test_has_mod_accepts_extracted_dll_without_zip(paths: AppPaths) -> None:
    store = LibraryStore(paths)
    extracted = store.mod_extracted("com.example.local", "1.0.0")
    folder = extracted / "LocalMod"
    folder.mkdir(parents=True)
    (folder / "LocalMod.dll").write_bytes(b"MZ")
    assert store.has_mod("com.example.local", "1.0.0")


def test_prune_keeps_pinned(paths: AppPaths, tmp_path: Path) -> None:
    store = LibraryStore(paths)
    for version in ("1.0.0", "1.1.0"):
        archive = tmp_path / f"{version}.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Mod/Mod.dll", b"MZ")
        store.ingest_mod_zip(
            "com.example.mod",
            version,
            archive,
            version_raw=version,
            repo="https://github.com/example/mod",
            source_url="https://example/mod.zip",
        )
    removed = store.prune_unused({("com.example.mod", "1.1.0")})
    assert removed == 1
    assert store.has_mod("com.example.mod", "1.1.0")
    assert not store.has_mod("com.example.mod", "1.0.0")


ORIGINAL = "https://github.com/example/mod"
FORK = "https://github.com/me/mod-fork"


class _ReleaseHttp:
    """Serves one release with a zip whose DLL bytes name the repository it came from."""

    token = ""

    def __init__(self, repo: str, tag: str = "v1.2.0") -> None:
        self.owner_repo = repo.removeprefix("https://github.com/")
        self.tag = tag
        self.downloads: list[str] = []

    def get_json(self, url, extra_headers=None, etag=None):
        assert url == f"https://api.github.com/repos/{self.owner_repo}/releases/tags/{self.tag}"
        asset_url = f"https://github.com/{self.owner_repo}/releases/download/{self.tag}/Mod.zip"
        return {
            "tag_name": self.tag,
            "html_url": f"https://github.com/{self.owner_repo}/releases/tag/{self.tag}",
            "assets": [{"name": "Mod.zip", "browser_download_url": asset_url}],
        }, None, False

    def download(self, url, dest, progress=None):
        self.downloads.append(url)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Mod/Mod.dll", b"MZ" + self.owner_repo.encode())


def _fetch(store: LibraryStore, repo: str):
    from sailwind_mod_sync.library.download import ensure_mod_artifact

    http = _ReleaseHttp(repo)
    meta = ensure_mod_artifact(store, http, guid="com.example.mod", repo=repo, version="1.2.0", version_raw="v1.2.0")
    return meta, http


def _dll_bytes(store: LibraryStore, key: str) -> bytes:
    return (store.mod_extracted("com.example.mod", key) / "Mod" / "Mod.dll").read_bytes()


def test_same_version_from_two_sources_is_kept_apart(paths: AppPaths) -> None:
    store = LibraryStore(paths)
    _fetch(store, ORIGINAL)

    fork_meta, fork_http = _fetch(store, FORK)

    assert fork_http.downloads
    assert fork_meta.version == "1.2.0"
    assert store.artifact_key("com.example.mod", "1.2.0", ORIGINAL) == "1.2.0"
    assert store.artifact_key("com.example.mod", "1.2.0", FORK) == "1.2.0@me.mod-fork"
    assert _dll_bytes(store, "1.2.0") == b"MZexample/mod"
    assert _dll_bytes(store, "1.2.0@me.mod-fork") == b"MZme/mod-fork"
    assert sorted(entry.version for entry in store.list_mods()) == ["1.2.0", "1.2.0@me.mod-fork"]

    _, again = _fetch(store, FORK)
    assert again.downloads == []


def test_imported_artifact_serves_any_source(paths: AppPaths, tmp_path: Path) -> None:
    archive = tmp_path / "mod.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Mod/Mod.dll", b"MZ")
    store = LibraryStore(paths)
    store.ingest_mod_zip(
        "com.example.mod", "1.2.0", archive, version_raw="1.2.0", repo=ORIGINAL, source_url=str(archive)
    )

    assert store.artifact_key("com.example.mod", "1.2.0", FORK) == "1.2.0"


def test_artifact_saved_before_suffix_support_is_still_found(paths) -> None:
    import json

    from sailwind_mod_sync.library.store import LibraryStore
    from sailwind_mod_sync.models import parse_mod_version

    store = LibraryStore(paths)
    legacy = store.mod_dir("com.kingcam16.realisticskies", "2.3.0")
    (legacy / "extracted" / "RealisticSkies").mkdir(parents=True)
    (legacy / "extracted" / "RealisticSkies" / "RealisticSkies.dll").write_bytes(b"MZ")
    (legacy / "metadata.json").write_text(json.dumps(
        {"guid": "com.kingcam16.realisticskies", "version": "2.3.0", "version_raw": "2.3.0F-4.1"}), encoding="utf-8")

    assert store.artifact_key("com.kingcam16.realisticskies", parse_mod_version("2.3.0F-4.1")) == "2.3.0"
    # Another build of 2.3.0 gets its own folder instead of reusing that one.
    other = parse_mod_version("2.3.0F-4")
    assert store.artifact_key("com.kingcam16.realisticskies", other) == other


def test_second_source_artifact_saved_before_suffix_support_is_still_found(paths) -> None:
    import json

    from sailwind_mod_sync.library.store import LibraryStore
    from sailwind_mod_sync.models import parse_mod_version

    guid = "com.kingcam16.realisticskies"
    store = LibraryStore(paths)

    def legacy(key: str, raw: str, repo: str) -> None:
        folder = store.mod_dir(guid, key)
        (folder / "extracted" / "RealisticSkies").mkdir(parents=True)
        (folder / "extracted" / "RealisticSkies" / "RealisticSkies.dll").write_bytes(b"MZ")
        (folder / "metadata.json").write_text(json.dumps({
            "guid": guid, "version": "2.3.0", "version_raw": raw, "repo": repo,
            "source_url": f"{repo}/releases/download/{raw}/RealisticSkies.zip",
        }), encoding="utf-8")

    legacy("2.3.0", "V2.3.0", "https://github.com/KingCam77/Realistic-Skies")
    legacy("2.3.0@fluffyjesus.realistic-skies", "2.3.0F-4.1", "https://github.com/FluffyJesus/Realistic-Skies")

    fork = "https://github.com/FluffyJesus/Realistic-Skies"
    assert store.artifact_key(guid, parse_mod_version("2.3.0F-4.1"), fork) == "2.3.0@fluffyjesus.realistic-skies"
    assert store.artifact_key(guid, "2.3.0", "https://github.com/KingCam77/Realistic-Skies") == "2.3.0"
