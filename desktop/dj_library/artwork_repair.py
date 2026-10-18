"""Fill missing MP3/FLAC artwork using exact remote identities."""
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
import requests
from PySide6.QtGui import QImage
from .audio_tags import picture,embed
from .catalogue import file_hash,atomic_json
from .downloads import ffmpeg_path


def audio_hash(path):
    result=subprocess.run([str(ffmpeg_path('ffmpeg')),'-v','error','-nostdin','-i',str(path),'-map','0:a:0','-c:a','copy','-f','hash','-hash','sha256','-'],
        capture_output=True,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:raise ValueError('Could not verify audio packets')
    return result.stdout.strip()


def embed_verified(catalogue,row,data,mime):
    path=catalogue.resolve(row)
    if picture(path):return False
    if file_hash(path)!=row['sha256']:raise ValueError('File changed; scan again before repairing artwork')
    image=QImage.fromData(data)
    if len(data)>8*1024*1024 or image.isNull():raise ValueError('Cover image is invalid or too large')
    temporary=path.with_name('.zotibox-art-'+uuid.uuid4().hex+path.suffix)
    backup=catalogue.workspace/'.local'/'tag-backups'/row['sha256']/(path.name)
    backup.parent.mkdir(parents=True,exist_ok=True)
    try:
        shutil.copy2(path,temporary);before=audio_hash(path);embed(temporary,data,mime)
        if audio_hash(temporary)!=before:raise ValueError('Audio changed during artwork repair; original retained')
        if not backup.exists():shutil.copy2(path,backup)
        if file_hash(backup)!=row['sha256'] or file_hash(path)!=row['sha256']:raise ValueError('Original changed during artwork repair')
        os.replace(temporary,path);stat=path.stat();digest=file_hash(path)
        with catalogue.connect() as db:
            db.execute('UPDATE files SET sha256=?,size=?,mtime_ns=? WHERE id=?',(digest,stat.st_size,stat.st_mtime_ns,row['id']))
            catalogue.change(db,'file',row['id'],'embed missing artwork')
        provenance=catalogue.get_setting('audio_source_'+row['sha256'],None)
        if provenance:catalogue.set_setting('audio_source_'+digest,provenance)
        receipt=path.with_suffix(path.suffix+'.json')
        if receipt.is_file():
            import json
            value=json.loads(receipt.read_text(encoding='utf-8'))
            if value.get('output')==row['sha256']:value['output']=digest;atomic_json(receipt,value)
        sources=catalogue.get_setting('compatibility_sources',{})
        if os.path.normcase(str(path)) in sources:
            sources[os.path.normcase(str(path))].update(output=digest,size=stat.st_size,mtime_ns=stat.st_mtime_ns);catalogue.set_setting('compatibility_sources',sources)
        catalogue.file_revision+=1;return True
    finally:temporary.unlink(missing_ok=True)


def repair(catalogue,rows,spotify=None,progress=lambda *a:None,cancel=None,session=None):
    cancel=cancel or threading.Event();session=session or requests.Session()
    result={'embedded':0,'skipped':0,'errors':[]}
    with catalogue.connect() as db:identities={r[0]:r[1] for r in db.execute('SELECT recording_id,spotify_id FROM matches WHERE recording_id IS NOT NULL')}
    source_files={r['sha256']:r for r in catalogue.files() if r['available']}
    conversions=catalogue.get_setting('compatibility_sources',{})
    for index,row in enumerate(rows):
        if cancel.is_set():raise InterruptedError('Artwork repair paused; completed artwork retained')
        path=catalogue.resolve(row)
        try:
            if not path or path.suffix.lower() not in {'.mp3','.flac'} or picture(path):result['skipped']+=1;continue
            conversion=conversions.get(os.path.normcase(str(path)),{})
            original=source_files.get(conversion.get('source'))
            original_cover=picture(catalogue.resolve(original)) if original else None
            if original_cover:
                with catalogue.scan_lock:
                    if embed_verified(catalogue,row,*original_cover):result['embedded']+=1
                continue
            url=None
            identity=identities.get(row['recording_id']) or (identities.get(original['recording_id']) if original else None)
            if spotify and identity:
                try:
                    track=spotify.request('tracks/'+identity);images=track.get('album',{}).get('images',[])
                    if images:url=images[0]['url']
                except Exception:pass  # Exact ISRC lookup remains usable without Spotify login.
            if not url and row.get('isrc'):
                if cancel.wait(1.05):raise InterruptedError('Artwork lookup paused')
                response=session.get('https://musicbrainz.org/ws/2/recording/',params={'query':'isrc:'+row['isrc'],'fmt':'json','limit':10},
                    headers={'User-Agent':'ZotiBox/0.13.0 (https://github.com/Kit-Leeming/zotibox)'},timeout=20)
                response.raise_for_status()
                from .matching import normalized
                candidates=[r for r in response.json().get('recordings',[]) if row['isrc'].upper() in {i.upper() for i in r.get('isrcs',[])}
                    and normalized(r.get('title',''))==normalized(row['title'])]
                releases={r['id'] for c in candidates for r in c.get('releases',[]) if normalized(r.get('title',''))==normalized(row.get('album',''))}
                if len(releases)==1:url='https://coverartarchive.org/release/'+next(iter(releases))+'/front-500'
            if not url:result['skipped']+=1;continue
            with session.get(url,timeout=20,stream=True) as response:
                if response.status_code==404:result['skipped']+=1;continue
                response.raise_for_status();data=bytearray()
                for chunk in response.iter_content(65536):
                    if cancel.is_set():raise InterruptedError('Artwork lookup paused')
                    data.extend(chunk)
                    if len(data)>8*1024*1024:raise ValueError('Cover image exceeds 8 MB')
                mime='image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
            with catalogue.scan_lock:
                if embed_verified(catalogue,row,bytes(data),mime):result['embedded']+=1
        except InterruptedError:raise
        except Exception as exc:result['errors'].append(row['title']+': '+str(exc))
        finally:progress(index+1,row['title'])
    return result
