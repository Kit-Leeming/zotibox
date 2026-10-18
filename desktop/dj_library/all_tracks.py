"""Virtual library playlist with effective-version membership and date sorting."""
import os
from datetime import datetime,timezone
from PySide6.QtCore import Qt,QSignalBlocker
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QCheckBox,QLabel,QTableWidgetItem,QMenu
from .playlist_picker import choose_playlist

ROLE=Qt.ItemDataRole.UserRole

def tracks(catalogue,native):
    with catalogue.connect() as db:
        versions=dict(db.execute('SELECT recording_id,preferred_id FROM recording_versions'))
        entries=list(db.execute('SELECT e.recording_id,p.name FROM entries e JOIN playlists p ON p.id=e.playlist_id WHERE e.recording_id IS NOT NULL'))
        downloads=list(db.execute("SELECT d.output,d.finished_at,m.recording_id FROM downloads d LEFT JOIN matches m ON m.spotify_id=d.spotify_id WHERE d.state='complete' AND d.output<>''"))
    def effective(id_):
        seen=set()
        while id_ in versions:
            if id_ in seen:break
            seen.add(id_);id_=versions[id_]
        return id_
    def key(path):return os.path.normcase(os.path.normpath(str(path)))
    files=catalogue.files();chosen={};paths={};members={};dates={}
    for row in files:
        if not row['available']:continue
        path=catalogue.resolve(row)
        if not path:continue
        id_=effective(row['recording_id']);paths[key(path)]=id_
        if id_==row['recording_id'] and id_ not in chosen:chosen[id_]=dict(row,kind='file',path=str(path))
    for id_,name in entries:members.setdefault(effective(id_),set()).add(name)
    native_ids={t['id']:paths.get(key(t['path'])) for t in native.get('tracks',[]) if t['path']}
    for playlist in native.get('playlists',[]):
        for song in playlist['songs']:
            id_=native_ids.get(song['track_id'])
            if id_:members.setdefault(id_,set()).add('Rekordbox: '+playlist['name'])
    for output,finished,id_ in downloads:
        id_=effective(id_) if id_ else paths.get(key(output))
        if id_ and finished:
            try:dates[id_]=max(dates.get(id_,0),datetime.fromisoformat(finished).timestamp())
            except ValueError:pass
    for id_,row in chosen.items():
        row['playlists']=sorted(members.get(id_,[]));row['added_time']=dates.get(id_) or row['created_ns']/1e9
        if not row['added_time']:
            try:row['added_time']=datetime.fromisoformat(row['indexed_at']).timestamp()
            except ValueError:row['added_time']=0
    return list(chosen.values())

class SortItem(QTableWidgetItem):
    def __lt__(self,other):
        left,right=self.data(ROLE+2),other.data(ROLE+2)
        return left<right if left is not None and right is not None else super().__lt__(other)

class AllTracks(QWidget):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;box=QVBoxLayout(self);bar=QHBoxLayout();box.addLayout(bar)
        self.search=owner.search_bar('Search all tracks');bar.addWidget(self.search,1)
        self.unassigned=QCheckBox('Not in any playlist');bar.addWidget(self.unassigned)
        bar.addWidget(owner.button('Refresh Rekordbox memberships',self.load_native,'SP_BrowserReload'))
        self.table=owner.table(['Title','Artist','Genre','BPM','Added / downloaded','Playlists','File'])
        self.table.setDragEnabled(True);self.table.drag_payload=lambda:owner.drag_data(self.selected())
        self.table.setSelectionMode(self.table.SelectionMode.ExtendedSelection)
        self.table.itemDoubleClicked.connect(lambda item:owner.play_row(item.data(ROLE)))
        self.table.customContextMenuRequested.connect(self.context);box.addWidget(self.table,1)
        self.summary=QLabel();self.summary.setWordWrap(True);box.addWidget(self.summary)
        self.search.textChanged.connect(self.refresh);self.unassigned.toggled.connect(self.refresh)
        self.table.sortItems(4,Qt.SortOrder.DescendingOrder);self.table.setSortingEnabled(True)
        for i,width in enumerate([300,200,150,70,170,300]):self.table.setColumnWidth(i,width)

    def selected(self):return [self.table.item(i.row(),0).data(ROLE) for i in self.table.selectionModel().selectedRows()]

    def activate(self):
        self.refresh()
        backend=self.owner.rb_panel.backend
        if not self.owner.rb_panel.preview_loaded and not self.owner.job and (backend.state or backend.source.is_file()):self.load_native()

    def refresh(self):
        panel=self.owner.rb_panel
        rows=tracks(self.owner.catalogue,panel.data);needle=self.search.text().casefold()
        rows=[r for r in rows if (not self.unassigned.isChecked() or not r['playlists']) and needle in ' '.join([r['title'],r['artist'],r.get('genre') or '',r['relative_path']]).casefold()]
        selected={r['recording_id'] for r in self.selected()};column=self.table.horizontalHeader().sortIndicatorSection();order=self.table.horizontalHeader().sortIndicatorOrder()
        block=QSignalBlocker(self.table);self.table.setSortingEnabled(False);self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            stamp=datetime.fromtimestamp(row['added_time']).strftime('%Y-%m-%d %H:%M') if row['added_time'] else ''
            values=[row['title'],row['artist'],row.get('genre') or '',f"{row['bpm']:g}" if row['bpm']>0 else '',stamp,', '.join(row['playlists']),row['relative_path']]
            for j,value in enumerate(values):
                item=SortItem(value);item.setData(ROLE,row);item.setToolTip(value)
                if j==4:item.setData(ROLE+2,row['added_time'])
                elif j==3:item.setData(ROLE+2,row['bpm'])
                if j==0:item.setIcon(self.owner.track_thumbnail(row));item.setData(ROLE+1,row['path'])
                self.table.setItem(i,j,item)
            if row['recording_id'] in selected:self.table.selectRow(i)
        self.table.setSortingEnabled(True);self.table.sortItems(max(0,column),order)
        suffix='' if panel.preview_loaded else ' · Rekordbox memberships not loaded; refresh to include them.'
        self.summary.setText(f'{len(rows)} tracks'+suffix)

    def load_native(self):
        self.owner.rb_panel.task('Reading Rekordbox memberships',lambda:self.owner.rb_panel.backend.view() if self.owner.rb_panel.backend.state else self.owner.rb_panel.backend.connect())

    def context(self,point):
        item=self.table.itemAt(point)
        if not item:return
        if not item.isSelected():self.table.selectRow(item.row())
        row=item.data(ROLE);menu=QMenu(self)
        menu.addAction('Play / preview',lambda:self.owner.play_row(row))
        menu.addAction('Show in File Explorer',lambda:self.owner.explorer(row['path']))
        menu.addAction('Add selected to playlist…',self.add_selected)
        menu.addAction('File details',lambda:self.owner.show_file_details(row))
        menu.exec(self.table.viewport().mapToGlobal(point))

    def add_selected(self):
        c=self.owner.catalogue
        def create(name):
            id_=c.create_playlist(name);return next(p for p in c.playlists() if p['id']==id_)
        choice=choose_playlist(c.playlists(),self,'Add selected tracks',create=create)
        if choice:
            c.add_entries(choice['id'],[r['recording_id'] for r in self.selected()]);self.owner.refresh_playlists();self.refresh()
