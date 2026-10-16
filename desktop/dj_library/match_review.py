"""Sequential review of distinct Spotify recordings, with durable decisions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QListWidget,QPushButton,QMessageBox
from .matching import candidates


class MatchReview(QDialog):
    def __init__(self,catalogue,tracks,parent=None):
        super().__init__(parent);self.catalogue=catalogue
        self.tracks=list({t['id']:t for t in tracks if t}.values());self.position=0;self.decisions=0
        self.setWindowTitle('Review local matches');self.resize(900,480)
        layout=QVBoxLayout(self);self.heading=QLabel();self.heading.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.heading)
        self.details=QLabel();self.details.setTextFormat(Qt.TextFormat.PlainText);self.details.setWordWrap(True);layout.addWidget(self.details)
        self.list=QListWidget();layout.addWidget(self.list,1)
        self.list.currentRowChanged.connect(lambda row:self.accept_button.setEnabled(row>=0))
        self.list.itemDoubleClicked.connect(lambda _:self.confirm())
        row=QHBoxLayout();layout.addLayout(row)
        self.previous=QPushButton('Previous');self.previous.clicked.connect(lambda:self.step(-1));row.addWidget(self.previous)
        self.previous.setShortcut('Alt+Left')
        self.skip=QPushButton('Skip / next');self.skip.clicked.connect(lambda:self.step(1));row.addWidget(self.skip)
        self.skip.setShortcut('Alt+Right')
        row.addStretch()
        self.missing=QPushButton('Not in local library');self.missing.clicked.connect(self.mark_missing);row.addWidget(self.missing)
        self.missing.setShortcut('Alt+M');self.missing.setToolTip('Alt+M — save as missing and advance')
        self.accept_button=QPushButton('Use selected match');self.accept_button.clicked.connect(self.confirm);row.addWidget(self.accept_button)
        close=QPushButton('Done');close.clicked.connect(self.accept);row.addWidget(close)
        self.show_track()

    def show_track(self):
        self.previous.setEnabled(self.position>0)
        self.skip.setEnabled(self.position+1<len(self.tracks))
        self.list.clear();self.ranked=[]
        if not self.tracks:
            self.heading.setText('No tracks need review');self.missing.setEnabled(False);self.accept_button.setEnabled(False);return
        track=self.tracks[self.position]
        self.heading.setText(f"{self.position+1} of {len(self.tracks)} — {track['artist']} — {track['title']}")
        self.ranked=candidates(self.catalogue,track)
        for candidate in self.ranked:
            self.list.addItem(f"{candidate['score']:.0%} · {candidate['label']}\n{candidate['evidence']}")
        with self.catalogue.connect() as db:
            saved=db.execute('SELECT decision FROM matches WHERE spotify_id=?',(track['id'],)).fetchone()
        self.details.setText((f"Saved decision: {saved['decision']}. " if saved else '')+
            'Choose a local recording, or mark it missing to include it in Queue missing. Skip leaves the decision unchanged.')
        self.accept_button.setEnabled(False)

    def step(self,delta):
        self.position=max(0,min(len(self.tracks)-1,self.position+delta));self.show_track()

    def decide(self,recording,decision):
        track=self.tracks[self.position];self.catalogue.save_remote(track)
        self.catalogue.decide_match(track['id'],recording,decision)
        self.decisions+=1
        if self.position+1<len(self.tracks):self.step(1)
        else:self.accept()

    def confirm(self):
        row=self.list.currentRow()
        if row<0:return
        recording=self.ranked[row]['recording_id']
        if not self.catalogue.available_recording(recording):
            QMessageBox.information(self,'File unavailable','This recording is no longer available. Choose another match or mark it missing.');self.show_track();return
        self.decide(recording,'confirmed')

    def mark_missing(self):
        if self.tracks:self.decide(None,'missing')
