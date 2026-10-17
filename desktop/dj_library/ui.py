from __future__ import annotations
import copy
import os
import subprocess
import sys
import threading
from html import escape
from datetime import datetime,timedelta
from pathlib import Path, PurePosixPath
from PySide6.QtCore import Qt, QEvent, QThread, QTimer, Signal, Slot, QSettings, QSignalBlocker, QLockFile, QUrl, QStandardPaths, QSize, QPoint,QItemSelectionModel
from PySide6.QtGui import QColor, QIcon, QPixmap, QDesktopServices, QPalette, QPainter, QPen,QAction,QKeySequence
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QFrame,QLabel,
    QListWidget,QListWidgetItem,QStackedWidget,QPushButton,QLineEdit,QComboBox,QCheckBox,QTableWidget,QTableWidgetItem,
    QTreeWidget,QTreeWidgetItem,QSplitter,QFileDialog,QInputDialog,QMessageBox,QDialog,QDialogButtonBox,QMenu,QProgressBar,QScrollArea,QStyle,QTabWidget,QSlider)
from .dragdrop import DragMixin,TrackTable,PlaylistList,HoverNavigation,HoverTabs
from .playlist_picker import choose_playlist
from .playlist_tree import PlaylistTree,PlaylistItem
from .match_review import MatchReview
from .zotify_settings import ZotifySettings
from .player import MusicPlayer
from .artwork_async import ArtworkCache
from .instance import acquire_lock
from .file_actions import recycle
from .catalogue import Catalogue
from .branding import icon_image, save_icon
from .themes import make_theme, valid_theme, stylesheet, RowDelegate,DownloadDelegate, ThemeDialog, selection_brush
from .icons import icon as outline_icon
from .spotify import Spotify, TokenStore, REDIRECT_URI, track_data,spotify_id
from .matching import match_track,local_recordings,reclassify_reviews
from .downloads import DownloadEngine, schema, MANAGED, validated_options, worker_config
from .transfer import export_workspace, import_workspace, inspect_package
from .workspace import relocate
from .updates import VERSION, find_update, launch_installer, github_update, download_update, REPOSITORY
from .chrome import Backdrop, TitleBar, resize_hit, configure_frame
from .rekordbox import Rekordbox
from .rekordbox_ui import RekordboxPanel
from .renaming import rename_plan, apply_renames

ROLE=Qt.ItemDataRole.UserRole

def muted(text):
    label=QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("muted",True)
    label.setWordWrap(True)
    return label

def duration(value):
    minutes,seconds=divmod(int(value or 0),60)
    return f"{minutes}:{seconds:02d}"


def clock_duration(value):
    hours,remainder=divmod(max(0,int(value or 0)),3600);minutes,seconds=divmod(remainder,60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'

def date_ns(value):
    return datetime.fromtimestamp(value/1e9).strftime("%Y-%m-%d %H:%M") if value else ""

class Job(QThread):
    progress=Signal(int,str)
    finished_result=Signal(object)
    failed=Signal(str)
    cancelled=Signal(str)
    def __init__(self,work,parent):
        super().__init__(parent)
        self.work=work
        self.cancel=threading.Event()
    def run(self):
        try:
            self.finished_result.emit(self.work(self.progress.emit,self.cancel))
        except InterruptedError as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))

class LibraryItem(QTreeWidgetItem):
    def __lt__(self,other):
        left,right=self.data(0,ROLE) or {},other.data(0,ROLE) or {}
        if left.get("kind")!=right.get("kind"):
            return left.get("kind")=="folder"
        column=self.treeWidget().sortColumn()
        if column in {4,5} and left.get("kind")=="file":
            key="duration" if column==4 else "created_ns"
            return left[key]<right[key]
        return self.text(column).casefold()<other.text(column).casefold()

class LibraryTree(DragMixin,QTreeWidget):
    def __init__(self,get_theme):
        super().__init__()
        self.get_theme=get_theme

    def drawBranches(self,painter,rect,index):
        super().drawBranches(painter,rect,index)
        if self.selectionModel().isSelected(index):
            from PySide6.QtCore import QRectF,QPointF
            from PySide6.QtGui import QPolygonF
            theme=self.get_theme()
            painter.fillRect(rect,selection_brush(theme["selection"],QRectF(0,rect.y(),self.viewport().width(),rect.height())))
            if self.model().hasChildren(index):
                centre=rect.right()-self.indentation()/2
                y=rect.center().y()
                points=[(centre-4,y-2),(centre,y+2),(centre+4,y-2)] if self.isExpanded(index) else [(centre-2,y-4),(centre+2,y),(centre-2,y+4)]
                painter.setPen(QPen(QColor(theme["selection_text"]["start"]),1.4))
                painter.drawPolyline(QPolygonF([QPointF(x,y) for x,y in points]))

