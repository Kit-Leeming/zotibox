# ZotiBox

A Windows desktop app for organising local music, keeping Spotify playlists in sync, downloading through Zotify, and preparing Rekordbox library updates.

**[Download for Windows](https://github.com/Kit-Leeming/zotibox/releases/latest)** · [Report a bug](https://github.com/Kit-Leeming/zotibox/issues) · [Build from source](docs/BUILD.md)

![ZotiBox playlist management](docs/images/playlists.png)

## What it does

- Index music in existing folders, find duplicates and match local files to Spotify tracks.
- Keep linked playlists in Spotify order, review uncertain matches and queue missing tracks across the library.
- Download tracks, albums and playlists into one shared music folder, with progress and an estimated finish time.
- Preview playlist and metadata changes for Rekordbox, then apply them with verified backups.
- Browse folder trees, drag tracks and playlists, and preview audio with a waveform and available Rekordbox cue markers.
- Customise colours, gradients and row sizes. Collapse navigation for more workspace.

Screenshots use generated demo data.

<details>
<summary>Local music and download queue screenshots</summary>

![Local music folder tree and cover thumbnails](docs/images/local-music.png)

![Download queue with remaining audio time and estimated finish](docs/images/downloads.png)

</details>

## Install

Download and run the latest installer on **Windows 10 or 11, 64-bit**. Updates install over the existing version; uninstalling first is unnecessary. ZotiBox checks for updates at startup and also under **Settings → Updates**.

The catalogue is stored in **ZotiBox Database** beside the application. Use **Settings → Database** to choose another location. Music stays in its existing folders unless you explicitly move or delete it.

## Connect your library

1. **Settings → Library folders:** add music folders, then scan from Local Music.
2. Create a Web API app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and register both redirect URIs:
   - `http://127.0.0.1:4381/callback`
   - `http://127.0.0.1:4381/login`
3. **Settings → Spotify account:** enter the Client ID and connect. No client secret is required. The account must have access to the developer app; development-mode users need their own app or an allowlist invitation.
4. **Settings → Downloads:** choose the music destination and connect Zotify. Its streaming and developer authorisations may each open a browser. Saved valid logins are reused; use **Fresh Zotify login** to reconnect explicitly.
5. **Settings → Rekordbox:** select `master.db` and connect. Rekordbox **7.2.11** is the tested version.

## Spotify to Rekordbox

**Link playlist → Sync all linked → Review needed → Queue missing → Start downloads → Sync with Rekordbox → Preview → Apply**

Paste a Spotify playlist into search to create a new sync. Spotify controls linked membership and ordering; local playlists remain independent. **Queue missing** covers every linked playlist and keeps you on the current page. In review, **Not in local library** makes a track eligible for that queue. Weak candidates are classified as missing; strong but ambiguous candidates remain for review.

**Sync with Rekordbox** lets you choose multiple playlists to create or update. Missing files are skipped, including when creating an empty playlist; sync again after downloading. Rekordbox changes stay in a preview until Apply. Single-playlist sync is available from its right-click menu.

## Backups and current limits

- Rekordbox can stay open for viewing and preparing previews. Close it before Backup, Apply or Restore. Database writes are disabled initially; every Apply and Restore creates a verified backup. External music files are not included in those backups.
- Track identities, existing cues and analysis data are preserved where possible. New tracks need Rekordbox analysis. Cue markers are read-only, and metadata edits affect the Rekordbox database rather than audio-file tags.
- Zotify downloads one track at a time at real-time speed. Finish times are estimates and exclude login, conversion and retry delays. FLAC output alone does not prove lossless audio; source-format information appears in the queue and file details.
- Spotify playlist folders are unavailable through the Web API. ZotiBox provides its own folders. Automatic PC-to-PC sync is not included; manual `.zotibox` export/import is available.

Zotify integration uses the [ZahBoy fork](https://github.com/ZahBoy/zotify). Dependency licences and attribution are listed in [third-party notices](THIRD-PARTY-NOTICES.md).
