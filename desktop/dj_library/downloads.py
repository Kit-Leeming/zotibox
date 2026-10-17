"""Persistent serial queue around ZahBoy's sequential downloader."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import math
from string import Formatter
from pathlib import Path

from .catalogue import AUDIO_EXTENSIONS, file_hash, read_metadata, atomic_json
from .matching import match_track
from .spotify import dpapi

MANAGED = {"ROOT_PATH", "ROOT_PODCAST_PATH", "CREDENTIALS_LOCATION", "API_CREDENTIALS_LOCATION", "SAVE_CREDENTIALS",
           "DOWNLOAD_PARENT_ALBUM", "TEMP_DOWNLOAD_DIR", "SONG_ARCHIVE_LOCATION", "DISABLE_SONG_ARCHIVE",
           "DISABLE_DIRECTORY_ARCHIVES", "SKIP_EXISTING", "EXPORT_M3U8", "M3U8_LOCATION", "LYRICS_LOCATION",
           "IMPORT_ZMD_LOCATION", "EXPORT_ZMD_LOCATION",
           "STANDARD_INTERFACE", "PRINT_SPLASH"}
PATTERNS={'OUTPUT','OUTPUT_SINGLE','OUTPUT_ALBUM','OUTPUT_PLAYLIST_EXT','OUTPUT_LIKED_SONGS'}
NAME_FIELDS={'artist','album','song_name','song_title','title','song_id','isrc','playlist','playlist_num','album_num'}


def validate_pattern(value):
    if not value:return value
    if value.startswith(('/','\\')) or '\\' in value or ':' in value or any(part in {'.','..',''} for part in value.split('/')):
        raise ValueError('Filename patterns must stay inside the download folder; use / for subfolders')
    for literal,field,spec,conversion in Formatter().parse(value):
        if field is not None and (field not in NAME_FIELDS or spec or conversion):raise ValueError('Unknown filename field or format: '+str(field))
    return value


def schema():
    return json.loads((Path(__file__).parent / "resources" / "zotify-settings.json").read_text(encoding="utf-8"))


def validated_options(options):
    values = {}
    for key, value in options.items():
        definition = schema().get(key)
        if not definition:
            raise ValueError(f"Unknown Zotify setting: {key}")
        if key in MANAGED:
            continue
        kind = definition["type"]
        if kind == "bool":
            if str(value).lower() not in {"true", "false"}:
                raise ValueError(f"{key} must be True or False")
            value = str(value).lower() == "true"
        elif kind == "int":
            value = int(value)
        elif kind == "float":
            value = float(value)
            if not math.isfinite(value):raise ValueError(f'{key} must be a finite number')
        else:
            value = str(value)
        if key in PATTERNS:validate_pattern(value)
        if key=='REDIRECT_PORT' and not 1<=value<=65535:raise ValueError('Redirect port must be between 1 and 65535')
        if key=='REDIRECT_ADDRESS':
            import ipaddress
            if value!='localhost' and not (ipaddress.ip_address(value).version==4 and ipaddress.ip_address(value).is_loopback):raise ValueError('The login listener must use a local loopback address')
        if key=='API_CLIENT_ID' and value and not re.fullmatch('[A-Za-z0-9]{32}',value):raise ValueError('Enter a valid Spotify Client ID, or leave it empty to use the Spotify account setting')
        if key in {'DOWNLOAD_RATE_LIMITER','FETCH_TIMEOUT','FETCH_DELAY','RETRY_DELAY','RETRY_ATTEMPTS','BULK_WAIT_TIME','MAX_FILENAME_LENGTH'} and value<0:raise ValueError(f'{key} cannot be negative')
        if key in {'REDIRECT_TIMEOUT','CHUNK_SIZE','SEARCH_QUERY_SIZE'} and value<=0:raise ValueError(f'{key} must be greater than zero')
        values[key] = value
    return values


def worker_config(catalogue, stage,options=None):
    config = {key: value["default"] for key, value in schema().items()}
    config.update(API_CLIENT_LEGACY=False,DOWNLOAD_RATE_LIMITER=1.0,OPTIMIZED_DOWNLOADING=False,REDIRECT_ADDRESS='127.0.0.1',REDIRECT_PORT=4381,CUSTOM_FFMEPG_ARGS='')
    config.update(validated_options(catalogue.get_setting("zotify_options", {}) if options is None else options))
    config.update({"ROOT_PATH": str(stage / "music"), "ROOT_PODCAST_PATH": str(stage / "music"),
        "CREDENTIALS_LOCATION": str(stage / "credentials.json"), "API_CREDENTIALS_LOCATION": str(stage / "api-credentials.json"),
        "SAVE_CREDENTIALS": True,
        "DOWNLOAD_PARENT_ALBUM": False, "TEMP_DOWNLOAD_DIR": str(stage / "temp"), "OUTPUT": "{song_id}",
        "OUTPUT_SINGLE": "{song_id}", "DISABLE_SONG_ARCHIVE": True, "DISABLE_DIRECTORY_ARCHIVES": True,
        "SONG_ARCHIVE_LOCATION": str(stage / "archive"), "SKIP_EXISTING": True, "EXPORT_M3U8": False,
        "M3U8_LOCATION": str(stage), "LYRICS_LOCATION": str(stage / "music"), "EXPORT_ZMD_LOCATION": str(stage / "zmd"),
        "IMPORT_ZMD_LOCATION": str(stage / "zmd"), "API_CLIENT_ID": config.get('API_CLIENT_ID') or catalogue.get_setting("spotify_client_id", ""),
        "STANDARD_INTERFACE": True, "PRINT_SPLASH": False, "DEBUG": False})
    return config


def command(config, track_id=None, authenticate=False,lossy_fallback='mp3'):
    if lossy_fallback not in {'native','mp3'}:raise ValueError('Unsupported lossy fallback format')
    args = ["--zotify-worker", "--config", str(config)]
    args += ['--lossy-fallback',lossy_fallback]
    args += ["--authenticate"] if authenticate else ["--track", track_id]
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).with_name("ZotiBox Worker.exe")), *args]
    return [sys.executable, str(Path(__file__).parents[1] / "launcher.py"), *args]


def ffmpeg_path(name):
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "ffmpeg" / f"{name}.exe"
    bundled = Path(__file__).parents[2] / "tools" / "ffmpeg" / f"{name}.exe"
    return bundled if bundled.exists() else Path(shutil.which(name) or name)


def safe_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:140] or "Track"


def download_destination(music,track,audio,options=None,sources=None):
    options=options or {};source=next(iter(sources or []),{})
    kind='OUTPUT_ALBUM' if source.get('id','').startswith('spotify:album:') else 'OUTPUT_PLAYLIST_EXT' if source.get('id','').startswith(('spotify:playlist:','local:')) else 'OUTPUT_SINGLE'
    pattern=options.get('OUTPUT') or options.get(kind) or '{artist} - {song_name}';validate_pattern(pattern)
    fields={key:safe_name(str(value)) for key,value in {'artist':track.get('artist') or 'Unknown artist','album':track.get('album') or 'Unknown album',
        'song_name':track['title'],'song_title':track['title'],'title':track['title'],'song_id':track.get('id',''),'isrc':track.get('isrc',''),
        'playlist':source.get('name') or 'Downloads','playlist_num':source.get('position',0)+1,'album_num':source.get('position',0)+1}.items()}
    relative='/'.join(safe_name(part) for part in pattern.format_map(fields).split('/'))
    destination=music/(relative+audio.suffix.lower());stem=destination.stem;parent=destination.parent
    if not destination.resolve().is_relative_to(music.resolve()):raise ValueError('The output path is outside the download folder')
    parent.mkdir(parents=True,exist_ok=True)
    number=2
    while destination.exists() and file_hash(destination)!=file_hash(audio):
        destination=parent/(f"{stem} ({number})"+audio.suffix.lower())
        number+=1
    return destination


class DownloadEngine:
    def __init__(self, catalogue, event=lambda *args: None):
        self.catalogue = catalogue
        self.event = event
        self.cancel = threading.Event()
        self.process = None
        self.process_lock = threading.Lock()
        self.run_lock = threading.Lock()

    def stop(self):
        self.cancel.set()
        with self.process_lock:
            if self.process and self.process.poll() is None:
                # Also stop FFmpeg subprocesses belonging to this worker.
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)

    def _credentials(self, stage, save=False):
        path = self.catalogue.workspace / ".local" / "zotify-session.bin"
        if save:
            data = {}
            for name in ["credentials.json", "api-credentials.json"]:
                file = stage / name
                if file.exists():
                    try:
                        data[name] = json.loads(file.read_text(encoding="utf-8"))
                    except ValueError:
                        pass  # A terminated worker may have left a partial credentials write.
            if data:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(dpapi(json.dumps(data).encode()))
                temporary.replace(path)
            for name in ["credentials.json", "api-credentials.json"]:
                (stage / name).unlink(missing_ok=True)
        elif path.exists():
            try:
                data = json.loads(dpapi(path.read_bytes(), True))
            except Exception as exc:
                raise ValueError("Saved Zotify login is unavailable on this PC; reconnect in Setup") from exc
            for name in ["credentials.json", "api-credentials.json"]:
                if name in data:
                    atomic_json(stage / name, data[name])

    def invoke(self, stage, track_id=None, authenticate=False, job_id=None,fresh=False,options=None):
        staging_root=(self.catalogue.workspace/".local"/"staging").resolve()
        if stage.resolve()==staging_root or not stage.resolve().is_relative_to(staging_root):
            raise ValueError("Worker staging path is outside this database's staging folder")
        stage.mkdir(parents=True, exist_ok=True)
        if not fresh:self._credentials(stage)
        config = stage / "config.json"
        config_values=worker_config(self.catalogue,stage,options);atomic_json(config,config_values)
        environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        environment["PATH"] = str(ffmpeg_path("ffmpeg").parent) + os.pathsep + environment.get("PATH", "")
        source_format = "unverified"
        with self.process_lock:
            if self.cancel.is_set():
                raise InterruptedError("Queue paused")
            self.process = subprocess.Popen(command(config, track_id, authenticate,self.catalogue.get_setting('lossy_fallback','mp3')), cwd=stage, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        errors = []
        timed_out = threading.Event()
        process = self.process
        def expire():
            if process.poll() is None:
                timed_out.set()
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        timer = threading.Timer(max(420,float(config_values['REDIRECT_TIMEOUT'])*2+60), expire)
        timer.daemon = True
        timer.start()
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue  # Never show raw dependency logs that might contain login data.
            kind = message.get("event")
            if kind == "progress" and job_id:
                rate=float(config_values['DOWNLOAD_RATE_LIMITER']);pacing='real-time pacing' if rate==1 else 'no added pacing' if rate==0 else f'pacing ×{rate:g}'
                self.catalogue.update_download(job_id, progress=message["percent"], message='Downloading · '+pacing)
            elif kind == "auth_url":
                if job_id:
                    self.catalogue.update_download(job_id,message="Waiting for browser login; return to Setup if needed")
                self.event("auth_url", message["url"])
            elif kind == "status":
                if job_id:
                    self.catalogue.update_download(job_id, message=message["message"])
                self.event("status",message["message"])
            elif kind == "connected":
                timer.cancel()
                self.event("connected", "")
            elif kind == "source":
                source_format = message["format"]
                offered=message.get("available_formats",[])
                self.catalogue.set_setting("spotify_formats_"+track_id,offered)
                if job_id:self.catalogue.update_download(job_id,message=source_description(source_format))
            elif kind == "error":
                errors.append(message.get("message", "Zotify failed"))
                atomic_json(self.catalogue.workspace/'.local'/'last-worker-error.json',message.get('diagnostic',{}))
            self.event("refresh", "")
        exit_code = self.process.wait()
        timer.cancel()
        with self.process_lock:
            self.process = None
        self._credentials(stage, save=True)
        for name in ["credentials.json", "api-credentials.json"]:
            (stage / name).unlink(missing_ok=True)
        if timed_out.is_set():
            raise RuntimeError("Zotify connection timed out. Reconnect in Setup and complete both browser logins.")
        if self.cancel.is_set():
            raise InterruptedError("Queue paused")
        if exit_code:
            raise RuntimeError(errors[-1] if errors else "Zotify could not complete the request; reconnect or retry")
        return source_format

    def cleanup(self, stage):
        staging_root = (self.catalogue.workspace / ".local" / "staging").resolve()
        resolved = stage.resolve()
        if resolved != staging_root and resolved.is_relative_to(staging_root):
            shutil.rmtree(resolved, ignore_errors=True)

    def validate_audio(self,audio,seconds):
        if audio.suffix.lower()=='.flac':
            from mutagen.flac import FLAC
            try:FLAC(audio)
            except Exception as exc:raise ValueError('Output is not a valid FLAC stream; nothing published') from exc
        with self.process_lock:
            if self.cancel.is_set():raise InterruptedError('Queue paused')
            process=subprocess.Popen([str(ffmpeg_path('ffmpeg')),'-v','error','-i',str(audio),'-f','null','-'],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
            self.process=process
        try:
            process.communicate(timeout=max(120,int(seconds)*2))
            if self.cancel.is_set():raise InterruptedError('Queue paused')
            if process.returncode:raise ValueError('Downloaded audio failed decode validation')
        except subprocess.TimeoutExpired:
            process.kill();process.communicate();raise ValueError('Audio validation timed out')
        finally:
            with self.process_lock:
                if self.process is process:self.process=None

    def authenticate(self,fresh=False):
        import uuid
        stage = self.catalogue.workspace / ".local" / "staging" / ("login-" + str(uuid.uuid4()))
        try:
            self.invoke(stage, authenticate=True,fresh=fresh)
            return "Zotify connected"
        finally:
            self.cleanup(stage)

    def run_queue(self):
        if not self.run_lock.acquire(blocking=False):
            raise RuntimeError("The download queue is already running")
        try:
            self.cancel.clear()
            while True:
                if self.cancel.is_set():
                    break
                job = self.catalogue.claim_next_download()
                if job is None:
                    break
                import uuid
                try:
                    if str(uuid.UUID(job["id"]))!=job["id"]:
                        raise ValueError("Noncanonical queue ID")
                except ValueError:
                    self.catalogue.update_download(job["id"],state="failed",message="Invalid queue identity; no files were touched")
                    self.event("refresh","")
                    continue
                track = self.catalogue.remote_track(job["spotify_id"])
                matched = match_track(self.catalogue, track)
                if matched["state"] == "matched":
                    self.catalogue.update_download(job["id"], state="complete", progress=100, message="Already in local library")
                    self.event("refresh", "")
                    continue
                if matched["state"] == "unavailable":
                    self.catalogue.update_download(job["id"], state="failed", message="Known local file is unavailable; remap its folder before downloading")
                    continue
                self.catalogue.update_download(job["id"], state="running", progress=0, message="Connecting to Zotify")
                self.event("refresh", "")
                stage = self.catalogue.workspace / ".local" / "staging" / job["id"]
                try:
                    options=validated_options(self.catalogue.get_setting('zotify_options',{}))
                    self.cleanup(stage)
                    source_format = self.invoke(stage, track["id"], job_id=job["id"],options=options)
                    files = [path for path in (stage / "music").rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS]
                    if len(files) != 1:
                        raise RuntimeError("Zotify returned no single-track output; no file was added")
                    audio = files[0]
                    metadata = read_metadata(audio)
                    if track["duration"] and abs(metadata["duration"] - track["duration"]) > 3:
                        raise ValueError("Downloaded duration does not match; output held for review")
                    if track["isrc"] and metadata["isrc"] and track["isrc"].upper() != metadata["isrc"].upper():
                        raise ValueError("Downloaded ISRC does not match; output held for review")
                    self.catalogue.update_download(job["id"], message="Validating audio")
                    self.validate_audio(audio,metadata['duration'])
                    music = Path(self.catalogue.get_setting("download_folder", str(self.catalogue.workspace / "Music"))).resolve()
                    music.mkdir(parents=True, exist_ok=True)
                    # The destination is one shared music store, independent of playlist names.
                    destination = download_destination(music, track, audio,options,job.get('sources'))
                    if destination.exists():
                        if file_hash(destination) != file_hash(audio):
                            raise ValueError("Destination exists with different content; nothing was replaced")
                    else:
                        partial=music/(".zotibox-"+job["id"]+".part")
                        try:
                            with audio.open("rb") as source, partial.open("xb") as target:
                                shutil.copyfileobj(source,target)
                            if file_hash(audio)!=file_hash(partial):
                                raise ValueError("Download copy checksum failed")
                            if self.cancel.is_set():raise InterruptedError('Queue paused')
                            partial.rename(destination)
                        finally:
                            partial.unlink(missing_ok=True)
                    if file_hash(audio) != file_hash(destination):
                        raise ValueError("Copied download checksum failed; retained for review")
                    lyrics = list((stage / "music").rglob("*.lrc"))
                    lyric_destination = destination.with_suffix(".lrc")
                    if len(lyrics) == 1 and not lyric_destination.exists():
                        with lyrics[0].open("rb") as source, lyric_destination.open("xb") as target:
                            shutil.copyfileobj(source, target)
                    if options.get('ALBUM_ART_TO_FILE'):
                        images=[p for p in (stage/'music').rglob('*') if p.is_file() and p.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}]
                        if len(images)==1:
                            cover=destination.with_suffix(images[0].suffix.lower())
                            if not cover.exists():
                                with images[0].open('rb') as source,cover.open('xb') as target:shutil.copyfileobj(source,target)
                    root_id = next((root["id"] for root in self.catalogue.roots() if root["path"] and music.is_relative_to(Path(root["path"]).resolve())), None)
                    root_id = root_id or self.catalogue.add_root(music)
                    self.catalogue.index_file(destination, root_id, cancel=self.cancel)
                    rows = [row for row in self.catalogue.files() if self.catalogue.resolve(row) == destination]
                    if not rows:
                        raise ValueError("Downloaded file was not indexed")
                    self.catalogue.set_setting("audio_source_"+rows[0]["sha256"],source_description(source_format))
                    self.catalogue.decide_match(track["id"], rows[0]["recording_id"], "download", f"Downloaded exact Spotify ID; source {source_format}")
                    self.catalogue.update_download(job["id"], state="complete", progress=100,
                        message=f"Complete · {source_description(source_format)}", output=str(destination))
                    self.cleanup(stage)
                except InterruptedError:
                    current = next((row for row in self.catalogue.download_jobs() if row["id"] == job["id"]),None)
                    if current and current["state"] not in {"cancelled","removing"}:
                        self.catalogue.update_download(job["id"], state="paused", message="Paused; resume to retry this track")
                    break
                except Exception as exc:
                    self.catalogue.update_download(job["id"], state="failed", message=str(exc), output=str(stage / "music") if (stage / "music").exists() else "")
                finally:
                    if stage.exists():
                        self._credentials(stage, save=True)
                        current=next((r for r in self.catalogue.download_jobs() if r['id']==job['id']),None)
                        if current and current['state']=='removing':self.cleanup(stage)
                self.event("refresh", "")
            return "Queue paused" if self.cancel.is_set() else "Queue finished"
        finally:
            self.catalogue.finish_queue_removals()
            self.run_lock.release()


def source_description(format):
    if format in {"FLAC_FLAC","FLAC_FLAC_24BIT"}:return f"Lossless Spotify source: {format}"
    if format=="unverified":return "Source quality unverified (file extension does not prove lossless)"
    return f"Lossy Spotify source: {format}"
