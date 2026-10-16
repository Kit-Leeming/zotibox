from datetime import datetime,timedelta
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QMessageBox,QDialog,QListWidget,QDialogButtonBox
from dj_library.catalogue import Catalogue
from dj_library.matching import candidates,match_track,reclassify_reviews
from dj_library.ui import Window,clock_duration
from dj_library.zotify_login import install_login_adapter,failure_message
from dj_library.zotify_oauth import LoopbackOAuth,LoginError
from test_round6 import app,pump_until
from test_round8 import track,linked
from test_sync_downloads import tagged
from test_rekordbox import rb


@pytest.mark.parametrize('saved',[{'client_id':'client','access_token':None,'refresh_token':None},
    {'client_id':'client','access_token':'expired','refresh_token':'revoked'}])
def test_failed_saved_oauth_opens_browser_and_never_becomes_logged_in(saved):
    flows=[];created=[]
    class OAuth:
        valid=False
        def ingest_token_response(self,data):return self
        def token(self):
            if not self.valid:raise LoginError('Expired')
            return 'new'
        def flow(self):flows.append(True);self.valid=True
    def create(client):oauth=OAuth();created.append(oauth);return oauth
    class Handler:
        SESSION=object();OAUTH=None
        @staticmethod
        def get_creds_from_file(path):return saved
    config=SimpleNamespace(permit_client_api=lambda:True,get_api_client_id=lambda:'client',get_api_credentials_location=lambda:'api')
    install_login_adapter(Handler,SimpleNamespace(CONFIG=config),create,lambda *a,**k:None)
    Handler.attempt_login(None)
    assert flows==[True] and Handler.OAUTH.valid and Handler.OAUTH is created[-1]


def test_declined_relogin_does_not_report_connected():
    class OAuth:
        def flow(self):raise LoginError('Declined')
    class Handler:
        SESSION=object();OAUTH=None
        @staticmethod
        def get_creds_from_file(path):return None
    config=SimpleNamespace(permit_client_api=lambda:True,get_api_client_id=lambda:'client',get_api_credentials_location=lambda:'api')
    install_login_adapter(Handler,SimpleNamespace(CONFIG=config),lambda _:OAuth(),lambda *a,**k:None)
    with pytest.raises(LoginError):Handler.attempt_login(None)
    assert Handler.OAUTH is None


def test_refresh_keeps_refresh_token_and_has_bounded_timeout(monkeypatch):
    oauth=LoopbackOAuth('client','http://127.0.0.1:4381/login',lambda *a,**kw:None)
    oauth.ingest_token_response({'access_token':'old','refresh_token':'refresh','expires_at':0})
    calls=[]
    def post(*args,**kwargs):calls.append(kwargs);return SimpleNamespace(status_code=200,json=lambda:{'access_token':'new','expires_in':3600})
    monkeypatch.setattr('dj_library.zotify_oauth.requests.post',post)
    assert oauth.token()=='new' and calls[0]['timeout']==30 and oauth._OAuth__refresh_token=='refresh'
    assert 'sensitive-value' not in failure_message(RuntimeError('sensitive-value'))
    assert 'authorization' in failure_message(RuntimeError('You need to receive a token before!'))


def test_weak_review_becomes_missing_but_confirmed_and_identity_conflicts_survive(tmp_path):
    c=Catalogue(tmp_path/'db');tagged(tmp_path/'music'/'track.wav',title='Night Drive',artist='Elsewhere',isrc='')
    c.add_root(tmp_path/'music');c.scan();t=dict(track(),title='Night Dive',artist='Unknown',duration=90)
    ranked=candidates(c,t);assert ranked and .45<=ranked[0]['score']<.8
    p=linked(c,t);assert match_track(c,t)['state']=='missing';assert reclassify_reviews(c)==1
    assert all(e['match_state']=='missing' for e in c.entries(p))
    c.decide_match(t['id'],c.files()[0]['recording_id']);assert match_track(c,t)['state']=='matched'


