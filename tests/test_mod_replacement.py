from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sailwind_mod_sync.config import AppConfig
from sailwind_mod_sync.http_util import HttpClient
from sailwind_mod_sync.manager import BulkRollbackError, Manager
from sailwind_mod_sync.models import CatalogEntry, PinnedMod


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


@pytest.mark.parametrize("phase", ["ingest", "cache_backup", "cache_promote"])
def test_failed_local_ingestion_or_cache_promotion_preserves_existing_artifact(replacement_case, monkeypatch, phase):
    from sailwind_mod_sync.library.store import LibraryStore

    manager, pack_id, guid, _, archive, _ = replacement_case
    replace_mod(replacement_case, "import")
    cache = manager.library.mod_dir(guid, "2.0.0")
    snapshot = {item.relative_to(cache): item.read_bytes() for item in cache.rglob("*") if item.is_file()}
    before_manifest = manager.packs.manifest_path(pack_id).read_bytes()
    before_dll = (manager.packs.plugins_dir(pack_id) / "Shared/mod.dll").read_bytes()
    zip_mod(archive, {"Shared/mod.dll": before_dll + b"revision", "ZNew/mod.dll": before_dll + b"revision"})
    real_replace, real_ingest = Path.replace, LibraryStore.ingest_mod_zip

    def replace(source, destination):
        if ((phase == "cache_backup" and Path(destination).name == "library-backup")
                or (phase == "cache_promote" and source.name == "2.0.0" and Path(destination) == cache)):
            raise PermissionError("cache injection")
        return real_replace(source, destination)

    def ingest(store, *args, **kwargs):
        result = real_ingest(store, *args, **kwargs)
        if phase == "ingest":
            raise OSError("cache injection")
        return result

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(LibraryStore, "ingest_mod_zip", ingest)
    with pytest.raises(OSError, match="cache injection"):
        replace_mod(replacement_case, "import")
    assert {item.relative_to(cache): item.read_bytes() for item in cache.rglob("*") if item.is_file()} == snapshot
    assert manager.packs.manifest_path(pack_id).read_bytes() == before_manifest
    monkeypatch.setattr(Path, "replace", real_replace)
    manager.resolve_pack_artifacts(pack_id)
    assert (manager.packs.plugins_dir(pack_id) / "Shared/mod.dll").read_bytes() == before_dll


def test_failed_cache_restore_retains_original_artifact_for_manual_recovery(replacement_case, monkeypatch):
    import json

    manager, pack_id, guid, _, archive, _ = replacement_case
    replace_mod(replacement_case, "import")
    cache = manager.library.mod_dir(guid, "2.0.0")
    before_dll = (cache / "extracted/Shared/mod.dll").read_bytes()
    zip_mod(archive, {"Shared/mod.dll": before_dll + b"revision", "ZNew/mod.dll": before_dll + b"revision"})
    real_replace = Path.replace

    def replace(source, destination):
        if source.name == "library-backup" or (source.parent.name == "staged" and source.name == "ZNew"):
            raise PermissionError("cache rollback injection")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(BulkRollbackError, match="Backup retained"):
        replace_mod(replacement_case, "import")
    marker = manager.bulk_recovery_path(pack_id)
    plan = json.loads((marker.parent / "transaction.json").read_text())
    assert plan["library_artifact"] == str(cache) and plan["library_originally_present"]
    assert (Path(plan["library_backup"]) / "extracted/Shared/mod.dll").read_bytes() == before_dll
    assert "restore its original cache" in marker.read_text()


def test_local_reimport_of_source_variant_preserves_other_repository_cache(replacement_case):
    manager, pack_id, guid, key, archive, _ = replacement_case
    pin = replace_mod(replacement_case, "cached")
    plain_cache = manager.library.mod_dir(guid, "2.0.0")
    plain_snapshot = {item.relative_to(plain_cache): item.read_bytes()
                      for item in plain_cache.rglob("*") if item.is_file()}
    dll = (manager.packs.plugins_dir(pack_id) / "Shared/mod.dll").read_bytes() + b"local revision"
    zip_mod(archive, {"Shared/mod.dll": dll, "ZNew/mod.dll": dll})
    imported = replace_mod(replacement_case, "import")
    assert imported.repo == pin.repo and manager.library.pinned_key(imported) == key
    manager.resolve_pack_artifacts(pack_id)
    assert (manager.packs.plugins_dir(pack_id) / "Shared/mod.dll").read_bytes() == dll
    assert {item.relative_to(plain_cache): item.read_bytes()
            for item in plain_cache.rglob("*") if item.is_file()} == plain_snapshot


