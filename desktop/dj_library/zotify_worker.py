"""JSON progress bridge; actual downloads stay in the unmodified ZahBoy fork."""
import argparse
import json
import sys


def install_source_policy(track_class, quality_class, audio_file, zotify, emit, fallback='mp3'):
    """Choose the container from the selected stream, before Zotify builds its path.

    A .flac preference must never turn a Vorbis/AAC/MP3 stream into pretend
    lossless audio. Lossy fallback is MP3 320 kbps for DJ compatibility, or an
    optional native stream copy. The worker handles one track at a time; this
    scoped adapter retains Zotify's downloader, tagging and genuine FLAC output.
    """
    from librespot.audio import SuperAudioFormat
    from librespot.audio.decoders import AudioQuality
    from contextlib import ExitStack
    if fallback not in {'native', 'mp3'}:
        raise ValueError('Unsupported lossy fallback format')

    original_get = quality_class.get_file
    original_download = track_class.download
    original_fetch = track_class.fetch_stream
    original_codec, original_ext = track_class._codec, track_class._ext
    original_convert = track_class.convert_audio_format
    selected = {}

    def get_file(self, files):
        result = original_get(self, files)
        if result is None and self.preferred == AudioQuality.LOSSLESS:
            # A track can expose only lossy representations. Preserve the best
            # available Vorbis stream rather than failing or encoding it as FLAC.
            result = original_get(quality_class(AudioQuality.VERY_HIGH, SuperAudioFormat.VORBIS), files)
        if result is not None:
            selected['format'] = audio_file.Format.Name(result.format)
            emit('source', format=selected['format'], available_formats=sorted({
                audio_file.Format.Name(f.format) for f in files if f.HasField('format')}))
        return result

    quality_class.get_file = get_file
    track_class._codec = property(lambda self: getattr(self, '_zotibox_output', (None, None))[0] or original_codec.fget(self))
    track_class._ext = property(lambda self: getattr(self, '_zotibox_output', (None, None))[1] or original_ext.fget(self))

    def fetch(self):
        return self._zotibox_stream if hasattr(self, '_zotibox_stream') else original_fetch(self)

    def convert(self, temporary, path):
        # Custom encoder arguments cannot override the lossless safety fallback.
        with ExitStack() as context:
            output = getattr(self, '_zotibox_output', None)
            if output:
                context.enter_context(zotify.CONFIG.temporary_config('CUSTOM_FFMEPG_ARGS', ''))
                if output[0] == 'libmp3lame':
                    context.enter_context(zotify.CONFIG.temporary_config('TRANSCODE_BITRATE', '320k'))
            result = original_convert(self, temporary, path)
        if result is None and zotify.CONFIG.get_download_format().lower() == 'flac':
            # The fork otherwise renames failed conversions to .ogg, including
            # a FLAC source. Never publish an incorrectly labelled fallback.
            raise RuntimeError('Audio format conversion failed; no output was published')
        return result

    def download(self, parents):
        requested = zotify.CONFIG.get_download_format().lower()
        if requested != 'flac':
            return original_download(self, parents)
        previous_quality = zotify.DOWNLOAD_QUALITY
        stream = None
        selected.clear()
        try:
            if zotify.CONFIG.get_download_qual_pref() in {'auto', 'lossless'}:
                zotify.DOWNLOAD_QUALITY = zotify.parse_dl_quality('lossless')[1]
            stream = original_fetch(self)
            if stream is None and zotify.DOWNLOAD_QUALITY.preferred == AudioQuality.LOSSLESS:
                emit('status', message='Lossless stream unavailable; trying the highest available lossy quality')
                selected.clear()
                zotify.DOWNLOAD_QUALITY = zotify.parse_dl_quality('auto')[1]
                stream = original_fetch(self)
            if stream is None:
                return
            source = selected.get('format', '')
            if source not in {'FLAC_FLAC', 'FLAC_FLAC_24BIT'}:
                extension = 'ogg' if source.startswith('OGG_VORBIS_') else 'mp3' if source.startswith('MP3_') else 'm4a' if source.startswith('AAC_') else None
                if extension is None:
                    raise ValueError('Unknown source audio format; refusing unverified FLAC conversion')
                if fallback == 'mp3':
                    self._zotibox_output = ('libmp3lame', 'mp3')
                    emit('status', message=f'{source}: encoding MP3 320 kbps for DJ compatibility; lossy re-encoding, not lossless')
                else:
                    self._zotibox_output = ('copy', extension)
                    emit('status', message=f'{source}: preserving the lossy source as .{extension}; no FLAC transcoding')
            self._zotibox_stream = stream
            return original_download(self, parents)
        finally:
            zotify.DOWNLOAD_QUALITY = previous_quality
            for name in ('_zotibox_stream', '_zotibox_output'):
                self.__dict__.pop(name, None)
            if stream is not None:
                try:
                    stream.stream().close()
                except Exception:
                    pass  # Cleanup must not hide the original download failure.

    track_class.fetch_stream = fetch
    track_class.convert_audio_format = convert
    track_class.download = download


