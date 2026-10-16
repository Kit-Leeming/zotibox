# Interaction review: 0.7

Reviewed [Mixxx library workflows](https://manual.mixxx.org/2.5/en/chapters/library.html),
its [playlist deletion shortcut issue](https://github.com/mixxxdj/mixxx/issues/13361),
and [Clementine startup/activation](https://github.com/clementine-player/Clementine/blob/master/src/main.cpp).
These informed interaction choices; their application code was not copied.

| Flow | Change |
|---|---|
| Launch while running | Restore the current window through a user-scoped local channel; retain the database writer lock. |
| Slow startup | Show startup progress and the window before library population; load visible cover art in background workers. Load the saved Rekordbox preview when needed. |
| Crash/relaunch | Recover locks whose owning process has died; never age-expire a live owner's lock. |
| Close during a task | Cancel/pause, then close automatically when jobs finish. |
| Missing downloads | Prepare partial or empty Rekordbox playlists; report skipped tracks and preserve source membership. Resync later to add downloaded tracks. |
| Playlist deletion | Toolbar, context menu and Delete shortcut prepare deletion. Collection tracks, music and cues remain. Clear app links only after Apply. |
| New playlist | Select it and reveal its parent folders; offer creation inside add-to-playlist pickers. |
| Existing playlist actions | Prefill rename, export native playlists as M3U8, and remove multiple selected entries. |
| Broken saved preview | Keep recovery data and leave the rest of the app usable. |

Automated checks use disposable files and native database copies. Real window activation,
storage latency and native Rekordbox use still need validation on the installed release.

## 0.8 follow-up

- Selected-playlist and all-playlist review queues deduplicate Spotify tracks, offer skip/previous,
  and save confirmed matches or explicit missing decisions across later syncs. Completed downloads
  replace missing decisions with their indexed recording. No-candidate review can still mark missing.
- Local playlist trees support nested folders, playlist/folder moves, retained expansion, rename,
  and removal of organisational folders while keeping playlists. Creation respects the selected folder.
- Rekordbox playlist/folder drags stage parent changes through pyrekordbox; root moves and cycle
  rejection preserve entries, cues and live source data. Apply remains a separate action.
- Track and playlist drag payloads are separated; destinations reject cross-database and invalid
  moves. Hover expands collapsed folders. Queue missing deduplicates and skips unavailable tracks.

Checked against [Mixxx library interactions](https://manual.mixxx.org/2.5/en/chapters/library.html)
and [pyrekordbox playlist API](https://pyrekordbox.readthedocs.io/en/latest/generated/pyrekordbox.masterdb.database.html).
REA/native mouse interaction is not available in this task; automated widget events and disposable
databases cover these flows, with physical drag testing left for the installed build.
