"""Local file organisation with explicit plans and recoverable cleanup."""
import json
import re
from pathlib import Path
from PySide6.QtCore import Qt,Signal,QUrl
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QTabWidget,QWidget,QLabel,QComboBox,QPushButton,
    QLineEdit,QFileDialog,QTableWidget,QTableWidgetItem,QHeaderView,QListWidget,QListWidgetItem,QMessageBox,QCheckBox,QTreeWidget,QTreeWidgetItem)
from .organisation import Organiser
from .file_actions import recycle
from .matching import normalized

ROLE=Qt.ItemDataRole.UserRole


def button(label,fn,layout):
    result=QPushButton(label);result.clicked.connect(fn);layout.addWidget(result);return result


def base_title(title):
    return normalized(re.sub(r'\b(extended|original|radio)\s*(mix|version|edit)?\b','',title,flags=re.I))


class OrganiseDialog(QDialog):
    def __init__(self,owner,tab=0):
        super().__init__(owner);self.owner=owner;self.c=owner.catalogue;self.service=Organiser(self.c,owner.rb_panel.backend);self.plan_data=None
        self.setWindowTitle('Organise local music');self.resize(1100,730);layout=QVBoxLayout(self)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        page=QWidget();box=QVBoxLayout(page);self.tabs.addTab(page,'Copy / move')
        explanation=QLabel('Preview paths, copy and verify, then apply Rekordbox changes. Remove originals only after checking the result.');explanation.setWordWrap(True);box.addWidget(explanation)
        row=QHBoxLayout();box.addLayout(row);self.action=QComboBox()
        for title,key in [('Consolidate all scanned music','consolidate'),('Move download folder to another drive','move'),('Convert old Ogg / unsupported files to MP3 320','convert')]:self.action.addItem(title,key)
        row.addWidget(self.action);self.destination=QLineEdit(self.c.get_setting('download_folder',str(self.c.workspace/'Music')));row.addWidget(self.destination,1)
        button('Browse…',self.browse,row);button('Prepare preview',self.prepare,row)
        self.preview=QTableWidget(0,3);self.preview.setHorizontalHeaderLabels(['Original file','Destination','Size'])
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);box.addWidget(self.preview,1)
        row=QHBoxLayout();box.addLayout(row);self.execute_button=button('Copy and prepare relinks',self.execute,row)
        button('View / apply Rekordbox preview',self.native_preview,row);self.cleanup_button=button('Remove verified originals…',self.cleanup,row)
        history=QHBoxLayout();box.addLayout(history);self.history=QComboBox();history.addWidget(self.history,1);button('Open saved operation',self.open_history,history)
        page=QWidget();box=QVBoxLayout(page);self.tabs.addTab(page,'Duplicates')
        label=QLabel('Exact copies and possible duplicates are listed separately. Select a group, then select the copy to keep.');label.setWordWrap(True);box.addWidget(label)
        row=QHBoxLayout();box.addLayout(row);self.groups=QListWidget();self.copies=QListWidget();row.addWidget(self.groups,1);row.addWidget(self.copies,2)
        self.groups.currentItemChanged.connect(self.show_copies);button('Review keeping selected copy',self.keep_duplicate,box)
        self.keep_selected_cues=QCheckBox('Keep only the selected copy’s native cues / analysis if cue sets conflict')
        box.addWidget(self.keep_selected_cues)
        page=QWidget();box=QVBoxLayout(page);self.tabs.addTab(page,'Preferred versions')
        label=QLabel('Use a local extended mix for a regular recording, including every future Spotify sync. Search one playlist or your entire library.');label.setWordWrap(True);box.addWidget(label)
        row=QHBoxLayout();box.addLayout(row);self.scope=QComboBox();self.scope.addItem('Entire library',None)
        for p in self.c.playlists():self.scope.addItem(p['name'],p['id'])
        row.addWidget(self.scope);button('Find extended versions…',self.find_extended,row)
        self.versions=QListWidget();box.addWidget(self.versions,1);button('Choose preferred version for selected recording…',self.choose_version,box)
        button('Show superseded files in Local Music',self.show_superseded,box)
        page=QWidget();box=QVBoxLayout(page);self.tabs.addTab(page,'Artwork')
        label=QLabel('Fill missing MP3 / FLAC covers from a saved Spotify match or an exact ISRC + album match in MusicBrainz / Cover Art Archive. Existing covers are preserved. Original tags are backed up; audio packets are verified unchanged.');label.setWordWrap(True);box.addWidget(label)
        button('Find and embed missing covers in selected local files',lambda:self.artwork(True),box)
        button('Find and embed missing covers in entire library',lambda:self.artwork(False),box);box.addStretch()
        self.message=QLabel('');self.message.setWordWrap(True);layout.addWidget(self.message)
        button('Close',self.close,layout);self.refresh();self.tabs.setCurrentIndex(tab)

    def run(self,label,work,done):
        try:
            self.owner.ensure_idle()
            def complete(value):
                done(value);self.refresh()
                self.owner.download_folder.setText(self.c.get_setting('download_folder',str(self.c.workspace/'Music')))
                if self.service.rekordbox.state:self.owner.rb_panel.loaded(self.service.rekordbox.view())
                self.owner.refresh()
            self.owner.start_job(label,work,complete)
            self.owner.job.setProperty('exclusive_files',True)
        except Exception as exc:self.owner.show_error(exc)

    def refresh(self):
        self.execute_button.setEnabled(bool(self.plan_data and self.plan_data['state']=='planned'))
        self.cleanup_button.setEnabled(bool(self.plan_data and self.plan_data['state']=='ready-for-cleanup'))
        selected=self.history.currentData();self.history.clear()
        for path in sorted(self.service.journals.glob('*.json'),reverse=True):
            try:
                value=json.loads(path.read_text(encoding='utf-8'))
                self.history.addItem(f"{value['created'][:19]} · {value['kind']} · {value['state']}",str(path))
            except (OSError,ValueError,KeyError):continue
        if selected:self.history.setCurrentIndex(max(0,self.history.findData(selected)))
        self.groups.clear();groups={}
        for row in self.c.files():
            if row['available']:groups.setdefault(row['sha256'],[]).append(row)
        for rows in groups.values():
            if len(rows)<2:continue
            item=QListWidgetItem(f"{rows[0]['artist']} — {rows[0]['title']} ({len(rows)} copies)");item.setData(ROLE,rows);self.groups.addItem(item)
        possible={}
        for row in self.c.files():
            if row['available']:possible.setdefault((normalized(row['artist']),normalized(row['title'])),[]).append(row)
        for (artist,title),rows in possible.items():
            if artist and title and len({r['sha256'] for r in rows})>1 and max(r['duration'] for r in rows)-min(r['duration'] for r in rows)<=2.5:
                item=QListWidgetItem(f"{rows[0]['artist']} — {rows[0]['title']} · possible duplicates, audio differs");item.setData(ROLE,rows);self.groups.addItem(item)
        self.versions.clear();seen=set()
        for row in self.c.files():
            if not row['available'] or row['recording_id'] in seen:continue
            seen.add(row['recording_id']);preferred=self.c.effective_recording(row['recording_id'])
            item=QListWidgetItem(f"{row['artist']} — {row['title']}"+(' · preferred version set' if preferred!=row['recording_id'] else ''))
            item.setData(ROLE,row);self.versions.addItem(item)

    def browse(self):
        value=QFileDialog.getExistingDirectory(self,'Destination folder')
        if value:self.destination.setText(value)

    def prepare(self):
        kind=self.action.currentData();files=None
        if kind=='convert':files=[f for f in self.c.files() if f['available'] and Path(f['relative_path']).suffix.lower() in {'.ogg','.oga','.opus','.aac','.wma','.alac','.mp4'}]
        self.run('Preparing file organisation',lambda progress,cancel:self.service.plan(kind,self.destination.text(),files),self.display_plan)

    def display_plan(self,plan):
        self.plan_data=plan;self.tabs.setCurrentIndex(0);self.preview.setRowCount(len(plan['rows']))
        for i,row in enumerate(plan['rows']):
            for j,value in enumerate((row['source'],row['target'] or 'Verified MP3 in download folder',f"{row['size']/1024/1024:.1f} MB")):
                item=QTableWidgetItem(value);item.setToolTip(value);self.preview.setItem(i,j,item)
        total=sum(r['size'] for r in plan['rows'])/1024**3
        self.message.setText(f"{len(plan['rows'])} files · {total:.2f} GB of originals · {plan['state']}. Originals remain until you explicitly remove them.")
        self.refresh()

    def execute(self):
        if not self.plan_data:return
        self.run('Copying and preparing relinks',lambda progress,cancel:self.service.execute(self.plan_data,progress,cancel),self.display_plan)

    def native_preview(self):
        self.owner.navigation.setCurrentRow(3);self.owner.playlist_tabs.setCurrentIndex(1);self.close()

    def cleanup(self):
        if not self.plan_data:return
        if QMessageBox.question(self,'Remove originals','Move the verified original files in this operation to the Recycle Bin?\nThe retained copies must still match their checksums, and Rekordbox must no longer reference the originals.')!=QMessageBox.StandardButton.Yes:return
        self.owner.player.media.stop();self.owner.player.media.setSource(QUrl())
        self.run('Removing verified originals',lambda progress,cancel:self.service.cleanup(self.plan_data,recycle,progress,cancel),self.display_plan)

    def open_history(self):
        if self.history.currentData():self.display_plan(json.loads(Path(self.history.currentData()).read_text(encoding='utf-8')))

    def show_copies(self,item,*_):
        self.copies.clear()
        for row in item.data(ROLE) if item else []:
            value=QListWidgetItem(f"{row['codec']} · {row['bitrate']//1000} kbps · {row['sample_rate']} Hz · {self.c.resolve(row)}");value.setData(ROLE,row);self.copies.addItem(value)
        if self.copies.count():self.copies.setCurrentRow(0)

    def keep_duplicate(self):
        group=self.groups.currentItem();keeper=self.copies.currentItem()
        if not group or not keeper:return
        files=group.data(ROLE);path=self.c.resolve(keeper.data(ROLE))
        kind='duplicates'
        if len({f['sha256'] for f in files})>1 or self.keep_selected_cues.isChecked():
            if QMessageBox.question(self,'Keep the selected recording','Keep only the selected recording in Rekordbox?\nIts existing cues and analysis are retained; the other recordings’ cues will be discarded at Apply. If this file has no native analysis, it will need analysis in Rekordbox. Originals stay until cleanup.')!=QMessageBox.StandardButton.Yes:return
            kind='version'
        self.run('Preparing duplicate consolidation',lambda progress,cancel:self.service.plan(kind,files=files,keeper=path),self.display_plan)

    def choose_version(self):
        item=self.versions.currentItem()
        if item:VersionDialog(self.owner,item.data(ROLE)).exec();self.refresh()

    def find_extended(self):ExtendedSearch(self.owner,self.scope.currentData()).exec();self.refresh()

    def show_superseded(self):self.c.set_setting('hidden_version_recordings',[]);self.owner.refresh_files()

    def artwork(self,selected):
        from .artwork_repair import repair
        rows=self.owner.selected_files() if selected else [f for f in self.c.files() if f['available']]
        if not rows:self.message.setText('Select files in Local Music first.');return
        def done(result):
            self.owner.artwork.images.clear()
            self.message.setText(f"Artwork added: {result['embedded']} · unchanged / no exact cover: {result['skipped']}"+('\n'+'\n'.join(result['errors'][:8]) if result['errors'] else ''))
        self.run('Finding missing artwork',lambda progress,cancel:repair(self.c,rows,self.owner.spotify,progress,cancel),done)

    def closeEvent(self,event):
        if self.owner.job:self.message.setText('Wait for the operation to finish, or use Cancel task in the main window.');event.ignore()
        else:super().closeEvent(event)


