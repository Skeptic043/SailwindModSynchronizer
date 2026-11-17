from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QMessageBox, QWidget

from sailwind_mod_sync.packs.dependencies import DependencyPlan


def dependency_lines(plan: DependencyPlan, display_name: Callable[[str], str]) -> list[str]:
    """Return one line per mod ``plan`` needs, saying what adding it does."""
    return (
        [f"• {display_name(guid)}" for guid in plan.download]
        + [f"• {display_name(guid)} (needs a newer version)" for guid in plan.update]
        + [f"• {guid} (not in the catalog)" for guid in plan.missing]
    )


def _joined(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def ask_dependents(name: str, dependents: list[str], action: str, parent: QWidget | None = None) -> bool | None:
    """Ask whether to ``action`` (Remove or Disable) the mods that depend on ``name`` too.

    True does it to them as well, False only to ``name``, None cancels.
    """
    single = len(dependents) == 1
    box = QMessageBox(
        QMessageBox.Icon.Question,
        f"{action} {name}",
        f"{_joined(dependents)} {'requires' if single else 'require'} {name} in order to work.",
        QMessageBox.StandardButton.NoButton,
        parent,
    )
    box.setInformativeText(f"Would you like to {action.lower()} {'it' if single else 'them'} too?")
    also = box.addButton(action, QMessageBox.ButtonRole.AcceptRole)
    keep = box.addButton("Keep", QMessageBox.ButtonRole.NoRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(also)
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is also:
        return True
    if clicked is keep:
        return False
    return None


def ask_enable_required(name: str, required: list[str], parent: QWidget | None = None) -> bool:
    """Ask whether to turn on the turned-off mods ``name`` requires along with it."""
    names = _joined(required)
    box = QMessageBox(QMessageBox.Icon.Question, f"Enable {name}", f"{name} requires {names}.",
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    box.setInformativeText(f"Do you wish to enable {names} too?")
    box.setDefaultButton(QMessageBox.StandardButton.Yes)
    box.setEscapeButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def ask_dependencies(
    plan: DependencyPlan,
    name: str,
    display_name: Callable[[str], str],
    parent: QWidget | None = None,
    *,
    imported: bool = False,
) -> bool | None:
    """Ask whether to add what ``name`` needs: True adds it, False goes on without it, None cancels.

    After an import there is nothing to cancel, so the choice is only Add or Skip.
    """
    actionable = bool(plan.download or plan.update)
    text = f"{name} needs these mods:\n\n" + "\n".join(dependency_lines(plan, display_name))
    if plan.missing:
        text += "\n\nMods that aren't in the catalog have to be added by hand."
    box = QMessageBox(QMessageBox.Icon.Question, "Required mods", text, QMessageBox.StandardButton.NoButton, parent)
    if imported and not actionable:
        box.setDefaultButton(box.addButton("Close", QMessageBox.ButtonRole.RejectRole))
        box.exec()
        return False
    add = box.addButton("Add", QMessageBox.ButtonRole.AcceptRole)
    skip = None
    if actionable:
        box.setInformativeText("Add them?" if imported else "Add them too?")
        skip = box.addButton("Skip", QMessageBox.ButtonRole.RejectRole if imported else QMessageBox.ButtonRole.NoRole)
    cancel = None if imported else box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(add)
    box.setEscapeButton(cancel or skip)
    box.exec()
    clicked = box.clickedButton()
    if clicked is add:
        return actionable
    if clicked is skip:
        return False
    return None
