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
