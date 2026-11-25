"""Undo the VR switch UUVR's patcher writes into Sailwind's own files.

UUVR's Mono Legacy patcher keeps the game's ``globalgamemanagers`` as ``globalgamemanagers.bak`` and, on every
start, writes a copy of that backup with OpenVR and Oculus added to the build's VR devices. The change stays after
the game closes, so a later launch without UUVR would open in the headset. Mod Sync puts the backup back before
each launch; a pack with UUVR makes a fresh backup and patches it again as the game starts.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sailwind_mod_sync.constants import GAME_EXE_NAME

log = logging.getLogger(__name__)

GLOBAL_SETTINGS = "globalgamemanagers"
BACKUP_SUFFIX = ".bak"
# OpenVR as a serialized Unity string in the build's VR device list; vanilla Sailwind has no VR devices.
_OPENVR_DEVICE = b"\x06\x00\x00\x00OpenVR"


def global_settings_path(game_dir: Path) -> Path:
    return game_dir / f"{Path(GAME_EXE_NAME).stem}_Data" / GLOBAL_SETTINGS


def vr_enabled(path: Path) -> bool:
    return _OPENVR_DEVICE in path.read_bytes()


def restore_flat_settings(game_dir: Path) -> None:
    """Put back the game's own settings file if UUVR turned VR on, and drop UUVR's copy of it.

    A backup older than the current file comes from before a game update and must not be restored or reused,
    so it is dropped whenever the current file is the game's own.
    """
    current = global_settings_path(game_dir)
    try:
        _restore(current, current.with_name(current.name + BACKUP_SUFFIX))
    except OSError as exc:
        # The file is locked while Sailwind is still running; the launch goes ahead and the next one retries.
        log.warning("Could not check or restore %s: %s", current, exc)


def _restore(current: Path, backup: Path) -> None:
    if not current.is_file():
        return
    if not vr_enabled(current):
        if backup.exists():
            backup.unlink()
            log.info("Removed UUVR's settings backup %s; the game's own file is in place", backup)
        return
    if backup.is_file() and not vr_enabled(backup):
        os.replace(backup, current)
        log.info("Turned VR off again by restoring %s from UUVR's backup", current)
        return
    log.warning(
        "%s has VR turned on and no backup to restore. Verify Sailwind's files in Steam to turn it off.", current,
    )
