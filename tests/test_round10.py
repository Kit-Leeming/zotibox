import threading,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt,QPoint,QItemSelectionModel
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu,QMessageBox,QDialog,QListWidget,QComboBox
from dj_library.catalogue import Catalogue
from dj_library.downloads import DownloadEngine,worker_config,download_destination,validated_options
from dj_library.ui import Window,ROLE
from test_round6 import app,pump_until
from test_sync_downloads import tagged,remote
from test_round8 import linked


def queue(c,count=4):
    ids=[]
    for i in range(count):
        t=remote(chr(65+i)*22,'Track '+chr(65+i));t['isrc']='';c.save_remote(t);ids.append(c.enqueue(t['id']))
    return ids


def select(table,rows):
    table.clearSelection()
    for row in rows:table.selectionModel().select(table.model().index(row,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)


def test_queue_order_persists_and_claim_respects_priority_without_moving_active(tmp_path):
    c=Catalogue(tmp_path/'db');ids=queue(c);active=c.claim_next_download();assert active['id']==ids[0]
    c.move_downloads([ids[3]],ids[1]);c=Catalogue(c.workspace)
    assert [j['id'] for j in c.download_jobs()]==[ids[0],ids[3],ids[1],ids[2]]
    assert c.claim_next_download() is None
    with pytest.raises(ValueError):c.move_downloads([ids[0]],ids[3])
    c.update_download(ids[0],state='complete');assert c.claim_next_download()['id']==ids[3]


def test_upgrade_retains_old_queue_order_and_recovers_playlist_sources(tmp_path):
    c=Catalogue(tmp_path/'db');t=remote();p=linked(c,t);c.decide_match(t['id'],None,'missing');job=c.enqueue(t['id'])
    with c.connect() as db:
        db.execute('DELETE FROM download_sources');db.execute('ALTER TABLE downloads DROP COLUMN queue_position');db.execute('ALTER TABLE downloads DROP COLUMN finished_at')
    restored=Catalogue(c.workspace);row=restored.download_jobs()[0]
    assert row['id']==job and row['sources'][0]['name']=='Set' and row['sources'][0]['id']=='spotify:playlist:'+'p'*22


def test_remove_active_prevents_resurrection_and_pending_deletion_keeps_files(tmp_path):
    c=Catalogue(tmp_path/'db');ids=queue(c);sentinel=tmp_path/'keep.wav';sentinel.write_bytes(b'original music')
    c.update_download(ids[3],state='complete',output=str(sentinel));active=c.claim_next_download()
    assert c.remove_downloads(ids)==[ids[0]]
    c.update_download(ids[0],state='complete',progress=100);assert c.download_jobs()[0]['state']=='removing'
    assert c.claim_next_download() is None
    c.finish_queue_removals();assert c.download_jobs()==[] and sentinel.read_bytes()==b'original music'


def test_engine_reordering_changes_next_track_and_removal_stops_owned_job(tmp_path,monkeypatch):
    c=Catalogue(tmp_path/'db');ids=queue(c,3);engine=DownloadEngine(c);calls=[]
    def invoke(stage,track_id=None,**kwargs):
        calls.append(track_id)
        if len(calls)==1:c.move_downloads([ids[2]],ids[1])
        t=c.remote_track(track_id);tagged(stage/'music'/'output.wav',title=t['title'],isrc='');return 'OGG_VORBIS_320'
    monkeypatch.setattr(engine,'invoke',invoke);engine.run_queue();assert calls==['A'*22,'C'*22,'B'*22]
    # A separate disposable worker is stopped while in its mocked stream wait.
    c2=Catalogue(tmp_path/'stop-db');ids=queue(c2,2);engine=DownloadEngine(c2);started=threading.Event()
    def wait(*args,**kwargs):started.set();engine.cancel.wait(3);raise InterruptedError('Stopped')
    monkeypatch.setattr(engine,'invoke',wait);thread=threading.Thread(target=engine.run_queue);thread.start();assert started.wait(3)
    assert c2.remove_downloads(ids)==[ids[0]];engine.stop();thread.join(5)
    assert not thread.is_alive() and c2.download_jobs()==[]


def test_playlist_priority_keeps_shared_tracks_unique(tmp_path):
    c=Catalogue(tmp_path/'db');ids=queue(c,4)
    with c.connect() as db:
        for job,key,name in [(ids[0],'set:a','A'),(ids[1],'set:b','B'),(ids[2],'set:a','A'),(ids[2],'set:b','B')]:
            db.execute('INSERT INTO download_sources VALUES (?,?,?,0)',(job,key,name))
    c.prioritise_sources(['set:b','set:a','direct'])
    assert [j['id'] for j in c.download_jobs()]==[ids[1],ids[2],ids[0],ids[3]]
    assert len(c.download_jobs())==4 and len(c.download_jobs()[1]['sources'])==2


def test_retry_reuses_failed_entry_and_keeps_its_priority(tmp_path):
    c=Catalogue(tmp_path/'db');ids=queue(c,2);c.move_downloads([ids[1]],ids[0]);c.update_download(ids[1],state='failed',progress=30)
    assert c.enqueue('B'*22)==ids[1] and c.download_jobs()[0]['id']==ids[1]
    assert len(c.download_jobs())==2 and c.download_jobs()[0]['state']=='queued'


def test_ui_multi_selection_survives_progress_and_context_removes_all_selected(tmp_path,monkeypatch):
    app();w=Window(tmp_path/'db');ids=queue(w.catalogue);w.refresh_downloads();w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);w.show();w.navigation.setCurrentRow(1);w.download_tabs.setCurrentIndex(1)
    app().processEvents();select(w.downloads_table,[0,2]);w.catalogue.update_download(ids[0],progress=53);w.refresh_downloads();app().processEvents()
    assert {j['id'] for j in w.selected_downloads()}=={ids[0],ids[2]}
    class Menu(QMenu):
        def exec(self,*args):next(a for a in self.actions() if a.text().startswith('Remove selected')).trigger()
    monkeypatch.setattr('dj_library.ui.QMenu',Menu);point=w.downloads_table.visualItemRect(w.downloads_table.item(2,0)).center();w.downloads_context(point)
    assert {j['id'] for j in w.catalogue.download_jobs()}=={ids[1],ids[3]};w.close()