def test_reimport_can_read_its_input_from_cache_being_replaced(replacement_case):
    manager, pack_id, guid, _, _, _ = replacement_case
    replace_mod(replacement_case, "import")
    cache = manager.library.mod_dir(guid, "2.0.0")
    input_zip = manager.library.mod_zip_path(guid, "2.0.0")
    old_dll = (cache / "extracted/Shared/mod.dll").read_bytes()
    zip_mod(input_zip, {"Shared/mod.dll": old_dll + b"from cache", "ZNew/mod.dll": old_dll + b"from cache"})
    manager.import_local_mod(input_zip, pack_id, guid=guid)
    manager.resolve_pack_artifacts(pack_id)
    assert (cache / "extracted/Shared/mod.dll").read_bytes() == old_dll + b"from cache"
    assert (manager.packs.plugins_dir(pack_id) / "Shared/mod.dll").read_bytes() == old_dll + b"from cache"


def test_failed_import_removes_new_cache_and_restores_original_profile(replacement_case, monkeypatch):
    manager, pack_id, guid, _, archive, _ = replacement_case
    payload = b"MZ" + b"\0" * 32 + guid.encode() + b"\0" + b"3.0.0" + b"\0" * 16
    zip_mod(archive, {"Shared/mod.dll": payload, "ZNew/mod.dll": payload})
    real_replace = Path.replace

    def replace(source, destination):
        if source.parent.name == "staged" and source.name == "ZNew":
            raise OSError("new cache injection")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="new cache injection"):
        replace_mod(replacement_case, "import")
    assert not manager.library.mod_dir(guid, "3.0.0").exists()
    assert_original(replacement_case)


@pytest.mark.parametrize("consumer_repo", ["stored", "catalog"])
def test_partial_cache_rollback_blocks_only_consumers_of_that_artifact(replacement_case, monkeypatch, consumer_repo):
    from sailwind_mod_sync.models import CatalogEntry

    manager, pack_id, guid, key, archive, _ = replacement_case
    pin = replace_mod(replacement_case, "cached")
    consumer = manager.packs.create("Shared cache consumer")
    manager.add_library_mod_to_pack(consumer.id, guid, key, repo=pin.repo)
    if consumer_repo == "catalog":
        pack = manager.packs.get(consumer.id)
        pack.find_mod(guid).repo = ""
        manager.packs.save(pack)
        manager.catalog = [CatalogEntry(
            name="Replacement", repo=pin.repo, guids=[guid], primary_guid=guid,
            latest_raw="2.0.0", latest_version="2.0.0", available=True,
        )]
    unrelated = manager.packs.create("Other cache")
    manager.add_library_mod_to_pack(unrelated.id, guid, "1.0.0")
    cache = manager.library.mod_dir(guid, key)
    consumer_manifest = manager.packs.manifest_path(consumer.id).read_bytes()
    original_dll = (cache / "extracted/Shared/mod.dll").read_bytes()
    zip_mod(archive, {"Shared/mod.dll": original_dll + b"rejected", "ZNew/mod.dll": original_dll + b"rejected"})
    real_replace, real_rmtree = Path.replace, shutil.rmtree

    def replace(source, destination):
        if source.parent.name == "staged" and source.name == "ZNew":
            raise PermissionError("promotion injection")
        return real_replace(source, destination)

    def rmtree(path, *args, **kwargs):
        if Path(path) == cache:
            raise PermissionError("cache cleanup injection")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    with pytest.raises(BulkRollbackError, match="Backup retained"):
        replace_mod(replacement_case, "import")
    marker = manager.bulk_recovery_path(pack_id)
    assert (marker.parent / "library-backup/extracted/Shared/mod.dll").read_bytes() == original_dll
    monkeypatch.setattr(Path, "replace", real_replace)
    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    # Direct preparation, cached replacement and enabled toggles all consume
    # that same artifact. A different version remains usable.
    for action in (
        lambda: manager.resolve_pack_artifacts(consumer.id),
        lambda: manager.add_library_mod_to_pack(consumer.id, guid, key, repo=pin.repo),
        lambda: manager.import_local_mod(archive, consumer.id, guid=guid),
    ):
        with pytest.raises(BulkRollbackError, match="artifact needs recovery"):
            action()
    assert manager.packs.manifest_path(consumer.id).read_bytes() == consumer_manifest
    assert (manager.packs.plugins_dir(consumer.id) / "Shared/mod.dll").read_bytes() == original_dll
    if consumer_repo == "catalog":
        pack = manager.packs.get(consumer.id)
        pack.find_mod(guid).repo = pin.repo
        manager.packs.save(pack)
    manager.set_mod_enabled(consumer.id, guid, False)
    with pytest.raises(BulkRollbackError, match="artifact needs recovery"):
        manager.set_mod_enabled(consumer.id, guid, True)
    manager.resolve_pack_artifacts(unrelated.id)
    assert (manager.packs.plugins_dir(unrelated.id) / "Shared/mod.dll").read_bytes() == b"old-b"


