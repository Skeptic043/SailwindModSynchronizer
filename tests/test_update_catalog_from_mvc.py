import io
import zipfile

from catalog_scripts import load_script
from plugin_metadata_fixture import managed_dll

from sailwind_mod_sync.catalog.mvc import merge_catalog, shared_catalog
from sailwind_mod_sync.http_util import HttpError

REPO = "https://github.com/bryon82/SailwindHooksHangMore"
GUID = "com.raddude82.hookshangmore"


script = load_script("update_catalog_from_mvc")


class _ReleaseHttp:
    token = ""

    def __init__(self, archive: bytes, status_code: int | None = None) -> None:
        self.archive = archive
        self.status_code = status_code
        self.urls: list[str] = []

    def get_json(self, url, extra_headers=None, etag=None):
        self.urls.append(url)
        if self.status_code:
            raise HttpError(f"HTTP {self.status_code}", status_code=self.status_code)
        repo = url.split("/repos/", 1)[1].split("/releases", 1)[0]
        payload = {
            "tag_name": "v2.2.3",
            "name": "v2.2.3",
            "html_url": f"https://github.com/{repo}/releases/tag/v2.2.3",
            "assets": [{"name": "HooksHangMore.zip", "browser_download_url": f"https://github.com/{repo}/HooksHangMore.zip"}],
        }
        return payload, None, False

    def download(self, url, dest, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.archive)


def _archive(plugins: tuple[tuple[str, str, str], ...] = ((GUID, "HooksHangMore", "2.2.3"),)) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("HooksHangMore/HooksHangMore.dll", managed_dll(plugins))
    return buffer.getvalue()


def _mvc(guid: str = GUID, repo: str = REPO) -> list:
    return merge_catalog([{"guid": guid, "repo": repo}], [{"guid": guid, "repo": repo, "version": "v2.2.3"}])


def test_unnamed_mods_leaves_named_mods_alone() -> None:
    own = [{"guid": GUID, "repo": REPO, "name": "Hooks Hang More", "plugin_folders": ["HooksHangMore"]}]
    targets, skipped = script.unnamed_mods(_mvc(), own)
    assert targets == [] and skipped == []


def test_unnamed_mods_reports_guid_listed_from_another_repo() -> None:
    own = [{"guid": GUID, "repo": "https://github.com/someone/HooksFork", "name": "Hooks Fork", "plugin_folders": []}]
    targets, skipped = script.unnamed_mods(_mvc(), own)
    assert targets == []
    assert [(item.guid, item.reason) for item in skipped] == [
        (GUID, f"catalog/ModList.json lists {GUID} from https://github.com/someone/HooksFork"),
    ]


def test_unnamed_mods_reports_repository_it_cannot_download() -> None:
    targets, skipped = script.unnamed_mods(_mvc(repo="https://thunderstore.io/c/sailwind/p/Someone/Mod"), [])
    assert targets == []
    assert [item.reason for item in skipped] == ["not a GitHub or GitLab repository"]


def test_name_mods_reads_the_declared_plugin_name() -> None:
    targets, _ = script.unnamed_mods(_mvc(), [])
    result = script.name_mods(_ReleaseHttp(_archive()), targets)
    assert result.named == [script.NamedMod(GUID, REPO, "HooksHangMore", ["HooksHangMore"], "v2.2.3")]
    assert result.skipped == [] and result.stopped == ""


def test_name_mods_skips_release_without_the_mods_plugin() -> None:
    targets, _ = script.unnamed_mods(_mvc(), [])
    result = script.name_mods(_ReleaseHttp(_archive((("other.mod", "Other", "1.0.0"),))), targets)
    assert result.named == []
    assert len(result.skipped) == 1
    assert "no plugin declares com.raddude82.hookshangmore" in result.skipped[0].reason
    assert "Other (other.mod)" in result.skipped[0].reason


def test_name_mods_stops_when_github_rate_limits() -> None:
    targets, _ = script.unnamed_mods(
        merge_catalog(
            [{"guid": GUID, "repo": REPO}, {"guid": "com.raddude82.radfixes", "repo": "https://github.com/bryon82/SailwindRadFixes"}],
            [],
        ),
        [],
    )
    http = _ReleaseHttp(_archive(), status_code=403)
    result = script.name_mods(http, targets)
    assert result.named == [] and result.skipped == []
    assert "HTTP 403" in result.stopped
    assert len(http.urls) == 1


