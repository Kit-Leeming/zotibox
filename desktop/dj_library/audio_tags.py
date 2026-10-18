"""Embedded artwork helpers shared by conversions and missing-cover repair."""
import base64
from pathlib import Path
from mutagen import File
from mutagen.flac import Picture
from mutagen.id3 import APIC
from mutagen.mp3 import MP3


def picture(path):
    audio=File(path)
    if getattr(audio,'pictures',None):return audio.pictures[0].data,audio.pictures[0].mime
    tags=getattr(audio,'tags',None)
    if tags:
        if hasattr(tags,'getall') and tags.getall('APIC'):
            value=tags.getall('APIC')[0];return value.data,value.mime
        if tags.get('metadata_block_picture'):
            value=Picture(base64.b64decode(tags['metadata_block_picture'][0]));return value.data,value.mime
        if tags.get('covr'):
            data=bytes(tags['covr'][0]);return data,'image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
    return None


def embed(path,data,mime):
    if Path(path).suffix.lower()=='.mp3':
        audio=MP3(path)
        if audio.tags is None:audio.add_tags()
        audio.tags.add(APIC(encoding=3,mime=mime,type=3,desc='Cover',data=data));audio.save()
    elif Path(path).suffix.lower()=='.flac':
        audio=File(path);value=Picture();value.type=3;value.mime=mime;value.data=data
        audio.add_picture(value);audio.save()
    else:raise ValueError('Artwork embedding supports MP3 and FLAC')


def copy_tags(source,target,missing_only=False):
    source_tags=File(source,easy=True)
    output=File(target,easy=True)
    if output.tags is None:output.add_tags()
    for key,values in (getattr(source_tags,'tags',None) or {}).items():
        if hasattr(getattr(source_tags,'tags',None),'getall'):
            key={'TIT2':'title','TPE1':'artist','TPE2':'albumartist','TALB':'album','TCON':'genre','TDRC':'date','TRCK':'tracknumber','TPOS':'discnumber','TSRC':'isrc','TBPM':'bpm','TCOM':'composer'}.get(key.split(':')[0])
            if not key:continue
            values=[str(value) for value in getattr(values,'text',[]) ]
        if missing_only and output.get(key):continue
        try:output[key]=values
        except (KeyError,ValueError,TypeError):pass  # Container-specific tags have no ID3 equivalent.
    output.save()
    image=picture(source)
    if not image:
        for suffix in ('.jpg','.jpeg','.png'):
            sidecar=Path(source).with_suffix(suffix)
            if sidecar.is_file() and sidecar.stat().st_size<=8*1024*1024:
                image=(sidecar.read_bytes(),'image/png' if suffix=='.png' else 'image/jpeg');break
    if image and not (missing_only and picture(target)):embed(target,*image)


def repair_missing(catalogue,source,target,expected_hash):
    """Repair older verified conversions without changing audio or existing tags."""
    import os,shutil,uuid
    from .catalogue import file_hash
    from .artwork_repair import audio_hash
    original=File(source,easy=True);output=File(target,easy=True)
    missing=any(not output.get(key) for key in (getattr(original,'tags',None) or {}) if key in {'title','artist','album','date','tracknumber','discnumber','genre','isrc','bpm'})
    if not missing and (picture(target) or not picture(source)):return expected_hash
    temporary=target.with_name('.zotibox-tags-'+uuid.uuid4().hex+'.mp3')
    try:
        shutil.copy2(target,temporary);copy_tags(source,temporary,missing_only=True)
        new_hash=file_hash(temporary)
        if new_hash==expected_hash:return expected_hash
        if file_hash(target)!=expected_hash or audio_hash(temporary)!=audio_hash(target):raise ValueError('Compatibility metadata repair could not be verified; existing copy retained')
        backup=catalogue.workspace/'.local'/'tag-backups'/expected_hash/target.name;backup.parent.mkdir(parents=True,exist_ok=True)
        if not backup.exists():shutil.copy2(target,backup)
        if file_hash(backup)!=expected_hash:raise ValueError('Metadata backup could not be verified')
        os.replace(temporary,target);stat=target.stat()
        matching=[f['id'] for f in catalogue.files() if f['sha256']==expected_hash and catalogue.resolve(f)==target]
        with catalogue.connect() as db:
            for id_ in matching:db.execute('UPDATE files SET sha256=?,size=?,mtime_ns=? WHERE id=?',(new_hash,stat.st_size,stat.st_mtime_ns,id_))
        catalogue.file_revision+=1;return new_hash
    finally:temporary.unlink(missing_ok=True)
