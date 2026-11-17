from __future__ import annotations

import pytest

from sailwind_mod_sync.packs.dependencies import DependencyPlan


@pytest.fixture
def click(monkeypatch):
    """Answer the next message box by clicking the button with the given text, and record the buttons shown."""
    from PySide6.QtWidgets import QApplication, QMessageBox

    QApplication.instance() or QApplication([])
    shown: list[list[str]] = []

    def answer(label: str):
        def exec_(box: QMessageBox) -> int:
            buttons = {button.text(): button for button in box.buttons()}
            shown.append(sorted(buttons))
            buttons[label].click()
            return 0
        monkeypatch.setattr(QMessageBox, "exec", exec_)
        return shown

    return answer


@pytest.mark.parametrize("label, expected", [("Add", True), ("Skip", False), ("Cancel", None)])
def test_mod_question_answers(click, label: str, expected: bool | None) -> None:
    from sailwind_mod_sync.ui.dependency_dialog import ask_dependencies

    shown = click(label)
    plan = DependencyPlan("com.example.mod", download=["com.example.api"])
    assert ask_dependencies(plan, "Example", str) is expected
    assert shown == [["Add", "Cancel", "Skip"]]


def test_import_with_only_uncataloged_mods_just_informs(click) -> None:
    from sailwind_mod_sync.ui.dependency_dialog import ask_dependencies

    shown = click("Close")
    plan = DependencyPlan("Shared", missing=["com.example.gone"])
    assert ask_dependencies(plan, "Shared", str, imported=True) is False
    assert shown == [["Close"]]


@pytest.mark.parametrize("label, expected", [("Remove", True), ("Keep", False), ("Cancel", None)])
def test_dependents_question_answers(click, label: str, expected: bool | None) -> None:
    from sailwind_mod_sync.ui.dependency_dialog import ask_dependents

    shown = click(label)
    assert ask_dependents("Old Chronian", ["Chronian Mug Fix"], "Remove") is expected
    assert shown == [["Cancel", "Keep", "Remove"]]


def test_dependents_question_wording(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from sailwind_mod_sync.ui.dependency_dialog import ask_dependents

    QApplication.instance() or QApplication([])
    texts: list[tuple[str, str]] = []

    def exec_(box: QMessageBox) -> int:
        texts.append((box.text(), box.informativeText()))
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    ask_dependents("Old Chronian", ["Chronian Mug Fix"], "Remove")
    ask_dependents("Mod Save Backups", ["Virtual Crew", "Good Timing"], "Disable")
    assert texts == [
        ("Chronian Mug Fix requires Old Chronian in order to work.", "Would you like to remove it too?"),
        ("Virtual Crew and Good Timing require Mod Save Backups in order to work.", "Would you like to disable them too?"),
    ]


@pytest.mark.parametrize("button, expected", [("Yes", True), ("No", False)])
def test_enable_question(monkeypatch, button: str, expected: bool) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from sailwind_mod_sync.ui.dependency_dialog import ask_enable_required

    QApplication.instance() or QApplication([])
    shown: list[str] = []

    def exec_(box: QMessageBox) -> QMessageBox.StandardButton:
        shown.append(f"{box.text()} {box.informativeText()}")
        return getattr(QMessageBox.StandardButton, button)

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    assert ask_enable_required("Better Ports", ["Custom Island API"]) is expected
    assert shown == ["Better Ports requires Custom Island API. Do you wish to enable Custom Island API too?"]
