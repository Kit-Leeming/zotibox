"""Copy local extras into a portable ZIP; never upload or transcode user audio."""
import json
import os
from pathlib import Path
import re
import uuid
import zipfile

INSTRUCTIONS='''Local files in Spotify

Extract the Music folder first.
Desktop: Spotify Settings > Library > Show Local Files > Add a source.
Select the extracted Music folder, then add songs from Local Files to your playlist in Spotify.

Android: copy Music to your phone using USB file transfer, or your own file storage app.
iPhone: copy the audio to On My iPhone > Spotify using Files, or Spotify file sharing over USB.
Phone: Spotify Settings > Apps and devices > Local audio files. Open Library > Local Files.
Add the local songs to your playlist in Spotify on that device as needed.

Spotify's Web API cannot add local files to playlists. This ZIP does not upload audio,
automatically transfer files to your phone, or guarantee matching desktop/mobile local URIs.
The M3U8 is for compatible players; Spotify does not import it as a Spotify playlist.
https://support.spotify.com/article/local-files/
'''

def export_extras(catalogue,playlist_id,destination):
    playlist=next(p for p in catalogue.playlists() if p['id']==playlist_id)
    entries=[e for e in catalogue.entries(playlist_id) if not e['spotify_id']]
    if not entries:raise ValueError('This playlist has no local extras')
    destination=Path(destination)
    if destination.exists():raise ValueError('Choose a new ZIP filename; existing files are not overwritten')
    if destination.suffix.lower()!='.zip':raise ValueError('Choose a .zip filename')
    temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.part')
    files=catalogue.files();names=set();copied={};missing=[];lines=['#EXTM3U']
    try:
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
            for entry in entries:
                recording=entry['recording_id']
                if recording not in copied:
                    candidates=[f for f in files if f['recording_id']==recording and f['available']]
                    path=next((p for f in candidates if (p:=catalogue.resolve(f)) and p.is_file()),None)
                    if not path:missing.append(entry['title']);continue
                    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',path.stem).strip(' .')[:160] or 'Track'
                    target=name+path.suffix;counter=2
                    while target.casefold() in names:target=f'{name} ({counter}){path.suffix}';counter+=1
                    names.add(target.casefold());target='Music/'+target;archive.write(path,target);copied[recording]=target
                label=(entry['artist']+' - '+entry['title']).replace('\n',' ').replace('\r',' ')
                lines.extend([f"#EXTINF:{int(entry['duration'])},{label}",copied[recording]])
            if not copied:raise ValueError('No available local extras to export')
            archive.writestr('Local extras.m3u8','\n'.join(lines)+'\n');archive.writestr('Spotify local files.txt',INSTRUCTIONS)
            archive.writestr('manifest.json',json.dumps({'playlist':playlist['name'],'files':len(copied),'missing':missing},ensure_ascii=False,indent=2))
        # Windows rename fails rather than silently replacing an existing destination.
        if destination.exists():raise ValueError('Destination was created while exporting; choose another filename')
        os.rename(temporary,destination)
        return {'files':len(copied),'missing':missing,'path':str(destination)}
    finally:
        if temporary.exists():temporary.unlink()
