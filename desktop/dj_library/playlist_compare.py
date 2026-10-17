"""Read-only playlist comparison using explicit identities, never fuzzy titles."""
import csv
import os
from pathlib import Path
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QComboBox,QLabel,QLineEdit,QTableWidget,QTableWidgetItem,QPushButton,QFileDialog

def playlist_sources(catalogue,native):
    files=catalogue.files();by_recording={};by_path={}
    compatibility=catalogue.get_setting('compatibility_sources',{})
    for file in files:
        path=catalogue.resolve(file)
        row=dict(file,path=str(path) if path else '')
        by_recording.setdefault(file['recording_id'],[]).append(row)
        if path:by_path[os.path.normcase(str(path))]=row
    sources=[]
    for p in catalogue.playlists():
        rows=[]
        for e in catalogue.entries(p['id']):
            local=by_recording.get(e['recording_id'],[]);keys=[]
            if e['recording_id']:keys.append('recording:'+e['recording_id'])
            if e['spotify_id']:keys.append('spotify:'+e['spotify_id'])
            for f in local:
                keys.extend(['hash:'+f['sha256']]+(['path:'+os.path.normcase(f['path'])] if f['path'] else []))
                if f['isrc']:keys.append('isrc:'+f['isrc'].strip().upper())
            rows.append(dict(e,keys=keys,available=any(f['available'] and f['path'] and Path(f['path']).is_file() for f in local)))
        sources.append({'id':'local:'+p['id'],'name':p['name']+' · '+('Spotify' if p['source']=='spotify' else 'Local'),'rows':rows})
    tracks={t['id']:t for t in native.get('tracks',[])}
    for p in native.get('playlists',[]):
        if p['type']!=0:continue
        rows=[]
        for s in p['songs']:
            t=tracks.get(s['track_id'])
            if not t:continue
            path=t['path'];f=by_path.get(os.path.normcase(path));keys=['native:'+t['id']]
            if path:keys.append('path:'+os.path.normcase(path))
            derived=compatibility.get(os.path.normcase(path))
            if derived:
                try:
                    stat=Path(path).stat()
                    if (stat.st_size,stat.st_mtime_ns)==(derived['size'],derived['mtime_ns']):keys.append('hash:'+derived['source'])
                except (OSError,KeyError):pass
            if t.get('isrc'):keys.append('isrc:'+t['isrc'].strip().upper())
            if f:keys.extend(['recording:'+f['recording_id'],'hash:'+f['sha256']])
            rows.append(dict(t,keys=keys,position=s['position']-1,available=bool(path and Path(path).is_file())))
        sources.append({'id':'native:'+p['id'],'name':p['name']+' · Rekordbox','rows':rows})
    return sources

def compare(left,right):
    rows=[(0,r) for r in left]+[(1,r) for r in right];parent=list(range(len(rows)));seen={}
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,(_,r) in enumerate(rows):
        for key in r.get('keys',[]):
            if key in seen:parent[find(i)]=find(seen[key])
            else:seen[key]=i
    groups={}
    for i,(side,r) in enumerate(rows):groups.setdefault(find(i),[[],[]])[side].append(r)
    result=[]
    for a,b in groups.values():
        row=(a or b)[0];bpms=[r.get('bpm',0) for r in a+b if r.get('bpm',0)>0]
        result.append({'title':row['title'],'artist':row['artist'],'bpm':bpms[0] if bpms else 0,
            'status':'Shared' if a and b else 'Only left' if a else 'Only right',
            'left':', '.join(str(r['position']+1) for r in a),'right':', '.join(str(r['position']+1) for r in b),
            'missing':any(not r['available'] for r in a+b)})
    return result

class ComparisonDialog(QDialog):
    def __init__(self,sources,parent=None,initial=()):
        super().__init__(parent);self.setWindowTitle('Compare playlists');self.resize(1050,650);self.sources=sources
        layout=QVBoxLayout(self);bar=QHBoxLayout();self.left=QComboBox();self.right=QComboBox()
        for combo in [self.left,self.right]:
            for p in sources:combo.addItem(p['name'],p['id'])
            combo.setMinimumWidth(240);bar.addWidget(combo,1)
        layout.addLayout(bar)
        if len(sources)>1:self.right.setCurrentIndex(1)
        for combo,id_ in zip([self.left,self.right],initial):combo.setCurrentIndex(max(0,combo.findData(id_)))
        bar=QHBoxLayout();self.filter=QComboBox();self.filter.addItems(['All','Shared','Only left','Only right','Missing local files']);bar.addWidget(self.filter)
        self.search=QLineEdit();self.search.setPlaceholderText('Filter title or artist');self.search.setClearButtonEnabled(True);bar.addWidget(self.search,1);layout.addLayout(bar)
        self.summary=QLabel();layout.addWidget(self.summary)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['Title','Artist','BPM','Comparison','Left positions','Right positions','Local file']);self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers);self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(0,280);self.table.setColumnWidth(1,180);layout.addWidget(self.table,1)
        note=QLabel('Matches use track IDs, local recording/file identities and ISRC. Duplicate positions are listed; similar titles alone are not treated as matches.');note.setWordWrap(True);layout.addWidget(note)
        button=QPushButton('Export comparison CSV…');button.clicked.connect(self.export);layout.addWidget(button)
        for combo in [self.left,self.right,self.filter]:combo.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh);self.refresh()
    def refresh(self,*_):
        indexed={s['id']:s for s in self.sources};left=indexed.get(self.left.currentData(),{}).get('rows',[]);right=indexed.get(self.right.currentData(),{}).get('rows',[])
        self.rows=compare(left,right);counts={k:sum(r['status']==k for r in self.rows) for k in ['Shared','Only left','Only right']}
        self.summary.setText(' · '.join(f'{k}: {n}' for k,n in counts.items())+f" · Missing local files: {sum(r['missing'] for r in self.rows)}")
        mode=self.filter.currentText();query=self.search.text().casefold();self.visible=[r for r in self.rows if (mode=='All' or r['status']==mode or mode=='Missing local files' and r['missing']) and query in (r['title']+' '+r['artist']).casefold()]
        self.table.setRowCount(len(self.visible))
        for i,r in enumerate(self.visible):
            for j,v in enumerate([r['title'],r['artist'],f"{r['bpm']:g}" if r['bpm'] else '',r['status'],r['left'],r['right'],'Missing' if r['missing'] else 'Available']):self.table.setItem(i,j,QTableWidgetItem(v))
    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'Export comparison','Playlist comparison.csv','CSV (*.csv)')
        if not path:return
        with open(path,'w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow([self.table.horizontalHeaderItem(i).text() for i in range(7)])
            for row in range(self.table.rowCount()):writer.writerow([self.table.item(row,i).text() for i in range(7)])
