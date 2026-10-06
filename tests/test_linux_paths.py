from __future__ import annotations

import os
from pathlib import Path

import pytest

from sailwind_mod_sync import paths as paths_module
from sailwind_mod_sync.game import saves as saves_module


def test_data_root_prefers_explicit_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAILWIND_MOD_SYNC_HOME", str(tmp_path / "custom"))
    assert paths_module.default_data_root() == (tmp_path / "custom").resolve()


@pytest.mark.skipif(os.name == "nt", reason="XDG folders are the Linux default")
def test_data_root_uses_xdg_data_home_on_linux(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SAILWIND_MOD_SYNC_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert paths_module.default_data_root() == tmp_path / "share" / "SailwindModSynchronizer"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert paths_module.default_data_root() == Path.home() / ".local" / "share" / "SailwindModSynchronizer"


def test_proton_saves_candidates_cover_every_library(tmp_path: Path) -> None:
    home_lib = tmp_path / "Steam"
    sd_card = tmp_path / "sdcard"
    found = saves_module.proton_saves_candidates([home_lib, sd_card])
    tail = Path("steamapps/compatdata/1764530/pfx/drive_c/users/steamuser/AppData/LocalLow/Raw Lion Workshop/Sailwind")
    assert found == [home_lib / tail, sd_card / tail]


@pytest.mark.skipif(os.name == "nt", reason="Proton save folders only apply off Windows")
def test_default_saves_dir_finds_the_existing_proton_prefix(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SAILWIND_SAVES_DIR", raising=False)
    home_lib = tmp_path / "Steam"
    sd_card = tmp_path / "sdcard"
    # Game on the SD card, but its prefix (and saves) in the main Steam folder.
    existing = saves_module.proton_saves_candidates([home_lib, sd_card])[0]
    existing.mkdir(parents=True)
    from sailwind_mod_sync.game import detect

    monkeypatch.setattr(detect, "steam_libraries", lambda: [sd_card, home_lib])
    assert saves_module.default_saves_dir() == existing
