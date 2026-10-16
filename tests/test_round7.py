import json,os,subprocess,sys,threading,time
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QApplication,QInputDialog
from dj_library.instance import Instance,notify_existing,acquire_lock
from dj_library.rekordbox import Rekordbox,database
from dj_library.ui import Window
from dj_library.playlist_picker import PlaylistPicker
from test_rekordbox import rb
from test_catalogue import audio

def app():return QApplication.instance() or QApplication([])
def pump_until(predicate,seconds=4):
    end=time.monotonic()+seconds
    while not predicate() and time.monotonic()<end:app().processEvents();time.sleep(.01)
    assert predicate()

def test_second_launch_activates_existing_owner_without_unlocking(tmp_path):
    app();owner=Instance(tmp_path);assert owner.acquire()
    received=[];owner.activate.connect(lambda:received.append(True))
    assert notify_existing(tmp_path);pump_until(lambda:bool(received))
    duplicate=Instance(tmp_path);assert duplicate.acquire() is False
    with pytest.raises(ValueError,match='already open'):acquire_lock(tmp_path)
    owner.close();replacement=Instance(tmp_path);assert replacement.acquire();replacement.close()

def test_dead_process_lock_is_recovered_but_live_pid_is_protected(tmp_path):
    app();code='from PySide6.QtCore import QCoreApplication,QLockFile;import sys,time;a=QCoreApplication([]);l=QLockFile(sys.argv[1]);assert l.tryLock(0);print("locked",flush=True);time.sleep(30)'
    child=subprocess.Popen([sys.executable,'-c',code,str(tmp_path/'.application.lock')],stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='locked'
        with pytest.raises(ValueError):acquire_lock(tmp_path)
    finally:child.kill();child.wait();child.stdout.close() # Only this disposable test child.
    lock=acquire_lock(tmp_path);lock.unlock()

def test_deferred_startup_avoids_reading_saved_native_preview_and_music_tags(tmp_path,monkeypatch):
    app();reads=[]
    monkeypatch.setattr('dj_library.rekordbox.Rekordbox.view',lambda self:reads.append(True))
    window=Window(tmp_path/'database',defer_startup=True)
    assert reads==[]
    assert not window.files_table.topLevelItemCount()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show()
    pump_until(lambda:window.status.text()=='Ready');window.close()

def test_embedded_art_is_loaded_off_gui_thread_only_for_visible_rows(tmp_path,monkeypatch):
    app();threads=[]
    def cover(*args):
        threads.append(threading.get_ident());image=QImage(2,2,QImage.Format.Format_RGB32);image.fill(QColor('red'));return image
    monkeypatch.setattr('dj_library.artwork_async.cover',cover)
    window=Window(tmp_path/'database');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show()
    path=audio(tmp_path/'music'/'Track.wav');window.catalogue.add_root(path.parent);window.catalogue.scan();window.refresh_files()
    assert threads==[]
    window.navigation.setCurrentRow(2);pump_until(lambda:bool(window.artwork.images))
    assert threads and all(id_!=threading.get_ident() for id_ in threads)
    window.close()

def test_sync_empty_and_partial_playlist_keeps_source_and_skipped_order(rb,tmp_path):
    c=rb.catalogue;track={'id':'a'*22,'title':'Missing','artist':'Artist','duration':1,'playable':True}
    snapshot={'id':'b'*22,'name':'Spotify set','snapshot':'one','complete':True,'tracks':[track]}
    result=c.sync_playlist(snapshot,{track['id']:{'recording_id':None,'state':'missing'}})
    target=rb.sync_local(result['playlist_id'])
    assert next(p for p in rb.view()['playlists'] if p['id']==target)['songs']==[]
    assert len(rb.state['skipped_tracks'][result['playlist_id']])==1
    c.add_root(tmp_path/'music');c.scan();recording=c.files()[0]['recording_id'];c.decide_match(track['id'],recording)
    assert rb.sync_local(result['playlist_id'])==target
    assert len(next(p for p in rb.view()['playlists'] if p['id']==target)['songs'])==1
    assert not rb.state['skipped_tracks'][result['playlist_id']]
    assert len(c.entries(result['playlist_id']))==1

def test_delete_native_folder_preserves_tracks_cues_and_clears_links_only_on_apply(rb):
    before=rb.source.read_bytes();folder=rb.create_playlist('Folder',folder=True);playlist=rb.create_playlist('Set',folder)
    rb.add_tracks(playlist,['1']);local=rb.catalogue.create_playlist('Local')
    rb.catalogue.set_setting('rekordbox_write_enabled',True);rb.apply()
    with rb.catalogue.connect() as db:db.execute('UPDATE playlists SET rekordbox_id=? WHERE id=?',(playlist,local))
    before=rb.source.read_bytes();rb.delete_playlist(folder)
    assert not any(p['id'] in {folder,playlist} for p in rb.view()['playlists'])
    assert len(rb.view()['tracks'])==2 and rb.source.read_bytes()==before
    assert rb.catalogue.playlists()[0]['rekordbox_id']==playlist
    with database(rb.staged) as db:assert db.get_cue(ID='cue').InMsec==1000
    rb.apply();assert rb.catalogue.playlists()[0]['rekordbox_id'] is None

def test_corrupt_preview_is_recoverable_without_overwriting_it(tmp_path):
    from dj_library.catalogue import Catalogue
    c=Catalogue(tmp_path/'database');folder=c.workspace/'.local'/'rekordbox';folder.mkdir(parents=True)
    path=folder/'pending.json';path.write_text('{broken',encoding='utf-8')
    backend=Rekordbox(c);assert backend.load_error and backend.state is None
    app();window=Window(c.workspace);assert 'cannot load' in window.rb_panel.summary.text()
    assert path.read_text()=='{broken';window.close()

def test_picker_can_create_and_select_new_playlist_without_losing_search(tmp_path,monkeypatch):
    app();created=[]
    def create(name):created.append(name);return {'id':'new','name':name,'source':'manual'}
    picker=PlaylistPicker([],create=create)
    monkeypatch.setattr(QInputDialog,'getText',lambda *args:('New set',True))
    picker.create_new();picker.accept_choice();assert picker.choice['id']=='new' and created==['New set'];picker.close()

def test_close_during_task_finishes_quit_automatically(tmp_path):
    app();window=Window(tmp_path/'database');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show()
    from dj_library.ui import Job
    def work(progress,cancel):
        while not cancel.wait(.01):pass
        return None
    window.start_job('Disposable task',work,lambda _:None)
    window.close();assert window.close_pending
    pump_until(lambda:window.job is None and not window.isVisible())
    lock=acquire_lock(window.catalogue.workspace);lock.unlock()

def test_remove_multiple_native_entries_keeps_collection(rb):
    playlist=rb.create_playlist('Set');rb.add_tracks(playlist,['1','2','1'])
    songs=next(p['songs'] for p in rb.view()['playlists'] if p['id']==playlist)
    rb.remove_songs(playlist,[songs[0]['id'],songs[-1]['id']])
    assert [s['track_id'] for s in next(p['songs'] for p in rb.view()['playlists'] if p['id']==playlist)]==['2']
    assert len(rb.view()['tracks'])==2
