import json,time,threading
import pytest
from PySide6.QtCore import Qt,QPoint,QMimeData
from PySide6.QtGui import QDragEnterEvent,QDragMoveEvent
from PySide6.QtWidgets import QApplication
from dj_library.ui import Window
from dj_library.playlist_picker import PlaylistPicker
from dj_library.dragdrop import MIME,decode
from dj_library.player import waveform
from test_catalogue import audio
from test_rekordbox import rb

def app():return QApplication.instance() or QApplication([])

def pump_until(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while not predicate() and time.monotonic()<deadline:app().processEvents();time.sleep(.01)
    assert predicate()

def test_picker_search_filters_without_visible_identifiers():
    app();picker=PlaylistPicker([{'id':'random-id','name':'House','source':'manual'},
        {'id':'other-id','name':'Garage','source':'manual','rekordbox_id':'native'}])
    assert picker.list.item(0).text()=='House'
    picker.search.setText('garage');assert picker.list.count()==1
    picker.accept_choice();assert picker.choice['id']=='other-id'
    picker.search.clear();picker.filter.setCurrentIndex(2);assert picker.list.count()==1
    picker.close()

def test_drag_add_reorder_and_hover_navigation(tmp_path,monkeypatch):
    app();window=Window(tmp_path/'database');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show()
    audio(tmp_path/'music'/'One.wav');audio(tmp_path/'music'/'Two.wav',16000)
    window.catalogue.add_root(tmp_path/'music');window.catalogue.scan()
    playlist=window.catalogue.create_playlist('Local');window.refresh();window.navigation.setCurrentRow(2)
    assert window.scan_button.isVisible()
    rows=window.catalogue.files();payload=window.drag_data(rows)
    assert window.drop_local(payload,QPoint(5,9999))
    entries=window.catalogue.entries(playlist);assert len(entries)==2
    moving=dict(payload,playlist=playlist,entries=[entries[0]['id']])
    assert window.drop_local(moving,QPoint(5,9999))
    assert window.catalogue.entries(playlist)[-1]['id']==entries[0]['id']
    mime=QMimeData();mime.setData(MIME,json.dumps(payload).encode());assert decode(mime)==payload
    point=window.navigation.visualItemRect(window.navigation.item(3)).center()
    enter=QDragEnterEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    move=QDragMoveEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    window.navigation.dragEnterEvent(enter);window.navigation.dragMoveEvent(move)
    pump_until(lambda:window.navigation.currentRow()==3,2)
    assert not window.scan_button.isVisible()
    errors=[];monkeypatch.setattr(window,'show_error',lambda error:errors.append(str(error)))
    assert not window.drop_local(dict(payload,workspace='another database'),QPoint(1,1));assert errors
    window.close()

def test_spotify_drop_is_protected_and_download_buttons_are_contextual(tmp_path,monkeypatch):
    app();window=Window(tmp_path/'database')
    snapshot={'id':'a'*22,'name':'Linked','snapshot':'one','tracks':[],'complete':True}
    window.catalogue.sync_playlist(snapshot,{});window.refresh_playlists()
    errors=[];monkeypatch.setattr(window,'show_error',lambda error:errors.append(str(error)))
    payload={'workspace':str(window.catalogue.workspace),'recordings':[]}
    assert not window.drop_local(payload,QPoint(0,0));assert 'Spotify' in errors[0]
    assert window.download_buttons['Retry failed'].isHidden()
    assert window.download_buttons['Queue selected'].isHidden()
    window.close()

def test_waveform_and_local_player_load_seek_without_audible_output(tmp_path):
    app();path=audio(tmp_path/'music'/'Track.wav',16000)
    peaks,length=waveform(path,threading.Event());assert peaks and max(peaks)>0 and length==2
    cancelled=threading.Event();cancelled.set()
    with pytest.raises(InterruptedError):waveform(path,cancelled)
    window=Window(tmp_path/'database');window.player.audio.setVolume(0)
    window.player.load(path,'Track',[{'position':.5,'end':.75,'label':'Memory point'}],autoplay=False)
    pump_until(lambda:not window.player.jobs)
    assert window.player.wave.peaks and window.player.cue_choice.itemText(1)=='Memory point'
    pump_until(lambda:window.player.media.duration()>0)
    window.player.jump_cue(1);pump_until(lambda:window.player.media.position()==500)
    window.player.dismiss();assert window.player.media.source().isEmpty() and window.player.path is None
    window.close()

def test_rekordbox_drag_import_reorder_and_cue_preview_preserve_live_source(rb,tmp_path):
    before=rb.source.read_bytes();playlist=rb.create_playlist('Drag target')
    catalogue=rb.catalogue;catalogue.add_root(tmp_path/'music');catalogue.scan()
    rows=catalogue.files();rb.add_recordings(playlist,[r['recording_id'] for r in rows])
    view=rb.view();songs=next(p['songs'] for p in view['playlists'] if p['id']==playlist)
    assert len(songs)==2 and {s['track_id'] for s in songs}=={'1','2'}
    rb.reorder_playlist(playlist,[s['id'] for s in reversed(songs)])
    assert next(p['songs'] for p in rb.view()['playlists'] if p['id']==playlist)[0]['id']==songs[-1]['id']
    assert next(t for t in view['tracks'] if t['id']=='1')['cues'][0]['position']==1
    assert rb.source.read_bytes()==before
