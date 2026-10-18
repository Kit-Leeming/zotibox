"""WAV-to-FLAC conversion verified against decoded PCM, never lossy encoding."""
import json,os,subprocess,uuid
from pathlib import Path
from .catalogue import file_hash,atomic_json
from .downloads import ffmpeg_path
from .audio_tags import copy_tags

def run(args):
    result=subprocess.run(args,capture_output=True,timeout=600,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:raise ValueError('Lossless conversion or audio verification failed; original WAV retained')
    return result.stdout

def pcm_hash(path):
    return run([str(ffmpeg_path('ffmpeg')),'-v','error','-nostdin','-i',str(path),'-map','0:a:0','-c:a','pcm_s32le','-f','hash','-hash','sha256','-']).strip()

def wav_to_flac(path):
    path=Path(path);digest=file_hash(path);target=path.with_suffix('.flac')
    if target.exists():
        try:
            saved=json.loads(target.with_suffix('.flac.json').read_text(encoding='utf-8'))
            if saved['source']==digest and saved['output']==file_hash(target):return target
        except (OSError,ValueError,KeyError):pass
    if target.exists():target=target.with_stem(target.stem+' - '+digest[:12])
    receipt=target.with_suffix('.flac.json')
    if target.exists():
        try:
            saved=json.loads(receipt.read_text(encoding='utf-8'))
            if saved['source']==digest and saved['output']==file_hash(target):return target
        except (OSError,ValueError,KeyError):pass
        raise ValueError('A different FLAC already exists; no files were replaced')
    info=json.loads(run([str(ffmpeg_path('ffprobe')),'-v','error','-select_streams','a:0','-show_entries','stream=codec_name,bits_per_sample','-of','json',str(path)]))['streams'][0]
    if not info['codec_name'].startswith(('pcm_s','pcm_u')) or not 0<int(info.get('bits_per_sample',0))<=24:
        raise ValueError('Only integer PCM WAV up to 24 bits can be converted here without reducing precision. Original retained.')
    temporary=target.with_name('.zotibox-flac-'+uuid.uuid4().hex+'.flac')
    try:
        run([str(ffmpeg_path('ffmpeg')),'-v','error','-nostdin','-i',str(path),'-map','0:a:0','-map_metadata','0','-c:a','flac','-compression_level','8',str(temporary)])
        copy_tags(path,temporary)
        if pcm_hash(path)!=pcm_hash(temporary) or file_hash(path)!=digest:raise ValueError('PCM verification failed; original WAV retained')
        temporary.rename(target);atomic_json(receipt,{'source':digest,'output':file_hash(target),'conversion':'WAV to FLAC; decoded PCM verified identical'})
        return target
    finally:temporary.unlink(missing_ok=True)
