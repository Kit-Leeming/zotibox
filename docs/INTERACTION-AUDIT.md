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
