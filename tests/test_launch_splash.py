from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QWidget

from sailwind_mod_sync.game.wait_window import focus_window, process_has_visible_window
from sailwind_mod_sync.ui.launch_splash import LaunchSplash
from sailwind_mod_sync.ui.progress_dialog import BusyDialog
from sailwind_mod_sync.ui.sailboat_scene import SailboatScene


class _FakeProcess:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


def test_process_has_visible_window_rejects_invalid_pid() -> None:
    assert process_has_visible_window(0) is False
    assert process_has_visible_window(-1) is False


def test_focus_window_rejects_invalid_hwnd() -> None:
    assert focus_window(0) is False
    assert focus_window(-1) is False


def test_launch_splash_shows_heading_and_sailboat() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = LaunchSplash(
        None,
        _FakeProcess(),
        heading="Starting Sailwind — Crew",
        has_window=lambda _pid: False,
    )
    try:
        assert dialog.windowTitle() == "Starting Sailwind"
        labels = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "Starting Sailwind — Crew" in labels
        assert "Waiting for the Sailwind window" in labels
        assert dialog.findChildren(SailboatScene)
        assert not dialog.findChildren(QPlainTextEdit)
        assert dialog._scene.running()
        dialog._scene._advance()
        assert dialog._scene.phase > 0
    finally:
        dialog._timer.stop()
        dialog._scene.stop()
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_sailboat_scene_paints_and_bobs() -> None:
    app = QApplication.instance() or QApplication([])
    clock = SimpleNamespace(now=0.0)
    scene = SailboatScene(clock=lambda: clock.now)
    try:
        scene.resize(420, 220)
        clock.now = 0.05
        scene._advance()
        assert scene.phase > 0
        image = scene.grab()
        assert not image.isNull()
        assert image.width() == 420
        assert image.height() == 220
    finally:
        scene.stop()
        scene.deleteLater()
    app.processEvents()


def test_launch_splash_preview_stays_open() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = LaunchSplash(None, None, heading="Starting Sailwind", preview=True)
    try:
        assert dialog._preview is True
        assert dialog._opened is False
        dialog._tick()
        assert dialog._opened is False
        assert dialog._timer.isActive()
    finally:
        dialog._timer.stop()
        dialog._scene.stop()
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_launch_splash_reports_failed_exit() -> None:
    app = QApplication.instance() or QApplication([])
    proc = _FakeProcess()
    dialog = LaunchSplash(None, proc, heading="Starting Sailwind", has_window=lambda _pid: False)
    try:
        proc.returncode = 1
        dialog._tick()
        assert "exit code 1" in dialog._status.text()
        assert not dialog._timer.isActive()
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_launch_splash_closes_when_window_appears() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = LaunchSplash(
        None,
        _FakeProcess(),
        heading="Starting Sailwind",
        has_window=lambda _pid: True,
    )
    try:
        assert dialog._opened is True
        assert "Sailwind is open" in dialog._status.text()
    finally:
        dialog._timer.stop()
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_launch_splash_without_process_uses_window_probe() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = LaunchSplash(None, None, heading="Starting Sailwind", window_probe=lambda: True)
    try:
        assert dialog._opened is True
        assert "Sailwind is open" in dialog._status.text()
    finally:
        dialog._timer.stop()
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_launch_splash_without_process_polls_probe() -> None:
    app = QApplication.instance() or QApplication([])
    clock = SimpleNamespace(now=0.0)

    def now() -> float:
        return clock.now

    dialog = LaunchSplash(
        None,
        None,
        heading="Starting Sailwind",
        window_probe=lambda: False,
        clock=now,
    )
    try:
        assert dialog._opened is False
        assert "Waiting for Steam to open Sailwind" in dialog._status.text()
        clock.now = 120
        dialog._tick()
        assert "taking a long time" in dialog._status.text()
        assert dialog._timer.isActive()
    finally:
        dialog._timer.stop()
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_busy_dialog_still_exists() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = BusyDialog(None, "Working", "Preparing ModPack…")
    try:
        assert "Preparing ModPack" in dialog._label.text()
        dialog.allow_close()
    finally:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_launch_splash_focuses_game_when_window_appears() -> None:
    app = QApplication.instance() or QApplication([])
    focused: list[int] = []
    dialog = LaunchSplash(
        None,
        None,
        heading="Starting Sailwind",
        window_probe=lambda: True,
        find_hwnd=lambda: 4242,
        focus_hwnd=lambda hwnd: focused.append(hwnd) or True,
        focus_delay_ms=0,
    )
    try:
        assert dialog._opened is True
        assert dialog._game_hwnd == 4242
        dialog.accept()
        app.processEvents()
        assert focused == [4242]
        assert dialog.parent() is None
    finally:
        dialog._timer.stop()
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_launch_splash_unparents_from_manager_when_game_opens() -> None:
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    focused: list[int] = []
    dialog = LaunchSplash(
        parent,
        None,
        heading="Starting Sailwind",
        window_probe=lambda: True,
        find_hwnd=lambda: 99,
        focus_hwnd=lambda hwnd: focused.append(hwnd) or True,
        focus_delay_ms=0,
    )
    try:
        dialog.accept()
        app.processEvents()
        assert dialog.parent() is None
        assert focused == [99]
    finally:
        dialog._timer.stop()
        dialog.close()
        dialog.deleteLater()
        parent.deleteLater()
    app.processEvents()