class VersionDialog(QDialog):
    def __init__(self,owner,source,target=None):
        super().__init__(owner);self.owner=owner;self.source=source;self.target=target;self.setWindowTitle('Preferred local version');self.resize(850,550)
        box=QVBoxLayout(self);box.addWidget(QLabel(f"Use a different local version for {source['artist']} — {source['title']}"))
        self.search=QLineEdit();self.search.setPlaceholderText('Search title, artist or filename');box.addWidget(self.search)
        self.list=QListWidget();box.addWidget(self.list,1);self.search.textChanged.connect(self.fill)
        self.native=QCheckBox('Also replace the regular version across Rekordbox playlists and collection')
        self.native.setChecked(owner.rb_panel.backend.source.is_file())
        self.native.setToolTip('The extended version keeps its own cues and analysis. Regular-version cues are discarded only after confirmation and Apply, with a backup.')
        box.addWidget(self.native);self.hide_old=QCheckBox('Hide the regular version in Local Music');self.hide_old.setChecked(True);box.addWidget(self.hide_old)
        label=QLabel('Saving remembers the choice for every future sync. Originals can be removed through Organise → saved operation after the Rekordbox preview is applied.');label.setWordWrap(True);box.addWidget(label)
        button('Use selected version',self.save,box);button('Close',self.close,box);self.fill()
        button('Clear saved preference / show original',self.clear_preference,box)

    def clear_preference(self):
        try:
            self.owner.ensure_idle();self.owner.catalogue.clear_preferred_version(self.source.get('original_recording_id') or self.source['recording_id'])
            self.owner.refresh();self.accept()
        except Exception as exc:self.owner.show_error(exc)

    def fill(self):
        self.list.clear();needle=self.search.text().casefold();seen=set()
        for row in self.owner.catalogue.files():
            if not row['available'] or row['recording_id']==self.source['recording_id'] or row['recording_id'] in seen:continue
            label=f"{row['artist']} — {row['title']} · {row['relative_path']}"
            if needle and needle not in label.casefold():continue
            seen.add(row['recording_id']);item=QListWidgetItem(label);item.setData(ROLE,row);self.list.addItem(item)
            if self.target and row['recording_id']==self.target['recording_id']:self.list.setCurrentItem(item)

    def save(self):
        item=self.list.currentItem()
        if not item:return
        c=self.owner.catalogue;source_id=self.source.get('original_recording_id') or self.source['recording_id'];target=item.data(ROLE)
        try:
            self.owner.ensure_idle()
            if self.native.isChecked():
                if QMessageBox.question(self,'Replace regular version in Rekordbox',
                    'Replace the regular recording with the selected version in all Rekordbox playlists and remove its collection identity?\n\nThe selected version keeps its own cues and beatgrid. The regular version’s cues and analysis will not transfer. A native backup is required at Apply. Original music stays until reviewed cleanup.')!=QMessageBox.StandardButton.Yes:return
                service=Organiser(c,self.owner.rb_panel.backend)
                files=[f for f in c.files() if f['recording_id']==source_id and f['available']]
                plan=service.plan('version',files=files,keeper=c.resolve(target))
                def work(progress,cancel):return service.execute(plan,progress,cancel)
                def done(value):
                    c.prefer_version(source_id,target['recording_id']);self.hide_source(source_id)
                    self.owner.rb_panel.loaded(service.rekordbox.view());self.owner.refresh()
                    QMessageBox.information(self.owner,'Preferred version saved','Review and apply the Rekordbox preview, then use Organise → Open saved operation → Remove verified originals.')
                self.owner.start_job('Preparing preferred version',work,done)
                self.owner.job.setProperty('exclusive_files',True)
            else:
                service=Organiser(c,self.owner.rb_panel.backend)
                files=[f for f in c.files() if f['recording_id']==source_id and f['available']]
                plan=service.plan('version',files=files,keeper=c.resolve(target))
                c.prefer_version(source_id,target['recording_id'])
                for row in plan['rows']:row['target_sha256']=row['preferred_sha256'];row['copied']=True
                plan['state']='ready-for-cleanup';plan['native_required']=service.rekordbox.source.is_file();service.save(plan)
                self.hide_source(source_id);self.owner.refresh()
            self.accept()
        except Exception as exc:self.owner.show_error(exc)

    def hide_source(self,source_id):
        c=self.owner.catalogue;hidden=set(c.get_setting('hidden_version_recordings',[]))
        if self.hide_old.isChecked():hidden.add(source_id)
        else:hidden.discard(source_id)
        c.set_setting('hidden_version_recordings',sorted(hidden))


