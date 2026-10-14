from pathlib import Path
import sqlite3
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from pyrekordbox.db6 import tables
from dj_library.catalogue import Catalogue,file_hash
from dj_library.rekordbox import Rekordbox,database,fingerprint
from test_sync_downloads import tagged


@pytest.fixture
def rb(tmp_path,monkeypatch):
    monkeypatch.setattr('dj_library.rekordbox.running',lambda:False)
    source=tmp_path/'native'/'master.db';source.parent.mkdir()
    engine=create_engine('sqlite:///'+str(source))
    for table in tables.Base.metadata.tables.values():
        for column in table.columns:
            if not column.primary_key:column.nullable=True
    tables.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        targets={(fk.column.table.name,fk.column.name) for table in tables.Base.metadata.tables.values() for fk in table.foreign_keys}
        for table,column in targets:
            conn.exec_driver_sql(f'CREATE UNIQUE INDEX IF NOT EXISTS "uq_{table}_{column}" ON "{table}" ("{column}")')
    first=tagged(tmp_path/'music'/'one.wav',title='One')
    second=tagged(tmp_path/'music'/'two.wav',title='Two')
    from datetime import datetime
    stamp=datetime.now().isoformat(sep=' ')
    with sqlite3.connect(source) as sql:
        def insert(table,**values):
            values.update(created_at=stamp,updated_at=stamp)
            sql.execute('INSERT INTO "'+table+'" ('+','.join('"'+key+'"' for key in values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
        insert('agentRegistry',registry_id='localUpdateCount',int_1=0)
        insert('djmdDevice',ID='device',Name='Synthetic',MasterDBID='master')
        insert('djmdMenuItems',ID='menu',Name='TRACK',rb_local_usn=1)
        insert('djmdPlaylist',ID='root',Name='ROOT',Attribute=1,Seq=0)
        for id_,path in [('1',first),('2',second)]:
            insert('djmdContent',ID=id_,UUID='track-'+id_,Title=path.stem,FolderPath=str(path),Length=1,Rating=0)
        insert('djmdCue',ID='cue',ContentID='1',InMsec=1000)
    sql.close()
    engine.dispose()
    share=source.parent/'share';share.mkdir();(share/'analysis.DAT').write_bytes(b'beatgrid and waveform sentinel')
    c=Catalogue(tmp_path/'app');c.set_setting('rekordbox_path',str(source))
    adapter=Rekordbox(c);adapter.connect()
    return adapter


def test_preview_does_not_change_source_and_preserves_cues(rb):
    before=fingerprint(rb.source)
    p=rb.create_playlist('Test')
    rb.add_tracks(p,['1','2'])
    songs=next(x for x in rb.view()['playlists'] if x['id']==p)['songs']
    rb.move_song(p,songs[1]['id'],1)
    rb.edit_track('1',{'rating':5,'genre':'House','comment':'Preview'})
    assert fingerprint(rb.source)==before
    assert next(t for t in rb.view()['tracks'] if t['id']=='1')['genre']=='House'
    with database(rb.staged) as db:
        assert db.get_cue(ID='cue').InMsec==1000
    rb.verify(rb.staged)


def test_apply_opt_in_backup_identities_and_restore(rb):
    source_sha=file_hash(rb.source)
    assets_sha=file_hash(rb.source.parent/'share'/'analysis.DAT')
    p=rb.create_playlist('Test')
    rb.add_tracks(p,['1','2'])
    rb.edit_track('1',{'rating':4,'comment':'Changed'})
    with pytest.raises(ValueError,match='Enable'):rb.apply()
    assert file_hash(rb.source)==source_sha
    rb.catalogue.set_setting('rekordbox_write_enabled',True)
    rb.apply()
    assert len(rb.backups())==1
    backup=rb.backups()[0]
    with database(rb.source) as db:
        assert db.get_content(ID='1').Rating==204
        assert db.get_cue(ID='cue').InMsec==1000
        assert len(db.get_playlist(ID=p).Songs)==2
    assert file_hash(rb.source.parent/'share'/'analysis.DAT')==assets_sha
    rb.restore(backup)
    assert file_hash(rb.source.parent/'share'/'analysis.DAT')==assets_sha
    with database(rb.source) as db:
        assert db.get_content(ID='1').Rating==0
        assert db.get_playlist(ID=p) is None
        assert db.get_cue(ID='cue').InMsec==1000


def test_conflicts_running_app_and_failed_stage_are_safe(rb,monkeypatch):
    before=fingerprint(rb.staged)
    with pytest.raises(ValueError):rb.add_tracks('missing',['1'])
    assert fingerprint(rb.staged)==before
    rb.create_playlist('Test')
    rb.catalogue.set_setting('rekordbox_write_enabled',True)
    with sqlite3.connect(rb.source) as db:db.execute("UPDATE djmdContent SET Title='Externally changed' WHERE ID='1'")
    db.close()
    with pytest.raises(ValueError,match='changed'):rb.apply()
    monkeypatch.setattr('dj_library.rekordbox.running',lambda:True)
    with pytest.raises(ValueError,match='Close Rekordbox'):rb.backup()


def test_local_sync_reuses_native_track_ids_and_applies_link(rb):
    c=rb.catalogue
    with database(rb.source) as db:path=Path(db.get_content(ID='1').FolderPath)
    c.add_root(path.parent);c.scan()
    playlist=c.create_playlist('Local')
    recording=next(r['recording_id'] for r in c.files() if c.resolve(r)==path)
    c.add_entries(playlist,[recording,recording])
    target=rb.sync_local(playlist)
    staged=next(p for p in rb.view()['playlists'] if p['id']==target)
    assert [s['track_id'] for s in staged['songs']]==['1','1']
    rb.catalogue.set_setting('rekordbox_write_enabled',True)
    rb.apply()
    assert next(p for p in c.playlists() if p['id']==playlist)['rekordbox_id']==target
    ids=[s['id'] for s in next(p for p in rb.view()['playlists'] if p['id']==target)['songs']]
    assert rb.sync_local(playlist)==target
    assert [s['id'] for s in next(p for p in rb.view()['playlists'] if p['id']==target)['songs']]==ids


def test_sync_imports_new_audio_and_detects_changed_local_playlist(rb):
    c=rb.catalogue
    with database(rb.source) as db:music=Path(db.get_content(ID='1').FolderPath).parent
    new=tagged(music/'new.wav',title='New',frames=16000)
    c.add_root(music);c.scan()
    recording=next(r['recording_id'] for r in c.files() if c.resolve(r)==new)
    playlist=c.create_playlist('New files');c.add_entries(playlist,[recording])
    target=rb.sync_local(playlist)
    assert len(rb.view()['tracks'])==3
    assert len(next(p for p in rb.view()['playlists'] if p['id']==target)['songs'])==1
    c.add_entries(playlist,[recording])
    c.set_setting('rekordbox_write_enabled',True)
    with pytest.raises(ValueError,match='local playlist changed'):rb.apply()


def test_failed_apply_rolls_back_live_copy(rb,monkeypatch):
    before=file_hash(rb.source)
    rb.create_playlist('Preview');rb.catalogue.set_setting('rekordbox_write_enabled',True)
    original=rb.verify
    def verify(path):
        if path==rb.source:raise ValueError('Simulated post-commit integrity failure')
        original(path)
    monkeypatch.setattr(rb,'verify',verify)
    with pytest.raises(ValueError,match='Simulated'):rb.apply()
    assert file_hash(rb.source)==before
