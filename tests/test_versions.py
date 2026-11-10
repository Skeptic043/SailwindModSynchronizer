from sailwind_mod_sync.models import is_newer, parse_mod_version, version_key, PinnedMod, display_mod_name
from sailwind_mod_sync.ui.tables import version_sort_key
from sailwind_mod_sync.ui.version_dialog import (
    BROWSE_VERSIONS,
    merge_version_rows,
    pack_version_items,
)


def test_parse_strips_v_prefix() -> None:
    assert parse_mod_version("v1.3.3") == "1.3.3"
    assert parse_mod_version("1.2.0") == "1.2.0"
    assert parse_mod_version("Release 19.0") == "19.0"


def test_parse_none_is_unavailable() -> None:
    assert parse_mod_version("none") is None
    assert parse_mod_version("") is None
    assert parse_mod_version(None) is None


def test_is_newer() -> None:
    assert is_newer("v1.4.0", "1.3.4")
    assert not is_newer("1.3.3", "v1.3.3")
    assert version_key("v2.0.0") > version_key("v1.10.0")


def test_version_sort_key_orders_numeric_versions() -> None:
    assert version_sort_key("1.9.0") < version_sort_key("1.10.0")
    assert version_sort_key("v0.3.3") == version_sort_key("0.3.3")


def _pinned(version: str = "1.0.0") -> PinnedMod:
    return PinnedMod(
        guid="com.example.mod",
        version=version,
        repo="https://github.com/example/mod",
        version_raw=f"v{version}",
    )


def test_pack_version_items_lists_library_and_browse() -> None:
    items = pack_version_items(
        _pinned("1.0.0"),
        [("1.1.0", "v1.1.0"), ("1.0.0", "v1.0.0")],
        catalog_latest_raw="v1.2.0",
        catalog_latest_version="1.2.0",
        has_repo=True,
    )
    labels = [label for label, _data in items]
    assert labels[0] == "v1.2.0 (download)"
    assert labels[1] == "v1.1.0"
    assert labels[2] == "v1.0.0"
    assert labels[-1] == "More versions…"
    assert items[-1][1] == BROWSE_VERSIONS
    assert items[0][1] == ("1.2.0", "v1.2.0")


def test_pack_version_items_marks_missing_and_skips_browse_without_repo() -> None:
    items = pack_version_items(
        _pinned("0.9.0"),
        [],
        catalog_latest_raw="v1.0.0",
        catalog_latest_version="1.0.0",
        has_repo=False,
        missing=True,
    )
    labels = [label for label, _data in items]
    assert "v0.9.0 (not downloaded)" in labels
    assert "v1.0.0 (download)" in labels
    assert "More versions…" not in labels


def test_merge_version_rows_prefers_library_and_marks_current() -> None:
    rows = merge_version_rows(
        [("1.1.0", "v1.1.0")],
        [("1.1.0", "v1.1.0"), ("1.0.0", "v1.0.0")],
        "1.1.0",
    )
    assert [row.version for row in rows] == ["1.1.0", "1.0.0"]
    assert rows[0].in_library and rows[0].current
    assert "current" in rows[0].label
    assert "downloaded" in rows[0].label
    assert not rows[1].in_library
    assert "download" in rows[1].label


def test_display_mod_name_prefers_alias() -> None:
    assert (
        display_mod_name(
            "com.dizzy.sailwind.gamma",
            alias="Dizzy Gamma",
            catalog_name="dizzy_sailwind_mods",
            catalog_shared=True,
            plugin_folders=["Dizzy.Gamma"],
        )
        == "Dizzy Gamma"
    )


def test_display_mod_name_uses_catalog_when_unique() -> None:
    assert (
        display_mod_name(
            "com.nandbrew.stickyfix",
            catalog_name="StickyFix",
            plugin_folders=["StickyFix"],
        )
        == "StickyFix"
    )


def test_display_mod_name_uses_plugin_folder_when_catalog_is_shared() -> None:
    assert (
        display_mod_name(
            "com.dizzy.sailwind.gamma",
            catalog_name="dizzy_sailwind_mods",
            catalog_shared=True,
            plugin_folders=["Dizzy.Gamma"],
            repo="https://github.com/foxyv/dizzy_sailwind_mods",
        )
        == "Dizzy.Gamma"
    )


# Tags published by FluffyJesus/Realistic-Skies, oldest first; one uses "-F2" instead of "F-2".
REALISTIC_SKIES_FORK_TAGS = [
    "2.3.0F", "2.3.0F-1", "2.3.0-F2", "2.3.0F-2.1", "2.3.0F-3", "2.3.0F-3.1", "2.3.0F-4", "2.3.0F-4.1",
]


def test_suffixed_releases_stay_distinct_and_ordered() -> None:
    versions = [parse_mod_version(tag) for tag in REALISTIC_SKIES_FORK_TAGS]
    assert len(set(versions)) == len(versions)
    assert parse_mod_version("2.3.0-F2") == parse_mod_version("2.3.0F-2")
    keys = [version_key(tag) for tag in ["V2.3.0", *REALISTIC_SKIES_FORK_TAGS]]
    assert keys == sorted(keys)
    assert is_newer("2.3.0F-4.1", "2.3.0F-4")
    assert not is_newer("2.3.0F-4", "2.3.0F-4.1")


def test_prerelease_sorts_before_its_release() -> None:
    assert is_newer("v0.9.6", "v0.9.6-beta1")
    assert not is_newer("v0.9.6-beta1", "v0.9.6")
    assert is_newer("1.4.1-beta.11", "1.4.1-beta.10")
    assert is_newer("1.4.1-rc1", "1.4.1-beta.11")


def test_tag_without_number_is_a_version() -> None:
    from sailwind_mod_sync.models import version_id

    assert version_id("textures") == "textures"
    assert version_id("none") is None
    assert is_newer("textures", None)
    assert not is_newer("textures", "textures")
    # Unnumbered tags can't be ordered, so any different latest release is the update.
    assert is_newer("textures", "1.0.0")
    assert is_newer("v1.0.0", "textures")


def test_version_in_file_name_ignores_extension() -> None:
    from sailwind_mod_sync.models import version_text

    assert parse_mod_version("SaveCleaner-1.1.1.dll") == "1.1.1"
    assert version_text("Realistic Skies 2.3.0F-4.1") == "2.3.0F-4.1"


def test_pin_saved_before_suffix_support_recovers_exact_version() -> None:
    pinned = PinnedMod.from_dict({"guid": "g", "version": "2.3.0", "version_raw": "2.3.0F-4.1"})
    assert pinned.version == parse_mod_version("2.3.0F-4.1")
    unrelated = PinnedMod.from_dict({"guid": "g", "version": "1.2", "version_raw": "latest"})
    assert unrelated.version == "1.2"