def test_add_named_mods_appends_matching_entries_in_name_order() -> None:
    own_mods = [{"guid": "a.mod", "repo": "https://github.com/a/A", "name": "A", "plugin_folders": ["A"]}]
    own_versions = [{"guid": "a.mod", "repo": "https://github.com/a/A", "version": "v1"}]
    named = [
        script.NamedMod("z.mod", "https://github.com/z/Z", "Zeta", ["Zeta"], "v3"),
        script.NamedMod("b.mod", "https://github.com/b/B", "Beta", ["Beta", "BetaData"], "v2"),
    ]
    mods, versions = script.add_named_mods(own_mods, own_versions, named)
    assert [item["name"] for item in mods] == ["A", "Beta", "Zeta"]
    assert mods[1] == {"guid": "b.mod", "repo": "https://github.com/b/B", "name": "Beta", "plugin_folders": ["Beta", "BetaData"]}
    assert [item["version"] for item in versions] == ["v1", "v2", "v3"]
    assert own_mods == [{"guid": "a.mod", "repo": "https://github.com/a/A", "name": "A", "plugin_folders": ["A"]}]


def test_add_named_mods_names_an_unnamed_entry_in_place() -> None:
    own_mods = [{"guid": GUID, "repo": REPO, "plugin_folders": ["HooksHangMore"]}]
    own_versions = [{"guid": GUID, "repo": REPO, "version": "v2.2.0"}]
    named = [script.NamedMod(GUID, REPO, "HooksHangMore", ["Other"], "v2.2.3")]
    mods, versions = script.add_named_mods(own_mods, own_versions, named)
    assert mods == [{"guid": GUID, "repo": REPO, "name": "HooksHangMore", "plugin_folders": ["HooksHangMore"]}]
    assert versions == own_versions


def test_added_names_reach_the_shared_catalog() -> None:
    mods, versions = script.add_named_mods([], [], [script.NamedMod(GUID, REPO, "HooksHangMore", ["HooksHangMore"], "v2.2.3")])
    shared = shared_catalog(_mvc(), merge_catalog(mods, versions), {})
    assert [entry.name for entry in shared] == ["HooksHangMore"]


def test_newer_versions_takes_only_newer_mvc_versions() -> None:
    repos = {guid: f"https://github.com/example/{guid}" for guid in ("a.mod", "b.mod", "c.mod", "d.mod", "e.mod")}
    mvc = merge_catalog(
        [{"guid": guid, "repo": repos[guid]} for guid in ("a.mod", "b.mod", "c.mod", "d.mod")],
        [
            {"guid": "a.mod", "version": "v1.2.0"},
            {"guid": "b.mod", "version": "v1.0.0"},
            {"guid": "c.mod", "version": "v0.9.0"},
            {"guid": "d.mod", "version": "none"},
        ],
    )
    own = [
        {"guid": "a.mod", "repo": repos["a.mod"], "version": "1.1.9"},
        {"guid": "b.mod", "repo": repos["b.mod"], "version": "1.0.0"},
        {"guid": "c.mod", "repo": repos["c.mod"], "version": "v1.0.0"},
        {"guid": "d.mod", "repo": repos["d.mod"], "version": "v1.0.0"},
        {"guid": "e.mod", "repo": repos["e.mod"], "version": "v1.0.0"},
    ]
    assert script.newer_versions(mvc, own) == [script.VersionUpdate("a.mod", repos["a.mod"], "1.1.9", "v1.2.0")]


def test_newer_versions_ignores_mvc_mod_from_another_repo() -> None:
    mvc = merge_catalog([{"guid": GUID, "repo": REPO}], [{"guid": GUID, "version": "v3.0.0"}])
    own = [{"guid": GUID, "repo": "https://github.com/someone/HooksFork", "version": "v2.2.3"}]
    assert script.newer_versions(mvc, own) == []
