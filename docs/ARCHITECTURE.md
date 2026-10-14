# ZotiBox architecture

PySide6 desktop app with a SQLite catalogue and separate Spotify, Zotify and
Rekordbox adapters. Background jobs use independent database connections.
Permanent recording/file IDs and root-relative paths preserve playlist references.

Spotify snapshots replace linked membership only after complete pagination.
Zotify retains the pinned fork's serial downloader; staged audio is decoded and
checksummed before indexing. Tokens are protected with Windows DPAPI and excluded
from transfer packages and public source.

Rekordbox uses pyrekordbox 0.4.4 and SQLCipher. Reads are copied consistently into
private sessions. Every successful edit creates a new preview copy; failures leave
the previous preview intact. Existing IDs and analysis relationships are preserved.
Applying requires explicit opt-in, closed Rekordbox, unchanged source fingerprints,
verified integrity and a complete data-directory backup. Database/XML replacements
roll back on failure. Restoration verifies the backup and makes a rescue backup.
External audio is never changed by the Rekordbox adapter.

A custom title bar retains native Windows resizing/maximising/snap hit targets.
The gradient is painted once behind the interface; controls use flat surfaces.
Harmony swatches stay anchored between independently edited background endpoints.
GitHub release updates require a matching SHA-256 digest before installation.

Manual transfer is available. Desktop/laptop synchronisation is deferred.