def test_launch_splash_hide_before_game_does_not_focus() -> None:
    app = QApplication.instance() or QApplication([])
    focused: list[int] = []
    dialog = LaunchSplash(
        None,
        None,
        heading="Starting Sailwind",
        window_probe=lambda: False,
        find_hwnd=lambda: 4242,
        focus_hwnd=lambda hwnd: focused.append(hwnd) or True,
        focus_delay_ms=0,
    )
    try:
        assert dialog._opened is False
        dialog.accept()
        app.processEvents()
        assert focused == []
    finally:
        dialog._timer.stop()
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_launch_splash_timeout_message() -> None:
    app = QApplication.instance() or QApplication([])
    clock = SimpleNamespace(now=0.0)

    def now() -> float:
        return clock.now

    dialog = LaunchSplash(
        None,
        _FakeProcess(),
        heading="Starting Sailwind",
        has_window=lambda _pid: False,
        clock=now,
    )
    try:
        clock.now = 120
        dialog._tick()
        assert "taking a long time" in dialog._status.text()
        assert dialog._timer.isActive()
    finally:
        dialog._timer.stop()
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def _fake_proc(root: Path, uptime: float, processes: list[tuple[int, str, bytes, float]]) -> Path:
    """Build a minimal /proc: (pid, comm, cmdline, start seconds after boot)."""
    from sailwind_mod_sync.game.wait_window import _clock_ticks

    root.mkdir(parents=True, exist_ok=True)
    (root / "uptime").write_text(f"{uptime:.2f} 0.00\n", encoding="utf-8")
    (root / "self").mkdir(exist_ok=True)
    for pid, comm, cmdline, started in processes:
        entry = root / str(pid)
        entry.mkdir()
        (entry / "comm").write_text(comm + "\n", encoding="utf-8")
        (entry / "cmdline").write_bytes(cmdline)
        ticks = int(started * _clock_ticks())
        fields = ["S"] + ["0"] * 18 + [str(ticks)] + ["0"] * 10
        (entry / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields), encoding="utf-8")
    return root


def test_linux_game_process_running_finds_proton_sailwind(tmp_path: Path) -> None:
    from sailwind_mod_sync.game.wait_window import linux_game_process_running

    proc = _fake_proc(tmp_path / "proc", 1000.0, [
        (100, "bash", b"/bin/bash\x00", 10.0),
        (200, "Sailwind.exe", b"Z:\\games\\Sailwind\\Sailwind.exe\x00", 990.0),
    ])
    assert linux_game_process_running(proc=proc, min_age=5.0)


def test_linux_game_process_running_matches_cmdline_when_comm_differs(tmp_path: Path) -> None:
    from sailwind_mod_sync.game.wait_window import linux_game_process_running

    proc = _fake_proc(tmp_path / "proc", 1000.0, [
        (300, "wine64-preload", b"C:\\Program Files\\Sailwind\\Sailwind.exe\x00--arg\x00", 900.0),
    ])
    assert linux_game_process_running(proc=proc, min_age=5.0)


def test_linux_game_process_running_waits_for_the_window_grace(tmp_path: Path) -> None:
    from sailwind_mod_sync.game.wait_window import linux_game_process_running

    proc = _fake_proc(tmp_path / "proc", 1000.0, [
        (200, "Sailwind.exe", b"Z:\\Sailwind.exe\x00", 998.0),
    ])
    assert not linux_game_process_running(proc=proc, min_age=5.0)


def test_linux_game_process_running_ignores_other_processes(tmp_path: Path) -> None:
    from sailwind_mod_sync.game.wait_window import linux_game_process_running

    proc = _fake_proc(tmp_path / "proc", 1000.0, [
        (100, "steam", b"/home/deck/.steam/steam\x00", 10.0),
        (101, "SailwindModSyn", b"/home/deck/sms-dev/.venv/bin/python\x00-m\x00sailwind_mod_sync\x00", 10.0),
    ])
    assert not linux_game_process_running(proc=proc, min_age=5.0)
    assert not linux_game_process_running(proc=tmp_path / "missing")
