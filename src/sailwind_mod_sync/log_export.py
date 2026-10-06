from __future__ import annotations

import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sailwind_mod_sync.game.saves import default_player_data_dir


@dataclass(frozen=True)
class LogExportResult:
    dest: Path
    included: tuple[str, ...]
    missing: tuple[str, ...]


# People share these zips publicly to get help, so personal details are removed from
# the exported copies (the logs on disk are untouched).
_STEAM_ID = re.compile(r"7656119[0-9]{10}")
# "Steam user: Name (7656119...)" / "User: Name (7656119...)", as Co-Op logs it.
_STEAM_USER = re.compile(r"(?i)\b((?:steam )?user):[ \t]*[^\r\n(]*?[ \t]*\((7656119[0-9]{10})\)")


def _home_spellings(home: Path) -> list[str]:
    text = str(home).rstrip("\\/")
    if not text or text in ("/", "\\"):
        return []
    forward = text.replace("\\", "/")
    backslashed = forward.replace("/", "\\")
    # Python error messages show paths escaped, with every backslash doubled.
    spellings = {text, forward, backslashed, backslashed.replace("\\", "\\\\")}
    if forward.startswith("/"):
        # How Wine/Proton shows Linux paths inside the game's logs.
        spellings.add("Z:" + forward.replace("/", "\\"))
    # Longest first, so a Wine spelling is replaced whole before its tail matches.
    return sorted(spellings, key=len, reverse=True)


def redact_log(text: str, home: Path | None = None) -> str:
    """Replace Steam IDs, Steam user names and the home folder path."""
    text = _STEAM_USER.sub(lambda match: f"{match.group(1)}: [steam-user] ([steam-id])", text)
    text = _STEAM_ID.sub("[steam-id]", text)
    for spelling in _home_spellings(home if home is not None else Path.home()):
        # Only whole folder names: /home/deck, not the start of /home/deckhand.
        text = re.sub(re.escape(spelling) + r"(?![\w.-])", "~", text, flags=re.IGNORECASE)
    return text


def latest_player_log() -> Path:
    return default_player_data_dir() / "Player.log"


def export_logs(
    dest: Path, *, bepinex_log: Path,
    manager_log: Path, player_log: Path | None = None, home: Path | None = None,
) -> LogExportResult:
    """Export only available explicit logs, redacted, replacing the destination after success."""
    sources = {
        "LogOutput.log": bepinex_log,
        "Player.log": player_log if player_log is not None else latest_player_log(),
        "manager.log": manager_log,
    }
    dest = dest.resolve()
    if any(dest == source.resolve() for source in sources.values()):
        raise ValueError("The export destination must differ from the source logs")
    included: list[str] = []
    missing: list[str] = []
    with tempfile.NamedTemporaryFile(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, source in sources.items():
                if not source.is_file():
                    missing.append(name)
                    continue
                raw = source.read_bytes()
                # surrogateescape keeps any non-UTF-8 bytes exactly as they were.
                text = raw.decode("utf-8", errors="surrogateescape")
                archive.writestr(name, redact_log(text, home).encode("utf-8", errors="surrogateescape"))
                included.append(name)
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)
    return LogExportResult(dest, tuple(included), tuple(missing))
