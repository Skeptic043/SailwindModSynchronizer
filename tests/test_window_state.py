from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter, QWidget

from sailwind_mod_sync.ui.window_state import restore_window_state, save_window_state


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_window() -> tuple[QMainWindow, QSplitter]:
    window = QMainWindow()
    splitter = QSplitter()
    splitter.addWidget(QWidget())
    splitter.addWidget(QWidget())
    splitter.setSizes([300, 900])
    window.setCentralWidget(splitter)
    window.resize(1200, 760)
    return window, splitter


def test_geometry_round_trip(data_root: Path) -> None:
    app = _app()
    root = SimpleNamespace(root=data_root)
    window, splitter = _make_window()
    # Restoring shrinks windows to fit the screen, and headless test screens
    # can be small, so pick a size that fits whatever screen this runs on.
    available = app.primaryScreen().availableGeometry()
    width = min(900, available.width() - 100)
    height = min(640, available.height() - 100)
    window.resize(width, height)
    window.move(40, 30)
    save_window_state(window, splitter, paths=root)
    assert (data_root / "geometry.ini").exists()

    restored, restored_splitter = _make_window()
    ok = restore_window_state(restored, restored_splitter, paths=root)
    assert ok is True
    assert restored.size().width() == width
    assert restored.size().height() == height
    app.processEvents()
    restored.close()


def test_splitter_sizes_round_trip(data_root: Path) -> None:
    # QSplitter stores handle positions relative to the splitter's width, so
    # absolute pixels are not round-tripped exactly — the *proportion* is.
    app = _app()
    root = SimpleNamespace(root=data_root)
    window, splitter = _make_window()
    splitter.setSizes([400, 800])
    save_window_state(window, splitter, paths=root)

    restored, restored_splitter = _make_window()
    restore_window_state(restored, restored_splitter, paths=root)
    sizes = restored_splitter.sizes()
    assert sizes[1] == pytest.approx(2 * sizes[0], abs=2)
    app.processEvents()
    restored.close()


def test_invalid_saved_geometry_uses_default(data_root: Path) -> None:
    # Corrupted stored geometry must not be able to lose the window: restore
    # fails and the window falls back to its default size.
    app = _app()
    root = SimpleNamespace(root=data_root)
    settings = QSettings(str(data_root / "geometry.ini"), QSettings.Format.IniFormat)
    settings.setValue("geometry", QByteArray(b"not-a-valid-geometry"))
    settings.sync()

    window, splitter = _make_window()
    ok = restore_window_state(
        window,
        splitter,
        paths=root,
        default_size=(700, 500),
    )
    assert ok is False
    assert window.width() == 700
    assert window.height() == 500
    app.processEvents()
    window.close()


def test_no_saved_state_uses_default(data_root: Path) -> None:
    app = _app()
    root = SimpleNamespace(root=data_root)
    window, splitter = _make_window()
    ok = restore_window_state(
        window,
        splitter,
        paths=root,
        default_size=(666, 444),
    )
    assert ok is False
    assert window.width() == 666
    assert window.height() == 444
    app.processEvents()
    window.close()


def test_maximized_false_string_does_not_maximize(data_root: Path) -> None:
    # A fresh process reads the INI bool back as the string "false", not a
    # bool; restoring must not maximize the window then.
    (data_root / "geometry.ini").write_text("[General]\nmaximized=false\n", encoding="utf-8")
    app = _app()
    root = SimpleNamespace(root=data_root)
    window, splitter = _make_window()
    restore_window_state(window, splitter, paths=root)
    app.processEvents()
    assert not window.isMaximized()
    window.close()


def test_maximized_true_string_maximizes(data_root: Path) -> None:
    (data_root / "geometry.ini").write_text("[General]\nmaximized=true\n", encoding="utf-8")
    app = _app()
    root = SimpleNamespace(root=data_root)
    window, splitter = _make_window()
    restore_window_state(window, splitter, paths=root)
    app.processEvents()
    assert window.isMaximized()
    window.close()


def test_maximized_bool_false_round_trip(data_root: Path) -> None:
    app = _app()
    root = SimpleNamespace(root=data_root)
    window, splitter = _make_window()
    save_window_state(window, splitter, paths=root)
    restored, restored_splitter = _make_window()
    restore_window_state(restored, restored_splitter, paths=root)
    app.processEvents()
    assert not restored.isMaximized()
    restored.close()


def test_maximized_bool_true_round_trip(data_root: Path) -> None:
    app = _app()
    root = SimpleNamespace(root=data_root)
    window, splitter = _make_window()
    window.showMaximized()
    save_window_state(window, splitter, paths=root)
    window.close()
    restored, restored_splitter = _make_window()
    restore_window_state(restored, restored_splitter, paths=root)
    app.processEvents()
    assert restored.isMaximized()
    restored.close()