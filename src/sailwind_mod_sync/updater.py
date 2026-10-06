from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

from sailwind_mod_sync.catalog.github import fetch_release
from sailwind_mod_sync.constants import APP_REPO, APP_VERSION, UPDATE_CHECK_HOURS
from sailwind_mod_sync.http_util import HttpClient, HttpError, ProgressFn
from sailwind_mod_sync.library.extract import ExtractError, safe_extract_zip
from sailwind_mod_sync.models import ReleaseAsset, is_newer, parse_mod_version
from sailwind_mod_sync.paths import AppPaths

log = logging.getLogger(__name__)

EXE_NAME = "SailwindModSynchronizer.exe"
_TRUSTED_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


@dataclass
class AppUpdate:
    version: str
    version_raw: str
    tag: str
    html_url: str
    notes: str = ""
    asset_name: str = ""
    download_url: str = ""
    installable: bool = False


def on_windows() -> bool:
    return os.name == "nt"


def self_update_supported() -> bool:
    """The apply step is a Windows PowerShell script; Linux gets the release page until #22's self-update lands."""
    return on_windows()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path | None:
    if not is_frozen():
        return None
    return Path(sys.executable).resolve().parent


def update_check_due(last_iso: str, hours: float = UPDATE_CHECK_HOURS) -> bool:
    if not (last_iso or "").strip():
        return True
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last >= timedelta(hours=hours)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


LINUX_ASSET_SUFFIX = "linux-x86_64.tar.gz"


def pick_update_asset(assets: list[ReleaseAsset], *, windows: bool | None = None) -> ReleaseAsset | None:
    """The release download for this platform: the Windows zip, or the Linux tarball."""
    if windows is None:
        windows = on_windows()
    if not windows:
        return next(
            (
                asset for asset in assets
                if asset.download_url and asset.name.lower().endswith(LINUX_ASSET_SUFFIX)
            ),
            None,
        )
    zips = [
        asset for asset in assets
        if asset.name.lower().endswith(".zip") and asset.download_url and "linux" not in asset.name.lower()
    ]
    if not zips:
        return None

    def score(asset: ReleaseAsset) -> int:
        name = asset.name.lower()
        points = 0
        if "windows" in name:
            points += 10
        if "sailwindmodsynchronizer" in name or "sailwind-mod-sync" in name:
            points += 10
        if "source" in name:
            points -= 20
        if name.endswith("-sources.zip") or name.endswith("_source.zip"):
            points -= 20
        return points

    best = max(zips, key=score)
    return best if score(best) >= 0 else None


def find_app_update(
    http: HttpClient,
    *,
    current_version: str = APP_VERSION,
    skipped_version: str = "",
    ignore_skipped: bool = False,
    paths: AppPaths | None = None,
    progress: ProgressFn | None = None,
) -> AppUpdate | None:
    try:
        release = fetch_release(http, APP_REPO, tag=None, paths=paths, progress=progress)
    except HttpError as exc:
        text = str(exc).lower()
        if exc.status_code == 404 or "no github release" in text or "no published github" in text:
            log.info("No app releases yet: %s", exc)
            return None
        raise
    version = parse_mod_version(release.tag) or parse_mod_version(release.name)
    if not version:
        log.info("Could not parse app release version from %s", release.tag)
        return None
    if not is_newer(version, current_version):
        return None
    if not ignore_skipped and skipped_version and parse_mod_version(skipped_version) == version:
        log.info("Skipping app update %s by user request", version)
        return None
    asset = pick_update_asset(release.assets)
    download_url = ""
    asset_name = ""
    if asset:
        target = asset.api_url if (http.token and asset.api_url) else asset.download_url
        if _trusted_download_url(target):
            download_url = target
            asset_name = asset.name
        else:
            log.warning("Rejected untrusted update URL %s", target)
    return AppUpdate(
        version=version,
        version_raw=release.tag or version,
        tag=release.tag,
        html_url=release.html_url or APP_REPO,
        notes=(release.body or "").strip(),
        asset_name=asset_name,
        download_url=download_url,
        installable=bool(download_url) and is_frozen() and self_update_supported(),
    )


