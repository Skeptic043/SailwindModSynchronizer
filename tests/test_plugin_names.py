import zipfile
from pathlib import Path

from plugin_metadata_fixture import managed_dll

from sailwind_mod_sync.catalog.custom import repo_key
from sailwind_mod_sync.catalog.mvc import find_entry, merge_catalog, shared_catalog
from sailwind_mod_sync.catalog.plugin_names import PluginName, load_plugin_names, store_plugin_names
from sailwind_mod_sync.config import AppConfig
from sailwind_mod_sync.constants import (
    JSDELIVR_APP_MODLIST,
    JSDELIVR_APP_VERSIONS,
    JSDELIVR_MODLIST,
    JSDELIVR_VERSIONS,
)
from sailwind_mod_sync.http_util import HttpError
from sailwind_mod_sync.library.name_discovery import record_library_plugin_names
from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.manager import Manager
from sailwind_mod_sync.paths import AppPaths

GUID = "com.keevi.defthands"
REPO = "https://github.com/KeeviDev/SailwindDeftHands"
KEY = (GUID, repo_key(REPO))


class _CatalogHttp:
    def __init__(self, payloads: dict) -> None:
        self.token = ""
        self.payloads = payloads

    def get_json(self, url, extra_headers=None, etag=None):
        if url not in self.payloads:
            raise HttpError(f"unexpected {url}", status_code=404)
        return self.payloads[url], None, False

    def download(self, url, dest, progress=None):
        raise AssertionError("No download expected")

    def close(self) -> None:
        return None


def _mvc(version: str = "v1.2.1") -> list:
    return merge_catalog([{"guid": GUID, "repo": REPO}], [{"guid": GUID, "version": version}])


def _supplemental(name: str = "Deft Hands", repo: str = REPO) -> list:
    item = {"guid": GUID, "repo": repo, "plugin_folders": ["DeftHands"]}
    if name:
        item["name"] = name
    return merge_catalog([item], [])


def _library_copy(paths: AppPaths, tmp_path: Path, version: str, plugin_name: str, repo: str = REPO) -> LibraryStore:
    archive = tmp_path / f"DeftHands-{version}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DeftHands/DeftHands.dll", managed_dll(((GUID, plugin_name, version),)))
    store = LibraryStore(paths)
    store.ingest_mod_zip(GUID, version, archive, version_raw=f"v{version}", repo=repo, source_url="https://example/dh.zip")
    return store


def test_merge_catalog_marks_the_names_it_derives() -> None:
    derived = _mvc()[0]
    named = _supplemental()[0]
    assert (derived.name, derived.derived_name) == ("SailwindDeftHands", True)
    assert (named.name, named.derived_name) == ("Deft Hands", False)


def test_shared_catalog_names_mvc_mod_after_supplemental_entry() -> None:
    shared = shared_catalog(_mvc(), _supplemental(), {KEY: PluginName("Plugin Name", "v1.2.1")})
    assert [(entry.name, entry.derived_name) for entry in shared] == [("Deft Hands", False)]


def test_shared_catalog_uses_plugin_name_without_supplemental_name() -> None:
    shared = shared_catalog(_mvc(), _supplemental(name=""), {KEY: PluginName("Deft Hands", "v1.2.1")})
    assert [(entry.name, entry.derived_name) for entry in shared] == [("Deft Hands", True)]


def test_shared_catalog_falls_back_to_repository_name() -> None:
    shared = shared_catalog(_mvc(), [], {KEY: PluginName("", "v1.2.1")})
    assert [(entry.name, entry.derived_name) for entry in shared] == [("SailwindDeftHands", True)]


def test_shared_catalog_ignores_supplemental_name_from_another_repo() -> None:
    shared = shared_catalog(_mvc(), _supplemental(repo="https://github.com/someone/DeftHandsFork"), {})
    assert find_entry(shared, GUID, REPO).name == "SailwindDeftHands"


def test_shared_catalog_keeps_the_name_mvc_gives() -> None:
    mvc = merge_catalog([{"guid": GUID, "repo": REPO, "name": "Own Name"}], [])
    shared = shared_catalog(mvc, _supplemental(), {KEY: PluginName("Plugin Name", "v1.2.1")})
    assert find_entry(shared, GUID, REPO).name == "Own Name"


def test_plugin_names_survive_a_round_trip(paths: AppPaths) -> None:
    names = {KEY: PluginName("Deft Hands", "v1.2.1"), ("other.mod", "https://github.com/a/b"): PluginName("", "v2")}
    store_plugin_names(paths, names)
    assert load_plugin_names(paths) == names


def test_record_reads_name_from_library_copy_once(paths: AppPaths, tmp_path: Path) -> None:
    store = _library_copy(paths, tmp_path, "1.2.1", "Deft Hands")
    catalog = shared_catalog(_mvc(), [], {})
    assert record_library_plugin_names(paths, catalog, store.list_mods())
    assert load_plugin_names(paths)[KEY] == PluginName("Deft Hands", "v1.2.1")
    assert not record_library_plugin_names(paths, catalog, store.list_mods())
    assert [entry.name for entry in shared_catalog(_mvc(), [], load_plugin_names(paths))] == ["Deft Hands"]


def test_record_reads_newer_library_copy_again(paths: AppPaths, tmp_path: Path) -> None:
    _library_copy(paths, tmp_path, "1.2.1", "Deft Hands")
    store = _library_copy(paths, tmp_path, "1.3.0", "Deft Hands Plus")
    assert record_library_plugin_names(paths, shared_catalog(_mvc(), [], {}), store.list_mods())
    assert load_plugin_names(paths)[KEY] == PluginName("Deft Hands Plus", "v1.3.0")


def test_record_ignores_library_copy_from_another_repo(paths: AppPaths, tmp_path: Path) -> None:
    store = _library_copy(paths, tmp_path, "1.2.1", "Fork Hands", repo="https://github.com/someone/DeftHandsFork")
    assert not record_library_plugin_names(paths, shared_catalog(_mvc(), [], {}), store.list_mods())
    assert load_plugin_names(paths) == {}


def test_record_skips_mods_a_catalog_names(paths: AppPaths, tmp_path: Path) -> None:
    store = _library_copy(paths, tmp_path, "1.2.1", "Plugin Name")
    assert not record_library_plugin_names(paths, shared_catalog(_mvc(), _supplemental(), {}), store.list_mods())


def test_fetch_catalog_names_library_mods_after_their_plugins(paths: AppPaths, tmp_path: Path) -> None:
    _library_copy(paths, tmp_path, "1.2.1", "Deft Hands")
    http = _CatalogHttp(
        {
            JSDELIVR_MODLIST: [{"guid": GUID, "repo": REPO}],
            JSDELIVR_VERSIONS: [{"guid": GUID, "repo": REPO, "version": "v1.2.1"}],
            JSDELIVR_APP_MODLIST: [],
            JSDELIVR_APP_VERSIONS: [],
        },
    )
    manager = Manager(paths=paths, config=AppConfig(), http=http)
    try:
        entries = manager.fetch_catalog()
    finally:
        manager.close()
    assert find_entry(entries, GUID).name == "Deft Hands"
