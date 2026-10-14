"""Local-file audition player with asynchronously decoded waveform and read-only cues."""
from array import array
import math
import os
from pathlib import Path
import subprocess
import threading
from mutagen import File
from PySide6.QtCore import Qt,QThread,Signal,QUrl
from PySide6.QtGui import QColor,QPainter,QPen
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QSlider,QComboBox,QSizePolicy
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from .downloads import ffmpeg_path

def waveform(path,cancel):
    audio=File(path);length=float(audio.info.length) if audio is not None and audio.info is not None else 0
    chunk=max(128,math.ceil(length*8000/1600))
    process=subprocess.Popen([str(ffmpeg_path('ffmpeg')),'-v','error','-i',str(path),'-vn','-ac','1','-ar','8000','-f','s16le','-'],
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    peaks=[]
    try:
        while True:
            if cancel.is_set():raise InterruptedError()
            data=process.stdout.read(chunk*2)
            if not data:break
            samples=array('h');samples.frombytes(data[:len(data)//2*2])
            peaks.append(max((abs(n) for n in samples),default=0)/32768)
            if len(peaks)>10000:raise ValueError('Audio duration could not be determined accurately')
        if process.wait()!=0:raise ValueError('Waveform decoding failed')
        return peaks,length
    finally:
        if process.poll() is None:process.kill()
        process.wait();process.stdout.close()

class WaveJob(QThread):
    ready=Signal(str,object,float);failed=Signal(str,str)
    def __init__(self,path,parent):super().__init__(parent);self.path=str(path);self.cancel=threading.Event()
    def run(self):
        try:
            peaks,length=waveform(self.path,self.cancel)
            if not self.cancel.is_set():self.ready.emit(self.path,peaks,length)
        except InterruptedError:pass
        except Exception as exc:self.failed.emit(self.path,str(exc))

class Waveform(QWidget):
    seek=Signal(int)
    def __init__(self,get_theme):
        super().__init__();self.get_theme=get_theme;self.peaks=[];self.length=0;self.position=0;self.cues=[]
        self.setMinimumHeight(70);self.setMaximumHeight(90);self.setMouseTracking(True)
    def paintEvent(self,event):
        p=QPainter(self);theme=self.get_theme();p.fillRect(self.rect(),QColor(theme['surface']['start']))
        middle=self.height()/2+6;amplitude=self.height()/2-17
        p.setPen(QPen(QColor(theme['accent']['start']),1))
        for x in range(self.width()):
            if not self.peaks:break
            index=min(len(self.peaks)-1,int(x*len(self.peaks)/max(1,self.width())))
            value=self.peaks[index]*amplitude;p.drawLine(x,int(middle-value),x,int(middle+value))
        if self.length:
            for cue in self.cues:
                x=int(cue['position']/self.length*self.width());p.setPen(QColor('#ffbd69'));p.drawLine(x,15,x,self.height())
                if cue.get('end',0)>cue['position']:
                    end=int(cue['end']/self.length*self.width());p.fillRect(x,15,max(1,end-x),self.height()-15,QColor(255,189,105,35))
            x=int(self.position/1000/self.length*self.width());p.setPen(QPen(QColor(theme['foreground']['start']),2));p.drawLine(x,0,x,self.height())
        p.end()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.length:
            self.seek.emit(int(max(0,min(1,event.position().x()/max(1,self.width())))*self.length*1000))
    def mouseMoveEvent(self,event):
        if self.length:
            seconds=max(0,min(1,event.position().x()/max(1,self.width())))*self.length
            if event.buttons() & Qt.MouseButton.LeftButton:self.seek.emit(int(seconds*1000))
            nearby=next((c for c in self.cues if abs(c['position']-seconds)<self.length*6/max(1,self.width())),None)
            self.setToolTip(nearby['label'] if nearby else f'{int(seconds)//60}:{int(seconds)%60:02d}')

class MusicPlayer(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.path=None;self.jobs=[]
        self.media=QMediaPlayer(self);self.audio=QAudioOutput(self);self.audio.setVolume(.65);self.media.setAudioOutput(self.audio)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,6,0,0);bar=QHBoxLayout()
        self.play=owner.button('Play / pause',self.toggle,'SP_MediaPlay');bar.addWidget(self.play)
        bar.addWidget(owner.button('Stop',self.media.stop,'SP_DialogCancelButton'))
        self.title=QLabel();self.title.setTextFormat(Qt.TextFormat.PlainText);self.title.setMinimumWidth(80);self.title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);bar.addWidget(self.title,1)
        self.time=QLabel('0:00 / 0:00');bar.addWidget(self.time)
        self.cue_choice=QComboBox();self.cue_choice.setFixedWidth(180);self.cue_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.cue_choice.setMinimumContentsLength(10);self.cue_choice.activated.connect(self.jump_cue);bar.addWidget(self.cue_choice)
        bar.addWidget(QLabel('Volume'));volume=QSlider(Qt.Orientation.Horizontal);volume.setFixedWidth(100);volume.setRange(0,100);volume.setValue(65);volume.valueChanged.connect(lambda n:self.audio.setVolume(n/100));bar.addWidget(volume)
        bar.addWidget(owner.button('Close player',self.dismiss,'SP_DialogCancelButton'));layout.addLayout(bar)
        self.wave=Waveform(lambda:owner.theme);self.wave.seek.connect(self.media.setPosition);layout.addWidget(self.wave)
        self.media.positionChanged.connect(self.position_changed);self.media.durationChanged.connect(self.duration_changed)
        self.media.errorOccurred.connect(lambda *_:owner.status.setText('Playback: '+self.media.errorString()))
        self.hide()
    def load(self,path,title,cues=(),autoplay=True):
        path=Path(path)
        if not path.is_file():raise ValueError('This track needs an available local file for playback')
        for job in self.jobs:job.cancel.set()
        self.path=str(path.resolve());self.wave.peaks=[];self.wave.length=0;self.wave.position=0;self.wave.cues=list(cues)
        self.title.setText(title);self.title.setToolTip(title);self.cue_choice.clear();self.cue_choice.addItem('Jump to cue…',None)
        for cue in cues:self.cue_choice.addItem(cue['label'],int(cue['position']*1000))
        self.cue_choice.setVisible(bool(cues));self.show()
        self.media.setSource(QUrl.fromLocalFile(self.path))
        job=WaveJob(self.path,self);self.jobs.append(job);job.ready.connect(self.wave_ready)
        job.failed.connect(lambda path,error:self.owner.status.setText(error) if path==self.path else None)
        job.finished.connect(lambda:self.finished(job));job.start()
        if autoplay:self.media.play()
    def finished(self,job):
        if job in self.jobs:self.jobs.remove(job)
        job.deleteLater()
    def wave_ready(self,path,peaks,length):
        if path==self.path:self.wave.peaks=peaks;self.wave.length=length;self.wave.update()
    def position_changed(self,value):
        self.wave.position=value;self.wave.update();self.update_time()
    def duration_changed(self,value):
        self.wave.length=value/1000;self.wave.update();self.update_time()
    def update_time(self):
        def stamp(ms):return f'{int(ms)//60000}:{int(ms)//1000%60:02d}'
        self.time.setText(stamp(self.media.position())+' / '+stamp(self.media.duration()))
    def jump_cue(self,index):
        value=self.cue_choice.itemData(index)
        if value is not None:self.media.setPosition(value)
    def toggle(self):
        if self.media.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.media.pause()
        elif self.path:self.media.play()
    def dismiss(self):
        self.media.stop();self.media.setSource(QUrl());self.path=None
        for job in self.jobs:job.cancel.set()
        self.hide()
    def shutdown(self):
        self.media.stop();self.media.setSource(QUrl())
        for job in list(self.jobs):job.cancel.set()
        return all(job.wait(2000) for job in list(self.jobs))
