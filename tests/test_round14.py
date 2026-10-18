import json,shutil,threading,subprocess,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from mutagen.wave import WAVE
from mutagen.id3 import TCON,APIC
from dj_library.catalogue import Catalogue,file_hash
from dj_library.lossless import wav_to_flac,pcm_hash
from dj_library.audio_tags import picture
from dj_library.artwork_repair import repair,embed_verified
from dj_library.all_tracks import tracks
from dj_library.organisation import Organiser
from dj_library.zotify_worker import install_genre_policy
from dj_library.ui import Window
from dj_library.rekordbox import database
from dj_library.downloads import ffmpeg_path
from test_rekordbox import rb
from test_sync_downloads import tagged,remote
from test_round13 import png
from test_round6 import app


def test_backup_retention_default_four_and_configurable(rb):
 paths=[rb.backup() for _ in range(6)]
 assert len(rb.backups())==4 and not paths[0].exists() and paths[-1].exists()
 unrelated=rb.root/'backups'/'User archive.zip'
 with zipfile.ZipFile(unrelated,'w') as z:z.writestr('readme.txt','Not a managed ZotiBox backup')
 rb.catalogue.set_setting('rekordbox_backup_limit',1);newest=rb.backup()
 assert newest.exists() and unrelated.exists() and all(not p.exists() for p in paths)
 rb.catalogue.set_setting('rekordbox_backup_limit',0);rb.backup();rb.backup();assert len(rb.backups())==4


def test_restore_with_retention_one_preserves_verified_recovery(rb):
 rb.catalogue.set_setting('rekordbox_backup_limit',1);rb.catalogue.set_setting('rekordbox_write_enabled',True)
 old=rb.backup();rb.edit_track('1',{'rating':4});rb.apply();rb.restore(rb.backups()[0])
 assert len(rb.backups())==1 and rb.backups()[0].exists()


def test_wav_to_flac_preserves_pcm_tags_cover_and_reuses_output(tmp_path):
 source=tagged(tmp_path/'Music'/'Track.wav',title='Track',artist='Artist');tags=WAVE(source);tags.tags.add(TCON(encoding=3,text=['House']));tags.tags.add(APIC(encoding=3,mime='image/png',type=3,data=png()));tags.save()
 before=file_hash(source);converted=wav_to_flac(source)
 from dj_library.catalogue import read_metadata
 metadata=read_metadata(converted)
 assert pcm_hash(source)==pcm_hash(converted) and file_hash(source)==before
 assert metadata['title']=='Track' and metadata['artist']=='Artist' and metadata['genre']=='House' and picture(converted)[0]==png()
 assert wav_to_flac(source)==converted and len(list(source.parent.glob('*.flac')))==1


def test_float_wav_is_not_silently_reduced_to_integer_flac(tmp_path):
 source=tagged(tmp_path/'source.wav');floating=tmp_path/'Float.wav'
 subprocess.run([str(ffmpeg_path('ffmpeg')),'-v','error','-i',str(source),'-c:a','pcm_f32le',str(floating)],check=True,creationflags=0x08000000)
 with pytest.raises(ValueError,match='precision'):wav_to_flac(floating)
 assert floating.exists() and not floating.with_suffix('.flac').exists()


def test_wav_organisation_then_artwork_can_apply_and_cleanup(rb,tmp_path):
 c=rb.catalogue;c.add_root(tmp_path/'music');c.scan();service=Organiser(c,rb);plan=service.plan('flac');service.execute(plan)
 converted=Path(plan['rows'][0]['target']);row=next(r for r in c.files() if c.resolve(r)==converted)
 assert embed_verified(c,row,png(),'image/png',rekordbox=rb)
 saved=json.loads((service.journals/(plan['id']+'.json')).read_text(encoding='utf-8'))
 assert saved['rows'][0]['target_sha256']==file_hash(converted)
 c.set_setting('rekordbox_write_enabled',True);rb.apply();service.cleanup(saved,lambda p:p.unlink())
 assert converted.exists() and not any(c.resolve(f).suffix=='.wav' for f in c.files())
 with database(rb.source) as db:assert db.get_cue(ID='cue').ContentID=='1' and db.get_content(ID='1').FileType==5


def test_organiser_is_non_modal_and_has_its_own_artwork_selection(tmp_path):
 app();w=Window(tmp_path/'DB');source=tagged(tmp_path/'Music'/'Track.wav');converted=wav_to_flac(source);w.catalogue.add_root(source.parent);w.catalogue.scan();w.refresh()
 w.organise_library(3);dialog=w.organise_window
 assert not dialog.isModal() and dialog.art_files.topLevelItemCount()==1
 dialog.check_artwork(True);assert dialog.art_files.topLevelItem(0).checkState(0)==Qt.CheckState.Checked
 w.navigation.setCurrentRow(2);app().processEvents();assert w.pages.currentIndex()==2
 w.organise_library(0);assert w.organise_window is dialog
 dialog.close();w.close()


