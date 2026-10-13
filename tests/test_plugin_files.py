from __future__ import annotations

import json
from pathlib import Path

import pytest
from shipdata_fixture import EDITED_SHIPDATA, GALLUS, SHIPDATA, ship_json

from sailwind_mod_sync.packs.plugin_files import (
    apply_file_plan,
    ask_conflicts,
    baseline_from_package,
    merge_json,
    plan_mod_files,
)

GUID = "com.example.mod"


def write_tree(root: Path, files: dict[str, bytes]) -> Path:
    for relpath, data in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def read_tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def sync(tmp_path: Path, previous: dict | None, disk: dict, package: dict, resolver=None, damaged: dict | None = None):
    """Install ``package`` over ``disk``, which was installed from ``previous`` (None: no record).

    ``damaged`` replaces recorded copies after their hashes were taken.
    """
    plugins = write_tree(tmp_path / "plugins", disk)
    new = write_tree(tmp_path / "new", package)
    baseline = baseline_from_package(write_tree(tmp_path / "old", previous), ["Mod"]) if previous is not None else None
    write_tree(tmp_path / "old", damaged or {})
    plan = plan_mod_files(GUID, new, ["Mod"], plugins, ["Mod"], baseline)
    apply_file_plan(plan, ask_conflicts([plan], resolver), plugins)
    return read_tree(plugins), plan.conflicts


@pytest.mark.parametrize(
    ("previous", "disk", "package", "expected"),
    [
        pytest.param({"Mod/a.cfg": b"v1"}, {"Mod/a.cfg": b"mine"}, {"Mod/a.cfg": b"v1"},
                     {"Mod/a.cfg": b"mine"}, id="edited-file-survives-unchanged-package"),
        pytest.param({"Mod/a.cfg": b"v1"}, {"Mod/a.cfg": b"v1"}, {"Mod/a.cfg": b"v2"},
                     {"Mod/a.cfg": b"v2"}, id="untouched-file-updates"),
        pytest.param({"Mod/a.cfg": b"v1"}, {}, {"Mod/a.cfg": b"v1", "Mod/b.cfg": b"new"},
                     {"Mod/a.cfg": b"v1", "Mod/b.cfg": b"new"}, id="missing-files-copied"),
        pytest.param({"Mod/m.dll": b"v1"}, {"Mod/m.dll": b"v1", "Mod/scramble_1.json": b"{}"}, {"Mod/m.dll": b"v1"},
                     {"Mod/m.dll": b"v1", "Mod/scramble_1.json": b"{}"}, id="runtime-file-kept"),
        pytest.param({"Mod/m.dll": b"v1", "Mod/old.txt": b"x"}, {"Mod/m.dll": b"v1", "Mod/old.txt": b"x"},
                     {"Mod/m.dll": b"v2"}, {"Mod/m.dll": b"v2"}, id="stale-package-file-deleted"),
        pytest.param({"Mod/m.dll": b"v1", "Mod/old.txt": b"x"}, {"Mod/m.dll": b"v1", "Mod/old.txt": b"mine"},
                     {"Mod/m.dll": b"v2"}, {"Mod/m.dll": b"v2", "Mod/old.txt": b"mine"}, id="dropped-edited-file-kept"),
        pytest.param({"Mod/m.dll": b"v1"}, {"Mod/m.dll": b"swapped", "Mod/extra.dll": b"hand-added"},
                     {"Mod/m.dll": b"v1"}, {"Mod/m.dll": b"v1"}, id="dlls-forced-to-package"),
        pytest.param(None, {"Mod/a.cfg": b"mine", "Mod/m.dll": b"v1"}, {"Mod/a.cfg": b"v1", "Mod/m.dll": b"v1"},
                     {"Mod/a.cfg": b"mine", "Mod/m.dll": b"v1"}, id="no-record-difference-defaults-to-mine"),
    ],
)
def test_plan_keeps_changed_files_and_follows_the_package(tmp_path, previous, disk, package, expected):
    result, _ = sync(tmp_path, previous, disk, package)
    assert result == expected


@pytest.mark.parametrize("choice", ["none", "keep", "new"])
def test_file_both_sides_changed_is_one_conflict(tmp_path, choice):
    asked = []

    def resolver(conflicts):
        asked.append(conflicts)
        return set(conflicts) if choice == "new" else set()

    result, conflicts = sync(
        tmp_path, {"Mod/a.cfg": b"v1"}, {"Mod/a.cfg": b"mine"}, {"Mod/a.cfg": b"v2"},
        resolver=None if choice == "none" else resolver,
    )
    assert [(item.guid, item.relpath, item.merged) for item in conflicts] == [(GUID, "Mod/a.cfg", False)]
    assert asked == ([] if choice == "none" else [conflicts])
    assert result["Mod/a.cfg"] == (b"v2" if choice == "new" else b"mine")


def test_json_edits_kept_while_new_entries_arrive(tmp_path):
    # Better Drag: the player tuned shipped examples; the new version adds a GALLUS entry.
    mine = {"BOAT JUNK (20)": {"drag": 9.0}, "BOAT BRIG (10)": {"drag": 1.5}}
    result, conflicts = sync(
        tmp_path, {"Mod/ship.json": ship_json(SHIPDATA)},
        {"Mod/ship.json": b"\xef\xbb\xbf" + ship_json(mine, indent=4)},
        {"Mod/ship.json": ship_json({**SHIPDATA, **GALLUS})}, resolver=lambda _c: pytest.fail("No conflict expected"),
    )
    assert conflicts == []
    merged = result["Mod/ship.json"].decode("utf-8")
    assert json.loads(merged) == {**mine, **GALLUS}
    assert list(json.loads(merged)) == ["BOAT JUNK (20)", "BOAT BRIG (10)", "BOAT GALLUS (197)"]
    assert merged.endswith("}\n") and '\n  "BOAT JUNK (20)"' in merged


