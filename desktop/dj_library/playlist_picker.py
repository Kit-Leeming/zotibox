"""Playlist choices retain hidden identities while showing searchable names."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLineEdit,QComboBox,QListWidget,QListWidgetItem,QDialogButtonBox,QLabel,QPushButton,QInputDialog,QMessageBox

class PlaylistPicker(QDialog):
    def __init__(self,items,parent=None,title="Choose playlist",create=None):
        super().__init__(parent);self.setWindowTitle(title);self.resize(520,460);self.items=list(items);self.choice=None;self.create_callback=create
        layout=QVBoxLayout(self);bar=QHBoxLayout()
        self.search=QLineEdit();self.search.setPlaceholderText("Search playlist name");self.search.setClearButtonEnabled(True);bar.addWidget(self.search,1)
        self.mode="spotify" if any("collaborative" in p for p in items) else "local" if any("source" in p for p in items) else "native"
        self.filter=QComboBox();self.filter.addItems(["All","Collaborative","Non-collaborative"] if self.mode=="spotify" else ["All","Local","Rekordbox linked","Spotify linked"]);self.filter.setVisible(self.mode!="native");bar.addWidget(self.filter);layout.addLayout(bar)
        if create:
            button=QPushButton("New playlist…");button.clicked.connect(self.create_new);layout.addWidget(button)
        self.list=QListWidget();layout.addWidget(self.list,1)
        self.empty=QLabel("No matching playlists");layout.addWidget(self.empty)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.ok=buttons.button(QDialogButtonBox.StandardButton.Ok);buttons.accepted.connect(self.accept_choice);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.search.textChanged.connect(self.refresh);self.filter.currentIndexChanged.connect(self.refresh)
        self.list.currentItemChanged.connect(lambda *_:self.ok.setEnabled(self.list.currentItem() is not None))
        self.list.itemDoubleClicked.connect(lambda *_:self.accept_choice());self.refresh()
    def create_new(self):
        name,ok=QInputDialog.getText(self,"New playlist","Name")
        if not ok or not name.strip():return
        try:
            playlist=self.create_callback(name.strip());self.items.append(playlist)
            self.search.clear();self.filter.setCurrentIndex(0);self.refresh()
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.ItemDataRole.UserRole)["id"]==playlist["id"]:self.list.setCurrentRow(i);break
        except Exception as exc:QMessageBox.warning(self,"Cannot create playlist",str(exc))

    def refresh(self):
        self.list.clear();query=self.search.text().casefold();kind=self.filter.currentIndex()
        for p in self.items:
            if query not in p['name'].casefold():continue
            if self.mode=="spotify":
                if kind==1 and not p.get('collaborative'):continue
                if kind==2 and p.get('collaborative'):continue
            else:
                if kind==1 and p.get('source','manual')!='manual':continue
                if kind==2 and not p.get('rekordbox_id'):continue
                if kind==3 and p.get('source')!='spotify':continue
            owner=p.get('owner',{});owner=owner.get('display_name','') if isinstance(owner,dict) else owner
            item=QListWidgetItem(p['name']+(f" — {owner}" if owner else ''));item.setData(Qt.ItemDataRole.UserRole,p);self.list.addItem(item)
        self.empty.setVisible(self.list.count()==0);self.ok.setEnabled(self.list.count()>0)
        if self.list.count():self.list.setCurrentRow(0)
    def accept_choice(self):
        item=self.list.currentItem()
        if item:self.choice=item.data(Qt.ItemDataRole.UserRole);self.accept()

def choose_playlist(items,parent,title="Choose playlist",create=None):
    picker=PlaylistPicker(items,parent,title,create=create)
    return picker.choice if picker.exec() else None