def test_genre_backfill_keeps_identity_and_syncs_without_overwriting_native_genre(rb,tmp_path):
 source=tmp_path/'music'/'one.wav';tag=WAVE(source);tag.tags.add(TCON(encoding=3,text=['Tech House']));tag.save()
 c=rb.catalogue;c.add_root(source.parent);c.scan();row=next(r for r in c.files() if c.resolve(r)==source)
 with c.connect() as db:db.execute('ALTER TABLE files DROP COLUMN genre')
 c=Catalogue(c.workspace);c.scan();new=next(r for r in c.files() if r['id']==row['id']);assert new['genre']=='Tech House' and new['recording_id']==row['recording_id']
 rb.catalogue=c;playlist=c.create_playlist('Set');c.add_entries(playlist,[row['recording_id']]);rb.sync_local(playlist)
 assert next(t for t in rb.view()['tracks'] if t['id']=='1')['genre']=='Tech House'
 rb.edit_track('1',{'genre':'My custom genre'});rb.sync_local(playlist)
 assert next(t for t in rb.view()['tracks'] if t['id']=='1')['genre']=='My custom genre'


def test_worker_recovers_genres_from_cached_artist_metadata():
 seen=[]
 class Track:
  genres=None;artists=[SimpleNamespace(genres=['House','Bass House'])]
  def write_audio_tags(self,path):seen.append(self.genres)
 zotify=SimpleNamespace(CONFIG=SimpleNamespace(get_save_genres=lambda:True))
 install_genre_policy(Track,zotify,lambda *a,**k:None);Track().write_audio_tags('unused')
 assert seen==[['Bass House','House']]


def test_artwork_service_503_is_retried_and_deferred_without_request_storm(rb,tmp_path):
 source=tagged(tmp_path/'Source.wav');rows=[];c=rb.catalogue
 for name in ['First','Second']:
  path=tmp_path/'Music'/(name+'.mp3');path.parent.mkdir(exist_ok=True)
  subprocess.run([str(ffmpeg_path('ffmpeg')),'-v','error','-i',str(source),'-c:a','libmp3lame',str(path)],check=True,creationflags=0x08000000)
 c.add_root(tmp_path/'Music');c.scan();rows=c.files();calls=[]
 class Cancel:
  def is_set(self):return False
  def wait(self,*a):return False
 class Response:
  status_code=503
  def close(self):pass
 class Session:
  def get(self,*a,**k):calls.append(1);return Response()
 result=repair(c,rows,cancel=Cancel(),session=Session())
 assert result['deferred']==2 and result['not_found']==0 and len(calls)==3
 assert set(c.get_setting('artwork_retry_files',[]))=={r['id'] for r in rows if r['relative_path'].endswith('.mp3')}
 pending=c.get_setting('artwork_retry_files',[]);repair(c,[],cancel=Cancel(),session=Session());assert c.get_setting('artwork_retry_files',[])==pending


def test_all_tracks_membership_includes_app_and_native_and_dates(rb,tmp_path):
 c=rb.catalogue;c.add_root(tmp_path/'music');c.scan();rows=c.files();one=next(r for r in rows if c.resolve(r).name=='one.wav');two=next(r for r in rows if c.resolve(r).name=='two.wav')
 playlist=c.create_playlist('Local');c.add_entries(playlist,[one['recording_id']]);native=rb.create_playlist('Native');rb.add_tracks(native,['2'])
 items=tracks(c,rb.view());assert all(r['playlists'] for r in items)
 c.delete_playlist(playlist);items=tracks(c,rb.view());assert next(r for r in items if r['id']==one['id'])['playlists']==[]
 track=remote();c.save_remote(track);c.decide_match(track['id'],one['recording_id']);job=c.enqueue(track['id']);c.update_download(job,state='complete',output=str(c.resolve(one)))
 with c.connect() as db:db.execute("UPDATE downloads SET finished_at='2026-10-18T12:00:00+00:00' WHERE id=?",(job,))
 expected=__import__('datetime').datetime.fromisoformat('2026-10-18T12:00:00+00:00').timestamp()
 assert next(r for r in tracks(c,rb.view()) if r['id']==one['id'])['added_time']==expected


def test_all_tracks_filter_and_date_sort_preserved(tmp_path):
 app();w=Window(tmp_path/'DB');source=tagged(tmp_path/'Music'/'First.wav');tagged(source.with_name('Second.wav'),title='Second');w.catalogue.add_root(source.parent);w.catalogue.scan();w.refresh()
 row=w.catalogue.files()[0];id_=w.catalogue.create_playlist('Set');w.catalogue.add_entries(id_,[row['recording_id']]);w.all_tracks.refresh()
 view=w.all_tracks;view.unassigned.setChecked(True);assert view.table.rowCount()==1
 view.table.sortItems(4,Qt.SortOrder.DescendingOrder);view.refresh();assert view.table.horizontalHeader().sortIndicatorSection()==4
 assert view.table.horizontalHeader().sortIndicatorOrder()==Qt.SortOrder.DescendingOrder
 w.close()