class Window(QMainWindow):
    integration_event=Signal(str,object)
    def __init__(self,workspace,remember=False,workspace_lock=None,defer_startup=False):
        super().__init__()
        self.remember=remember
        self.workspace_lock=workspace_lock or self.lock_workspace(workspace)
        self.catalogue=Catalogue(workspace)
        self.artwork=ArtworkCache(self);self.artwork.ready.connect(self.artwork_ready)
        self.artwork_timer=QTimer(self);self.artwork_timer.setSingleShot(True);self.artwork_timer.setInterval(60);self.artwork_timer.timeout.connect(self.load_visible_art)
        self.theme=valid_theme(self.catalogue.get_setting("theme",make_theme()))
        self.job=self.download_job=self.scan_job=None
        self.update_check_job=None
        self.close_pending=False
        self.resume_after_removal=False
        self.queue_ids=set()
        self.playlist_id=None
        self.setWindowTitle("ZotiBox")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint,True)
        self.setMinimumSize(1120,740)
        self.resize(1500,900)
        base=Backdrop(lambda:self.theme)
        self.setCentralWidget(base)
        outer=QVBoxLayout(base)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(0)
        self.titlebar=TitleBar(self)
        outer.addWidget(self.titlebar)
        body=QWidget()
        outer.addWidget(body,1)
        row=QHBoxLayout(body)
        row.setContentsMargins(0,0,0,0)
        row.setSpacing(0)
        sidebar=self.sidebar=QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(198)
        side=self.sidebar_layout=QVBoxLayout(sidebar)
        side.setContentsMargins(18,28,18,20)
        brand=self.sidebar_brand=QLabel("ZotiBox")
        brand.setObjectName("brand")
        side.addWidget(brand)
        side.addSpacing(24)
        self.navigation=HoverNavigation()
        self.navigation.setObjectName("navigation")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation_names=["Dashboard","Downloads","Local Music","Playlists","Settings / Setup"]
        self.navigation.addItems(self.navigation_names)
        for index,name in enumerate(self.navigation_names):self.navigation.item(index).setToolTip(name)
        side.addWidget(self.navigation)
        self.sidebar_version=muted(f"Version {VERSION}");side.addWidget(self.sidebar_version)
        row.addWidget(sidebar)
        content=QWidget()
        content.setObjectName("content")
        layout=QVBoxLayout(content)
        layout.setContentsMargins(26,22,26,16)
        header=QHBoxLayout()
        self.heading=QLabel("Dashboard")
        self.heading.setObjectName("heading")
        header.addWidget(self.heading)
        header.addStretch()
        self.scan_button=self.button("Scan music folders",self.scan,"SP_BrowserReload",True)
        header.addWidget(self.scan_button)
        layout.addLayout(header)
        self.workspace_label=muted(str(self.catalogue.workspace))
        layout.addWidget(self.workspace_label)
        self.pages=QStackedWidget()
        layout.addWidget(self.pages,1)
        self.build_dashboard()
        self.build_downloads()
        self.build_library()
        self.build_playlists()
        self.build_settings()
        self.player=MusicPlayer(self)
        layout.addWidget(self.player)
        self.setup_dragdrop()
        footer=QHBoxLayout()
        self.status=muted("Ready")
        footer.addWidget(self.status,1)
        self.activity=QProgressBar()
        self.activity.setRange(0,0)
        self.activity.setTextVisible(False)
        self.activity.setFixedSize(140,8)
        self.activity.hide()
        footer.addWidget(self.activity)
        self.cancel_button=self.button("Cancel task",self.cancel_task,"SP_DialogCancelButton")
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        row.addWidget(content,1)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.integration_event.connect(self.handle_integration_event)
        self.download_refresh=QTimer(self)
        self.download_refresh.setInterval(350)
        self.download_refresh.setSingleShot(True)
        self.download_refresh.timeout.connect(self.refresh_live)
        self.library_refresh=QTimer(self)
        self.library_refresh.setInterval(500)
        self.library_refresh.setSingleShot(True)
        self.library_refresh.timeout.connect(self.refresh_files)
        self.reset_integrations(load_preview=not defer_startup)
        self.apply_theme(self.theme)
        self.set_row_height(self.catalogue.get_setting("row_height",40))
        self.set_sidebar_collapsed(self.catalogue.get_setting('sidebar_collapsed',False))
        self.navigation.setCurrentRow(0)
        if defer_startup:self.status.setText("Loading library…");QTimer.singleShot(50,self.initial_load)
        else:self.refresh()

    def set_sidebar_collapsed(self,collapsed):
        self.sidebar.setFixedWidth(68 if collapsed else 198)
        self.sidebar_layout.setContentsMargins(8 if collapsed else 18,28,8 if collapsed else 18,20)
        self.sidebar_brand.setVisible(not collapsed);self.sidebar_version.setVisible(not collapsed)
        for index,name in enumerate(self.navigation_names):self.navigation.item(index).setText('' if collapsed else name)
        self.titlebar.menu_button.setToolTip('Expand navigation' if collapsed else 'Collapse navigation')
        self.catalogue.set_setting('sidebar_collapsed',bool(collapsed))

    def toggle_sidebar(self):self.set_sidebar_collapsed(not self.catalogue.get_setting('sidebar_collapsed',False))

    def play_row(self,row):
        try:
            if not row or row.get("kind")=="folder":return
            path=row.get("path") or row.get("output")
            if row.get("kind")=="file":path=self.catalogue.resolve(row)
            if not path and row.get("recording_id"):
                file=next((f for f in self.catalogue.files() if f["recording_id"]==row["recording_id"] and f["available"]),None)
                path=self.catalogue.resolve(file) if file else None
            if not path:raise ValueError("Download or match a local file before playback")
            native=next((t for t in self.rb_panel.data["tracks"] if os.path.normcase(t["path"])==os.path.normcase(str(path))),None)
            cues=row.get("cues",native.get("cues",[]) if native else [])
            self.player.load(path,f"{row.get('artist','')} — {row.get('title',Path(path).stem)}",cues,analysis=(native or row).get('analysis_paths',()))
        except Exception as exc:self.show_error(exc)

    def recent_context(self,point):
        row=self.recent_table.rowAt(point.y())
        if row<0:return
        data=self.recent_table.item(row,0).data(ROLE);menu=QMenu(self)
        menu.addAction("Play / preview",lambda:self.play_row(data))
        menu.addAction("Show in File Explorer",lambda:self.explorer(data["output"]))
        menu.exec(self.recent_table.viewport().mapToGlobal(point))

    def drag_data(self,rows,entries=False,native=False):
        recordings=[];files=self.catalogue.files()
        for row in rows:
            id_=row.get("recording_id")
            path=row.get("path") or row.get("output")
            if not id_ and path:
                file=next((f for f in files if os.path.normcase(str(self.catalogue.resolve(f)))==os.path.normcase(str(path))),None)
                id_=file["recording_id"] if file else None
            if id_:recordings.append(id_)
        if not recordings and not native:return None
        return {"workspace":str(self.catalogue.workspace),"recordings":recordings,
            "entries":[r["id"] for r in rows] if entries else [],"playlist":self.playlist_id if entries else None,
            "native_tracks":[r["id"] for r in rows] if native else [],
            "native_songs":[r.get("song_id") for r in rows] if native else [],
            "native_playlist":self.rb_panel.current_playlist() if native else None}

    def validate_drag(self,data):
        if data.get('kind') in {'playlist','queue'}:raise ValueError('Drop this selection into its own playlist or queue area')
        if data.get("workspace")!=str(self.catalogue.workspace):raise ValueError("These tracks come from a different database")
        ids=data.get("recordings",[])
        if not isinstance(ids,list) or len(ids)>10000 or any(not isinstance(i,str) for i in ids):raise ValueError("Invalid track selection")
        known={f["recording_id"] for f in self.catalogue.files()}
        if any(i not in known for i in ids):raise ValueError("A dragged track is no longer in this catalogue")
        return ids

    def drop_local(self,data,point,on_list=False):
        try:
            if on_list and data.get('kind')=='playlist':
                if data.get('workspace')!=str(self.catalogue.workspace) or data.get('scope')!='local':return False
                item=self.playlist_list.itemAt(point)
                folder=item.data(ROLE+2) if item else None
                if item and not folder:return False
                self.catalogue.move_playlist_item(data.get('id'),folder['id'] if folder else None,data.get('folder') is True)
                if folder:
                    expanded=set(self.catalogue.get_setting('playlist_expanded',[]));expanded.add(folder['id']);self.catalogue.set_setting('playlist_expanded',sorted(expanded))
                self.refresh_playlists();return True
            ids=self.validate_drag(data)
            item=self.playlist_list.itemAt(point) if on_list else self.playlist_list.currentItem()
            if not item:return False
            playlist=item.data(ROLE)
            if not playlist:return False
            if playlist['source']=='spotify' and not ids:raise ValueError('Spotify controls its songs. Drop available local tracks to add local extras.')
            if not on_list and data.get("playlist")==playlist["id"] and data.get("entries"):
                existing=[r["id"] for r in self.catalogue.entries(playlist["id"])]
                moved=[i for i in existing if i in data["entries"]]
                target=self.entries_table.insertion_row(point)
                position=len([i for i in existing[:target] if i not in moved]);remaining=[i for i in existing if i not in moved]
                self.catalogue.reorder(playlist["id"],remaining[:position]+moved+remaining[position:])
            else:
                if not ids:raise ValueError("Scan these tracks into Local Music before adding to a local playlist")
                self.catalogue.add_entries(playlist["id"],ids)
            self.playlist_id=playlist["id"];self.refresh_playlists()
            if not on_list and data.get('playlist')==playlist['id']:self.entries_table.restore_drag_selection(data.get('entries',[]))
            self.status.setText("Playlist updated");return True
        except Exception as exc:self.show_error(exc);return False

    def setup_dragdrop(self):
        self.files_table.setDragEnabled(True);self.files_table.drag_payload=lambda:self.drag_data(self.selected_files())
        self.entries_table.setDragEnabled(True);self.entries_table.setAcceptDrops(True);self.entries_table.setDropIndicatorShown(True)
        self.entries_table.drag_payload=lambda:self.drag_data([self.entries_table.item(i.row(),0).data(ROLE) for i in self.entries_table.selectionModel().selectedRows()],True)
        self.entries_table.drop_tracks=lambda data,point:self.drop_local(data,point)
        self.playlist_list.setAcceptDrops(True);self.playlist_list.drop_tracks=lambda data,point:self.drop_local(data,point,True)
        self.playlist_list.drag_payload=self.playlist_drag_data
        self.recent_table.setDragEnabled(True);self.recent_table.drag_payload=lambda:self.drag_data([self.recent_table.item(i.row(),0).data(ROLE) for i in self.recent_table.selectionModel().selectedRows()])
        self.playlist_hover=HoverTabs(self.playlist_tabs)
        self.rb_panel.setup_dragdrop()
        self.downloads_table.setDragEnabled(True);self.downloads_table.setAcceptDrops(True);self.downloads_table.setDropIndicatorShown(True)
        self.downloads_table.drag_payload=self.queue_drag_data;self.downloads_table.drop_tracks=self.drop_queue

    def icon(self,name):
        return outline_icon(name,QColor(self.theme["foreground"]["start"]))

    def button(self,text,callback,icon=None,primary=False):
        result=QPushButton(text)
        result.setProperty("primary",primary)
        if icon:
            result.setProperty("icon_name",icon)
            result.setIcon(outline_icon(icon,QColor(self.theme["selection_text" if primary else "foreground"]["start"])))
        result.clicked.connect(callback)
        return result

    def search_bar(self,placeholder):
        result=QLineEdit()
        result.setPlaceholderText(placeholder)
        result.setClearButtonEnabled(True)
        return result

    def page(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        layout.setContentsMargins(0,10,0,0)
        layout.setSpacing(14)
        self.pages.addWidget(page)
        return layout

    def row_size_control(self):
        widget=QWidget();bar=QHBoxLayout(widget);bar.setContentsMargins(0,0,0,0)
        bar.addWidget(QLabel("Row size"))
        slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(32,96);slider.setFixedWidth(120)
        slider.setValue(self.catalogue.get_setting("row_height",40));slider.setToolTip("Row and cover thumbnail size")
        slider.valueChanged.connect(self.set_row_height);bar.addWidget(slider)
        return widget

    def set_row_height(self,value):
        self.catalogue.set_setting("row_height",value)
        for table in self.findChildren(QTableWidget):
            table.verticalHeader().setDefaultSectionSize(value);table.setIconSize(QSize(value-8,value-8))
        for tree in self.findChildren(QTreeWidget):
            if tree is self.files_table:tree.setIconSize(QSize(value-8,value-8));tree.doItemsLayout()
        for slider in self.findChildren(QSlider):
            if slider.toolTip()=="Row and cover thumbnail size":
                block=QSignalBlocker(slider);slider.setValue(value);del block

    def initial_load(self):
        try:self.refresh();self.status.setText("Ready")
        except Exception as exc:self.status.setText("Library loading failed: "+str(exc))

    def thumbnail_path(self,row):
        path=row.get("path") or row.get("output")
        file=row if row.get("kind")=="file" else getattr(self,"_thumbnail_files",{}).get(row.get("recording_id"))
        if file:
            root=getattr(self,"_thumb_bindings",{}).get(file["root_id"])
            if root:path=Path(root).joinpath(*PurePosixPath(file["relative_path"]).parts)
        return str(path) if path else None

    def track_thumbnail(self,row):
        path=self.thumbnail_path(row);icon=self.artwork.get(path) if path else QIcon()
        return icon if not icon.isNull() else self.icon("music")

    def visible_art_items(self):
        if self.files_table.isVisible():
            seen=set()
            for y in range(0,self.files_table.viewport().height(),max(1,self.files_table.iconSize().height()+8)):
                item=self.files_table.itemAt(QPoint(5,y))
                if item and id(item) not in seen:seen.add(id(item));yield item,True
        for table in self.findChildren(QTableWidget):
            if not table.isVisible():continue
            first=table.rowAt(0)
            if first<0:continue
            last=table.rowAt(table.viewport().height()-1)
            if last<0:last=table.rowCount()-1
            column=1 if table.horizontalHeaderItem(0).text()=="#" else 0
            for row in range(first,min(last+1,table.rowCount())):
                item=table.item(row,column)
                if item:yield item,False

    def load_visible_art(self):
        for item,tree in self.visible_art_items():
            path=item.data(0,ROLE+1) if tree else item.data(ROLE+1)
            if path:
                icon=self.artwork.get(path)
                if not icon.isNull():
                    if tree:item.setIcon(0,icon)
                    else:item.setIcon(icon)
                else:self.artwork.request(path)

    def artwork_ready(self,path,image):
        icon=QIcon(QPixmap.fromImage(image)) if image is not None else QIcon()
        self.artwork.save(path,icon)
        if not icon.isNull():
            for item,tree in self.visible_art_items():
                key=item.data(0,ROLE+1) if tree else item.data(ROLE+1)
                if key==path:
                    if tree:item.setIcon(0,icon)
                    else:item.setIcon(icon)

    def table(self,columns):
        table=TrackTable(0,len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(self.catalogue.get_setting("row_height",40))
        height=self.catalogue.get_setting("row_height",40)
        table.setIconSize(QSize(height-8,height-8))
        table.horizontalHeader().setStretchLastSection(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setAlternatingRowColors(True)
        table.setItemDelegate(RowDelegate(table,lambda:self.theme))
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.verticalScrollBar().valueChanged.connect(lambda *_:self.artwork_timer.start())
        return table

    def build_dashboard(self):
        layout=self.page()
        bar=QHBoxLayout()
        self.dashboard_search=self.search_bar("Search Spotify, or paste a track / album / playlist URL")
        self.dashboard_search.returnPressed.connect(self.dashboard_find)
        bar.addWidget(self.dashboard_search,1)
        bar.addWidget(self.button("Find music",self.dashboard_find,"SP_FileDialogContentsView",True))
        layout.addLayout(bar)
        cards=QHBoxLayout()
        self.metrics={}
        for key,title in [("files","Local files"),("playlists","Playlists"),("queue","Queued downloads")]:
            card=QFrame()
            card.setObjectName("card")
            box=QVBoxLayout(card)
            box.setContentsMargins(20,20,20,20)
            box.addWidget(muted(title))
            number=QLabel("0")
            number.setObjectName("metric")
            self.metrics[key]=number
            box.addWidget(number)
            cards.addWidget(card)
        layout.addLayout(cards)
        bar=QHBoxLayout()
        bar.addStretch()
        for text,action,icon in [("Add music folder",self.add_root,"SP_DirOpenIcon"),("Local Music",lambda:self.navigation.setCurrentRow(2),"SP_DirIcon"),
            ("Playlists",lambda:self.navigation.setCurrentRow(3),"SP_FileDialogListView"),("Setup",lambda:self.navigation.setCurrentRow(4),"settings")]:
            button=self.button(text,action,icon);button.setMinimumSize(176,84);button.setIconSize(QSize(30,30));bar.addWidget(button)
        bar.addStretch()
        layout.addLayout(bar)
        self.connection_summary=muted("")
        layout.addWidget(self.connection_summary)
        layout.addWidget(QLabel("Recently downloaded"))
        self.recent_table=self.table(["Title","Artist","Downloaded file"])
        self.recent_table.setMaximumHeight(260)
        self.recent_table.setColumnWidth(0,360);self.recent_table.setColumnWidth(1,250)
        self.recent_table.itemDoubleClicked.connect(lambda item:self.play_row(item.data(ROLE)))
        self.recent_table.customContextMenuRequested.connect(self.recent_context)
        layout.addWidget(self.recent_table,1)
        self.recent_empty=muted("Completed downloads will appear here.");layout.addWidget(self.recent_empty)
        layout.addStretch()

    def build_downloads(self):
        layout=self.page()
        bar=QHBoxLayout()
        self.download_search=self.search_bar("Search Spotify, or paste a track / album / playlist URL")
        self.download_search.returnPressed.connect(self.find_downloads)
        bar.addWidget(self.download_search,1)
        self.search_kind=QComboBox()
        for label,key in [("Tracks","track"),("Albums","album"),("Playlists","playlist")]:
            self.search_kind.addItem(label,key)
        bar.addWidget(self.search_kind)
        bar.addWidget(self.button("Search",self.find_downloads,"SP_FileDialogContentsView"))
        layout.addLayout(bar)
        options=QHBoxLayout()
        self.codec,self.quality=QComboBox(),QComboBox()
        self.codec.addItems(["flac","copy","mp3","aac","ogg","opus","vorbis"])
        self.quality.addItems(["auto","lossless","very_high","high","normal"])
        stored=self.catalogue.get_setting("zotify_options",{})
        self.codec.setCurrentText(stored.get("DOWNLOAD_FORMAT","flac"))
        self.quality.setCurrentText(stored.get("DOWNLOAD_QUALITY","auto"))
        self.codec.currentTextChanged.connect(self.save_common_options)
        self.quality.currentTextChanged.connect(self.save_common_options)
        for title,control in [("Format",self.codec),("Source quality",self.quality)]:
            options.addWidget(QLabel(title))
            options.addWidget(control)
        options.addWidget(self.button("Zotify settings…",self.zotify_settings,"settings"))
        options.addStretch()
        speed=self.download_speed=muted("One active download · real-time pacing")
        speed.setWordWrap(False)
        options.addWidget(speed)
        layout.addLayout(options)
        layout.addWidget(muted("FLAC output alone does not establish a lossless source. The completed queue shows the selected input format."))
        self.results_table=self.table(["Name","Artist / owner","Type","Length / tracks"])
        self.results_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.results_table.customContextMenuRequested.connect(self.results_context)
        self.results_table.setColumnWidth(0,420)
        self.results_table.setColumnWidth(1,280)
        self.download_tabs=QTabWidget()
        self.download_tabs.addTab(self.results_table,"Search results")
        actions=QHBoxLayout()
        self.download_buttons={}
        for text,action,icon in [("Queue selected",self.queue_results,"SP_ArrowDown"),("Start / resume queue",self.start_downloads,"SP_MediaPlay"),
            ("Pause queue",self.pause_downloads,"SP_MediaPause"),("Retry failed",self.retry_downloads,"SP_BrowserReload"),
            ('Download next',self.download_selected_next,'SP_ArrowUp'),('Move up',lambda:self.move_queue_selection(-1),'SP_ArrowUp'),
            ('Move down',lambda:self.move_queue_selection(1),'SP_ArrowDown'),('Remove selected',self.remove_selected_downloads,'SP_DialogCancelButton')]:
            button=self.button(text,action,icon);self.download_buttons[text]=button;actions.addWidget(button)
        self.results_table.itemSelectionChanged.connect(self.update_download_controls)
        actions.addStretch()
        layout.addLayout(actions)
        self.downloads_table=self.table(['#',"Track","Artist",'Playlist / source',"State","Progress","Details"])
        self.downloads_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.downloads_table.setItemDelegate(DownloadDelegate(self.downloads_table,lambda:self.theme))
        self.downloads_table.customContextMenuRequested.connect(self.downloads_context)
        for i,width in enumerate([42,280,180,210,100,160]):self.downloads_table.setColumnWidth(i,width)
        self.downloads_table.itemSelectionChanged.connect(self.update_download_controls)
        remove=QAction(self.downloads_table);remove.setShortcut(QKeySequence('Delete'));remove.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove.triggered.connect(self.remove_selected_downloads);self.downloads_table.addAction(remove)
        self.download_tabs.addTab(self.downloads_table,"Download queue")
        self.queue_summary=muted("Queue empty")
        layout.addWidget(self.queue_summary)
        row=QHBoxLayout()
        for text,fn,icon in [('Playlist priority…',self.queue_playlist_priority,'SP_FileDialogListView'),
                ('Clear finished',self.clear_finished_downloads,'SP_DialogCancelButton'),('Clear queue…',self.clear_download_queue,'SP_DialogCancelButton')]:
            button=self.button(text,fn,icon);self.download_buttons[text]=button;row.addWidget(button)
        row.addStretch();layout.addLayout(row)
        self.queue_progress=QProgressBar()
        self.queue_progress.setRange(0,1000)
        self.queue_progress.setMinimumHeight(10);self.queue_progress.setTextVisible(False);self.queue_progress.setObjectName('queueProgress')
        self.queue_percent=muted('0% of queued audio')
        row=QHBoxLayout();row.addWidget(self.queue_progress,1);row.addWidget(self.queue_percent);layout.addLayout(row)
        layout.addWidget(self.download_tabs,1)
        self.download_tabs.currentChanged.connect(self.update_download_controls)

    def build_library(self):
        layout=self.page()
        bar=QHBoxLayout()
        self.search=self.search_bar("Filter title, artist, album, filename or ISRC")
        self.search_timer=QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.refresh_files)
        self.search.textChanged.connect(lambda:self.search_timer.start())
        bar.addWidget(self.search,1)
        self.duplicates=QCheckBox("Exact duplicates")
        self.duplicates.toggled.connect(self.refresh_files)
        bar.addWidget(self.duplicates)
        bar.addWidget(self.row_size_control())
        bar.addWidget(self.button("Add folder",self.add_root,"SP_DirOpenIcon"))
        self.add_library_button=self.button("Add selected to playlist",self.add_selected,"SP_FileDialogListView");self.add_library_button.hide();bar.addWidget(self.add_library_button)
        layout.addLayout(bar)
        tree=self.files_table=LibraryTree(lambda:self.theme)
        tree.setHeaderLabels(["Folder / title","Artist","Album","Format","Length","File created","Status","Filename"])
        tree.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        height=self.catalogue.get_setting("row_height",40)
        tree.setIconSize(QSize(height-8,height-8))
        tree.verticalScrollBar().valueChanged.connect(lambda *_:self.artwork_timer.start())
        tree.itemExpanded.connect(lambda *_:self.artwork_timer.start())
        tree.setAllColumnsShowFocus(False)
        tree.setItemDelegate(RowDelegate(tree,lambda:self.theme))
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self.files_context)
        tree.itemSelectionChanged.connect(self.file_selection)
        tree.itemDoubleClicked.connect(lambda item,col:self.play_row(item.data(0,ROLE)))
        tree.header().setStretchLastSection(True)
        for index,width in enumerate([330,190,200,80,70,165,140]):
            tree.setColumnWidth(index,width)
        layout.addWidget(tree,1)
        self.details=muted("File created uses Windows creation time. Modified time is available in file details.")
        layout.addWidget(self.details)

    def build_playlists(self):
        page=self.page()
        self.playlist_tabs=QTabWidget()
        page.addWidget(self.playlist_tabs)
        panel=QWidget()
        layout=QVBoxLayout(panel)
        layout.setContentsMargins(0,8,0,0)
        self.playlist_tabs.addTab(panel,"ZotiBox playlists")
        bar=QHBoxLayout()
        for label,buttons in [('Create',[
                ('New playlist',self.new_playlist,'SP_FileIcon',False),('New folder',self.new_playlist_folder,'SP_DirIcon',False)]),
            ('Spotify links',[('Link playlist…',self.link_spotify,'SP_DialogOpenButton',False)]),
            ('Synchronise',[('Sync all linked',self.sync_all,'SP_BrowserReload',True),
                ('Sync with Rekordbox…',lambda:self.rb_panel.sync_many(),'SP_ArrowRight',True)])]:
            group=QWidget();group_layout=QVBoxLayout(group);group_layout.setContentsMargins(0,0,12,0)
            group_layout.addWidget(muted(label));buttons_row=QHBoxLayout();group_layout.addLayout(buttons_row)
            for text,action,icon,primary in buttons:buttons_row.addWidget(self.button(text,action,icon,primary))
            bar.addWidget(group)
        bar.addStretch()
        layout.addLayout(bar)
        bar=QHBoxLayout()
        self.queue_missing_button=self.button('Queue missing',self.queue_missing,'SP_ArrowDown');bar.addWidget(self.queue_missing_button)
        self.review_all_button=self.button('Review all',lambda:self.review_needed(True),'SP_FileDialogContentsView');bar.addWidget(self.review_all_button)
        bar.addStretch();bar.addWidget(self.button('Export…',self.export_menu,'SP_DialogSaveButton'));layout.addLayout(bar)
        splitter=QSplitter()
        panel=QWidget()
        left=QVBoxLayout(panel)
        left.setContentsMargins(0,0,10,0)
        self.playlist_filter=QComboBox()
        self.playlist_filter.addItems(["All playlists","Local","Spotify linked","Rekordbox linked"])
        self.playlist_filter.currentIndexChanged.connect(self.refresh_playlists)
        left.addWidget(self.playlist_filter)
        self.playlist_list=PlaylistTree()
        self.playlist_list.setIconSize(QSize(20,20))
        self.playlist_list.setItemDelegate(RowDelegate(self.playlist_list,lambda:self.theme))
        self.playlist_list.currentItemChanged.connect(self.refresh_entries)
        self.playlist_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.playlist_list.customContextMenuRequested.connect(self.playlist_context)
        self.playlist_list.itemExpanded.connect(self.save_playlist_expansion)
        self.playlist_list.itemCollapsed.connect(self.save_playlist_expansion)
        left.addWidget(self.playlist_list,1)
        splitter.addWidget(panel)
        panel=QWidget()
        right=QVBoxLayout(panel)
        right.setContentsMargins(10,0,0,0)
        self.playlist_summary=muted("Select a playlist")
        right.addWidget(self.playlist_summary)
        self.entries_table=self.table(["#","Title","Artist","Length","Local match"])
        self.entries_table.customContextMenuRequested.connect(self.entries_context)
        self.entries_table.itemDoubleClicked.connect(lambda item:self.play_row(item.data(ROLE)))
        for index,width in enumerate([45,360,240,80]):
            self.entries_table.setColumnWidth(index,width)
        right.addWidget(self.entries_table,1)
        actions=QHBoxLayout()
        self.up_button=self.button("Move up",lambda:self.move_entry(-1),"SP_ArrowUp")
        self.down_button=self.button("Move down",lambda:self.move_entry(1),"SP_ArrowDown")
        self.remove_entry_button=self.button("Remove entry",self.remove_entry,"SP_DialogCancelButton")
        for control in [self.up_button,self.down_button,self.remove_entry_button]:
            actions.addWidget(control)
        self.review_button=self.button("Review needed…",self.review_needed,"SP_FileDialogContentsView");actions.addWidget(self.review_button)
        actions.addStretch()
        right.addLayout(actions)
        splitter.addWidget(panel)
        splitter.setSizes([250,1050])
        layout.addWidget(splitter,1)
        right.addWidget(muted("Sync all linked → Queue missing → download → Sync with Rekordbox → review and Apply."))
        right.addWidget(self.row_size_control())
        self.rb_panel=RekordboxPanel(self)
        self.playlist_tabs.addTab(self.rb_panel,"Rekordbox")
        self.playlist_tabs.currentChanged.connect(self.rb_panel.ensure_loaded)

    def build_settings(self):
        layout=self.page()
        row=QHBoxLayout()
        self.settings_navigation=QListWidget()
        self.settings_navigation.setObjectName("navigation")
        self.settings_navigation.setFixedWidth(210)
        self.settings_stack=QStackedWidget()
        self.settings_icons=[]
        row.addWidget(self.settings_navigation)
        row.addWidget(self.settings_stack,1)
        layout.addLayout(row,1)
        def section(title,icon,description):
            self.settings_icons.append(icon)
            self.settings_navigation.addItem(QListWidgetItem(self.icon(icon),title))
            scroll=QScrollArea()
            scroll.setWidgetResizable(True)
            body=QWidget()
            body.setObjectName("settings_body")
            body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            form=QVBoxLayout(body)
            form.setContentsMargins(28,16,24,24)
            form.setSpacing(18)
            heading=QLabel(title)
            heading.setObjectName("heading")
            form.addWidget(heading)
            form.addWidget(muted(description))
            scroll.setWidget(body)
            self.settings_stack.addWidget(scroll)
            return form
        form=section("Library folders","SP_DirIcon","Local discovery runs alongside Spotify tasks and downloads.")
        form.addWidget(QLabel("Scanned music folders"))
        self.roots_list=QListWidget()
        self.roots_list.setFixedHeight(135)
        form.addWidget(self.roots_list)
        bar=QHBoxLayout()
        for text,action,icon in [("Add folder",self.add_root,"SP_DirOpenIcon"),("Map folder",self.map_root,"SP_DirIcon"),
            ("Remove selected folder",self.remove_root,"SP_DialogCancelButton")]:
            bar.addWidget(self.button(text,action,icon))
        bar.addStretch()
        form.addLayout(bar)
        form.addStretch()
        form=section("Appearance","SP_FileDialogDetailedView","Choose a preset or customise background, accent and text size.")
        from .themes import PRESETS, theme_sample
        from PySide6.QtCore import QSize
        self.theme_samples=QListWidget()
        self.theme_samples.setViewMode(QListWidget.ViewMode.IconMode)
        self.theme_samples.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.theme_samples.setIconSize(QSize(150,65))
        self.theme_samples.setGridSize(QSize(190,105))
        self.theme_samples.setMinimumHeight(260)
        self.theme_samples.setMaximumHeight(390)
        for name,theme in PRESETS.items():
            item=QListWidgetItem(theme_sample(theme),name)
            item.setData(ROLE,name)
            self.theme_samples.addItem(item)
        self.theme_samples.itemClicked.connect(lambda item:self.choose_theme_preset(item.data(ROLE)))
        form.addWidget(self.theme_samples)
        bar=QHBoxLayout()
        bar.addWidget(self.button("Customise colours…",self.edit_theme,"SP_FileDialogDetailedView"))
        bar.addWidget(self.button("Update desktop icon",self.update_desktop_icon,"SP_DesktopIcon"))
        bar.addStretch()
        form.addLayout(bar)
        form.addStretch()
        form=section("Spotify account","SP_FileDialogContentsView","Connect playlists and search using your developer Client ID.")
        form.addWidget(QLabel("Spotify developer app"))
        spotify_form=QFormLayout()
        self.client_edit=QLineEdit(self.catalogue.get_setting("spotify_client_id",""))
        self.client_edit.setPlaceholderText("Your developer Client ID — no client secret required")
        self.redirect=QLineEdit(REDIRECT_URI)
        self.redirect.setReadOnly(True)
        zotify_options=self.catalogue.get_setting('zotify_options',{})
        second=self.zotify_redirect_reference=QLineEdit(f"http://{zotify_options.get('REDIRECT_ADDRESS','127.0.0.1')}:{zotify_options.get('REDIRECT_PORT',4381)}/login")
        second.setReadOnly(True)
        for title,control in [("Client ID",self.client_edit),("Playlist login redirect",self.redirect),("Zotify login redirect",second)]:
            spotify_form.addRow(title,control)
        form.addLayout(spotify_form)
        self.spotify_status=muted("Spotify: not connected")
        form.addWidget(self.spotify_status)
        bar=QHBoxLayout()
        for text,action in [("Save Client ID",self.save_client),("Connect Spotify",self.connect_spotify),("Disconnect Spotify",self.disconnect_spotify),("Setup instructions…",self.spotify_instructions)]:
            bar.addWidget(self.button(text,action))
        bar.addStretch()
        form.addLayout(bar)
        form.addStretch()
        form=section("Downloads","SP_ArrowDown","One track at a time. Remaining time is estimated from audio duration.")
        form.addWidget(QLabel("Zotify downloads"))
        bar=QHBoxLayout()
        self.download_folder=QLineEdit(self.catalogue.get_setting("download_folder",str(self.catalogue.workspace/"Music")))
        self.download_folder.setReadOnly(True)
        bar.addWidget(self.download_folder,1)
        bar.addWidget(self.button("Choose download folder…",self.choose_download_folder,"SP_DirOpenIcon"))
        form.addLayout(bar)
        self.auto_download=QCheckBox("Automatically download missing tracks after a Spotify sync")
        self.auto_download.setChecked(self.catalogue.get_setting("auto_download",False))
        self.auto_download.toggled.connect(lambda value:self.catalogue.set_setting("auto_download",value))
        form.addWidget(self.auto_download)
        form.addWidget(QLabel('When a FLAC request has only a lossy source'))
        self.lossy_fallback=QComboBox();self.lossy_fallback.addItem('MP3 320 kbps — DJ compatibility','mp3');self.lossy_fallback.addItem('Original format — no additional lossy encoding','native')
        self.lossy_fallback.setCurrentIndex(max(0,self.lossy_fallback.findData(self.catalogue.get_setting('lossy_fallback','mp3'))))
        self.lossy_fallback.currentIndexChanged.connect(lambda _:self.catalogue.set_setting('lossy_fallback',self.lossy_fallback.currentData()))
        form.addWidget(self.lossy_fallback);form.addWidget(muted('MP3 conversion adds another lossy encoding step. Neither fallback is labelled lossless. Applies to subsequent downloads.'))
        self.zotify_status=muted("Zotify: not connected")
        form.addWidget(self.zotify_status)
        for actions in [[("Connect Zotify",self.connect_zotify),("Fresh Zotify login",lambda:self.connect_zotify(True)),('Open pending login',self.open_pending_login)],
            [('Last download error…',self.last_download_error),('Zotify settings…',self.zotify_settings)]]:
            bar=QHBoxLayout()
            for text,action in actions:bar.addWidget(self.button(text,action))
            bar.addStretch();form.addLayout(bar)
        form.addStretch()
        self.zotify_settings_index=self.settings_stack.count()
        form=section('Zotify','SP_MediaPlay','Downloader preferences, file naming, metadata, lyrics and connection options.')
        self.zotify_panel=ZotifySettings(self);form.addWidget(self.zotify_panel)
        form=section("Database","SP_DialogOpenButton","Open, import or relocate your catalogue.")
        form.addWidget(QLabel("ZotiBox Database"))
        bar=QHBoxLayout()
        self.database_path=QLineEdit(str(self.catalogue.workspace))
        bar.addWidget(self.database_path,1)
        bar.addWidget(self.button("Browse…",self.browse_database,"SP_DirOpenIcon"))
        bar.addWidget(self.button("Relocate database",self.relocate_database,"SP_ArrowRight"))
        form.addLayout(bar)
        form.addWidget(muted("Defaults beside ZotiBox.exe. Relocation verifies a copy and opens it; the original folder is retained."))
        bar=QHBoxLayout()
        bar.addWidget(self.button("Open database…",self.open_workspace,"SP_DirOpenIcon"))
        bar.addWidget(self.button("Import database package…",self.import_database,"SP_DialogOpenButton"))
        bar.addStretch()
        form.addLayout(bar)
        form.addStretch()
        form=section("Updates","SP_BrowserReload","Check GitHub releases and install updates over the current version. Your library is preserved.")
        form.addWidget(QLabel(f"Installed version: {VERSION}"))
        self.update_source=QComboBox()
        self.update_source.addItems(["GitHub releases (Kit-Leeming/zotibox)","Local / shared folder"])
        self.update_source.setCurrentIndex(self.catalogue.get_setting('update_source',0))
        self.update_source.currentIndexChanged.connect(lambda value:self.catalogue.set_setting('update_source',value))
        form.addWidget(self.update_source)
        self.update_folder=QLineEdit(self.catalogue.get_setting("update_folder",""))
        self.update_folder.setReadOnly(True)
        self.update_folder.setPlaceholderText("Folder containing ZotiBox installers")
        form.addWidget(self.update_folder)
        self.update_folder.setVisible(self.update_source.currentIndex()==1)
        self.update_source.currentIndexChanged.connect(lambda value:self.update_folder.setVisible(value==1))
        bar=QHBoxLayout()
        bar.addWidget(self.button("Choose update folder…",self.choose_update_folder,"SP_DirOpenIcon"))
        bar.addWidget(self.button("Check for updates",self.check_updates,"SP_BrowserReload"))
        self.install_update_button=self.button("Install update",self.install_update,"SP_ArrowDown",True)
        self.install_update_button.setEnabled(False)
        bar.addWidget(self.install_update_button)
        bar.addStretch()
        form.addLayout(bar)
        self.update_status=muted("No update checked yet")
        form.addWidget(self.update_status)
        self.available_update=None
        self.startup_updates=QCheckBox('Check for updates when ZotiBox starts')
        self.startup_updates.setChecked(self.catalogue.get_setting('check_updates_on_start',True))
        self.startup_updates.toggled.connect(lambda value:self.catalogue.set_setting('check_updates_on_start',value))
        form.addWidget(self.startup_updates)
        form.addStretch()
        form=section("Rekordbox","SP_FileDialogListView","Read the collection and prepare changes on a copy. Live writes require explicit opt-in and an automatic backup.")
        self.rb_path=QLineEdit(str(self.rb_panel.backend.source))
        self.rb_path.setReadOnly(True)
        form.addWidget(self.rb_path)
        bar=QHBoxLayout()
        bar.addWidget(self.button("Choose master.db…",self.choose_rb_database,"SP_DirOpenIcon"))
        bar.addWidget(self.button("Connect / refresh",self.rb_panel.connect,"SP_BrowserReload"))
        bar.addWidget(self.button("Open Rekordbox view",lambda:(self.navigation.setCurrentRow(3),self.playlist_tabs.setCurrentIndex(1)),"SP_FileDialogListView"))
        bar.addStretch();form.addLayout(bar)
        form.addWidget(muted("Before enabling writes, you can also use Rekordbox: File > Library > Backup Library. Close Rekordbox for backups and Apply."))
        self.rb_write=QCheckBox("Enable direct Rekordbox database modifications")
        self.rb_write.setChecked(self.catalogue.get_setting("rekordbox_write_enabled",False))
        self.rb_write.toggled.connect(self.enable_rb_write)
        form.addWidget(self.rb_write)
        form.addWidget(muted("Apply reuses its verified backup while Rekordbox matches the last ZotiBox apply. External changes require a new backup. Restore always creates one. Music files are not included."))
        bar=QHBoxLayout()
        bar.addWidget(self.button("Back up now",self.backup_rb,"SP_DialogSaveButton"))
        bar.addWidget(self.button("Open backups folder",lambda:self.explorer(self.rb_panel.backend.root/"backups",False),"SP_DirOpenIcon"))
        bar.addWidget(self.button("Restore selected backup…",self.restore_rb,"SP_BrowserReload"))
        bar.addStretch();form.addLayout(bar)
        self.rb_backups=QListWidget();self.rb_backups.setMinimumHeight(180);form.addWidget(self.rb_backups)
        self.refresh_rb_backups()
        form.addStretch()
        self.settings_navigation.currentRowChanged.connect(self.settings_stack.setCurrentIndex)
        self.settings_navigation.setCurrentRow(0)

    def choose_theme_preset(self,name):
        from .themes import PRESETS
        self.apply_theme(copy.deepcopy(PRESETS[name]))
        self.catalogue.set_setting("theme",self.theme)

    def choose_update_folder(self):
        folder=QFileDialog.getExistingDirectory(self,"Choose update folder")
        if folder:
            self.catalogue.set_setting("update_folder",folder)
            self.update_folder.setText(folder)
            self.update_source.setCurrentIndex(1)
            self.check_updates()

    def check_updates(self):
        def complete(update):
            self.available_update=update
            self.install_update_button.setEnabled(bool(update))
            self.update_status.setText(f"Version {update['version']} is available" if update else "No newer release available")
        self.install_update_button.setEnabled(False)
        self.start_job("Checking for updates",lambda progress,cancel:github_update() if self.update_source.currentIndex()==0 else find_update(self.update_folder.text()),complete)

    def schedule_startup_checks(self):
        if self.catalogue.get_setting('matching_revision',0)<2:
            QTimer.singleShot(500,self.upgrade_match_decisions)
        if not self.catalogue.get_setting('check_updates_on_start',True):return
        source=self.catalogue.get_setting('update_source',0);folder=self.catalogue.get_setting('update_folder','')
        def check():
            try:
                update=github_update() if source==0 else find_update(folder)
                self.integration_event.emit('startup_update',update)
            except Exception:
                try:self.integration_event.emit('startup_update_error',None)
                except RuntimeError:pass
        threading.Thread(target=check,daemon=True,name='ZotiBox update check').start()

    def upgrade_match_decisions(self):
        if self.close_pending:return
        if self.job:QTimer.singleShot(1000,self.upgrade_match_decisions);return
        def complete(count):self.status.setText(f'Match review updated: {count} track(s) classified')
        self.start_job('Updating match review',lambda progress,cancel:reclassify_reviews(self.catalogue,progress,cancel),complete)

    def prompt_startup_update(self):
        if self.close_pending or not self.isVisible():return
        if self.job or self.scan_job or self.download_job:QTimer.singleShot(1500,self.prompt_startup_update);return
        if not self.available_update:return
        box=QMessageBox(QMessageBox.Icon.Information,'ZotiBox update available',
            f"ZotiBox {self.available_update['version']} is available. Install the update now? Your library and settings will be kept.",parent=self)
        install=box.addButton('Update now',QMessageBox.ButtonRole.AcceptRole)
        later=box.addButton('Later',QMessageBox.ButtonRole.RejectRole);box.setDefaultButton(later);box.exec()
        if box.clickedButton()==install:self.install_update()

    def install_update(self):
        try:
            self.ensure_idle()
            update=self.available_update
            if not update:return
            def launch(path):
                if self.remember:QSettings().setValue("database",str(self.catalogue.workspace))
                if self.job:
                    self.pending_installer=path
                else:
                    launch_installer(path)
                    self.close()
            if "url" in update:
                self.start_job("Downloading verified update",lambda progress,cancel:download_update(update,self.catalogue.workspace/".local"/"updates",progress,cancel),launch)
            else:launch(update["path"])
        except Exception as exc:self.show_error(exc)

    def choose_rb_database(self):
        try:
            self.ensure_idle()
            if self.rb_panel.backend.state and self.rb_panel.backend.state["changes"]:raise ValueError("Discard or apply the pending Rekordbox preview first")
            path,_=QFileDialog.getOpenFileName(self,"Choose Rekordbox database",str(self.rb_panel.backend.source),"Rekordbox database (master.db)")
            if path:
                self.catalogue.set_setting("rekordbox_path",path)
                self.catalogue.set_setting("rekordbox_write_enabled",False)
                self.rb_panel.backend.state=None
                self.rb_panel.backend.state_path.unlink(missing_ok=True)
                self.rb_panel.data={"tracks":[],"playlists":[],"colors":[],"changes":[]}
                self.rb_panel.refresh_tree()
                self.rb_path.setText(path)
                self.rb_write.blockSignals(True);self.rb_write.setChecked(False);self.rb_write.blockSignals(False)
                self.rb_panel.connect()
        except Exception as exc:self.show_error(exc)

    def enable_rb_write(self,enabled):
        if not enabled:
            self.catalogue.set_setting("rekordbox_write_enabled",False)
            return
        self.rb_write.blockSignals(True);self.rb_write.setChecked(False);self.rb_write.blockSignals(False)
        if QMessageBox.question(self,"Enable Rekordbox writes","You can cancel and make a native Rekordbox backup first. Close Rekordbox before continuing.\n\nCreate a verified data-directory backup now and enable writes?")!=QMessageBox.StandardButton.Yes:return
        def complete(archive):
            self.catalogue.set_setting("rekordbox_write_enabled",True)
            self.rb_write.blockSignals(True);self.rb_write.setChecked(True);self.rb_write.blockSignals(False)
            self.status.setText(f"Rekordbox writes enabled; backup: {archive.name}");self.refresh_rb_backups()
        self.start_job("Backing up before enabling writes",lambda progress,cancel:self.rb_panel.backend.backup(progress,cancel,"enable-writes"),complete)

    def refresh_rb_backups(self):
        self.rb_backups.clear()
        for path in self.rb_panel.backend.backups():
            item=QListWidgetItem(f"{path.name} · {path.stat().st_size/1048576:.1f} MB")
            item.setData(ROLE,str(path));item.setIcon(self.icon("SP_DialogSaveButton"));self.rb_backups.addItem(item)

    def backup_rb(self):
        def complete(path):self.status.setText(f"Rekordbox backup verified: {path.name}");self.refresh_rb_backups()
        self.start_job("Backing up Rekordbox",lambda progress,cancel:self.rb_panel.backend.backup(progress,cancel),complete)

    def restore_rb(self):
        item=self.rb_backups.currentItem()
        if not item:return
        if QMessageBox.question(self,"Restore Rekordbox backup","Restore the selected data backup? A new backup of the current library will be created first. Close Rekordbox before continuing.")!=QMessageBox.StandardButton.Yes:return
        def work(progress,cancel):
            message=self.rb_panel.backend.restore(item.data(ROLE),progress,cancel)
            return message,self.rb_panel.backend.view()
        def complete(result):self.rb_panel.loaded(result[1]);self.status.setText(result[0]);self.refresh_rb_backups()
        self.start_job("Restoring Rekordbox backup",work,complete)

    def preview_renames(self):
        try:
            self.ensure_idle()
            plan=rename_plan(self.catalogue)
            dialog=QDialog(self)
            dialog.setWindowTitle("Preview download library filenames")
            dialog.resize(1000,650)
            layout=QVBoxLayout(dialog)
            table=self.table(["Current filename","Proposed filename","Status"])
            self.fill_table(table,plan,lambda i,row:[row["source"].name,row["target"].name,row["status"]])
            layout.addWidget(table)
            count=sum(row["status"]=="Ready" for row in plan)
            layout.addWidget(muted(f"{count} file(s) ready. Playlist identities and audio contents are preserved."))
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(count>0)
            def apply():
                try:
                    self.ensure_idle()
                    try:
                        changed=apply_renames(self.catalogue,plan)
                    finally:
                        self.update_exports()
                        self.refresh()
                    self.status.setText(f"Renamed {changed} file(s)")
                    dialog.accept()
                except Exception as exc:
                    self.show_error(exc)
            buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()
        except Exception as exc:
            self.show_error(exc)

    def navigate(self,index):
        self.scan_button.setVisible(index==2)
        self.pages.setCurrentIndex(index)
        self.heading.setText(self.navigation_names[index])
        self.artwork_timer.start()
        if index==3:self.rb_panel.ensure_loaded()

    def reset_integrations(self,load_preview=True):
        self.spotify=Spotify(self.catalogue.get_setting("spotify_client_id",""),TokenStore(self.catalogue.workspace))
        self.engine=DownloadEngine(self.catalogue,self.integration_event.emit)
        self.pending_login=None
        self.rb_panel.backend=Rekordbox(self.catalogue)
        self.rb_panel.preview_loaded=False
        if self.rb_panel.backend.load_error:self.rb_panel.summary.setText("Saved preview cannot load: "+self.rb_panel.backend.load_error+". Refresh to reconnect; recovery data is retained.")
        if load_preview and self.rb_panel.backend.state:
            try:self.rb_panel.loaded(self.rb_panel.backend.view())
            except Exception as exc:self.rb_panel.summary.setText("Saved preview could not load: "+str(exc)+". Use Refresh or Discard preview.")
        with self.catalogue.connect() as db:
            db.execute("DELETE FROM downloads WHERE state='removing'")
            db.execute("UPDATE downloads SET state='paused',message='Interrupted session; resume to retry' WHERE state='running'")

    def refresh(self):
        self.rb_path.setText(str(self.rb_panel.backend.source))
        self.rb_write.blockSignals(True);self.rb_write.setChecked(self.catalogue.get_setting("rekordbox_write_enabled",False));self.rb_write.blockSignals(False)
        self.workspace_label.setText(str(self.catalogue.workspace))
        self.database_path.setText(str(self.catalogue.workspace))
        self.refresh_files()
        self.refresh_playlists()
        self.refresh_downloads()
        self.roots_list.clear()
        for root in self.catalogue.roots():
            item=QListWidgetItem(f"{root['name']}\n{root['path'] or 'Not mapped on this PC'}")
            item.setData(ROLE,root["id"])
            item.setIcon(self.icon("SP_DirIcon"))
            self.roots_list.addItem(item)
        self.metrics["files"].setText(str(len(self.catalogue.files())))
        self.metrics["playlists"].setText(str(len(self.catalogue.playlists())))
        connected=(self.catalogue.workspace/".local"/"spotify.bin").exists()
        zotify=(self.catalogue.workspace/".local"/"zotify-session.bin").exists()
        self.spotify_status.setText("Spotify: login saved" if connected else "Spotify: not connected")
        self.zotify_status.setText("Zotify: login saved" if zotify else "Zotify: not connected")
        self.connection_summary.setText(f"Spotify: {'login saved' if connected else 'not connected'} · Zotify: {'login saved' if zotify else 'not connected'}")

    def refresh_files(self):
        self._thumb_bindings=self.catalogue.bindings()
        selected_ids={row["id"] for row in self.selected_files()}
        scroll=self.files_table.verticalScrollBar().value()
        expanded=set()
        def walk(item):
            data=item.data(0,ROLE) or {}
            if item.isExpanded():
                expanded.add((data.get("root_id"),data.get("folder")))
            for index in range(item.childCount()):
                walk(item.child(index))
        for index in range(self.files_table.topLevelItemCount()):
            walk(self.files_table.topLevelItem(index))
        block=QSignalBlocker(self.files_table)
        self.files_table.setSortingEnabled(False)
        self.files_table.clear()
        roots={}
        for root in self.catalogue.roots():
            item=LibraryItem([root["name"]])
            item.setData(0,ROLE,{"kind":"folder","root_id":root["id"],"folder":""})
            item.setIcon(0,self.icon("SP_DirIcon"))
            self.files_table.addTopLevelItem(item)
            item.setExpanded(True)
            roots[root["id"]]=item
        folders={}
        for row in self.catalogue.files(self.search.text(),self.duplicates.isChecked()):
            parent=roots[row["root_id"]]
            parts=PurePosixPath(row["relative_path"]).parts
            for depth,part in enumerate(parts[:-1]):
                folder="/".join(parts[:depth+1])
                key=(row["root_id"],folder)
                if key not in folders:
                    child=LibraryItem([part])
                    child.setData(0,ROLE,{"kind":"folder","root_id":row["root_id"],"folder":folder})
                    child.setIcon(0,self.icon("SP_DirIcon"))
                    parent.addChild(child)
                    child.setExpanded(key in expanded or bool(self.search.text()))
                    folders[key]=child
                parent=folders[key]
            row["kind"]="file"
            path=self.thumbnail_path(row)
            state="Available" if row["available"] and path else "Missing / unmapped"
            if row["copies"]>1:
                state+=" · duplicate"
            item=LibraryItem([row["title"],row["artist"],row["album"],row["codec"],duration(row["duration"]),date_ns(row["created_ns"]),state,parts[-1]])
            row["kind"]="file"
            item.setData(0,ROLE,row)
            item.setToolTip(0,str(path or row["relative_path"]))
            item.setIcon(0,self.track_thumbnail(row))
            item.setData(0,ROLE+1,self.thumbnail_path(row))
            parent.addChild(item)
            if row["id"] in selected_ids:
                item.setSelected(True)
        self.files_table.setSortingEnabled(True)
        self.files_table.sortItems(0,Qt.SortOrder.AscendingOrder)
        self.files_table.verticalScrollBar().setValue(scroll)
        del block

    def fill_table(self,table,rows,values):
        self._thumb_bindings=self.catalogue.bindings()
        self._thumbnail_files={f["recording_id"]:f for f in self.catalogue.files() if f["available"]}
        block=QSignalBlocker(table)
        table.setRowCount(len(rows))
        for index,row in enumerate(rows):
            for column,value in enumerate(values(index,row)):
                item=QTableWidgetItem(str(value))
                item.setData(ROLE,row)
                item.setToolTip(str(value))
                table.setItem(index,column,item)
                if column==(1 if table.horizontalHeaderItem(0).text()=="#" else 0):
                    item.setIcon(self.track_thumbnail(row))
                    item.setData(ROLE+1,self.thumbnail_path(row))
        del block
        self.artwork_timer.start()

    def refresh_playlists(self):
        previous=self.playlist_id
        block=QSignalBlocker(self.playlist_list)
        self.playlist_list._stop_hover()
        selected=self.playlist_list.currentItem()
        selected_folder=selected.data(ROLE+2) if selected else None
        self.playlist_list.clear()
        folders={}
        for folder in self.catalogue.playlist_folders():
            item=PlaylistItem([folder['name']]);item.setData(ROLE+2,folder);item.setIcon(0,self.icon('SP_DirIcon'));folders[folder['id']]=item
        for folder in self.catalogue.playlist_folders():
            parent=folders.get(folder['parent_id'])
            if parent:parent.addChild(folders[folder['id']])
            else:self.playlist_list.addTopLevelItem(folders[folder['id']])
        locations=self.catalogue.playlist_locations()
        for playlist in self.catalogue.playlists():
            choice=self.playlist_filter.currentIndex()
            if choice==1 and playlist["source"]!="manual" or choice==2 and playlist["source"]!="spotify" or choice==3 and not playlist["rekordbox_id"]:
                continue
            label="Spotify linked" if playlist["source"]=="spotify" else "Local"
            if playlist["rekordbox_id"]:
                label+=" · Rekordbox linked"
            item=PlaylistItem([playlist['name']])
            item.setToolTip(0,f"{label} · {playlist['count']} tracks")
            item.setData(ROLE,playlist)
            item.setIcon(0,self.icon("SP_FileDialogListView"))
            parent=folders.get(locations.get(playlist['id']))
            if parent:parent.addChild(item)
            else:self.playlist_list.addTopLevelItem(item)
            if playlist["id"]==previous:
                self.playlist_list.setCurrentItem(item)
        if selected_folder and not previous and selected_folder['id'] in folders:
            self.playlist_list.setCurrentItem(folders[selected_folder['id']])
        if not self.playlist_list.currentItem() and self.playlist_list.count():
            self.playlist_list.setCurrentRow(0)
        expanded=set(self.catalogue.get_setting('playlist_expanded',[]))
        for id_,item in folders.items():item.setExpanded(id_ in expanded)
        del block
        self.refresh_entries()

    def refresh_entries(self,*_):
        item=self.playlist_list.currentItem()
        playlist=item.data(ROLE) if item else None
        self.playlist_id=playlist["id"] if playlist else None
        entries=self.catalogue.entries(self.playlist_id) if self.playlist_id else []
        self.fill_table(self.entries_table,entries,lambda i,row:[str(i+1),row["title"],row["artist"],duration(row["duration"]),
            row["match_state"].capitalize() if row["spotify_id"] else (("Local extra · " if playlist and playlist['source']=='spotify' else '')+("Available" if self.catalogue.available_recording(row["recording_id"]) else "Missing"))])
        manual=bool(playlist and playlist["source"]=="manual")
        for control in [self.up_button,self.down_button,self.remove_entry_button]:
            control.setEnabled(manual)
            control.setVisible(False)
        review_count=len({e['spotify_id'] for e in entries if e['match_state']=='review' and e['spotify_id']})
        self.review_button.setVisible(bool(review_count))
        self.review_button.setText(f'Review needed ({review_count})…')
        self.refresh_global_playlist_controls()
        self.playlist_summary.setText(f"{playlist['name']} · {'Spotify order + local extras' if playlist['source']=='spotify' else 'Local playlist'} · Rekordbox: {'linked' if playlist['rekordbox_id'] else 'not linked'}" if playlist else "Select a playlist")

    def refresh_global_playlist_controls(self):
        missing=len(self.catalogue.missing_tracks());self.queue_missing_button.setVisible(bool(missing))
        self.queue_missing_button.setText(f'Queue missing ({missing})')
        with self.catalogue.connect() as db:review=db.execute("SELECT count(DISTINCT spotify_id) FROM entries WHERE match_state='review'").fetchone()[0]
        self.review_all_button.setVisible(bool(review));self.review_all_button.setText(f'Review all ({review})')

    def refresh_live(self):
        self.refresh_downloads()
        self.refresh_files()
        self.refresh_playlists()

    def update_download_controls(self):
        if not hasattr(self,"download_buttons"):return
        jobs=self.catalogue.download_jobs()
        self.download_buttons["Queue selected"].setVisible(self.download_tabs.currentIndex()==0 and bool(self.results_table.selectionModel().selectedRows()))
        self.download_buttons["Retry failed"].setVisible(any(j["state"]=="failed" for j in jobs))
        self.download_buttons["Pause queue"].setVisible(self.download_job is not None)
        self.download_buttons["Start / resume queue"].setVisible(self.download_job is None and any(j["state"] in {"queued","paused"} for j in jobs))
        on_queue=self.download_tabs.currentIndex()==1;selected=self.selected_downloads()
        pending=bool(selected and all(j['state'] in {'queued','paused','failed'} for j in selected))
        for label in ['Download next','Move up','Move down']:self.download_buttons[label].setVisible(on_queue and pending)
        self.download_buttons['Remove selected'].setVisible(on_queue and bool(selected))
        self.download_buttons['Clear queue…'].setVisible(on_queue and bool(jobs))
        self.download_buttons['Clear finished'].setVisible(on_queue and any(j['state'] in {'complete','cancelled'} for j in jobs))
        sources={s['id'] for j in jobs if j['state'] in {'queued','paused','failed'} for s in j['sources']} | ({'direct'} if any(not j['sources'] and j['state'] in {'queued','paused','failed'} for j in jobs) else set())
        self.download_buttons['Playlist priority…'].setVisible(on_queue and len(sources)>1)

    def refresh_downloads(self):
        jobs=self.catalogue.download_jobs()
        self.refresh_global_playlist_controls()
        self.update_download_controls()
        recent=[j for j in sorted(jobs,key=lambda j:j['finished_at'] or j['created'],reverse=True) if j["state"]=="complete" and j["output"]][:10]
        self.fill_table(self.recent_table,recent,lambda i,r:[r["title"],r["artist"],Path(r["output"]).name])
        self.recent_table.setVisible(bool(recent));self.recent_empty.setVisible(not recent)
        if not getattr(self.downloads_table,'_dragging',False):self.refresh_queue_table(jobs)
        active=[row for row in jobs if row["state"] in {"queued","paused","running"}]
        self.metrics["queue"].setText(str(len(active)))
        remaining=sum(row["duration"]*(1-min(100,max(0,row["progress"]))/100 if row["state"]=="running" else 1) for row in active)
        active_ids={row["id"] for row in active}
        if active_ids and not (active_ids & self.queue_ids):
            self.queue_ids=set(active_ids)
        self.queue_ids.update(active_ids)
        relevant=[row for row in jobs if row["id"] in self.queue_ids and row["state"] in {"queued","paused","running","complete"}]
        total=sum(row["duration"] for row in relevant)
        self.queue_progress.setValue(round(1000*(1-remaining/total)) if total else 0)
        self.queue_percent.setText(f'{self.queue_progress.value()/10:.0f}% of queued audio')
        current=next((row for row in active if row["state"]=="running"),None)
        running=self.download_job is not None and not self.engine.cancel.is_set()
        rate=float(self.catalogue.get_setting('zotify_options',{}).get('DOWNLOAD_RATE_LIMITER',1))
        self.download_speed.setText('One active download · '+('real-time pacing' if rate==1 else 'no added pacing' if rate==0 else f'pacing ×{rate:g}'))
        finish=(datetime.now()+timedelta(seconds=remaining*rate)).strftime('%H:%M')
        estimate=(f' · estimated finish {finish}' if rate else ' · finish estimate unavailable with pacing disabled') if running else ' · paused / waiting to start'
        self.queue_summary.setText(f"{len(active)} track(s) remaining · ~{clock_duration(remaining)} audio time remaining{estimate}" + (f" · {current['title']} ({current['progress']:.0f}%)" if current else "") if active else "Queue complete" if relevant else "Queue empty")

    def selected_files(self):
        return [item.data(0,ROLE) for item in self.files_table.selectedItems() if (item.data(0,ROLE) or {}).get("kind")=="file"]

    def selected_downloads(self):
        return [self.downloads_table.item(index.row(),0).data(ROLE) for index in self.downloads_table.selectionModel().selectedRows() if self.downloads_table.item(index.row(),0)]

    def refresh_queue_table(self,jobs):
        table=self.downloads_table;selected={j['id'] for j in self.selected_downloads()}
        current=table.item(table.currentRow(),0);current=current.data(ROLE)['id'] if current else None
        scroll=table.verticalScrollBar().value();horizontal=table.horizontalScrollBar().value()
        block=QSignalBlocker(table);table.clearSelection();jobs=[j for j in jobs if j['state']!='removing']
        positions={j['id']:i+1 for i,j in enumerate(j for j in jobs if j['state'] in {'running','queued','paused','failed'})}
        self.fill_table(table,jobs,lambda i,r:[positions.get(r['id'],''),r['title'],r['artist'],r['source_label'],r['state'].capitalize(),'',r['message']])
        for row,job in enumerate(jobs):
            table.item(row,5).setToolTip(f"{job['progress']:.0f}%")
            if job['id'] in selected:table.selectionModel().select(table.model().index(row,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
            if job['id']==current:table.setCurrentCell(row,0,QItemSelectionModel.SelectionFlag.NoUpdate)
        table.verticalScrollBar().setValue(scroll);table.horizontalScrollBar().setValue(horizontal);del block
        self.update_download_controls()

    def queue_drag_data(self):
        selected=self.selected_downloads()
        if not selected or any(j['state'] not in {'queued','paused','failed'} for j in selected):return None
        return {'kind':'queue','workspace':str(self.catalogue.workspace),'jobs':[j['id'] for j in selected]}

    def drop_queue(self,data,point):
        if data.get('kind')!='queue' or data.get('workspace')!=str(self.catalogue.workspace):return False
        try:
            ids=data.get('jobs');known={j['id'] for j in self.catalogue.download_jobs()}
            if not isinstance(ids,list) or not ids or not all(isinstance(id_,str) and id_ in known for id_ in ids):return False
            row=self.downloads_table.insertion_row(point);item=self.downloads_table.item(row,0) if row<self.downloads_table.rowCount() else None
            target=item.data(ROLE) if item else None
            if target and target['state']=='running':
                target=next((j for j in self.catalogue.download_jobs() if j['state'] in {'queued','paused','failed'}),None)
            before=target['id'] if target and target['state'] in {'queued','paused','failed'} else None
            self.catalogue.move_downloads(ids,before);self.refresh_downloads();return True
        except Exception as exc:self.show_error(exc);return False

    def move_queue_selection(self,direction):
        try:
            selected={j['id'] for j in self.selected_downloads()};order=[j['id'] for j in self.catalogue.download_jobs() if j['state'] in {'queued','paused','failed'}]
            for i in (range(1,len(order)) if direction<0 else range(len(order)-2,-1,-1)):
                other=i+direction
                if order[i] in selected and order[other] not in selected:order[i],order[other]=order[other],order[i]
            self.catalogue.reorder_downloads(order);self.refresh_downloads()
        except Exception as exc:self.show_error(exc)

    def download_selected_next(self):
        ids=[j['id'] for j in self.selected_downloads()]
        self.prioritise_download_ids(ids)

    def prioritise_download_ids(self,ids):
        try:
            first=next((j['id'] for j in self.catalogue.download_jobs() if j['state'] in {'queued','paused','failed'} and j['id'] not in ids),None)
            self.catalogue.move_downloads(ids,first);self.refresh_downloads();self.status.setText('Queue priority updated; the current download keeps running')
        except Exception as exc:self.show_error(exc)

    def queue_playlist_priority(self):
        jobs=[j for j in self.catalogue.download_jobs() if j['state'] in {'queued','paused','failed'}];sources={}
        for job in jobs:
            for source in job['sources'] or [{'id':'direct','name':'Direct downloads'}]:
                item=sources.setdefault(source['id'],dict(source,count=0));item['count']+=1
        dialog=QDialog(self);dialog.setWindowTitle('Playlist download priority');dialog.resize(560,480);layout=QVBoxLayout(dialog)
        layout.addWidget(muted('Drag playlists into priority order, or use the arrows. Shared tracks download once; the first matching playlist sets their priority. The current download stays first.'))
        listing=QListWidget();listing.setDragDropMode(QListWidget.DragDropMode.InternalMove);listing.setDefaultDropAction(Qt.DropAction.MoveAction);layout.addWidget(listing,1)
        for source in sources.values():
            item=QListWidgetItem(f"{source['name']} · {source['count']} tracks");item.setData(ROLE,source['id']);listing.addItem(item)
        def move(direction):
            row=listing.currentRow();target=row+direction
            if row>=0 and 0<=target<listing.count():item=listing.takeItem(row);listing.insertItem(target,item);listing.setCurrentRow(target)
        bar=QHBoxLayout();bar.addWidget(self.button('Move up',lambda:move(-1),'SP_ArrowUp'));bar.addWidget(self.button('Move down',lambda:move(1),'SP_ArrowDown'));bar.addStretch();layout.addLayout(bar)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec():
            try:self.catalogue.prioritise_sources([listing.item(i).data(ROLE) for i in range(listing.count())]);self.refresh_downloads()
            except Exception as exc:self.show_error(exc)

    def remove_download_ids(self,ids):
        running=self.catalogue.remove_downloads(ids)
        if running and self.download_job:self.pause_downloads(resume_after_removal=not self.engine.cancel.is_set())
        elif running:self.catalogue.finish_queue_removals()
        self.refresh_downloads();self.status.setText(f'Removed {len(ids)} queue entries; downloaded files kept')

    def remove_selected_downloads(self):
        ids=[j['id'] for j in self.selected_downloads()]
        if ids:self.remove_download_ids(ids)

    def clear_download_queue(self):
        ids=[j['id'] for j in self.catalogue.download_jobs()]
        if ids and QMessageBox.question(self,'Clear download queue',f'Remove all {len(ids)} queue entries? The active download will stop. Downloaded music files will be kept.')==QMessageBox.StandardButton.Yes:self.remove_download_ids(ids)

    def clear_finished_downloads(self):
        self.remove_download_ids([j['id'] for j in self.catalogue.download_jobs() if j['state'] in {'complete','cancelled'}])

    def file_selection(self):
        rows=self.selected_files()
        self.add_library_button.setVisible(bool(rows))
        self.details.setText(f"{len(rows)} file(s) selected"+(f" · {self.catalogue.resolve(rows[0])}" if len(rows)==1 else ""))

    def show_error(self,error):
        QMessageBox.warning(self,"ZotiBox",str(error))

    def ensure_idle(self):
        if self.job or self.download_job or self.scan_job:
            raise ValueError("Wait for the current task or pause the download queue first")

    def add_root(self):
        try:
            self.ensure_idle()
            folder=QFileDialog.getExistingDirectory(self,"Add music folder")
            if folder:
                self.catalogue.add_root(folder)
                self.refresh()
        except Exception as exc:
            self.show_error(exc)

    def map_root(self):
        item=self.roots_list.currentItem()
        if not item:
            return
        try:
            self.ensure_idle()
            folder=QFileDialog.getExistingDirectory(self,"Map music folder")
            if folder:
                self.catalogue.map_root(item.data(ROLE),folder)
                self.refresh()
        except Exception as exc:
            self.show_error(exc)

    def remove_root(self,checked=False,root_id=None):
        item=self.roots_list.currentItem()
        root_id=root_id or (item.data(ROLE) if item else None)
        if not root_id:
            return
        try:
            self.ensure_idle()
            if QMessageBox.question(self,"Remove scanned folder","Remove this folder from the catalogue? Music stays on disk; playlist references remain and may become unavailable.")==QMessageBox.StandardButton.Yes:
                self.catalogue.remove_root(root_id)
                self.refresh()
        except Exception as exc:
            self.show_error(exc)

    def start_job(self,label,work,complete,cancellable=True):
        if self.job:
            self.status.setText("A task is already running")
            return
        self.job=Job(work,self)
        self.status.setText(label)
        self.activity.show()
        self.cancel_button.setVisible(cancellable)
        self.job.progress.connect(lambda count,name:self.status.setText(f"{label} · {name}"))
        self.job.finished_result.connect(complete)
        self.job.failed.connect(self.job_error)
        self.job.cancelled.connect(self.status.setText)
        self.job.finished.connect(self.job_finished)
        self.job.start()

    def job_error(self,message):
        self.status.setText(message)
        self.show_error(message)

    def job_finished(self):
        self.job.deleteLater()
        self.job=None
        self.activity.hide()
        self.cancel_button.setVisible(bool(self.scan_job))
        self.refresh()
        self.maybe_close()
        pending=getattr(self,"pending_installer",None)
        if pending:
            self.pending_installer=None
            try:
                launch_installer(pending)
                self.close()
            except Exception as exc:self.show_error(exc)

    def cancel_task(self):
        if self.scan_job:
            self.scan_job.cancel.set()
        if self.job:
            self.job.cancel.set()
            self.status.setText("Cancelling task…")

    def scan(self):
        if self.scan_job:
            self.status.setText("Local discovery is already running")
            return
        if not self.catalogue.roots():
            self.add_root()
        if self.catalogue.roots():
            self.scan_job=Job(lambda progress,cancel:self.catalogue.scan(progress,cancel),self)
            self.scan_job.progress.connect(self.scan_progress)
            self.scan_job.finished_result.connect(self.scan_complete)
            self.scan_job.failed.connect(self.job_error)
            self.scan_job.cancelled.connect(self.status.setText)
            self.scan_job.finished.connect(self.scan_finished)
            self.scan_button.setEnabled(False)
            self.cancel_button.show()
            self.scan_job.start()

    def scan_progress(self,count,name):
        self.status.setText(f"Local discovery · {count} files · {name}")
        if not self.library_refresh.isActive():
            self.library_refresh.start()

    def scan_finished(self):
        self.scan_job.deleteLater()
        self.scan_job=None
        self.artwork.images.clear()
        self.scan_button.setEnabled(True)
        self.cancel_button.setVisible(bool(self.job))
        self.refresh()
        self.maybe_close()


    def scan_complete(self,result):
        self.status.setText(f"Scan complete · {result['indexed']} indexed · {result['unchanged']} unchanged · {result['missing']} missing")
        if result["errors"]:
            QMessageBox.information(self,"Scan issues","\n".join(result["errors"][:30]))

    def new_playlist(self):
        item=self.playlist_list.currentItem();folder=item.data(ROLE+2) if item else None
        name,ok=QInputDialog.getText(self,"New local playlist","Playlist name")
        if ok and name.strip():
            self.playlist_id=self.catalogue.create_playlist(name)
            if folder:
                self.catalogue.move_playlist_item(self.playlist_id,folder['id'])
                expanded=set(self.catalogue.get_setting('playlist_expanded',[]));expanded.add(folder['id']);self.catalogue.set_setting('playlist_expanded',sorted(expanded))
            self.refresh()
            self.navigation.setCurrentRow(3)

    def add_selected(self):
        rows=self.selected_files()
        if not rows:
            return
        playlists=self.catalogue.playlists()
        def create(name):
            id_=self.catalogue.create_playlist(name)
            return next(p for p in self.catalogue.playlists() if p["id"]==id_)
        choice=choose_playlist(playlists,self,"Add to playlist (Spotify links keep local extras)",create=create)
        if choice:
            self.catalogue.add_entries(choice["id"],list(dict.fromkeys(row["recording_id"] for row in rows)))
            self.refresh_playlists()

    def move_entry(self,direction):
        current=self.entries_table.currentRow()
        target=current+direction
        if current<0 or target<0 or target>=self.entries_table.rowCount():
            return
        try:
            ids=[self.entries_table.item(index,0).data(ROLE)["id"] for index in range(self.entries_table.rowCount())]
            ids[current],ids[target]=ids[target],ids[current]
            self.catalogue.reorder(self.playlist_id,ids)
            self.refresh_entries()
            self.entries_table.selectRow(target)
        except Exception as exc:
            self.show_error(exc)

    def remove_entry(self):
        row=self.entries_table.currentRow()
        if row>=0:
            try:
                self.catalogue.remove_entry(self.playlist_id,self.entries_table.item(row,0).data(ROLE)["id"])
                self.refresh_playlists()
            except Exception as exc:
                self.show_error(exc)

    def export_menu(self):
        menu=QMenu(self)
        menu.addAction("Export selected playlist as M3U8",self.export_playlist)
        menu.addAction("Playlist export settings…",self.export_settings)
        menu.addSeparator()
        menu.addAction("Export ZotiBox database package…",self.export_database)
        menu.exec(self.mapToGlobal(self.centralWidget().rect().center()))

    def export_settings(self):
        dialog=QDialog(self)
        dialog.setWindowTitle("Playlist export settings")
        layout=QVBoxLayout(dialog)
        relative=QCheckBox("Use relative paths when music and playlist are on the same drive")
        relative.setChecked(self.catalogue.get_setting("m3u_relative",False))
        automatic=QCheckBox("Update linked M3U8 exports after successful syncs and downloads")
        automatic.setChecked(self.catalogue.get_setting("m3u_auto",True))
        layout.addWidget(relative)
        layout.addWidget(automatic)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.catalogue.set_setting("m3u_relative",relative.isChecked())
            self.catalogue.set_setting("m3u_auto",automatic.isChecked())

    def export_playlist(self):
        if not self.playlist_id:
            return
        path,_=QFileDialog.getSaveFileName(self,"Export playlist","playlist.m3u8","M3U8 playlist (*.m3u8)")
        if path:
            try:
                missing=self.catalogue.export_m3u8(self.playlist_id,path,self.catalogue.get_setting("m3u_relative",False))
                exports=self.catalogue.get_setting("playlist_exports",{})
                exports[self.playlist_id]=path
                self.catalogue.set_setting("playlist_exports",exports)
                self.status.setText(f"Playlist exported · {len(missing)} unavailable entries omitted")
                if missing:
                    QMessageBox.information(self,"Unavailable entries","These tracks remain in the app playlist:\n\n"+"\n".join(missing[:30]))
            except Exception as exc:
                self.show_error(exc)

    def update_exports(self):
        if not self.catalogue.get_setting("m3u_auto",True):
            return
        exports=self.catalogue.get_setting("playlist_exports",{})
        existing={p["id"] for p in self.catalogue.playlists()}
        for playlist_id,path in exports.items():
            if playlist_id in existing and Path(path).parent.is_dir():
                self.catalogue.export_m3u8(playlist_id,path,self.catalogue.get_setting("m3u_relative",False))

    def export_database(self):
        path,_=QFileDialog.getSaveFileName(self,"Export ZotiBox database","ZotiBox.zotibox","ZotiBox database package (*.zotibox)")
        if path:
            include=QMessageBox.question(self,"Include music","Include indexed, available music in this package?")==QMessageBox.StandardButton.Yes
            self.start_job("Exporting database",lambda progress,cancel:export_workspace(self.catalogue,path,include,progress),lambda result:self.status.setText(f"Database exported · {result['files']} music files"),False)

    def save_client(self):
        import re
        value=self.client_edit.text().strip()
        try:
            self.ensure_idle()
            if not re.fullmatch(r"[a-fA-F0-9]{32}",value):
                raise ValueError("Enter the 32-character Client ID from Spotify Developer Dashboard")
            if value!=self.catalogue.get_setting("spotify_client_id",""):
                TokenStore(self.catalogue.workspace).clear()
                (self.catalogue.workspace/".local"/"zotify-session.bin").unlink(missing_ok=True)
            self.catalogue.set_setting("spotify_client_id",value)
            self.reset_integrations()
            self.status.setText("Client ID saved")
            self.refresh()
            return True
        except Exception as exc:
            self.show_error(exc)
            return False

    def connect_spotify(self):
        if not self.save_client():
            return
        self.start_job("Connecting Spotify",lambda progress,cancel:self.spotify.authenticate(cancel,progress),lambda name:self.status.setText(f"Spotify connected · {name}"))

    def disconnect_spotify(self):
        if not self.job and not self.download_job:
            TokenStore(self.catalogue.workspace).clear()
            self.refresh()

    def spotify_instructions(self):
        dialog=QDialog(self)
        dialog.setWindowTitle("Spotify setup")
        dialog.setMinimumWidth(650)
        layout=QVBoxLayout(dialog)
        label=QLabel('<p>1. Open <a href="https://developer.spotify.com/dashboard">Spotify Developer Dashboard</a> and sign in.</p>'
            '<p>2. Create an app for your own use and select Web API. Development Mode requires the app owner to have Premium.</p>'
            '<p>3. In its settings, register both redirect URIs exactly:</p>'
            f'<p><b>http://127.0.0.1:4381/callback</b><br><b>{escape(self.zotify_redirect_reference.text())}</b></p>'
            '<p>4. Copy the <b>Client ID</b> into ZotiBox and save it. Do not enter a client secret.</p>'
            '<p>5. Connect Spotify for playlists and Zotify for downloads. Each may open a browser login.</p>'
            '<p>Sharing your developer app requires adding users in Users Management (currently up to five users). Other ZotiBox users can create and configure their own developer app.</p>')
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(label)
        layout.addWidget(self.button("Close",dialog.accept))
        dialog.exec()

    def dashboard_find(self):
        self.download_search.setText(self.dashboard_search.text())
        self.navigation.setCurrentRow(1)
        self.find_downloads()

    def find_downloads(self):
        query=self.download_search.text().strip()
        kind=self.search_kind.currentData()
        if not query:
            return
        if self.offer_playlist_sync(query):return
        def work(progress,cancel):
            if query.startswith(("https://open.spotify.com/","spotify:")):
                return [dict(track,type="track") for track in self.spotify.tracks_for_url(query,cancel)]
            items=self.spotify.search(query,kind)
            results=[]
            for item in items:
                if not item:
                    continue
                if kind=="track":
                    results.append(dict(track_data(item),type="track"))
                else:
                    artist=", ".join(a["name"] for a in item.get("artists",[])) if kind=="album" else (item.get("owner") or {}).get("display_name","")
                    results.append({"id":item["id"],"title":item["name"],"artist":artist,"type":kind,"duration":0,
                        "count":item.get("total_tracks",(item.get("items") or item.get("tracks") or {}).get("total",""))})
            return results
        self.start_job("Searching Spotify",work,self.show_results)

    def offer_playlist_sync(self,query):
        if not query.startswith(('https://open.spotify.com/','spotify:playlist:')):return False
        try:id_=spotify_id(query,'playlist')
        except ValueError:return False
        existing=next((p for p in self.catalogue.playlists() if p['source']=='spotify' and p['spotify_id']==id_),None)
        prompt='This playlist is already linked. Sync it now? Choose No to browse its tracks instead.' if existing else 'Create a Spotify playlist sync for this playlist? Choose No to browse its tracks instead.'
        answer=QMessageBox.question(self,'Spotify playlist',prompt,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Yes:
            self.navigation.setCurrentRow(3);self.playlist_tabs.setCurrentIndex(0);self.sync_ids([id_]);return True
        return answer==QMessageBox.StandardButton.Cancel

    def show_results(self,rows):
        self.download_tabs.setCurrentIndex(0)
        self.fill_table(self.results_table,rows,lambda i,row:[row["title"],row["artist"],row["type"].capitalize(),duration(row["duration"]) if row["type"]=="track" else row.get("count","")])
        self.status.setText(f"{len(rows)} result(s)")

    def queue_results(self):
        selected=[self.results_table.item(row.row(),0).data(ROLE) for row in self.results_table.selectionModel().selectedRows()]
        if not selected:
            return
        def work(progress,cancel):
            queued=0
            reviews=[]
            for item in selected:
                tracks=[item] if item["type"]=="track" else self.spotify.tracks_for_url(f"spotify:{item['type']}:{item['id']}",cancel)
                for track in tracks:
                    if cancel.is_set():
                        raise InterruptedError("Queue request cancelled")
                    self.catalogue.save_remote(track)
                    state=match_track(self.catalogue,track)["state"]
                    if state=="review":
                        reviews.append(track)
                    elif track.get("playable",True) and state=="missing":
                        self.catalogue.enqueue(track["id"],track.get('_download_source'))
                        queued+=1
                        self.integration_event.emit("refresh","")
            return {"queued":queued,"reviews":reviews}
        def complete(result):
            count=result["queued"]
            if result["reviews"] and QMessageBox.question(self,"Possible existing tracks",
                f"{len(result['reviews'])} track(s) have ambiguous local matches. Queue new downloads anyway? Choose No to review their local matches from the search results.")==QMessageBox.StandardButton.Yes:
                for track in result["reviews"]:
                    self.catalogue.enqueue(track["id"],track.get('_download_source'))
                    count+=1
            self.status.setText(f"Queued {count} track(s)")
            self.download_tabs.setCurrentIndex(1 if count else 0)
        self.start_job("Adding to queue",work,complete)

    def link_spotify(self):
        def complete(items):
            items=[p for p in items if p]
            if not items:
                self.status.setText("No Spotify playlists available")
                return
            choice=choose_playlist(items,self,"Link an owned or collaborative Spotify playlist")
            if choice:
                remote_id=choice["id"]
                QTimer.singleShot(100,lambda:self.sync_ids([remote_id]))
        self.start_job("Loading Spotify playlists",lambda progress,cancel:self.spotify.playlists(cancel),complete)

    def sync_selected(self):
        item=self.playlist_list.currentItem()
        playlist=item.data(ROLE) if item else None
        if playlist and playlist["source"]=="spotify":
            self.sync_ids([playlist["spotify_id"]])

    def sync_all(self):
        self.sync_ids([p["spotify_id"] for p in self.catalogue.playlists() if p["source"]=="spotify"])

    def sync_ids(self,ids):
        if not ids:
            return
        def work(progress,cancel):
            reports=[]
            available=local_recordings(self.catalogue)
            for remote_id in ids:
                snapshot=self.spotify.snapshot(remote_id,cancel)
                progress(0,snapshot["name"])
                self.integration_event.emit("refresh","")
                resolved={}
                for track in snapshot["tracks"]:
                    if cancel.is_set():
                        raise InterruptedError("Sync cancelled; current playlist kept")
                    self.catalogue.save_remote(track)
                    resolved[track["id"]]=match_track(self.catalogue,track,available)
                reports.append(self.catalogue.sync_playlist(snapshot,resolved))
                if self.catalogue.get_setting("auto_download",False):
                    for track in snapshot["tracks"]:
                        if resolved[track["id"]]["state"]=="missing":
                            self.catalogue.enqueue(track["id"])
            self.update_exports()
            return reports
        self.start_job("Synchronising Spotify",work,self.sync_complete)

    def sync_complete(self,reports):
        if reports:
            self.playlist_id=reports[-1]["playlist_id"]
        self.status.setText(f"Synced {len(reports)} playlist(s) · {sum(r['added'] for r in reports)} added · {sum(r['removed'] for r in reports)} removed")
        if self.catalogue.get_setting("auto_download",False):
            QTimer.singleShot(250,self.start_downloads)

    def queue_missing(self):
        tracks=self.catalogue.missing_tracks()
        pending={track['id'] for track in tracks}
        for playlist in self.catalogue.playlists():
            for entry in self.catalogue.entries(playlist['id']):
                if entry['spotify_id'] in pending:self.catalogue.enqueue(entry['spotify_id']);pending.remove(entry['spotify_id'])
        self.refresh_downloads()
        self.status.setText(f'Queued {len(tracks)} missing track(s) across all playlists')

    def review_needed(self,all_playlists=False):
        playlists=self.catalogue.playlists() if all_playlists else [{'id':self.playlist_id}] if self.playlist_id else []
        ids=dict.fromkeys(e['spotify_id'] for p in playlists for e in self.catalogue.entries(p['id']) if e['match_state']=='review' and e['spotify_id'])
        tracks=[self.catalogue.remote_track(id_) for id_ in ids]
        if not tracks:self.status.setText('No tracks need review');return
        MatchReview(self.catalogue,tracks,self).exec()
        self.refresh_playlists();self.update_exports()

    def review_match(self):
        row=self.entries_table.currentRow()
        if row<0:
            return
        entry=self.entries_table.item(row,0).data(ROLE)
        if not entry["spotify_id"]:
            return
        track=self.catalogue.remote_track(entry["spotify_id"])
        self.review_track(track)

    def mark_entry_missing(self,entry):
        self.catalogue.decide_match(entry['spotify_id'],None,'missing')
        self.refresh_playlists();self.update_exports()

    def review_track(self,track):
        if track:
            MatchReview(self.catalogue,[track],self).exec()
            self.refresh_playlists();self.update_exports()

    def save_common_options(self):
        options=self.catalogue.get_setting("zotify_options",{})
        options.update(DOWNLOAD_FORMAT=self.codec.currentText(),DOWNLOAD_QUALITY=self.quality.currentText())
        self.catalogue.set_setting("zotify_options",options)

    def zotify_settings(self):
        self.navigation.setCurrentRow(4)
        self.settings_navigation.setCurrentRow(self.zotify_settings_index)
        self.zotify_panel.load()

    def connect_zotify(self,fresh=False):
        if self.job or self.download_job:
            return
        self.engine.cancel.clear()
        self.download_job=Job(lambda progress,cancel:self.engine.authenticate(fresh=fresh),self)
        self.download_job.finished_result.connect(lambda text:self.status.setText(text))
        self.download_job.failed.connect(self.job_error)
        self.download_job.cancelled.connect(self.status.setText)
        self.download_job.finished.connect(self.download_finished)
        self.download_job.start()
        self.status.setText("Connecting Zotify; complete the browser login(s)")

    def last_download_error(self):
        import json
        path=self.catalogue.workspace/'.local'/'last-worker-error.json'
        detail=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        text='No diagnostic has been saved yet.' if not detail else str(detail.get('type','Download failure'))+'\n'+'\n'.join(f"{frame['file']}:{frame['line']} — {frame['function']}" for frame in detail.get('frames',[]))
        QMessageBox.information(self,'Last download diagnostic',text)

    def start_downloads(self):
        if self.download_job:
            return
        self.download_job=Job(lambda progress,cancel:self.engine.run_queue(),self)
        self.download_job.finished_result.connect(lambda text:self.status.setText(text))
        self.download_job.failed.connect(self.job_error)
        self.download_job.cancelled.connect(self.status.setText)
        self.download_job.finished.connect(self.download_finished)
        self.download_job.start()
        self.update_download_controls()

    def pause_downloads(self,resume_after_removal=False):
        self.resume_after_removal=resume_after_removal
        if self.download_job:
            self.engine.cancel.set()
            threading.Thread(target=self.engine.stop,daemon=True).start()
            self.status.setText("Pausing queue…")

    def retry_downloads(self):
        skipped=0
        for job in self.catalogue.download_jobs():
            if job["state"]=="failed":
                try:self.catalogue.enqueue(job['spotify_id'])
                except ValueError:skipped+=1
        self.refresh_downloads()
        if skipped:self.status.setText(f'{skipped} unavailable track(s) could not be retried')

    def download_finished(self):
        self.download_job.deleteLater()
        self.download_job=None
        self.pending_login=None
        self.refresh()
        try:
            self.update_exports()
        except Exception as exc:
            self.status.setText(f"Playlist export needs attention: {exc}")

        self.maybe_close()
        resume=self.resume_after_removal;self.resume_after_removal=False
        if resume and not self.close_pending and any(j['state'] in {'queued','paused'} for j in self.catalogue.download_jobs()):QTimer.singleShot(0,self.start_downloads)

    @Slot(str,object)
    def handle_integration_event(self,kind,value):
        if kind=='startup_update':
            self.available_update=value;self.install_update_button.setEnabled(bool(value))
            self.update_status.setText(f"Version {value['version']} is available" if value else 'No newer release available')
            if value:QTimer.singleShot(500,self.prompt_startup_update)
        elif kind=='startup_update_error':self.update_status.setText('Startup update check unavailable; try Check for updates again.')
        elif kind=="auth_url":
            url=QUrl(value)
            if url.scheme()=="https" and url.host()=="accounts.spotify.com":
                self.pending_login=value
                self.status.setText("Complete Zotify login in your browser")
                QDesktopServices.openUrl(url)
        elif kind=="status":
            self.status.setText(str(value))
        elif kind=="connected":
            self.pending_login=None
            self.zotify_status.setText("Zotify: connected")
        elif not self.download_refresh.isActive():
            self.download_refresh.start()

    def open_pending_login(self):
        if self.pending_login:
            QDesktopServices.openUrl(QUrl(self.pending_login))

    def choose_download_folder(self):
        try:
            self.ensure_idle()
            folder=QFileDialog.getExistingDirectory(self,"Choose download folder")
            if folder:
                self.catalogue.set_setting("download_folder",folder)
                self.download_folder.setText(folder)
        except Exception as exc:
            self.show_error(exc)

    def edit_theme(self):
        original=copy.deepcopy(self.theme)
        dialog=ThemeDialog(self.theme,self)
        dialog.preview.connect(self.apply_theme)
        if dialog.exec():
            self.apply_theme(dialog.theme)
            self.catalogue.set_setting("theme",self.theme)
        else:
            self.apply_theme(original)

    def apply_theme(self,theme):
        self.theme=valid_theme(theme)
        arrow=self.catalogue.workspace/".local"/"ui"/"chevron.svg"
        arrow.parent.mkdir(parents=True,exist_ok=True)
        arrow.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8" viewBox="0 0 12 8"><path d="M1 1 L6 6 L11 1" fill="none" stroke="{self.theme["foreground"]["start"]}" stroke-width="2"/></svg>',encoding="utf-8")
        self.setStyleSheet(stylesheet(self.theme)+f' QComboBox::down-arrow {{image:url("{arrow.as_posix()}");width:12px;height:8px;}}')
        palette=self.palette()
        for role,key in [(QPalette.ColorRole.Text,"foreground"),(QPalette.ColorRole.WindowText,"foreground"),(QPalette.ColorRole.Highlight,"selection"),(QPalette.ColorRole.HighlightedText,"selection_text")]:
            palette.setColor(role,QColor(self.theme[key]["start"]))
        for role,key in [(QPalette.ColorRole.Window,"background"),(QPalette.ColorRole.Base,"surface"),(QPalette.ColorRole.AlternateBase,"alternate"),
                (QPalette.ColorRole.Button,"surface"),(QPalette.ColorRole.ButtonText,"foreground"),(QPalette.ColorRole.ToolTipBase,"surface"),(QPalette.ColorRole.ToolTipText,"foreground")]:
            palette.setColor(role,QColor(self.theme[key]["start"]))
        self.setPalette(palette)
        self.setWindowIcon(QIcon(QPixmap.fromImage(icon_image(accent=self.theme["accent"]["start"]))))
        for control in self.findChildren(QPushButton):
            name=control.property("icon_name")
            if name:
                control.setIcon(outline_icon(name,QColor(self.theme["selection_text" if control.property("primary") else "foreground"]["start"])))
        for index,name in enumerate(["SP_ComputerIcon","SP_ArrowDown","SP_DirIcon","SP_FileDialogListView","settings"]):
            self.navigation.item(index).setIcon(self.icon(name))
        for index,name in enumerate(self.settings_icons):
            self.settings_navigation.item(index).setIcon(self.icon(name))
        def recolour_folder(item):
            if (item.data(0,ROLE) or {}).get("kind")=="folder":
                item.setIcon(0,self.icon("SP_DirIcon"))
            for index in range(item.childCount()):
                recolour_folder(item.child(index))
        for index in range(self.files_table.topLevelItemCount()):
            recolour_folder(self.files_table.topLevelItem(index))
        self.centralWidget().update()
        for view in [self.files_table,self.entries_table,self.results_table,self.downloads_table]:
            view.viewport().update()

    def update_desktop_icon(self):
        try:
            if not getattr(sys,"frozen",False):
                raise ValueError("Use the executable build to update the desktop shortcut")
            icon=self.catalogue.workspace/".local"/"icons"/f"zotibox-{self.theme['accent']['start'].lstrip('#')}.ico"
            save_icon(icon,self.theme["accent"]["start"])
            desktop=QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
            script=Path(__file__).parent/"resources"/"desktop-shortcut.ps1"
            powershell=Path(os.environ["SystemRoot"])/"System32"/"WindowsPowerShell"/"v1.0"/"powershell.exe"
            result=subprocess.run([str(powershell),"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script),"-IconPath",str(icon),"-TargetPath",sys.executable,"-DesktopPath",desktop],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=20)
            if result.returncode:
                raise ValueError("Windows could not update the shortcut")
            self.status.setText("Desktop shortcut updated; Windows may refresh its icon shortly")
        except Exception as exc:
            self.show_error(exc)

    def browse_database(self):
        folder=QFileDialog.getExistingDirectory(self,"Choose parent folder for ZotiBox Database")
        if folder:
            self.database_path.setText(str(Path(folder)/"ZotiBox Database"))

    def relocate_database(self):
        try:
            self.ensure_idle()
            destination=self.database_path.text().strip()
            self.start_job("Relocating database",lambda progress,cancel:relocate(self.catalogue,destination),self.open_relocated,False)
        except Exception as exc:
            self.show_error(exc)

    def open_relocated(self,path):
        try:
            self.switch_workspace(path)
            self.update_exports()
            self.status.setText("Database opened; original retained")
        except Exception as exc:
            self.show_error(exc)

    @staticmethod
    def lock_workspace(path):return acquire_lock(path)

    def switch_workspace(self,path):
        if Path(path).resolve()==self.catalogue.workspace:
            return
        lock=self.lock_workspace(path)
        try:
            catalogue=Catalogue(path)
        except Exception:
            lock.unlock()
            raise
        self.workspace_lock.unlock()
        self.workspace_lock,self.catalogue=lock,catalogue
        if getattr(self,"instance",None):self.instance.rebind(path,lock)
        self.reset_integrations()
        self.client_edit.setText(catalogue.get_setting("spotify_client_id",""))
        self.download_folder.setText(catalogue.get_setting("download_folder",str(catalogue.workspace/"Music")))
        self.apply_theme(catalogue.get_setting("theme",make_theme()))
        if hasattr(self,'zotify_panel'):self.zotify_panel.load()
        with QSignalBlocker(self.lossy_fallback):self.lossy_fallback.setCurrentIndex(max(0,self.lossy_fallback.findData(catalogue.get_setting('lossy_fallback','mp3'))))
        if self.remember:
            QSettings().setValue("database",str(catalogue.workspace))
        self.refresh()

    def open_workspace(self):
        try:
            self.ensure_idle()
            folder=QFileDialog.getExistingDirectory(self,"Open ZotiBox Database")
            if folder:
                if not (Path(folder)/"catalogue.sqlite3").is_file():
                    raise ValueError("Select a ZotiBox Database folder")
                self.switch_workspace(folder)
        except Exception as exc:
            self.show_error(exc)

    def import_database(self):
        try:
            self.ensure_idle()
            path,_=QFileDialog.getOpenFileName(self,"Import ZotiBox Database","","ZotiBox package (*.zotibox)")
            if not path:
                return
            info=inspect_package(path)
            QMessageBox.information(self,"Import preview",f"{len(info['music'])} music files · {info['transfer_bytes']/1024**2:.1f} MB\nChoose an empty folder. Existing databases are never overwritten.")
            folder=QFileDialog.getExistingDirectory(self,"Choose empty import folder")
            if folder:
                self.start_job("Importing database",lambda progress,cancel:import_workspace(path,folder,progress),self.open_relocated,False)
        except Exception as exc:
            self.show_error(exc)

    def show_file_details(self,row):
        if not row or row.get("kind")!="file":
            return
        quality=" · ".join([row["codec"]]+([f"{row['sample_rate']} Hz"] if row["sample_rate"] else [])+
            ([f"{row['bits']} bit"] if row["bits"] else [])+([f"{row['bitrate']//1000} kbps"] if row["bitrate"] else []))
        text="\n".join([f"Title: {row['title']}",f"Artist: {row['artist']}",f"Album: {row['album']}",f"Path: {self.catalogue.resolve(row) or 'Not mapped'}",
            f"Format: {quality}",f"Source: {self.catalogue.get_setting('audio_source_'+row['sha256'],'Unknown — a FLAC extension does not prove a lossless source')}",f"Size: {row['size']/1024**2:.2f} MB",f"Duration: {duration(row['duration'])}",
            f"Created: {date_ns(row['created_ns'])}",f"Modified: {date_ns(row['mtime_ns'])}",f"First indexed: {row['indexed_at']}",f"ISRC: {row['isrc'] or 'Not tagged'}",f"SHA-256: {row['sha256']}"])
        box=QMessageBox(self)
        box.setWindowTitle("File details")
        box.setText(text)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.exec()

    def explorer(self,path,file=True):
        if path and Path(path).exists():
            subprocess.Popen(["explorer.exe","/select,",str(path)] if file else ["explorer.exe",str(path)])

    def files_context(self,point):
        item=self.files_table.itemAt(point)
        if not item:
            return
        if not item.isSelected():
            self.files_table.clearSelection()
            item.setSelected(True)
        row=item.data(0,ROLE)
        menu=QMenu(self)
        if row["kind"]=="file":
            menu.addAction("Play / preview",lambda:self.play_row(row))
            menu.addAction("File details",lambda:self.show_file_details(row))
            menu.addAction("Show in File Explorer",lambda:self.explorer(self.catalogue.resolve(row)))
            menu.addAction("Add selected to playlist",self.add_selected)
            menu.addAction("Copy file path",lambda:QApplication.clipboard().setText(str(self.catalogue.resolve(row))))
            menu.addSeparator()
            menu.addAction("Delete selected files…",self.delete_selected_files)
        else:
            root=next((r for r in self.catalogue.roots() if r["id"]==row["root_id"]),None)
            folder=Path(root["path"])/row["folder"] if root and root["path"] else None
            menu.addAction("Open folder in File Explorer",lambda:self.explorer(folder,False))
            if not row["folder"]:
                menu.addAction("Remove scanned folder",lambda:self.remove_root(root_id=row["root_id"]))
        menu.exec(self.files_table.viewport().mapToGlobal(point))

    def playlist_context(self,point):
        item=self.playlist_list.itemAt(point)
        if not item:
            menu=QMenu(self);menu.addAction('New folder…',self.new_playlist_folder);menu.exec(self.playlist_list.viewport().mapToGlobal(point));return
        self.playlist_list.setCurrentItem(item)
        folder=item.data(ROLE+2)
        if folder:
            menu=QMenu(self);menu.addAction('New subfolder…',self.new_playlist_folder)
            menu.addAction('Rename folder…',lambda:self.rename_playlist_folder(folder))
            menu.addAction('Move to top level',lambda:self.move_playlist_top(folder['id'],True))
            menu.addAction('Remove folder (keep playlists)',lambda:self.remove_playlist_folder(folder))
            menu.exec(self.playlist_list.viewport().mapToGlobal(point));return
        playlist=item.data(ROLE)
        menu=QMenu(self)
        menu.addAction("Rename playlist…",self.rename_current_playlist)
        menu.addAction("Link / sync with Rekordbox…",self.rb_panel.sync_local)
        menu.addAction("Export M3U8",self.export_playlist)
        source_key='spotify:playlist:'+playlist['spotify_id'] if playlist['source']=='spotify' else 'local:'+playlist['id']
        queued=[j['id'] for j in self.catalogue.download_jobs() if j['state'] in {'queued','paused','failed'} and any(s['id']==source_key for s in j['sources'])]
        if queued:menu.addAction('Download queued tracks first',lambda:self.prioritise_download_ids(queued))
        menu.addAction('Move to top level',lambda:self.move_playlist_top(playlist['id']))
        if playlist["source"]=="spotify":
            menu.addAction('Local files in Spotify / phone…',self.local_spotify_help)
            menu.addAction('Export local extras for phone…',self.export_local_extras)
            menu.addAction('Review needed tracks…',self.review_needed)
            menu.addAction("Sync from Spotify",self.sync_selected)
            menu.addAction("Make independent",self.detach_current)
        menu.addAction("Remove playlist",self.delete_current_playlist)
        menu.exec(self.playlist_list.viewport().mapToGlobal(point))

    def playlist_drag_data(self):
        item=self.playlist_list.currentItem()
        if not item:return None
        folder=item.data(ROLE+2);row=folder or item.data(ROLE)
        if not row:return None
        return {'kind':'playlist','scope':'local','workspace':str(self.catalogue.workspace),'id':row['id'],'folder':bool(folder)}

    def save_playlist_expansion(self,*_):
        expanded=[]
        def visit(item):
            folder=item.data(ROLE+2)
            if folder and item.isExpanded():expanded.append(folder['id'])
            for i in range(item.childCount()):visit(item.child(i))
        for i in range(self.playlist_list.topLevelItemCount()):visit(self.playlist_list.topLevelItem(i))
        self.catalogue.set_setting('playlist_expanded',expanded)

    def new_playlist_folder(self):
        item=self.playlist_list.currentItem();folder=item.data(ROLE+2) if item else None
        name,ok=QInputDialog.getText(self,'New playlist folder','Name')
        if ok and name.strip():
            self.catalogue.create_playlist_folder(name,folder['id'] if folder else None)
            if folder:
                expanded=set(self.catalogue.get_setting('playlist_expanded',[]));expanded.add(folder['id']);self.catalogue.set_setting('playlist_expanded',sorted(expanded))
            self.refresh_playlists()

    def rename_playlist_folder(self,folder):
        name,ok=QInputDialog.getText(self,'Rename folder','Name',text=folder['name'])
        if ok and name.strip():self.catalogue.rename_playlist_folder(folder['id'],name);self.refresh_playlists()

    def remove_playlist_folder(self,folder):
        if QMessageBox.question(self,'Remove folder','Remove this folder? Playlists and subfolders will be kept.')==QMessageBox.StandardButton.Yes:
            self.catalogue.remove_playlist_folder(folder['id']);self.refresh_playlists()

    def move_playlist_top(self,id_,folder=False):
        self.catalogue.move_playlist_item(id_,None,folder);self.refresh_playlists()

    def rename_current_playlist(self):
        if not self.playlist_id:return
        playlist=next(p for p in self.catalogue.playlists() if p["id"]==self.playlist_id)
        prompt="Name (Spotify sync will restore the Spotify name)" if playlist["source"]=="spotify" else "Name"
        name,ok=QInputDialog.getText(self,"Rename playlist",prompt,text=playlist["name"])
        if ok and name.strip():
            self.catalogue.rename_playlist(self.playlist_id,name.strip());self.refresh_playlists()

    def delete_selected_files(self):
        try:
            self.ensure_idle()
            rows=self.selected_files()
            if not rows:return
            paths=[str(self.catalogue.resolve(r)) for r in rows]
            if QMessageBox.question(self,"Delete music files",f"Move {len(rows)} selected music file(s) to the Recycle Bin?\nPlaylist references will become missing. Rekordbox is unchanged.\n\n"+"\n".join(paths[:12]))!=QMessageBox.StandardButton.Yes:return
            selected_paths={os.path.normcase(str(self.catalogue.resolve(r))) for r in rows}
            if (self.player.path and os.path.normcase(self.player.path) in selected_paths) or any(os.path.normcase(j.path) in selected_paths for j in self.player.jobs):
                if not self.player.shutdown():raise ValueError("Waveform decoding is stopping; retry file removal shortly")
                self.player.dismiss()
            for row in rows:self.catalogue.remove_file(row["id"],recycle)
            self.refresh()
        except Exception as exc:self.refresh();self.show_error(exc)

    def detach_current(self):
        self.catalogue.detach_playlist(self.playlist_id)
        self.refresh_playlists()

    def delete_current_playlist(self):
        if self.playlist_id and QMessageBox.question(self,"Remove playlist","Remove this app playlist? Music and the Spotify playlist stay unchanged.")==QMessageBox.StandardButton.Yes:
            self.catalogue.delete_playlist(self.playlist_id)
            self.refresh()

    def entries_context(self,point):
        row=self.entries_table.rowAt(point.y())
        if row<0:
            return
        if not self.entries_table.item(row,0).isSelected():self.entries_table.selectRow(row)
        self.entries_table.setCurrentCell(row,0,QItemSelectionModel.SelectionFlag.NoUpdate)
        self.entries_table.selectRow(row)
        entry=self.entries_table.item(row,0).data(ROLE)
        menu=QMenu(self)
        if entry["recording_id"]:menu.addAction("Play / preview",lambda:self.play_row(entry))
        if entry["spotify_id"]:
            menu.addAction("Review local match",self.review_match)
            menu.addAction('Not in local library',lambda:self.mark_entry_missing(entry))
            if entry["match_state"]=="missing":
                menu.addAction("Queue download",lambda:self.queue_one(entry["spotify_id"]))
        files=[file for file in self.catalogue.files() if file["recording_id"]==entry["recording_id"]]
        if files:
            file=dict(files[0],kind="file")
            menu.addAction("File details",lambda:self.show_file_details(file))
            menu.addAction("Show in File Explorer",lambda:self.explorer(self.catalogue.resolve(file)))
        menu.addAction("Export playlist",self.export_playlist)
        playlist=next((p for p in self.catalogue.playlists() if p['id']==self.playlist_id),None)
        if playlist and (playlist['source']=='manual' or not entry['spotify_id']):
            menu.addAction('Move up',lambda:self.move_entry(-1));menu.addAction('Move down',lambda:self.move_entry(1))
            menu.addAction('Remove from playlist',self.remove_entry)
        menu.exec(self.entries_table.viewport().mapToGlobal(point))

    def queue_one(self,track_id):
        self.catalogue.enqueue(track_id)
        self.refresh_downloads()

    def local_spotify_help(self):
        from .phone_export import INSTRUCTIONS
        QMessageBox.information(self,'Local files in Spotify',INSTRUCTIONS)

    def export_local_extras(self):
        try:
            if not self.playlist_id:return
            from .phone_export import export_extras
            destination,_=QFileDialog.getSaveFileName(self,'Export local extras for phone','Local extras.zip','ZIP (*.zip)')
            if not destination:return
            playlist_id=self.playlist_id
            self.start_job('Exporting local extras',lambda progress,cancel:export_extras(self.catalogue,playlist_id,destination),
                lambda result:QMessageBox.information(self,'Local extras exported',f"{result['files']} file(s) copied unchanged. {len(result['missing'])} unavailable.\nExtract the ZIP and follow Spotify local files.txt to transfer them to your phone."),False)
        except Exception as exc:self.show_error(exc)

    def results_context(self,point):
        row=self.results_table.rowAt(point.y())
        if row>=0:
            self.results_table.selectRow(row)
            menu=QMenu(self)
            menu.addAction("Queue selected",self.queue_results)
            item=self.results_table.item(row,0).data(ROLE)
            if item["type"]=="track":
                menu.addAction("Review local match",lambda:self.review_track(item))
            menu.addAction("Open in Spotify",lambda:QDesktopServices.openUrl(QUrl(f"https://open.spotify.com/{item['type']}/{item['id']}")))
            menu.exec(self.results_table.viewport().mapToGlobal(point))

    def downloads_context(self,point):
        row=self.downloads_table.rowAt(point.y())
        if row<0:
            return
        job=self.downloads_table.item(row,0).data(ROLE)
        if not self.downloads_table.item(row,0).isSelected():self.downloads_table.selectRow(row)
        selected=self.selected_downloads()
        menu=QMenu(self)
        if selected and all(j['state'] in {'queued','paused','failed'} for j in selected):
            menu.addAction('Download selected next',self.download_selected_next)
            menu.addAction('Move up',lambda:self.move_queue_selection(-1));menu.addAction('Move down',lambda:self.move_queue_selection(1))
        menu.addAction(f'Remove selected ({len(selected)})',self.remove_selected_downloads)
        if job['sources']:
            for source in job['sources'][:10]:
                ids=[j['id'] for j in self.catalogue.download_jobs() if j['state'] in {'queued','paused','failed'} and any(s['id']==source['id'] for s in j['sources'])]
                if ids:menu.addAction('Download '+source['name']+' first',lambda ids=ids:self.prioritise_download_ids(ids))
        if job["output"]:
            menu.addAction("Show in File Explorer",lambda:self.explorer(job["output"]))
        menu.exec(self.downloads_table.viewport().mapToGlobal(point))

    def cancel_queue_entry(self,job_id):
        self.remove_download_ids([job_id])

    def nativeEvent(self,event_type,message):
        hit=resize_hit(self,message)
        if hit is not None:return True,hit
        return super().nativeEvent(event_type,message)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange and hasattr(self,"titlebar"):
            self.titlebar.maximise_button.setText("❐" if self.isMaximized() else "□")

    def showEvent(self,event):
        super().showEvent(event)
        if not getattr(self,"native_frame_configured",False):
            self.native_frame_configured=True
            configure_frame(self)

    def maybe_close(self):
        if self.close_pending and not (self.job or self.download_job or self.scan_job):QTimer.singleShot(0,self.close)

    def closeEvent(self,event):
        if self.job or self.download_job or self.scan_job:
            self.close_pending=True
            self.cancel_task()
            self.pause_downloads()
            self.status.setText("Stopping tasks; ZotiBox will close when they finish.")
            event.ignore()
        else:
            if not self.player.shutdown():
                self.status.setText("Stopping waveform decoding…");QTimer.singleShot(250,self.close);event.ignore();return
            self.artwork.close()
            if getattr(self,"instance",None):self.instance.close()
            self.workspace_lock.unlock()
            event.accept()
