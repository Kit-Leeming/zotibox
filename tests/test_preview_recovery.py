"""Recovery checks use disposable native databases, never the user's library."""
import copy
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from test_rekordbox import rb
from dj_library.rekordbox import Rekordbox,database,fingerprint
from dj_library.rekordbox_recovery import PreviewConflict


def test_startup_reuses_unchanged_preview_and_refreshes_external_edits(rb):
    session=rb.state['session']
    rb.startup_preview(lambda *_:None,threading.Event())
    assert rb.state['session']==session
    rb.edit_track('1',{'comment':'Keep pending'})
    native_edit(rb);live=fingerprint(rb.source)
    view=rb.startup_preview(lambda *_:None,threading.Event())
    assert next(t for t in view['tracks'] if t['id']=='1')['comment']=='Keep pending'
    assert next(t for t in view['tracks'] if t['id']=='2')['title']=='External edit'
    assert view['changes'] and fingerprint(rb.source)==live


def native_edit(rb,sql="UPDATE djmdContent SET Title='External edit' WHERE ID='2'"):
    with sqlite3.connect(rb.source) as db:db.execute(sql)


def test_refresh_replays_created_hierarchy_entries_metadata_and_restart(rb):
    folder=rb.create_playlist('Sets',folder=True)
    playlist=rb.create_playlist('Warmup',folder)
    rb.add_tracks(playlist,['1','2','1'])
    songs=next(p for p in rb.view()['playlists'] if p['id']==playlist)['songs']
    rb.move_song(playlist,songs[2]['id'],1)
    rb.remove_songs(playlist,[songs[1]['id']])
    rb.rename_playlist(playlist,'Friday')
    rb.edit_track('1',{'rating':4,'comment':'Keep me'})
    rb=Rekordbox(rb.catalogue)
    native_edit(rb);source=fingerprint(rb.source)
    view=rb.refresh_preserving()
    assert fingerprint(rb.source)==source
    parent=next(p for p in view['playlists'] if p['name']=='Sets')
    playlist=next(p for p in view['playlists'] if p['name']=='Friday')
    assert playlist['parent']==parent['id']
    assert [s['track_id'] for s in playlist['songs']]==['1','1']
    assert next(t for t in view['tracks'] if t['id']=='1')['comment']=='Keep me'
    assert next(t for t in view['tracks'] if t['id']=='2')['title']=='External edit'
    rb.catalogue.set_setting('rekordbox_write_enabled',True)
    rb.apply()
    with database(rb.source) as db:assert db.get_cue(ID='cue').InMsec==1000


def test_metadata_merge_preserves_external_fields_and_conflict_preserves_preview(rb):
    rb.edit_track('1',{'rating':5,'title':'one'})  # unchanged title must not be replayed
    native_edit(rb,"UPDATE djmdContent SET Title='Native title' WHERE ID='1'")
    view=rb.refresh_preserving()
    assert next(t for t in view['tracks'] if t['id']=='1')['title']=='Native title'
    rb.edit_track('1',{'comment':'My comment'})
    original=copy.deepcopy(rb.state);pending=rb.state_path.read_bytes();staged=fingerprint(rb.staged)
    native_edit(rb,"UPDATE djmdContent SET Commnt='Their comment' WHERE ID='1'")
    source=fingerprint(rb.source)
    with pytest.raises(PreviewConflict,match='comment changed in both'):rb.refresh_preserving()
    assert rb.state==original and rb.state_path.read_bytes()==pending
    assert fingerprint(rb.staged)==staged and fingerprint(rb.source)==source


