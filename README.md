# Sailwind Mod Synchronizer

A mod manager for [Sailwind](https://store.steampowered.com/app/1764530/Sailwind/). Mod Sync keeps sets of mods as separate **ModPacks**, downloads and updates them from GitHub and GitLab, and starts the game with the pack you pick. Your Steam game folder stays vanilla: each pack runs from its own folder through UnityDoorstop.

Mods come from [ModVersionChecker](https://github.com/bryon82/SailwindModVersionChecker), from this repository's own catalog, and from any repository you add. Every downloaded version is kept in a local library, so switching versions or packs does not download it again.

## Download

**Windows:** download `SailwindModSynchronizer-<version>-windows.zip` from the [latest release](https://github.com/foxyv/SailwindModSynchronizer/releases/latest), extract it anywhere, and run `SailwindModSynchronizer.exe`. The app checks for new versions at startup and installs them when you agree, or on demand through **Help → Check for updates**.

**Linux and Steam Deck:** download `SailwindModSynchronizer-<version>-linux-x86_64.tar.gz`. See the [Linux and Steam Deck guide](docs/linux.md) for setup, getting mods to load through Proton, and Game Mode.

## Managing mods

- Pick or create a ModPack on the left, then add mods to it with **Add to pack** on the **Catalog** tab. Right-click a mod and choose **Show Details** for more about it.
- On the **Pack** tab, the check box beside a mod turns it on or off, and the version column allows you to choose a different version to use.
- **Import Mod DLL/ZIP** adds a mod from a file, for mods that are not on GitHub or GitLab.
- **Copy** and **Paste** share a ModPack as text.
- **Export** and **Import** share a ModPack as a file.
- **Launch Vanilla** starts the game without mods.

### Catalog and updates

The catalog refreshes in the background once a day (this can be disabled in the Settings menu) and can be refreshed manually with **Refresh catalog**. **Tools → Check for mod updates** checks every mod in the catalog for updates. A newer GitHub prerelease is shown beside the mod's latest release and in its version list, and counts as an update only when **Include prereleases in mod updates** is on in Settings. **Add GitHub repo** adds a repository that is not in the catalog, including a fork of a listed mod. A mod listed from several repositories gets a row for each **Source**, and **Switch source** moves the selected pack to another one.

### Files and cache

Settings, the mod library and ModPack folders live in `%LOCALAPPDATA%\SailwindModSynchronizer` on Windows, or `~/.local/share/SailwindModSynchronizer` (`$XDG_DATA_HOME`) on Linux. Set `SAILWIND_MOD_SYNC_HOME` to use another folder.

**Tools → Clear cache…** deletes what the app can download again: catalogs, update checks, downloaded mods, BepInEx packs and app updates. Packs, their mod settings, your own catalog entries and backups are kept, and so are mods imported from files unless you tick **Also delete mods imported from files**. Packs download their mods again on the next **Play**.

## Troubleshooting

- **Windows protected your PC:** the exe is signed, but not yet with a publicly trusted certificate. Click **More info**, then **Run anyway**.
- **Mods do not load:** **Tools → Open Latest Log** opens the selected pack's BepInEx log, and **Tools → Open Plugins Folder** shows what the pack installed. On Linux, see the [Linux and Steam Deck guide](docs/linux.md).
- **Reporting a problem:** **Tools → Export Logs…** saves the selected pack's logs as a ZIP, with personal details removed, to attach to an issue.

## How it works

BepInEx comes from Thunderstore **BepInExPack**, and mods from GitHub and GitLab **release zip assets**. **Play** copies `winhttp.dll` into the game folder and launches `Sailwind.exe` with `--doorstop-target-assembly` pointed at the selected pack, so BepInEx and the pack's mods load from the pack's folder.

## Running from source

The app is written in Python with Qt (PySide6). It needs Python 3.11+ and a Sailwind install (Steam appid `1764530`), on Windows or on Linux through Proton.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
sailwind-mod-sync
```

Run the tests with `pytest`.

### Windows executable

With the venv active:

```powershell
pip install -e ".[dev,build]"
.\scripts\build.ps1
```

That is an **incremental** freeze for development: it reuses the PyInstaller cache, skips UPX, and does not write a release zip.

For a GitHub release zip, run `az login`, then `.\scripts\build.ps1 -Release`. That does a clean freeze, **Authenticode-signs** it with Azure Artifact Signing, and writes `dist\SailwindModSynchronizer-<version>-windows.zip`; upload that zip to the GitHub release so the app can update itself.

Signing reads `%USERPROFILE%\sms-signing\metadata.json` (copy [scripts/signing.metadata.example.json](scripts/signing.metadata.example.json) and fill in your Artifact Signing account, certificate profile, and regional endpoint), or the path in `SMS_SIGNING_METADATA`. It needs [Azure CLI](https://aka.ms/installazurecliwindows), [Artifact Signing Client Tools](https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations) (`winget install -e --id Microsoft.Azure.ArtifactSigningClientTools`), and the **Artifact Signing Certificate Profile Signer** role on your Azure user.

`-SkipSign` (`--skip-sign`) makes an unsigned release zip, `--sign` signs an incremental build, and `-SkipShortcut` (`--skip-shortcut`) skips the Desktop shortcut both modes create. `python scripts/build.py` takes the same options.

## Maintaining the catalog

The catalog combines [ModVersionChecker](https://github.com/bryon82/SailwindModVersionChecker), this repository's [`catalog/ModList.json`](catalog/ModList.json) and [`catalog/release_versions.json`](catalog/release_versions.json) on `master`, and repositories players add themselves.

To add a mod, give it an entry in both catalog files (BepInEx GUID, repository, name, plugin folder and current release) and add its release layout to `tests/test_catalog_additions.py`.

- `python scripts/update_catalog_from_mvc.py` names the ModVersionChecker mods this catalog does not, after the `[BepInPlugin]` attribute in their latest release, and takes ModVersionChecker's version where it is newer.
- `python scripts/update_catalog_versions.py` records each mod's newer release when it still extracts to the mod's plugin folders with a plugin declaring its GUID, and reports the rest. It prints each new release's files for updating its layout in the tests.

Both take `--dry-run` and call the GitHub API, so set `GITHUB_TOKEN` or save a token in the app's settings first.

## License

This project is under the [MIT License](LICENSE). You may copy, modify, redistribute, and sell it, as long as you keep the copyright and license notice.
