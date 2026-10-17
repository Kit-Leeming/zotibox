"""Dedicated settings page for downloader preferences and integration details."""
from pathlib import Path
from PySide6.QtCore import Qt,QSignalBlocker
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QLineEdit,QTableWidget,QTableWidgetItem,QFileDialog,QMessageBox
from .audio_preferences import fill_formats,fill_qualities,value as audio_value
from .downloads import schema,MANAGED,PATTERNS,validated_options,worker_config

DEFAULTS={**{k:'{artist} - {song_name}' for k in PATTERNS},'OUTPUT':'','API_CLIENT_ID':'','DOWNLOAD_RATE_LIMITER':1.0,'OPTIMIZED_DOWNLOADING':False,'API_CLIENT_LEGACY':False,'DOWNLOAD_FORMAT':'auto','DOWNLOAD_QUALITY':'lossless'}
INTERNAL={
    'ROOT_PATH':'Choose the shared music destination above. Verified downloads are placed there.',
    'ROOT_PODCAST_PATH':'The shared destination above is used for the single-track queue.',
    'SAVE_CREDENTIALS':'Login is saved encrypted for this Windows user; use Fresh Zotify login to renew it.',
    'CREDENTIALS_LOCATION':'Temporary login data is kept inside the active job and removed after use. Saved login is encrypted.',
    'API_CREDENTIALS_LOCATION':'Developer authorization uses the same encrypted login storage.',
    'DOWNLOAD_PARENT_ALBUM':'Paste an album URL into Downloads to queue its tracks individually.',
    'TEMP_DOWNLOAD_DIR':'Each queue entry has its own temporary folder for validation and safe cancellation.',
    'SONG_ARCHIVE_LOCATION':'The ZotiBox catalogue provides download history and duplicate detection.',
    'DISABLE_SONG_ARCHIVE':'The catalogue tracks recordings shared by multiple playlists.',
    'DISABLE_DIRECTORY_ARCHIVES':'Playlist folders share music through catalogue references.',
    'SKIP_EXISTING':'Existing music is checked by the catalogue; completed files are never overwritten.',
    'EXPORT_M3U8':'Configure playlist exports in Playlists → Export.',
    'M3U8_LOCATION':'Choose playlist export locations in Playlists → Export.',
    'LYRICS_LOCATION':'Sidecar lyrics are copied beside the verified music file.',
    'IMPORT_ZMD_LOCATION':'Metadata import is scoped to the temporary job folder.',
    'EXPORT_ZMD_LOCATION':'Metadata export is scoped to the temporary job folder.',
    'STANDARD_INTERFACE':'The worker uses structured progress events for the download queue.',
    'PRINT_SPLASH':'Terminal splash output is omitted in the desktop app.'}
HELP={
    'DOWNLOAD_FORMAT':'Auto: genuine FLAC if available, otherwise MP3 320 kbps. MP3: always encode 320 kbps. FLAC: requires a lossless source. Original: preserve the selected source codec.',
    'DOWNLOAD_QUALITY':'Preferred source, independent of output. Lossless where available falls back to the best lossy representation. Explicit FLAC output always requires lossless.',
    'DOWNLOAD_RATE_LIMITER':'Audio-duration multiplier: 1 adds real-time pacing; 0 adds no delay. Downloads remain sequential.',
    'OPTIMIZED_DOWNLOADING':'Enable the fork’s optimisation within a job; ZotiBox still runs one queue entry at a time.',
    'CUSTOM_FFMEPG_ARGS':'Additional FFmpeg output arguments, for example -ar 48000. The fork may fall back if they fail.',
    'API_CLIENT_ID':'Optional Zotify-specific Client ID. Leave empty to use Settings → Spotify account.',
    'REDIRECT_ADDRESS':'Local loopback address for Zotify login. Register the matching redirect URI in the developer app.',
    'REDIRECT_PORT':'Zotify login port. Register http://<address>:<port>/login in the developer app after changing it.',
    'REDIRECT_TIMEOUT':'Seconds to wait for each browser login.',
    'OUTPUT':'Optional pattern for all final filenames. Leave empty to use the single/album/playlist patterns.',
    'OUTPUT_SINGLE':'Final filename pattern for directly queued tracks.',
    'OUTPUT_ALBUM':'Final filename pattern for tracks queued from an album.',
    'OUTPUT_PLAYLIST_EXT':'Final filename pattern for tracks queued from a playlist.',
    'OUTPUT_LIKED_SONGS':'Pattern retained for liked-song sources. Liked Songs bulk import is not currently available.',
    'MAX_FILENAME_LENGTH':'Upstream temporary filename limit. Final pattern components are limited to 140 characters.',
    'TRANSCODE_BITRATE':'MP3 output is fixed at 320 kbps. Original preserves the source bitrate; FLAC is lossless. Source preference is selected separately.',
    'DOWNLOAD_QUALITY':'Requested source quality. Actual source availability may differ.',
    'DOWNLOAD_FORMAT':'Output codec. copy keeps the selected source codec; FLAC conversion does not restore lost audio data.',
    'FFMPEG_LOG_LEVEL':'Verbosity of FFmpeg conversion output inside the worker.',
    'ALBUM_ART_TO_FILE':'Copy a single upstream artwork sidecar beside the music file. Embedded artwork is retained.'}

