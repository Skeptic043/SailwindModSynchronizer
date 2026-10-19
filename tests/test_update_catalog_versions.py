import io
import zipfile

from catalog_scripts import load_script
from plugin_metadata_fixture import managed_dll

from sailwind_mod_sync.http_util import HttpError

script = load_script("update_catalog_versions")

GAMMA = "com.dizzy.sailwind.gamma"
CALENDAR = "com.dizzy.sailwind.calendar"
DIZZY = "https://github.com/foxyv/dizzy_sailwind_mods"
HOOKS = "com.raddude82.hookshangmore"
HOOKS_REPO = "https://github.com/bryon82/SailwindHooksHangMore"


def _zip(folder: str, guid: str, name: str, version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{folder}/{folder}.dll", managed_dll(((guid, name, version),)))
        zf.writestr(f"{folder}/{folder}.assets", b"bundle")
    return buffer.getvalue()


class _ReleaseHttp:
    token = ""

    def __init__(self, releases: dict[str, tuple[str, str, bytes]], status_code: int | None = None) -> None:
        self.releases = releases
        self.status_code = status_code
        self.urls: list[str] = []
        self.downloads: list[str] = []

    def get_json(self, url, extra_headers=None, etag=None):
        self.urls.append(url)
        if self.status_code:
            raise HttpError(f"HTTP {self.status_code}", status_code=self.status_code)
        path = url.split("/repos/", 1)[1]
        repo = "https://github.com/" + path.split("/releases", 1)[0]
        tag, asset, _ = self.releases[repo]
        if "/releases/tags/" in path and not path.endswith(f"/{tag}"):
            raise HttpError("HTTP 404", status_code=404)
        payload = {
            "tag_name": tag,
            "name": tag,
            "html_url": f"{repo}/releases/tag/{tag}",
            "assets": [{"name": asset, "browser_download_url": f"{repo}/releases/download/{tag}/{asset}"}],
        }
        return payload, None, False

    def download(self, url, dest, progress=None):
        self.downloads.append(url)
        repo = url.split("/releases/download/", 1)[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.releases[repo][2])


def _mod(guid: str, repo: str, folder: str, version: str):
    return script.CatalogMod(guid, repo, [folder], version)


def test_catalog_mods_pairs_mods_with_their_versions() -> None:
    mods = script.catalog_mods(
        [
            {"guid": HOOKS, "repo": HOOKS_REPO, "name": "HooksHangMore", "plugin_folders": ["HooksHangMore"]},
            {"guid": "no.version", "repo": "https://github.com/a/B", "name": "B", "plugin_folders": []},
        ],
        [{"guid": HOOKS, "repo": HOOKS_REPO, "version": "v2.2.3"}],
    )
    assert mods == [script.CatalogMod(HOOKS, HOOKS_REPO, ["HooksHangMore"], "v2.2.3")]


def test_scan_takes_a_newer_release_that_still_installs_the_mod() -> None:
    http = _ReleaseHttp({HOOKS_REPO: ("v2.3.0", "HooksHangMore.zip", _zip("HooksHangMore", HOOKS, "HooksHangMore", "2.3.0"))})
    result = script.scan_versions(http, [_mod(HOOKS, HOOKS_REPO, "HooksHangMore", "v2.2.3")])
    assert [checked.update for checked in result.updated] == [script.VersionUpdate(HOOKS, HOOKS_REPO, "v2.2.3", "v2.3.0")]
    assert result.updated[0].asset == "HooksHangMore.zip"
    assert result.updated[0].files == ["HooksHangMore/HooksHangMore.assets", "HooksHangMore/HooksHangMore.dll"]
    assert result.rejected == [] and result.unchanged == 0


def test_scan_does_not_download_a_release_it_already_lists() -> None:
    http = _ReleaseHttp({HOOKS_REPO: ("v2.2.3", "HooksHangMore.zip", b"")})
    result = script.scan_versions(http, [_mod(HOOKS, HOOKS_REPO, "HooksHangMore", "v2.2.3")])
    assert result.updated == [] and result.unchanged == 1
    assert http.downloads == []


def test_scan_leaves_out_a_release_of_another_mod_from_the_same_repo() -> None:
    http = _ReleaseHttp({DIZZY: ("v0.3.4", "Dizzy.Gamma-0.3.4.zip", _zip("Dizzy.Gamma", GAMMA, "Dizzy Gamma", "0.3.4"))})
    mods = [_mod(CALENDAR, DIZZY, "Dizzy.Calendar", "v0.2.0"), _mod(GAMMA, DIZZY, "Dizzy.Gamma", "v0.3.3")]
    result = script.scan_versions(http, mods)
    assert [checked.update.guid for checked in result.updated] == [GAMMA]
    assert [(rejected.guid, rejected.reason) for rejected in result.rejected] == [
        (CALENDAR, "v0.3.4: Dizzy.Gamma-0.3.4.zip extracts to Dizzy.Gamma instead of Dizzy.Calendar"),
    ]
    assert sum(url.endswith("/releases/latest") for url in http.urls) == 1


def test_scan_leaves_out_a_release_whose_plugin_declares_another_guid() -> None:
    http = _ReleaseHttp({HOOKS_REPO: ("v3.0.0", "HooksHangMore.zip", _zip("HooksHangMore", "other.hooks", "Other", "3.0.0"))})
    result = script.scan_versions(http, [_mod(HOOKS, HOOKS_REPO, "HooksHangMore", "v2.2.3")])
    assert result.updated == []
    assert result.rejected[0].reason == (
        f"v3.0.0: no plugin declares {HOOKS} in HooksHangMore.zip; found: Other (other.hooks)"
    )


def test_scan_stops_when_github_rate_limits() -> None:
    http = _ReleaseHttp({}, status_code=429)
    mods = [_mod(HOOKS, HOOKS_REPO, "HooksHangMore", "v2.2.3"), _mod(GAMMA, DIZZY, "Dizzy.Gamma", "v0.3.3")]
    result = script.scan_versions(http, mods)
    assert "HTTP 429" in result.stopped
    assert result.updated == [] and result.rejected == []
    assert len(http.urls) == 1
