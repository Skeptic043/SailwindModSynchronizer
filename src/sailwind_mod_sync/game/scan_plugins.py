from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sailwind_mod_sync.catalog.mvc import find_entry
from sailwind_mod_sync.game.plugin_metadata import read_plugin_metadata
from sailwind_mod_sync.library.main_dll import (
    SKIP_GUID_PREFIXES,
    is_generic_folder_name,
    pick_main_dll,
    _tokens,
)
from sailwind_mod_sync.models import CatalogEntry, parse_mod_version

log = logging.getLogger(__name__)

LOAD_RE = re.compile(r"Loading \[(.+?) (\d+(?:\.\d+){1,3})\]")
LOG_GUID_RE = re.compile(r"Plugin ([A-Za-z0-9_.]+) is loaded")
DLL_GUID_RE = re.compile(
    rb"(?:com|zzz|pr0skynesis|NatoriusG|DogEggz|dogeggz)\.[A-Za-z0-9_.]{2,80}"
)
LOOSE_GUID_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_]{1,40}(?:\.[A-Za-z0-9_]{1,40}){1,6}")
DLL_VERSION_RE = re.compile(rb"(?:\d+\.){1,3}\d+")
SKIP_DLL_HINTS = ("bridge", "facepunch", "scripthandler")
AUTO_MATCH_SCORE = 60
AUTO_MATCH_EXACT = 80
AUTO_MATCH_MARGIN = 15
SUGGEST_SCORE = 12


class AmbiguousPluginError(ValueError):
    """A file or folder declares more than one plugin and cannot be one mod."""


@dataclass
class DiscoveredPlugin:
    name: str
    guid: str
    version: str
    version_raw: str
    plugin_paths: list[Path]
    repo: str = ""
    source: str = ""
    declared_identity: bool = False


@dataclass(frozen=True)
class PluginScanResult:
    discovered: tuple[DiscoveredPlugin, ...]
    skipped_plugins: tuple[str, ...] = ()


