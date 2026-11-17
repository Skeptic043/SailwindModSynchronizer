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