@pytest.mark.parametrize("state", ["malformed", "resolved"])
def test_external_recovery_records_do_not_gate_usable_cache(replacement_case, state, caplog):
    import json

    manager, pack_id, guid, _, _, _ = replacement_case
    consumer = manager.packs.create("Usable cache")
    manager.add_library_mod_to_pack(consumer.id, guid, "1.0.0")
    work = manager.packs.pack_dir(pack_id) / ".bulk-external"
    work.mkdir()
    (work / "recovery-required.txt").write_text("Recovery facts\n", encoding="utf-8")
    if state == "malformed":
        (work / "transaction.json").write_text("{invalid", encoding="utf-8")
    else:
        (work / "transaction.json").write_text(json.dumps({
            "library_artifact": str(manager.library.mod_dir(guid, "1.0.0")),
        }), encoding="utf-8")
        (work / "recovery-resolved.txt").write_text("Cleanup only\n", encoding="utf-8")
    manager.resolve_pack_artifacts(consumer.id)
    assert (manager.packs.plugins_dir(consumer.id) / "Shared/mod.dll").read_bytes() == b"old-b"
    if state == "malformed":
        assert "Could not read library recovery facts" in caplog.text


def test_resolving_multiple_enabled_mods_scans_recovery_records_once(replacement_case, tmp_path, monkeypatch):
    manager, pack_id, _, _, _, _ = replacement_case
    other_guid = "com.example.other"
    archive = zip_mod(tmp_path / "other.zip", {"Other/mod.dll": b"other"})
    manager.library.ingest_mod_zip(
        other_guid, "1.0.0", archive, version_raw="1.0.0", repo="", source_url=str(archive),
    )
    manager.add_library_mod_to_pack(pack_id, other_guid, "1.0.0")
    scans = []
    real_glob = Path.glob

    def glob(path, pattern, *args, **kwargs):
        if pattern == "*/.bulk-*/recovery-required.txt":
            scans.append(path)
        return real_glob(path, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", glob)
    manager.resolve_pack_artifacts(pack_id)
    assert scans == [manager.paths.packs_dir]
    plugins = manager.packs.plugins_dir(pack_id)
    assert (plugins / "Shared/mod.dll").read_bytes() == b"old-b"
    assert (plugins / "Other/mod.dll").read_bytes() == b"other"


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
    # Preparing the pack must not replay the payload of a failed reimport.
    monkeypatch.setattr(Path, "replace", real_replace)
    manager.resolve_pack_artifacts(pack_id)
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




@pytest.mark.parametrize("name", ["unsafe.", "CON", "bad\x01"])
def test_windows_aliased_names_are_refused_before_profile_mutation(replacement_case, name):
    manager, pack_id, guid, _, _, _ = replacement_case
    pack = manager.packs.get(pack_id)
    pack.find_mod(guid).plugin_folders.append(name)
    manager.packs.save(pack)
    before = manager.packs.manifest_path(pack_id).read_bytes()
    with pytest.raises(ValueError, match="Invalid plugin folder name"):
        replace_mod(replacement_case, "cached")
    assert_original((*replacement_case[:-1], before))


def test_untracked_destination_is_preserved_instead_of_overwritten(replacement_case):
    manager, pack_id, _, _, _, before = replacement_case
    plugins = manager.packs.plugins_dir(pack_id)
    (plugins / "ZNew").mkdir()
    foreign = plugins / "ZNew" / "foreign.dll"
    foreign.write_bytes(b"untracked files")
    with pytest.raises(ValueError, match="not owned by this mod"):
        replace_mod(replacement_case, "cached")
    assert foreign.read_bytes() == b"untracked files"
    assert (plugins / "Old/mod.dll").read_bytes() == b"old-a"
    assert (plugins / "Shared/mod.dll").read_bytes() == b"old-b"
    assert manager.packs.manifest_path(pack_id).read_bytes() == before


@pytest.mark.parametrize("target", ["manifest", "library_file"])
def test_redirected_manifest_or_library_content_is_refused(replacement_case, tmp_path, target):
    manager, pack_id, guid, key, _, before = replacement_case
    linked = (manager.packs.manifest_path(pack_id) if target == "manifest"
              else manager.library.mod_extracted(guid, key) / "Shared" / "mod.dll")
    original = linked.read_bytes()
    external = tmp_path / "external"
    external.write_bytes(original)
    linked.unlink()
    try:
        try:
            linked.symlink_to(external)
        except OSError:
            linked.write_bytes(original)
            pytest.skip("Creating symlinks is unavailable in this environment")
        with pytest.raises(ValueError, match="Linked"):
            replace_mod(replacement_case, "cached")
        assert external.read_bytes() == original
        assert manager.packs.manifest_path(pack_id).read_bytes() == before
    finally:
        if linked.is_symlink():
            linked.unlink()
            linked.write_bytes(original)


@pytest.fixture
def recovery_case(replacement_case):
    manager, pack_id, _, _, _, _ = replacement_case
    marker = manager.packs.pack_dir(pack_id) / ".bulk-recovery" / "recovery-required.txt"
    marker.parent.mkdir()
    marker.write_bytes(b"Recover this profile.\n")
    backup = marker.parent / "backup/Old/mod.dll"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"retained recovery copy")
    return replacement_case, marker, backup


