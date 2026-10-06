"""Linux/Proton checks: Doorstop's winhttp.dll only loads when Wine is told to prefer it."""

from __future__ import annotations

import os
import re
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
    return suggested_launch_options(options)