def scan_plugins_dir(
    plugins_dir: Path,
    catalog: list[CatalogEntry] | None = None,
    log_path: Path | None = None,
) -> PluginScanResult:
    catalog = catalog or []
    log_versions = parse_load_versions(log_path) if log_path and log_path.exists() else {}
    log_guids = parse_log_guids(log_path) if log_path and log_path.exists() else {}
    discovered: list[DiscoveredPlugin] = []
    skipped: list[str] = []
    if not plugins_dir.is_dir():
        return PluginScanResult(())
    for child in sorted(plugins_dir.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            dlls = list(child.rglob("*.dll"))
            if not dlls:
                continue
            name = child.name
        elif child.suffix.lower() == ".dll":
            name = child.stem
            dlls = [child]
        else:
            continue
        try:
            unit = _from_unit(name, [child], dlls, catalog, log_versions, log_guids)
        except AmbiguousPluginError as exc:
            log.warning("Skipping %s", exc)
            skipped.append(str(exc))
            continue
        if unit:
            discovered.append(unit)
    return PluginScanResult(tuple(discovered), tuple(skipped))


def discover_local_file(
    path: Path,
    catalog: list[CatalogEntry] | None = None,
    hints: tuple[str, ...] = (),
) -> list[DiscoveredPlugin]:
    catalog = catalog or []
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".dll":
        unit = _from_unit(path.stem, [path], [path], catalog, {}, {},
                          loose_guid=True, hints=hints)
        if unit is None:
            return []
        _refine_local_identity(unit, path)
        return [unit]
    if suffix == ".zip":
        return _discover_zip(path, catalog, hints=hints)
    raise ValueError(f"Unsupported file type: {path.suffix}. Use a .dll or .zip.")


def parse_load_versions(log_path: Path) -> dict[str, str]:
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    versions: dict[str, str] = {}
    for match in LOAD_RE.finditer(text):
        versions[match.group(1)] = match.group(2)
    return versions


def parse_log_guids(log_path: Path) -> dict[str, str]:
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    found: dict[str, str] = {}
    for match in LOG_GUID_RE.finditer(text):
        guid = match.group(1)
        found[_norm(guid.split(".")[-1])] = guid
    return found


def extract_guids(dll_path: Path, *, loose: bool = False) -> list[str]:
    try:
        data = dll_path.read_bytes()
    except OSError:
        return []
    seen: list[str] = []
    for match in DLL_GUID_RE.finditer(data):
        guid = match.group(0).decode("ascii", errors="ignore")
        if guid not in seen:
            seen.append(guid)
    if loose:
        for guid in _loose_guids(data):
            if guid not in seen:
                seen.append(guid)
    return seen


def _from_unit(
    name: str,
    paths: list[Path],
    dlls: list[Path],
    catalog: list[CatalogEntry],
    log_versions: dict[str, str],
    log_guids: dict[str, str],
    loose_guid: bool = False,
    hints: tuple[str, ...] = (),
) -> DiscoveredPlugin | None:
    inspected = [(dll, read_plugin_metadata(dll)) for dll in dlls]
    declared = [plugin for _, result in inspected for plugin in result.plugins]
    if len(declared) > 1:
        identities = ", ".join(f"{plugin.name} ({plugin.guid})" for plugin in declared)
        raise AmbiguousPluginError(
            f"Multiple plugin identities found in {name}: {identities}. "
            "Import a package containing one plugin per file or folder."
        )
    if declared:
        plugin = declared[0]
        # Catalog associates a download source, never changes declared identity.
        entry = find_entry(catalog, plugin.guid)
        return DiscoveredPlugin(
            name=plugin.name, guid=plugin.guid, version=plugin.version,
            version_raw=plugin.version, plugin_paths=paths,
            repo=entry.repo if entry else "", source=str(paths[0]),
            declared_identity=True,
        )
    dlls = [dll for dll, result in inspected if result.status == "unsupported"]
    if not dlls:
        return None
    main_dlls = [path for path in dlls if not _skip_dll(path)]
    search_dlls = main_dlls or dlls
    all_hints = (name,) + hints if name else hints
    main_dll = pick_main_dll(search_dlls, hints=all_hints)
    dll_key = _norm(main_dll.stem) if main_dll else ""
    candidates: list[str] = []
    for dll in search_dlls:
        for guid in extract_guids(dll, loose=loose_guid):
            if guid not in candidates:
                candidates.append(guid)
    guid = _pick_guid(name, dll_key, candidates, catalog, log_guids)
    if not guid:
        guid = f"local.{_norm(name)}"
    guid, entry = apply_catalog_identity(name, guid, catalog)
    if is_generic_folder_name(name) and main_dll:
        name = main_dll.stem
    version = (
        _match_log_version(name, guid, log_versions)
        or "0.0.0"
    )
    return DiscoveredPlugin(
        name=name,
        guid=guid,
        version=version,
        version_raw=version if not version.startswith("v") else version,
        plugin_paths=paths,
        repo=entry.repo if entry else "",
        source=str(paths[0]),
    )


def _pick_guid(
    name: str,
    dll_key: str,
    candidates: list[str],
    catalog: list[CatalogEntry],
    log_guids: dict[str, str],
) -> str:
    name_key = _norm(name)
    log_guid = log_guids.get(name_key)
    if log_guid:
        return log_guid
    if not candidates:
        ranked = rank_catalog_matches(name, "", catalog)
        if ranked and ranked[0][0] >= AUTO_MATCH_EXACT:
            if len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= AUTO_MATCH_MARGIN:
                return ranked[0][1].primary_guid
        return ""

    def score(guid: str) -> int:
        guid_key = _norm(guid)
        tail = _norm(guid.split(".")[-1])
        points = 0
        if name_key and name_key in guid_key:
            points += 50
        if dll_key and dll_key in guid_key:
            points += 40
        if len(tail) >= 4 and (tail == name_key or tail in name_key or name_key in tail):
            points += 30
        if len(tail) >= 4 and (tail == dll_key or tail in dll_key or dll_key in tail):
            points += 20
        if tail in {"mod", "plugin", "core"}:
            points -= 8
        if find_entry(catalog, guid) and points >= 30:
            points += 10
        return points

    pool = [g for g in candidates if DLL_GUID_RE.match(g.encode("ascii", errors="ignore"))]
    if not pool:
        pool = candidates
    return max(pool, key=score)


def _match_log_version(name: str, guid: str, log_versions: dict[str, str]) -> str | None:
    name_tokens = _tokens(name)
    best: tuple[int, str] | None = None
    for log_name, version in log_versions.items():
        if _norm(log_name) == _norm(name) or _norm(log_name) == _norm(guid.split(".")[-1]):
            return version
        log_tokens = _tokens(log_name)
        if not name_tokens or not log_tokens:
            continue
        overlap = name_tokens & log_tokens
        if not overlap:
            continue
        score = len(overlap) * 10
        if name_tokens <= log_tokens:
            score += 25
        if log_tokens <= name_tokens:
            score += 5
        score -= abs(len(log_tokens) - len(name_tokens))
        if best is None or score > best[0]:
            best = (score, version)
    if best and best[0] >= 20:
        return best[1]
    return None


def apply_catalog_identity(
    name: str,
    guid: str,
    catalog: list[CatalogEntry],
) -> tuple[str, CatalogEntry | None]:
    entry = find_entry(catalog, guid)
    if entry is not None:
        return guid, entry
    ranked = rank_catalog_matches(name, guid, catalog)
    if not ranked:
        return guid, None
    score, matched = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0
    if second and score - second < AUTO_MATCH_MARGIN:
        return guid, None
    local = guid.lower().startswith("local.")
    if score >= AUTO_MATCH_EXACT or (local and score >= AUTO_MATCH_SCORE):
        if guid in matched.guids or guid == matched.primary_guid:
            return guid, matched
        if local:
            return matched.primary_guid, matched
        return guid, None
    return guid, None


def rank_catalog_matches(
    name: str,
    guid: str,
    catalog: list[CatalogEntry],
    extra_names: list[str] | None = None,
) -> list[tuple[int, CatalogEntry]]:
    scored: list[tuple[int, CatalogEntry]] = []
    for entry in catalog:
        points = _catalog_match_score(name, guid, entry, extra_names)
        if points >= SUGGEST_SCORE:
            scored.append((points, entry))
    scored.sort(key=lambda item: (-item[0], item[1].name.lower()))
    return scored


def _catalog_match_score(
    name: str,
    guid: str,
    entry: CatalogEntry,
    extra_names: list[str] | None,
) -> int:
    if guid in entry.guids or guid == entry.primary_guid:
        return 100
    labels = [name, *(extra_names or [])]
    repo_key = _norm(entry.name)
    repo_slug = _norm(entry.repo.rstrip("/").split("/")[-1])
    tails = [_norm(item.split(".")[-1]) for item in entry.guids]
    tails.append(_norm(entry.primary_guid.split(".")[-1]))
    catalog_tokens = _tokens(entry.name) | _tokens(repo_slug)
    for item in entry.guids:
        catalog_tokens |= _tokens(item.split(".")[-1])
    guid_tail = _norm(guid.split(".")[-1]) if guid else ""
    points = 0
    if guid_tail and len(guid_tail) >= 4 and guid_tail in tails:
        points = max(points, 70)
    for raw in labels:
        key = _norm(raw)
        if not key:
            continue
        if key == repo_key or key == repo_slug:
            points = max(points, 85)
        if key in tails:
            points = max(points, 80)
        for tail in tails:
            if len(tail) >= 4 and tail != key and (tail in key or key in tail):
                points = max(points, 45)
        name_tokens = _tokens(raw)
        overlap = name_tokens & catalog_tokens
        if overlap:
            token_score = len(overlap) * 12
            if name_tokens and name_tokens <= catalog_tokens:
                token_score += 20
            if len(name_tokens) >= 2 and name_tokens <= catalog_tokens:
                token_score = max(token_score, 75)
            points = max(points, token_score)
    return points


def _skip_dll(path: Path) -> bool:
    stem = path.stem.lower()
    return any(hint in stem for hint in SKIP_DLL_HINTS)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _discover_zip(
    path: Path,
    catalog: list[CatalogEntry],
    hints: tuple[str, ...] = (),
) -> list[DiscoveredPlugin]:
    from sailwind_mod_sync.library.extract import (
        _find_bepinex_plugins,
        _safe_extract,
        _strip_junk,
        _unwrap_single_root,
    )

    archive_hints = (path.stem,) + hints
    with tempfile.TemporaryDirectory(prefix="sms-import-") as tmp:
        root = Path(tmp) / "extracted"
        root.mkdir()
        _safe_extract(path, root)
        _strip_junk(root)
        source = _unwrap_single_root(root)
        plugins = _find_bepinex_plugins(source)
        scan = scan_plugins_dir(plugins or source, catalog=catalog)
        if scan.skipped_plugins:
            raise AmbiguousPluginError("\n\n".join(scan.skipped_plugins))
        found = scan.discovered
        refined: list[DiscoveredPlugin] = []
        for unit in found:
            dlls = []
            for item in unit.plugin_paths:
                if item.is_file() and item.suffix.lower() == ".dll":
                    dlls.append(item)
                elif item.is_dir():
                    dlls.extend(item.rglob("*.dll"))
            if dlls:
                extra = _from_unit(unit.name, unit.plugin_paths, dlls, catalog, {}, {},
                                   loose_guid=True, hints=archive_hints)
                if extra:
                    unit.guid = extra.guid
                    unit.repo = extra.repo or unit.repo
            _refine_local_identity(unit, path)
            unit.plugin_paths = [path]
            unit.source = str(path)
            refined.append(unit)
        return refined


def _refine_local_identity(unit: DiscoveredPlugin, path: Path) -> None:
    if unit.declared_identity:
        return
    dlls: list[Path] = []
    for item in unit.plugin_paths:
        if item.is_file() and item.suffix.lower() == ".dll":
            dlls.append(item)
        elif item.is_dir():
            dlls.extend(item.rglob("*.dll"))
    dll_version = None
    for dll in dlls:
        dll_version = _version_from_dll(dll, unit.guid)
        if dll_version:
            break
    file_version = parse_mod_version(path.stem) or parse_mod_version(path.name)
    chosen = dll_version or file_version
    if chosen:
        unit.version = chosen
        unit.version_raw = chosen


def _loose_guids(data: bytes) -> list[str]:
    found: list[str] = []
    for blob in (data, data.decode("utf-16-le", errors="ignore").encode("ascii", errors="ignore")):
        for match in LOOSE_GUID_RE.finditer(blob):
            guid = match.group(0).decode("ascii", errors="ignore")
            lower = guid.lower()
            if any(lower.startswith(prefix) for prefix in SKIP_GUID_PREFIXES):
                continue
            if guid.count(".") < 1:
                continue
            if guid not in found:
                found.append(guid)
    return found


def _version_from_dll(dll_path: Path, guid: str) -> str | None:
    try:
        data = dll_path.read_bytes()
    except OSError:
        return None
    windows: list[bytes] = []
    guid_ascii = guid.encode("ascii", errors="ignore")
    guid_utf16 = guid.encode("utf-16-le", errors="ignore")
    for needle in (guid_ascii, guid_utf16):
        if not needle:
            continue
        idx = data.find(needle)
        if idx >= 0:
            windows.append(data[idx : idx + 800])
    windows.append(data[:8192])
    for window in windows:
        for match in DLL_VERSION_RE.finditer(window):
            raw = match.group(0).decode("ascii", errors="ignore")
            if raw in {"0.0.0", "0.0.0.0", "1.0.0.0", "4.0.0.0"}:
                continue
            parsed = parse_mod_version(raw)
            if parsed and parsed.count(".") >= 1:
                return parsed
    return None

