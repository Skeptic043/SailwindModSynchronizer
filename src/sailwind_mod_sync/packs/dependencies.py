"""Which mods a pack still needs when a mod is added.

Required mods come from the hard BepInDependency attributes of the downloaded DLLs, plus the short rule list
below for mods that need another mod without declaring it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sailwind_mod_sync.models import version_key


@dataclass(frozen=True)
class Rule:
    """``mod`` needs ``needs``; with ``required`` False, ``mod`` only does nothing without it."""

    mod: str
    needs: str
    required: bool = True


MOD_SAVE_BACKUPS = "com.raddude.modsavebackups"

# Requirements the mods don't declare, found by reading their source (2026-10 and 2026-11 audits).
# Rules match GUIDs ignoring case. Edit or remove a rule when its mod starts declaring the dependency or drops it.
RULES: tuple[Rule, ...] = (
    Rule("com.johng.goodtiming", MOD_SAVE_BACKUPS),  # calls ModSave.Save/Load directly
    Rule("com.johng.physicstuning", MOD_SAVE_BACKUPS),  # calls ModSave.Save/Load directly
    Rule("com.zorkinian.virtualcrew", MOD_SAVE_BACKUPS),  # saves crew data through ModSave
    Rule("com.nandbrew.sailwinddifficulty", "com.app24.sailwindmoddinghelper"),  # uses its extension methods
    Rule("com.TheOriginOfAllEvil.S_Topsail1", "com.nandbrew.shipyardexpansion"),  # The Gaff Pack adds SailScaler
    Rule("com.sailwind.removefirstofficerrequirement", "com.zorkinian.virtualcrew"),  # does nothing without it
    Rule("oldchroniandinghy", "pr0skynesis.dinghies"),  # quits without the Dinghies types
    Rule("com.nandbrew.ChronianMugFix", "com.Aquilartz.OldChronian", required=False),  # does nothing without it
)


@dataclass(frozen=True)
class Requirement:
    guid: str
    minimum_version: str = ""


@dataclass(frozen=True)
class Provider:
    """A pinned mod that provides a GUID, at the version its DLL declares."""

    pin_guid: str
    enabled: bool
    version: str


@dataclass
class DependencyPlan:
    """What adding ``mod`` needs: catalog mods to download, pinned mods to enable or update, and missing mods."""

    mod: str
    download: list[str] = field(default_factory=list)
    enable: list[str] = field(default_factory=list)
    update: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.download or self.enable or self.update or self.missing)

    @property
    def asks(self) -> bool:
        """Whether the player gets a choice; turned-off mods it needs just come back on, like Co-op's Player Model."""
        return bool(self.download or self.update or self.missing)


def rule_requirements(guids: Iterable[str]) -> list[Requirement]:
    """Return what the rules say any of ``guids`` requires."""
    wanted = {guid.casefold() for guid in guids}
    return [Requirement(rule.needs) for rule in RULES if rule.required and rule.mod.casefold() in wanted]


def plan_dependencies(
    mod: str,
    roots: list[Requirement],
    providers: dict[str, Provider],
    requirements_of: Callable[[str], list[Requirement]],
    catalog_guid: Callable[[str], str | None],
) -> DependencyPlan:
    """Plan what ``mod`` and, recursively, the mods it pulls in still need.

    ``providers`` maps case-folded GUIDs to the pins that provide them. ``catalog_guid`` returns the catalog GUID
    to download for a GUID, or None when the catalog doesn't have it. ``requirements_of`` returns what a catalog
    GUID is known to need before it is downloaded. Downloads are listed dependencies first.
    """
    plan = DependencyPlan(mod)
    seen = {mod.casefold()}
    pending = list(roots)
    downloads: list[str] = []
    while pending:
        requirement = pending.pop(0)
        key = requirement.guid.casefold()
        provider = providers.get(key)
        if provider is not None:
            if not provider.enabled and provider.pin_guid not in plan.enable:
                plan.enable.append(provider.pin_guid)
                pending.extend(requirements_of(provider.pin_guid))  # Turning it on needs what it needs.
            if (requirement.minimum_version and provider.pin_guid not in plan.update
                    and version_key(provider.version) < version_key(requirement.minimum_version)):
                plan.update.append(provider.pin_guid)
            continue
        if key in seen:
            continue
        seen.add(key)
        target = catalog_guid(requirement.guid)
        if target is None:
            plan.missing.append(requirement.guid)
            continue
        seen.add(target.casefold())
        downloads.append(target)
        pending.extend(requirements_of(target))
    plan.download = list(reversed(downloads))
    return plan
