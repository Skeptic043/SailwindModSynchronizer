from __future__ import annotations

import zipfile
from pathlib import Path

from sailwind_mod_sync.catalog.github import GitHubDownloadError
from sailwind_mod_sync.http_util import HttpClient, HttpError
from sailwind_mod_sync.library.download import ensure_bundled_player_model, ensure_mod_artifact
from sailwind_mod_sync.library.special_mods import (
    COOP_GUID,
    COOP_REPO,
    FAIL_DOWNLOAD_GUID,
    PLAYER_MODEL_GUID,
    PLAYER_MODEL_REPO,
    artifact_needs_refetch,
    artifact_ready,
    known_repo_for,
)
from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.models import ReleaseAsset, RemoteRelease
from sailwind_mod_sync.paths import AppPaths


class _FakeHttp(HttpClient):
    def __init__(self, payload: bytes) -> None:
        self.token = ""
        self._owns_client = False
        self._client = None
        self._payload = payload

    def close(self) -> None:
        return None

    def download(self, url: str, dest: Path, progress=None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._payload)


def test_installs_dll_only_github_release(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    store = LibraryStore(paths)
    http = _FakeHttp(b"MZ" + b"\0" * 16)

    def fake_fetch(*args, **kwargs):
        return RemoteRelease(
            tag="v0.3.2",
            name="SaveCleaner",
            assets=[ReleaseAsset("SaveCleaner.dll", "https://example/SaveCleaner.dll")],
        )

    monkeypatch.setattr("sailwind_mod_sync.library.download.fetch_release", fake_fetch)
    meta = ensure_mod_artifact(
        store,
        http,
        guid="com.nandbrew.savecleaner",
        repo="https://github.com/NANDbrew/SaveCleaner",
        version="0.3.2",
        version_raw="v0.3.2",
    )
    assert meta.version == "0.3.2"
    assert store.has_mod("com.nandbrew.savecleaner", "0.3.2")
    extracted = store.mod_extracted("com.nandbrew.savecleaner", "0.3.2")
    assert (extracted / "SaveCleaner" / "SaveCleaner.dll").exists()


def test_known_repo_for_coop() -> None:
    assert known_repo_for("com.sailwindcoop.mod") == COOP_REPO
    assert known_repo_for("COM.SAILWINDCOOP.MOD") == COOP_REPO
    assert known_repo_for("com.dizzy.sailwind.gamma") is None
    assert known_repo_for(FAIL_DOWNLOAD_GUID).endswith("SailwindModSynchronizer")


def test_fail_download_test_mod_raises_manual_import_help(paths: AppPaths) -> None:
    store = LibraryStore(paths)
    try:
        ensure_mod_artifact(
            store,
            _FakeHttp(b"MZ"),
            guid=FAIL_DOWNLOAD_GUID,
            repo="",
            version="0.0.1",
        )
    except GitHubDownloadError as exc:
        text = str(exc)
    else:
        raise AssertionError("expected GitHubDownloadError")
    assert GitHubDownloadError.is_help_text(text)
    assert "BrokenDownloadTest.zip" in text
    assert "Import Mod DLL/ZIP" in text
    assert "12 of 52428800" in text


def test_refetches_incomplete_coop_artifact(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    store = LibraryStore(paths)
    plugin_dir = tmp_path / "SailwindCoop"
    plugin_dir.mkdir()
    (plugin_dir / "SailwindCoop.dll").write_bytes(b"MZ")
    (plugin_dir / "Facepunch.Steamworks.Win64.dll").write_bytes(b"MZ")
    store.ingest_plugin_paths(
        COOP_GUID,
        "0.3.2",
        [plugin_dir],
        version_raw="0.3.2",
        repo="",
        source_url="game-plugins",
    )
    assert store.has_mod(COOP_GUID, "0.3.2")
    assert artifact_needs_refetch(store, COOP_GUID, "0.3.2")
    assert not artifact_ready(store, COOP_GUID, "0.3.2")

    overlay = tmp_path / "SailwindCoop-v0.3.2.zip"
    with zipfile.ZipFile(overlay, "w") as zf:
        zf.writestr("steam_api64.dll", b"steam-api")
        zf.writestr("winhttp.dll", b"doorstop")
        zf.writestr("BepInEx/plugins/SailwindCoop/SailwindCoop.dll", b"MZ" + b"\0" * 16)
        zf.writestr("BepInEx/plugins/SailwindCoop/Facepunch.Steamworks.Win64.dll", b"MZ")

    http = _FakeHttp(overlay.read_bytes())

    def fake_fetch(*args, **kwargs):
        return RemoteRelease(
            tag="v0.3.2",
            name="v0.3.2",
            assets=[ReleaseAsset("SailwindCoop-v0.3.2.zip", "https://example/coop.zip")],
        )

    monkeypatch.setattr("sailwind_mod_sync.library.download.fetch_release", fake_fetch)
    meta = ensure_mod_artifact(
        store,
        http,
        guid=COOP_GUID,
        repo="",
        version="0.3.2",
        version_raw="v0.3.2",
    )
    assert meta.repo == COOP_REPO
    plugin = store.mod_extracted(COOP_GUID, "0.3.2") / "SailwindCoop"
    assert (plugin / "steam_api64.dll").read_bytes() == b"steam-api"
    assert artifact_ready(store, COOP_GUID, "0.3.2")



def test_coop_release_splits_into_coop_and_player_model_artifacts(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    from plugin_metadata_fixture import coop_release_files

    store = LibraryStore(paths)
    release = tmp_path / "SailwindCoop-v0.6.0.zip"
    with zipfile.ZipFile(release, "w") as zf:
        for name, data in coop_release_files("0.6.0", "0.2.0").items():
            zf.writestr(name, data)
    url = "https://github.com/DiamondMiner99/sailwind-coop/releases/download/v0.6.0/SailwindCoop-v0.6.0.zip"
    monkeypatch.setattr(
        "sailwind_mod_sync.library.download.fetch_release",
        lambda *args, **kwargs: RemoteRelease(tag="v0.6.0", name="v0.6.0", assets=[ReleaseAsset(release.name, url)]),
    )
    coop = ensure_mod_artifact(
        store, _FakeHttp(release.read_bytes()), guid=COOP_GUID, repo=COOP_REPO,
        version="0.6.0", version_raw="v0.6.0", plugin_folders=["SailwindCoop"],
    )

    key = ensure_bundled_player_model(store, "0.6.0")

    assert coop.plugin_folders == ["SailwindCoop"]
    assert artifact_ready(store, COOP_GUID, "0.6.0")
    assert key == "0.2.0"
    model = store.read_mod_meta(PLAYER_MODEL_GUID, key)
    assert (model.version, model.repo, model.source_url) == ("0.2.0", PLAYER_MODEL_REPO, url)
    assert model.plugin_folders == ["SailwindPlayerModel"]
    extracted = store.mod_extracted(PLAYER_MODEL_GUID, key)
    assert sorted(path.name for path in extracted.iterdir()) == ["SailwindPlayerModel"]
    assert not any(extracted.rglob("steam_api64.dll"))


def test_fetches_release_by_version_when_tag_is_not_a_version(
    paths: AppPaths, tmp_path: Path, monkeypatch
) -> None:
    """A release tagged with a literal word (e.g. "release") whose version only
    lives in the release name is still fetched when individual tag lookups 404.
    Regression: BryanP-JP19/SailwindSeaLifeMod failed install with HTTP 404 on
    /releases/tags/v0.0.1 after its real tag ("release") was no longer used.
    """
    store = LibraryStore(paths)
    http = _FakeHttp(b"MZ" + b"\0" * 16)

    def fake_fetch(*args, **kwargs):
        if kwargs.get("tag") is not None:
            raise HttpError("404 Not Found")
        raise AssertionError("fetch_release without a tag should not be reached when version is set")

    def fake_list_releases(*args, **kwargs):
        return [
            RemoteRelease(
                tag="release",
                name="0.0.1",
                assets=[ReleaseAsset("SeaLifeMod.dll", "https://example/SeaLifeMod.dll")],
            )
        ]

    monkeypatch.setattr("sailwind_mod_sync.library.download.fetch_release", fake_fetch)
    monkeypatch.setattr("sailwind_mod_sync.library.download.list_releases", fake_list_releases)
    meta = ensure_mod_artifact(
        store,
        http,
        guid="com.yourname.sailwind.sealifeplugin",
        repo="https://github.com/BryanP-JP19/SailwindSeaLifeMod",
        version="0.0.1",
        version_raw="0.0.1",
    )
    assert meta.version == "0.0.1"
    assert meta.version_raw == "0.0.1"
    assert store.has_mod("com.yourname.sailwind.sealifeplugin", "0.0.1")