def main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--track")
    parser.add_argument("--authenticate", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument('--lossy-fallback', choices=('native', 'mp3'), default='mp3')
    args = parser.parse_args(arguments)
    output = sys.stdout
    def emit(event, **values):
        output.write(json.dumps({"event": event, **values}) + "\n")
        output.flush()
    class Quiet:
        def write(self, *_):
            return 0
        def flush(self):
            pass
        def isatty(self):
            return False
    sys.stdout = sys.stderr = Quiet()
    try:
        from zotify.config import LoginHandler, Zotify
        from .zotify_oauth import LoopbackOAuth, LoginError
        from zotify.const import SCOPES
        from zotify.termoutput import Printer
        from librespot.audio.decoders import FormatOnlyAudioQuality
        from librespot.proto.Metadata_pb2 import AudioFile
        from pathlib import Path
        from zotify.__main__ import main as upstream_main
        if args.check:
            import zotify.api
            emit("ready", version=Zotify.VERSION)
            return 0
        login_errors = []
        class WorkerOAuth(LoopbackOAuth):
            def flow(self):
                try:
                    return super().flow()
                except LoginError as exc:
                    login_errors.append(str(exc))
                    raise
        def create_oauth(client_id):
            if login_errors:
                raise LoginError(login_errors[-1])
            redirect = f"http://{Zotify.CONFIG.get_oauth_address()}:{Zotify.CONFIG.get_oauth_port()}/login"
            return WorkerOAuth(client_id, redirect, emit,timeout=Zotify.CONFIG.get_oauth_timeout() or 180).set_scopes(SCOPES)
        LoginHandler.create_oauth = staticmethod(create_oauth)
        from .zotify_login import install_login_adapter,failure_message
        install_login_adapter(LoginHandler,Zotify,create_oauth,emit)
        def login(cls, arguments):
            emit("status", message="Connecting to Zotify; checking saved login")
            cls.attempt_login(arguments)
            if not cls.login_success():
                raise LoginError(login_errors[-1] if login_errors else "Zotify could not sign in to Spotify. Reconnect in Setup and complete both browser login steps.")
            cls.save_credentials()
            emit("connected")
            return cls.SESSION
        LoginHandler.login = classmethod(login)
        # Retain both native stream credentials and developer OAuth separately.
        def save_credentials(cls):
            if cls.SESSION:
                Path(Zotify.CONFIG.get_credentials_location()).write_text(json.dumps(cls.SESSION.credentials()), encoding="utf-8")
            if cls.OAUTH:
                cls.OAUTH.save_creds(Zotify.CONFIG.get_api_credentials_location())
        LoginHandler.save_credentials = classmethod(save_credentials)
        original_pbar = Printer.pbar
        def pbar(*parameters, **keywords):
            bar = original_pbar(*parameters, **keywords)
            class Progress:
                written = 0
                def update(self, count=1):
                    result = bar.update(count)
                    self.written += count
                    if keywords.get("unit") == "B" and getattr(bar, "total", 0):
                        emit("progress", percent=min(99, self.written / bar.total * 100))
                    return result
                def __iter__(self):
                    return iter(bar)
                def __getattr__(self, name):
                    return getattr(bar, name)
            return Progress()
        Printer.pbar = staticmethod(pbar)
        from zotify.api import Track
        install_source_policy(Track, FormatOnlyAudioQuality, AudioFile, Zotify, emit, args.lossy_fallback)
        if args.authenticate:
            import zotify.app
            def login_complete(*_):
                Zotify.end()
                emit("authenticated")
            zotify.app.client = login_complete
        elif not args.track or len(args.track) != 22 or not args.track.isalnum():
            raise ValueError("Invalid Spotify track ID")
        sys.argv = ["zotify", "--config-location", args.config]
        if not args.authenticate:
            sys.argv += [f"https://open.spotify.com/track/{args.track}"]
        upstream_main()
        emit("complete")
        return 0
    except (Exception, SystemExit) as exc:
        # Avoid forwarding upstream token/credential exception text.
        from .zotify_login import failure_message
        import traceback
        frames=[{'file':frame.filename.replace('\\','/').split('/')[-1],'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(exc.__traceback__)[-8:]]
        emit("error", message=failure_message(exc),diagnostic={'type':type(exc).__name__,'frames':frames})
        return 1
