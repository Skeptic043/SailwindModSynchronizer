"""Build a Windows executable and a desktop shortcut.

Usage (from the repo root, with the project venv active):

    python scripts/build.py
    python scripts/build.py --release
    python scripts/build.py --skip-shortcut
    python scripts/build.py --console
    python scripts/build.py --release --skip-sign

Default is an incremental freeze: reuse the PyInstaller cache, skip UPX, and skip
the GitHub zip. Pass --release for a clean freeze, Azure Authenticode signing, and
a versioned zip. Use --skip-sign for an unsigned zip.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "assets"
PNG_ICON = ASSETS / "icon.png"
ICO_ICON = ASSETS / "icon.ico"
CHANGELOG = ASSETS / "CHANGELOG.md"
EXE_NAME = "SailwindModSynchronizer"
SHORTCUT_NAME = "Sailwind Mod Synchronizer.lnk"
DIST_DIR = REPO_ROOT / "dist" / EXE_NAME
BUILD_DIR = REPO_ROOT / "build"
LINUX_ARCHIVE_SUFFIX = "linux-x86_64.tar.gz"
LINUX_LAUNCHER = "sailwind-mod-sync"
LINUX_DESKTOP_INSTALLER = "install-desktop-entry.sh"
SIGN_EXTENSIONS = {".exe", ".dll", ".pyd"}
SIGN_BATCH = 16
MIN_SIGNTOOL_VERSION = (10, 0, 22621)
UNSUPPORTED_SIGNTOOL_VERSIONS = {(10, 0, 20348)}
TIMESTAMP_URL = "http://timestamp.acs.microsoft.com"

QT_EXCLUDES = [
    "tkinter",
    "unittest",
    "pydoc",
    "PIL",
    "PyInstaller",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile Sailwind Mod Synchronizer to an executable.")
    parser.add_argument(
        "--release",
        action="store_true",
        help="Clean freeze and write a versioned zip for a GitHub release.",
    )
    parser.add_argument("--skip-shortcut", action="store_true", help="Do not create a Desktop shortcut.")
    parser.add_argument("--console", action="store_true", help="Show a console window (useful for debugging).")
    parser.add_argument(
        "--sign",
        action="store_true",
        help="Sign freeze output with Azure Artifact Signing (implied by --release).",
    )
    parser.add_argument(
        "--skip-sign",
        action="store_true",
        help="Do not Authenticode-sign the freeze output.",
    )
    args = parser.parse_args(argv)
    if args.sign and args.skip_sign:
        parser.error("use only one of --sign and --skip-sign")
    if args.release and not args.skip_sign:
        args.sign = True
    if os.name != "nt":
        # Authenticode signing is Windows-only.
        args.sign = False
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    os.chdir(REPO_ROOT)
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir in sys.path:
        sys.path.remove(scripts_dir)
    if not PNG_ICON.is_file():
        raise SystemExit(f"Missing icon PNG: {PNG_ICON}")

    _ensure_build_deps()
    write_ico(PNG_ICON, ICO_ICON)
    if os.name != "nt":
        return _main_linux(args)
    if exe_in_use(DIST_DIR / f"{EXE_NAME}.exe"):
        raise SystemExit(f"{EXE_NAME}.exe is running from {DIST_DIR}. Close it and build again.")
    clear_readonly(BUILD_DIR, DIST_DIR)
    _run_pyinstaller(windowed=not args.console, clean=args.release)

    exe = DIST_DIR / f"{EXE_NAME}.exe"
    if not exe.is_file():
        raise SystemExit(f"PyInstaller did not produce {exe}")

    shutil.copy2(ICO_ICON, DIST_DIR / "icon.ico")
    print(f"Built {exe}")

    if args.sign:
        sign_dist(DIST_DIR, exe)

    if args.release:
        version = _app_version()
        archive = zip_dist(DIST_DIR, REPO_ROOT / "dist" / f"{EXE_NAME}-{version}-windows.zip")
        print(f"Release zip {archive}")
        print("Upload that zip to a GitHub release so the app can auto-update.")
    else:
        print("Incremental freeze (cache reused, no GitHub zip).")
        print("Use --release for a clean build and a versioned zip.")

    if not args.skip_shortcut:
        shortcut = create_desktop_shortcut(exe, DIST_DIR / "icon.ico")
        print(f"Desktop shortcut: {shortcut}")
    return 0


def _main_linux(args: argparse.Namespace) -> int:
    _run_pyinstaller(windowed=not args.console, clean=args.release)
    exe = DIST_DIR / EXE_NAME
    if not exe.is_file():
        raise SystemExit(f"PyInstaller did not produce {exe}")
    write_linux_extras(DIST_DIR)
    print(f"Built {exe}")
    if args.release:
        version = _app_version()
        archive = tar_dist(DIST_DIR, REPO_ROOT / "dist" / f"{EXE_NAME}-{version}-{LINUX_ARCHIVE_SUFFIX}")
        print(f"Release archive {archive}")
        print("Upload that archive to the GitHub release next to the Windows zip.")
    else:
        print("Incremental freeze (cache reused, no release archive).")
        print("Use --release for a clean build and a versioned archive.")
    return 0


LINUX_LAUNCHER_SCRIPT = """#!/bin/sh
# Starts Sailwind Mod Synchronizer. Use this rather than the binary directly:
# Steam sets LD_LIBRARY_PATH/LD_PRELOAD for its own runtime when it starts
# non-Steam games, which makes Qt load the wrong libraries and crash.
here=$(dirname "$(readlink -f "$0")")
# If an update is still being copied, wait for it (at most a minute), then start the
# freshly copied launcher. The whole block is read before it runs, so the update
# replacing this file meanwhile is fine.
if [ -f "$here/.update-in-progress" ]; then
  updater=$(cat "$here/.update-in-progress" 2>/dev/null)
  waited=0
  while [ -f "$here/.update-in-progress" ] && [ -n "$updater" ] && kill -0 "$updater" 2>/dev/null && [ "$waited" -lt 60 ]; do
    sleep 1
    waited=$((waited + 1))
  done
  rm -f "$here/.update-in-progress"
  exec "$here/sailwind-mod-sync" "$@"
