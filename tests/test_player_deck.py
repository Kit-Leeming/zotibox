"""Player behavior with synthetic audio and synthetic read-only Rekordbox analysis."""
import hashlib
import struct
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QVBoxLayout

from dj_library.player import MusicPlayer, Waveform, waveform, read_analysis
from dj_library.themes import make_theme, stylesheet
from test_round6 import app, pump_until


def synthetic_audio(path, seconds=3):
    rate = 8000
    t = np.arange(int(rate * seconds)) / rate
    # Three well-separated spectral bands, repeated for longer visual fixtures.
    frequency = np.array([125, 1000, 3000])[np.floor(t * 3 / min(seconds, 3)).astype(int) % 3]
    values = (np.sin(2 * np.pi * frequency * t) * .6 * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, rate, len(values), 'NONE', 'not compressed'))
        audio.writeframes(values.tobytes())
    return path


def synthetic_analysis(path, seconds=4):
    count = int(seconds * 2)
    entries = b''.join(struct.pack('>HHI', i % 4 + 1, 12000, 125 + 500 * i) for i in range(count))
    grid = b'PQTZ' + struct.pack('>IIIII', 24, 24 + len(entries), 0, 0x80000, count) + entries
    # Native RGB bitfields have a deliberately asymmetric red scale in the parser.
    colors = [(7 << 13) | (1 << 10) | (2 << 7) | (31 << 2),
              (1 << 13) | (7 << 10) | (2 << 7) | (16 << 2)]
    data = struct.pack('>2H', *colors)
    detail = b'PWV5' + struct.pack('>IIIII', 24, 24 + len(data), 2, 2, 0) + data
    body = grid + detail
    path.write_bytes(b'PMAI' + struct.pack('>6I', 28, 28 + len(body), 0, 0, 0, 0) + body)
    return path


class PlayerOwner(QWidget):
    def __init__(self):
        super().__init__()
        self.theme = make_theme()
        self.settings = {}
        self.catalogue = SimpleNamespace(get_setting=lambda key, default=None: self.settings.get(key, default),
                                        set_setting=lambda key, value: self.settings.__setitem__(key, value))
        self.status = QLabel()
        layout = QVBoxLayout(self)
        self.player = MusicPlayer(self)
        layout.addWidget(self.player)

    def button(self, text, callback, icon=None):
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button


def test_zoom_maps_pointer_to_visible_time_and_overview_stays_full_length():
    app()
    detail = Waveform(make_theme)
    overview = Waveform(make_theme, overview=True)
    for view in (detail, overview):
        view.resize(800, view.height())
        view.length = 120
        view.position = 60_000
        view.zoom = 8
    assert detail.visible_range() == (52.5, 67.5)
    assert detail.seconds_at(0) == 52.5
    assert detail.seconds_at(400) == 60
    assert overview.visible_range() == (0, 120)
    assert overview.seconds_at(200) == 30
    emitted = []
    detail.seek.connect(emitted.append)
    event = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(200, 30), QPointF(200, 30),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    detail.mousePressEvent(event)
    assert emitted == [56250]
    detail.position = 0
    assert detail.visible_range() == (0, 15)
    detail.position = 120_000
    assert detail.visible_range() == (105, 120)


def test_real_decode_frequency_bands_have_125_frames_per_second(tmp_path):
    data, length = waveform(synthetic_audio(tmp_path / 'bands.wav'), threading.Event(), detailed=True)
    assert length == 3
    assert len(data['peaks']) == 375
    assert data['bands'].shape == (375, 3)
    for band in range(3):
        # Ignore transitions where FFT windows include two adjacent sections.
        energy = data['bands'][band * 125 + 15:band * 125 + 110].mean(axis=0)
        assert int(energy.argmax()) == band
        assert energy[band] > np.delete(energy, band).max() * 3


def test_overview_has_visible_waveform_height():
    app()
    theme = make_theme()
    view = Waveform(lambda: theme, overview=True)
    view.resize(800, 32)
    view.length = 120
    view.peaks = [.8] * 500
    view.mode = 'Accent'
    image = view.grab().toImage()
    colored = sum(image.pixelColor(100, y).name() == theme['accent']['start'] for y in range(image.height()))
    assert colored >= 12


