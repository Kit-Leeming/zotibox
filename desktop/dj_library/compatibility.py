"""Verified DJ-compatible copies for formats Rekordbox cannot import."""
import json
import os
from pathlib import Path
import subprocess
import uuid
from mutagen.mp3 import MP3
from .catalogue import file_hash,atomic_json
from .downloads import ffmpeg_path

SUPPORTED={'.mp3','.wav','.aiff','.aif','.flac','.m4a'}

def compatible_path(catalogue,path):
    path=Path(path)
    if path.suffix.lower() in SUPPORTED:return path
    if path.suffix.lower() not in {'.ogg','.oga','.opus','.aac','.wma','.alac','.mp4'}:
        raise ValueError(f'Rekordbox cannot import {path.suffix} files')
    digest=file_hash(path)
    music=Path(catalogue.get_setting('download_folder',str(catalogue.workspace/'Music')))
    folder=music/'Rekordbox compatible';folder.mkdir(parents=True,exist_ok=True)
    # Source identity makes the copy reusable without changing any existing file.
    target=folder/(path.stem[:130]+' - '+digest[:12]+'.mp3');receipt=target.with_suffix('.mp3.json')
    def remember(output_hash):
        stat=target.stat();sources=catalogue.get_setting('compatibility_sources',{})
        sources[os.path.normcase(str(target))]={'source':digest,'output':output_hash,'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}
        catalogue.set_setting('compatibility_sources',sources);return target
    if target.exists():
        try:
            saved=json.loads(receipt.read_text(encoding='utf-8'))
            if saved['source']==digest and saved['output']==file_hash(target):MP3(target);return remember(saved['output'])
        except (OSError,ValueError,KeyError):pass
        raise ValueError(f'A compatibility copy already exists but cannot be verified: {target.name}')
    temporary=folder/('.zotibox-'+uuid.uuid4().hex+'.mp3')
    try:
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        result=subprocess.run([str(ffmpeg_path('ffmpeg')),'-v','error','-nostdin','-i',str(path),'-map','0:a:0','-vn','-map_metadata','0',
            '-c:a','libmp3lame','-b:a','320k',str(temporary)],capture_output=True,timeout=600,creationflags=flags)
        if result.returncode:raise ValueError('Could not make a Rekordbox-compatible MP3 copy')
        MP3(temporary)
        check=subprocess.run([str(ffmpeg_path('ffmpeg')),'-v','error','-nostdin','-i',str(temporary),'-f','null','-'],capture_output=True,timeout=600,creationflags=flags)
        if check.returncode or file_hash(path)!=digest:raise ValueError('Compatibility copy validation failed; original retained')
        temporary.rename(target);output_hash=file_hash(target);atomic_json(receipt,{'source':digest,'output':output_hash,'conversion':'MP3 320 kbps; lossy source re-encoded'})
        catalogue.set_setting('audio_source_'+output_hash,'Lossy compatibility copy · MP3 320 kbps')
        return remember(output_hash)
    finally:temporary.unlink(missing_ok=True)