fi
unset LD_LIBRARY_PATH LD_PRELOAD
exec "$here/SailwindModSynchronizer" "$@"
"""

LINUX_DESKTOP_INSTALLER_SCRIPT = """#!/bin/sh
# Adds Sailwind Mod Synchronizer to your desktop's application menu.
# Run it again if you move this folder.
set -e
here=$(dirname "$(readlink -f "$0")")
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$apps"
cat > "$apps/sailwind-mod-sync.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Sailwind Mod Synchronizer
Comment=Manage and launch Sailwind ModPacks
Exec="$here/sailwind-mod-sync"
Icon=$here/icon.png
Terminal=false
Categories=Game;Utility;
EOF
echo "Added Sailwind Mod Synchronizer to your applications menu."
"""


def write_linux_extras(dist_dir: Path) -> list[Path]:
    """Launcher, desktop-entry installer and icon next to the Linux binary."""
    written = []
    for name, text in (
        (LINUX_LAUNCHER, LINUX_LAUNCHER_SCRIPT),
        (LINUX_DESKTOP_INSTALLER, LINUX_DESKTOP_INSTALLER_SCRIPT),
    ):
        path = dist_dir / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)
        written.append(path)
    icon = dist_dir / "icon.png"
    shutil.copy2(PNG_ICON, icon)
    written.append(icon)
    return written


def tar_dist(source: Path, dest: Path) -> Path:
    """Gzipped tarball with a top-level folder, keeping executable bits."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with tarfile.open(dest, "w:gz") as archive:
        archive.add(source, arcname=source.name)
    return dest


def _ensure_build_deps() -> None:
    try:
        import PIL  # noqa: F401
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller>=6.0", "pillow>=10.0"]
        )


def write_ico(png_path: Path, ico_path: Path) -> Path:
    from PIL import Image

    image = Image.open(png_path).convert("RGBA")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(ico_path, format="ICO", sizes=sizes)
    return ico_path


def _app_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version"):
            _, _, value = stripped.partition("=")
            return value.strip().strip('"').strip("'")
    return "0.0.0"