class ExtendedSearch(QDialog):
    found=Signal(object,object)
    def __init__(self,owner,playlist_id=None):
        super().__init__(owner);self.owner=owner;self.setWindowTitle('Find extended versions');self.resize(1000,650);box=QVBoxLayout(self)
        ids={r['recording_id'] for r in owner.catalogue.entries(playlist_id)} if playlist_id else None
        self.files=[f for f in owner.catalogue.files() if f['available']];seen=set();self.sources=[]
        for f in self.files:
            if (ids is not None and f['recording_id'] not in ids) or f['recording_id'] in seen or 'extended' in normalized(f['title']).split():continue
            seen.add(f['recording_id']);self.sources.append(f)
        label=QLabel('Local matches appear below. Spotify search can only find versions listed on Spotify; it cannot establish that an extended mix exists elsewhere. Choose each replacement explicitly.');label.setWordWrap(True);box.addWidget(label)
        self.tree=QTreeWidget();self.tree.setHeaderLabels(['Recording / candidate','Source']);box.addWidget(self.tree,1)
        for source in self.sources:
            parent=QTreeWidgetItem([source['artist']+' — '+source['title'],'Current']);parent.setData(0,ROLE,source);self.tree.addTopLevelItem(parent)
            for candidate in self.files:
                if 'extended' in normalized(candidate['title']).split() and base_title(source['title'])==base_title(candidate['title']) and normalized(source['artist'])==normalized(candidate['artist']):
                    child=QTreeWidgetItem([candidate['title'],'Local extended mix']);child.setData(0,ROLE,(source,candidate));parent.addChild(child);parent.setExpanded(True)
        self.found.connect(self.add_online)
        row=QHBoxLayout();box.addLayout(row);button('Search Spotify for selected recording',self.online,row)
        button('Search Spotify for this whole scope',lambda:self.online(True),row)
        button('Use selected local version / queue Spotify candidate',self.choose,row);button('Close',self.close,row)

    def online(self,all_sources=False):
        item=self.tree.currentItem()
        if not item and not all_sources:return
        if item and item.parent():item=item.parent()
        sources=self.sources if all_sources else [item.data(0,ROLE)]
        def work(progress,cancel):
            from .spotify import track_data
            for index,source in enumerate(sources):
                if cancel.wait(.15):raise InterruptedError('Extended version search paused; discovered candidates retained')
                tracks=[track_data(t) for t in self.owner.spotify.search(f"{source['artist']} {source['title']} extended")]
                self.found.emit(source,tracks);progress(index+1,f"{index+1}/{len(sources)} · {source['title']}")
        try:self.owner.ensure_idle();self.owner.start_job('Searching extended versions',work,lambda result:None)
        except Exception as exc:self.owner.show_error(exc)

    def add_online(self,source,tracks):
        item=next((self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(i).data(0,ROLE)['recording_id']==source['recording_id']),None)
        if not item:return
        existing={item.child(i).data(0,ROLE)[1].get('id') for i in range(item.childCount())}
        for track in tracks:
            if track['id'] in existing or 'extended' not in normalized(track['title']).split() or base_title(track['title'])!=base_title(source['title']) or normalized(track['artist'])!=normalized(source['artist']):continue
            child=QTreeWidgetItem([track['artist']+' — '+track['title'],'Spotify · queue then review']);child.setData(0,ROLE,(source,track));item.addChild(child)
        item.setExpanded(True)

    def choose(self):
        item=self.tree.currentItem()
        if not item or not item.parent():return
        source,target=item.data(0,ROLE)
        if target.get('recording_id'):VersionDialog(self.owner,source,target).exec()
        else:self.owner.catalogue.save_remote(target);self.owner.queue_one(target['id']);item.setText(1,'Queued · choose preferred version after download')
