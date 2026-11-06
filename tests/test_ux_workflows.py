from PySide6.QtCore import Qt,QItemSelectionModel
from PySide6.QtWidgets import QDialog
from test_round6 import app,pump_until
from test_rekordbox import rb
from test_sync_downloads import tagged,remote
from dj_library.ui import Window,ROLE
from dj_library.rekordbox import fingerprint
from test_preview_recovery import native_edit


def test_apply_refreshes_before_review_and_cancel_keeps_edits(rb,monkeypatch):
    app();rb.create_playlist('My pending set');native_edit(rb)
    w=Window(rb.catalogue.workspace);w.catalogue.set_setting('rekordbox_write_enabled',True)
    errors=[];monkeypatch.setattr(w,'show_error',errors.append)
    reviews=[]
    def cancel(dialog):
        reviews.append(dialog.windowTitle())
        assert w.rb_panel.backend.state['fingerprint']==fingerprint(rb.source)
        assert next(t for t in w.rb_panel.data['tracks'] if t['id']=='2')['title']=='External edit'
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',cancel)
    source=fingerprint(rb.source);w.rb_panel.apply()
    pump_until(lambda:len(reviews)==1 and w.job is None)
    assert not errors and fingerprint(rb.source)==source
    assert w.rb_panel.backend.state['changes']
    monkeypatch.setattr(QDialog,'exec',lambda dialog:QDialog.DialogCode.Accepted)
    w.rb_panel.apply();pump_until(lambda:w.job is None and not w.rb_panel.confirm_pending and not w.rb_panel.backend.state['changes'],10)
    assert not errors
    assert next(p for p in w.rb_panel.data['playlists'] if p['name']=='My pending set')
    w.close()


def test_table_selection_stays_on_identity_and_unchanged_refresh_reuses_items(tmp_path):
    app();w=Window(tmp_path/'db');c=w.catalogue
    for i in range(5):tagged(tmp_path/'music'/f'{i}.wav',title=f'Track {i}')
    c.add_root(tmp_path/'music');c.scan();playlist=c.create_playlist('Set')
    c.add_entries(playlist,[f['recording_id'] for f in c.files()]);w.playlist_id=playlist;w.refresh()
    table=w.entries_table;table.selectRow(2);selected=table.item(2,0).data(ROLE)['id'];item=table.item(2,0)
    w.refresh_entries();assert table.item(2,0) is item
    ids=[e['id'] for e in c.entries(playlist)];c.reorder(playlist,[ids[2]]+ids[:2]+ids[3:]);w.refresh_entries()
    assert table.item(table.selectionModel().selectedRows()[0].row(),0).data(ROLE)['id']==selected
    assert table.currentRow()==0 and not w.up_button.isEnabled()
    table.selectionModel().select(table.model().index(1,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
    w.remove_entry();assert len(c.entries(playlist))==3
    w.close()


def test_playlist_search_keeps_matching_ancestors_and_saved_expansion(tmp_path):
    app();w=Window(tmp_path/'db');c=w.catalogue
    folder=c.create_playlist_folder('Sets');a=c.create_playlist('Friday warmup');b=c.create_playlist('Sunday')
    c.move_playlist_item(a,folder);c.move_playlist_item(b,folder);c.set_setting('playlist_expanded',[])
    w.refresh_playlists();w.playlist_name_search.setText('friday')
    parent=w.playlist_list.topLevelItem(0)
    assert parent.isExpanded() and not parent.isHidden()
    assert len([i for i in w.playlist_list.playlist_items() if not i.isHidden()])==1
    assert c.get_setting('playlist_expanded',[])==[]
    w.refresh_playlists();assert w.playlist_name_search.text()=='friday'
    w.playlist_name_search.clear();assert all(not i.isHidden() for i in w.playlist_list.playlist_items())
    w.close()


def test_queue_toolbar_stays_in_place_when_selection_changes(tmp_path):
    app();w=Window(tmp_path/'db');w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);w.show()
    w.navigation.setCurrentRow(1);w.download_tabs.setCurrentIndex(1)
    for i in range(2):
        track=remote(chr(65+i)*22);w.catalogue.save_remote(track);w.catalogue.enqueue(track['id'])
    w.refresh_downloads();app().processEvents()
    before={key:button.geometry() for key,button in w.download_buttons.items() if button.isVisible()}
    w.downloads_table.selectRow(0);app().processEvents()
    assert before=={key:button.geometry() for key,button in w.download_buttons.items() if button.isVisible()}
    assert w.download_buttons['Remove selected'].isEnabled()
    w.downloads_table.clearSelection();assert not w.download_buttons['Remove selected'].isEnabled()
    w.close()


def test_preview_undo_restores_sync_state_and_survives_restart(rb):
    folder=rb.create_playlist('Sets',folder=True);rb.create_playlist('Warmup',folder)
    rb.undo();assert [p['name'] for p in rb.view()['playlists']]==['Sets']
    from dj_library.rekordbox import Rekordbox
    rb=Rekordbox(rb.catalogue);rb.undo()
    assert not rb.view()['playlists'] and not rb.state['changes'] and not rb.state['recipes']


def test_streaming_progress_does_not_rebuild_library_trees(tmp_path,monkeypatch):
    app();w=Window(tmp_path/'db');calls=[]
    w.library_refresh_needed=False
    monkeypatch.setattr(w,'refresh_files',lambda:calls.append('files'))
    monkeypatch.setattr(w,'refresh_playlists',lambda:calls.append('playlists'))
    w.handle_integration_event('refresh','progress');w.download_refresh.stop();w.refresh_live()
    assert not calls
    w.handle_integration_event('refresh','');w.download_refresh.stop();w.refresh_live()
    assert calls==['files','playlists']
    w.close()


def test_rekordbox_duplicate_occurrence_selection_and_first_link_continuation(rb,monkeypatch):
    app();target=rb.create_playlist('Duplicates');rb.add_tracks(target,['1','1'])
    w=Window(rb.catalogue.workspace);panel=w.rb_panel
    panel.tree.setCurrentItem(panel.tree.topLevelItem(1));panel.table.selectRow(1)
    selected=panel.table.item(1,0).data(ROLE)['song_id']
    panel.backend.rename_playlist(target,'Still duplicates');panel.loaded(panel.backend.view())
    assert len(panel.table.selectionModel().selectedRows())==1
    assert panel.table.item(panel.table.currentRow(),0).data(ROLE)['song_id']==selected
    local=w.catalogue.create_playlist('Link me');w.playlist_id=local;w.refresh_playlists();panel.preview_loaded=False
    dialogs=[]
    def reject(dialog):dialogs.append(dialog.windowTitle());return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',reject)
    panel.sync_local();pump_until(lambda:bool(dialogs) and w.job is None)
    assert dialogs==['Link playlist with Rekordbox']
    w.close()