def test_global_queue_stays_on_page_hides_after_queuing_and_skips_unavailable(tmp_path):
    app();window=Window(tmp_path/'db');a,b=track(),track('b')
    for i,t in enumerate([a,b]):linked(window.catalogue,t,str(i));window.catalogue.decide_match(t['id'],None,'missing')
    unavailable=dict(track('c'),playable=False);linked(window.catalogue,unavailable,'u');window.catalogue.decide_match(unavailable['id'],None,'missing')
    window.refresh_playlists();window.navigation.setCurrentRow(3);window.playlist_id=None
    assert not window.queue_missing_button.isHidden();window.queue_missing()
    assert window.navigation.currentRow()==3 and len(window.catalogue.download_jobs())==2
    assert window.queue_missing_button.isHidden();window.queue_missing();assert len(window.catalogue.download_jobs())==2
    window.close()


def test_sidebar_remembers_collapse_and_keeps_headings_and_tooltips(tmp_path):
    app();window=Window(tmp_path/'db');window.toggle_sidebar();window.navigation.setCurrentRow(2)
    assert window.heading.text()=='Local Music' and window.navigation.item(2).text()==''
    assert window.navigation.item(2).toolTip()=='Local Music' and window.sidebar.width()==68
    window.close();window=Window(tmp_path/'db');assert window.sidebar.width()==68;window.close()


def test_playlist_url_offer_creates_sync_or_allows_track_browsing(tmp_path,monkeypatch):
    app();window=Window(tmp_path/'db');synced=[];monkeypatch.setattr(window,'sync_ids',lambda ids:synced.extend(ids))
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    assert window.offer_playlist_sync('https://open.spotify.com/playlist/'+'a'*22+'?si=example')
    assert synced==['a'*22] and window.navigation.currentRow()==3
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.No)
    assert not window.offer_playlist_sync('spotify:playlist:'+'a'*22)
    assert not window.offer_playlist_sync('https://open.spotify.com/track/'+'a'*22);window.close()


def test_queue_time_is_hours_minutes_seconds_and_finish_is_local_clock(tmp_path):
    app();window=Window(tmp_path/'db');t=dict(track(),duration=3661);window.catalogue.save_remote(t);window.catalogue.enqueue(t['id'])
    assert clock_duration(3661)=='01:01:01';window.refresh_downloads()
    assert '01:01:01' in window.queue_summary.text() and 'finish' not in window.queue_summary.text()
    window.download_job=object();window.refresh_downloads()
    assert (datetime.now()+timedelta(seconds=3661)).strftime('%H:%M') in window.queue_summary.text()
    window.download_job=None;window.close()


def test_startup_check_is_nonblocking_quiet_and_can_be_disabled(tmp_path,monkeypatch):
    app();window=Window(tmp_path/'db');window.catalogue.set_setting('matching_revision',2)
    calls=[];monkeypatch.setattr('dj_library.ui.github_update',lambda:calls.append(True) or {'version':'99.0.0'})
    window.schedule_startup_checks();pump_until(lambda:window.available_update is not None)
    assert calls==[True] and window.job is None and window.install_update_button.isEnabled()
    window.catalogue.set_setting('check_updates_on_start',False);window.schedule_startup_checks();assert calls==[True]
    window.close()


def test_batch_rekordbox_sync_updates_multiple_playlists_in_preview_only(rb,monkeypatch):
    app();c=rb.catalogue;a=linked(c,track());b=linked(c,track('b'),'q')
    window=Window(c.workspace);panel=window.rb_panel;panel.backend=rb;before=rb.source.read_bytes()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show();window.navigation.setCurrentRow(3);panel.preview_loaded=False
    def accept(dialog):
        listing=dialog.findChild(QListWidget)
        assert listing.count()==2
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',accept);panel.sync_many();pump_until(lambda:window.job is None)
    assert {a,b}<=set(rb.state['links']) and len([p for p in rb.view()['playlists'] if p['type']==0])==2
    assert rb.source.read_bytes()==before;window.close()
