"""Rekordbox collection and playlist preview inside the Playlists page."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTreeWidget,QTreeWidgetItem,QSplitter,
    QComboBox,QInputDialog,QDialog,QFormLayout,QLineEdit,QSpinBox,QDialogButtonBox,QMessageBox,QFileDialog)
from .rekordbox import Rekordbox

ROLE=Qt.ItemDataRole.UserRole


class RekordboxPanel(QWidget):
    def __init__(self,owner):
        super().__init__()
        self.owner=owner
        self.backend=Rekordbox(owner.catalogue)
        self.data={"tracks":[],"playlists":[],"colors":[],"changes":[]}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,12,0,0)
        bar=QHBoxLayout()
        for text,fn,icon in [("Refresh Rekordbox",self.connect,"SP_BrowserReload"),("New playlist",lambda:self.create(False),"SP_FileIcon"),
                ("New folder",lambda:self.create(True),"SP_DirIcon"),("Rename",self.rename,"SP_FileDialogDetailedView")]:
            bar.addWidget(owner.button(text,fn,icon))
        bar.addStretch();layout.addLayout(bar)
        self.search=owner.search_bar("Search Rekordbox collection or selected playlist")
        self.search.textChanged.connect(self.refresh_tracks);layout.addWidget(self.search)
        split=QSplitter()
        from .themes import RowDelegate
        self.tree=type(owner.files_table)(lambda:owner.theme)
        self.tree.setItemDelegate(RowDelegate(self.tree,lambda:owner.theme))
        self.tree.setHeaderHidden(True);self.tree.setStyleSheet("QTreeWidget::item {min-height:30px;}")
        self.tree.currentItemChanged.connect(self.refresh_tracks)
        split.addWidget(self.tree)
        self.table=owner.table(["#","Title","Artist","Genre","Rating","Colour","Length"])
        for column,width in enumerate([45,300,210,160,75,100,70]):self.table.setColumnWidth(column,width)
        split.addWidget(self.table);split.setSizes([260,1050]);layout.addWidget(split,1)
        bar=QHBoxLayout()
        for text,fn,icon in [("Edit metadata…",self.edit,"SP_FileDialogDetailedView"),("Add to playlist…",self.add,"SP_ArrowRight"),
                ("Move up",lambda:self.move(-1),"SP_ArrowUp"),("Move down",lambda:self.move(1),"SP_ArrowDown"),
                ("Remove entry",self.remove,"SP_DialogCancelButton")]:
            bar.addWidget(owner.button(text,fn,icon))
        bar.addStretch();layout.addLayout(bar)
        self.summary=QLabel("Connect to Rekordbox in Settings, or refresh to inspect a copy of its library.")
        self.summary.setWordWrap(True);layout.addWidget(self.summary)
        bar=QHBoxLayout()
        for text,fn,icon in [("Preview changes…",self.preview,"SP_FileDialogContentsView"),("Apply to Rekordbox",self.apply,"SP_DialogApplyButton"),
                ("Discard preview",self.discard,"SP_DialogCancelButton")]:
            bar.addWidget(owner.button(text,fn,icon))
        bar.addStretch();layout.addLayout(bar)
        self.refresh_tree()

    def task(self,label,work):
        self.owner.start_job(label,lambda progress,cancel:work(),self.loaded)

    def loaded(self,data):
        self.data=data;self.refresh_tree()
        self.owner.status.setText("Rekordbox preview updated; live library is unchanged")

    def connect(self):
        self.task("Reading Rekordbox",self.backend.connect)

    def change(self,label,work):
        def run():work();return self.backend.view()
        self.task(label,run)

    def current_playlist(self):
        item=self.tree.currentItem()
        return item.data(0,ROLE) if item else None

    def refresh_tree(self):
        previous=self.current_playlist()
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
        self.tree.expandAll();self.tree.setCurrentItem(items.get(previous,collection));self.tree.blockSignals(False)
        self.refresh_tracks()
        n=len(self.data["changes"])
        self.summary.setText(f"{len(self.data['tracks'])} tracks · {n} pending change(s). Changes affect only the preview until Apply.")

    def refresh_tracks(self,*_):
        id_=self.current_playlist();tracks={t["id"]:t for t in self.data["tracks"]}
        playlist=next((p for p in self.data["playlists"] if p["id"]==id_),None)
        rows=[dict(tracks[s["track_id"]],song_id=s["id"],position=s["position"]) for s in playlist["songs"] if s["track_id"] in tracks] if playlist else list(tracks.values()) if id_ is None else []
        query=self.search.text().casefold()
        rows=[r for r in rows if query in " ".join(str(r[k]) for k in ["title","artist","album","genre"]).casefold()]
        self.owner.fill_table(self.table,rows,lambda i,r:[r.get("position",i+1),r["title"],r["artist"],r["genre"],"★"*r["rating"],r["color"],f"{int(r['duration'])//60}:{int(r['duration'])%60:02d}"])

    def selected(self):
        return [self.table.item(index.row(),0).data(ROLE) for index in self.table.selectionModel().selectedRows()]

    def create(self,folder):
        name,ok=QInputDialog.getText(self,"New Rekordbox folder" if folder else "New Rekordbox playlist","Name")
        if ok:
            current=self.current_playlist()
            parent=next((p["id"] for p in self.data["playlists"] if p["id"]==current and p["type"]==1),None)
            self.change("Preparing playlist",lambda:self.backend.create_playlist(name,parent,folder))

    def rename(self):
        id_=self.current_playlist()
        if not id_:return
        name,ok=QInputDialog.getText(self,"Rename playlist/folder","Name")
        if ok:self.change("Preparing rename",lambda:self.backend.rename_playlist(id_,name))

    def add(self):
        rows=self.selected()
        options=[p for p in self.data["playlists"] if p["type"]==0]
        if not rows or not options:return
        labels=[f"{p['name']} [{p['id']}]" for p in options]
        choice,ok=QInputDialog.getItem(self,"Add selected tracks","Playlist",labels,0,False)
        if ok:self.change("Preparing playlist entries",lambda:self.backend.add_tracks(options[labels.index(choice)]["id"],[r["id"] for r in rows]))

    def move(self,delta):
        rows=self.selected();id_=self.current_playlist()
        if len(rows)!=1 or not rows[0].get("song_id") or not id_:return
        playlist=next(p for p in self.data["playlists"] if p["id"]==id_)
        position=rows[0]["position"]+delta
        if 1<=position<=len(playlist["songs"]):self.change("Preparing order",lambda:self.backend.move_song(id_,rows[0]["song_id"],position))

    def remove(self):
        rows=self.selected();id_=self.current_playlist()
        if len(rows)==1 and id_ and rows[0].get("song_id"):
            self.change("Preparing removal",lambda:self.backend.remove_song(id_,rows[0]["song_id"]))

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
        QMessageBox.information(self,"Pending Rekordbox changes","\n".join(self.backend.state["changes"]) if self.backend.state and self.backend.state["changes"] else "No pending changes")

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
            self.backend.sync_local(id_,playlist["rekordbox_id"])
            return self.backend.view()
        self.owner.playlist_tabs.setCurrentIndex(1)
        self.task("Preparing Rekordbox sync preview",work)
