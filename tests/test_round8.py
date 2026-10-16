import pytest
from PySide6.QtCore import Qt,QPoint
from dj_library.catalogue import Catalogue
from dj_library.match_review import MatchReview
from dj_library.matching import match_track
from dj_library.ui import Window,ROLE
from test_round6 import app,pump_until
from test_rekordbox import rb
from test_sync_downloads import tagged


def track(id_='a'):
    return {'id':id_*22,'title':'Song','artist':'Artist','duration':1,'playable':True}

def linked(c,t,id_='p'):
    return c.sync_playlist({'id':id_*22,'name':'Set','snapshot':'one','complete':True,'tracks':[t,t]},
                           {t['id']:{'recording_id':None,'state':'review'}})['playlist_id']


def test_missing_decision_survives_resync_and_download_overrides_it(tmp_path):
    c=Catalogue(tmp_path/'db');t=track();p=linked(c,t);linked(c,t,'q')
    tagged(tmp_path/'music'/'song.wav',title='Song',artist='Artist');c.add_root(tmp_path/'music');c.scan()
    assert match_track(c,t)['state']=='matched'
    c.decide_match(t['id'],None,'missing')
    assert match_track(c,t)=={'recording_id':None,'state':'missing'}
    assert all(e['match_state']=='missing' for pl in c.playlists() for e in c.entries(pl['id']))
    c.sync_playlist({'id':'p'*22,'name':'Set','snapshot':'two','complete':True,'tracks':[t]}, {t['id']:match_track(c,t)})
    assert c.entries(p)[0]['match_state']=='missing';c.enqueue(t['id']);assert len(c.download_jobs())==1
    c.decide_match(t['id'],c.files()[0]['recording_id'],'download')
    assert match_track(c,t)['state']=='matched'


def test_review_deduplicates_steps_skips_and_rejects_without_candidates(tmp_path):
    app();c=Catalogue(tmp_path/'db');a,b=track(),track('b');linked(c,a);linked(c,b,'q')
    dialog=MatchReview(c,[a,a,b]);assert len(dialog.tracks)==2 and not dialog.accept_button.isEnabled()
    dialog.step(1);dialog.step(-1);assert dialog.position==0
    dialog.mark_missing();assert dialog.position==1
    assert all(e['match_state']=='missing' for p in c.playlists() for e in c.entries(p['id']) if e['spotify_id']==a['id'])
    dialog.mark_missing();assert dialog.decisions==2 and match_track(c,b)['state']=='missing'
    dialog.close()


def test_review_confirm_and_empty_dialog(tmp_path):
    app();c=Catalogue(tmp_path/'db');t=track();linked(c,t)
    tagged(tmp_path/'music'/'song.wav',title='Song',artist='Artist');c.add_root(tmp_path/'music');c.scan()
    dialog=MatchReview(c,[t]);dialog.list.setCurrentRow(0);dialog.confirm()
    assert match_track(c,t)['recording_id']==c.files()[0]['recording_id']
    empty=MatchReview(c,[]);assert not empty.missing.isEnabled();empty.close();dialog.close()


def test_local_folder_moves_persist_and_reject_cycles_without_deleting_playlists(tmp_path):
    c=Catalogue(tmp_path/'db');p=c.create_playlist('Set');a=c.create_playlist_folder('A');b=c.create_playlist_folder('B',a)
    c.move_playlist_item(p,b);assert Catalogue(c.workspace).playlist_locations()[p]==b
    with pytest.raises(ValueError,match='descendants'):c.move_playlist_item(a,b,True)
    with pytest.raises(ValueError):c.move_playlist_item(p,'unknown')
    c.rename_playlist_folder(a,'Renamed');c.remove_playlist_folder(a)
    assert c.playlist_folders()[0]['parent_id'] is None and c.playlist_locations()[p]==b
    c.remove_playlist_folder(b);assert c.playlist_locations()=={} and len(c.playlists())==1


def test_transfer_retains_folder_hierarchy_and_review_decisions(tmp_path):
    from dj_library.transfer import export_workspace,import_workspace
    c=Catalogue(tmp_path/'db');t=track();p=linked(c,t);a=c.create_playlist_folder('A');b=c.create_playlist_folder('B',a)
    c.move_playlist_item(p,b);c.decide_match(t['id'],None,'missing')
    export_workspace(c,tmp_path/'transfer.zotibox');import_workspace(tmp_path/'transfer.zotibox',tmp_path/'restored')
    restored=Catalogue(tmp_path/'restored');assert restored.playlist_locations()[p]==b
    assert {f['id']:f['parent_id'] for f in restored.playlist_folders()}[b]==a
    assert match_track(restored,t)['state']=='missing'


def test_local_tree_playlist_drop_and_queue_reviewed_missing(tmp_path,monkeypatch):
    app();window=Window(tmp_path/'db');c=window.catalogue;t=track();p=linked(c,t)
    folder=c.create_playlist_folder('Folder');window.playlist_id=p;window.refresh_playlists()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show();window.navigation.setCurrentRow(3)
    tree=window.playlist_list;item=tree.topLevelItem(0)
    payload={'kind':'playlist','scope':'local','workspace':str(c.workspace),'id':p,'folder':False}
    assert window.drop_local(payload,tree.visualItemRect(item).center(),True)
    assert c.playlist_locations()[p]==folder and c.get_setting('playlist_expanded',[])==[folder]
    errors=[];monkeypatch.setattr(window,'show_error',lambda exc:errors.append(str(exc)))
    assert not window.drop_local(payload,QPoint(0,9999),False) and errors
    with pytest.raises(ValueError):window.validate_drag(payload)
    window.mark_entry_missing(c.entries(p)[0]);window.playlist_id=p;window.queue_missing()
    assert len(c.download_jobs())==1 # duplicate Spotify occurrences queue once
    window.close()


def test_native_playlist_moves_preserve_entries_cues_and_source(rb):
    before=rb.source.read_bytes();a=rb.create_playlist('A',folder=True);b=rb.create_playlist('B',a,True)
    p=rb.create_playlist('Set');rb.add_tracks(p,['1','2']);rb.move_playlist(p,b)
    view=rb.view();moved=next(pl for pl in view['playlists'] if pl['id']==p)
    assert moved['parent']==b and [s['track_id'] for s in moved['songs']]==['1','2']
    with pytest.raises(ValueError,match='descendants'):rb.move_playlist(a,b)
    rb.move_playlist(p);assert next(pl for pl in rb.view()['playlists'] if pl['id']==p)['parent']=='root'
    assert rb.source.read_bytes()==before and next(t for t in view['tracks'] if t['id']=='1')['cues'][0]['position']==1


def test_native_tree_drop_stages_move_and_rejects_cross_scope(rb):
    app();a=rb.create_playlist('Folder',folder=True);p=rb.create_playlist('Set')
    window=Window(rb.catalogue.workspace);window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show()
    panel=window.rb_panel;panel.backend=rb;panel.loaded(rb.view());window.navigation.setCurrentRow(3);window.playlist_tabs.setCurrentIndex(1)
    tree=panel.tree;folder=next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).data(0,ROLE)==a)
    payload={'kind':'playlist','scope':'rekordbox','workspace':str(rb.catalogue.workspace),'id':p}
    assert not panel.drop_tracks(dict(payload,scope='local'),tree.visualItemRect(folder).center(),True)
    assert panel.drop_tracks(payload,tree.visualItemRect(folder).center(),True)
    pump_until(lambda:window.job is None)
    assert next(pl for pl in panel.data['playlists'] if pl['id']==p)['parent']==a
    window.close()
