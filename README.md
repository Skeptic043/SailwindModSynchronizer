# Sailwind Mod Synchronizer

A Qt (PySide6) mod manager for [Sailwind](https://store.steampowered.com/app/1764530/Sailwind/). It catalogs mods from [ModVersionChecker](https://github.com/bryon82/SailwindModVersionChecker), caches versioned zips in a Maven-like local library, and launches isolated ModPacks through UnityDoorstop so the Steam game folder stays vanilla.

## Download

Grab `SailwindModSynchronizer-<version>-windows.zip` from the [latest release](https://github.com/foxyv/SailwindModSynchronizer/releases/latest), extract it anywhere, and run `SailwindModSynchronizer.exe`. Later versions install themselves through **Help → Check for updates**.

The exe is signed, but not yet with a publicly trusted certificate, so the first time you run it Windows SmartScreen may show **Windows protected your PC**. Click **More info**, then **Run anyway**.

## Requirements

- Python 3.11+
- Windows (Sailwind / Doorstop `winhttp.dll`)
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

`%LOCALAPPDATA%\SailwindModSynchronizer`

Override with `SAILWIND_MOD_SYNC_HOME`.

BepInEx is downloaded from Thunderstore **BepInExPack**. Mods are downloaded from GitHub/GitLab **release zip assets** listed in ModVersionChecker. Extra mods that are not in that list live in this repo's [`catalog/ModList.json`](catalog/ModList.json) (and [`catalog/release_versions.json`](catalog/release_versions.json)) on `master`; **Refresh catalog** and **Scan updates** merge them in after MVC. On the Catalog tab you can also **Add GitHub repo** for projects that are not in either list. Play copies `winhttp.dll` into the game folder and launches `Sailwind.exe` with `--doorstop-target-assembly` pointed at the selected pack.

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
