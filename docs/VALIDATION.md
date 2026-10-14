# ZotiBox 0.4 validation

Windows 11 x64; Rekordbox 7.2.11. Automated checks cover catalogue/playlist identity,
Spotify snapshots/PKCE, downloader queue/audio validation, concurrent scanning,
themes, GitHub update checks/checksums and verified transfer packages.

Rekordbox checks use synthetic databases and an isolated copy of the installed
7.2.11 database. Read, playlist/metadata changes, backups, Apply and Restore have
been exercised on copies. Protected cue/censor/mixer tables and analysis paths
remain unchanged. The live user database was not modified.

Safety checks cover opt-in, source conflicts, running Rekordbox, failed staging,
restore verification and playlist sync reusing existing content identities.
Full backups include the Rekordbox data directory and its analysis assets;
external music is excluded.

Installer/runtime checks and exact hashes/test counts are recorded in
`dist/release-evidence.json`. Live Spotify/Zotify login and Windows drag/snap
behaviour need user validation on the installed build. Rekordbox has not been
opened against the altered test copy; test preview/backup workflows before live Apply.

The app bundles Python, Qt, SQLCipher, the pinned downloader and FFmpeg.
No Python installation is needed to run the Windows release.
