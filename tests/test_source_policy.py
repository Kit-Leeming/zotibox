"""Stream selection tests without Spotify sessions or music library changes."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from librespot.audio import SuperAudioFormat
from librespot.audio.decoders import AudioQuality, FormatOnlyAudioQuality
from librespot.proto.Metadata_pb2 import AudioFile

from dj_library.zotify_worker import install_source_policy

def test_invalid_flac_container_rejected_before_publication(tmp_path):
    from dj_library.catalogue import Catalogue
    from dj_library.downloads import DownloadEngine
    from test_catalogue import audio
    path=audio(tmp_path/'NotFlac.flac')
    engine=DownloadEngine(Catalogue(tmp_path/'Database'))
    with pytest.raises(ValueError,match='not a valid FLAC'):engine.validate_audio(path,1)


def bridge(formats, *, preference='auto', custom='-c:a flac', configured='flac', converter=None, unavailable_lossless=False,fallback='native'):
    class Config:
        get_download_format = staticmethod(lambda: configured)
        get_download_qual_pref = staticmethod(lambda: preference)
        arguments = custom
        bitrate = '128k'
        get_transcode_bitrate = classmethod(lambda cls: cls.bitrate)
        get_custom_ffmpeg_args = classmethod(lambda cls: cls.arguments.split())
        get_ffmpeg_log_level = staticmethod(lambda: 'error')

        @classmethod
        @contextmanager
        def temporary_config(cls, key, value):
            attribute = {'CUSTOM_FFMEPG_ARGS': 'arguments', 'TRANSCODE_BITRATE': 'bitrate'}[key]
            previous = getattr(cls, attribute)
            setattr(cls, attribute, value)
            try:
                yield
            finally:
                setattr(cls, attribute, previous)

    class Quality(FormatOnlyAudioQuality):
        pass

    previous = Quality(AudioQuality.VERY_HIGH, SuperAudioFormat.VORBIS)
    zotify = SimpleNamespace(CONFIG=Config, DOWNLOAD_QUALITY=previous,
        parse_dl_quality=lambda pref: (True, Quality(AudioQuality.LOSSLESS, SuperAudioFormat.FLAC) if pref == 'lossless' else previous, None))
    stream = SimpleNamespace(closed=False)
    stream.stream = lambda: stream
    stream.close = lambda: setattr(stream, 'closed', True)
    events = []

    class Track:
        _codec = property(lambda self: 'flac' if configured == 'flac' else 'libmp3lame')
        _ext = property(lambda self: configured)

        def fetch_stream(self):
            self.fetches += 1
            if unavailable_lossless and zotify.DOWNLOAD_QUALITY.preferred == AudioQuality.LOSSLESS:
                return None
            zotify.DOWNLOAD_QUALITY.get_file([AudioFile(format=f, file_id=b'file') for f in formats])
            return stream

        def convert_audio_format(self, temporary, path):
            self.conversion = (self._codec, Config.arguments, path)
            return '0s'

        def download(self, parents):
            # This reproduces the pinned fork's important ordering: filename
            # before fetch, then conversion, then tagging and registration.
            self.path = 'track.' + self._ext
            self.fetch_stream()
            self.convert_audio_format('track.tmp', self.path)
            self.registered = self.path
            self.tag_extension = self._ext

    if converter is not None:
        Track.convert_audio_format = converter
    install_source_policy(Track, Quality, AudioFile, zotify, lambda event, **kw: events.append((event, kw)),fallback=fallback)
    track = Track()
    track.fetches = 0
    return track, zotify, stream, events, previous


@pytest.mark.parametrize('source', [AudioFile.FLAC_FLAC, AudioFile.FLAC_FLAC_24BIT])
@pytest.mark.parametrize('fallback', ['native','mp3'])
def test_actual_flac_source_retains_flac_and_quality_preferences(source,fallback):
    track, zotify, stream, events, previous = bridge([source, AudioFile.OGG_VORBIS_320],fallback=fallback)
    track.download([])
    assert track.path == track.registered == 'track.flac'
    assert track.conversion[0] == 'flac'
    assert events[0][1]['format'] == AudioFile.Format.Name(source)
    assert track.fetches == 1 and stream.closed
    assert zotify.DOWNLOAD_QUALITY is previous
    assert not hasattr(track, '_zotibox_stream')


def test_auto_lossless_unavailable_copies_best_vorbis_without_second_encoding():
    track, zotify, stream, events, previous = bridge([AudioFile.OGG_VORBIS_96, AudioFile.OGG_VORBIS_320])
    track.download([])
    assert track.path == track.registered == 'track.ogg'
    assert track.tag_extension == 'ogg'
    assert track.conversion == ('copy', '', 'track.ogg')
    assert events[0][1]['format'] == 'OGG_VORBIS_320'
    assert zotify.CONFIG.arguments == '-c:a flac'
    assert zotify.DOWNLOAD_QUALITY is previous
    assert track.fetches == 1 and stream.closed


def test_explicit_lossy_quality_preserved_even_if_flac_exists():
    track, zotify, _, events, _ = bridge([AudioFile.FLAC_FLAC, AudioFile.OGG_VORBIS_320], preference='very_high')
    track.download([])
    assert track.conversion == ('copy', '', 'track.ogg')
    assert events[0][1]['format'] == 'OGG_VORBIS_320'


def test_unknown_source_cannot_be_published_as_flac():
    track, zotify, stream, _, previous = bridge([])
    with pytest.raises(ValueError, match='refusing unverified FLAC'):
        track.download([])
    assert not hasattr(track, 'registered')
    assert stream.closed and zotify.DOWNLOAD_QUALITY is previous


def test_explicit_mp3_request_keeps_existing_encoder_setting():
    track, _, _, events, _ = bridge([AudioFile.OGG_VORBIS_320], configured='mp3', custom='-ar 44100')
    track.download([])
    assert track.conversion == ('libmp3lame', '-ar 44100', 'track.mp3')
    assert events[0][1]['format'] == 'OGG_VORBIS_320'


def test_unavailable_lossless_stream_retries_normal_quality_and_restores_preferences():
    track, zotify, stream, _, previous = bridge([AudioFile.FLAC_FLAC, AudioFile.OGG_VORBIS_320], unavailable_lossless=True)
    track.download([])
    assert track.fetches == 2
    assert track.conversion == ('copy', '', 'track.ogg')
    assert stream.closed and zotify.DOWNLOAD_QUALITY is previous


def test_failed_conversion_cannot_register_fork_mislabeled_fallback():
    track, zotify, stream, _, previous = bridge([AudioFile.FLAC_FLAC], converter=lambda *args: None)
    with pytest.raises(RuntimeError, match='no output was published'):
        track.download([])
    assert not hasattr(track, 'registered')
    assert stream.closed and zotify.DOWNLOAD_QUALITY is previous


@pytest.mark.parametrize('fallback', ['native','mp3'])
def test_real_pinned_converter_preserves_vorbis_or_encodes_320k_mp3(tmp_path, monkeypatch,fallback):
    """Run the real fork's FFmpeg conversion against disposable synthetic audio."""
    import json
    import os
    import subprocess
    import zotify.api as upstream
    from dj_library.downloads import ffmpeg_path

    ffmpeg = ffmpeg_path('ffmpeg').resolve()
    probe = ffmpeg_path('ffprobe').resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PATH', str(ffmpeg.parent) + os.pathsep + os.environ['PATH'])
    subprocess.run([str(ffmpeg), '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=0.15',
                    '-c:a', 'libvorbis', '-f', 'ogg', 'track.tmp'], check=True)

    def packets(path):
        result = subprocess.run([str(probe), '-v', 'error', '-select_streams', 'a', '-show_packets',
            '-show_data_hash', 'sha256', '-show_streams', '-of', 'json', path], capture_output=True, check=True)
        data = json.loads(result.stdout)
        return data['streams'][0]['codec_name'], [packet['data_hash'] for packet in data['packets']]

    before = packets('track.tmp')
    track, zotify, _, events, _ = bridge([AudioFile.OGG_VORBIS_320], converter=upstream.DLContent.convert_audio_format,fallback=fallback)
    track.run_ffm = upstream.DLContent.run_ffm
    monkeypatch.setattr(upstream, 'Zotify', zotify)
    monkeypatch.setattr(upstream, 'Printer', SimpleNamespace(logger=lambda *args: None))
    track.download([])
    if fallback=='native':
        assert track.path == 'track.ogg'
        assert packets(track.path) == before
    else:
        assert track.path == 'track.mp3'
        result=subprocess.run([str(probe),'-v','error','-show_streams','-of','json',track.path],capture_output=True,check=True)
        info=json.loads(result.stdout)['streams'][0]
        assert info['codec_name']=='mp3' and int(info['bit_rate'])==320000
        assert any('lossy re-encoding' in data.get('message','') for event,data in events)
        assert zotify.CONFIG.bitrate=='128k'
    assert before[0] == 'vorbis'
    assert not (tmp_path / 'track.flac').exists()


def test_worker_command_defaults_to_mp3_with_native_opt_in(tmp_path):
    from dj_library.downloads import command
    default=command(tmp_path/'config.json','a'*22)
    assert default[default.index('--lossy-fallback')+1]=='mp3'
    native=command(tmp_path/'config.json','a'*22,lossy_fallback='native')
    assert native[native.index('--lossy-fallback')+1]=='native'
