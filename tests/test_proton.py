from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from sailwind_mod_sync.game import proton

LOCALCONFIG = r'''"UserLocalConfigStore"
{
	// comment lines are ignored
	"Software"
	{
		"Valve"
		{
			"Steam"
			{
				"apps"
				{
					"1764530"
					{
						"LastPlayed"		"1780000000"
						"LaunchOptions"		"WINEDLLOVERRIDES=\"winhttp=n,b\" %command%"
					}
					"620"
					{
						"LaunchOptions"		"-novid"
					}
				}
			}
		}
	}
}
'''


def test_parse_vdf_handles_nesting_escapes_and_comments() -> None:
    data = proton.parse_vdf(LOCALCONFIG)
    app = data["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]["1764530"]
    assert app["LaunchOptions"] == 'WINEDLLOVERRIDES="winhttp=n,b" %command%'
    assert app["LastPlayed"] == "1780000000"


def test_launch_options_from_localconfig() -> None:
    assert proton.launch_options_from_localconfig(LOCALCONFIG) == 'WINEDLLOVERRIDES="winhttp=n,b" %command%'
    assert proton.launch_options_from_localconfig(LOCALCONFIG, app_id="620") == "-novid"
    assert proton.launch_options_from_localconfig(LOCALCONFIG, app_id="999") is None
    no_options = LOCALCONFIG.replace('"LaunchOptions"\t\t"WINEDLLOVERRIDES=\\"winhttp=n,b\\" %command%"', "")
    assert proton.launch_options_from_localconfig(no_options) == ""


def test_launch_options_lookup_ignores_key_case() -> None:
    text = LOCALCONFIG.replace('"apps"', '"Apps"').replace('"Software"', '"software"')
    assert proton.launch_options_from_localconfig(text) == 'WINEDLLOVERRIDES="winhttp=n,b" %command%'


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ('WINEDLLOVERRIDES="winhttp=n,b" %command%', True),
        ("WINEDLLOVERRIDES=winhttp=n,b %command%", True),
        ("WINEDLLOVERRIDES='dxgi=n;winhttp=native,builtin' %command%", True),
        ('WINEDLLOVERRIDES="dxgi=n" %command%', False),
        ('WINEDLLOVERRIDES="winhttp=b" %command%', False),
        ("%command% -skipintro", False),
        ("", False),
    ],
)
def test_has_winhttp_override(options: str, expected: bool) -> None:
    assert proton.has_winhttp_override(options) is expected


@pytest.mark.parametrize(
    ("existing", "suggested"),
    [
        ("", 'WINEDLLOVERRIDES="winhttp=n,b" %command%'),
        ("%command% -skipintro", 'WINEDLLOVERRIDES="winhttp=n,b" %command% -skipintro'),
        ("-skipintro", 'WINEDLLOVERRIDES="winhttp=n,b" %command% -skipintro'),
        ('WINEDLLOVERRIDES="dxgi=n" %command%', 'WINEDLLOVERRIDES="dxgi=n;winhttp=n,b" %command%'),
        ("DXVK_HUD=1 WINEDLLOVERRIDES=dxgi=n %command%", 'DXVK_HUD=1 WINEDLLOVERRIDES="dxgi=n;winhttp=n,b" %command%'),
        ('WINEDLLOVERRIDES="winhttp=n,b" %command%', 'WINEDLLOVERRIDES="winhttp=n,b" %command%'),
    ],
)
def test_suggested_launch_options_keeps_existing_options(existing: str, suggested: str) -> None:
    result = proton.suggested_launch_options(existing)
    assert result == suggested
    assert proton.has_winhttp_override(result)


def _write_localconfig(steam: Path, user: str, text: str, mtime: float) -> Path:
    path = steam / "userdata" / user / "config" / "localconfig.vdf"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_current_localconfig_uses_the_most_recent_account(tmp_path: Path) -> None:
    now = time.time()
    _write_localconfig(tmp_path, "111", LOCALCONFIG, now - 1000)
    recent = _write_localconfig(tmp_path, "222", LOCALCONFIG, now)
    assert proton.current_localconfig(tmp_path) == recent
    assert proton.current_localconfig(None) is None
    assert proton.current_localconfig(tmp_path / "empty") is None


def test_needs_winhttp_override_only_speaks_up_when_sure(tmp_path: Path) -> None:
    missing = LOCALCONFIG.replace('WINEDLLOVERRIDES=\\"winhttp=n,b\\" %command%', "-skipintro")
    _write_localconfig(tmp_path, "111", missing, time.time())
    assert proton.needs_winhttp_override(tmp_path, windows=False) == 'WINEDLLOVERRIDES="winhttp=n,b" %command% -skipintro'
    # Never on Windows, where Doorstop loads without any override.
    assert proton.needs_winhttp_override(tmp_path, windows=True) is None
    # Not when Steam's config can't be found or read.
    assert proton.needs_winhttp_override(tmp_path / "no-steam", windows=False) is None


def test_needs_winhttp_override_is_quiet_when_already_set(tmp_path: Path) -> None:
    _write_localconfig(tmp_path, "111", LOCALCONFIG, time.time())
    assert proton.needs_winhttp_override(tmp_path, windows=False) is None