def test_ui_clear_queue_keeps_music_and_drag_reorders_without_cross_database_drops(tmp_path,monkeypatch):
    app();w=Window(tmp_path/'db');ids=queue(w.catalogue);sentinel=tmp_path/'music.wav';sentinel.write_bytes(b'keep');w.catalogue.update_download(ids[1],state='complete',output=str(sentinel));w.refresh_downloads()
    payload={'kind':'queue','workspace':str(w.catalogue.workspace),'jobs':[ids[3]]}
    assert w.drop_queue(payload,QPoint(0,0));assert w.catalogue.download_jobs()[0]['id']==ids[3]
    assert not w.drop_queue(dict(payload,workspace='elsewhere'),QPoint(0,0))
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes);w.clear_download_queue()
    assert w.catalogue.download_jobs()==[] and w.downloads_table.rowCount()==0 and sentinel.read_bytes()==b'keep';w.close()


def test_removing_active_entry_resumes_remaining_queue(tmp_path,monkeypatch):
    app();w=Window(tmp_path/'db');ids=queue(w.catalogue,2);started=threading.Event()
    def invoke(stage,track_id=None,**kwargs):
        if track_id=='A'*22:started.set();w.engine.cancel.wait(4);raise InterruptedError('Removed')
        t=w.catalogue.remote_track(track_id);tagged(stage/'music'/'output.wav',title=t['title'],isrc='');return 'OGG_VORBIS_320'
    monkeypatch.setattr(w.engine,'invoke',invoke);w.start_downloads();pump_until(started.is_set)
    w.remove_download_ids([ids[0]]);pump_until(lambda:w.download_job is None and len(w.catalogue.download_jobs())==1 and w.catalogue.download_jobs()[0]['state']=='complete',8)
    assert w.catalogue.download_jobs()[0]['id']==ids[1];w.close()


def test_removal_does_not_resume_a_queue_the_user_paused(tmp_path,monkeypatch):
    app();w=Window(tmp_path/'db');ids=queue(w.catalogue,2);started=threading.Event();calls=[]
    def invoke(*args,**kwargs):calls.append(True);started.set();w.engine.cancel.wait(3);raise InterruptedError('Paused')
    monkeypatch.setattr(w.engine,'invoke',invoke);w.start_downloads();pump_until(started.is_set)
    w.pause_downloads();w.remove_download_ids([ids[0]]);pump_until(lambda:w.download_job is None)
    assert len(calls)==1 and w.catalogue.download_jobs()[0]['id']==ids[1] and w.catalogue.download_jobs()[0]['state']=='queued';w.close()


