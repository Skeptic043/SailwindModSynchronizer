from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
import os
import subprocess

import pytest

from sailwind_mod_sync.http_util import HttpError
from sailwind_mod_sync.models import ReleaseAsset, RemoteRelease
from sailwind_mod_sync.paths import AppPaths
from sailwind_mod_sync.updater import (
    EXE_NAME,
    AppUpdate,
    download_and_stage_update,
    find_app_update,
    launch_apply_and_exit,
    pick_update_asset,
    update_check_due,
    utc_now_iso,
    zip_release_dir,
    _powershell_exe,
    _write_apply_script,
)


def test_pick_update_asset_prefers_windows_zip() -> None:
    assets = [
        ReleaseAsset("source.zip", "https://github.com/foxyv/SailwindModSynchronizer/archive/refs/tags/v0.2.0.zip"),
        ReleaseAsset(
            "SailwindModSynchronizer-0.2.0-windows.zip",
            "https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.2.0/SailwindModSynchronizer-0.2.0-windows.zip",
        ),
    ]
    chosen = pick_update_asset(assets, windows=True)
    assert chosen is not None
    assert chosen.name.endswith("windows.zip")


def test_pick_update_asset_ignores_source_only() -> None:
    assets = [ReleaseAsset("project-sources.zip", "https://github.com/example/src.zip")]
    assert pick_update_asset(assets, windows=True) is None


def test_update_check_due_empty_and_old() -> None:
    assert update_check_due("")
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(hours=25)).isoformat()
    assert not update_check_due(recent)
    assert update_check_due(old)


def test_find_app_update_returns_newer(monkeypatch) -> None:
    release = RemoteRelease(
        tag="v0.2.0",
        name="v0.2.0",
        html_url="https://github.com/foxyv/SailwindModSynchronizer/releases/tag/v0.2.0",
        body="Bug fixes",
        assets=[
            ReleaseAsset(
                "SailwindModSynchronizer-0.2.0-windows.zip",
                "https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.2.0/app.zip",
            )
        ],
    )
    monkeypatch.setattr("sailwind_mod_sync.updater.fetch_release", lambda *args, **kwargs: release)
    monkeypatch.setattr("sailwind_mod_sync.updater.is_frozen", lambda: True)
    monkeypatch.setattr("sailwind_mod_sync.updater.self_update_supported", lambda: True)
    monkeypatch.setattr("sailwind_mod_sync.updater.on_windows", lambda: True)
    update = find_app_update(MagicMock(), current_version="0.1.0")
    assert update is not None
    assert update.version == "0.2.0"
    assert update.installable
    assert "Bug fixes" in update.notes


def test_find_app_update_skips_same_and_skipped(monkeypatch) -> None:
    release = RemoteRelease(
        tag="v0.2.0",
        name="v0.2.0",
        assets=[
            ReleaseAsset(
                "SailwindModSynchronizer-0.2.0-windows.zip",
                "https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.2.0/app.zip",
            )
        ],
    )
    monkeypatch.setattr("sailwind_mod_sync.updater.fetch_release", lambda *args, **kwargs: release)
    assert find_app_update(MagicMock(), current_version="0.2.0") is None
    skipped = find_app_update(MagicMock(), current_version="0.1.0", skipped_version="0.2.0")
    assert skipped is None
    forced = find_app_update(
        MagicMock(), current_version="0.1.0", skipped_version="0.2.0", ignore_skipped=True
    )
    assert forced is not None


def test_find_app_update_handles_no_releases(monkeypatch) -> None:
    monkeypatch.setattr(
        "sailwind_mod_sync.updater.fetch_release",
        lambda *args, **kwargs: (_ for _ in ()).throw(HttpError("No GitHub releases for x", status_code=404)),
    )
    assert find_app_update(MagicMock()) is None


