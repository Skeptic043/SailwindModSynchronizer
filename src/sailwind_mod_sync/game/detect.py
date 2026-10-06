from __future__ import annotations

import os
import re
from pathlib import Path

from sailwind_mod_sync.constants import DEFAULT_GAME_PATH, GAME_EXE_NAME, STEAM_APP_ID

PATH_RE = re.compile(r'"path"\s+"([^"]+)"')


def detect_game_path() -> Path | None:
    candidates: list[Path] = []
    default = Path(DEFAULT_GAME_PATH)
    if default.is_file() or (default / GAME_EXE_NAME).exists():
        return _as_game_dir(default)
    steam_root = _steam_install_path()
    if steam_root:
        for library in _steam_libraries(steam_root):
            common = library / "steamapps" / "common" / "Sailwind"
            candidates.append(common)
            acf = library / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf"
            install_dir = _acf_installdir(acf)
            if install_dir:
                candidates.append(library / "steamapps" / "common" / install_dir)
    for candidate in candidates:
        game = _as_game_dir(candidate)
        if game:
            return game
    return None


def steam_root() -> Path | None:
    """The main Steam folder (where userdata and the Steam client live), or None."""
    return _steam_install_path()


def steam_libraries() -> list[Path]:
    """Every Steam library folder on this machine, the main Steam folder first."""
    steam_root = _steam_install_path()
    return _steam_libraries(steam_root) if steam_root else []


def resolve_game_dir(configured: str) -> Path | None:
    if configured.strip():
        found = _as_game_dir(Path(configured))
        if found:
            return found
    return detect_game_path()


def _as_game_dir(path: Path) -> Path | None:
    path = Path(path)
    if path.is_file() and path.name.lower() == GAME_EXE_NAME.lower():
        return path.parent
    exe = path / GAME_EXE_NAME
    if exe.exists():
        return path
    return None


def _steam_install_path() -> Path | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            path = Path(str(value))
            if path.exists():
                return path
    except (ImportError, OSError):
        # ImportError: winreg only exists on Windows.
        pass
    for candidate in _steam_candidates():
        if candidate.exists():
            return candidate
    return None


def _steam_candidates() -> list[Path]:
    if os.name == "nt":
        return [
            Path(r"C:\Program Files (x86)\Steam"),
            Path(r"C:\Program Files\Steam"),
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
        ]
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return [
        data_home / "Steam",
        home / ".steam" / "steam",
        home / ".steam" / "root",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]


def steam_libraries_from_vdf(text: str) -> list[Path]:
    paths = []
    for match in PATH_RE.finditer(text):
        raw = match.group(1).replace("\\\\", "\\")
        paths.append(Path(raw))
    return paths


def parse_libraries_file(vdf_path: Path) -> list[Path]:
    if not vdf_path.exists():
        return []
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return steam_libraries_from_vdf(text)


def _steam_libraries(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    for relative in (
        Path("steamapps") / "libraryfolders.vdf",
        Path("config") / "libraryfolders.vdf",
    ):
        libraries.extend(parse_libraries_file(steam_root / relative))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in libraries:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _acf_installdir(acf_path: Path) -> str | None:
    if not acf_path.exists():
        return None
    try:
        text = acf_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r'"installdir"\s+"([^"]+)"', text)
    return match.group(1) if match else None