def test_native_grid_and_color_analysis_are_parsed_without_mutation(tmp_path):
    path = synthetic_analysis(tmp_path / 'ANLZ0000.EXT')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    data = read_analysis([path])
    assert data['beats'][0] == {'time': .125, 'beat': 1, 'bpm': 120}
    assert data['beats'][4] == {'time': 2.125, 'beat': 1, 'bpm': 120}
    assert len(data['beats']) == 8
    heights, colors = data['native_colors']
    assert heights[0] == 1 and heights[1] == pytest.approx(16 / 31)
    assert colors[0] == pytest.approx([1, 1 / 7, 2 / 7])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_color_modes_persist_and_render_differently():
    app()
    owner = PlayerOwner()
    player = owner.player
    player.resize(1100, 240)
    player.wave.resize(1000, 112)
    player.wave_ready(None, {'peaks': [.8] * 500, 'bands': np.tile([10., 2., 1.], (500, 1)),
                           'beats': [], 'native_colors': (np.ones(500), np.tile([.1, .9, .3], (500, 1)))}, 120)
    renders = {}
    try:
        for mode in ('RGB', '3 band', 'Blue', 'Accent', 'Rekordbox RGB'):
            player.colors.setCurrentText(mode)
            assert player.wave.mode == player.overview.mode == mode
            assert owner.settings.get('player_wave_color', 'RGB') == mode
            image = player.wave.grab().toImage()
            renders[mode] = image.pixelColor(100, 50).name()
        assert len(set(renders.values())) == 5
    finally:
        assert player.shutdown()
        owner.close()


def test_transport_tracks_actual_muted_media_state_and_readonly_grid(tmp_path):
    app()
    owner = PlayerOwner()
    player = owner.player
    player.audio.setMuted(True)
    path = synthetic_audio(tmp_path / 'playback.wav', 8)
    analysis = synthetic_analysis(tmp_path / 'ANLZ0000.DAT', 8)
    try:
        player.load(path, 'Synthetic playback test', autoplay=False, analysis=[analysis])
        pump_until(lambda: not player.jobs)
        pump_until(lambda: player.media.duration() > 0)
        assert player.grid.isEnabled() and len(player.wave.beats) == 16
        assert not player.play.playing and player.play.accessibleName() == 'Play'
        player.toggle()
        pump_until(lambda: player.media.playbackState() == QMediaPlayer.PlaybackState.PlayingState and player.media.position() > 50)
        assert player.play.playing and player.play.accessibleName() == 'Pause'
        player.toggle()
        pump_until(lambda: player.media.playbackState() == QMediaPlayer.PlaybackState.PausedState)
        assert not player.play.playing and player.play.toolTip() == 'Play'
        assert player.play.width() == player.play.height() == 42 and not player.play.text()
        player.zoom.setValue(4)
        assert player.wave.zoom == player.overview.zoom == 16
        player.grid.setChecked(False)
        assert not player.wave.grid
    finally:
        assert player.shutdown()
        owner.close()


def test_transport_controls_fit_1280_window_even_with_cue_selector():
    app()
    owner = PlayerOwner()
    owner.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    owner.setStyleSheet(stylesheet(owner.theme))
    owner.resize(1280, 260)
    player = owner.player
    player.title.setText('A long artist and title that should shrink before the player controls')
    player.cue_choice.show()
    player.show()
    owner.show()
    app().processEvents()
    try:
        assert owner.width() == 1280
        # Leave room for the app's expanded navigation sidebar too.
        assert player.minimumSizeHint().width() < 1080
        bar = player.layout().itemAt(0).layout()
        for index in range(bar.count()):
            widget = bar.itemAt(index).widget()
            if widget is not None and widget.isVisible():
                assert widget.geometry().right() < player.width()
    finally:
        assert player.shutdown()
        owner.close()
