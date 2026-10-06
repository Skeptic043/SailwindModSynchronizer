from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from sailwind_mod_sync.fileutil import atomic_write_json
from sailwind_mod_sync.paths import AppPaths


@dataclass
class AppConfig:
    game_path: str = ""
    github_token: str = ""
    last_pack_id: str = ""
    warn_missing_mods: bool = True
    check_for_updates: bool = True
    auto_scan_mods: bool = True
    auto_refresh_catalog: bool = True
    last_update_check: str = ""
    last_catalog_refresh: str = ""
    skipped_update_version: str = ""
    hidden_catalog_mods: list[str] = field(default_factory=list)
    catalog_sources_restored: bool = False

    def token(self) -> str:
        return self.github_token.strip() or os.environ.get("GITHUB_TOKEN", "").strip()


def load_config(paths: AppPaths) -> AppConfig:
    path = paths.config_file
    if not path.exists():
        return AppConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig()
    if not isinstance(data, dict):
        return AppConfig()
    return AppConfig(
        game_path=str(data.get("game_path") or ""),
        github_token=str(data.get("github_token") or ""),
        last_pack_id=str(data.get("last_pack_id") or ""),
        warn_missing_mods=_as_bool(data.get("warn_missing_mods"), True),
        check_for_updates=_as_bool(data.get("check_for_updates"), True),
        auto_scan_mods=_as_bool(data.get("auto_scan_mods"), True),
        auto_refresh_catalog=_as_bool(data.get("auto_refresh_catalog"), True),
        last_update_check=str(data.get("last_update_check") or ""),
        last_catalog_refresh=str(data.get("last_catalog_refresh") or ""),
        skipped_update_version=str(data.get("skipped_update_version") or ""),
        hidden_catalog_mods=_as_str_list(data.get("hidden_catalog_mods")),
        catalog_sources_restored=_as_bool(data.get("catalog_sources_restored"), False),
    )


def save_config(paths: AppPaths, config: AppConfig) -> None:
    paths.ensure()
    payload = asdict(config)
    atomic_write_json(paths.config_file, payload)


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for entry in value:
        text = str(entry).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