def zip_dist(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with ZipFile(dest, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    return dest


def default_signing_metadata_path() -> Path:
    override = os.environ.get("SMS_SIGNING_METADATA", "").strip()
    if override:
        return Path(override).expanduser()
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(profile) / "sms-signing" / "metadata.json"


def load_signing_metadata(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(
            "Azure Artifact Signing metadata not found.\n"
            f"  Expected: {path}\n"
            "Create that JSON file (see scripts/signing.metadata.example.json), or set "
            "SMS_SIGNING_METADATA, or pass --skip-sign for an unsigned zip."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid signing metadata JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Signing metadata must be a JSON object: {path}")
    required = ("Endpoint", "CodeSigningAccountName", "CertificateProfileName")
    missing = [key for key in required if not str(data.get(key) or "").strip()]
    if missing:
        raise SystemExit(f"Signing metadata at {path} is missing: {', '.join(missing)}")
    return data


def kit_version_tuple(folder_name: str) -> tuple[int, int, int] | None:
    parts = folder_name.split(".")
    if len(parts) < 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def find_signtool() -> Path:
    override = os.environ.get("SMS_SIGNTOOL", "").strip()
    if override:
        path = Path(override)
        if not path.is_file():
            raise SystemExit(f"SMS_SIGNTOOL is set but not a file: {path}")
        return path
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    found: list[tuple[tuple[int, int, int], Path]] = []
    if kits.is_dir():
        for child in kits.iterdir():
            version = kit_version_tuple(child.name)
            if version is None:
                continue
            if version in UNSUPPORTED_SIGNTOOL_VERSIONS or version < MIN_SIGNTOOL_VERSION:
                continue
            candidate = child / "x64" / "signtool.exe"
            if candidate.is_file():
                found.append((version, candidate))
    if found:
        found.sort()
        return found[-1][1]
    which = shutil.which("signtool")
    if which:
        return Path(which)
    raise SystemExit(
        "signtool.exe not found. Install the Windows SDK (10.0.22621 or later, not 10.0.20348) "
        "or Artifact Signing Client Tools, or set SMS_SIGNTOOL."
    )


def _dlib_search_roots() -> list[Path]:
    roots: list[Path] = []
    override = os.environ.get("SMS_SIGNING_DLIB", "").strip()
    if override:
        roots.append(Path(override))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    local_app = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    roots.extend(
        [
            program_files / "Microsoft Artifact Signing Client Tools",
            program_files / "Azure Artifact Signing Client Tools",
            program_files / "Microsoft Azure Artifact Signing Client Tools",
            program_files_x86 / "Microsoft Artifact Signing Client Tools",
            local_app / "Microsoft" / "MicrosoftArtifactSigningClientTools",
            Path.home() / ".nuget" / "packages" / "microsoft.artifactsigning.client",
            Path.home() / ".nuget" / "packages" / "microsoft.trusted.signing.client",
            local_app / "Microsoft" / "WinGet" / "Packages",
        ]
    )
    return roots


def find_signing_dlib() -> Path:
    override = os.environ.get("SMS_SIGNING_DLIB", "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path
        nested = path / "x64" / "Azure.CodeSigning.Dlib.dll"
        if nested.is_file():
            return nested
        nested = path / "Azure.CodeSigning.Dlib.dll"
        if nested.is_file():
            return nested
    for root in _dlib_search_roots():
        if not root.exists():
            continue
        if root.is_file() and root.name.lower() == "azure.codesigning.dlib.dll":
            return root
        for relative in (
            Path("Azure.CodeSigning.Dlib.dll"),
            Path("bin") / "x64" / "Azure.CodeSigning.Dlib.dll",
            Path("x64") / "Azure.CodeSigning.Dlib.dll",
        ):
            direct = root / relative
            if direct.is_file():
                return direct
        if root.is_dir():
            children = [root]
            if root.name.lower() == "packages" and "winget" in str(root).lower():
                children = [
                    child
                    for child in root.iterdir()
                    if child.is_dir()
                    and ("artifactsigning" in child.name.lower() or "trusted.signing" in child.name.lower())
                ]
            for child in children:
                matches = sorted(
                    path
                    for path in child.rglob("Azure.CodeSigning.Dlib.dll")
                    if path.name.lower() == "azure.codesigning.dlib.dll"
                )
                if matches:
                    return matches[-1]
    raise SystemExit(
        "Azure.CodeSigning.Dlib.dll not found. Install with:\n"
        "  winget install -e --id Microsoft.Azure.ArtifactSigningClientTools\n"
        "or set SMS_SIGNING_DLIB to the x64 dlib path."
    )


def binaries_to_sign(dist_dir: Path) -> list[Path]:
    files = [
        path
        for path in sorted(dist_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in SIGN_EXTENSIONS
    ]
    if not files:
        raise SystemExit(f"No .exe/.dll/.pyd files to sign in {dist_dir}")
    return files


def sign_files(signtool: Path, dlib: Path, metadata: Path, files: list[Path]) -> None:
    for index in range(0, len(files), SIGN_BATCH):
        batch = files[index : index + SIGN_BATCH]
        cmd = [
            str(signtool),
            "sign",
            "/fd",
            "SHA256",
            "/tr",
            TIMESTAMP_URL,
            "/td",
            "SHA256",
            "/dlib",
            str(dlib),
            "/dmdf",
            str(metadata),
            *[str(path) for path in batch],
        ]
        print(f"Signing {len(batch)} file(s) ({index + 1}-{index + len(batch)} of {len(files)})…")
        subprocess.check_call(cmd)


def sign_dist(dist_dir: Path, exe: Path) -> None:
    if os.name != "nt":
        raise SystemExit("Authenticode signing is only supported on Windows.")
    metadata = default_signing_metadata_path()
    load_signing_metadata(metadata)
    signtool = find_signtool()
    dlib = find_signing_dlib()
    files = binaries_to_sign(dist_dir)
    print(f"Signing {len(files)} binaries with Azure Artifact Signing")
    print(f"  metadata: {metadata}")
    print(f"  signtool: {signtool}")
    print(f"  dlib:     {dlib}")
    print("If a browser or az login prompt appears, finish it with the account that has")
    print("the Artifact Signing Certificate Profile Signer role.")
    try:
        sign_files(signtool, dlib, metadata, files)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"signtool failed with exit code {exc.returncode}.\n"
            "Check az login, the Certificate Profile Signer role, and that Endpoint "
            "in metadata.json matches the Artifact Signing account region."
        ) from exc
    if not exe.is_file():
        raise SystemExit(f"Signed exe missing: {exe}")
    print(f"Signed {exe}")


def pyinstaller_args(*, windowed: bool, clean: bool) -> list[str]:
    add_data_sep = ";" if os.name == "nt" else ":"
    args = ["--noconfirm", "--noupx"]
    if clean:
        args.append("--clean")
    args.extend(
        [
            "--windowed" if windowed else "--console",
            "--name",
            EXE_NAME,
            "--icon",
            str(ICO_ICON),
            "--paths",
            str(REPO_ROOT / "src"),
            "--collect-submodules",
            "sailwind_mod_sync",
            "--add-data",
            f"{ICO_ICON}{add_data_sep}assets",
            "--add-data",
            f"{PNG_ICON}{add_data_sep}assets",
            "--add-data",
            f"{CHANGELOG}{add_data_sep}assets",
            str(REPO_ROOT / "src" / "sailwind_mod_sync" / "__main__.py"),
        ]
    )
    for module in QT_EXCLUDES:
        args.extend(["--exclude-module", module])
    return args


def exe_in_use(exe: Path) -> bool:
    """True when the dist exe is running, so PyInstaller would delete files out from under it.

    Asks Windows for processes started from that exact path, and also tries to open
    the exe for writing (Windows refuses while it runs). Either one is enough.
    """
    if not exe.is_file():
        return False
    if os.name == "nt" and exe_running_processes(exe):
        return True
    try:
        mode = exe.stat().st_mode
        if not mode & stat.S_IWRITE:
            os.chmod(exe, mode | stat.S_IWRITE)
        with exe.open("r+b"):
            return False
    except PermissionError:
        return True


def exe_running_processes(exe: Path) -> list[int]:
    """PIDs of processes whose image is exactly this exe (Windows only; [] when unknown)."""
    target = str(exe.resolve()).lower()
    script = (
        "Get-Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path } | ForEach-Object { \"$($_.Id)`t$($_.Path)\" }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in result.stdout.splitlines():
        pid, _, path = line.partition("\t")
        if path.strip().lower() == target and pid.strip().isdigit():
            pids.append(int(pid))
    return pids


def clear_readonly(*roots: Path) -> int:
    """Clear read-only flags OneDrive leaves on synced folders, which make PyInstaller's cleanup fail."""
    cleared = 0
    for root in roots:
        if not root.exists():
            continue
        for path in [root, *root.rglob("*")]:
            try:
                mode = path.stat().st_mode
            except OSError:
                continue
            if not mode & stat.S_IWRITE:
                try:
                    os.chmod(path, mode | stat.S_IWRITE)
                    cleared += 1
                except OSError as exc:
                    print(f"Could not clear read-only flag on {path}: {exc}")
    if cleared:
        print(f"Cleared read-only flags on {cleared} item(s)")
    return cleared


def _run_pyinstaller(*, windowed: bool, clean: bool) -> None:
    import PyInstaller.__main__

    PyInstaller.__main__.run(pyinstaller_args(windowed=windowed, clean=clean))


def desktop_dir() -> Path:
    home = Path.home()
    candidates = [
        Path(os.environ.get("USERPROFILE", str(home))) / "Desktop",
        home / "OneDrive" / "Desktop",
        home / "Desktop",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[0]


def create_desktop_shortcut(exe: Path, icon: Path) -> Path:
    desktop = desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    shortcut = desktop / SHORTCUT_NAME
    exe = exe.resolve()
    icon = icon.resolve()
    working = str(exe.parent)
    ps = f"""
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut({_ps_quote(str(shortcut))})
$shortcut.TargetPath = {_ps_quote(str(exe))}
$shortcut.WorkingDirectory = {_ps_quote(working)}
$shortcut.WindowStyle = 1
$shortcut.Description = 'Sailwind Mod Synchronizer'
$shortcut.IconLocation = {_ps_quote(f'{icon},0')}
$shortcut.Save()
"""
    subprocess.check_call(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]
    )
    if not shortcut.exists():
        raise SystemExit(f"Failed to create shortcut at {shortcut}")
    return shortcut


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
