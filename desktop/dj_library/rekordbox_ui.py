"""Rekordbox collection and playlist preview inside the Playlists page."""
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTreeWidget,QTreeWidgetItem,QSplitter,
    QComboBox,QInputDialog,QDialog,QFormLayout,QLineEdit,QSpinBox,QDialogButtonBox,QMessageBox,QFileDialog,QMenu,QApplication)
from PySide6.QtGui import QAction,QKeySequence
from .rekordbox import Rekordbox
from .playlist_picker import choose_playlist

ROLE=Qt.ItemDataRole.UserRole


class RekordboxPanel(QWidget):
    def __init__(self,owner):
        super().__init__()
        self.owner=owner
        self.backend=Rekordbox(owner.catalogue)
        self.preview_loaded=False
        self.data={"tracks":[],"playlists":[],"colors":[],"changes":[]}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,12,0,0)
        bar=QHBoxLayout();self.tree_buttons={}
        for text,fn,icon in [("Refresh Rekordbox",self.connect,"SP_BrowserReload"),("New playlist",lambda:self.create(False),"SP_FileIcon"),
                ("New folder",lambda:self.create(True),"SP_DirIcon"),("Rename",self.rename,"SP_FileDialogDetailedView"),("Delete…",self.delete,"SP_DialogCancelButton")]:
            button=owner.button(text,fn,icon);self.tree_buttons[text]=button;bar.addWidget(button)
        bar.addStretch();layout.addLayout(bar)
        self.search=owner.search_bar("Search Rekordbox collection or selected playlist")
        self.search.textChanged.connect(self.refresh_tracks);layout.addWidget(self.search)
        split=QSplitter()
        from .themes import RowDelegate
        self.tree=type(owner.files_table)(lambda:owner.theme)
        self.tree.setItemDelegate(RowDelegate(self.tree,lambda:owner.theme))
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_context)
        self.tree.itemExpanded.connect(self.save_expansion);self.tree.itemCollapsed.connect(self.save_expansion)
        self.tree.currentItemChanged.connect(self.refresh_tracks)
        for key,fn in [("Delete",self.delete),("F2",self.rename)]:
            action=QAction(self.tree);action.setShortcut(QKeySequence(key));action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut);action.triggered.connect(fn);self.tree.addAction(action)
        split.addWidget(self.tree)
        self.table=owner.table(["#","Title","Artist","Genre","Rating","Colour","Length"])
        for column,width in enumerate([45,300,210,160,75,100,70]):self.table.setColumnWidth(column,width)
        self.table.customContextMenuRequested.connect(self.track_context)
        self.table.itemDoubleClicked.connect(lambda item:self.owner.play_row(item.data(ROLE)))
        self.table.setContentsMargins(8,0,0,0)
        split.setHandleWidth(12)
        split.addWidget(self.table);split.setSizes([260,1050]);layout.addWidget(split,1)
        bar=QHBoxLayout()
        self.track_buttons={}
        for text,fn,icon in [("Edit metadata…",self.edit,"SP_FileDialogDetailedView"),("Add to playlist…",self.add,"SP_ArrowRight"),
                ("Move up",lambda:self.move(-1),"SP_ArrowUp"),("Move down",lambda:self.move(1),"SP_ArrowDown"),
                ("Remove entry",self.remove,"SP_DialogCancelButton")]:
            button=owner.button(text,fn,icon);self.track_buttons[text]=button;bar.addWidget(button)
        self.table.itemSelectionChanged.connect(self.update_track_controls)
        bar.addStretch();bar.addWidget(owner.row_size_control());layout.addLayout(bar)
        self.summary=QLabel("Connect to Rekordbox in Settings, or refresh to inspect a copy of its library.")
        self.summary.setWordWrap(True);layout.addWidget(self.summary)
        bar=QHBoxLayout()
        for text,fn,icon in [("Preview changes…",self.preview,"SP_FileDialogContentsView"),("Apply to Rekordbox",self.apply,"SP_DialogApplyButton"),
                ("Discard preview",self.discard,"SP_DialogCancelButton")]:
            bar.addWidget(owner.button(text,fn,icon))
        bar.addStretch();layout.addLayout(bar)
        self.refresh_tree()

    def setup_dragdrop(self):
        self.table.setDragEnabled(True);self.table.setAcceptDrops(True);self.table.setDropIndicatorShown(True)
        self.table.drag_payload=lambda:self.owner.drag_data(self.selected(),native=True)
        self.table.drop_tracks=lambda data,point:self.drop_tracks(data,point)
        self.tree.setAcceptDrops(True);self.tree.drop_tracks=lambda data,point:self.drop_tracks(data,point,True)

    def drop_tracks(self,data,point,on_tree=False):
        try:
            recordings=self.owner.validate_drag(data)
            item=self.tree.itemAt(point) if on_tree else self.tree.currentItem()
            id_=item.data(0,ROLE) if item else None
            playlist=next((p for p in self.data["playlists"] if p["id"]==id_ and p["type"]==0),None)
            if not playlist:return False
            if not on_tree and data.get("native_playlist")==id_ and all(data.get("native_songs",[])) and data.get("native_songs"):
                if self.search.text():raise ValueError("Clear the search before reordering a Rekordbox playlist")
                existing=[s["id"] for s in playlist["songs"]];moved=[i for i in existing if i in data["native_songs"]]
                target=self.table.rowAt(point.y());target=target if target>=0 else len(existing)
                position=len([i for i in existing[:target] if i not in moved]);remaining=[i for i in existing if i not in moved]
                ordered=remaining[:position]+moved+remaining[position:]
                self.change("Preparing playlist order",lambda:self.backend.reorder_playlist(id_,ordered))
            elif data.get("native_tracks"):
                known={t["id"] for t in self.data["tracks"]}
                if any(i not in known for i in data["native_tracks"]):raise ValueError("Rekordbox tracks changed; refresh")
                self.change("Preparing playlist entries",lambda:self.backend.add_tracks(id_,data["native_tracks"]))
            elif recordings:self.change("Importing tracks into Rekordbox preview",lambda:self.backend.add_recordings(id_,recordings))
            else:return False
            return True
        except Exception as exc:self.owner.show_error(exc);return False

    def task(self,label,work):
        self.owner.start_job(label,lambda progress,cancel:work(),self.loaded)

    def ensure_loaded(self,*_):
        self.owner.artwork_timer.start()
        if not self.isVisible() or self.preview_loaded or not self.backend.state:return
        if self.owner.job:QTimer.singleShot(200,self.ensure_loaded);return
        self.preview_loaded=True
        self.task("Loading saved Rekordbox preview",self.backend.view)

    def loaded(self,data):
        self.preview_loaded=True
        self.data=data;self.refresh_tree()
        self.owner.status.setText("Rekordbox preview updated; live library is unchanged")

    def connect(self):
        self.task("Reading Rekordbox",self.backend.connect)

    def change(self,label,work):
        def run():
            if not self.backend.state:self.backend.connect()
            selected=work();view=self.backend.view()
            if isinstance(selected,str):view["_selection"]=selected
            return view
        self.task(label,run)

    def current_playlist(self):
        item=self.tree.currentItem()
        return item.data(0,ROLE) if item else None

    def refresh_tree(self):
        reveal=self.data.get("_selection") is not None
        previous=self.data.pop("_selection",None) or self.current_playlist()
        self.tree.blockSignals(True);self.tree.clear()
        collection=QTreeWidgetItem(["Collection"]);collection.setData(0,ROLE,None)
        collection.setIcon(0,self.owner.icon("SP_FileDialogContentsView"));self.tree.addTopLevelItem(collection)
        items={}
        for p in sorted(self.data["playlists"],key=lambda p:p["seq"]):
            item=QTreeWidgetItem([p["name"]+(" (smart)" if p["type"]==4 else "")]);item.setData(0,ROLE,p["id"])
            item.setIcon(0,self.owner.icon("SP_DirIcon" if p["type"]==1 else "SP_FileDialogListView"));items[p["id"]]=item
        for p in self.data["playlists"]:
            parent=items.get(p["parent"])
            if parent:parent.addChild(items[p["id"]])
            else:self.tree.addTopLevelItem(items[p["id"]])
        expanded=set(self.owner.catalogue.get_setting("rekordbox_expanded",[]))
        for id_,item in items.items():item.setExpanded(id_ in expanded)
        chosen=items.get(previous,collection);self.tree.setCurrentItem(chosen)
        if reveal:
            parent=chosen.parent()
            while parent:parent.setExpanded(True);parent=parent.parent()
        self.tree.blockSignals(False)
        self.refresh_tracks()
        n=len(self.data["changes"])
        skipped=sum(len(rows) for rows in (self.backend.state or {}).get("skipped_tracks",{}).values())
        self.summary.setText(f"{len(self.data['tracks'])} tracks · {n} pending change(s). Changes affect only the preview until Apply."+(f" {skipped} missing track(s) skipped; download/match and resync to add them." if skipped else ""))

    def save_expansion(self,*_):
        expanded=[]
        def visit(item):
            if item.isExpanded() and item.data(0,ROLE):expanded.append(item.data(0,ROLE))
            for i in range(item.childCount()):visit(item.child(i))
        for i in range(self.tree.topLevelItemCount()):visit(self.tree.topLevelItem(i))
        self.owner.catalogue.set_setting("rekordbox_expanded",expanded)

    def tree_context(self,point):
        item=self.tree.itemAt(point)
        if not item:return
        self.tree.setCurrentItem(item);menu=QMenu(self)
        menu.addAction("New playlist…",lambda:self.create(False));menu.addAction("New folder…",lambda:self.create(True))
        if self.current_playlist():
            menu.addAction("Rename…",self.rename)
            menu.addAction("Delete playlist / folder…",self.delete)
            playlist=next((p for p in self.data["playlists"] if p["id"]==self.current_playlist()),None)
            if playlist and playlist["type"]==0:menu.addAction("Export M3U8…",self.export_playlist)
        menu.addAction("Collapse all folders",self.tree.collapseAll)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def track_context(self,point):
        row=self.table.rowAt(point.y())
        if row<0:return
        if not self.table.item(row,0).isSelected():self.table.selectRow(row)
        selected=self.selected();menu=QMenu(self)
        if len(selected)==1:
            menu.addAction("Play / preview",lambda:self.owner.play_row(selected[0]))
            menu.addAction("Edit metadata…",self.edit)
            menu.addAction("Show in File Explorer",lambda:self.owner.explorer(selected[0]["path"]))
            menu.addAction("Copy file path",lambda:QApplication.clipboard().setText(selected[0]["path"]))
        menu.addAction("Add to playlist…",self.add)
        if len(selected)==1 and selected[0].get("song_id"):
            menu.addAction("Move up",lambda:self.move(-1));menu.addAction("Move down",lambda:self.move(1))
        if selected and all(r.get("song_id") for r in selected):menu.addAction("Remove from playlist",self.remove)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def refresh_tracks(self,*_):
        current=next((p for p in self.data["playlists"] if p["id"]==self.current_playlist()),None)
        for label in ["Rename","Delete…"]:self.tree_buttons[label].setEnabled(bool(current and current["type"] in {0,1}))
        id_=self.current_playlist();tracks={t["id"]:t for t in self.data["tracks"]}
        playlist=next((p for p in self.data["playlists"] if p["id"]==id_),None)
        rows=[dict(tracks[s["track_id"]],song_id=s["id"],position=s["position"]) for s in playlist["songs"] if s["track_id"] in tracks] if playlist else list(tracks.values()) if id_ is None else []
        query=self.search.text().casefold()
        rows=[r for r in rows if query in " ".join(str(r[k]) for k in ["title","artist","album","genre"]).casefold()]
        self.owner.fill_table(self.table,rows,lambda i,r:[r.get("position",i+1),r["title"],r["artist"],r["genre"],"★"*r["rating"],r["color"],f"{int(r['duration'])//60}:{int(r['duration'])%60:02d}"])
        self.update_track_controls()

    def update_track_controls(self):
        rows=self.selected();one=len(rows)==1;membership=bool(rows and all(r.get("song_id") for r in rows))
        for label,button in self.track_buttons.items():button.setVisible(one if label=="Edit metadata…" else bool(rows) if label=="Add to playlist…" else membership and (one or label=="Remove entry"))

    def selected(self):
        return [self.table.item(index.row(),0).data(ROLE) for index in self.table.selectionModel().selectedRows()]

    def create(self,folder):
        if self.owner.job:self.owner.status.setText("Wait for the current task before creating a playlist");return
        name,ok=QInputDialog.getText(self,"New Rekordbox folder" if folder else "New Rekordbox playlist","Name")
        if ok:
            current=self.current_playlist()
            selected=next((p for p in self.data["playlists"] if p["id"]==current),None)
            parent=(selected["id"] if selected["type"]==1 else selected["parent"]) if selected else None
            if parent in {"root","100000","200000"}:parent=None
            self.change("Preparing playlist",lambda:self.backend.create_playlist(name,parent,folder))

    def rename(self):
        if self.owner.job:self.owner.status.setText("Wait for the current task before renaming");return
        id_=self.current_playlist()
        if not id_:return
        row=next(p for p in self.data["playlists"] if p["id"]==id_)
        name,ok=QInputDialog.getText(self,"Rename playlist/folder","Name",text=row["name"])
        if ok:self.change("Preparing rename",lambda:self.backend.rename_playlist(id_,name))

    def delete(self):
        if self.owner.job:self.owner.status.setText("Wait for the current task before deleting a playlist");return
        id_=self.current_playlist();playlist=next((p for p in self.data["playlists"] if p["id"]==id_),None)
        if not playlist or playlist["type"] not in {0,1}:return
        suffix=" and all playlists inside it" if playlist["type"]==1 else ""
        if QMessageBox.question(self,"Delete Rekordbox playlist / folder",f"Prepare deletion of ‘{playlist['name']}’{suffix}?\nTracks, music files and cues are retained. Review and Apply to change Rekordbox.")!=QMessageBox.StandardButton.Yes:return
        parent=playlist["parent"]
        def work():self.backend.delete_playlist(id_);return parent
        self.change("Preparing playlist deletion",work)

    def export_playlist(self):
        id_=self.current_playlist();playlist=next((p for p in self.data["playlists"] if p["id"]==id_ and p["type"]==0),None)
        if not playlist:return
        path,_=QFileDialog.getSaveFileName(self,"Export Rekordbox playlist","playlist.m3u8","M3U8 playlist (*.m3u8)")
        if not path:return
        try:
            if Path(path).suffix.casefold()!=".m3u8":path+=".m3u8"
            tracks={t["id"]:t for t in self.data["tracks"]};lines=["#EXTM3U"];missing=0
            for song in playlist["songs"]:
                track=tracks[song["track_id"]];file=Path(track["path"])
                if not file.is_file():missing+=1;continue
                label=(track["artist"]+" - "+track["title"]).replace("\n"," ").replace("\r"," ")
                lines.extend([f"#EXTINF:{int(track['duration'])},{label}",str(file.absolute())])
            Path(path).write_text("\n".join(lines)+"\n",encoding="utf-8")
            self.owner.status.setText(f"Exported playlist; {missing} missing file(s) skipped")
        except Exception as exc:self.owner.show_error(exc)

    def add(self):
        if self.owner.job:self.owner.status.setText("Wait for the current task before editing playlists");return
        rows=self.selected()
        options=[p for p in self.data["playlists"] if p["type"]==0]
        if not rows:return
        def create(name):
            if not self.backend.state:self.backend.connect()
            id_=self.backend.create_playlist(name);return next(p for p in self.backend.view()["playlists"] if p["id"]==id_)
        choice=choose_playlist(options,self,"Add selected tracks to Rekordbox playlist",create=create)
        if choice:
            def work():self.backend.add_tracks(choice["id"],[r["id"] for r in rows]);return choice["id"]
            self.change("Preparing playlist entries",work)
        elif self.backend.state:self.loaded(self.backend.view())

    def move(self,delta):
        rows=self.selected();id_=self.current_playlist()
        if len(rows)!=1 or not rows[0].get("song_id") or not id_:return
        playlist=next(p for p in self.data["playlists"] if p["id"]==id_)
        position=rows[0]["position"]+delta
        if 1<=position<=len(playlist["songs"]):self.change("Preparing order",lambda:self.backend.move_song(id_,rows[0]["song_id"],position))

    def remove(self):
        rows=self.selected();id_=self.current_playlist()
        if rows and id_ and all(r.get("song_id") for r in rows):
            self.change("Preparing removal",lambda:self.backend.remove_songs(id_,[r["song_id"] for r in rows]))

    def edit(self):
        rows=self.selected()
        if len(rows)!=1:return
        row=rows[0];dialog=QDialog(self);dialog.setWindowTitle("Edit Rekordbox metadata — preview")
        layout=QVBoxLayout(dialog);form=QFormLayout();fields={}
        for key,label in [("title","Title"),("artist","Artist"),("album","Album"),("genre","Genre"),("key","Key"),("label","Label"),("comment","Comment")]:
            widget=QLineEdit(str(row[key]));form.addRow(label,widget);fields[key]=widget
        for key,label,maximum in [("rating","Rating",5),("year","Release year",9999),("track_no","Track number",9999)]:
            widget=QSpinBox();widget.setRange(0,maximum);widget.setValue(int(row[key]));form.addRow(label,widget);fields[key]=widget
        colour=QComboBox();colour.addItem("None","")
        for c in self.data["colors"]:colour.addItem(c["name"],c["id"])
        colour.setCurrentIndex(max(0,colour.findData(row["color_id"])));form.addRow("Colour",colour)
        layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec():
            values={k:w.value() if isinstance(w,QSpinBox) else w.text() for k,w in fields.items()};values["color_id"]=colour.currentData()
            self.change("Preparing metadata",lambda:self.backend.edit_track(row["id"],values))

    def preview(self):
        lines=list((self.backend.state or {}).get("changes",[]))
        for rows in (self.backend.state or {}).get("skipped_tracks",{}).values():
            if rows:lines.extend(["",f"Skipped {len(rows)} missing track(s):"]+[f"{r['artist']} — {r['title']}" for r in rows[:30]])
        QMessageBox.information(self,"Pending Rekordbox changes","\n".join(lines) if lines else "No pending changes")

    def apply(self):
        try:
            self.owner.ensure_idle()
            if not self.owner.catalogue.get_setting("rekordbox_write_enabled",False):raise ValueError("Enable database writes in Settings > Rekordbox first")
            changes=self.backend.state["changes"] if self.backend.state else []
            if not changes:return
            if QMessageBox.question(self,"Apply Rekordbox changes","\n".join(changes)+"\n\nClose Rekordbox first. A complete data backup will be created before applying.")!=QMessageBox.StandardButton.Yes:return
            def work(progress,cancel):
                message=self.backend.apply(progress,cancel)
                return message,self.backend.view()
            def complete(result):
                self.loaded(result[1]);self.owner.status.setText(result[0]);self.owner.refresh();self.owner.refresh_rb_backups()
            self.owner.start_job("Backing up and applying Rekordbox",work,complete)
        except Exception as exc:self.owner.show_error(exc)

    def discard(self):
        if QMessageBox.question(self,"Discard preview","Discard all pending Rekordbox changes and reload the library?")==QMessageBox.StandardButton.Yes:
            self.task("Refreshing Rekordbox",self.backend.discard)

    def sync_local(self):
        id_=self.owner.playlist_id
        if not id_:return
        playlist=next(p for p in self.owner.catalogue.playlists() if p["id"]==id_)
        def work():
            if not self.backend.state:self.backend.connect()
            target=self.backend.sync_local(id_,playlist["rekordbox_id"])
            view=self.backend.view();view["_selection"]=target;return view
        self.owner.playlist_tabs.setCurrentIndex(1)
        self.task("Preparing Rekordbox sync preview",work)
