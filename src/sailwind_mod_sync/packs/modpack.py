from __future__ import annotations

import io
import json
import logging
import re
import shutil
import zipfile
from pathlib import Path

from sailwind_mod_sync.constants import DEFAULT_BEPINEX_VERSION, DEFAULT_PACK_NAME
from sailwind_mod_sync.http_util import ProgressFn
from sailwind_mod_sync.models import ModPack, PinnedMod
from sailwind_mod_sync.paths import AppPaths, sanitize_segment

log = logging.getLogger(__name__)

PACK_FILENAME = "modpack.json"
BUNDLE_BEPINEX_DIR = "bepinex"
BUNDLE_CONFIG_DIR = "config"
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "pack"


class PackStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def list_packs(self) -> list[ModPack]:
        packs: list[ModPack] = []
        if not self.paths.packs_dir.exists():
            return packs
        for directory in sorted(self.paths.packs_dir.iterdir()):
            manifest = directory / PACK_FILENAME
            if not manifest.exists():
                continue
            pack = self._read_manifest(manifest)
            if pack:
                packs.append(pack)
        return packs

    def get(self, pack_id: str) -> ModPack:
        pack = self._read_manifest(self.manifest_path(pack_id))
        if pack is None:
            raise FileNotFoundError(f"ModPack not found: {pack_id}")
        return pack

    def exists(self, pack_id: str) -> bool:
        return self.manifest_path(pack_id).exists()

    def pack_dir(self, pack_id: str) -> Path:
        return self.paths.pack_dir(pack_id)

    def instance_dir(self, pack_id: str) -> Path:
        return self.pack_dir(pack_id) / "instance"

    def plugins_dir(self, pack_id: str) -> Path:
        return self.instance_dir(pack_id) / "BepInEx" / "plugins"

    def manifest_path(self, pack_id: str) -> Path:
        return self.pack_dir(pack_id) / PACK_FILENAME

    def save(self, pack: ModPack) -> None:
        directory = self.pack_dir(pack.id)
        directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path(pack.id).write_text(
            json.dumps(pack.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def create(self, name: str, bepinex: str = DEFAULT_BEPINEX_VERSION) -> ModPack:
        pack_id = self._unique_id(slugify(name))
        pack = ModPack(id=pack_id, name=name.strip() or pack_id, bepinex=bepinex)
        self.save(pack)
        self.plugins_dir(pack.id).mkdir(parents=True, exist_ok=True)
        return pack

    def ensure_default(self) -> ModPack:
        packs = self.list_packs()
        if packs:
            return packs[0]
        return self.create(DEFAULT_PACK_NAME)

    def delete(self, pack_id: str) -> None:
        directory = self.pack_dir(pack_id)
        if directory.exists():
            shutil.rmtree(directory)

    def rename(self, pack_id: str, new_name: str) -> ModPack:
        pack = self.get(pack_id)
        pack.name = new_name.strip() or pack.name
        self.save(pack)
        return pack

    def duplicate(self, pack_id: str, new_name: str) -> ModPack:
        source = self.get(pack_id)
        copy = self.create(new_name, bepinex=source.bepinex)
        copy.version = source.version
        copy.mods = [PinnedMod.from_dict(mod.to_dict()) for mod in source.mods]
        self.save(copy)
        src_instance = self.instance_dir(pack_id)
        dst_instance = self.instance_dir(copy.id)
        if src_instance.exists():
            _copy_instance(src_instance, dst_instance)
        return copy

    def set_mods(self, pack_id: str, mods: list[PinnedMod]) -> ModPack:
        pack = self.get(pack_id)
        pack.mods = mods
        self.save(pack)
        return pack

    def upsert_mod(self, pack_id: str, pinned: PinnedMod) -> ModPack:
        pack = self.get(pack_id)
        remaining = [mod for mod in pack.mods if mod.guid != pinned.guid]
        remaining.append(pinned)
        remaining.sort(key=lambda mod: mod.guid.lower())
        pack.mods = remaining
        self.save(pack)
        return pack

    def remove_mod(self, pack_id: str, guid: str) -> ModPack:
        pack = self.get(pack_id)
        target = pack.find_mod(guid)
        pack.mods = [mod for mod in pack.mods if mod.guid != guid]
        self.save(pack)
        if target:
            from sailwind_mod_sync.packs.instance import remove_plugin_folders

            remove_plugin_folders(self.plugins_dir(pack_id), target.plugin_folders)
        return pack

    def export_json(self, pack_id: str, dest: Path) -> Path:
        pack = self.get(pack_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(pack.to_dict(), indent=2) + "\n", encoding="utf-8")
        return dest

    def export_bundle(
        self,
        pack_id: str,
        dest: Path,
        library,
        include_context: bool = False,
        progress: ProgressFn | None = None,
    ) -> Path:
        """Write a bundle of manifest and mod zips; with include_context, also BepInExPack and pack configs."""
        pack = self.get(pack_id)
        total = len(pack.mods)

        def report(message: str) -> None:
            if progress:
                progress(message)

        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(PACK_FILENAME, json.dumps(pack.to_dict(), indent=2) + "\n")
            if include_context and pack.bepinex and library.has_bepinex(pack.bepinex):
                bx_zip = library.bepinex_zip_path(pack.bepinex)
                if bx_zip.exists():
                    report(f"Adding BepInEx {pack.bepinex} ({bx_zip.name})…")
                    zf.write(bx_zip, f"{BUNDLE_BEPINEX_DIR}/{bx_zip.name}")
            for index, mod in enumerate(pack.mods, start=1):
                if not library.has_mod(mod.guid, mod.version):
                    report(f"Skipping {mod.guid} {mod.version}: not downloaded ({index}/{total})")
                    continue
                zip_path = library.mod_zip_path(mod.guid, mod.version)
                meta_path = library.mod_dir(mod.guid, mod.version) / "metadata.json"
                prefix = f"artifacts/{sanitize_segment(mod.guid)}/{sanitize_segment(mod.version)}"
                report(f"Adding {zip_path.name} ({index}/{total})…")
                if zip_path.exists():
                    zf.write(zip_path, f"{prefix}/{zip_path.name}")
                else:
                    _write_dir_as_zip(zf, library.mod_extracted(mod.guid, mod.version), f"{prefix}/{zip_path.name}")
                if meta_path.exists():
                    zf.write(meta_path, f"{prefix}/metadata.json")
            config_dir = self.instance_dir(pack_id) / "BepInEx" / "config"
            if include_context and config_dir.is_dir():
                for path in sorted(config_dir.rglob("*")):
                    if path.is_file() and not _is_ignored_log(path.name):
                        report(f"Adding config {path.relative_to(config_dir).as_posix()}…")
                        zf.write(path, f"{BUNDLE_CONFIG_DIR}/{path.relative_to(config_dir).as_posix()}")
        report(f"Finished writing {dest.name}")
        return dest

    def import_manifest(self, payload: dict) -> ModPack:
        incoming = ModPack.from_dict(payload)
        if not incoming.name:
            incoming.name = incoming.id or "Imported"
        incoming.id = self._unique_id(slugify(incoming.name) or incoming.id or "imported")
        if not incoming.bepinex:
            incoming.bepinex = DEFAULT_BEPINEX_VERSION
        self.save(incoming)
        self.plugins_dir(incoming.id).mkdir(parents=True, exist_ok=True)
        return incoming

    def import_file(self, path: Path, library) -> ModPack:
        log.info("Reading pack file %s", path)
        if path.suffix.lower() == ".zip":
            return self._import_bundle(path, library)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("ModPack JSON must be an object")
        pack = self.import_manifest(payload)
        log.info("Created pack %s (%s) from JSON with %s mod(s)", pack.id, pack.name, len(pack.mods))
        return pack

    def _import_bundle(self, path: Path, library) -> ModPack:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            manifest_name = next((n for n in names if n.endswith(PACK_FILENAME) and n.count("/") <= 1), None)
            if not manifest_name:
                raise ValueError("Bundle is missing modpack.json")
            payload = json.loads(zf.read(manifest_name).decode("utf-8"))
            pack = self.import_manifest(payload)
            self._import_bundle_extras(zf, pack, library)
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name.startswith("artifacts/") or info.is_dir():
                    continue
                parts = name.split("/")
                if len(parts) < 4:
                    continue
                guid, version, filename = parts[1], parts[2], parts[-1]
                target_dir = library.mod_dir(guid, version)
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / filename
                with zf.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                if filename.lower().endswith(".zip"):
                    meta_name = "/".join(parts[:3] + ["metadata.json"])
                    repo = ""
                    source_url = ""
                    version_raw = version
                    if meta_name in names:
                        meta = json.loads(zf.read(meta_name).decode("utf-8"))
                        repo = str(meta.get("repo") or "")
                        source_url = str(meta.get("source_url") or "")
                        version_raw = str(meta.get("version_raw") or version)
                    library.ingest_mod_zip(
                        guid,
                        version,
                        target,
                        version_raw=version_raw,
                        repo=repo,
                        source_url=source_url,
                        filename=filename,
                    )
                    log.info("Ingested bundle artifact %s %s", guid, version)
        log.info("Created pack %s (%s) from bundle with %s mod(s)", pack.id, pack.name, len(pack.mods))
        return pack

    def _import_bundle_extras(self, zf: zipfile.ZipFile, pack: ModPack, library) -> None:
        config_dir = self.instance_dir(pack.id) / "BepInEx" / "config"
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            if name.startswith(f"{BUNDLE_BEPINEX_DIR}/") and name.lower().endswith(".zip"):
                if not pack.bepinex or library.has_bepinex(pack.bepinex):
                    continue
                target = library.bepinex_zip_path(pack.bepinex)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                library.ingest_bepinex_zip(pack.bepinex, target)
                log.info("Ingested bundled BepInEx %s", pack.bepinex)
            elif name.startswith(f"{BUNDLE_CONFIG_DIR}/"):
                relative = name[len(BUNDLE_CONFIG_DIR) + 1 :]
                target = (config_dir / relative).resolve()
                if not target.is_relative_to(config_dir.resolve()):
                    log.warning("Skipping unsafe bundle config path %s", name)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)

    def _read_manifest(self, path: Path) -> ModPack | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        pack = ModPack.from_dict(data)
        if not pack.id:
            pack.id = path.parent.name
        return pack

    def _unique_id(self, base: str) -> str:
        candidate = base
        index = 2
        while self.exists(candidate):
            candidate = f"{base}-{index}"
            index += 1
        return candidate


_INSTANCE_IGNORE = shutil.ignore_patterns(
    "LogOutput.log",
    "*.log",
    "ErrorLog*",
    "harmony.log",
)


def _is_ignored_log(name: str) -> bool:
    return bool(_INSTANCE_IGNORE(None, [name]))


def _write_dir_as_zip(zf: zipfile.ZipFile, source: Path, arcname: str) -> None:
    """Nest an extracted plugin folder as a zip inside the bundle."""
    if not source.is_dir():
        return
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as inner:
        for path in source.rglob("*"):
            if path.is_file():
                inner.write(path, path.relative_to(source).as_posix())
    zf.writestr(arcname, buffer.getvalue())


def _copy_instance(src: Path, dst: Path) -> None:
    """Copy a pack instance, skipping locked BepInEx logs so duplicate can finish."""
    try:
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=_INSTANCE_IGNORE, dirs_exist_ok=True)
    except OSError as exc:
        log.warning("Could not copy pack instance %s -> %s: %s", src, dst, exc)
