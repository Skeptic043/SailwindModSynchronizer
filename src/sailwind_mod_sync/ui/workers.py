from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from sailwind_mod_sync.catalog.github import GitHubDownloadError
from sailwind_mod_sync.http_util import ProgressFn

log = logging.getLogger(__name__)


class TaskBridge(QObject):
    """Lives on the GUI thread. Background threads only emit these signals."""

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)


class GuiCall(QObject):
    """Run a function on the GUI thread and return its result, also from a background thread."""

    _requested = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._requested.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)

    def __call__(self, fn: Callable[..., object], *args: object) -> object:
        request = [fn, args, None]
        if QThread.currentThread() == self.thread():
            # A blocking queued call to the own thread would deadlock.
            self._run(request)
        else:
            self._requested.emit(request)
        return request[2]

    @Slot(object)
    def _run(self, request: list) -> None:
        fn, args, _ = request
        try:
            request[2] = fn(*args)
        except Exception:
            log.exception("GUI call failed")


def run_background(fn: Callable[[ProgressFn], object], bridge: TaskBridge) -> threading.Thread:
    def body() -> None:
        log.info("Background task started")
        try:
            result = fn(_progress(bridge))
        except Exception as exc:
            log.exception("Background task failed")
            if isinstance(exc, GitHubDownloadError):
                bridge.failed.emit(str(exc))
                return
            detail = str(exc).strip() or repr(exc)
            bridge.failed.emit(f"{type(exc).__name__}: {detail}")
            return
        log.info("Background task finished")
        bridge.finished.emit(result)

    thread = threading.Thread(target=body, name="sailwind-mod-sync-task", daemon=True)
    thread.start()
    return thread


def _progress(bridge: TaskBridge) -> ProgressFn:
    def emit(message: str) -> None:
        text = str(message)
        log.info("%s", text)
        bridge.progress.emit(text)

    return emit