def test_sync_recovery_keeps_native_extras_and_name(rb):
    c=rb.catalogue
    with database(rb.source) as db:music=Path(db.get_content(ID='1').FolderPath).parent
    c.add_root(music);c.scan()
    local=c.create_playlist('Local source')
    recording=next(f['recording_id'] for f in c.files() if c.resolve(f).name=='one.wav')
    c.add_entries(local,[recording])
    target=rb.create_playlist('Native name');rb.add_tracks(target,['2'])
    c.set_setting('rekordbox_write_enabled',True);rb.apply()
    # The preview predates a native rename and native membership changes.
    rb.sync_local(local,target)
    external=Rekordbox(c);external.state=None;external.connect()
    external.rename_playlist(target,'Updated in Rekordbox');external.add_tracks(target,['2']);external.apply()
    # Restore the original app's saved preview, as an independent native app would not write it.
    rb.save()
    view=rb.refresh_preserving();playlist=next(p for p in view['playlists'] if p['id']==target)
    assert playlist['name']=='Updated in Rekordbox'
    assert [s['track_id'] for s in playlist['songs']]==['1','2','2']
    rb.apply();assert next(p for p in c.playlists() if p['id']==local)['rekordbox_id']==target


def test_delete_conflict_and_cancel_leave_preview_recoverable(rb):
    target=rb.create_playlist('Delete later');rb.catalogue.set_setting('rekordbox_write_enabled',True);rb.apply()
    rb.delete_playlist(target);pending=rb.state_path.read_bytes()
    native_edit(rb,f"UPDATE djmdPlaylist SET Name='Renamed externally' WHERE ID='{target}'")
    with pytest.raises(PreviewConflict,match='marked for deletion'):rb.refresh_preserving()
    assert rb.state_path.read_bytes()==pending
    cancel=threading.Event();cancel.set()
    with pytest.raises(InterruptedError):rb.refresh_preserving(cancel=cancel)
    assert rb.state_path.read_bytes()==pending


def test_legacy_or_unsupported_preview_is_retained_and_discard_failure_is_atomic(rb):
    rb.create_playlist('Legacy');rb.state.pop('recipes');rb.save();original=rb.state_path.read_bytes()
    native_edit(rb)
    with pytest.raises(PreviewConflict,match='older edits'):rb.refresh_preserving()
    assert rb.state_path.read_bytes()==original
    previous=copy.deepcopy(rb.state)
    rb.catalogue.set_setting('rekordbox_path',str(rb.source.parent/'missing.db'))
    with pytest.raises(ValueError):rb.discard()
    assert rb.state==previous and rb.state_path.read_bytes()==original


def test_already_satisfied_metadata_edit_is_safe(rb):
    rb.edit_track('1',{'comment':'Same'})
    native_edit(rb,"UPDATE djmdContent SET Commnt='Same' WHERE ID='1'")
    view=rb.refresh_preserving()
    assert next(t for t in view['tracks'] if t['id']=='1')['comment']=='Same'
    assert not view['changes']


def test_partial_replay_cancellation_and_failed_stage_keep_original_preview(rb,monkeypatch):
    rb.create_playlist('First');rb.create_playlist('Second');native_edit(rb)
    pending=rb.state_path.read_bytes();previous=copy.deepcopy(rb.state)
    cancel=threading.Event()
    def progress(index,message):
        if 'edit 2' in message:cancel.set()
    with pytest.raises(InterruptedError):rb.refresh_preserving(progress,cancel)
    assert rb.state==previous and rb.state_path.read_bytes()==pending
    original=rb.view;session=rb.state['session']
    def view():
        if rb.state['session']!=session:raise ValueError('Cannot read candidate preview')
        return original()
    monkeypatch.setattr(rb,'view',view)
    with pytest.raises(ValueError,match='candidate preview'):rb.create_playlist('Failed edit')
    assert rb.state==previous and rb.state_path.read_bytes()==pending


def test_imported_track_and_subsequent_metadata_replay_keep_track_identity(rb,tmp_path):
    from test_sync_downloads import tagged
    new=tagged(tmp_path/'imports'/'new.wav',title='Imported')
    c=rb.catalogue;c.add_root(new.parent);c.scan()
    playlist=rb.create_playlist('Imports')
    rb.add_recordings(playlist,[c.files()[0]['recording_id']])
    track=next(t for t in rb.view()['tracks'] if t['path']==str(new))
    rb.edit_track(track['id'],{'comment':'Remember this'})
    native_edit(rb)
    view=rb.refresh_preserving()
    track=next(t for t in view['tracks'] if t['path']==str(new))
    assert track['comment']=='Remember this'
    playlist=next(p for p in view['playlists'] if p['name']=='Imports')
    assert playlist['songs'][0]['track_id']==track['id']
