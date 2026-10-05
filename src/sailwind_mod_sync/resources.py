from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap


def icon_path() -> Path | None:
    """Window-icon path. Prefers .ico on Windows so the taskbar can pick a size."""
    return _first_icon(("icon.ico", "icon.png"))


def raster_icon_path() -> Path | None:
    """High-resolution bitmap for in-window artwork. Prefers the PNG master."""
    return _first_icon(("icon.png", "icon.ico"))


def changelog_path() -> Path | None:
    """The bundled CHANGELOG.md shown from Help → Change log."""
    return _first_icon(("CHANGELOG.md",))


def load_icon_pixmap(logical_px: int, device_pixel_ratio: float = 1.0) -> QPixmap | None:
    """Scale the app icon to ``logical_px`` using the PNG (or the largest ICO size)."""
    dpr = max(1.0, float(device_pixel_ratio) or 1.0)
    phys = max(1, int(round(logical_px * dpr)))
    png = _first_icon(("icon.png",))
    if png is not None:
        source = QPixmap(str(png))
        if not source.isNull():
            scaled = source.scaled(
                phys,
                phys,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            return scaled
    ico = icon_path()
    if ico is None:
        return None
    pix = QIcon(str(ico)).pixmap(phys, phys)
    if pix.isNull():
        return None
    pix.setDevicePixelRatio(dpr)
    return pix


def _first_icon(names: tuple[str, ...]) -> Path | None:
    seen: set[Path] = set()
    for path in _icon_candidates(names):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def _icon_candidates(names: tuple[str, ...]) -> list[Path]:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass)
        for name in names:
            candidates.append(bundled / "assets" / name)
            candidates.append(bundled / name)
    here = Path(__file__).resolve().parent
    repo_assets = here.parent.parent / "assets"
    for name in names:
        candidates.append(repo_assets / name)
        candidates.append(here / "assets" / name)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for name in names:
            candidates.append(exe_dir / "assets" / name)
            candidates.append(exe_dir / name)
    return candidates