def test_playlist_priority_dialog_reorders_pending_groups(tmp_path,monkeypatch):
    app();w=Window(tmp_path/'db');ids=queue(w.catalogue,2)
    with w.catalogue.connect() as db:
        db.execute('INSERT INTO download_sources VALUES (?,?,?,0)',(ids[0],'set:a','First set'))
        db.execute('INSERT INTO download_sources VALUES (?,?,?,0)',(ids[1],'set:b','Second set'))
    class Dialog(QDialog):
        def exec(self):
            listing=self.findChild(QListWidget);assert listing.count()==2
            listing.insertItem(0,listing.takeItem(1));return QDialog.DialogCode.Accepted
    monkeypatch.setattr('dj_library.ui.QDialog',Dialog);w.queue_playlist_priority()
    assert [j['id'] for j in w.catalogue.download_jobs()]==list(reversed(ids));w.close()


def test_progress_selection_spans_bar_cell_and_text_is_separate(tmp_path):
    app();w=Window(tmp_path/'db');ids=queue(w.catalogue,1);w.catalogue.update_download(ids[0],progress=50);w.refresh_downloads();w.resize(1600,900)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);w.show();w.navigation.setCurrentRow(1);w.download_tabs.setCurrentIndex(1);w.downloads_table.selectRow(0);app().processEvents()
    assert w.downloads_table.cellWidget(0,5) is None and w.downloads_table.item(0,5).text()==''
    assert not w.queue_progress.isTextVisible() and '%' in w.queue_percent.text()
    rect=w.downloads_table.visualItemRect(w.downloads_table.item(0,5));pixmap=w.downloads_table.viewport().grab();ratio=pixmap.devicePixelRatio();image=pixmap.toImage()
    assert image.pixelColor(int((rect.left()+12)*ratio),int((rect.top()+4)*ratio)).name()==w.theme['selection']['start']
    w.close()


def test_settings_page_exposes_user_preferences_and_keeps_staging_valid(tmp_path):
    app();w=Window(tmp_path/'db');w.zotify_settings();panel=w.zotify_panel
    assert w.settings_navigation.currentItem().text()=='Zotify' and panel.editors['DOWNLOAD_RATE_LIMITER'].isEnabled()
    assert panel.editors['OUTPUT_SINGLE'].isEnabled() and panel.editors['CUSTOM_FFMEPG_ARGS'].isEnabled()
    assert not panel.editors['CREDENTIALS_LOCATION'].isEnabled()
    panel.editors['DOWNLOAD_RATE_LIMITER'].setText('0');panel.editors['REDIRECT_PORT'].setText('4390');panel.editors['OUTPUT_SINGLE'].setText('{artist}/{song_name}')
    panel.editors['CUSTOM_FFMEPG_ARGS'].setText('-ar 48000');panel.save();options=w.catalogue.get_setting('zotify_options',{})
    assert options['DOWNLOAD_RATE_LIMITER']==0 and options['OUTPUT_SINGLE']=='{artist}/{song_name}'
    config=worker_config(w.catalogue,tmp_path/'stage');assert config['REDIRECT_PORT']==4390 and config['CUSTOM_FFMEPG_ARGS']=='-ar 48000' and config['OUTPUT']=='{song_id}'
    panel.reset();assert panel.editors['DOWNLOAD_RATE_LIMITER'].text()=='1.0';w.close()


def test_custom_filenames_are_relative_and_never_overwrite(tmp_path):
    music=tmp_path/'music';audio=tmp_path/'input.wav';audio.write_bytes(b'new');t=remote();options={'OUTPUT_SINGLE':'{artist}/{song_name}'}
    target=download_destination(music,t,audio,options);assert target==music/'Artist'/'Track.wav'
    target.write_bytes(b'old');assert download_destination(music,t,audio,options).name=='Track (2).wav';assert target.read_bytes()==b'old'
    for value in ['../{song_name}','C:/{song_name}','{title.__class__}']:
        with pytest.raises(ValueError):validated_options({'OUTPUT_SINGLE':value})
    with pytest.raises(ValueError):validated_options({'REDIRECT_PORT':70000})
