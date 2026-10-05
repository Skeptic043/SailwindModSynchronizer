import json
from pathlib import Path

from sailwind_mod_sync.catalog.github import (
    GitHubDownloadError,
    download_release_asset,
    fetch_readme,
    fetch_release,
    github_manual_download_help,
    github_release_page_url,
    list_releases,
    pick_release_asset,
    pick_zip_asset,
    parse_repo_url,
    repo_page_url,
)
from sailwind_mod_sync.catalog.mvc import merge_catalog
from sailwind_mod_sync.http_util import HttpError
from sailwind_mod_sync.models import ReleaseAsset
from sailwind_mod_sync.paths import AppPaths


def test_merge_dedupes_duplicate_guids() -> None:
    mod_list = [
        {"guid": "com.raddude.sailadex", "repo": "https://github.com/bryon82/SailwindSail-a-dex"},
        {"guid": "com.raddude82.sailadex", "repo": "https://github.com/bryon82/SailwindSail-a-dex"},
        {"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix"},
    ]
    versions = [
        {"guid": "com.raddude.sailadex", "repo": "https://github.com/bryon82/SailwindSail-a-dex", "version": "v2.0.0"},
        {"guid": "com.raddude82.sailadex", "repo": "https://github.com/bryon82/SailwindSail-a-dex", "version": "v2.0.0"},
        {"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix", "version": "none"},
    ]
    entries = merge_catalog(mod_list, versions)
    by_name = {entry.name: entry for entry in entries}
    sailadex = by_name["SailwindSail-a-dex"]
    assert sailadex.available
    assert sailadex.latest_version == "2.0.0"
    assert "com.raddude.sailadex" in sailadex.guids
    assert "com.raddude82.sailadex" in sailadex.guids
    sticky = by_name["StickyFix"]
    assert not sticky.available
    assert sticky.latest_version is None


def test_app_catalog_keeps_better_ports_and_custom_island_api_separate() -> None:
    root = Path(__file__).resolve().parents[1]
    mod_list = json.loads((root / "catalog" / "ModList.json").read_text(encoding="utf-8"))
    versions = json.loads((root / "catalog" / "release_versions.json").read_text(encoding="utf-8"))
    entries = merge_catalog(mod_list, versions)
    by_guid = {entry.primary_guid: entry for entry in entries}
    ports = by_guid["com.winter.betterports"]
    api = by_guid["com.winter.customislandapi"]
    assert ports.repo.endswith("/BetterPorts")
    assert api.repo.endswith("/CustomIslandAPI")
    assert ports.name == "Better Ports"
    assert api.name == "Custom Island API"
    assert ports.latest_version == "1.1.0"
    assert api.latest_version == "1.0.1"


def test_merge_splits_distinct_mods_in_one_repo() -> None:
    entries = merge_catalog(
        [
            {"guid": "com.dizzy.sailwind.gamma", "repo": "https://github.com/foxyv/dizzy_sailwind_mods"},
            {"guid": "com.dizzy.sailwind.calendar", "repo": "https://github.com/foxyv/dizzy_sailwind_mods"},
        ],
        [
            {"guid": "com.dizzy.sailwind.gamma", "repo": "https://github.com/foxyv/dizzy_sailwind_mods", "version": "v0.3.3"},
            {"guid": "com.dizzy.sailwind.calendar", "repo": "https://github.com/foxyv/dizzy_sailwind_mods", "version": "v0.2.0"},
        ],
    )
    by_guid = {entry.primary_guid: entry for entry in entries}
    assert set(by_guid) == {"com.dizzy.sailwind.gamma", "com.dizzy.sailwind.calendar"}
    assert by_guid["com.dizzy.sailwind.gamma"].name == "Gamma"
    assert by_guid["com.dizzy.sailwind.calendar"].name == "Calendar"
    assert by_guid["com.dizzy.sailwind.gamma"].repo.endswith("dizzy_sailwind_mods")
    assert by_guid["com.dizzy.sailwind.calendar"].latest_version == "0.2.0"


