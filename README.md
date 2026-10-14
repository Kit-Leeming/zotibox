# ZotiBox

Windows music catalogue, Spotify playlists, Zotify downloads and Rekordbox management.

## Install and update

Download the Windows installer from [Releases](https://github.com/Kit-Leeming/zotibox/releases/latest).
Install over the existing app folder when updating; uninstalling is unnecessary.
No Python environment or P: drive is required. The default installation is in your Windows user profile.
The app creates **ZotiBox Database** beside its EXE. Settings → Database can relocate it.
Settings → Updates checks GitHub and downloads a checksum-verified installer.

## Setup

1. Settings → Library folders: add your music folders, then scan.
2. Create a Web API app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
   Register `http://127.0.0.1:4381/callback` and `http://127.0.0.1:4381/login` as redirect URIs.
3. Settings → Spotify account: enter its Client ID and connect. No client secret is needed.
4. Settings → Downloads: choose a destination and connect Zotify; complete both browser login steps.
   Other testers need their own Client ID or access to your developer app's allowlist.
5. Settings → Rekordbox: select `master.db` and connect. The tested version is **7.2.11**.

## Use

- **Downloads:** search or paste a Spotify track/album/playlist URL, queue tracks and start.
  Downloads run sequentially at real-time speed. Results and file details show the selected source format.
  FLAC output does not guarantee a lossless source; the fork may fall back when requesting lossless.
- **Local Music:** search the folder tree; right-click for details, Explorer and playlist actions.
  Drag songs into local playlists or Rekordbox previews; hover navigation/tabs to switch while dragging.
  Drag within editable playlists to reorder. Spotify-linked membership stays controlled by Spotify.
  Use Row size to adjust thumbnails. File deletion prompts before using the Windows Recycle Bin.
  Discovery can run alongside downloads and Spotify tasks. Existing music is never replaced automatically.
- **Playlists:** create local playlists or link owned/collaborative Spotify playlists.
  Linked membership and order follow Spotify. To update Rekordbox: **Sync selected → Queue missing →
  Start downloads → Send / resync to Rekordbox → review → Apply**. Export settings and M3U8 are here.
- **Playlists → Rekordbox:** view the collection/folders, prepare playlists and metadata edits,
  or preview a ZotiBox playlist sync. Review changes before Apply.
- Double-click a local track to play. The player includes volume, a seekable waveform and the cue/loop
  points available in the connected Rekordbox database. Cue markers are read-only; this is a preview player.
- Playlist pickers have search and filters. Spotify does not expose its playlist folder structure through
  the Web API. Dashboard shows recent completed downloads.
- **Settings → Appearance:** choose a visual preset or edit background/accent colours.
  Harmony swatches stay anchored while choosing gradient endpoints.

## Rekordbox backups and writes

Rekordbox can stay open for viewing and preparing previews; close it for Backup, Apply or Restore.
Writes are disabled initially. You can first use Rekordbox's **File → Library → Backup Library**.
Close Rekordbox, then enable writes in Settings → Rekordbox; ZotiBox verifies a data-directory backup.
Every Apply and Restore creates another backup. External music files are excluded from these backups.
Existing track IDs, cues and analysis data are retained; newly imported tracks need Rekordbox analysis.
Metadata edits affect the Rekordbox database, not audio-file tags. Smart/system playlists are read-only.

Desktop/laptop synchronisation is not included. Manual `.zotibox` export/import is available.
Build instructions and validation details are in [docs](docs/BUILD.md).
