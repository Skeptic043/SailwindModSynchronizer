from __future__ import annotations

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


def latest_player_log() -> Path:
    return default_player_data_dir() / "Player.log"


def export_logs(
    dest: Path, *, bepinex_log: Path,
    manager_log: Path, player_log: Path | None = None,
) -> LogExportResult:
    """Export only available explicit logs, replacing the destination after success."""
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
                archive.write(source, name)
                included.append(name)
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)
    return LogExportResult(dest, tuple(included), tuple(missing))
