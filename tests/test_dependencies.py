from __future__ import annotations

from sailwind_mod_sync.packs.dependencies import (
    MOD_SAVE_BACKUPS,
    Provider,
    Requirement,
    plan_dependencies,
    rule_requirements,
)

VIRTUAL_CREW = "com.zorkinian.virtualcrew"
REMOVE_FIRST_OFFICER = "com.sailwind.removefirstofficerrequirement"


def _plan(roots, providers=None, needs=None, catalog=None, mod="com.example.mod"):
    needs = needs or {}
    catalog = catalog if catalog is not None else {}
    return plan_dependencies(
        mod,
        roots,
        {key.casefold(): value for key, value in (providers or {}).items()},
        lambda guid: needs.get(guid, []),
        lambda guid: catalog.get(guid.casefold()),
    )


def test_rules_chain_downloads_dependencies_first() -> None:
    catalog = {VIRTUAL_CREW: VIRTUAL_CREW, MOD_SAVE_BACKUPS: MOD_SAVE_BACKUPS}
    plan = _plan(
        rule_requirements([REMOVE_FIRST_OFFICER]),
        needs={VIRTUAL_CREW: rule_requirements([VIRTUAL_CREW])},
        catalog=catalog,
        mod=REMOVE_FIRST_OFFICER,
    )
    assert plan.download == [MOD_SAVE_BACKUPS, VIRTUAL_CREW]
    assert not plan.enable and not plan.update and not plan.missing

    # A turned-off Virtual Crew comes back on, and still brings Mod Save Backups.
    plan = _plan(
        rule_requirements([REMOVE_FIRST_OFFICER]),
        {VIRTUAL_CREW: Provider(VIRTUAL_CREW, False, "1.0.0")},
        needs={VIRTUAL_CREW: rule_requirements([VIRTUAL_CREW])},
        catalog=catalog,
        mod=REMOVE_FIRST_OFFICER,
    )
    assert (plan.enable, plan.download) == ([VIRTUAL_CREW], [MOD_SAVE_BACKUPS])


def test_removal_only_rules_never_add_mods() -> None:
    assert rule_requirements(["com.nandbrew.chronianmugfix"]) == []


def test_pinned_providers_are_enabled_or_updated_never_downgraded() -> None:
    providers = {
        "com.example.off": Provider("com.example.off", False, "1.0.0"),
        "com.example.old": Provider("com.example.old", True, "1.0.0"),
        "com.example.new": Provider("com.example.new", True, "3.0.0"),
    }
    plan = _plan(
        [Requirement("com.example.off"), Requirement("com.example.old", "2.0.0"), Requirement("com.example.new", "2.0.0")],
        providers,
    )
    assert plan.enable == ["com.example.off"]
    assert plan.update == ["com.example.old"]
    assert not plan.download


def test_bundled_plugin_counts_as_installed_and_aliases_map_to_the_catalog() -> None:
    # Co-op's pin provides the Player Model plugin it bundles.
    providers = {"com.diamondminer99.playermodel": Provider("com.sailwindcoop.mod", True, "0.2.0")}
    plan = _plan(
        [Requirement("com.diamondminer99.playermodel"), Requirement("com.raddude82.oldname"), Requirement("com.example.gone")],
        providers,
        catalog={"com.raddude82.oldname": "com.raddude.newname"},
    )
    assert plan.download == ["com.raddude.newname"]
    assert plan.missing == ["com.example.gone"]
    assert not plan.enable
