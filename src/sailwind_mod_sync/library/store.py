from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sailwind_mod_sync.library.extract import extract_bepinex_pack, normalize_plugin_archive
from sailwind_mod_sync.library.hashing import dir_size, sha256_file
from sailwind_mod_sync.models import ArtifactMeta, LibraryEntry
from sailwind_mod_sync.paths import AppPaths


class LibraryStore:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def has_mod(self, guid: str, version: str) -> bool:
        extracted = self.mod_extracted(guid, version)
        if extracted.exists() and any(extracted.rglob("*.dll")):
            return True
        zip_path = self.mod_zip_path(guid, version)
        return zip_path.exists() and zip_path.stat().st_size > 0

    def has_bepinex(self, version: str) -> bool:
        extracted = self.bepinex_extracted(version)
        return extracted.exists() and (extracted / "BepInEx" / "core").exists()

    def mod_dir(self, guid: str, version: str) -> Path:
        return self.paths.mod_artifact_dir(guid, version)

    def mod_zip_path(self, guid: str, version: str, meta: ArtifactMeta | None = None) -> Path:
        if meta is None:
            meta = self.read_mod_meta(guid, version)
        directory = self.mod_dir(guid, version)
        if meta and meta.filename:
            candidate = directory / meta.filename
            if candidate.exists():
                return candidate
        matches = list(directory.glob("*.zip"))
        return matches[0] if matches else directory / "artifact.zip"

    def mod_extracted(self, guid: str, version: str) -> Path:
        return self.mod_dir(guid, version) / "extracted"

    def bepinex_dir(self, version: str) -> Path:
        return self.paths.bepinex_dir(version)

    def bepinex_extracted(self, version: str) -> Path:
        return self.bepinex_dir(version) / "extracted"

    def bepinex_zip_path(self, version: str) -> Path:
        return self.bepinex_dir(version) / f"BepInExPack-{version}.zip"

    def read_mod_meta(self, guid: str, version: str) -> ArtifactMeta | None:
        path = self.mod_dir(guid, version) / "metadata.json"
        if not path.exists():
            return None
        try:
            return ArtifactMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None

    def write_mod_meta(self, meta: ArtifactMeta) -> None:
        directory = self.mod_dir(meta.guid, meta.version)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "metadata.json").write_text(
            json.dumps(meta.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def ingest_mod_zip(
        self,
        guid: str,
        version: str,
        zip_path: Path,
        *,
        version_raw: str,
        repo: str,
        source_url: str,
        filename: str | None = None,
        keep_folders: list[str] | None = None,
    ) -> ArtifactMeta:
        directory = self.mod_dir(guid, version)
        directory.mkdir(parents=True, exist_ok=True)
        stored_name = filename or zip_path.name or "artifact.zip"
        stored = directory / stored_name
        if zip_path.resolve() != stored.resolve():
            shutil.copy2(zip_path, stored)
        digest = sha256_file(stored)
        (directory / "sha256").write_text(digest + "\n", encoding="utf-8")
        extracted = self.mod_extracted(guid, version)
        folders = normalize_plugin_archive(stored, extracted, keep_folders=keep_folders)
        meta = ArtifactMeta(
            guid=guid,
            version=version,
            version_raw=version_raw,
            repo=repo,
            source_url=source_url,
            sha256=digest,
            filename=stored_name,
            plugin_folders=folders,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
        )
        self.write_mod_meta(meta)
        return meta

    def ingest_plugin_paths(
        self,
        guid: str,
        version: str,
        sources: list[Path],
        *,
        version_raw: str,
        repo: str,
        source_url: str,
    ) -> ArtifactMeta:
        directory = self.mod_dir(guid, version)
        directory.mkdir(parents=True, exist_ok=True)
        extracted = self.mod_extracted(guid, version)
        if extracted.exists():
            shutil.rmtree(extracted)
        extracted.mkdir(parents=True, exist_ok=True)
        folders: list[str] = []
        for source in sources:
            if source.is_dir():
                dest = extracted / source.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(source, dest)
                folders.append(source.name)
            elif source.is_file():
                dest_dir = extracted / source.stem
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest_dir / source.name)
                folders.append(source.stem)
        zip_path = directory / f"{guid}-{version}.zip"
        _zip_directory(extracted, zip_path)
        digest = sha256_file(zip_path)
        (directory / "sha256").write_text(digest + "\n", encoding="utf-8")
        meta = ArtifactMeta(
            guid=guid,
            version=version,
            version_raw=version_raw,
            repo=repo,
            source_url=source_url,
            sha256=digest,
            filename=zip_path.name,
            plugin_folders=folders,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
        )
        self.write_mod_meta(meta)
        return meta

    def ingest_bepinex_zip(self, version: str, zip_path: Path) -> Path:
        directory = self.bepinex_dir(version)
        directory.mkdir(parents=True, exist_ok=True)
        stored = self.bepinex_zip_path(version)
        if zip_path.resolve() != stored.resolve():
            shutil.copy2(zip_path, stored)
        extracted = self.bepinex_extracted(version)
        extract_bepinex_pack(stored, extracted)
        (directory / "sha256").write_text(sha256_file(stored) + "\n", encoding="utf-8")
        return extracted

    def list_mods(self) -> list[LibraryEntry]:
        entries: list[LibraryEntry] = []
        if not self.paths.library_mods.exists():
            return entries
        for guid_dir in sorted(self.paths.library_mods.iterdir()):
            if not guid_dir.is_dir():
                continue
            for version_dir in sorted(guid_dir.iterdir()):
                if not version_dir.is_dir():
                    continue
                guid = guid_dir.name
                version = version_dir.name
                meta = self.read_mod_meta(guid, version)
                if meta is None:
                    continue
                zip_path = self.mod_zip_path(guid, version, meta)
                extracted = self.mod_extracted(guid, version)
                size = dir_size(zip_path) if zip_path.exists() else dir_size(version_dir)
                entries.append(
                    LibraryEntry(
                        guid=guid,
                        version=version,
                        path=version_dir,
                        zip_path=zip_path,
                        extracted_dir=extracted,
                        meta=meta,
                        size_bytes=size,
                    )
                )
        return entries

    def rekey_mod(self, old_guid: str, old_version: str, new_guid: str, *, repo: str = "") -> ArtifactMeta:
        if old_guid == new_guid:
            meta = self.read_mod_meta(old_guid, old_version)
            if meta is None:
                raise FileNotFoundError(self.mod_dir(old_guid, old_version))
            if repo:
                meta.repo = repo
            self.write_mod_meta(meta)
            return meta
        dest_meta = self.read_mod_meta(new_guid, old_version)
        if dest_meta is not None:
            dest_meta.repo = repo or dest_meta.repo
            dest_meta.guid = new_guid
            self.write_mod_meta(dest_meta)
            self.delete_mod(old_guid, old_version)
            return dest_meta
        src = self.mod_dir(old_guid, old_version)
        dest = self.mod_dir(new_guid, old_version)
        if not src.exists():
            raise FileNotFoundError(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(src), str(dest))
        guid_dir = src.parent
        if guid_dir.exists() and not any(guid_dir.iterdir()):
            guid_dir.rmdir()
        meta = self.read_mod_meta(new_guid, old_version)
        if meta is None:
            raise FileNotFoundError(dest)
        meta.guid = new_guid
        if repo:
            meta.repo = repo
        self.write_mod_meta(meta)
        return meta

    def delete_mod(self, guid: str, version: str) -> None:
        directory = self.mod_dir(guid, version)
        if directory.exists():
            shutil.rmtree(directory)
        guid_dir = directory.parent
        if guid_dir.exists() and not any(guid_dir.iterdir()):
            guid_dir.rmdir()

    def prune_unused(self, pinned: set[tuple[str, str]]) -> int:
        removed = 0
        for entry in self.list_mods():
            if (entry.guid, entry.version) not in pinned:
                self.delete_mod(entry.guid, entry.version)
                removed += 1
        return removed


def _zip_directory(source: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())

