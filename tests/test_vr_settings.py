from __future__ import annotations

import logging
from pathlib import Path

from sailwind_mod_sync.game.vr_settings import global_settings_path, restore_flat_settings

# BuildSettings' enabledVRDevices before and after UUVR's patcher: an empty list, then OpenVR and Oculus.
FLAT = b"header" + b"\x00\x00\x00\x00" + b"trailer"
VR = b"header" + b"\x02\x00\x00\x00\x06\x00\x00\x00OpenVR\x00\x00\x06\x00\x00\x00Oculus\x00\x00" + b"trailer"


def _settings(tmp_path: Path, current: bytes, backup: bytes | None) -> tuple[Path, Path]:
    path = global_settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(current)
    backup_path = path.with_name("globalgamemanagers.bak")
    if backup is not None:
        backup_path.write_bytes(backup)
    return path, backup_path


def test_settings_live_in_sailwind_data(tmp_path: Path) -> None:
    assert global_settings_path(tmp_path) == tmp_path / "Sailwind_Data" / "globalgamemanagers"


def test_vr_settings_are_restored_from_uuvr_backup(tmp_path: Path) -> None:
    current, backup = _settings(tmp_path, VR, FLAT)
    restore_flat_settings(tmp_path)
    assert current.read_bytes() == FLAT
    assert not backup.exists()


def test_backup_from_before_a_game_update_is_dropped(tmp_path: Path) -> None:
    updated = FLAT + b"new build"
    current, backup = _settings(tmp_path, updated, FLAT)
    restore_flat_settings(tmp_path)
    assert current.read_bytes() == updated
    assert not backup.exists()


def test_flat_settings_without_backup_are_left_alone(tmp_path: Path) -> None:
    current, backup = _settings(tmp_path, FLAT, None)
    restore_flat_settings(tmp_path)
    assert current.read_bytes() == FLAT
    assert not backup.exists()


def test_vr_settings_without_a_flat_backup_are_kept_and_logged(tmp_path: Path, caplog) -> None:
    current, backup = _settings(tmp_path, VR, VR)
    with caplog.at_level(logging.WARNING):
        restore_flat_settings(tmp_path)
    assert current.read_bytes() == VR
    assert backup.read_bytes() == VR
    assert "Verify Sailwind's files" in caplog.text


def test_missing_game_data_is_ignored(tmp_path: Path) -> None:
    restore_flat_settings(tmp_path)


def test_locked_settings_file_is_logged_and_the_launch_goes_on(tmp_path: Path, monkeypatch, caplog) -> None:
    """Sailwind holds the file while it runs, so a Play click then must not fail."""
    current, backup = _settings(tmp_path, VR, FLAT)

    def locked(*_args, **_kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr("sailwind_mod_sync.game.vr_settings.os.replace", locked)
    with caplog.at_level(logging.WARNING):
        restore_flat_settings(tmp_path)
    assert current.read_bytes() == VR
    assert backup.read_bytes() == FLAT
    assert "Could not check or restore" in caplog.text