def test_merge_catalog_reads_name_folders_and_inline_version() -> None:
    entries = merge_catalog(
        [
            {
                "guid": "com.dizzy.sailwind.calendar",
                "repo": "https://github.com/foxyv/dizzy_sailwind_mods",
                "name": "Dizzy.Calendar",
                "plugin_folders": ["Dizzy.Calendar"],
                "version": "v0.2.0",
            }
        ],
        [],
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == "Dizzy.Calendar"
    assert entry.plugin_folders == ["Dizzy.Calendar"]
    assert entry.latest_version == "0.2.0"
    assert entry.available
    assert not entry.custom


def test_parse_github_and_gitlab_urls() -> None:
    github = parse_repo_url("https://github.com/foxyv/dizzy_sailwind_mods/")
    assert github.is_github
    assert github.full_path == "foxyv/dizzy_sailwind_mods"
    gitlab = parse_repo_url("https://gitlab.com/group/sub/project.git")
    assert gitlab.is_gitlab
    assert gitlab.full_path == "group/sub/project"


def test_repo_page_url_normalizes_hosts() -> None:
    assert (
        repo_page_url("https://github.com/NANDbrew/StickyFix.git/")
        == "https://github.com/NANDbrew/StickyFix"
    )
    assert repo_page_url("NANDbrew/StickyFix") == "https://github.com/NANDbrew/StickyFix"
    assert (
        repo_page_url("https://gitlab.com/group/sub/project.git")
        == "https://gitlab.com/group/sub/project"
    )
    assert (
        repo_page_url("https://github.com/NANDbrew/StickyFix/releases/tag/v1.2.3")
        == "https://github.com/NANDbrew/StickyFix"
    )
    assert (
        repo_page_url("https://gitlab.com/group/sub/project/-/releases")
        == "https://gitlab.com/group/sub/project"
    )
    assert repo_page_url("") is None
    assert repo_page_url("not a url") is None


def test_pick_zip_prefers_matching_name() -> None:
    assets = [
        ReleaseAsset("source.zip", "https://example/source.zip"),
        ReleaseAsset("Dizzy.Calendar-0.1.0.zip", "https://example/cal.zip"),
        ReleaseAsset("Dizzy.Gamma-0.3.3.zip", "https://example/gamma.zip"),
    ]
    chosen = pick_zip_asset(assets, "com.dizzy.sailwind.gamma", "dizzy_sailwind_mods")
    assert chosen.name == "Dizzy.Gamma-0.3.3.zip"


def test_pick_release_uses_plugin_folder_hint() -> None:
    assets = [
        ReleaseAsset("ShatteredSeasSmall.zip", "https://example/small.zip"),
        ReleaseAsset("ShatteredSeasLarge.zip", "https://example/large.zip"),
    ]
    chosen = pick_release_asset(
        assets,
        "com.TheOriginOfAllEvil.riverSloop",
        "Shattered-Seas-Expansion",
        extra_hints=["Shattered Seas Small"],
    )
    assert chosen.name == "ShatteredSeasSmall.zip"


def test_pick_release_accepts_dll_when_no_zip() -> None:
    assets = [ReleaseAsset("SaveCleaner.dll", "https://example/SaveCleaner.dll")]
    chosen = pick_release_asset(assets, "com.nandbrew.savecleaner", "SaveCleaner")
    assert chosen.name == "SaveCleaner.dll"


def test_pick_release_prefers_zip_over_dll() -> None:
    assets = [
        ReleaseAsset("CookedInfo.dll", "https://example/CookedInfo.dll"),
        ReleaseAsset("CookedInfo.zip", "https://example/CookedInfo.zip"),
    ]
    chosen = pick_release_asset(assets, "pr0skynesis.cookedinfo", "CookedInfo-Sailwind-Mod")
    assert chosen.name == "CookedInfo.zip"


def test_parse_github_release_keeps_api_asset_url() -> None:
    payload = {
        "tag_name": "v1.0.0",
        "name": "v1.0.0",
        "html_url": "https://github.com/example/mod/releases/tag/v1.0.0",
        "assets": [
            {
                "name": "HugeMod.zip",
                "url": "https://api.github.com/repos/example/mod/releases/assets/99",
                "browser_download_url": "https://github.com/example/mod/releases/download/v1.0.0/HugeMod.zip",
                "size": 80_000_000,
            }
        ],
    }

    class _Http:
        def get_json(self, url, extra_headers=None, etag=None):
            return payload, None, False

    release = fetch_release(_Http(), "https://github.com/example/mod", tag="v1.0.0")
    assert release.assets[0].download_url.endswith("HugeMod.zip")
    assert release.assets[0].api_url.endswith("/releases/assets/99")
    assert release.assets[0].size == 80_000_000


def test_download_release_asset_uses_api_url_when_tokened(tmp_path) -> None:
    from pathlib import Path
    from sailwind_mod_sync.http_util import HttpClient

    class _Http(HttpClient):
        def __init__(self, token: str) -> None:
            self.token = token
            self._owns_client = False
            self._client = None
            self.urls: list[str] = []

        def download(self, url: str, dest: Path, progress=None) -> None:
            self.urls.append(url)
            dest.write_bytes(b"ok")

    asset = ReleaseAsset(
        "HugeMod.zip",
        "https://github.com/example/mod/releases/download/v1.0.0/HugeMod.zip",
        api_url="https://api.github.com/repos/example/mod/releases/assets/99",
    )
    dest = tmp_path / "HugeMod.zip"
    authed = _Http("ghs_secret")
    download_release_asset(authed, asset, dest)
    assert authed.urls == ["https://api.github.com/repos/example/mod/releases/assets/99"]
    anon = _Http("")
    download_release_asset(anon, asset, dest)
    assert anon.urls == ["https://github.com/example/mod/releases/download/v1.0.0/HugeMod.zip"]


def test_github_release_page_url_from_browser_download() -> None:
    assert github_release_page_url(
        "https://github.com/example/mod/releases/download/v1.2.0/HugeMod.zip"
    ) == "https://github.com/example/mod/releases/tag/v1.2.0"
    assert github_release_page_url(
        "",
        release_url="https://github.com/example/mod/releases/tag/v9",
    ) == "https://github.com/example/mod/releases/tag/v9"


def test_github_manual_download_help_lists_import_steps() -> None:
    text = github_manual_download_help(
        asset_name="HugeMod.zip",
        download_url="https://github.com/example/mod/releases/download/v1.0.0/HugeMod.zip",
        reason="HTTP 403",
    )
    assert GitHubDownloadError.is_help_text(text)
    assert "HugeMod.zip" in text
    assert "https://github.com/example/mod/releases/tag/v1.0.0" in text
    assert "Import Mod DLL/ZIP" in text
    assert "HTTP 403" in text


def test_download_release_asset_warns_on_http_error(tmp_path) -> None:
    from pathlib import Path
    from sailwind_mod_sync.http_util import HttpClient, HttpError

    class _Http(HttpClient):
        def __init__(self) -> None:
            self.token = ""
            self._owns_client = False
            self._client = None

        def download(self, url: str, dest: Path, progress=None) -> None:
            dest.write_bytes(b"partial")
            raise HttpError("HTTP 403 for github.com", status_code=403)

    asset = ReleaseAsset(
        "HugeMod.zip",
        "https://github.com/example/mod/releases/download/v1.0.0/HugeMod.zip",
        size=80_000_000,
    )
    dest = tmp_path / "HugeMod.zip"
    try:
        download_release_asset(
            _Http(),
            asset,
            dest,
            release_url="https://github.com/example/mod/releases/tag/v1.0.0",
        )
    except GitHubDownloadError as exc:
        text = str(exc)
    else:
        raise AssertionError("expected GitHubDownloadError")
    assert GitHubDownloadError.is_help_text(text)
    assert "HTTP 403" in text
    assert "https://github.com/example/mod/releases/tag/v1.0.0" in text
    assert not dest.exists()


def test_download_release_asset_warns_on_short_file(tmp_path) -> None:
    from pathlib import Path
    from sailwind_mod_sync.http_util import HttpClient

    class _Http(HttpClient):
        def __init__(self) -> None:
            self.token = ""
            self._owns_client = False
            self._client = None

        def download(self, url: str, dest: Path, progress=None) -> None:
            dest.write_bytes(b"short")

    asset = ReleaseAsset(
        "HugeMod.zip",
        "https://github.com/example/mod/releases/download/v1.0.0/HugeMod.zip",
        size=50_000_001,
    )
    dest = tmp_path / "HugeMod.zip"
    try:
        download_release_asset(_Http(), asset, dest, repo_url="https://github.com/example/mod")
    except GitHubDownloadError as exc:
        text = str(exc)
    else:
        raise AssertionError("expected GitHubDownloadError")
    assert "Incomplete download" in text
    assert "Import Mod DLL/ZIP" in text
    assert not dest.exists()


class _GithubListHttp:
    def __init__(self, latest_error: Exception | None, payload) -> None:
        self.latest_error = latest_error
        self.payload = payload
        self.urls: list[str] = []

    def get_json(self, url, extra_headers=None, etag=None):
        self.urls.append(url)
        if url.endswith("/releases/latest"):
            if self.latest_error is not None:
                raise self.latest_error
            return self.payload, None, False
        if url.endswith("/releases"):
            return self.payload, None, False
        raise AssertionError(url)


def test_fetch_release_falls_back_to_prerelease_list() -> None:
    payload = [
        {
            "tag_name": "v0.3.2",
            "name": "v0.3.2 (alpha)",
            "draft": False,
            "prerelease": True,
            "published_at": "2026-08-09T21:48:02Z",
            "html_url": "https://github.com/DiamondMiner99/sailwind-coop/releases/tag/v0.3.2",
            "assets": [
                {
                    "name": "SailwindCoop-v0.3.2.zip",
                    "browser_download_url": "https://example/SailwindCoop-v0.3.2.zip",
                    "size": 12,
                }
            ],
        },
        {
            "tag_name": "v0.3.1",
            "name": "v0.3.1",
            "draft": False,
            "prerelease": True,
            "published_at": "2026-08-01T00:00:00Z",
            "html_url": "https://github.com/DiamondMiner99/sailwind-coop/releases/tag/v0.3.1",
            "assets": [
                {
                    "name": "SailwindCoop-v0.3.1.zip",
                    "browser_download_url": "https://example/old.zip",
                    "size": 8,
                }
            ],
        },
    ]
    http = _GithubListHttp(HttpError("HTTP 404", status_code=404), payload)
    release = fetch_release(http, "https://github.com/DiamondMiner99/sailwind-coop")
    assert release.tag == "v0.3.2"
    assert release.assets[0].name == "SailwindCoop-v0.3.2.zip"
    assert http.urls[0].endswith("/releases/latest")
    assert http.urls[1].endswith("/releases")


def test_fetch_release_list_prefers_stable_over_newer_prerelease() -> None:
    payload = [
        {
            "tag_name": "v0.4.0-pre",
            "prerelease": True,
            "draft": False,
            "published_at": "2026-09-01T00:00:00Z",
            "assets": [
                {
                    "name": "mod-pre.zip",
                    "browser_download_url": "https://example/pre.zip",
                }
            ],
        },
        {
            "tag_name": "v0.3.0",
            "prerelease": False,
            "draft": False,
            "published_at": "2026-08-01T00:00:00Z",
            "assets": [
                {
                    "name": "mod.zip",
                    "browser_download_url": "https://example/mod.zip",
                }
            ],
        },
    ]
    http = _GithubListHttp(HttpError("HTTP 404", status_code=404), payload)
    release = fetch_release(http, "https://github.com/example/mod")
    assert release.tag == "v0.3.0"


def test_merge_with_custom_keeps_extra_repo() -> None:
    from sailwind_mod_sync.catalog.custom import merge_with_custom
    from sailwind_mod_sync.models import CatalogEntry

    mvc = merge_catalog(
        [{"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix"}],
        [{"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix", "version": "v1.0.0"}],
    )
    custom = [
        CatalogEntry(
            repo="https://github.com/DiamondMiner99/sailwind-coop",
            guids=["com.sailwindcoop.mod"],
            primary_guid="com.sailwindcoop.mod",
            name="sailwind-coop",
            latest_raw="v0.3.2",
            latest_version="0.3.2",
            available=True,
            custom=True,
        )
    ]
    merged = merge_with_custom(mvc, custom)
    names = {entry.name: entry for entry in merged}
    assert "StickyFix" in names
    assert names["sailwind-coop"].custom
    assert names["sailwind-coop"].primary_guid == "com.sailwindcoop.mod"


def test_merge_with_custom_skips_mvc_duplicate() -> None:
    from sailwind_mod_sync.catalog.custom import merge_with_custom
    from sailwind_mod_sync.models import CatalogEntry

    mvc = merge_catalog(
        [{"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix"}],
        [{"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix", "version": "v1.0.0"}],
    )
    custom = [
        CatalogEntry(
            repo="https://github.com/NANDbrew/StickyFix.git",
            guids=["com.nandbrew.stickyfix"],
            primary_guid="com.nandbrew.stickyfix",
            name="StickyFix",
            latest_raw="v9.9.9",
            latest_version="9.9.9",
            available=True,
            custom=True,
        )
    ]
    merged = merge_with_custom(mvc, custom)
    sticky = [entry for entry in merged if "stickyfix" in entry.primary_guid.lower()]
    assert len(sticky) == 1
    assert not sticky[0].custom
    assert sticky[0].latest_version == "1.0.0"


def test_merge_with_custom_keeps_extra_mod_from_mvc_repo() -> None:
    from sailwind_mod_sync.catalog.custom import merge_with_custom
    from sailwind_mod_sync.models import CatalogEntry

    mvc = merge_catalog(
        [{"guid": "com.dizzy.sailwind.gamma", "repo": "https://github.com/foxyv/dizzy_sailwind_mods"}],
        [{"guid": "com.dizzy.sailwind.gamma", "repo": "https://github.com/foxyv/dizzy_sailwind_mods", "version": "v0.3.3"}],
    )
    custom = [
        CatalogEntry(
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
            guids=["com.dizzy.sailwind.calendar"],
            primary_guid="com.dizzy.sailwind.calendar",
            name="Dizzy.Calendar",
            latest_raw="v0.2.0",
            latest_version="0.2.0",
            available=True,
            custom=True,
            plugin_folders=["Dizzy.Calendar"],
        )
    ]
    merged = merge_with_custom(mvc, custom)
    guids = {entry.primary_guid: entry for entry in merged}
    assert "com.dizzy.sailwind.gamma" in guids
    assert guids["com.dizzy.sailwind.calendar"].custom
    assert guids["com.dizzy.sailwind.calendar"].plugin_folders == ["Dizzy.Calendar"]


def test_load_custom_catalog_splits_bundled_repo(paths: AppPaths) -> None:
    from sailwind_mod_sync.catalog.custom import load_custom_catalog, save_custom_catalog
    from sailwind_mod_sync.models import CatalogEntry

    save_custom_catalog(
        paths,
        [
            CatalogEntry(
                repo="https://github.com/TheOriginOfAllEvil/Shattered-Seas-Expansion",
                guids=["com.TheOriginOfAllEvil.riverSloop", "com.TheOriginOfAllEvil.clipper"],
                primary_guid="com.TheOriginOfAllEvil.riverSloop",
                name="Shattered-Seas-Expansion",
                latest_raw="b1.2.8",
                latest_version="1.2.8",
                available=True,
                custom=True,
            )
        ],
    )
    loaded = load_custom_catalog(paths)
    by_guid = {entry.primary_guid: entry for entry in loaded}
    assert set(by_guid) == {"com.TheOriginOfAllEvil.riverSloop", "com.TheOriginOfAllEvil.clipper"}
    assert by_guid["com.TheOriginOfAllEvil.riverSloop"].name == "River Sloop"
    assert by_guid["com.TheOriginOfAllEvil.clipper"].name == "Clipper"


def test_load_custom_catalog_keeps_entry_without_repo(paths: AppPaths) -> None:
    from sailwind_mod_sync.catalog.custom import load_custom_catalog, save_custom_catalog
    from sailwind_mod_sync.models import CatalogEntry

    save_custom_catalog(
        paths,
        [
            CatalogEntry(
                repo="",
                guids=["local.discord.mystery"],
                primary_guid="local.discord.mystery",
                name="Mystery",
                latest_raw="2.0.0",
                latest_version="2.0.0",
                available=True,
                custom=True,
            )
        ],
    )
    loaded = load_custom_catalog(paths)
    assert len(loaded) == 1
    assert loaded[0].primary_guid == "local.discord.mystery"
    assert loaded[0].repo == ""
    assert loaded[0].name == "Mystery"
    assert loaded[0].latest_version == "2.0.0"


class _ReadmeHttp:
    def get_bytes(self, url, extra_headers=None):
        assert url.endswith("/readme")
        assert extra_headers and "raw" in extra_headers.get("Accept", "")
        return b"# Sailwind Co-op\n\nCrew a ship."

    def get_json(self, url, extra_headers=None, etag=None):
        assert "/releases" in url
        return (
            [
                {
                    "tag_name": "v0.3.2",
                    "name": "v0.3.2",
                    "draft": False,
                    "assets": [],
                },
                {
                    "tag_name": "v0.3.1",
                    "name": "v0.3.1",
                    "draft": True,
                    "assets": [],
                },
            ],
            None,
            False,
        )


def test_fetch_readme_and_list_releases() -> None:
    http = _ReadmeHttp()
    text = fetch_readme(http, "https://github.com/DiamondMiner99/sailwind-coop")
    assert "Crew a ship" in text
    releases = list_releases(http, "https://github.com/DiamondMiner99/sailwind-coop")
    assert [item.tag for item in releases] == ["v0.3.2"]


class _CatalogHttp:
    def __init__(self, payloads: dict[str, object], missing: set[str] | None = None) -> None:
        self.payloads = payloads
        self.missing = missing or set()
        self.urls: list[str] = []

    def get_json(self, url, extra_headers=None, etag=None):
        self.urls.append(url)
        if url in self.missing:
            raise HttpError("HTTP 404", status_code=404)
        if url not in self.payloads:
            raise HttpError(f"unexpected {url}", status_code=404)
        return self.payloads[url], None, False


def test_refresh_catalog_merges_project_list(paths: AppPaths) -> None:
    from sailwind_mod_sync.catalog.mvc import find_entry, refresh_catalog
    from sailwind_mod_sync.constants import (
        GITHUB_RAW_APP_MODLIST,
        GITHUB_RAW_APP_VERSIONS,
        GITHUB_RAW_MODLIST,
        GITHUB_RAW_VERSIONS,
        JSDELIVR_APP_MODLIST,
        JSDELIVR_APP_VERSIONS,
        JSDELIVR_MODLIST,
        JSDELIVR_VERSIONS,
    )

    http = _CatalogHttp(
        {
            JSDELIVR_MODLIST: [
                {"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix"},
            ],
            JSDELIVR_VERSIONS: [
                {
                    "guid": "com.nandbrew.stickyfix",
                    "repo": "https://github.com/NANDbrew/StickyFix",
                    "version": "v1.0.0",
                }
            ],
            JSDELIVR_APP_MODLIST: [
                {
                    "guid": "com.dizzy.sailwind.calendar",
                    "repo": "https://github.com/foxyv/dizzy_sailwind_mods",
                    "name": "Dizzy.Calendar",
                    "plugin_folders": ["Dizzy.Calendar"],
                }
            ],
            JSDELIVR_APP_VERSIONS: [
                {
                    "guid": "com.dizzy.sailwind.calendar",
                    "repo": "https://github.com/foxyv/dizzy_sailwind_mods",
                    "version": "v0.2.0",
                }
            ],
        }
    )
    entries = refresh_catalog(paths, http)
    sticky = find_entry(entries, "com.nandbrew.stickyfix")
    calendar = find_entry(entries, "com.dizzy.sailwind.calendar")
    assert sticky is not None and not sticky.custom
    assert calendar is not None and not calendar.custom
    assert calendar.name == "Dizzy.Calendar"
    assert calendar.plugin_folders == ["Dizzy.Calendar"]
    assert calendar.latest_version == "0.2.0"
    assert GITHUB_RAW_MODLIST not in http.urls
    assert GITHUB_RAW_VERSIONS not in http.urls
    assert GITHUB_RAW_APP_MODLIST not in http.urls
    assert GITHUB_RAW_APP_VERSIONS not in http.urls
    from sailwind_mod_sync.catalog.mvc import load_cached_catalog

    cached = load_cached_catalog(paths)
    assert cached is not None
    assert find_entry(cached, "com.dizzy.sailwind.calendar") is not None


def test_refresh_catalog_skips_project_mods_already_in_mvc(paths: AppPaths) -> None:
    from sailwind_mod_sync.catalog.mvc import find_entry, refresh_catalog
    from sailwind_mod_sync.constants import (
        JSDELIVR_APP_MODLIST,
        JSDELIVR_APP_VERSIONS,
        JSDELIVR_MODLIST,
        JSDELIVR_VERSIONS,
    )

    http = _CatalogHttp(
        {
            JSDELIVR_MODLIST: [
                {"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix"},
            ],
            JSDELIVR_VERSIONS: [
                {
                    "guid": "com.nandbrew.stickyfix",
                    "repo": "https://github.com/NANDbrew/StickyFix",
                    "version": "v1.0.0",
                }
            ],
            JSDELIVR_APP_MODLIST: [
                {"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/example/fork"},
            ],
            JSDELIVR_APP_VERSIONS: [
                {
                    "guid": "com.nandbrew.stickyfix",
                    "repo": "https://github.com/example/fork",
                    "version": "v9.9.9",
                }
            ],
        }
    )
    entries = refresh_catalog(paths, http)
    matches = [entry for entry in entries if "stickyfix" in entry.primary_guid.lower()]
    assert len(matches) == 1
    assert matches[0].repo.endswith("StickyFix")
    assert matches[0].latest_version == "1.0.0"
    assert find_entry(entries, "com.nandbrew.stickyfix") is not None


def test_refresh_catalog_keeps_mvc_when_project_list_missing(paths: AppPaths) -> None:
    from sailwind_mod_sync.catalog.mvc import find_entry, refresh_catalog
    from sailwind_mod_sync.constants import (
        GITHUB_RAW_APP_MODLIST,
        GITHUB_RAW_APP_VERSIONS,
        JSDELIVR_APP_MODLIST,
        JSDELIVR_APP_VERSIONS,
        JSDELIVR_MODLIST,
        JSDELIVR_VERSIONS,
    )

    http = _CatalogHttp(
        {
            JSDELIVR_MODLIST: [
                {"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix"},
            ],
            JSDELIVR_VERSIONS: [
                {
                    "guid": "com.nandbrew.stickyfix",
                    "repo": "https://github.com/NANDbrew/StickyFix",
                    "version": "v1.0.0",
                }
            ],
        },
        missing={
            JSDELIVR_APP_MODLIST,
            JSDELIVR_APP_VERSIONS,
            GITHUB_RAW_APP_MODLIST,
            GITHUB_RAW_APP_VERSIONS,
        },
    )
    entries = refresh_catalog(paths, http)
    assert find_entry(entries, "com.nandbrew.stickyfix") is not None
    assert find_entry(entries, "com.dizzy.sailwind.calendar") is None


def test_merge_with_custom_leaves_custom_entries_unchanged() -> None:
    from sailwind_mod_sync.catalog.custom import merge_with_custom
    from sailwind_mod_sync.models import CatalogEntry

    mvc = merge_catalog(
        [{"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix"}],
        [{"guid": "com.nandbrew.stickyfix", "version": "v1.0.0"}],
    )
    custom = CatalogEntry(
        repo="https://github.com/example/fork",
        guids=["com.nandbrew.stickyfix", "com.nandbrew.stickyfix.extra"],
        primary_guid="com.nandbrew.stickyfix",
        name="StickyFix fork",
        latest_raw="v9.9.9",
        latest_version="9.9.9",
        available=True,
    )
    merged = merge_with_custom(mvc, [custom])
    extra = next(entry for entry in merged if entry.custom)
    assert extra.guids == ["com.nandbrew.stickyfix.extra"]
    assert custom.guids == ["com.nandbrew.stickyfix", "com.nandbrew.stickyfix.extra"]
    assert custom.primary_guid == "com.nandbrew.stickyfix"
    assert not custom.custom


def test_refresh_catalog_keeps_cache_when_download_is_empty(paths: AppPaths) -> None:
    from sailwind_mod_sync.catalog.mvc import find_entry, load_cached_catalog, refresh_catalog
    from sailwind_mod_sync.constants import JSDELIVR_MODLIST, JSDELIVR_VERSIONS

    full = _CatalogHttp(
        {
            JSDELIVR_MODLIST: [
                {"guid": "com.nandbrew.stickyfix", "repo": "https://github.com/NANDbrew/StickyFix"},
            ],
            JSDELIVR_VERSIONS: [{"guid": "com.nandbrew.stickyfix", "version": "v1.0.0"}],
        }
    )
    refresh_catalog(paths, full)
    empty = _CatalogHttp({JSDELIVR_MODLIST: [], JSDELIVR_VERSIONS: []})

    entries = refresh_catalog(paths, empty)

    assert find_entry(entries, "com.nandbrew.stickyfix") is not None
    cached = load_cached_catalog(paths)
    assert cached is not None and find_entry(cached, "com.nandbrew.stickyfix") is not None
    assert not list(paths.catalog_dir.glob("*.tmp"))
