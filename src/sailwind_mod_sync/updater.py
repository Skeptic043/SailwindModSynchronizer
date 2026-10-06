from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tarfile
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
LINUX_BINARY_NAME = "SailwindModSynchronizer"
LINUX_LAUNCHER_NAME = "sailwind-mod-sync"
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
    """Windows applies updates with PowerShell, Linux with a shell script."""
    return on_windows() or sys.platform.startswith("linux")


def app_binary_name(*, windows: bool | None = None) -> str:
    if windows is None:
        windows = on_windows()
    return EXE_NAME if windows else LINUX_BINARY_NAME


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
        raise RuntimeError("This release has no download for this platform")
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
        if archive.name.lower().endswith((".tar.gz", ".tgz")):
            safe_extract_tar(archive, extracted)
        else:
            safe_extract_zip(archive, extracted)
    except (ExtractError, tarfile.TarError) as exc:
        raise RuntimeError(str(exc)) from exc
    payload = _payload_root(extracted)
    log.info("Staged app update %s at %s", update.version, payload)
    return payload


def launch_apply_and_exit(payload: Path, *, pid: int | None = None) -> Path:
    dest = install_dir()
    if dest is None:
        raise RuntimeError("Auto-update only works for the installed Sailwind Mod Synchronizer")
    wait_pid = pid if pid is not None else os.getpid()
    if not on_windows():
        return _launch_linux_apply(payload, dest, wait_pid)
    exe = dest / EXE_NAME
    if not exe.is_file() and Path(sys.executable).name.lower().endswith(".exe"):
        exe = Path(sys.executable).resolve()
        dest = exe.parent
    src = payload.resolve()
    dest = dest.resolve()
    if src == dest or dest in src.parents:
        raise RuntimeError("Update payload overlaps the install folder")
    script = _write_apply_script(src, dest, exe, wait_pid)
    _launch_hidden(script)
    log.info("Launched update script %s", script)
    return script


def _launch_linux_apply(payload: Path, dest: Path, pid: int) -> Path:
    src = payload.resolve()
    dest = dest.resolve()
    if src == dest or dest in src.parents:
        raise RuntimeError("Update payload overlaps the install folder")
    # Restart through the launcher so Steam's LD_LIBRARY_PATH/LD_PRELOAD are cleared.
    start = dest / LINUX_LAUNCHER_NAME
    if not (src / LINUX_LAUNCHER_NAME).is_file() and not start.is_file():
        start = dest / LINUX_BINARY_NAME
    script = write_linux_apply_script(src, dest, start, pid)
    subprocess.Popen(
        ["/bin/sh", str(script)],
        cwd=str(script.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    log.info("Launched update script %s", script)
    return script


def write_linux_apply_script(src: Path, dest: Path, start: Path, pid: int) -> Path:
    """A shell script that waits for the app to exit, copies the new build over it, and restarts it."""
    work = _updates_folder(src)
    log_path = work / "apply.log"
    script = work / "apply_update.sh"
    lines = [
        "#!/bin/sh",
        f"src={_sh_single(str(src))}",
        f"dst={_sh_single(str(dest))}",
        f"start={_sh_single(str(start))}",
        f"log={_sh_single(str(log_path))}",
        f"pid={int(pid)}",
        'say() { echo "$(date -Iseconds 2>/dev/null || date) $*" >> "$log"; }',
        # A process that has exited but not been reaped yet is a zombie ("Z"); kill -0 still
        # succeeds on it, so read its state from /proc instead.
        'alive() {',
        '  [ -r "/proc/$pid/stat" ] || return 1',
        '  state=$(sed "s/^.*) //" "/proc/$pid/stat" 2>/dev/null | cut -c1)',
        '  [ -n "$state" ] && [ "$state" != "Z" ]',
        '}',
        'say "Waiting for process $pid"',
        'waited=0',
        'while alive; do',
        '  if [ "$waited" -ge 300 ]; then',
        '    say "Gave up waiting for process $pid; nothing was changed"',
        '    exit 1',
        '  fi',
        '  sleep 1',
        '  waited=$((waited + 1))',
        'done',
        "sleep 1",
        'say "Copying files"',
        'if ! cp -a "$src/." "$dst/"; then',
        '  say "Copy failed"',
        "  exit 1",
        "fi",
        'chmod +x "$dst/SailwindModSynchronizer" "$dst/sailwind-mod-sync" 2>/dev/null',
        'say "Starting app"',
        'cd "$dst" || exit 1',
        'nohup "$start" >/dev/null 2>&1 &',
        'say "Done"',
    ]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    script.chmod(0o755)
    return script


def _updates_folder(src: Path) -> Path:
    """The updates folder holding payload/ (the Linux archive nests one folder deeper than the zip)."""
    for parent in src.parents:
        if parent.name == "payload":
            return parent.parent
    return src.parent.parent


def safe_extract_tar(archive: Path, dest: Path) -> None:
    """Extract a .tar.gz, refusing absolute paths, '..' and links that point outside dest."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
            return
        root = dest.resolve()
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if member.name.startswith(("/", "\\")) or (target != root and root not in target.parents):
                raise ExtractError(f"Unsafe path in update archive: {member.name}")
            if member.issym() or member.islnk():
                raise ExtractError(f"Links are not allowed in update archive: {member.name}")
        tar.extractall(dest)


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
    name = app_binary_name()
    direct = extracted / name
    if direct.is_file():
        return extracted
    matches = [path for path in extracted.rglob(name) if path.is_file()]
    if not matches:
        raise RuntimeError(f"Update archive did not contain {name}")
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


def _sh_single(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _ps_single(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
