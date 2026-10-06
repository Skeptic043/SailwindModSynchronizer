import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sailwind_mod_sync.log_export import export_logs, latest_player_log


def test_export_includes_only_explicit_latest_logs_with_original_bytes(tmp_path):
    sources = {name: tmp_path / name for name in ("LogOutput.log", "Player.log", "manager.log")}
    for name, path in sources.items():
        path.write_text(f"synthetic {name}", encoding="utf-8")
    dest = tmp_path / "logs.zip"
    result = export_logs(dest,
                         bepinex_log=sources["LogOutput.log"], player_log=sources["Player.log"],
                         manager_log=sources["manager.log"])
    assert result.included == tuple(sources) and not result.missing
    with zipfile.ZipFile(dest) as archive:
        assert set(archive.namelist()) == set(sources)
        for name, path in sources.items():
            assert archive.read(name) == path.read_bytes()


def test_export_all_missing_produces_empty_zip_and_clear_missing_result(tmp_path):
    result = export_logs(tmp_path / "logs.zip",
                         bepinex_log=tmp_path / "absent-bepinex.log", player_log=tmp_path / "absent-player.log",
                         manager_log=tmp_path / "absent-manager.log")
    assert not result.included
    assert result.missing == ("LogOutput.log", "Player.log", "manager.log")
    with zipfile.ZipFile(result.dest) as archive:
        assert not archive.namelist()


@pytest.mark.parametrize("failure", ["read", "replace"])
def test_export_failure_preserves_destination_and_cleans_temporary(tmp_path, monkeypatch, failure):
    source = tmp_path / "manager.log"
    source.write_text("synthetic current log")
    dest = tmp_path / "logs.zip"
    dest.write_bytes(b"previous export")
    if failure == "read":
        original_read = Path.read_bytes
        def fail_read(path):
            if path == source:
                raise PermissionError("locked log")
            return original_read(path)
        monkeypatch.setattr(Path, "read_bytes", fail_read)
    else:
        original = Path.replace
        def fail_replace(path, target):
            if target == dest:
                raise PermissionError("locked destination")
            return original(path, target)
        monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(PermissionError):
        export_logs(dest, bepinex_log=tmp_path / "absent.log",
                    player_log=tmp_path / "absent-player.log", manager_log=source)
    assert dest.read_bytes() == b"previous export"
    assert not list(tmp_path.glob(".logs.zip.*.tmp"))


def test_player_log_uses_native_data_directory_even_with_save_override(tmp_path, monkeypatch):
    from sailwind_mod_sync.game import saves

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(saves, "os", SimpleNamespace(
        name="nt", environ={"SAILWIND_SAVES_DIR": str(tmp_path / "custom-saves")},
    ))
    native = tmp_path / "home" / "AppData" / "LocalLow" / saves.SAVES_COMPANY / saves.SAVES_PRODUCT
    assert latest_player_log() == native / "Player.log"
    assert saves.default_saves_dir() == (tmp_path / "custom-saves").resolve()


@pytest.mark.parametrize("prefix", ["home", "sd-card", "not-created", "no-libraries"])
def test_player_log_uses_proton_lookup_independently_of_save_override(tmp_path, monkeypatch, prefix):
    from sailwind_mod_sync.game import detect, saves

    libraries = [] if prefix == "no-libraries" else [tmp_path / "home-library", tmp_path / "sd-card"]
    candidates = saves.proton_saves_candidates(libraries)
    if prefix in ("home", "sd-card"):
        expected = candidates[0 if prefix == "home" else 1]
        expected.mkdir(parents=True)
    elif candidates:
        expected = candidates[0]
    else:
        expected = tmp_path / "home" / "AppData" / "LocalLow" / saves.SAVES_COMPANY / saves.SAVES_PRODUCT
    monkeypatch.setattr(detect, "steam_libraries", lambda: libraries)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    platform = SimpleNamespace(name="posix", environ={"SAILWIND_SAVES_DIR": str(tmp_path / "custom-saves")})
    monkeypatch.setattr(saves, "os", platform)
    assert latest_player_log() == expected / "Player.log"
    assert saves.default_saves_dir() == (tmp_path / "custom-saves").resolve()
    platform.environ.clear()
    assert saves.default_saves_dir() == expected


def test_export_refuses_overwriting_a_source_log(tmp_path):
    source = tmp_path / "manager.log"
    source.write_bytes(b"original log")
    with pytest.raises(ValueError, match="must differ"):
        export_logs(source, bepinex_log=tmp_path / "missing-bepinex.log",
                    player_log=tmp_path / "missing-player.log", manager_log=source)
    assert source.read_bytes() == b"original log"


def test_export_redacts_steam_ids_user_names_and_home_paths(tmp_path):
    from sailwind_mod_sync.log_export import redact_log

    home = Path("C:/Users/Sailor")
    bepinex = tmp_path / "LogOutput.log"
    bepinex.write_text(
        "[Info   :Sailwind Coop] Steam initialized successfully. User: Captain Jo (76561198000000001)\n"
        "[Info   :Sailwind Coop] Steam user: Captain Jo (76561198000000001)\n"
        "[Info   :Sailwind Coop] Lobby owner 76561198000000002 joined\n"
        "[Info   : BepInEx] Loading C:\\Users\\Sailor\\AppData\\Local\\Mods\\x.dll\n",
        encoding="utf-8",
    )
    manager_log = tmp_path / "manager.log"
    manager_log.write_text("Logging to c:/users/sailor/AppData/Local/SailwindModSynchronizer/manager.log\n", encoding="utf-8")
    dest = tmp_path / "logs.zip"
    export_logs(dest, bepinex_log=bepinex, player_log=tmp_path / "absent.log", manager_log=manager_log, home=home)
    with zipfile.ZipFile(dest) as archive:
        exported = archive.read("LogOutput.log").decode("utf-8") + archive.read("manager.log").decode("utf-8")
    assert "7656119" not in exported
    assert "Captain Jo" not in exported
    assert "Sailor" not in exported and "sailor" not in exported
    assert "User: [steam-user] ([steam-id])" in exported
    assert "Steam user: [steam-user] ([steam-id])" in exported
    assert "Lobby owner [steam-id] joined" in exported
    assert "Loading ~\\AppData\\Local\\Mods\\x.dll" in exported
    assert "Logging to ~/AppData/Local/SailwindModSynchronizer/manager.log" in exported
    # The logs on disk are untouched.
    assert "Captain Jo" in bepinex.read_text(encoding="utf-8")

    # Linux paths, and how Wine shows them inside the game's logs.
    linux = redact_log("/home/deck/.steam and Z:\\home\\deck\\Games and /home/deckhand", Path("/home/deck"))
    assert linux == "~/.steam and ~\\Games and /home/deckhand"  # deckhand isn't deck's home

    # Paths inside Python error messages are escaped, with doubled backslashes.
    escaped = redact_log("PermissionError: 'C:\\\\Users\\\\Sailor\\\\AppData\\\\x'", Path("C:/Users/Sailor"))
    assert escaped == "PermissionError: '~\\\\AppData\\\\x'"
