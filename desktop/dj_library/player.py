"""Local-file audition player with asynchronously decoded waveform and read-only cues."""
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
import numpy as np
from PySide6.QtCore import QRectF,QPointF
from PySide6.QtGui import QPolygonF
from PySide6.QtWidgets import QCheckBox

def waveform(path,cancel,detailed=False):
    audio=File(path);length=float(audio.info.length) if audio is not None and audio.info is not None else 0
    chunk=16000
    process=subprocess.Popen([str(ffmpeg_path('ffmpeg')),'-v','error','-i',str(path),'-vn','-ac','1','-ar','8000','-f','s16le','-'],
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    peaks=[];bands=[]
    try:
        while True:
            if cancel.is_set():raise InterruptedError()
            data=process.stdout.read(chunk*2)
            if not data:break
            samples=np.frombuffer(data[:len(data)//2*2],dtype='<i2').astype(float)/32768
            # Frequency envelopes at 125 frames/s keep transient detail when zoomed.
            pad=(-len(samples))%64
            if pad:samples=np.pad(samples,(0,pad))
            frames=samples.reshape(-1,64);spectrum=np.abs(np.fft.rfft(frames*np.hanning(64),axis=1))
            peaks.extend(np.max(np.abs(frames),axis=1).tolist())
            bands.extend(np.stack([spectrum[:,:3].sum(axis=1),spectrum[:,3:20].sum(axis=1),spectrum[:,20:].sum(axis=1)],axis=1).tolist())
            if len(peaks)>125*60*180:raise ValueError('Waveform preview supports tracks up to three hours')
        if process.wait()!=0:raise ValueError('Waveform decoding failed')
        return ({'peaks':peaks,'bands':np.asarray(bands),'beats':[],'native_colors':None} if detailed else peaks),length
    finally:
        if process.poll() is None:process.kill()
        process.wait();process.stdout.close()

def read_analysis(paths):
    """Read existing native analysis only. No grid estimates and no native writes."""
    from pyrekordbox.anlz import AnlzFile
    result={'beats':[],'native_colors':None}
    for path in paths:
        path=Path(path)
        if not path.is_file() or path.stat().st_size>32*1024*1024:continue
        analysis=AnlzFile.parse_file(path)
        for tag in analysis.tags:
            if tag.type=='PQTZ':
                beats,bpms,times=tag.get()
                result['beats']=[{'time':float(t),'beat':int(b),'bpm':float(p)} for b,p,t in zip(beats,bpms,times)]
            elif tag.type=='PWV5':
                heights,colors=tag.get();colors=colors.astype(float);colors[:,0]/=14;colors[:,1:]/=7
                result['native_colors']=(heights,np.clip(colors,0,1))
    return result

class WaveJob(QThread):
    ready=Signal(str,object,float);failed=Signal(str,str)
    def __init__(self,path,parent,analysis=()):super().__init__(parent);self.path=str(path);self.cancel=threading.Event();self.analysis=analysis
    def run(self):
        try:
            peaks,length=waveform(self.path,self.cancel,True)
            if self.analysis:
                try:peaks.update(read_analysis(self.analysis))
                except Exception:peaks['analysis_error']=True
            if not self.cancel.is_set():self.ready.emit(self.path,peaks,length)
        except InterruptedError:pass
        except Exception as exc:self.failed.emit(self.path,str(exc))

class Waveform(QWidget):
    seek=Signal(int);zoomRequested=Signal(int)
    def __init__(self,get_theme,overview=False):
        super().__init__();self.get_theme=get_theme;self.peaks=[];self.length=0;self.position=0;self.cues=[]
        self.bands=None;self.beats=[];self.native_colors=None;self.grid=True;self.mode='RGB';self.zoom=1;self.overview=overview
        self.setFixedHeight(32 if overview else 112);self.setMouseTracking(True)
    def visible_range(self):
        if self.overview or self.zoom<=1:return 0,max(.001,self.length)
        span=max(.1,self.length/self.zoom);start=max(0,min(self.length-span,self.position/1000-span/2))
        return start,start+span
    def seconds_at(self,x):
        start,end=self.visible_range();return max(0,min(self.length,start+x/max(1,self.width())*(end-start)))
    def paintEvent(self,event):
        p=QPainter(self);theme=self.get_theme();p.fillRect(self.rect(),QColor(theme['surface']['start']))
        middle=self.height()/2 if self.overview else self.height()/2+6
        amplitude=max(2,self.height()/2-4 if self.overview else self.height()/2-17)
        start,end=self.visible_range();span=end-start
        def xpos(t):return int((t-start)/span*self.width())
        p.setPen(QPen(QColor(theme['accent']['start']),1))
        for x in range(self.width()):
            if not self.peaks:break
            index=min(len(self.peaks)-1,int(self.seconds_at(x)/max(.001,self.length)*len(self.peaks)))
            last=min(len(self.peaks),max(index+1,int(self.seconds_at(x+1)/max(.001,self.length)*len(self.peaks))))
            color=QColor(theme['accent']['start']);value=max(self.peaks[index:last])*amplitude
            if self.mode=='Rekordbox RGB' and self.native_colors is not None:
                heights,colors=self.native_colors;i=min(len(heights)-1,int(self.seconds_at(x)/max(.001,self.length)*len(heights)))
                value=float(heights[i])*amplitude;color=QColor.fromRgbF(*[float(c) for c in colors[i]])
            elif self.mode in {'RGB','3 band'} and self.bands is not None and len(self.bands):
                energy=self.bands[index:last].max(axis=0);total=max(.001,float(energy.max()))
                if self.mode=='3 band':
                    for band,col in sorted(zip(energy,['#ed745d','#57cfa9','#65aaf7']),reverse=True):
                        p.setPen(QColor(col));h=min(1,float(band)/max(.001,total))*value;p.drawLine(x,int(middle-h),x,int(middle+h))
                    continue
                color=QColor.fromRgbF(*[min(1,.18+.82*float(c)/total) for c in energy])
            elif self.mode=='Blue':color=QColor('#58b8f1')
            p.setPen(color);p.drawLine(x,int(middle-value),x,int(middle+value))
        if self.length:
            if self.grid and not self.overview:
                previous=-100
                for beat in self.beats:
                    x=xpos(beat['time']);down=beat['beat']==1
                    if x<0 or x>self.width() or (x-previous<7 and not down):continue
                    p.setPen(QColor(255,255,255,135 if down else 55));p.drawLine(x,16,x,self.height());previous=x
                    if down and span<35:p.drawText(x+3,26,'1')
            for cue in self.cues:
                x=xpos(cue['position']);p.setPen(QColor('#ffbd69'));p.drawLine(x,15,x,self.height())
                if cue.get('end',0)>cue['position']:
                    end=xpos(cue['end']);p.fillRect(x,15,max(1,end-x),self.height()-15,QColor(255,189,105,35))
            if self.overview and self.zoom>1:
                size=self.length/self.zoom;a=max(0,min(self.length-size,self.position/1000-size/2))
                p.setPen(QColor(theme['foreground']['start']));p.drawRect(QRectF(a/self.length*self.width(),1,size/self.length*self.width(),self.height()-2))
            x=xpos(self.position/1000);p.setPen(QPen(QColor(theme['foreground']['start']),2));p.drawLine(x,0,x,self.height())
        p.end()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.length:
            self.seek.emit(int(self.seconds_at(event.position().x())*1000))
    def mouseMoveEvent(self,event):
        if self.length:
            seconds=self.seconds_at(event.position().x())
            if event.buttons() & Qt.MouseButton.LeftButton:self.seek.emit(int(seconds*1000))
            nearby=next((c for c in self.cues if abs(c['position']-seconds)<(self.visible_range()[1]-self.visible_range()[0])*6/max(1,self.width())),None)
            self.setToolTip(nearby['label'] if nearby else f'{int(seconds)//60}:{int(seconds)%60:02d}')
    def wheelEvent(self,event):self.zoomRequested.emit(1 if event.angleDelta().y()>0 else -1);event.accept()

class TransportButton(QPushButton):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.playing=False;self.setFixedSize(42,42);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('Play');self.setAccessibleName('Play');self.setStyleSheet('border:none;background:transparent;padding:0;')
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setPen(Qt.PenStyle.NoPen)
        color=QColor(self.owner.theme['accent']['start']);p.setBrush(color.lighter(115) if self.underMouse() else color);p.drawEllipse(QRectF(2,2,38,38))
        p.setBrush(QColor('#17191b') if color.lightness()>130 else QColor('#ffffff'))
        if self.playing:p.drawRoundedRect(QRectF(15,13,4,16),1,1);p.drawRoundedRect(QRectF(23,13,4,16),1,1)
        else:p.drawPolygon(QPolygonF([QPointF(17,12),QPointF(17,30),QPointF(31,21)]))
        if self.hasFocus():p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor(self.owner.theme['foreground']['start']),1));p.drawEllipse(QRectF(1,1,40,40))
        p.end()

class MusicPlayer(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.path=None;self.jobs=[]
        self.media=QMediaPlayer(self);self.audio=QAudioOutput(self);self.audio.setVolume(.65);self.media.setAudioOutput(self.audio)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,6,0,0);bar=QHBoxLayout()
        self.play=TransportButton(owner);self.play.clicked.connect(self.toggle);bar.addWidget(self.play)
        self.title=QLabel();self.title.setTextFormat(Qt.TextFormat.PlainText);self.title.setMinimumWidth(80);self.title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);bar.addWidget(self.title,1)
        self.time=QLabel('0:00 / 0:00');bar.addWidget(self.time)
        self.cue_choice=QComboBox();self.cue_choice.setFixedWidth(165);self.cue_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.cue_choice.setMinimumContentsLength(10);self.cue_choice.activated.connect(self.jump_cue);bar.addWidget(self.cue_choice)
        self.colors=QComboBox();self.colors.addItems(['RGB','3 band','Blue','Accent','Rekordbox RGB']);self.colors.setToolTip('Waveform colour');bar.addWidget(self.colors)
        self.colors.setCurrentText(owner.catalogue.get_setting('player_wave_color','RGB'))
        self.grid=QCheckBox('Beatgrid');self.grid.setChecked(True);self.grid.toggled.connect(lambda value:self.set_grid(value));bar.addWidget(self.grid)
        bar.addWidget(QLabel('Zoom'));self.zoom=QSlider(Qt.Orientation.Horizontal);self.zoom.setRange(0,8);self.zoom.setFixedWidth(90);self.zoom.valueChanged.connect(self.set_zoom);bar.addWidget(self.zoom)
        self.zoom_label=QLabel('1×');self.zoom_label.setFixedWidth(38);bar.addWidget(self.zoom_label)
        bar.addWidget(QLabel('Volume'));volume=QSlider(Qt.Orientation.Horizontal);volume.setFixedWidth(100);volume.setRange(0,100);volume.setValue(65);volume.valueChanged.connect(lambda n:self.audio.setVolume(n/100));bar.addWidget(volume)
        close=owner.button('',self.dismiss,'SP_DialogCancelButton');close.setToolTip('Close player');close.setAccessibleName('Close player');close.setFixedWidth(34);bar.addWidget(close);layout.addLayout(bar)
        self.wave=Waveform(lambda:owner.theme);self.overview=Waveform(lambda:owner.theme,True)
        for wave in [self.wave,self.overview]:
            wave.seek.connect(self.media.setPosition);wave.zoomRequested.connect(lambda step:self.zoom.setValue(self.zoom.value()+step));layout.addWidget(wave)
        self.info=QLabel('');layout.addWidget(self.info);self.colors.currentTextChanged.connect(self.set_color);self.set_color(self.colors.currentText(),False)
        self.media.playbackStateChanged.connect(self.playback_changed)
        self.media.positionChanged.connect(self.position_changed);self.media.durationChanged.connect(self.duration_changed)
        self.media.errorOccurred.connect(lambda *_:owner.status.setText('Playback: '+self.media.errorString()))
        self.hide()
    def load(self,path,title,cues=(),autoplay=True,analysis=()):
        path=Path(path)
        if not path.is_file():raise ValueError('This track needs an available local file for playback')
        for job in self.jobs:job.cancel.set()
        self.path=str(path.resolve())
        for wave in [self.wave,self.overview]:wave.peaks=[];wave.bands=None;wave.native_colors=None;wave.beats=[];wave.length=0;wave.position=0;wave.cues=list(cues);wave.update()
        self.grid.setEnabled(False);self.info.setText('Reading waveform…')
        self.title.setText(title);self.title.setToolTip(title);self.cue_choice.clear();self.cue_choice.addItem('Jump to cue…',None)
        for cue in cues:self.cue_choice.addItem(cue['label'],int(cue['position']*1000))
        self.cue_choice.setVisible(bool(cues));self.show()
        self.media.setSource(QUrl.fromLocalFile(self.path))
        job=WaveJob(self.path,self,analysis);self.jobs.append(job);job.ready.connect(self.wave_ready)
        job.failed.connect(lambda path,error:self.info.setText(error) if path==self.path else None)
        job.finished.connect(lambda:self.finished(job));job.start()
        if autoplay:self.media.play()
    def finished(self,job):
        if job in self.jobs:self.jobs.remove(job)
        job.deleteLater()
    def wave_ready(self,path,peaks,length):
        if path!=self.path:return
        if not isinstance(peaks,dict):peaks={'peaks':peaks}
        for wave in [self.wave,self.overview]:
            wave.peaks=peaks['peaks'];wave.bands=peaks.get('bands');wave.native_colors=peaks.get('native_colors');wave.beats=peaks.get('beats',[]);wave.length=length;wave.update()
        available=bool(peaks.get('beats'));self.grid.setEnabled(available);self.grid.setToolTip('Existing Rekordbox beatgrid · read only' if available else 'No existing Rekordbox beatgrid for this track')
        self.info.setText(f"Rekordbox beatgrid · {len(peaks['beats'])} beats · read only" if available else 'Audio frequency analysis · no Rekordbox beatgrid')
        if peaks.get('analysis_error'):self.info.setText('Rekordbox analysis unavailable · using audio frequency analysis')
        if peaks.get('native_colors') is None and self.colors.currentText()=='Rekordbox RGB':self.info.setText(self.info.text()+' · native colours unavailable, using accent')
    def playback_changed(self,state):
        self.play.playing=state==QMediaPlayer.PlaybackState.PlayingState
        label='Pause' if self.play.playing else 'Play';self.play.setToolTip(label);self.play.setAccessibleName(label);self.play.update()
    def set_color(self,mode,persist=True):
        for wave in [self.wave,self.overview]:wave.mode=mode;wave.update()
        if persist:self.owner.catalogue.set_setting('player_wave_color',mode)
    def set_grid(self,value):self.wave.grid=value;self.wave.update()
    def set_zoom(self,value):
        zoom=2**value;self.wave.zoom=zoom;self.overview.zoom=zoom;self.zoom_label.setText(f'{zoom}×');self.wave.update();self.overview.update()
    def position_changed(self,value):
        for wave in [self.wave,self.overview]:wave.position=value;wave.update()
        self.update_time()
    def duration_changed(self,value):
        for wave in [self.wave,self.overview]:wave.length=value/1000;wave.update()
        self.update_time()
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