def download_and_stage_update(
    http: HttpClient,
    update: AppUpdate,
    paths: AppPaths,
    progress: ProgressFn | None = None,
) -> Path:
    if not update.download_url:
        raise RuntimeError("This release has no Windows zip to install")
    if not _trusted_download_url(update.download_url):
        raise RuntimeError("Update download is not from GitHub")
    paths.updates_dir.mkdir(parents=True, exist_ok=True)
    archive = paths.updates_dir / (update.asset_name or f"SailwindModSynchronizer-{update.version}.zip")
    if progress:
        progress(f"Downloading {archive.name}…")
    http.download(update.download_url, archive, progress=progress)
    staging = paths.updates_dir / "payload"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("Extracting update…")
    extracted = staging / "extracted"
    extracted.mkdir()
    try:
        safe_extract_zip(archive, extracted)
    except ExtractError as exc:
        raise RuntimeError(str(exc)) from exc
    payload = _payload_root(extracted)
    log.info("Staged app update %s at %s", update.version, payload)
    return payload


def launch_apply_and_exit(payload: Path, *, pid: int | None = None) -> Path:
    dest = install_dir()
    if dest is None:
        raise RuntimeError("Auto-update only works for the installed SailwindModSynchronizer.exe")
    exe = dest / EXE_NAME
    if not exe.is_file() and Path(sys.executable).name.lower().endswith(".exe"):
        exe = Path(sys.executable).resolve()
        dest = exe.parent
    src = payload.resolve()
    dest = dest.resolve()
    if src == dest or dest in src.parents:
        raise RuntimeError("Update payload overlaps the install folder")
    script = _write_apply_script(src, dest, exe, pid if pid is not None else os.getpid())
    if os.name == "nt":
        _launch_hidden(script)
    else:
        subprocess.Popen(["sh", str(script)], start_new_session=True)
    log.info("Launched update script %s", script)
    return script


def zip_release_dir(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with ZipFile(dest, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    return dest


def _payload_root(extracted: Path) -> Path:
    direct = extracted / EXE_NAME
    if direct.is_file():
        return extracted
    matches = [path for path in extracted.rglob(EXE_NAME) if path.is_file()]
    if not matches:
        raise RuntimeError(f"Update zip did not contain {EXE_NAME}")
    matches.sort(key=lambda path: len(path.parts))
    return matches[0].parent


def _trusted_download_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "api.github.com":
        return "/releases/assets/" in parsed.path.lower()
    if host in _TRUSTED_HOSTS:
        return True
    return host.endswith(".githubusercontent.com")


def _launch_hidden(script: Path) -> None:
    powershell = _powershell_exe()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
        startupinfo.wShowWindow = 0
    subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
        ],
        cwd=str(script.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )


def _powershell_exe() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    bundled = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if bundled.is_file():
        return str(bundled)
    return "powershell.exe"


def _write_apply_script(src: Path, dest: Path, exe: Path, pid: int) -> Path:
    log_path = src.parent.parent / "apply.log"
    script = src.parent.parent / "apply_update.ps1"
    wait_pid = int(pid)
    script.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Continue'",
                f"$waitPid = {wait_pid}",
                f"$src = {_ps_single(str(src))}",
                f"$dst = {_ps_single(str(dest))}",
                f"$exe = {_ps_single(str(exe))}",
                f"$log = {_ps_single(str(log_path))}",
                "function Write-Log([string] $Message) {",
                "  Add-Content -LiteralPath $log -Value ((Get-Date -Format o) + ' ' + $Message)",
                "}",
                "Write-Log ('Waiting for process ' + $waitPid)",
                "while (Get-Process -Id $waitPid -ErrorAction SilentlyContinue) {",
                "  Start-Sleep -Seconds 1",
                "}",
                "Start-Sleep -Milliseconds 500",
                "Write-Log 'Copying files'",
                "$robocopy = Join-Path $env:SystemRoot 'System32\\robocopy.exe'",
                "& $robocopy $src $dst /E /IS /IT /R:8 /W:1 /NFL /NDL /NJH /NJS",
                "$rc = $LASTEXITCODE",
                "if ($rc -ge 8) {",
                "  Write-Log ('robocopy failed ' + $rc)",
                "  exit $rc",
                "}",
                "Write-Log 'Starting app'",
                "Start-Process -FilePath $exe -WorkingDirectory $dst",
                "Write-Log 'Done'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def _ps_single(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
