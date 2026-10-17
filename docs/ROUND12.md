# 0.12 changes and validation

- Ogg/Opus imports no longer reach `pyrekordbox.FileType.OGG` (which does not exist).
  Sync prepares a reusable, decoded-and-verified MP3 320 kbps compatibility copy.
  Original files and existing native identities remain intact. Preview changes
  report compatibility copies. Source provenance also links the copy in comparison.
- Waveforms cache three view widths and blit while scrolling. The playhead uses
  a precise 16 ms timer independent of media position notifications. Dense grids
  reduce detail until zoomed; native height data is pooled to retain transients.
- Ctrl/Shift playlist selections survive refreshes. Context menus offer valid
  bulk delete, sync, export and move actions. Native deletions remain staged.
- Output choices are Auto, MP3, FLAC and Original, with a separate source
  preference. Auto uses genuine FLAC or MP3 320 kbps; explicit FLAC rejects lossy
  sources; Original preserves the selected codec/container. Older preferences
  migrate to equivalent choices.
- Playlists → Compare supports local, Spotify-linked and native playlists:
  shared/unique tracks, duplicate positions, missing files and CSV export.
  Matching uses IDs, file identity and ISRC, not fuzzy titles.
- BPM is read from file tags and native Rekordbox values (stored hundredths).
  Rescanning backfills tags without changing file hashes or catalogue identities.

`tests/test_round12.py` covers conversion/reuse/source hashes, mixed comparison,
bulk actions in both context menus, BPM migration, source/output policies and
live muted playhead updates. Existing suites cover the surrounding integrations.

Controlled Qt paint comparison on this PC, 10-minute synthetic envelopes,
1200-pixel view, 60 frames, published 0.11 source (`9a76618`) versus this renderer:

| View | 0.11 median | 0.12 cached median | 0.12 p95 |
| --- | ---: | ---: | ---: |
| Overview scale | 23.36 ms | 0.24 ms | 0.29 ms |
| 16× zoom | 22.73 ms | 0.24 ms | 0.39 ms |

Raw local results: `.tmp/round12-waveform-profile.json`. This measures Qt
painting rather than physical display frame rate. A cold cache still builds
an image; platform load can affect visible smoothness. No user music or live
Rekordbox database was modified during validation.
