from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_build():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build.py"
    spec = importlib.util.spec_from_file_location("sailwind_mod_sync_build", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_build_is_incremental() -> None:
    build = _load_build()
    args = build.parse_args([])
    assert args.release is False
    freeze = build.pyinstaller_args(windowed=True, clean=False)
    assert "--clean" not in freeze
    assert "--noupx" in freeze
    assert "--noconfirm" in freeze


def test_release_build_cleans_cache() -> None:
    build = _load_build()
    args = build.parse_args(["--release", "--skip-shortcut"])
    assert args.release is True
    freeze = build.pyinstaller_args(windowed=True, clean=True)
    assert "--clean" in freeze
    assert "--noupx" in freeze
    assert freeze.count("--clean") == 1


def test_release_implies_sign() -> None:
    build = _load_build()
    args = build.parse_args(["--release", "--skip-shortcut"])
    assert args.sign is True
    skipped = build.parse_args(["--release", "--skip-sign"])
    assert skipped.sign is False
    incremental = build.parse_args([])
    assert incremental.sign is False
    explicit = build.parse_args(["--sign"])
    assert explicit.sign is True


def test_sign_and_skip_sign_conflict() -> None:
    build = _load_build()
    try:
        build.parse_args(["--sign", "--skip-sign"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("expected parse_args to reject --sign with --skip-sign")


def test_load_signing_metadata(tmp_path: Path) -> None:
    build = _load_build()
    path = tmp_path / "metadata.json"
    try:
        build.load_signing_metadata(path)
        raise AssertionError("expected missing metadata to fail")
    except SystemExit:
        pass
    path.write_text('{"Endpoint": "https://eus.codesigning.azure.net"}', encoding="utf-8")
    try:
        build.load_signing_metadata(path)
        raise AssertionError("expected incomplete metadata to fail")
    except SystemExit:
        pass
    path.write_text(
        '{"Endpoint": "https://eus.codesigning.azure.net",'
        '"CodeSigningAccountName": "acct",'
        '"CertificateProfileName": "prof"}',
        encoding="utf-8",
    )
    data = build.load_signing_metadata(path)
    assert data["CodeSigningAccountName"] == "acct"


def test_kit_version_and_signtool_discovery(tmp_path: Path, monkeypatch) -> None:
    build = _load_build()
    assert build.kit_version_tuple("10.0.26100.0") == (10, 0, 26100)
    assert build.kit_version_tuple("bin") is None
    kits = tmp_path / "Windows Kits" / "10" / "bin"
    old = kits / "10.0.20348.0" / "x64"
    old.mkdir(parents=True)
    (old / "signtool.exe").write_bytes(b"MZ")
    newest = kits / "10.0.26100.0" / "x64"
    newest.mkdir(parents=True)
    (newest / "signtool.exe").write_bytes(b"MZ")
    mid = kits / "10.0.22621.0" / "x64"
    mid.mkdir(parents=True)
    (mid / "signtool.exe").write_bytes(b"MZ")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.delenv("SMS_SIGNTOOL", raising=False)
    found = build.find_signtool()
    assert found == newest / "signtool.exe"


def test_binaries_to_sign(tmp_path: Path) -> None:
    build = _load_build()
    exe = tmp_path / "SailwindModSynchronizer.exe"
    exe.write_bytes(b"MZ")
    (tmp_path / "python3.dll").write_bytes(b"MZ")
    (tmp_path / "readme.txt").write_text("no", encoding="utf-8")
    nested = tmp_path / "PySide6"
    nested.mkdir()
    (nested / "Qt6Core.dll").write_bytes(b"MZ")
    names = {path.name for path in build.binaries_to_sign(tmp_path)}
    assert names == {"SailwindModSynchronizer.exe", "python3.dll", "Qt6Core.dll"}


def test_sign_files_batches(tmp_path: Path, monkeypatch) -> None:
    build = _load_build()
    calls: list[list[str]] = []

    def fake_call(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return 0

    monkeypatch.setattr(build.subprocess, "check_call", fake_call)
    files = [tmp_path / f"f{i}.dll" for i in range(20)]
    for path in files:
        path.write_bytes(b"MZ")
    signtool = tmp_path / "signtool.exe"
    dlib = tmp_path / "Azure.CodeSigning.Dlib.dll"
    metadata = tmp_path / "metadata.json"
    build.sign_files(signtool, dlib, metadata, files)
    assert len(calls) == 2
    assert calls[0][0] == str(signtool)
    assert "/dlib" in calls[0]
    assert str(dlib) in calls[0]
    assert str(metadata) in calls[0]
    assert str(files[0]) in calls[0]
    assert str(files[-1]) in calls[1]


def test_clear_readonly_makes_files_and_folders_writable(tmp_path: Path) -> None:
    import os
    import stat

    build = _load_build()
    folder = tmp_path / "dist" / "plugins"
    folder.mkdir(parents=True)
    dll = folder / "qico.dll"
    dll.write_bytes(b"MZ")
    for path in (dll, folder):
        os.chmod(path, stat.S_IREAD)
    assert build.clear_readonly(tmp_path / "dist", tmp_path / "missing") == 2
    assert os.access(dll, os.W_OK)
    dll.unlink()
    folder.rmdir()


def test_exe_in_use_is_false_for_missing_or_idle_exe(tmp_path: Path) -> None:
    build = _load_build()
    exe = tmp_path / "App.exe"
    assert not build.exe_in_use(exe)
    exe.write_bytes(b"MZ")
    assert not build.exe_in_use(exe)