@pytest.mark.parametrize("operation", ["save", "delete", "duplicate", "remove", "repo", "associate", "resolve"])
def test_recovery_blocks_writes_without_destroying_backup(recovery_case, operation):
    case, marker, backup = recovery_case
    manager, pack_id, guid, key, _, _ = case
    pack = manager.packs.get(pack_id)
    operations = {
        "save": lambda: manager.packs.save(pack),
        "delete": lambda: manager.packs.delete(pack_id),
        "duplicate": lambda: manager.packs.duplicate(pack_id, "Copy"),
        "remove": lambda: manager.packs.remove_mod(pack_id, guid),
        "repo": lambda: manager.set_mod_repo(guid, "https://github.com/example/new", pack_id),
        "associate": lambda: manager.associate_mod(guid, key, catalog_entry=CatalogEntry(
            name="Replacement", repo="https://github.com/example/new", guids=[guid], primary_guid=guid,
            latest_raw="2.0.0", latest_version="2.0.0", available=True,
        ), pack_id=pack_id),
        "resolve": lambda: manager.resolve_pack_artifacts(pack_id),
    }
    with pytest.raises(BulkRollbackError, match="needs recovery"):
        operations[operation]()
    assert_original(case)
    assert marker.read_bytes() == b"Recover this profile.\n"
    assert backup.read_bytes() == b"retained recovery copy"


def test_repository_update_preserves_nonparticipating_broken_pack(recovery_case):
    case, marker, backup = recovery_case
    manager, broken_id, guid, _, _, _ = case
    healthy = manager.packs.create("Healthy")
    manager.packs.upsert_mod(healthy.id, PinnedMod(guid=guid, version="1.0.0", repo=""))
    repo = "https://github.com/example/healthy"
    manager.set_mod_repo(guid, repo, healthy.id)
    assert manager.packs.get(healthy.id).find_mod(guid).repo == repo
    assert manager.packs.get(broken_id).find_mod(guid).repo == "https://github.com/example/original"
    assert_original(case)
    assert marker.exists() and backup.read_bytes() == b"retained recovery copy"