def category(key):
    if key in MANAGED:return 'Integration details'
    if key in PATTERNS or key.startswith('OUTPUT_') or key in {'MAX_FILENAME_LENGTH','SPLIT_ALBUM_DISCS'}:return 'File names'
    if key.startswith(('LYRICS','ALWAYS_CHECK_LYRICS')):return 'Lyrics'
    if key.startswith(('MD_','LANGUAGE','ALBUM_ART')):return 'Metadata'
    if key.startswith(('API_','REDIRECT_','FETCH_','RETRY_','ESCALATING')):return 'Connection'
    if key.startswith(('DOWNLOAD_','TRANSCODE_','CUSTOM_','FFMPEG_')) or key=='OPTIMIZED_DOWNLOADING':return 'Audio'
    return 'Advanced'

class ZotifySettings(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.loading=True;layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        layout.addWidget(QLabel('Music destination'))
        row=QHBoxLayout();self.destination=QLineEdit();row.addWidget(self.destination,1);row.addWidget(owner.button('Browse…',self.browse,'SP_DirOpenIcon'));layout.addLayout(row)
        note=QLabel('Audio, metadata and connection preferences apply to subsequent downloads. Filename patterns support artist, album, song_name, song_title, title, song_id, isrc, playlist, playlist_num and album_num. Use / for subfolders.');note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();self.search=owner.search_bar('Search Zotify settings');row.addWidget(self.search,1)
        self.category=QComboBox();self.category.addItems(['All settings','Audio','File names','Metadata','Lyrics','Connection','Advanced','Integration details']);self.category.setCurrentText('Audio');row.addWidget(self.category);layout.addLayout(row)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['Setting','Value','Details']);self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide();self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection);self.table.setWordWrap(True)
        self.table.setColumnWidth(0,210);self.table.setColumnWidth(1,210);layout.addWidget(self.table)
        self.editors={};self.definitions=schema();self.table.setRowCount(len(self.definitions))
        enums={'DOWNLOAD_FORMAT':['auto','mp3','flac','original'],'DOWNLOAD_QUALITY':['auto','lossless','very_high','high','normal']}
        for index,(key,definition) in enumerate(self.definitions.items()):
            label=QTableWidgetItem(key.replace('_',' ').title());label.setToolTip(key+'\n'+' '.join(definition['flags']));label.setFlags(label.flags()&~Qt.ItemFlag.ItemIsEditable);self.table.setItem(index,0,label)
            if definition['type']=='bool' or key in enums:
                editor=QComboBox();editor.addItems(['True','False'] if definition['type']=='bool' else enums[key]);editor.currentTextChanged.connect(self.changed)
                if key=='DOWNLOAD_FORMAT':fill_formats(editor)
                if key=='DOWNLOAD_QUALITY':fill_qualities(editor)
            else:editor=QLineEdit();editor.textChanged.connect(self.changed)
            editor.setEnabled(key not in MANAGED and key!='TRANSCODE_BITRATE');editor.setToolTip(key);editor.setMaximumHeight(36)
            holder=QWidget();holder_layout=QVBoxLayout(holder);holder_layout.setContentsMargins(4,6,4,6);holder_layout.addWidget(editor,0,Qt.AlignmentFlag.AlignVCenter)
            self.table.setCellWidget(index,1,holder);self.editors[key]=editor
            detail=INTERNAL.get(key) or HELP.get(key) or f"{category(key)} preference. Type: {definition['type']}."
            item=QTableWidgetItem(detail);item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable);self.table.setItem(index,2,item);self.table.setRowHeight(index,64)
        self.search.textChanged.connect(self.filter);self.category.currentIndexChanged.connect(self.filter)
        row=QHBoxLayout();row.addWidget(owner.button('Save Zotify settings',self.save,'SP_DialogSaveButton',True));row.addWidget(owner.button('Reset defaults',self.reset));row.addStretch();layout.addLayout(row)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status);self.destination.textChanged.connect(self.changed);self.load()

    def filter(self,*_):
        query=self.search.text().casefold();selected=self.category.currentText()
        for i,key in enumerate(self.definitions):
            text=key+' '+self.table.item(i,0).text()+' '+self.table.item(i,2).text()
            self.table.setRowHidden(i,(not query and selected!='All settings' and category(key)!=selected) or (bool(query) and query not in text.casefold()))

    def load(self,defaults=False):
        self.loading=True;options={} if defaults else self.owner.catalogue.get_setting('zotify_options',{})
        effective=worker_config(self.owner.catalogue,self.owner.catalogue.workspace/'.local/staging/job')
        self.destination.setText(self.owner.catalogue.get_setting('download_folder',str(self.owner.catalogue.workspace/'Music')))
        for key,editor in self.editors.items():
            value=options.get(key,DEFAULTS.get(key,self.definitions[key]['default'])) if key not in MANAGED else effective[key]
            if key=='TRANSCODE_BITRATE':value='320k'
            if key=='DOWNLOAD_FORMAT':fill_formats(editor,str(value))
            elif key=='DOWNLOAD_QUALITY':fill_qualities(editor,str(value))
            elif isinstance(editor,QComboBox):editor.setCurrentText(str(value))
            else:editor.setText(str(value))
        self.loading=False;self.status.setText('Defaults loaded; save to apply.' if defaults else 'Preferences saved.');self.filter()

    def changed(self,*_):
        if not self.loading:self.status.setText('Unsaved changes')

    def reset(self):self.load(defaults=True)

    def browse(self):
        path=QFileDialog.getExistingDirectory(self,'Choose music destination',self.destination.text())
        if path:self.destination.setText(path)

    def save(self):
        try:
            destination=Path(self.destination.text()).expanduser()
            if not destination.is_absolute():raise ValueError('Choose an absolute music destination path')
            if str(destination)!=self.owner.catalogue.get_setting('download_folder',str(self.owner.catalogue.workspace/'Music')):self.owner.ensure_idle()
            values=validated_options({key:(audio_value(editor) if isinstance(editor,QComboBox) else editor.text()) for key,editor in self.editors.items() if key not in MANAGED})
            self.owner.catalogue.set_setting('zotify_options',values);self.owner.catalogue.set_setting('download_folder',str(destination));self.owner.download_folder.setText(str(destination))
            self.owner.zotify_redirect_reference.setText(f"http://{values['REDIRECT_ADDRESS']}:{values['REDIRECT_PORT']}/login")
            for widget,key in [(self.owner.codec,'DOWNLOAD_FORMAT'),(self.owner.quality,'DOWNLOAD_QUALITY')]:
                block=QSignalBlocker(widget)
                if key=='DOWNLOAD_FORMAT':fill_formats(widget,str(values[key]))
                else:fill_qualities(widget,str(values[key]))
                del block
            self.status.setText('Saved. The running download keeps its settings; queued tracks use these preferences.')
        except Exception as exc:QMessageBox.warning(self,'Invalid Zotify setting',str(exc))
