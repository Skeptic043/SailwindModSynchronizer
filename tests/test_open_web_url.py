from __future__ import annotations

import os
import sys

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from sailwind_mod_sync.ui import links
from sailwind_mod_sync.ui.links import open_web_url

URL = "https://github.com/settings/tokens/new"
# Grabbed before conftest's autouse fixture swaps it out.
real_xdg_open = links._xdg_open


@pytest.fixture(autouse=True)
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_qt_opens_url(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(links.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    monkeypatch.setattr(links.webbrowser, "open", lambda url: pytest.fail("fallback should not run"))
    assert open_web_url(URL)
    assert opened == [URL]


def test_falls_back_to_webbrowser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(links.QDesktopServices, "openUrl", lambda url: False)
    monkeypatch.setattr(links.webbrowser, "open", lambda url: opened.append(url) or True)
    assert open_web_url(URL)
    assert opened == [URL]


def test_shows_url_when_nothing_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(links.QDesktopServices, "openUrl", lambda url: False)

    def fail(url: str) -> bool:
        raise links.webbrowser.Error("no browser")

    monkeypatch.setattr(links.webbrowser, "open", fail)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: shown.append(box.text()) or 0)
    assert not open_web_url(URL)
    assert len(shown) == 1 and URL in shown[0]


def test_linux_failed_xdg_open_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(links, "_xdg_open", lambda url: False)
    monkeypatch.setattr(links.QDesktopServices, "openUrl", lambda url: pytest.fail("Qt would report a false success"))
    monkeypatch.setattr(links.webbrowser, "open", lambda url: pytest.fail("webbrowser would rerun xdg-open"))
    monkeypatch.setattr(QMessageBox, "exec", lambda box: shown.append(box.text()) or 0)
    assert not open_web_url(URL)
    assert len(shown) == 1 and URL in shown[0]


def test_linux_without_xdg_open_uses_qt(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(links.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    assert open_web_url(URL)
    assert opened == [URL]


posix_only = pytest.mark.skipif(os.name == "nt", reason="needs a shell script as xdg-open")


def _fake_xdg_open(monkeypatch: pytest.MonkeyPatch, tmp_path, script: str) -> None:
    fake = tmp_path / "xdg-open"
    fake.write_text("#!/bin/sh\n" + script + "\n")
    fake.chmod(0o755)
    monkeypatch.setattr(links.shutil, "which", lambda name: str(fake) if name == "xdg-open" else None)


@posix_only
@pytest.mark.parametrize(
    ("script", "expected"),
    [("exit 0", True), ("echo 'no handler' >&2; exit 4", False), ("sleep 10", True)],
    ids=["succeeds", "fails", "still-running"],
)
def test_xdg_open_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path, script: str, expected: bool) -> None:
    monkeypatch.setattr(links, "_XDG_OPEN_WAIT_SECONDS", 0.5)
    _fake_xdg_open(monkeypatch, tmp_path, script)
    assert real_xdg_open(URL) is expected


def test_xdg_open_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links.shutil, "which", lambda name: None)
    assert real_xdg_open(URL) is None


def test_frozen_linux_uses_original_library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    monkeypatch.setattr(links.QDesktopServices, "openUrl", lambda url: seen.append(os.environ.get("LD_LIBRARY_PATH")) or True)
    assert open_web_url(URL)
    assert seen == ["/usr/lib"]
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle"


def test_frozen_linux_without_original_clears_library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setattr(links.QDesktopServices, "openUrl", lambda url: seen.append(os.environ.get("LD_LIBRARY_PATH")) or True)
    assert open_web_url(URL)
    assert seen == [None]
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle"