def test_failed_merged_write_leaves_the_players_file_whole_and_the_next_sync_merges(tmp_path, monkeypatch):
    previous = {"Mod/ship.json": ship_json(SHIPDATA)}
    disk = {"Mod/ship.json": ship_json(EDITED_SHIPDATA)}
    package = {"Mod/ship.json": ship_json({**SHIPDATA, **GALLUS})}
    real_replace = Path.replace

    def replace(source, destination):
        # Whatever the write goes through, its last step onto the player's file fails.
        if Path(destination).name == "ship.json" and Path(destination).parent.parent.name == "plugins":
            raise OSError("disk full")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="disk full"):
        sync(tmp_path, previous, disk, package)
    assert read_tree(tmp_path / "plugins") == disk
    monkeypatch.setattr(Path, "replace", real_replace)
    result, _ = sync(tmp_path, previous, disk, package)
    assert json.loads(result["Mod/ship.json"]) == {**EDITED_SHIPDATA, **GALLUS}


@pytest.mark.parametrize("choice", ["keep", "new"])
def test_json_leaf_conflict_defaults_to_mine_and_still_adds_entries(tmp_path, choice):
    package = {**SHIPDATA, "BOAT BRIG (10)": {"drag": 0.5}, **GALLUS}
    result, conflicts = sync(
        tmp_path, {"Mod/ship.json": ship_json(SHIPDATA)}, {"Mod/ship.json": ship_json(EDITED_SHIPDATA)},
        {"Mod/ship.json": ship_json(package)}, resolver=lambda c: set(c) if choice == "new" else set(),
    )
    assert [(item.relpath, item.entries, item.merged) for item in conflicts] == [
        ("Mod/ship.json", ("BOAT BRIG (10)/drag",), True)
    ]
    expected = package if choice == "new" else {**EDITED_SHIPDATA, **GALLUS}
    assert json.loads(result["Mod/ship.json"]) == expected


@pytest.mark.parametrize(
    ("mine", "damaged"),
    [
        pytest.param(b"{ not json", None, id="unparseable"),
        # Merging against a guessed base could bring back entries the player deleted.
        pytest.param(ship_json(EDITED_SHIPDATA), {"Mod/ship.json": b"{}"}, id="damaged-recorded-copy"),
    ],
)
def test_json_without_a_usable_base_or_file_is_a_whole_file_conflict(tmp_path, mine, damaged):
    result, conflicts = sync(
        tmp_path, {"Mod/ship.json": ship_json(SHIPDATA)}, {"Mod/ship.json": mine},
        {"Mod/ship.json": ship_json({**SHIPDATA, **GALLUS})}, damaged=damaged,
    )
    assert [(item.entries, item.merged) for item in conflicts] == [((), False)]
    assert result["Mod/ship.json"] == mine


@pytest.mark.parametrize(
    ("base", "mine", "new", "expected", "conflicts"),
    [
        pytest.param({"a": 1, "b": 2}, {"a": 1, "b": 2}, {"a": 1}, {"a": 1}, [], id="package-removal-of-untouched-entry"),
        pytest.param({"a": 1, "b": 2}, {"a": 1, "b": 5}, {"a": 1}, {"a": 1, "b": 5}, [], id="package-removal-keeps-edit"),
        pytest.param({"a": 1, "b": 2}, {"a": 1}, {"a": 1, "b": 2}, {"a": 1}, [], id="my-removal-stays"),
        pytest.param({"a": 1, "b": 2}, {"a": 1}, {"a": 1, "b": 3}, {"a": 1}, ["b"], id="my-removal-of-changed-entry"),
        pytest.param({"a": {"x": 1, "y": 1}}, {"a": {"x": 2, "y": 1}}, {"a": {"x": 1, "y": 3}},
                     {"a": {"x": 2, "y": 3}}, [], id="nested-objects-merge"),
        pytest.param({"a": [1]}, {"a": [1, 2]}, {"a": [1, 3]}, {"a": [1, 2]}, ["a"], id="arrays-are-atomic"),
        pytest.param({}, {"a": 1}, {"a": 2, "b": 1}, {"a": 1, "b": 1}, ["a"], id="no-base"),
        pytest.param({"a": 1, "b": {"x": 1.5}}, {"a": 1, "b": {"x": 1}}, {"a": True, "b": {"x": 1.0}},
                     {"a": True, "b": {"x": 1}}, ["b/x"], id="types-differ"),
    ],
)
def test_merge_json_rules(base, mine, new, expected, conflicts):
    merged, found = merge_json(base, mine, new)
    # Compare as JSON text, where true, 1 and 1.0 differ.
    assert (json.dumps(merged), found) == (json.dumps(expected), conflicts)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        pytest.param({"Mod/b.cfg": b"mine", "Mod/run.json": b"{}"}, {"Mod/b.cfg": b"mine", "Mod/run.json": b"{}"},
                     id="changed-files-stay"),
        pytest.param({}, {}, id="untouched-folder-removed"),
    ],
)
def test_disabled_mod_keeps_only_changed_files(tmp_path, extra, expected):
    previous = {"Mod/m.dll": b"v1", "Mod/a.cfg": b"v1", "Mod/b.cfg": b"v1"}
    plugins = write_tree(tmp_path / "plugins", {**previous, **extra})
    baseline = baseline_from_package(write_tree(tmp_path / "old", previous), ["Mod"])
    plan = plan_mod_files(GUID, None, [], plugins, ["Mod"], baseline)
    apply_file_plan(plan, set(), plugins)
    assert read_tree(plugins) == expected
    assert (plugins / "Mod").exists() == bool(expected)
