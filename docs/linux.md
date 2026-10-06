# Sailwind Mod Synchronizer on Linux and Steam Deck

Sailwind only ships a Windows build, so on Linux it runs through Steam's **Proton**. Sailwind Mod Synchronizer itself runs natively on Linux, finds your Steam libraries (including SD cards and Flatpak Steam), and launches ModPacks through Proton the same way it does on Windows.

## What you need

- A 64-bit Linux system with **glibc 2.38 or newer**. That includes SteamOS 3.8 on the Steam Deck, Ubuntu 24.04, Fedora 39 and current Arch-based distros. On older systems, [run it from source](#run-from-source) instead.
- Sailwind installed through Steam, and started once so Proton has set it up.

## Install

1. Download `SailwindModSynchronizer-<version>-linux-x86_64.tar.gz` from the [latest release](https://github.com/foxyv/SailwindModSynchronizer/releases/latest).
2. Extract it anywhere you like, for example `~/Applications`:

   ```sh
   mkdir -p ~/Applications
   tar xzf SailwindModSynchronizer-*-linux-x86_64.tar.gz -C ~/Applications
   ```

3. Start it with the **`sailwind-mod-sync`** launcher in that folder:

   ```sh
   ~/Applications/SailwindModSynchronizer/sailwind-mod-sync
   ```

4. Optional: add it to your desktop's application menu:

   ```sh
   ~/Applications/SailwindModSynchronizer/install-desktop-entry.sh
   ```

   This writes `~/.local/share/applications/sailwind-mod-sync.desktop`. Run it again if you move the folder.

> **Always start the app through `sailwind-mod-sync`, not the `SailwindModSynchronizer` binary.** When Steam starts programs, it sets `LD_LIBRARY_PATH` and `LD_PRELOAD` for its own runtime, which makes the app load the wrong Qt libraries and crash. The launcher clears them first. It also waits for an update that's still being installed, so the app never starts from half-copied files.

## Steam Deck

Set things up once in **Desktop Mode**:

1. Install as above. `~/Applications` is a good place, since it survives SteamOS updates.
2. In Steam, choose **Games → Add a Non-Steam Game to My Library…**, click **Browse…**, and pick `sailwind-mod-sync` in the app's folder.
3. Back in **Game Mode**, the app is in your library.

Controls in Game Mode:

- The app doesn't support the controller yet ([#23](https://github.com/foxyv/SailwindModSynchronizer/issues/23)). Until it does, open the app's **Controller settings** in Steam and pick the **Web Browser** template, so the right trackpad moves a cursor and R2 clicks. Tapping the touchscreen works too.
- Press **Steam + X** for the on-screen keyboard, for example to name a pack.

## Getting mods to load

On Linux, Doorstop (the part that loads BepInEx into Sailwind) needs Wine to use its `winhttp.dll`. Without that, Sailwind starts normally but **without mods**.

When you press **Play**, the app checks for this. If it's missing, it shows **One Steam setting for mods** with two ways to fix it:

- **Steam launch option (recommended):** click **Copy**, then in Steam open Sailwind's **Properties**, and paste into **Launch Options**:

  ```
  WINEDLLOVERRIDES="winhttp=n,b" %command%
  ```

  If Sailwind already has launch options, the app shows them combined, so nothing you had is lost.
- **Set it in Proton instead…:** if you'd rather not touch launch options, the app can add the same setting (`winhttp = native,builtin`) to Sailwind's own Proton files. It explains exactly what changes before you agree, keeps a backup (`user.reg.sms-backup` next to the file), and only works while Sailwind is closed. You can undo it any time in **Settings → Proton → Remove Proton setting**. If Sailwind's Proton files are ever reset, you'll be asked again.

Once either is set, the reminder doesn't appear again. You can also choose **Don't remind me again** in the dialog, and turn the reminder back on in **Settings → Proton**.

The setting only affects Sailwind, and it's harmless for the plain game: **Launch Vanilla** still starts Sailwind without mods.

## Where things are

| What | Where |
|---|---|
| App data (packs, downloads, settings, logs) | `~/.local/share/SailwindModSynchronizer` (or `$XDG_DATA_HOME/SailwindModSynchronizer`); override with `SAILWIND_MOD_SYNC_HOME` |
| Sailwind's saves | Inside Sailwind's Proton prefix: `steamapps/compatdata/1764530/pfx/drive_c/users/steamuser/AppData/LocalLow/Raw Lion Workshop/Sailwind`. The app checks every Steam library, since the prefix isn't always in the same library as the game. |
| BepInEx log for a pack | **Tools → Open Latest Log** |

**Backup → Backup saves** and **Restore saves** find the Proton saves automatically.

## Updates

**Help → Check for updates…** works like on Windows: it downloads the Linux build and installs it when the app closes.

- **On the desktop**, the app closes, updates and reopens by itself.
- **In Steam Deck Game Mode**, the app closes and updates, but doesn't reopen: Game Mode only shows windows of games Steam started itself. Start it again from your library. If the update is still finishing, it opens as soon as it's done.

If an update doesn't seem to apply, `updates/apply.log` in the app's data folder records each step.

## Playing from Steam directly

Sailwind's own **Play** button in Steam uses whatever the app set up last: after you play a ModPack, starting Sailwind from Steam loads that pack's mods again. Use **Launch Vanilla** once in the app to go back to the plain game.

## Troubleshooting

- **The app closes right away when started from Steam.** The library entry probably points at the `SailwindModSynchronizer` binary. Point it at `sailwind-mod-sync` instead.
- **Sailwind starts, but no mods load.** Check that Sailwind has the launch option above, or that the Proton setting is on (**Settings → Proton** shows **Remove Proton setting** when it is). Then open the pack's log with **Tools → Open Latest Log**: if BepInEx ran, it lists every mod it loaded.
- **The app says Sailwind wasn't found.** Start Sailwind from Steam once, then set the game folder in **Settings** if it still isn't detected.
- **Asking for help.** **Tools → Export Logs…** saves the useful logs in a zip. Steam IDs, Steam names and your home folder are removed from the copies, so it's safe to share.

## Known limitations

- Builds need glibc 2.38 or newer, because they're built on SteamOS.
- No controller navigation yet ([#23](https://github.com/foxyv/SailwindModSynchronizer/issues/23)).
- The download is about 95 MB; it will get smaller.
- Updates copy the new version over the old one, so files that a new version no longer has are left in place. That's harmless, and the same as on Windows.

## Run from source

On systems the release build doesn't support, or to work on the app:

```sh
git clone https://github.com/foxyv/SailwindModSynchronizer.git
cd SailwindModSynchronizer
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m sailwind_mod_sync
```

Python 3.11 or newer is required. On SteamOS, keep the folder in your home directory, since system folders are reset by SteamOS updates.

To build the Linux archive yourself: `.venv/bin/pip install -e ".[build]"`, then `.venv/bin/python scripts/build.py --release`. The archive lands in `dist/`.
