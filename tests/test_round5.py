import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from dj_library.catalogue import Catalogue
from dj_library.ui import Window
from dj_library.downloads import source_description
from test_catalogue import audio

def test_remove_file_keeps_playlist_identity_and_rejects_changed_file(tmp_path):
    catalogue=Catalogue(tmp_path/'database');path=audio(tmp_path/'music'/'Track.wav')
    catalogue.add_root(path.parent);catalogue.scan();row=catalogue.files()[0]
    playlist=catalogue.create_playlist('Before');catalogue.add_entries(playlist,[row['recording_id']])
    catalogue.rename_playlist(playlist,'After');assert catalogue.playlists()[0]['name']=='After'
    before=path.read_bytes();path.write_bytes(before+b'changed')
    with pytest.raises(ValueError,match='changed'):catalogue.remove_file(row['id'],lambda p:p.unlink())
    assert path.exists() and catalogue.files()[0]['available']
    path.write_bytes(before)
    def cancelled(p):raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError):catalogue.remove_file(row['id'],cancelled)
    assert catalogue.files()[0]['available']
    catalogue.remove_file(row['id'],lambda p:p.unlink()) # Disposable fixture, no shell or user music.
    assert not catalogue.files()[0]['available']
    assert catalogue.entries(playlist)[0]['recording_id']==row['recording_id']

def test_source_quality_does_not_infer_lossless_from_output_extension():
    assert source_description('FLAC_FLAC_24BIT').startswith('Lossless Spotify source')
    assert source_description('OGG_VORBIS_320').startswith('Lossy Spotify source')
    assert 'unverified' in source_description('unverified')

def test_row_size_thumbnail_and_rekordbox_expansion_persist(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=Window(tmp_path/'database');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    path=audio(tmp_path/'music'/'Track.wav');window.catalogue.add_root(path.parent);window.catalogue.scan();window.refresh_files()
    assert not window.files_table.topLevelItem(0).child(0).icon(0).isNull()
    window.set_row_height(72);app.processEvents()
    assert window.files_table.iconSize().height()==64
    assert window.rb_panel.table.verticalHeader().defaultSectionSize()==72
    panel=window.rb_panel
    panel.data['playlists']=[{'id':'folder','name':'Folder','type':1,'parent':None,'seq':0,'songs':[]},
        {'id':'child','name':'Playlist','type':0,'parent':'folder','seq':1,'songs':[]}]
    panel.refresh_tree();folder=panel.tree.topLevelItem(1)
    assert not folder.isExpanded()
    folder.setExpanded(True);panel.refresh_tree();assert panel.tree.topLevelItem(1).isExpanded()
    panel.tree.topLevelItem(1).setExpanded(False);panel.refresh_tree();assert not panel.tree.topLevelItem(1).isExpanded()
    window.close();app.processEvents()
