from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Callable
from pathlib import Path
from ctypes import wintypes

from sailwind_mod_sync.constants import GAME_EXE_NAME

log = logging.getLogger(__name__)

GW_OWNER = 4
MIN_WINDOW_WIDTH = 200
MIN_WINDOW_HEIGHT = 120
SW_RESTORE = 9
HWND_TOP = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
ASFW_ANY = -1


def process_image_name(pid: int) -> str | None:
    """Basename of the executable for pid (e.g. 'Sailwind.exe'), or None when unknown."""
    if os.name != "nt" or pid <= 0:
        return None
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        size = wintypes.DWORD(264)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return None
    finally:
        kernel32.CloseHandle(process)


def sailwind_window_visible(
    *,
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
) -> bool:
    """True when a visible Sailwind.exe window of a useful size is on screen.

    Used when the game was handed off to Steam, so we have no pid to track and
    must recognize the game by its window instead.
    """
    if os.name != "nt":
        return linux_game_process_running()
    return sailwind_window_hwnd(min_width=min_width, min_height=min_height) is not None


def sailwind_window_hwnd(
    *,
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
) -> int | None:
    """HWND of a visible Sailwind.exe window, or None when none is on screen."""
    return window_hwnd_for_image(GAME_EXE_NAME, min_width=min_width, min_height=min_height)


LINUX_PROC = Path("/proc")
LINUX_WINDOW_GRACE_SECONDS = 5.0


def linux_game_process_running(
    *,
    proc: Path | None = None,
    min_age: float = LINUX_WINDOW_GRACE_SECONDS,
) -> bool:
    """True when a Sailwind.exe process (run by Proton/Wine) has been alive for min_age seconds.

    Linux has no portable way to see another app's windows (Game Mode runs under
    gamescope), so the game counts as open once its process has had a few
    seconds to show a window.
    """
    root = proc or LINUX_PROC
    uptime = _linux_uptime(root)
    if uptime is None:
        return False
    tick = _clock_ticks()
    target = GAME_EXE_NAME.lower()
    try:
        entries = list(root.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        if not _linux_is_game_process(entry, target):
            continue
        started = _linux_start_seconds(entry, tick)
        if started is not None and uptime - started >= min_age:
            return True
    return False


def _linux_is_game_process(entry: Path, target: str) -> bool:
    try:
        comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return False
    if comm.lower() == target:
        return True
    try:
        raw = (entry / "cmdline").read_bytes()
    except OSError:
        return False
    first = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    name = first.replace("\\", "/").rsplit("/", 1)[-1]
    return name.lower() == target


def _linux_uptime(root: Path) -> float | None:
    try:
        return float((root / "uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _linux_start_seconds(entry: Path, tick: float) -> float | None:
    try:
        stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Field 22 (starttime, in clock ticks since boot) comes after the ")" that ends the name.
    fields = stat.rsplit(")", 1)[-1].split()
    try:
        return int(fields[19]) / tick
    except (IndexError, ValueError):
        return None


def _clock_ticks() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100.0


def window_exists_for_image(
    image_name: str,
    *,
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
) -> bool:
    """True when some top-level visible window belongs to a process with this exe name."""
    return window_hwnd_for_image(image_name, min_width=min_width, min_height=min_height) is not None


def window_hwnd_for_image(
    image_name: str,
    *,
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
) -> int | None:
    """HWND of a visible top-level window for this exe name, or None."""
    if os.name != "nt":
        return None
    target = image_name.lower()

    def matches(pid: int) -> bool:
        name = process_image_name(pid)
        return bool(name) and name.lower() == target

    return _first_visible_hwnd(matches, min_width=min_width, min_height=min_height)


def process_has_visible_window(
    pid: int,
    *,
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
) -> bool:
    """True when pid has a visible, owned-by-itself window of a useful size."""
    return window_hwnd_for_pid(pid, min_width=min_width, min_height=min_height) is not None


def window_hwnd_for_pid(
    pid: int,
    *,
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
) -> int | None:
    """HWND of a visible top-level window for pid, or None."""
    if os.name != "nt" or pid <= 0:
        return None
    return _first_visible_hwnd(lambda value: value == pid, min_width=min_width, min_height=min_height)


def focus_window(hwnd: int) -> bool:
    """Bring a native window to the foreground.

    Closing a Qt dialog restores focus to the manager; call this afterward so
    Sailwind keeps the keyboard and taskbar highlight instead of SMS.
    """
    if os.name != "nt" or hwnd <= 0:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    handle = wintypes.HWND(hwnd)
    if not user32.IsWindow(handle):
        return False
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, SW_RESTORE)
    try:
        user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:
        log.debug("AllowSetForegroundWindow is unavailable", exc_info=True)

    current_tid = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = _thread_id_for_window(user32, fg)
    target_tid = _thread_id_for_window(user32, handle)
    attached_fg = False
    attached_target = False
    if fg_tid and fg_tid != current_tid:
        attached_fg = bool(user32.AttachThreadInput(current_tid, fg_tid, True))
    if target_tid and target_tid != current_tid:
        attached_target = bool(user32.AttachThreadInput(current_tid, target_tid, True))
    try:
        user32.BringWindowToTop(handle)
        user32.SetWindowPos(
            handle,
            HWND_TOP,
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW,
        )
        if user32.SetForegroundWindow(handle):
            return True
        return int(user32.GetForegroundWindow()) == int(hwnd)
    finally:
        if attached_target:
            user32.AttachThreadInput(current_tid, target_tid, False)
        if attached_fg:
            user32.AttachThreadInput(current_tid, fg_tid, False)


def _thread_id_for_window(user32, hwnd) -> int:
    if not hwnd:
        return 0
    proc = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
    return int(tid or 0)


def _first_visible_hwnd(
    pid_matches: Callable[[int], bool],
    *,
    min_width: int,
    min_height: int,
) -> int | None:
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        proc = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if not pid_matches(int(proc.value)):
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width >= min_width and height >= min_height:
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None
