# Change Log

## 0.5.0 (2026-10-06)

- The catalog refreshes in the background once a day, at startup and while the app stays open. Turn it off in **Settings** with **Refresh the catalog once a day**.
- When a mod is published from more than one repository, such as an original and a fork, the **Catalog** shows a row for each, with a new **Source** column. **Switch source** moves the selected pack to another one, and different packs can use different sources of the same mod.
- **Add GitHub repo** accepts a fork of a mod that is already in the catalog.
- **Update all** on the Pack tab updates every mod that has a newer version.
- **Tools → Clear cache…** deletes downloaded catalogs, mods, BepInEx packs and app updates. Packs, settings, your own catalog entries and backups are kept, and mods imported from files are kept unless you choose to delete them too.
- Mods that are not downloaded yet show a **Download** button, and the **Latest** column always shows the newest version instead of **Missing**.
- Mods on the Pack tab are sorted by name by default.
- The once-a-day check for app updates no longer waits for a restart; if the app stays open longer than a day, it checks again.
- Repositories you added yourself are no longer dropped from the catalog after **Scan updates** when ModVersionChecker lists the same mod. Sources your packs still use are added back on the first start.
- Versions found by **Scan updates** are kept after a restart, so mods like StickyFix no longer show as **Unavailable** until the next scan.
- The warning before **Play** lists only mods that can't be downloaded; other missing mods download while the pack starts.
- Hiding a catalog row hides only that source of the mod.
- Linking a mod to a repository in one pack no longer changes it in your other packs.

## 0.4.2 (2026-10-05)

- New **Help → Change log** shows what changed in each version, even without an internet connection.
- Switching between ModPacks no longer freezes the window for a few seconds.
- Quick tasks, like updating a mod to a version you already downloaded, no longer flash the **Working** dialog. It only appears when a task takes longer than a quarter second.

## 0.4.1 (2026-10-05)

- Removed the duplicate **Launch vanilla** menu bar item. The **Launch Vanilla** button under the ModPack list still starts Sailwind without mods.

## 0.4.0 (2026-10-05)

- **Export** now asks what to make before you pick a file: a full offline pack, mods only, or a recipe.
- A **full offline pack** includes BepInEx, every mod, and your mod settings, so friends can import it without downloading anything from GitHub or Thunderstore.
- While a bundle is being written, the status bar shows each file as it is added, so long exports don't look frozen.

## 0.3.9 (2026-10-04)

- **Check All** and **Uncheck All** on the Catalog, with toggles that stay responsive while they run.
- **Tools → Open Plugins Folder** opens the selected ModPack's BepInEx plugins folder.
- 21 more catalog mods, including Dizzy Firewood Bundle.

## 0.3.8 (2026-09-16)

- The launch splash shows a bobbing sailboat instead of a BepInEx log that never had time to fill.
- The splash icon uses the high-resolution image, so it stays sharp.
- **Help → Test splash screen** previews the splash without launching the game.
- The project is now MIT-licensed.

## 0.3.7 (2026-09-16)

- **Play** starts Sailwind through Steam. After the splash closes, focus stays on the game instead of jumping back to the manager.
- The window remembers its size, position, and panel layout.
- The status bar tells you when catalog updates are available, and update scans can run in the background.
- Importing a zip lets you pick which plugins folder to use, and newly added catalog mods are selected automatically.

## 0.3.6 (2026-09-16)

- GitHub release zips download correctly when a personal access token is set. Large downloads no longer fail around 50 MB.
- If a GitHub download comes through incomplete, the app shows the release page and how to use **Import Mod DLL/ZIP** instead.
- The extra catalog now includes Co-Op, Calendar, and other GitHub mods that aren't in ModVersionChecker. Use **Refresh catalog** to see them.

## 0.3.5 (2026-09-15)

- Mods are identified more reliably when their zips include extra DLLs or a generic plugins folder.
- You can hide catalog mods you don't want to see, and right-click menus were added to Catalog and ModPack rows.
- The app and its bundled DLLs are code-signed.

## 0.3.4 (2026-09-15)

- First code-signed build of the app and its bundled DLLs.

## 0.3.3 (2026-09-14)

- Confirms the auto-update fix from 0.3.2. If you're on 0.3.2, **Help → Check for updates** installs this.
- If you're still on 0.3.0 or 0.3.1, extract the 0.3.3 zip over your current folder once; those versions can't install it by themselves.

## 0.3.2 (2026-09-14)

- Fixed auto-update hanging on a console window that waited until you pressed Ctrl+C. Updates now wait and copy files from a hidden script.
- 0.3.0 and 0.3.1 can't install this update by themselves, so extract the zip over your current folder once. Later updates apply normally.

## 0.3.1 (2026-09-14)

- Catalog updates and downloads work for mods whose release tag has no version number (like `Release`); the version is read from the release name instead. Thanks @IhorSikaliuk.

## 0.3.0 (2026-09-13)

- **Copy** and **Paste** share a ModPack recipe on the clipboard, as Discord-friendly JSON or a compact code.
- This project's own mod list is merged in after ModVersionChecker, so extra mods like Dizzy Calendar show up without **Add GitHub repo**.
- Importing a pack adds any unknown mods to your custom catalog.
- The Catalog can hide mods already in the selected pack, and tints rows that are in it.

## 0.2.0 (2026-09-12)

- The Catalog lists one item per plugin, even when a GitHub repo ships several mods, and installing one only takes that plugin from the zip.
- The main window has Pack and Catalog tabs, and cached downloads moved to **Download Management**.
- **Play** keeps the splash up until the Sailwind window appears.
- The **Backup** menu can zip and restore your Sailwind saves. Restoring takes a snapshot of your current saves first.
- **Duplicate** no longer fails when a pack's BepInEx log is in use.

## 0.1.0 (2026-09-11)

- First public Windows release.
- Isolated ModPacks launched through Doorstop, so the Steam Sailwind folder stays vanilla.
- Catalog from ModVersionChecker, plus your own GitHub repos.
- Local download library, per-pack version picking, and BepInEx backup and restore.
- Auto-update from GitHub releases through **Help → Check for updates**.