def test_find_app_update_rejects_untrusted_url(monkeypatch) -> None:
    release = RemoteRelease(
        tag="v0.2.0",
        name="v0.2.0",
        html_url="https://github.com/foxyv/SailwindModSynchronizer/releases/tag/v0.2.0",
        assets=[
            ReleaseAsset(
                "SailwindModSynchronizer-0.2.0-windows.zip",
                "https://evil.example/SailwindModSynchronizer-0.2.0-windows.zip",
            )
        ],
    )
    monkeypatch.setattr("sailwind_mod_sync.updater.fetch_release", lambda *args, **kwargs: release)
    monkeypatch.setattr("sailwind_mod_sync.updater.is_frozen", lambda: True)
    update = find_app_update(MagicMock(), current_version="0.1.0")
    assert update is not None
    assert update.download_url == ""
    assert not update.installable


def test_download_and_stage_update(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sailwind_mod_sync.updater.on_windows", lambda: True)
    payload_src = tmp_path / "payload"
    payload_src.mkdir()
    (payload_src / EXE_NAME).write_bytes(b"MZ")
    inner = payload_src / "_internal"
    inner.mkdir()
    (inner / "readme.txt").write_text("ok", encoding="utf-8")
    archive = tmp_path / "app.zip"
    zip_release_dir(payload_src, archive)
    http = MagicMock()

    def download(url, dest, progress=None):
        dest.write_bytes(archive.read_bytes())

    http.download.side_effect = download
    update = AppUpdate(
        version="0.2.0",
        version_raw="v0.2.0",
        tag="v0.2.0",
        html_url="https://github.com/foxyv/SailwindModSynchronizer/releases/tag/v0.2.0",
        asset_name="SailwindModSynchronizer-0.2.0-windows.zip",
        download_url="https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.2.0/app.zip",
        installable=True,
    )
    staged = download_and_stage_update(http, update, paths)
    assert (staged / EXE_NAME).is_file()
    assert (staged / "_internal" / "readme.txt").read_text(encoding="utf-8") == "ok"


def test_download_nested_payload_root(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sailwind_mod_sync.updater.on_windows", lambda: True)
    nested = tmp_path / "outer" / "SailwindModSynchronizer"
    nested.mkdir(parents=True)
    (nested / EXE_NAME).write_bytes(b"MZ")
    (nested / "readme.txt").write_text("nested", encoding="utf-8")
    archive = tmp_path / "app.zip"
    zip_release_dir(tmp_path / "outer", archive)
    http = MagicMock()
    http.download.side_effect = lambda url, dest, progress=None: dest.write_bytes(archive.read_bytes())
    update = AppUpdate(
        version="0.2.0",
        version_raw="v0.2.0",
        tag="v0.2.0",
        html_url="https://github.com/foxyv/SailwindModSynchronizer/releases/tag/v0.2.0",
        asset_name="SailwindModSynchronizer-0.2.0-windows.zip",
        download_url="https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.2.0/app.zip",
        installable=True,
    )
    staged = download_and_stage_update(http, update, paths)
    assert staged.name == "SailwindModSynchronizer"
    assert (staged / EXE_NAME).is_file()
    assert (staged / "readme.txt").read_text(encoding="utf-8") == "nested"


@pytest.mark.skipif(os.name != "nt", reason="The update apply script is PowerShell until Linux self-update lands (#22)")
def test_launch_apply_writes_script(tmp_path: Path, monkeypatch) -> None:
    payload = tmp_path / "payload" / "extracted"
    payload.mkdir(parents=True)
    (payload / EXE_NAME).write_bytes(b"MZ")
    dest = tmp_path / "install"
    dest.mkdir()
    (dest / EXE_NAME).write_bytes(b"old")
    monkeypatch.setattr("sailwind_mod_sync.updater.is_frozen", lambda: True)
    monkeypatch.setattr("sailwind_mod_sync.updater.install_dir", lambda: dest)
    popen = MagicMock()
    monkeypatch.setattr("sailwind_mod_sync.updater.subprocess.Popen", popen)
    script = launch_apply_and_exit(payload, pid=4242)
    text = script.read_text(encoding="utf-8")
    assert script.suffix == ".ps1"
    assert "$waitPid = 4242" in text
    assert "Get-Process" in text
    assert "robocopy" in text
    assert "find " not in text
    assert "cmd.exe" not in text
    assert EXE_NAME in text
    assert str(dest) in text
    args = popen.call_args.args[0]
    assert args[0].lower().endswith("powershell.exe")
    assert "-File" in args
    assert str(script) in args
    assert "cmd.exe" not in args


@pytest.mark.skipif(os.name != "nt", reason="The update apply script is PowerShell until Linux self-update lands (#22)")
def test_apply_script_copies_when_pid_already_gone(tmp_path: Path) -> None:
    src = tmp_path / "payload" / "extracted"
    src.mkdir(parents=True)
    (src / EXE_NAME).write_bytes(b"MZ-new")
    dest = tmp_path / "install"
    dest.mkdir()
    (dest / EXE_NAME).write_bytes(b"old")
    script = _write_apply_script(src, dest, dest / EXE_NAME, pid=9999999)
    text = script.read_text(encoding="utf-8")
    script.write_text(text.replace("Start-Process -FilePath $exe -WorkingDirectory $dst", ""), encoding="utf-8")
    completed = subprocess.run(
        [_powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode < 8, completed.stderr
    assert (dest / EXE_NAME).read_bytes() == b"MZ-new"
    log_text = (src.parent.parent / "apply.log").read_text(encoding="utf-8")
    assert "Copying files" in log_text
    assert "robocopy failed" not in log_text


def test_launch_apply_requires_frozen(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sailwind_mod_sync.updater.is_frozen", lambda: False)
    with pytest.raises(RuntimeError, match="installed"):
        launch_apply_and_exit(tmp_path)


def test_utc_now_iso_is_parseable() -> None:
    stamp = utc_now_iso()
    datetime.fromisoformat(stamp)


RELEASE_ASSETS = [
    ReleaseAsset(
        "SailwindModSynchronizer-0.6.0-windows.zip",
        "https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.6.0/SailwindModSynchronizer-0.6.0-windows.zip",
    ),
    ReleaseAsset(
        "SailwindModSynchronizer-0.6.0-linux-x86_64.tar.gz",
        "https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.6.0/SailwindModSynchronizer-0.6.0-linux-x86_64.tar.gz",
    ),
]


def test_pick_update_asset_picks_each_platforms_download() -> None:
    assert pick_update_asset(RELEASE_ASSETS, windows=True).name.endswith("-windows.zip")
    assert pick_update_asset(RELEASE_ASSETS, windows=False).name.endswith("-linux-x86_64.tar.gz")


def test_pick_update_asset_never_crosses_platforms() -> None:
    windows_only = [RELEASE_ASSETS[0]]
    linux_only = [RELEASE_ASSETS[1]]
    assert pick_update_asset(windows_only, windows=False) is None
    assert pick_update_asset(linux_only, windows=True) is None
    # A zip that says linux is never a Windows update.
    assert pick_update_asset([ReleaseAsset("SailwindModSynchronizer-linux.zip", RELEASE_ASSETS[0].download_url)], windows=True) is None


def test_linux_update_is_offered_but_not_installed_automatically(monkeypatch) -> None:
    release = RemoteRelease(
        tag="v0.6.0",
        name="Sailwind Mod Synchronizer 0.6.0",
        html_url="https://github.com/foxyv/SailwindModSynchronizer/releases/tag/v0.6.0",
        body="Linux build",
        assets=RELEASE_ASSETS,
    )
    monkeypatch.setattr("sailwind_mod_sync.updater.fetch_release", lambda *args, **kwargs: release)
    monkeypatch.setattr("sailwind_mod_sync.updater.is_frozen", lambda: True)
    monkeypatch.setattr("sailwind_mod_sync.updater.self_update_supported", lambda: False)
    monkeypatch.setattr("sailwind_mod_sync.updater.on_windows", lambda: False)
    update = find_app_update(MagicMock(), current_version="0.5.0")
    assert update is not None and update.version == "0.6.0"
    assert update.asset_name.endswith("-linux-x86_64.tar.gz")
    assert not update.installable


def _linux_update(name: str) -> AppUpdate:
    return AppUpdate(
        version="0.6.0",
        version_raw="v0.6.0",
        tag="v0.6.0",
        html_url="https://github.com/foxyv/SailwindModSynchronizer/releases/tag/v0.6.0",
        asset_name=name,
        download_url=f"https://github.com/foxyv/SailwindModSynchronizer/releases/download/v0.6.0/{name}",
        installable=True,
    )


def _linux_build(root: Path) -> Path:
    build = root / "SailwindModSynchronizer"
    (build / "_internal").mkdir(parents=True)
    (build / "SailwindModSynchronizer").write_bytes(b"\x7fELF")
    (build / "sailwind-mod-sync").write_text("#!/bin/sh\n", encoding="utf-8")
    (build / "_internal" / "readme.txt").write_text("new", encoding="utf-8")
    return build


def test_self_update_supported_on_windows_and_linux(monkeypatch) -> None:
    from sailwind_mod_sync import updater

    monkeypatch.setattr(updater, "on_windows", lambda: False)
    monkeypatch.setattr(updater.sys, "platform", "linux")
    assert updater.self_update_supported()
    assert updater.app_binary_name() == "SailwindModSynchronizer"
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    assert not updater.self_update_supported()
    monkeypatch.setattr(updater, "on_windows", lambda: True)
    assert updater.self_update_supported()
    assert updater.app_binary_name() == "SailwindModSynchronizer.exe"


def test_download_and_stage_linux_tarball(paths: AppPaths, tmp_path: Path, monkeypatch) -> None:
    import tarfile

    monkeypatch.setattr("sailwind_mod_sync.updater.on_windows", lambda: False)
    build = _linux_build(tmp_path / "src")
    archive = tmp_path / "SailwindModSynchronizer-0.6.0-linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(build, arcname=build.name)
    http = MagicMock()
    http.download.side_effect = lambda url, dest, progress=None: dest.write_bytes(archive.read_bytes())
    staged = download_and_stage_update(http, _linux_update(archive.name), paths)
    assert staged.name == "SailwindModSynchronizer"
    assert (staged / "SailwindModSynchronizer").is_file()
    assert (staged / "_internal" / "readme.txt").read_text(encoding="utf-8") == "new"


def test_safe_extract_tar_refuses_escaping_paths(tmp_path: Path) -> None:
    import io
    import tarfile

    from sailwind_mod_sync.library.extract import ExtractError
    from sailwind_mod_sync.updater import safe_extract_tar

    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"pwned"
        info = tarfile.TarInfo("../escaped.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises((ExtractError, tarfile.TarError)):
        safe_extract_tar(archive, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_linux_apply_script_text(tmp_path: Path) -> None:
    from sailwind_mod_sync.updater import write_linux_apply_script

    src = tmp_path / "updates" / "payload" / "extracted"
    src.mkdir(parents=True)
    script = write_linux_apply_script(src, tmp_path / "it's app", tmp_path / "it's app" / "sailwind-mod-sync", 4242)
    text = script.read_bytes().decode("utf-8")
    assert text.startswith("#!/bin/sh\n") and "\r\n" not in text
    assert "pid=4242" in text
    assert "'\\''s app" in text  # single quote in the path is escaped for sh
    assert 'cp -a "$src/." "$dst/"' in text
    assert 'exec "$start"' in text and "nohup" not in text
    assert script.name == "apply_update.sh"


@pytest.mark.skipif(os.name == "nt", reason="Runs the generated shell script")
def test_linux_apply_script_waits_copies_and_restarts(tmp_path: Path) -> None:
    import time

    from sailwind_mod_sync.updater import write_linux_apply_script

    staging = tmp_path / "updates" / "payload" / "extracted"
    src = _linux_build(staging)
    dest = tmp_path / "install"
    (dest / "_internal").mkdir(parents=True)
    (dest / "_internal" / "readme.txt").write_text("old", encoding="utf-8")
    marker = tmp_path / "restarted"
    start = dest / "sailwind-mod-sync"
    # The "new" launcher just records that it was started.
    (src / "sailwind-mod-sync").write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    running = subprocess.Popen(["sleep", "2"])
    script = write_linux_apply_script(src, dest, start, running.pid)
    started = time.monotonic()
    applying = subprocess.Popen(["/bin/sh", str(script)])
    lock = dest / ".update-in-progress"
    for _ in range(20):
        if lock.exists():
            break
        time.sleep(0.05)
    assert lock.read_text(encoding="utf-8").strip() == str(applying.pid)  # held while waiting
    assert applying.wait(30) == 0
    assert not lock.exists()  # released once the copy is done
    assert time.monotonic() - started >= 1.5  # waited for the app to exit
    running.wait(5)
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.1)
    assert (dest / "_internal" / "readme.txt").read_text(encoding="utf-8") == "new"
    assert os.access(dest / "sailwind-mod-sync", os.X_OK)
    assert marker.exists()
    log = (tmp_path / "updates" / "apply.log").read_text(encoding="utf-8")
    assert "Copying files" in log and "Done; starting app" in log


def test_game_mode_detection_and_restart_choice(monkeypatch) -> None:
    from sailwind_mod_sync import updater

    assert updater.in_game_mode({"XDG_CURRENT_DESKTOP": "gamescope"})
    assert updater.in_game_mode({"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"})
    assert not updater.in_game_mode({"XDG_CURRENT_DESKTOP": "KDE"})
    assert not updater.in_game_mode({})
    monkeypatch.setattr(updater, "on_windows", lambda: False)
    monkeypatch.setattr(updater, "in_game_mode", lambda env=None: True)
    assert not updater.restarts_after_update()
    monkeypatch.setattr(updater, "in_game_mode", lambda env=None: False)
    assert updater.restarts_after_update()
    monkeypatch.setattr(updater, "on_windows", lambda: True)
    monkeypatch.setattr(updater, "in_game_mode", lambda env=None: True)
    assert updater.restarts_after_update()


def test_linux_apply_script_without_restart(tmp_path: Path) -> None:
    from sailwind_mod_sync.updater import write_linux_apply_script

    src = tmp_path / "updates" / "payload" / "extracted" / "SailwindModSynchronizer"
    src.mkdir(parents=True)
    script = write_linux_apply_script(src, tmp_path / "app", tmp_path / "app" / "sailwind-mod-sync", 1, restart=False)
    text = script.read_text(encoding="utf-8")
    assert 'cp -a "$src/." "$dst/"' in text
    assert "exec" not in text
    assert "not restarting" in text


def test_linux_apply_script_releases_the_lock_before_restarting(tmp_path: Path) -> None:
    from sailwind_mod_sync.updater import UPDATE_LOCK_NAME, write_linux_apply_script

    src = tmp_path / "updates" / "payload" / "extracted" / "SailwindModSynchronizer"
    src.mkdir(parents=True)
    lines = write_linux_apply_script(src, tmp_path / "app", tmp_path / "app" / "sailwind-mod-sync", 1).read_text(
        encoding="utf-8"
    ).splitlines()
    take = lines.index('echo $$ > "$lock"')
    copy = next(i for i, line in enumerate(lines) if line.startswith('if ! cp -a'))
    release = lines.index('rm -f "$lock"')
    restart = next(i for i, line in enumerate(lines) if line.startswith('exec "$start"'))
    assert take < copy < release < restart
    assert f'lock="$dst/{UPDATE_LOCK_NAME}"' in lines
    assert "trap 'rm -f \"$lock\"' EXIT" in lines  # also released if the copy fails