REG_WITH_SECTION = (
    "WINE REGISTRY Version 2\n"
    ";; All keys relative to REGISTRY\\\\User\\\\S-1-5-21\n"
    "\n"
    "[Software\\\\Wine\\\\AppDefaults\\\\Other.exe\\\\DllOverrides] 1763063467\n"
    '"winhttp"="builtin"\n'
    "\n"
    "[Software\\\\Wine\\\\DllOverrides] 1763063467\n"
    "#time=1dc0000000000000\n"
    '"d3d9"="native"\n'
    '"winhttp"="builtin"\n'
    "\n"
    "[Software\\\\Wine\\\\Fonts] 1763063467\n"
    '"x"="y"\n'
)


def test_prefix_section_uses_doubled_backslashes_like_user_reg() -> None:
    assert proton.PREFIX_SECTION == "[Software\\\\Wine\\\\DllOverrides]"


def test_add_winhttp_appends_a_wine_style_section() -> None:
    text = "WINE REGISTRY Version 2\n\n[Software\\\\Wine\\\\Fonts] 1\n\"x\"=\"y\"\n"
    out = proton.add_winhttp_to_registry(text, now=1780000000)
    assert out.endswith(
        "[Software\\\\Wine\\\\DllOverrides] 1780000000\n"
        f"#time={(1780000000 + 11644473600) * 10_000_000:x}\n"
        '"winhttp"="native,builtin"\n'
    )
    assert text.splitlines()[2] in out  # the rest of the file is untouched
    assert proton.registry_has_winhttp_override(out)


def test_add_winhttp_replaces_the_value_in_an_existing_section() -> None:
    assert not proton.registry_has_winhttp_override(REG_WITH_SECTION)
    out = proton.add_winhttp_to_registry(REG_WITH_SECTION)
    assert proton.registry_has_winhttp_override(out)
    assert out.count('"winhttp"=') == 2  # the other app's value is left alone
    assert '"d3d9"="native"' in out and "[Software\\\\Wine\\\\Fonts] 1763063467" in out
    # Applying twice changes nothing.
    assert proton.add_winhttp_to_registry(out) == out


def test_remove_winhttp_only_touches_the_global_section() -> None:
    out = proton.remove_winhttp_from_registry(proton.add_winhttp_to_registry(REG_WITH_SECTION))
    assert not proton.registry_has_winhttp_override(out)
    assert '"d3d9"="native"' in out
    assert '[Software\\\\Wine\\\\AppDefaults\\\\Other.exe\\\\DllOverrides] 1763063467\n"winhttp"="builtin"' in out
    assert proton.remove_winhttp_from_registry("no sections\n") == "no sections\n"


def _prefix(library: Path, text: str) -> Path:
    registry = library / "steamapps" / "compatdata" / "1764530" / "pfx" / "user.reg"
    registry.parent.mkdir(parents=True)
    registry.write_text(text, encoding="utf-8")
    return registry


def test_set_and_remove_prefix_override_with_backup(tmp_path: Path, monkeypatch) -> None:
    from sailwind_mod_sync.game import wait_window

    monkeypatch.setattr(wait_window, "linux_game_process_running", lambda **kwargs: False)
    original = "WINE REGISTRY Version 2\n"
    registry = _prefix(tmp_path / "sdcard", original)
    assert proton.sailwind_prefix_registry([tmp_path / "home", tmp_path / "sdcard"]) == registry
    assert not proton.prefix_has_winhttp_override([tmp_path / "sdcard"])

    backup = proton.set_prefix_winhttp_override(registry)
    assert backup.read_text(encoding="utf-8") == original
    assert proton.prefix_has_winhttp_override([tmp_path / "sdcard"])

    proton.remove_prefix_winhttp_override(registry)
    assert not proton.prefix_has_winhttp_override([tmp_path / "sdcard"])
    assert not list(registry.parent.glob("*.sms-tmp"))


def test_prefix_changes_refuse_while_sailwind_runs(tmp_path: Path, monkeypatch) -> None:
    from sailwind_mod_sync.game import wait_window

    registry = _prefix(tmp_path, "WINE REGISTRY Version 2\n")
    monkeypatch.setattr(wait_window, "linux_game_process_running", lambda **kwargs: True)
    with pytest.raises(proton.PrefixChangeError, match="Close Sailwind"):
        proton.set_prefix_winhttp_override(registry)
    assert registry.read_text(encoding="utf-8") == "WINE REGISTRY Version 2\n"
    monkeypatch.setattr(wait_window, "linux_game_process_running", lambda **kwargs: False)
    with pytest.raises(proton.PrefixChangeError, match="wasn't found"):
        proton.set_prefix_winhttp_override(tmp_path / "missing" / "user.reg")


def test_launch_option_reminder_is_quiet_once_the_prefix_has_the_override(tmp_path: Path, monkeypatch) -> None:
    missing = LOCALCONFIG.replace('WINEDLLOVERRIDES=\\"winhttp=n,b\\" %command%', "")
    _write_localconfig(tmp_path, "111", missing, time.time())
    monkeypatch.setattr(proton, "prefix_has_winhttp_override", lambda libraries=None: False)
    assert proton.needs_winhttp_override(tmp_path, windows=False) is not None
    monkeypatch.setattr(proton, "prefix_has_winhttp_override", lambda libraries=None: True)
    assert proton.needs_winhttp_override(tmp_path, windows=False) is None
