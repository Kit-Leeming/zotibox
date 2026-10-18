# Local music organisation

Open **Local Music → Organise**. Pause downloads and finish scanning before changing files.

## Consolidate or move

1. Choose **Consolidate all scanned music** to copy into the configured download folder, or **Move download folder** to copy the existing folder to a separate destination. Windows folder moves use Robocopy and include covers, lyrics and other sidecars.
2. Select **Prepare preview**, check the paths, then **Copy and prepare relinks**. Different files are never overwritten. Every copied file is checked against its SHA-256 checksum.
3. Open **View / apply Rekordbox preview**. Close Rekordbox and apply the changes after reviewing them. Track IDs, cues and analysis are retained during relocation; analysis file paths are updated too.
4. Reopen Organise, select **Open saved operation**, then **Remove verified originals**. This uses the Recycle Bin and refuses removal if files changed or Rekordbox still references an original. Empty the Recycle Bin separately when satisfied.

Saved operations and errors remain available in the database’s `.local/organisation` folder. An interrupted copy retains originals and completed copies. Prepare a new preview to continue copying; pending native changes must first be applied or discarded. Do not manually remove files based only on a failed operation.

## Duplicates

**Duplicates** shows exact SHA-256 copies and separate possible duplicates with matching artist/title and similar duration. Select a group and the copy to keep, then review its plan. Possible duplicates require confirmation because their audio differs. The chosen recording becomes the preferred file for exports and later syncs.

Native duplicate identities can be merged while retaining playlist memberships. If exact copies have competing cues or analysis, select **Keep only the selected copy’s native cues / analysis** and confirm, or resolve the conflict in Rekordbox first. Choosing a different audio version explicitly discards the other version’s native cues at Apply; the selected version keeps its own cues and analysis. Tracks referenced by native hot-cue banks require manual resolution in Rekordbox before merging. A backup is required before native writes.

## Extended mixes

Right-click a local track or playlist entry → **Use a preferred / extended version**. Choose the downloaded extended mix and save. Spotify membership and order continue to use the regular Spotify track; local exports, playback and later Rekordbox syncs use the saved version instead.

To replace the regular recording throughout the existing native collection, enable that option in the dialog and confirm the cue/analysis change. Apply the native preview, then remove the original through its saved operation. Hiding a superseded file is optional and does not delete it. Clear a saved preference in the same dialog to show the original again; this does not undo an already-applied native merge or restore removed files.

**Find extended versions** is available on a playlist’s context menu or Organise → Preferred versions. It compares local files and can search Spotify for the selected track or whole scope. Candidates require review. Spotify cannot report versions absent from its catalogue, so this does not search private download networks or guarantee availability elsewhere.

## Older Ogg files and artwork

Choose **Convert old Ogg / unsupported files to MP3 320** in Organise. Metadata and embedded artwork are copied. After applying native relinks, remove verified originals through the saved operation. Re-encoding lossy audio does not improve its source quality.

An unverified compatibility copy means its saved receipt is missing or no longer matches. ZotiBox compares it with a fresh verified conversion: matching audio can be adopted without overwriting metadata; otherwise a separately named verified copy is used and the unverified file is retained for review.

**Artwork** fills missing MP3/FLAC covers from conversion originals, saved Spotify identities, or exact ISRC/album matches using [MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API) and the [Cover Art Archive](https://musicbrainz.org/doc/Cover_Art_Archive/API). Existing covers stay intact. Original files are backed up under `.local/tag-backups`, and compressed audio packets must remain unchanged. Uncertain or unavailable artwork is skipped.
