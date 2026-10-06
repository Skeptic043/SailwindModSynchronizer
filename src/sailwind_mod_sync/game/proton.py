"""Linux/Proton checks: Doorstop's winhttp.dll only loads when Wine is told to prefer it."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from sailwind_mod_sync.constants import STEAM_APP_ID

WINHTTP_OVERRIDE = 'WINEDLLOVERRIDES="winhttp=n,b"'
# WINEDLLOVERRIDES=value, "value" or 'value'; group "value" holds the override list.
_OVERRIDE_RE = re.compile(
    r"""WINEDLLOVERRIDES\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s"']*))""",
    re.IGNORECASE,
)
_WINHTTP_NATIVE_RE = re.compile(r"(?:^|[;,])\s*winhttp\s*=\s*n", re.IGNORECASE)


def parse_vdf(text: str) -> dict:
    """Parse Steam's text VDF (KeyValues) into nested dicts. Later duplicate keys win."""
    tokens = _vdf_tokens(text)
    root: dict = {}
    stack = [root]
    key: str | None = None
    for kind, value in tokens:
        if kind == "open":
            child: dict = {}
            if key is not None:
                stack[-1][key] = child
                key = None
            stack.append(child)
        elif kind == "close":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = value
        else:
            stack[-1][key] = value
            key = None
    return root


def _vdf_tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(text)
    escapes = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
        elif ch == "{":
            tokens.append(("open", ""))
            i += 1
        elif ch == "}":
            tokens.append(("close", ""))
            i += 1
        elif ch == '"':
            i += 1
            out: list[str] = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    out.append(escapes.get(text[i + 1], "\\" + text[i + 1]))
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            tokens.append(("str", "".join(out)))
            i += 1
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '{}"':
                i += 1
            tokens.append(("str", text[start:i]))
    return tokens


