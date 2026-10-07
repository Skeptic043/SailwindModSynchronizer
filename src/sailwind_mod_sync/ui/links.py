from __future__ import annotations

import html
import logging
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

from sailwind_mod_sync.catalog.github import repo_page_url

log = logging.getLogger(__name__)

_HTTP_URL_RE = re.compile(r"https://[^\s<>]+")
_XDG_OPEN_WAIT_SECONDS = 3


@contextmanager
def _system_library_path() -> Iterator[None]:
    """Give programs we start the library path the user had, not the frozen build's.

    PyInstaller points LD_LIBRARY_PATH at its bundled libraries and saves the original
    in LD_LIBRARY_PATH_ORIG. A browser started through xdg-open inherits it and can
    crash before it opens anything.
    """
    if not getattr(sys, "frozen", False) or sys.platform != "linux":
        yield
        return
    saved = os.environ.get("LD_LIBRARY_PATH")
    original = os.environ.get("LD_LIBRARY_PATH_ORIG")
    if original is None:
        os.environ.pop("LD_LIBRARY_PATH", None)
    else:
        os.environ["LD_LIBRARY_PATH"] = original
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("LD_LIBRARY_PATH", None)
        else:
            os.environ["LD_LIBRARY_PATH"] = saved


def _xdg_open(url: str) -> bool | None:
    """Run xdg-open and report whether it worked; None when there's no xdg-open.

    Qt only reports whether xdg-open started, so a browser that fails to launch
    looks like success. Waiting for the exit code catches that. xdg-open usually
    exits once it has handed the URL over; if it's still running after a few
    seconds, assume the browser is starting.
    """
    xdg_open = shutil.which("xdg-open")
    if xdg_open is None:
        return None
    try:
        process = subprocess.Popen(
            [xdg_open, url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        log.exception("Could not start xdg-open for %s", url)
        return False
    try:
        _, stderr = process.communicate(timeout=_XDG_OPEN_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        return True
    if process.returncode == 0:
        return True
    log.warning(
        "xdg-open exited with %s for %s: %s",
        process.returncode,
        url,
        stderr.decode(errors="replace").strip(),
    )
    return False


def open_web_url(url: str, parent: QWidget | None = None) -> bool:
    """Open url in the browser; if nothing can open it, show it so the user can copy it."""
    with _system_library_path():
        opened = _xdg_open(url) if sys.platform == "linux" else None
        if opened:
            return True
        # When xdg-open ran and failed, skip the fallbacks: webbrowser would run
        # the same xdg-open and count starting it as success.
        if opened is None:
            if QDesktopServices.openUrl(QUrl(url)):
                return True
            log.warning("Qt could not open %s; trying the webbrowser module", url)
            try:
                if webbrowser.open(url):
                    return True
            except webbrowser.Error:
                log.exception("webbrowser could not open %s", url)
    log.warning("No browser could open %s", url)
    box = QMessageBox(
        QMessageBox.Icon.Warning,
        "Could not open browser",
        f"Couldn't open your web browser. Copy this link into it instead:\n\n{url}",
        QMessageBox.StandardButton.Ok,
        parent,
    )
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    box.exec()
    return False


def help_text_to_html(text: str) -> str:
    """Escape help text and turn https URLs into clickable links."""
    raw = text or ""
    parts: list[str] = []
    last = 0
    for match in _HTTP_URL_RE.finditer(raw):
        parts.append(html.escape(raw[last:match.start()]).replace("\n", "<br>\n"))
        full = match.group(0)
        url = full.rstrip(".,);]")
        safe = html.escape(url, quote=True)
        parts.append(f'<a href="{safe}">{html.escape(url)}</a>')
        if len(full) > len(url):
            parts.append(html.escape(full[len(url):]))
        last = match.end()
    parts.append(html.escape(raw[last:]).replace("\n", "<br>\n"))
    return "".join(parts)


def repo_button(repo: str, parent: QWidget | None = None) -> QPushButton:
    page = repo_page_url(repo)
    label = "Open GitLab in Browser" if page and "gitlab.com" in page.lower() else "Open GitHub in Browser"
    button = QPushButton(label, parent)
    if not page:
        button.setEnabled(False)
        button.setToolTip("No repository URL")
        return button
    button.setToolTip(page)
    button.clicked.connect(lambda _=False, url=page: open_web_url(url, button))
    return button


def repo_or_find_button(
    repo: str,
    parent: QWidget | None,
    on_find: Callable[[], None],
) -> QPushButton:
    page = repo_page_url(repo)
    if page:
        return repo_button(repo, parent)
    button = QPushButton("Add Repository", parent)
    button.setToolTip("Add a GitHub or GitLab repository URL, or pick a catalog entry")
    button.clicked.connect(lambda _=False: on_find())
    return button