@pytest.mark.parametrize("failed_cleanup", ["marker_and_directory", "resolved_and_directory"])
def test_completed_replacement_remains_usable_after_cleanup_failures(replacement_case, monkeypatch, failed_cleanup):
    real_unlink, real_write, real_rmtree = Path.unlink, Path.write_text, shutil.rmtree

    def unlink(path, *args, **kwargs):
        if failed_cleanup.startswith("marker") and path.name == "recovery-required.txt":
            raise PermissionError("cleanup injection")
        return real_unlink(path, *args, **kwargs)

    def write(path, *args, **kwargs):
        if failed_cleanup.startswith("resolved") and path.name == "recovery-resolved.txt":
            raise PermissionError("cleanup injection")
        return real_write(path, *args, **kwargs)

    def rmtree(path, *args, **kwargs):
        if Path(path).name.startswith(".bulk-"):
            raise PermissionError("cleanup injection")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    pinned = replace_mod(replacement_case, "cached")
    manager, pack_id, _, _, _, _ = replacement_case
    assert pinned.version == "2.0.0"
    assert (manager.packs.plugins_dir(pack_id) / "Shared/mod.dll").read_bytes().startswith(b"MZ")
    assert manager.bulk_recovery_path(pack_id) is None
    manager.packs.rename(pack_id, "Still usable")


@pytest.mark.parametrize("operation", ["rename", "delete", "remove", "associate"])
def test_recovery_ui_explains_blocked_action_and_preserves_evidence(recovery_case, monkeypatch, operation):
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    from sailwind_mod_sync.ui.main_window import MainWindow

    case, marker, backup = recovery_case
    manager, pack_id, guid, _, _, _ = case
    manager.catalog = [CatalogEntry(
        name="Replacement", repo="https://github.com/example/replacement", guids=[guid], primary_guid=guid,
        latest_raw="1.0.0", latest_version="1.0.0", available=True,
    )]
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_maybe_check_updates", lambda self: None)
    monkeypatch.setattr(MainWindow, "_maybe_auto_scan_mods", lambda self: None)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Renamed", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(str(_args[-1])))
    window = MainWindow(manager)
    try:
        window._reload_packs(select_id=pack_id)
        if operation == "rename":
            window._rename_pack()
        elif operation == "delete":
            window._delete_pack()
        elif operation == "remove":
            window._remove_mod(guid)
        else:
            window._offer_catalog_association([PinnedMod(guid=guid, version="1.0.0")], pack_id=pack_id)
        assert len(warnings) == 1 and "needs recovery" in warnings[0] and str(marker) in warnings[0]
        assert_original(case)
        assert marker.read_bytes() == b"Recover this profile.\n"
        assert backup.read_bytes() == b"retained recovery copy"
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def _symlink_or_skip(link: Path, target: Path) -> None:
    """A directory symlink, or on Windows without symlink rights a junction (resolve() follows both)."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError) as exc:
        error = exc
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError):
        pytest.skip(f"Creating directory links isn't allowed here: {error}")


def test_install_works_when_the_data_folder_is_reached_through_a_symlink(tmp_path):
    # Guards the "linked library files" check against symlinked homes (e.g. Bazzite's
    # /home -> /var/home); AppPaths resolves the data root, so paths stay comparable.
    from sailwind_mod_sync.paths import AppPaths

    real = tmp_path / "var" / "home" / "data"
    real.mkdir(parents=True)
    _symlink_or_skip(tmp_path / "home", tmp_path / "var" / "home")
    paths = AppPaths(tmp_path / "home" / "data")
    paths.ensure()
    manager = Manager(paths=paths, config=AppConfig(game_path="missing"), http=MagicMock(spec=HttpClient))
    try:
        pack = manager.packs.create("Linked home")
        archive = zip_mod(tmp_path / "mod.zip", {"Mod/mod.dll": b"MZ"})
        manager.library.ingest_mod_zip(
            "com.example.linkedhome", "1.0.0", archive, version_raw="1.0.0",
            repo="https://github.com/example/linkedhome",
            source_url="https://github.com/example/linkedhome/releases/download/v1.0.0/mod.zip",
        )
        manager.add_library_mod_to_pack(pack.id, "com.example.linkedhome", "1.0.0")
        assert (manager.packs.plugins_dir(pack.id) / "Mod" / "mod.dll").read_bytes() == b"MZ"
    finally:
        manager.close()
