# UI and interaction review

Reviewed 6 November 2026 using design-critique and UI/UX Pro Max. ZotiBox is a
desktop workspace for repeated library preparation tasks: keep controls steady,
preserve context, and make recovery cheaper than repeating the work.

| Finding | Severity / principle | Implemented change |
|---|---|---|
| Stale Rekordbox previews required discarding every edit. | Major; error recovery, user control, Tesler's law | Refresh & keep changes replays supported edits on a new private copy. Review & apply runs this automatically before confirmation. Conflicts and cancellation preserve the original pending preview. |
| One mistaken preview edit required starting again. | Major; user control | Undo last edit restores the preceding private session, including sync/link bookkeeping; history survives restart. Ctrl+Z works in the Rekordbox tree and table. |
| Queue controls appeared/disappeared as selection changed. | Moderate; spatial consistency | Keep queue actions in fixed positions and disable unavailable actions. Separate queue ordering/cleanup from playback controls. |
| Streaming progress rebuilt unrelated library trees. | Major; responsiveness and continuity | Progress refreshes queue rows; structural library refreshes follow completion and other library changes. |
| Table rebuilds moved selection to different tracks or lost focus. | Major; user control | Restore selection, current row and scrolling by identity; unchanged standard tables retain their existing items. All Tracks retains multiple selections through sorting. |
| Fixed widths hid important information at smaller sizes. | Moderate; visibility and information hierarchy | Main tables distribute remaining space across track/artist columns. Playlist trees have usable minimum widths. Right-click headings to choose visible columns; choices persist. |
| Local playlist editing was available mainly through context menus and drags. | Moderate; recognition and flexibility | Show move/remove controls for local playlists. Support Alt+Up/Down and Delete, including multiple selected removals. Spotify-controlled membership remains protected. |
| Long playlist trees were difficult to browse. | Moderate; recognition | Find a playlist filters names and reveals matching ancestors without overwriting saved folder expansion. Ctrl+F focuses the current view's search. Ctrl+1–5 opens main pages. |
| Blank tables gave no next action. | Moderate; visibility and guidance | Search, queue, playlist, Rekordbox and All Tracks empty states explain what to do next. |
| Keyboard focus and interaction states were hard to distinguish. | Moderate; accessibility and consistency | Add focus, pressed and disabled states, accessible search names and button targets. Retint existing library icons when changing theme. |
| First-use Rekordbox linking could stop after loading. | Major; orchestration | Resume the linking dialog after the loading worker finishes. |

## Rekordbox recovery details

Each new playlist or metadata edit records a serializable recipe. Refresh replays
the recipes against a fresh consistent native copy, translating newly allocated
playlist, track and entry IDs. Native extras and unrelated metadata remain in the
fresh copy. Conflicting renames, metadata, deletion targets and competing track
orders stop recovery without replacing the original preview. Apply still checks
opt-in, the closed native app, source fingerprints, file snapshots, integrity and
verified backups. Refresh alone never applies changes to the native library.

Pre-existing previews without recipes and file-organisation edits cannot always
be replayed safely. They are retained with an explanation. Changed source music
and genuine concurrent edits can still need review. Automatic conflict resolution
and a full virtualized library model remain outside this change.

## Validation

Regression tests use disposable native databases and generated audio. Recovery
tests include restart, linked native extras, generated IDs, same-field conflicts,
cancellation, failed refresh/discard, Undo, and the complete refresh-review-apply
UI sequence. Screenshots use generated demo data at 1500×920 and 1120×740 in dark
and light themes. These checks do not establish live Spotify service behaviour or
acceptance against the user's actual Rekordbox library.

Final source regression run: **223 passed**, with 20 dependency deprecation
warnings. Local OAuth callback and Windows activation tests run outside the
network/IPC sandbox; all test workspaces remain under the project's `.tmp`.

The separate review executable is under
`dist/UX-Review-2026-11-06/ZotiBox/ZotiBox.exe`. Packaged startup exited successfully
and rendered all five main pages against an empty disposable database. This build
does not replace the installed application. Close the existing app before opening
the review executable if both use the same catalogue.

UI/UX Pro Max was installed from
[the upstream repository](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)
at `C:\Users\KitLe\.codex\skills\ui-ux-pro-max`. No other skills were installed.
Its broad music-product styling suggestions were not a close desktop fit; the
targeted dense-workspace, recovery, keyboard and focus guidance informed this pass.
