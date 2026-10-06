# Sailwind Mod Synchronizer

A Qt (PySide6) mod manager for [Sailwind](https://store.steampowered.com/app/1764530/Sailwind/). It catalogs mods from [ModVersionChecker](https://github.com/bryon82/SailwindModVersionChecker), caches versioned zips in a Maven-like local library, and launches isolated ModPacks through UnityDoorstop so the Steam game folder stays vanilla.

## Download

Grab `SailwindModSynchronizer-<version>-windows.zip` from the [latest release](https://github.com/foxyv/SailwindModSynchronizer/releases/latest), extract it anywhere, and run `SailwindModSynchronizer.exe`. Later versions install themselves through **Help → Check for updates**.

The exe is signed, but not yet with a publicly trusted certificate, so the first time you run it Windows SmartScreen may show **Windows protected your PC**. Click **More info**, then **Run anyway**.

**Linux and Steam Deck:** grab `SailwindModSynchronizer-<version>-linux-x86_64.tar.gz` instead. See the [Linux and Steam Deck guide](docs/linux.md) for setup, getting mods to load through Proton, and Game Mode.

## Requirements

- Python 3.11+
- Windows, or Linux with Sailwind running through Proton (see the [Linux guide](docs/linux.md))
- A Sailwind install (Steam appid `1764530`)

## Install and run

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
sailwind-mod-sync
```

Or: `python -m sailwind_mod_sync`

## Data

Config, catalog cache, artifact library, and ModPack instances live under:

`%LOCALAPPDATA%\SailwindModSynchronizer` on Windows, or `~/.local/share/SailwindModSynchronizer` (`$XDG_DATA_HOME`) on Linux.

Override with `SAILWIND_MOD_SYNC_HOME`.

**Tools → Clear cache…** deletes everything the app can download again: cached catalogs, update check results, downloaded mods, BepInEx packs and staged app updates. Packs and their mod settings, your own catalog entries and backups are kept. Mods imported from files are kept too, unless you tick **Also delete mods imported from files**, as some of them may not be downloadable. Packs download their mods again on the next **Play**, or one by one with **Download** on the Pack tab.

## Catalog

BepInEx is downloaded from Thunderstore **BepInExPack**. Mods are downloaded from GitHub/GitLab **release zip assets**. The catalog combines three lists:

- [ModVersionChecker](https://github.com/bryon82/SailwindModVersionChecker);
- this repo's [`catalog/ModList.json`](catalog/ModList.json) (and [`catalog/release_versions.json`](catalog/release_versions.json)) on `master`, for mods that are not in ModVersionChecker;
- repositories you add with **Add GitHub repo** on the Catalog tab, including forks of mods that are already listed.

A mod listed from several repositories, such as an original and a fork, gets a row for each source; the **Source** column tells them apart. **Switch source** moves the selected pack to another source, and each pack keeps the source of each of its mods, so different packs can use different sources of the same mod. Downloads of the same version from different sources are stored separately in the library.

The catalog refreshes in the background once a day, at startup and while the app stays open (turn it off in Settings with **Refresh the catalog once a day**); **Refresh catalog** refreshes it on demand. **Scan updates** checks the latest release of every repository. Its results are kept across restarts and win over a catalog that lists no version or an older one. **Update all** on the Pack tab updates every mod of the pack that has a newer version.

Play copies `winhttp.dll` into the game folder and launches `Sailwind.exe` with `--doorstop-target-assembly` pointed at the selected pack. Mods the pack needs that are not downloaded yet are downloaded first.

## Tests

```powershell
pytest
```

## Windows executable

From the repo root, with the venv active:

```powershell
pip install -e ".[dev,build]"
.\scripts\build.ps1
```

Or: `python scripts/build.py`

That is an **incremental** freeze: it reuses the PyInstaller cache, skips UPX, and does not write a GitHub zip. Use it while iterating on the app.

For a GitHub release zip:

```powershell
az login
.\scripts\build.ps1 -Release
```

Or: `python scripts/build.py --release`

That does a clean freeze, **Authenticode-signs** the freeze output with Azure Artifact Signing, and writes `dist\SailwindModSynchronizer-<version>-windows.zip`. Upload that zip to the GitHub release so the app can auto-update.

Signing reads `%USERPROFILE%\sms-signing\metadata.json` (copy [scripts/signing.metadata.example.json](scripts/signing.metadata.example.json) and fill in your Artifact Signing account, certificate profile, and regional endpoint). Override the path with `SMS_SIGNING_METADATA`. Install [Azure CLI](https://aka.ms/installazurecliwindows) and [Artifact Signing Client Tools](https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations) (`winget install -e --id Microsoft.Azure.ArtifactSigningClientTools`). Your Azure user needs the **Artifact Signing Certificate Profile Signer** role.

Pass `--skip-sign` / `-SkipSign` for an unsigned zip. Incremental builds do not sign unless you pass `--sign`.

Both modes compile `dist\SailwindModSynchronizer\SailwindModSynchronizer.exe`, embed `assets/icon.png` as the application icon, and create a **Sailwind Mod Synchronizer** shortcut on the Desktop unless you pass `-SkipShortcut` / `--skip-shortcut`.

## License

This project is under the [MIT License](LICENSE). You may copy, modify, redistribute, and sell it, as long as you keep the copyright and license notice.
