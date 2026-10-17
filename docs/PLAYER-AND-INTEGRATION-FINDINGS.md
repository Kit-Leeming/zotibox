# Player and integration evidence — 0.11

## Spotify interactions

Installed Spotify package: `SpotifyAB.SpotifyMusic_1.304.258.0_x64`.
Read only the shipped `Apps/xpui.spa` JavaScript resources. A shared draggable
row component in `xpui-routes-collection-episodes.js` renders distinct BEFORE
and AFTER insertion markers. This informed ZotiBox's insertion line, midpoint
drop positions and edge autoscroll. It does not establish Spotify's live event flow.

REA parent evidence:
`ev_947aa4bfc4a1acd0c2dffcfd579ee3feb8221b9100a3e99be4579e99d0b9562f`.
Only that source was analyzed; semantic extraction was capped at 400 nodes.
The complete retained evidence is exported locally to `.tmp/round11-rea-evidence.json`.
REA's Windows host does not support its native UI or process scenario capture.
No client was restarted or instrumented; no account playlist was changed.

Qt viewport tests exercise mouse initiation, multirow MIME, drag events,
insertion rendering, selection retention and autoscroll. A real Windows OLE
drag loop remains a manual test, since a controlled attempt did not complete.
See `tests/test_drag_interactions.py`.

## Rekordbox analysis

Read a previously copied database and four selected native DAT/EXT pairs.
Existing color data parsed successfully; their PQTZ tags contained zero beat
entries. Those samples do not establish whether other library tracks have grids.
All sampled file hashes stayed unchanged. Small local evidence:
`.tmp/round11-native-analysis.json`.

The player reads selected-track PQTZ beat positions and PWV5 colors, with
frequency-analysis fallback. A synthetic binary fixture verifies nonempty
grid parsing and display without writes (`tests/test_player_deck.py`).
No grid is estimated or written. Cue/grid editing and structural cue generation
are deferred. Native references are constrained to the source's `share` folder.

## Audio source fidelity

Pinned Zotify builds its filename before fetching audio (`api.py`, Track.download).
Its automatic quality previously selected Vorbis even for FLAC output.
The worker now preflights the selected source before filename creation:
FLAC sources retain FLAC. The requested DJ compatibility fallback encodes MP3 at
320 kbps; an optional original-format mode uses FFmpeg stream copy. MP3 encoding
adds another lossy step and is labelled lossy. Unknown sources fail closed for FLAC requests.
The download validator rejects invalid FLAC containers before publication.

`tests/test_source_policy.py` verifies the pinned conversion with real FFmpeg:
every compressed Vorbis packet hash is unchanged after native-container output.
This proves no added lossy encoding for that path; a delivered FLAC cannot prove
the original studio master was never lossy. Imported files without provenance
remain unverified. No streaming credentials or actual user downloads were tested.

## Mixed playlists and phone files

[Spotify's API](https://developer.spotify.com/documentation/web-api/concepts/playlists)
does not support adding local files. ZotiBox preserves separate local extras
across complete Spotify snapshots and protects Spotify ordering. Linking an
existing Rekordbox target retains its unmatched extras and song identities;
later syncs remove only previously managed tracks.

Phone export copies local extras unchanged into a ZIP with a portable M3U8 and
[Spotify Local Files instructions](https://support.spotify.com/article/local-files/).
It does not upload private audio or automatically transfer files to a phone.
Actual mobile playback and cross-device local URI matching need device testing.

## Backups

The last successful own apply records native file hashes and the verified recovery
archive hash in machine-local state. A match reuses that archive; external changes,
missing/corrupt archives or unknown state require a fresh verified backup.
Restore always takes a recovery backup. Hash checks still read the native files.
Tests use disposable synthetic collections (`tests/test_native_backup_provenance.py`).
