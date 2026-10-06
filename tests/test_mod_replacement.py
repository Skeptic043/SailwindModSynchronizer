from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sailwind_mod_sync.config import AppConfig
from sailwind_mod_sync.http_util import HttpClient
from sailwind_mod_sync.manager import BulkRollbackError, Manager


def zip_mod(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def replace_mod(case, caller):
    manager, pack_id, guid, key, archive, _ = case
    if caller == "cached":
        return manager.add_library_mod_to_pack(pack_id, guid, key)
    return manager.import_local_mod(archive, pack_id, guid=guid)


def assert_original(case):
    manager, pack_id, guid, _, _, before = case
    assert manager.packs.manifest_path(pack_id).read_bytes() == before
    assert manager.packs.get(pack_id).find_mod(guid).version == "1.0.0"
    plugins = manager.packs.plugins_dir(pack_id)
    assert (plugins / "Old/mod.dll").read_bytes() == b"old-a"
    assert (plugins / "Shared/mod.dll").read_bytes() == b"old-b"
    assert not (plugins / "ZNew").exists()
    assert (plugins / "Unrelated/keep.txt").read_bytes() == b"untouched"


@pytest.fixture
def replacement_case(paths, tmp_path):
    http = MagicMock(spec=HttpClient)
    http.download.side_effect = AssertionError("Cached replacement must not download")
    manager = Manager(paths=paths, config=AppConfig(game_path="missing"), http=http)
    pack = manager.packs.create("Replacement")
    guid = "com.example.replace"
    repo = "https://github.com/example/fork"
    old = zip_mod(tmp_path / "old.zip", {"Old/mod.dll": b"old-a", "Shared/mod.dll": b"old-b"})
    manager.library.ingest_mod_zip(
        guid, "1.0.0", old, version_raw="1.0.0", repo="https://github.com/example/original",
        source_url="https://github.com/example/original/releases/download/v1.0.0/old.zip",
    )
    manager.add_library_mod_to_pack(pack.id, guid, "1.0.0")
    plugins = manager.packs.plugins_dir(pack.id)
    (plugins / "Unrelated").mkdir()
    (plugins / "Unrelated/keep.txt").write_bytes(b"untouched")
    # Force a source-specific key. A replacement must use key, never meta.version.
    manager.library.ingest_mod_zip(
        guid, "2.0.0", old, version_raw="2.0.0", repo="https://github.com/example/original",
        source_url="https://github.com/example/original/releases/download/v2.0.0/old.zip",
    )
    key = manager.library.artifact_key(guid, "2.0.0", repo)
    payload = b"MZ" + b"\0" * 32 + guid.encode() + b"\0" + b"2.0.0" + b"\0" * 16
    archive = zip_mod(tmp_path / "new.zip", {"Shared/mod.dll": payload, "ZNew/mod.dll": payload})
    manager.library.ingest_mod_zip(
        guid, key, archive, version_raw="2.0.0", repo=repo,
        source_url=f"{repo}/releases/download/v2.0.0/new.zip",
    )
    manifest = manager.packs.manifest_path(pack.id)
    manifest.write_bytes(manifest.read_bytes() + b"\n  ")
    yield manager, pack.id, guid, key, archive, manifest.read_bytes()
    manager.close()


@pytest.mark.parametrize("caller", ["cached", "import"])
@pytest.mark.parametrize("phase", ["copy", "backup", "promote", "manifest", "commit", "after_commit"])
def test_failed_replacement_restores_files_and_exact_manifest(replacement_case, monkeypatch, caller, phase):
    manager, pack_id, _, _, _, _ = replacement_case
    manifest = manager.packs.manifest_path(pack_id)
    real_copy, real_replace, real_write = shutil.copytree, Path.replace, Path.write_bytes

    def copy(source, destination, *args, **kwargs):
        if phase == "copy" and Path(destination).parent.name == "staged" and Path(destination).name == "ZNew":
            raise OSError("replacement injection")
        return real_copy(source, destination, *args, **kwargs)

    def replace(source, destination):
        destination = Path(destination)
        if ((phase == "backup" and destination.parent.name == "backup" and source.name == "Shared")
                or (phase == "promote" and source.parent.name == "staged" and source.name == "ZNew")
                or (phase == "commit" and source.name == "modpack.json" and destination == manifest)):
            raise PermissionError("replacement injection")
        result = real_replace(source, destination)
        if phase == "after_commit" and source.name == "modpack.json" and destination == manifest:
            raise OSError("replacement injection")
        return result

    def write(path, data):
        if phase == "manifest" and path.name == "modpack.json" and path.parent.name.startswith(".bulk-"):
            raise OSError("replacement injection")
        return real_write(path, data)

    monkeypatch.setattr(shutil, "copytree", copy)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(OSError, match="replacement injection"):
        replace_mod(replacement_case, caller)
    assert_original(replacement_case)
    assert manager.bulk_recovery_path(pack_id) is None


@pytest.mark.parametrize("caller", ["cached", "import"])
@pytest.mark.parametrize("enabled", [True, False])
def test_replacement_keeps_disabled_state_and_uses_correct_source(replacement_case, monkeypatch, caller, enabled):
    manager, pack_id, guid, key, _, _ = replacement_case
    if not enabled:
        manager.set_mod_enabled(pack_id, guid, False)
        real_copy = shutil.copytree

        def copy(source, destination, *args, **kwargs):
            if Path(destination).parent.name == "staged":
                raise AssertionError("Do not stage disabled plugins")
            return real_copy(source, destination, *args, **kwargs)

        monkeypatch.setattr(shutil, "copytree", copy)
    pinned = replace_mod(replacement_case, caller)
    assert pinned.version == "2.0.0" and pinned.enabled == enabled
    assert set(pinned.plugin_folders) == {"Shared", "ZNew"}
    if caller == "cached":
        assert manager.library.pinned_key(pinned) == key
        assert pinned.repo == "https://github.com/example/fork"
    else:
        assert pinned.repo == "https://github.com/example/original"
    plugins = manager.packs.plugins_dir(pack_id)
    assert not (plugins / "Old").exists()
    assert (plugins / "Shared/mod.dll").exists() == enabled
    assert (plugins / "ZNew/mod.dll").exists() == enabled
    if enabled:
        assert (plugins / "Shared/mod.dll").read_bytes().startswith(b"MZ")
    assert (plugins / "Unrelated/keep.txt").read_bytes() == b"untouched"


@pytest.mark.parametrize("failure", [False, True])
def test_case_only_update_commits_or_restores_original_spelling(replacement_case, monkeypatch, failure):
    manager, pack_id, guid, key, _, _ = replacement_case
    extracted = manager.library.mod_extracted(guid, key)
    (extracted / "Shared").rename(extracted / "shared")
    real_replace = Path.replace

    def replace(source, destination):
        if failure and source.parent.name == "staged" and source.name == "ZNew":
            raise OSError("case injection")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    if failure:
        with pytest.raises(OSError, match="case injection"):
            replace_mod(replacement_case, "cached")
        assert_original(replacement_case)
        assert "Shared" in {item.name for item in manager.packs.plugins_dir(pack_id).iterdir()}
    else:
        pin = replace_mod(replacement_case, "cached")
        assert set(pin.plugin_folders) == {"shared", "ZNew"}
        names = {item.name for item in manager.packs.plugins_dir(pack_id).iterdir()}
        assert "shared" in names and "Shared" not in names


def test_failed_rollback_retains_backup_and_blocks_further_replacement(replacement_case, monkeypatch):
    manager, pack_id, _, _, _, before = replacement_case
    real_replace = Path.replace

    def replace(source, destination):
        if source.parent.name == "backup" or (source.parent.name == "staged" and source.name == "ZNew"):
            raise PermissionError("rollback injection")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(BulkRollbackError, match="Backup retained"):
        replace_mod(replacement_case, "cached")
    marker = manager.bulk_recovery_path(pack_id)
    assert marker is not None
    assert (marker.parent / "backup/Old/mod.dll").read_bytes() == b"old-a"
    assert (marker.parent / "original-modpack.json").read_bytes() == before
    for caller in ("cached", "import"):
        with pytest.raises(BulkRollbackError, match="needs recovery"):
            replace_mod(replacement_case, caller)


@pytest.mark.parametrize("failure", [False, True])
def test_same_version_reimport_preserves_active_files_on_failure(replacement_case, monkeypatch, failure):
    manager, pack_id, guid, _, archive, _ = replacement_case
    replace_mod(replacement_case, "import")
    plugins = manager.packs.plugins_dir(pack_id)
    before_manifest = manager.packs.manifest_path(pack_id).read_bytes()
    before_dll = (plugins / "Shared/mod.dll").read_bytes()
    zip_mod(archive, {"Shared/mod.dll": before_dll + b"revision", "ZNew/mod.dll": before_dll + b"revision"})
    real_replace = Path.replace

    def replace(source, destination):
        if failure and source.parent.name == "staged" and source.name == "ZNew":
            raise PermissionError("reimport injection")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    if failure:
        with pytest.raises(PermissionError, match="reimport injection"):
            replace_mod(replacement_case, "import")
        assert manager.packs.manifest_path(pack_id).read_bytes() == before_manifest
    else:
        pin = replace_mod(replacement_case, "import")
        assert pin.version == "2.0.0" and pin.guid == guid
    expected = before_dll if failure else before_dll + b"revision"
    assert (plugins / "Shared/mod.dll").read_bytes() == expected
    assert (plugins / "ZNew/mod.dll").read_bytes() == expected


def test_incomplete_cache_refuses_replacement_before_removing_active_files(replacement_case):
    manager, _, guid, key, _, _ = replacement_case
    extracted = manager.library.mod_extracted(guid, key)
    shutil.rmtree(extracted)
    extracted.mkdir()
    (extracted / "orphan.dll").write_bytes(b"MZ")
    with pytest.raises(FileNotFoundError, match="No extracted plugin folders"):
        replace_mod(replacement_case, "cached")
    assert_original(replacement_case)