def _get(node: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(node, dict):
            return None
        match = next((value for name, value in node.items() if name.lower() == key.lower()), None)
        if match is None:
            return None
        node = match
    return node


def launch_options_from_localconfig(text: str, app_id: str = STEAM_APP_ID) -> str | None:
    """The app's launch options from a localconfig.vdf, "" when the app has none, None when it isn't listed."""
    apps = _get(parse_vdf(text), "UserLocalConfigStore", "Software", "Valve", "Steam", "apps")
    app = _get(apps, app_id)
    if not isinstance(app, dict):
        return None
    value = _get(app, "LaunchOptions")
    return value if isinstance(value, str) else ""


def has_winhttp_override(options: str) -> bool:
    """True when the launch options tell Wine to load the native winhttp.dll (Doorstop)."""
    return any(_WINHTTP_NATIVE_RE.search(_override_value(m)) for m in _OVERRIDE_RE.finditer(options or ""))


def _override_value(match: re.Match) -> str:
    return next((v for v in (match.group("dq"), match.group("sq"), match.group("bare")) if v is not None), "")


def suggested_launch_options(existing: str) -> str:
    """Launch options with the winhttp override added, keeping whatever the user already has."""
    current = (existing or "").strip()
    if has_winhttp_override(current):
        return current
    if not current:
        return f"{WINHTTP_OVERRIDE} %command%"
    match = _OVERRIDE_RE.search(current)
    if match:
        # Extend the existing WINEDLLOVERRIDES list instead of adding a second one.
        value = _override_value(match)
        merged = f"{value};winhttp=n,b" if value else "winhttp=n,b"
        return f'{current[:match.start()]}WINEDLLOVERRIDES="{merged}"{current[match.end():]}'
    if "%command%" in current:
        return f"{WINHTTP_OVERRIDE} {current}"
    return f"{WINHTTP_OVERRIDE} %command% {current}"


def current_localconfig(steam_root: Path | None) -> Path | None:
    """The localconfig.vdf of the Steam account that used Steam most recently."""
    if steam_root is None:
        return None
    configs = sorted(
        steam_root.glob("userdata/*/config/localconfig.vdf"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return configs[0] if configs else None


def sailwind_launch_options(steam_root: Path | None = None) -> str | None:
    """Sailwind's Steam launch options, or None when they can't be read."""
    if steam_root is None:
        from sailwind_mod_sync.game.detect import steam_root as find_steam_root

        steam_root = find_steam_root()
    config = current_localconfig(steam_root)
    if config is None:
        return None
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return launch_options_from_localconfig(text)


def needs_winhttp_override(steam_root: Path | None = None, *, windows: bool | None = None) -> str | None:
    """On Linux, the launch options to suggest when Doorstop's override is missing; otherwise None.

    Returns None on Windows, when the override is already set, or when Steam's
    config can't be read: the app only speaks up when it's sure something is missing.
    """
    if (os.name == "nt") if windows is None else windows:
        return None
    options = sailwind_launch_options(steam_root)
    if options is None or has_winhttp_override(options):
        return None
    # Check the prefix in the same Steam installation the launch options came from.
    libraries = None
    if steam_root is not None:
        from sailwind_mod_sync.game.detect import _steam_libraries

        libraries = _steam_libraries(steam_root)
    if prefix_has_winhttp_override(libraries):
        return None
    return suggested_launch_options(options)


def prefix_has_winhttp_override(libraries: list[Path] | None = None) -> bool:
    """True when the user chose to set the override inside Sailwind's Proton prefix."""
    registry = sailwind_prefix_registry(libraries)
    if registry is None:
        return False
    try:
        return registry_has_winhttp_override(registry.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False


# --- Opt-in alternative: set the override inside Sailwind's Proton prefix -------------
#
# Wine reads DLL overrides from the prefix registry (pfx/user.reg), the same setting
# winecfg and protontricks write. Setting winhttp there means no launch option is
# needed. It edits Proton's files, so the app only does it when the user chooses to.

# user.reg spells registry paths with doubled backslashes.
PREFIX_SECTION = r"[Software\\Wine\\DllOverrides]"
PREFIX_VALUE_LINE = '"winhttp"="native,builtin"'
PREFIX_BACKUP_SUFFIX = ".sms-backup"
_FILETIME_EPOCH_OFFSET = 11644473600


def prefix_registry_candidates(libraries: list[Path] | None = None) -> list[Path]:
    """Sailwind's Proton prefix user.reg in each Steam library (the prefix may sit in any of them)."""
    if libraries is None:
        from sailwind_mod_sync.game.detect import steam_libraries

        libraries = steam_libraries()
    return [library / "steamapps" / "compatdata" / STEAM_APP_ID / "pfx" / "user.reg" for library in libraries]


def sailwind_prefix_registry(libraries: list[Path] | None = None) -> Path | None:
    """The user.reg of Sailwind's Proton prefix, or None until the game has run under Proton once."""
    return next((path for path in prefix_registry_candidates(libraries) if path.is_file()), None)


def _section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Start (header line) and end (exclusive) of the global DllOverrides section."""
    for index, line in enumerate(lines):
        if line.startswith(PREFIX_SECTION):
            end = index + 1
            while end < len(lines) and not lines[end].startswith("["):
                end += 1
            return index, end
    return None


def _is_winhttp_value(line: str) -> bool:
    return line.strip().lower().startswith('"winhttp"=')


def registry_has_winhttp_override(text: str) -> bool:
    """True when the prefix registry already tells Wine to load the native winhttp.dll first."""
    lines = text.splitlines()
    bounds = _section_bounds(lines)
    if bounds is None:
        return False
    for line in lines[bounds[0] + 1:bounds[1]]:
        if _is_winhttp_value(line):
            value = line.split("=", 1)[1].strip().strip('"').lower()
            return value.startswith("native") or value.startswith("n")
    return False


def add_winhttp_to_registry(text: str, now: float | None = None) -> str:
    """The registry text with winhttp set to native,builtin, replacing any other winhttp value."""
    lines = text.splitlines()
    bounds = _section_bounds(lines)
    if bounds is not None:
        start, end = bounds
        body = [line for line in lines[start + 1:end] if not _is_winhttp_value(line)]
        # Keep the trailing blank line that separates sections.
        while body and not body[-1].strip():
            body.pop()
        lines[start + 1:end] = body + [PREFIX_VALUE_LINE, ""]
        return "\n".join(lines) + "\n"
    stamp = int(time.time() if now is None else now)
    filetime = (stamp + _FILETIME_EPOCH_OFFSET) * 10_000_000
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ["", f"{PREFIX_SECTION} {stamp}", f"#time={filetime:x}", PREFIX_VALUE_LINE]
    return "\n".join(lines) + "\n"


def remove_winhttp_from_registry(text: str) -> str:
    """The registry text without a winhttp override (the rest of the section is kept)."""
    lines = text.splitlines()
    bounds = _section_bounds(lines)
    if bounds is None:
        return text
    start, end = bounds
    lines[start + 1:end] = [line for line in lines[start + 1:end] if not _is_winhttp_value(line)]
    return "\n".join(lines) + "\n"


class PrefixChangeError(RuntimeError):
    pass


def _check_safe_to_edit(registry: Path) -> None:
    from sailwind_mod_sync.game.wait_window import linux_game_process_running

    if linux_game_process_running(min_age=0):
        raise PrefixChangeError("Close Sailwind first. Proton rewrites this file when the game exits.")
    if not registry.is_file():
        raise PrefixChangeError("Sailwind's Proton prefix wasn't found. Start Sailwind from Steam once, then try again.")


def _write_registry(registry: Path, text: str) -> None:
    tmp = registry.with_name(registry.name + ".sms-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, registry)


def set_prefix_winhttp_override(registry: Path) -> Path:
    """Set the override in user.reg, keeping a backup of the original. Returns the backup path."""
    _check_safe_to_edit(registry)
    text = registry.read_text(encoding="utf-8")
    backup = registry.with_name(registry.name + PREFIX_BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(registry, backup)
    _write_registry(registry, add_winhttp_to_registry(text))
    return backup


def remove_prefix_winhttp_override(registry: Path) -> None:
    """Undo set_prefix_winhttp_override by removing just the winhttp value."""
    _check_safe_to_edit(registry)
    text = registry.read_text(encoding="utf-8")
    _write_registry(registry, remove_winhttp_from_registry(text))
