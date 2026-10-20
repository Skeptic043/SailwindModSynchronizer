import json
from pathlib import Path

import pytest
from catalog_scripts import load_script

from sailwind_mod_sync.models import RemoteRelease

tools = load_script("catalog_tools")

REPO = "https://github.com/bryon82/SailwindHooksHangMore"
GUID = "com.raddude82.hookshangmore"


@pytest.mark.parametrize("name", ["ModList.json", "release_versions.json"])
def test_format_catalog_reproduces_the_catalog_files(name: str) -> None:
    path = Path(__file__).resolve().parents[1] / "catalog" / name
    text = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    assert tools.format_catalog(json.loads(text)) == text


def test_write_catalog_file_keeps_crlf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "release_versions.json"
    path.write_bytes(b"[\r\n]\r\n")
    tools.write_catalog_file(path, [{"guid": GUID, "repo": REPO, "version": "v2.2.3"}])
    assert path.read_bytes() == (
        b'[\r\n  {\r\n    "guid": "com.raddude82.hookshangmore",\r\n'
        b'    "repo": "https://github.com/bryon82/SailwindHooksHangMore",\r\n    "version": "v2.2.3"\r\n  }\r\n]\r\n'
    )


@pytest.mark.parametrize(
    "tag,name,expected",
    [("v2.2.3", "Hooks 2.2.3", "v2.2.3"), ("release", "0.0.1", "0.0.1"), ("latest", "Latest build", "latest")],
)
def test_release_version_raw_matches_the_app(tag: str, name: str, expected: str) -> None:
    assert tools.release_version_raw(RemoteRelease(tag=tag, name=name, assets=[])) == expected


def test_apply_version_updates_changes_only_the_updated_mods() -> None:
    own = [
        {"guid": GUID, "repo": REPO, "version": "v2.2.3"},
        {"guid": "other.mod", "repo": "https://github.com/a/Other", "version": "v1.0.0"},
    ]
    updated = tools.apply_version_updates(own, [tools.VersionUpdate(GUID, REPO, "v2.2.3", "v2.3.0")])
    assert updated == [
        {"guid": GUID, "repo": REPO, "version": "v2.3.0"},
        {"guid": "other.mod", "repo": "https://github.com/a/Other", "version": "v1.0.0"},
    ]
    assert own[0]["version"] == "v2.2.3"
