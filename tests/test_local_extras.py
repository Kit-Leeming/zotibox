import hashlib
import json
import zipfile
import pytest
from PySide6.QtCore import QPoint,Qt
from dj_library.catalogue import Catalogue
from dj_library.phone_export import export_extras
from test_catalogue import audio
from test_sync_downloads import remote
from test_round6 import app
from dj_library.ui import Window,ROLE

def setup(catalogue,tmp_path):
    for i in range(3):audio(tmp_path/'Music'/f'Track{i}.wav',8000+i*100)
    catalogue.add_root(tmp_path/'Music');catalogue.scan()
    tracks=[remote('A'*22),remote('B'*22,title='Second')]
    recordings=[f['recording_id'] for f in catalogue.files()]
    resolved={t['id']:{'recording_id':recordings[i],'state':'matched'} for i,t in enumerate(tracks)}
    snapshot={'id':'P'*22,'name':'Mixed','snapshot':'1','complete':True,'tracks':tracks}
    id_=catalogue.sync_playlist(snapshot,resolved)['playlist_id']
    return id_,recordings,snapshot,resolved

def test_local_extra_survives_spotify_removal_reorder_and_restart(tmp_path):
    catalogue=Catalogue(tmp_path/'Database');id_,recordings,snapshot,resolved=setup(catalogue,tmp_path)
    catalogue.add_entries(id_,[recordings[2],recordings[2]])
    extra=catalogue.entries(id_)[-1];assert len(catalogue.entries(id_))==3
    snapshot.update(snapshot='2',tracks=list(reversed(snapshot['tracks'])))
    catalogue.sync_playlist(snapshot,resolved)
    assert [e['spotify_id'] for e in catalogue.entries(id_)]==['B'*22,'A'*22,None]
    catalogue=Catalogue(catalogue.workspace);snapshot.update(snapshot='3',tracks=[]);catalogue.sync_playlist(snapshot,{})
    assert catalogue.entries(id_)[0]['id']==extra['id']
    assert len(catalogue.files())==3

def test_remote_order_protected_local_extras_editable(tmp_path):
    catalogue=Catalogue(tmp_path/'Database');id_,recordings,snapshot,resolved=setup(catalogue,tmp_path)
    catalogue.add_entries(id_,recordings[1:]);entries=catalogue.entries(id_)
    with pytest.raises(ValueError,match='Spotify'):catalogue.reorder(id_,[e['id'] for e in reversed(entries)])
    with pytest.raises(ValueError,match='Spotify'):catalogue.remove_entry(id_,entries[0]['id'])
    catalogue.remove_entry(id_,entries[-1]['id']);assert len(catalogue.entries(id_))==2

def test_phone_export_copies_only_extras_unchanged_and_never_overwrites(tmp_path):
    catalogue=Catalogue(tmp_path/'Database');id_,recordings,_,_=setup(catalogue,tmp_path)
    catalogue.add_entries(id_,[recordings[2]]);extra=next(f for f in catalogue.files() if f['recording_id']==recordings[2]);path=catalogue.resolve(extra)
    digest=hashlib.sha256(path.read_bytes()).hexdigest();dest=tmp_path/'Phone.zip';result=export_extras(catalogue,id_,dest)
    assert result['files']==1
    with zipfile.ZipFile(dest) as z:
        music=[n for n in z.namelist() if n.startswith('Music/')];assert len(music)==1
        assert hashlib.sha256(z.read(music[0])).hexdigest()==digest
        assert 'Web API cannot add local files' in z.read('Spotify local files.txt').decode()
        assert music[0] in z.read('Local extras.m3u8').decode()
        assert str(tmp_path) not in z.read('manifest.json').decode()
    with pytest.raises(ValueError,match='not overwritten'):export_extras(catalogue,id_,dest)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest

def test_ui_drop_local_track_into_spotify_link_and_remove_context(tmp_path,monkeypatch):
    app();window=Window(tmp_path/'Database');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    id_,recordings,_,_=setup(window.catalogue,tmp_path);window.playlist_id=id_;window.refresh_playlists()
    for i in range(window.playlist_list.count()):
        item=window.playlist_list.item(i)
        if item.data(ROLE) and item.data(ROLE)['id']==id_:window.playlist_list.setCurrentItem(item)
    row=next(f for f in window.catalogue.files() if f['recording_id']==recordings[2])
    assert window.drop_local(window.drag_data([row]),QPoint(0,99999))
    assert len(window.catalogue.entries(id_))==3
    assert 'Local extra' in window.entries_table.item(2,4).text()
    assert window.up_button.isHidden() and window.down_button.isHidden() and window.remove_entry_button.isHidden()
    window.close()
