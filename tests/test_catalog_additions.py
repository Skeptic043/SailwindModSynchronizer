"""Exercise catalog entries against the layouts of their public release assets.

Fixtures contain filenames only, captured from the named release tags. They do
not redistribute or execute third-party binaries and require no network access.
"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from sailwind_mod_sync.catalog.mvc import merge_catalog
from sailwind_mod_sync.library.download import ensure_mod_artifact
from sailwind_mod_sync.library.store import LibraryStore
from sailwind_mod_sync.paths import AppPaths


# Each source is https://github.com/{repo}/releases/tag/{tag}.
# GUIDs were verified from BepInPlugin attributes in those released DLLs.
RELEASE_LAYOUTS = [
    (
        "alesparise/FFLParaw-Sailwind-Mod",
        "pr0skynesis.paraw",
        "v0.0.0-beta.4",
        "Paraw.zip",
        (
            "Paraw/FFLParaw.dll",
            "Paraw/FFLParawScripts.dll",
            "Paraw/paraw",
            "Paraw/shipyardlib",
            "Paraw/ShipyardLib.dll",
        ),
    ),
    (
        "AugSphere/BetterDrag",
        "com.AugSphere.BetterDrag",
        "v1.4.0",
        "BetterDrag-1.4.0.zip",
        (
            "BetterDrag-1.4.0/BetterDrag/BetterDrag.dll",
        ),
    ),
    (
        "bryon82/SailwindBetterNPCBoats",
        "com.raddude.betternpcboats",
        "v0.3.1",
        "BetterNPCBoats-0.3.1.zip",
        (
            "BetterNPCBoats/BetterNPCBoats.dll",
        ),
    ),
    (
        "DiamondMiner99/sailwind-playermodel",
        "com.diamondminer99.playermodel",
        "v0.1.5",
        "SailwindPlayerModel-v0.1.5.zip",
        (
            "BepInEx/plugins/SailwindPlayerModel/SailwindPlayerModel.dll",
        ),
    ),
    (
        "DiamondMiner99/sailwind-threesheets",
        "com.diamondminer99.threesheets",
        "v0.2.1",
        "ThreeSheets-v0.2.1.zip",
        (
            "BepInEx/plugins/ThreeSheets/ThreeSheets.dll",
        ),
    ),
    (
        "Dixiecapt/Sailwind-InstrumentDisplay-MOD",
        "com.dixiecapt.sailwind.marinershud",
        "Instrument-Display_v3.0.2",
        "InstrumentDisplay_3.0.2.zip",
        (
            "manifest.json",
            "icon.png",
            "README.md",
            "InstrumentDisplay.dll",
            "Guide.html",
            "discord_post.md",
            "InstrumentDisplayPlugin.cs",
        ),
    ),
    (
        "DogEggz01/Propeller",
        "DogEggz.JetPump",
        "1.1.0",
        "Propeller-1.1.0.zip",
        (
            "Propeller/assets/dogeggz.jetpump.assets",
            "Propeller/Propeller.dll",
        ),
    ),
    (
        "DogEggz01/TobaccoPotAndCigar",
        "DogEggz.Cigar",
        "1.2.3",
        "TobaccoPotAndCigar-1.2.3.zip",
        (
            "TobaccoPotAndCigar/TobaccoPotAndCigar.dll",
            "TobaccoPotAndCigar/assets/dogeggz.cigar.assets",
        ),
    ),
    (
        "foxyv/DizzyFirewoodBundle",
        "com.dizzy.sailwind.firewoodbundle",
        "v0.3.1",
        "Dizzy.FirewoodBundle-0.3.1.zip",
        (
            "Dizzy.FirewoodBundle/Dizzy.FirewoodBundle.dll",
            "Dizzy.FirewoodBundle/LICENSE",
        ),
    ),
    (
        "JohnGilb/SailwindVirtualCrew",
        "com.zorkinian.virtualcrew",
        "0.2.2",
        "SailwindVirtualCrew.zip",
        (
            "SailwindVirtualCrew/SailwindVirtualCrew.dll",
            "SailwindVirtualCrew/Sounds/fourbells.wav",
            "SailwindVirtualCrew/Sounds/shipbell.wav",
        ),
    ),
    (
        "KeeviDev/SailwindDeftHands",
        "com.keevi.defthands",
        "v1.2.0",
        "DeftHands-1.2.0.zip",
        (
            "DeftHands/DeftHands.dll",
        ),
    ),
    (
        "Kemylar/KemyNavigationTools",
        "com.larsonlogistics.sailwind.navsuite",
        "v1.2.2",
        "KemyNavigationToolsv1.2.2.zip",
        (
            "KemyNavigationTools/Inclinometer.dll",
            "KemyNavigationTools/navigation_suite_assets",
        ),
    ),
    (
        "NANDbrew/BorderExpander",
        "com.nandbrew.borderexpander",
        "v0.5.0",
        "BorderExpander.dll",
        (
            "BorderExpander.dll",
        ),
    ),
    (
        "NANDbrew/scrambled-seas",
        "com.nandbrew.scrambledseas",
        "v7.1.9",
        "ScrambledSeas.zip",
        (
            "ScrambledSeas/scrambledseas.assets",
            "ScrambledSeas/scrambledseas.assets.manifest",
            "ScrambledSeas/ScrambledSeas.dll",
        ),
    ),
    (
        "NANDbrew/ShipShape",
        "com.nandbrew.shipshape",
        "v1.3.1",
        "ShipShape.dll",
        (
            "ShipShape.dll",
        ),
    ),
    (
        "Skeptic043/Sailwind-Drop-Safety",
        "com.skeptic043.sailwind.dropsafety",
        "v1.1.0",
        "Sailwind_Drop_Safety-1.1.0.zip",
        (
            "manifest.json",
            "README.md",
            "LICENSE",
            "icon.png",
            "BepInEx/plugins/DropSafety/DropSafety.dll",
            "CHANGELOG.md",
        ),
    ),
    (
        "Skeptic043/Sailwind-Fast-Forward",
        "local.sailwind.fastforward",
        "v1.2.1",
        "Sailwind_Fast_Forward-1.2.1.zip",
        (
            "manifest.json",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "icon.png",
            "docs/BUILDING.md",
            "plugins/SailwindFastForward/SailwindFastForward.dll",
        ),
    ),
    (
        "Skeptic043/sailwind-new-beginnings",
        "com.skeptic043.sailwind.newbeginnings",
        "v1.2.2",
        "NewBeginnings-1.2.2.zip",
        (
            "manifest.json",
            "icon.png",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "BepInEx/plugins/NewBeginnings/NewBeginnings.dll",
        ),
    ),
    (
        "Skeptic043/Sailwind-Radio",
        "local.sailwind.radio",
        "v1.2.0",
        "SailwindRadio-1.2.0.zip",
        (
            "manifest.json",
            "icon.png",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "licenses/NLayer.txt",
            "BepInEx/plugins/SailwindRadio/SailwindRadio.dll",
            "BepInEx/plugins/SailwindRadio/NLayer.dll",
        ),
    ),
    (
        "Skeptic043/SailwindReceiptAlert",
        "skeptic043.sailwind.receiptalert",
        "v1.1.0",
        "Sailwind_Receipt_Alert-1.1.0.zip",
        (
            "manifest.json",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "icon.png",
            "plugins/SailwindReceiptAlert/SailwindReceiptAlert.dll",
        ),
    ),
    (
        "sum-rock/MoreSailwindSails",
        "com.august.moresailwindsails",
        "v0.2.1",
        "MoreSailwindSails.dll",
        (
            "MoreSailwindSails.dll",
        ),
    ),
    (
        "DogEggz01/EconomyOverhaul",
        "DogEggz.EconomyOverhaul",
        "0.9.0",
        "EconomyOverhaul-0.9.0.zip",
        (
            "EconomyOverhaul/EconomyOverhaul.dll",
        ),
    ),
    (
        "NANDbrew/HeadBonker",
        "com.nandbrew.headbonker",
        "v0.0.3",
        "HeadBonker.zip",
        (
            "HeadBonker/bonk.wav",
            "HeadBonker/HeadBonker.dll",
        ),
    ),
    (
        "DogEggz01/MooringLineFix",
        "DogEggz.Moorlinefix",
        "1.0.1",
        "MooringLineFix-1.0.1.zip",
        (
            "MooringLineFix/MooringLineFix.dll",
        ),
    ),
    (
        "DogEggz01/Hercules-Throw",
        "com.DogEggz.HerculesThrow",
        "0.2.0",
        "HerculesThrow-0.2.0.zip",
        (
            "HerculesThrow/HerculesThrow.dll",
        ),
    ),
    (
        "ExocetC3i/Sailwind_WaterIntakeModifier",
        "com.Exocet.WaterIntakePatch",
        "1.0.0",
        "WaterIntakePatch.zip",
        (
            "WaterIntakePatch/WaterIntakePatch.dll",
        ),
    ),
    (
        "JohnGilb/SailwindPhysicsTuning",
        "com.johng.physicstuning",
        "1.0.0",
        "PhysicsTuning.dll",
        (
            "PhysicsTuning.dll",
        ),
    ),
    (
        "Kemylar/SailwindWaterPushRestored",
        "com.kemylar.sailwind.waterpushrestored",
        "v1.0.2",
        "SailwindWaterPushRestored.dll",
        (
            "SailwindWaterPushRestored.dll",
        ),
    ),
    (
        "GilanRanger/GreatLakesSailwind",
        "com.greatlakes.navigation",
        "0.3.0",
        "GreatLakes-0.3.0.zip",
        (
            "CHANGELOG.md",
            "Companion/map.html",
            "Companion/README.txt",
            "icon.png",
            "LICENSE",
            "manifest.json",
            "plugins/GreatLakes/ATTRIBUTION.md",
            "plugins/GreatLakes/coastline.bin",
            "plugins/GreatLakes/fetch.bin",
            "plugins/GreatLakes/GreatLakes.Celestial.dll",
            "plugins/GreatLakes/GreatLakes.Charts.dll",
            "plugins/GreatLakes/GreatLakes.Coast.dll",
            "plugins/GreatLakes/GreatLakes.Layout.dll",
            "plugins/GreatLakes/GreatLakes.Nav.dll",
            "plugins/GreatLakes/GreatLakes.Plugin.dll",
            "plugins/GreatLakes/GreatLakes.Weather.dll",
            "plugins/GreatLakes/hyg-filtered.bin",
            "plugins/GreatLakes/hyg-LICENSE.txt",
            "plugins/GreatLakes/map_alankh.png",
            "plugins/GreatLakes/map_emerald.png",
            "plugins/GreatLakes/map_georef.txt",
            "plugins/GreatLakes/map_medi.png",
            "plugins/GreatLakes/map_ocean.png",
            "plugins/GreatLakes/port_names.txt",
            "plugins/GreatLakes/ports.bin",
            "plugins/GreatLakes/regions.txt",
            "plugins/GreatLakes/stars-named.json",
            "plugins/GreatLakes/watercolors.txt",
            "plugins/GreatLakes/windzones.txt",
            "README.md",
        ),
    ),
    (
        "Skeptic043/ship-shuffle",
        "com.skeptic043.sailwind.shipshuffle",
        "v1.0.0",
        "ShipShuffle-1.0.0.zip",
        (
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "manifest.json",
            "icon.png",
            "BepInEx/plugins/ShipShuffle/ShipShuffle.dll",
        ),
    ),
    (
        "DiamondMiner99/sailwind-rigbalance",
        "com.sailwindrigbalance.mod",
        "v0.16.1",
        "SailwindRigBalance-v0.16.1.zip",
        (
            "BepInEx/plugins/SailwindRigBalance/SailwindRigBalance.dll",
        ),
    ),
]


def _catalog_data():
    root = Path(__file__).resolve().parents[1] / "catalog"
    return (
        json.loads((root / "ModList.json").read_text(encoding="utf-8")),
        json.loads((root / "release_versions.json").read_text(encoding="utf-8")),
    )


def test_supplemental_catalog_has_unique_matching_release_entries():
    mods, versions = _catalog_data()
    mod_guids = [item["guid"] for item in mods]
    version_guids = [item["guid"] for item in versions]
    assert len(mod_guids) == len(set(mod_guids))
    assert len(version_guids) == len(set(version_guids))
    assert set(mod_guids) == set(version_guids)
    repos = {item["guid"]: item["repo"] for item in mods}
    assert all(item["repo"] == repos[item["guid"]] for item in versions)


@pytest.mark.parametrize(
    "repo,guid,tag,asset_name,files",
    RELEASE_LAYOUTS,
    ids=[row[0] for row in RELEASE_LAYOUTS],
)
def test_catalog_release_resolves_and_preserves_plugin_payload(
    tmp_path, repo, guid, tag, asset_name, files
):
    entries = merge_catalog(*_catalog_data())
    matching = [entry for entry in entries if guid in entry.guids]
    assert len(matching) == 1
    entry = matching[0]
    assert entry.repo == f"https://github.com/{repo}"
    assert entry.latest_raw == tag
    assert entry.available

    payload = b"fixture plugin or asset contents"
    if asset_name.lower().endswith(".zip"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in files:
                archive.writestr(name, payload)
        archive_bytes = buffer.getvalue()
    else:
        archive_bytes = payload
    asset_url = f"https://github.com/{repo}/releases/download/{tag}/{asset_name}"

    class ReleaseHttp:
        token = ""

        def get_json(self, url, extra_headers=None, etag=None):
            # Includes nonstandard and prerelease tags, which must be preserved.
            assert url == f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
            return {
                "tag_name": tag,
                "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
                "assets": [{"name": asset_name, "browser_download_url": asset_url}],
            }, None, False

        def download(self, url, dest, progress=None):
            assert url == asset_url
            dest.write_bytes(archive_bytes)

    store = LibraryStore(AppPaths(tmp_path))
    meta = ensure_mod_artifact(
        store,
        ReleaseHttp(),
        guid=entry.primary_guid,
        repo=entry.repo,
        version=entry.latest_version,
        version_raw=entry.latest_raw,
        plugin_folders=entry.plugin_folders,
    )
    assert meta.plugin_folders == entry.plugin_folders
    assert meta.version_raw == tag
    extracted = store.mod_extracted(meta.guid, meta.version)
    extracted_names = {path.name for path in extracted.rglob("*") if path.is_file()}
    # All runtime binaries and asset files must survive folder filtering.
    for name in files:
        runtime_file = name.lower().endswith(".dll") or (
            "/" in name and not name.startswith(("docs/", "licenses/", "Companion/"))
        )
        if runtime_file:
            assert Path(name).name in extracted_names
            if repo == "GilanRanger/GreatLakesSailwind":
                # The suite loads its data beside the DLLs in the same folder.
                dlls = list(extracted.rglob("GreatLakes.Plugin.dll"))
                assert len(dlls) == 1
                assert (dlls[0].parent / Path(name).name).is_file()



def test_great_lakes_is_a_single_bundle():
    entries = merge_catalog(*_catalog_data())
    matching = [entry for entry in entries if entry.repo.endswith("/GreatLakesSailwind")]
    assert len(matching) == 1
    entry = matching[0]
    assert entry.name == "Great Lakes"
    assert entry.primary_guid == "com.greatlakes.navigation"
    assert entry.plugin_folders == ["GreatLakes"]


def test_rig_balance_resolves_current_guid_once():
    entries = merge_catalog(*_catalog_data())
    matching = [entry for entry in entries if entry.repo.endswith("/sailwind-rigbalance")]
    assert len(matching) == 1
    assert matching[0].guids == ["com.sailwindrigbalance.mod"]
    assert matching[0].latest_raw == "v0.16.1"
