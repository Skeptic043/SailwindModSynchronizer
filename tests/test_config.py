from __future__ import annotations

import json

from sailwind_mod_sync.config import AppConfig, load_config, save_config
from sailwind_mod_sync.paths import AppPaths


def test_load_config_defaults_warn_missing_mods(paths: AppPaths) -> None:
    config = load_config(paths)
    assert config.warn_missing_mods is True


def test_save_and_load_warn_missing_mods(paths: AppPaths) -> None:
    save_config(paths, AppConfig(warn_missing_mods=False))
    loaded = load_config(paths)
    assert loaded.warn_missing_mods is False
    payload = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert payload["warn_missing_mods"] is False


def test_old_config_without_flag_warns_by_default(paths: AppPaths) -> None:
    paths.config_file.write_text(
        json.dumps({"game_path": "D:/Sailwind", "github_token": "", "last_pack_id": "a"}),
        encoding="utf-8",
    )
    loaded = load_config(paths)
    assert loaded.warn_missing_mods is True
    assert loaded.check_for_updates is True
    assert loaded.auto_scan_mods is True
    assert loaded.game_path == "D:/Sailwind"
    assert loaded.last_update_check == ""
    assert loaded.skipped_update_version == ""
    assert loaded.hidden_catalog_mods == []


def test_save_and_load_auto_scan_mods(paths: AppPaths) -> None:
    save_config(paths, AppConfig(auto_scan_mods=False))
    loaded = load_config(paths)
    assert loaded.auto_scan_mods is False
    payload = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert payload["auto_scan_mods"] is False


def test_save_and_load_update_settings(paths: AppPaths) -> None:
    save_config(
        paths,
        AppConfig(
            check_for_updates=False,
            last_update_check="2026-09-11T18:00:00+00:00",
            skipped_update_version="0.2.0",
        ),
    )
    loaded = load_config(paths)
    assert loaded.check_for_updates is False
    assert loaded.last_update_check == "2026-09-11T18:00:00+00:00"
    assert loaded.skipped_update_version == "0.2.0"


def test_save_and_load_hidden_catalog_mods(paths: AppPaths) -> None:
    save_config(paths, AppConfig(hidden_catalog_mods=["com.example.mod", "com.example.mod", ""]))
    loaded = load_config(paths)
    assert loaded.hidden_catalog_mods == ["com.example.mod"]


def test_save_and_load_catalog_refresh_settings(paths: AppPaths) -> None:
    assert load_config(paths).auto_refresh_catalog is True
    save_config(
        paths,
        AppConfig(auto_refresh_catalog=False, last_catalog_refresh="2026-10-05T08:00:00+00:00"),
    )
    loaded = load_config(paths)
    assert loaded.auto_refresh_catalog is False
    assert loaded.last_catalog_refresh == "2026-10-05T08:00:00+00:00"
