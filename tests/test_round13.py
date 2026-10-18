import json,os,shutil,struct,subprocess,threading
from pathlib import Path
from contextlib import contextmanager
import pytest
from PySide6.QtCore import Qt,QBuffer,QIODevice
from PySide6.QtGui import QImage,QColor
from mutagen import File
from mutagen.oggvorbis import OggVorbis
from mutagen.flac import Picture
from dj_library.catalogue import Catalogue,file_hash
from dj_library.organisation import Organiser
from dj_library.compatibility import compatible_path
from dj_library.audio_tags import picture
from dj_library.artwork_repair import embed_verified,audio_hash
from dj_library.rekordbox import database,fingerprint,Rekordbox
from dj_library.ui import Window,ROLE
from dj_library.organisation_ui import OrganiseDialog,VersionDialog,ExtendedSearch
from test_catalogue import audio
from test_rekordbox import rb
from test_sync_downloads import tagged,remote
from test_round6 import app


def test_manifest_cache_skips_audio_and_per_file_sql_but_finds_edits(tmp_path,monkeypatch):
 c=Catalogue(tmp_path/'Database');folder=tmp_path/'Music'
 for i in range(25):audio(folder/f'{i}.wav',8000+i)
 c.add_root(folder);c.scan();c=Catalogue(c.workspace);calls=[];original=c.connect
 @contextmanager
 def counted():
  calls.append(1)
  with original() as db:yield db
 monkeypatch.setattr(c,'connect',counted)
 monkeypatch.setattr('dj_library.catalogue.file_hash',lambda *a:pytest.fail('Unchanged audio was hashed'))
 monkeypatch.setattr('dj_library.catalogue.read_metadata',lambda *a:pytest.fail('Unchanged tags were parsed'))
 result=c.scan();assert result['unchanged']==25 and len(calls)<8 and c.file_revision==0
 monkeypatch.undo();audio(folder/'0.wav',9000);audio(folder/'new.wav',9100);(folder/'1.wav').unlink()
 result=c.scan();assert result['indexed']==2 and result['missing']==1


def test_scan_refresh_preserves_root_subfolder_sort_selection_and_recent_local(tmp_path):
 app();w=Window(tmp_path/'Database');path=tagged(tmp_path/'Music'/'Nested'/'Local.wav',title='Local');w.catalogue.add_root(path.parents[1]);w.catalogue.scan();w.refresh()
 tree=w.files_table;root=tree.topLevelItem(0);root.setExpanded(True);nested=root.child(0);nested.setExpanded(True);nested.child(0).setSelected(True)
 tree.sortItems(8,Qt.SortOrder.DescendingOrder);root.setExpanded(False);w.refresh_files()
 assert not tree.topLevelItem(0).isExpanded() and tree.topLevelItem(0).child(0).isExpanded()
 assert tree.sortColumn()==8 and tree.header().sortIndicatorOrder()==Qt.SortOrder.DescendingOrder
 assert len(w.selected_files())==1 and w.recent_table.rowCount()==1 and w.download_tabs.currentIndex()==1
 assert w.recent_table.item(0,0).text()=='Local';w.close()
 w=Window(tmp_path/'Database');assert not w.files_table.topLevelItem(0).isExpanded() and w.files_table.sortColumn()==8;w.close()


def test_copy_relinks_rekordbox_id_and_cues_then_cleanup_requires_apply(rb,tmp_path):
 c=rb.catalogue;c.add_root(tmp_path/'music');c.scan();before=fingerprint(rb.source);row=next(r for r in c.files() if c.resolve(r).name=='one.wav')
 service=Organiser(c,rb);plan=service.plan('consolidate',tmp_path/'Destination');service.execute(plan)
 target=next(r for r in plan['rows'] if r['file_id']==row['id'])['target']
 assert Path(target).is_file() and Path(tmp_path/'music'/'one.wav').is_file() and fingerprint(rb.source)==before
 assert next(t for t in rb.view()['tracks'] if t['id']=='1')['path'].replace('\\','/')==target.replace('\\','/')
 with database(rb.staged) as db:assert db.get_cue(ID='cue').ContentID=='1'
 with pytest.raises(ValueError,match='Apply'):service.cleanup(plan,lambda p:p.unlink())
 c.set_setting('rekordbox_write_enabled',True);rb.apply();service.cleanup(plan,lambda p:p.unlink())
 assert plan['state']=='complete' and Path(target).is_file() and not (tmp_path/'music'/'one.wav').exists()
 assert c.available_recording(row['recording_id'])
 assert not any(f['id']==row['id'] for f in c.files())


def test_destination_collision_and_changed_source_never_overwrite(rb,tmp_path):
 c=rb.catalogue;c.add_root(tmp_path/'music');c.scan();service=Organiser(c,rb);plan=service.plan('consolidate',tmp_path/'Dest');first=plan['rows'][0]
 original=Path(first['source']);old=file_hash(original);audio(original,12345)
 with pytest.raises(ValueError,match='source changed'):service.execute(plan)
 assert original.is_file() and file_hash(original)!=old and not Path(first['target']).exists()


def test_exact_duplicate_native_merge_preserves_keeper_cue_and_song_id(rb,tmp_path):
 c=rb.catalogue;first=tmp_path/'music'/'one.wav';copy=tmp_path/'copies'/'Copy.wav';copy.parent.mkdir();shutil.copy2(first,copy)
 c.add_root(first.parent);c.add_root(copy.parent);c.scan()
 with database(rb.staged) as db:
  keeper=db.add_content(copy,Title='Copy');keeper_id=keeper.ID;db.session.commit()
 playlist=rb.create_playlist('Set');rb.add_tracks(playlist,['1',keeper_id]);before=fingerprint(rb.source)
 # Publish only this disposable fixture's starting state, never the actual DB.
 c.set_setting('rekordbox_write_enabled',True);rb.apply()
 songs={s['track_id']:s['id'] for s in next(p for p in rb.view()['playlists'] if p['id']==playlist)['songs']}
 service=Organiser(c,rb);group=[f for f in c.files() if f['sha256']==file_hash(first)];plan=service.plan('duplicates',files=group,keeper=copy);service.execute(plan)
 tracks=rb.view()['tracks'];assert any(t['id']=='1' for t in tracks) and not any(t['id']==keeper_id for t in tracks)
 with database(rb.staged) as db:assert db.get_cue(ID='cue').ContentID=='1'
 current=next(p for p in rb.view()['playlists'] if p['id']==playlist)['songs'];assert len(current)==1 and current[0]['id']==songs['1'] and current[0]['track_id']=='1'
 rb.verify(rb.staged)


def test_duplicate_competing_cues_refuses_without_mutating_preview(rb,tmp_path):
 target=tmp_path/'music'/'one.wav';before=fingerprint(rb.staged)
 with database(rb.staged) as db:
  from pyrekordbox.db6.tables import DjmdCue
  db.add(DjmdCue.create(ID='othercue',ContentID='2',InMsec=200));db.session.commit()
 before=fingerprint(rb.staged)
 with pytest.raises(ValueError,match='Both copies'):rb.relocate_files({tmp_path/'music'/'two.wav':target},merge=True)
 assert fingerprint(rb.staged)==before


def test_move_download_folder_with_robocopy_preserves_sidecars_and_catalogue(rb,tmp_path):
 c=rb.catalogue;source=tmp_path/'music';(source/'cover.jpg').write_bytes(b'cover sentinel');c.set_setting('download_folder',str(source));c.add_root(source);c.scan()
 service=Organiser(c,rb);destination=tmp_path/'Other drive'/'Music';plan=service.plan('move',destination);service.execute(plan)
 assert c.get_setting('download_folder',None)==str(destination) and (destination/'cover.jpg').read_bytes()==b'cover sentinel'
 assert not list(destination.glob('.zotibox-copy-*'))
 assert source.is_dir() and (source/'cover.jpg').is_file()
 with pytest.raises(ValueError,match='Apply'):service.cleanup(plan,lambda p:p.unlink())


def test_preferred_extended_mix_survives_original_removal_and_future_spotify_sync(tmp_path):
 c=Catalogue(tmp_path/'DB');normal=tagged(tmp_path/'Music'/'Normal.wav',title="Don't Do It",artist='longstoryshort');extended=tagged(normal.with_name('Extended.wav'),title="Don't Do It (Extended Mix)",artist='longstoryshort')
 c.add_root(normal.parent);c.scan();rows=c.files();original=next(r for r in rows if c.resolve(r)==normal);chosen=next(r for r in rows if c.resolve(r)==extended)
 c.prefer_version(original['recording_id'],chosen['recording_id']);track=dict(remote('a'*22,title="Don't Do It"),artist='longstoryshort')
 c.save_remote(track);c.decide_match(track['id'],original['recording_id']);normal.unlink();c.scan()
 from dj_library.matching import match_track
 resolution=match_track(c,track);assert resolution['state']=='matched'
 playlist=c.sync_playlist({'id':'test','name':'Spotify','snapshot':'new','complete':True,'tracks':[track]},{track['id']:resolution})['playlist_id']
 entry=c.entries(playlist)[0];assert entry['recording_id']==chosen['recording_id'] and 'Extended' in entry['title']
 c.export_m3u8(playlist,tmp_path/'Set.m3u8');assert str(extended) in (tmp_path/'Set.m3u8').read_text(encoding='utf-8')
 with pytest.raises(ValueError,match='cycle'):c.prefer_version(chosen['recording_id'],original['recording_id'])


def test_preferred_version_can_replace_native_regular_cues_only_in_preview(rb,tmp_path):
 c=rb.catalogue;c.add_root(tmp_path/'music');c.scan();source=next(f for f in c.files() if c.resolve(f).name=='one.wav');target=tmp_path/'music'/'two.wav';before=fingerprint(rb.source)
 playlist=rb.create_playlist('Set');rb.add_tracks(playlist,['1']);c.set_setting('rekordbox_write_enabled',True);rb.apply();before=fingerprint(rb.source)
 service=Organiser(c,rb);plan=service.plan('version',files=[source],keeper=target);service.execute(plan)
 assert fingerprint(rb.source)==before
 assert not any(t['id']=='1' for t in rb.view()['tracks'])
 assert next(p for p in rb.view()['playlists'] if p['id']==playlist)['songs'][0]['track_id']=='2'
 with database(rb.staged) as db:assert db.get_cue(ID='cue') is None
 rb.verify(rb.staged)


def png():
 image=QImage(12,12,QImage.Format.Format_RGB32);image.fill(QColor('blue'));buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buffer,'PNG');return bytes(buffer.data())


def test_conversion_recovers_unverified_copy_and_keeps_tags_and_cover(rb,tmp_path):
 import base64
 source=tmp_path/'Old.ogg';original=audio(tmp_path/'tone.wav')
 subprocess.run([str(__import__('dj_library.downloads',fromlist=['ffmpeg_path']).ffmpeg_path('ffmpeg')),'-v','error','-i',str(original),'-c:a','libvorbis',str(source)],check=True,creationflags=0x08000000)
 tags=OggVorbis(source);tags['title']='Song';tags['artist']='Artist';tags['album']='Album';tags['isrc']='GBTEST123456';p=Picture();p.type=3;p.mime='image/png';p.data=png();tags['metadata_block_picture']=[base64.b64encode(p.write()).decode()];tags.save()
 c=rb.catalogue;output=compatible_path(c,source);assert File(output,easy=True)['artist']==['Artist'] and picture(output)[0]==p.data
 from mutagen.mp3 import MP3
 old=MP3(output);old.tags.delall('APIC');old.tags.delall('TPE1');old.save();receipt=output.with_suffix('.mp3.json');saved=json.loads(receipt.read_text(encoding='utf-8'));saved['output']=file_hash(output);receipt.write_text(json.dumps(saved),encoding='utf-8')
 before_audio=audio_hash(output);assert compatible_path(c,source)==output and picture(output)[0]==p.data and File(output,easy=True)['artist']==['Artist'] and audio_hash(output)==before_audio
 sha=file_hash(source);output.with_suffix('.mp3.json').unlink();assert compatible_path(c,source)==output
 output.write_bytes(b'unverified damaged copy');recovered=compatible_path(c,source)
 assert recovered!=output and output.exists() and file_hash(source)==sha and compatible_path(c,source)==recovered


def test_artwork_embedding_preserves_audio_identity_and_existing_cover(rb,tmp_path):
 from dj_library.downloads import ffmpeg_path
 source=audio(tmp_path/'source.wav');target=tmp_path/'Coverless.mp3'
 subprocess.run([str(ffmpeg_path('ffmpeg')),'-v','error','-i',str(source),'-c:a','libmp3lame','-b:a','320k',str(target)],check=True,creationflags=0x08000000)
 c=rb.catalogue;c.add_root(tmp_path);c.scan();row=next(f for f in c.files() if c.resolve(f)==target);before=audio_hash(target);identity=row['recording_id']
 assert embed_verified(c,row,png(),'image/png');assert audio_hash(target)==before and picture(target)
 new=next(f for f in c.files() if f['id']==row['id']);assert new['recording_id']==identity and new['sha256']==file_hash(target)
 assert not embed_verified(c,new,png(),'image/png');assert c.scan()['unchanged']>=1


def test_cancelled_copy_leaves_originals_and_saved_journal(rb,tmp_path):
 c=rb.catalogue;c.add_root(tmp_path/'music');c.scan();service=Organiser(c,rb);plan=service.plan('consolidate',tmp_path/'Dest');cancel=threading.Event();cancel.set()
 with pytest.raises(InterruptedError):service.execute(plan,cancel=cancel)
 assert all(Path(r['source']).exists() for r in plan['rows']) and (service.journals/(plan['id']+'.json')).is_file()


def test_organisation_dialogs_open_and_extended_local_detection(tmp_path):
 app();w=Window(tmp_path/'DB');source=tagged(tmp_path/'Music'/'Normal.wav',title="Don't Do It",artist='longstoryshort');tagged(source.with_name('Extended.wav'),title="Don't Do It (Extended Mix)",artist='longstoryshort');w.catalogue.add_root(source.parent);w.catalogue.scan();w.refresh()
 dialog=OrganiseDialog(w);assert dialog.tabs.count()==4 and dialog.versions.count()==2;dialog.close()
 search=ExtendedSearch(w);assert search.tree.topLevelItemCount()==1 and search.tree.topLevelItem(0).childCount()==1;search.close();w.close()


def test_native_analysis_path_is_staged_and_applied_with_cues_intact(rb,tmp_path):
 from pyrekordbox.anlz import AnlzFile
 source=tmp_path/'music'/'one.wav';folder=rb.source.parent/'share'/'PIONEER'/'USBANLZ'/'test';folder.mkdir(parents=True)
 analysis=folder/'ANLZ0000.DAT';encoded=str(source).replace('\\','/').encode('utf-16-be')+b'\0\0'
 tag=b'PPTH'+struct.pack('>III',16,16+len(encoded),len(encoded))+encoded
 analysis.write_bytes(b'PMAI'+struct.pack('>6I',28,28+len(tag),0,0,0,0)+tag)
 rb.stage('Fixture analysis',lambda db:setattr(db.get_content(ID='1'),'AnalysisDataPath','/PIONEER/USBANLZ/test/ANLZ0000.DAT'))
 c=rb.catalogue;c.set_setting('rekordbox_write_enabled',True);rb.apply();c.add_root(source.parent);c.scan()
 before=file_hash(analysis);native_before=fingerprint(rb.source);service=Organiser(c,rb);plan=service.plan('consolidate',tmp_path/'Dest');service.execute(plan)
 target=next(r['target'] for r in plan['rows'] if r['source']==str(source));overlay=rb.staged.parent/'native-files'/analysis.relative_to(rb.source.parent)
 assert file_hash(analysis)==before and fingerprint(rb.source)==native_before
 assert AnlzFile.parse_file(overlay).get('path')==target.replace('\\','/')
 # A later stage must carry forward the analysis overlay as well as the DB.
 rb.create_playlist('After relocation');rb.apply()
 assert AnlzFile.parse_file(analysis).get('path')==target.replace('\\','/')
 with database(rb.source) as db:assert db.get_cue(ID='cue').ContentID=='1'


def test_cleanup_changed_target_and_open_rekordbox_retain_originals(rb,tmp_path,monkeypatch):
 c=rb.catalogue;c.add_root(tmp_path/'music');c.scan();service=Organiser(c,rb);plan=service.plan('consolidate',tmp_path/'Dest');service.execute(plan);c.set_setting('rekordbox_write_enabled',True);rb.apply()
 monkeypatch.setattr('dj_library.rekordbox.running',lambda:True)
 with pytest.raises(ValueError,match='Close'):service.cleanup(plan,lambda p:p.unlink())
 monkeypatch.setattr('dj_library.rekordbox.running',lambda:False)
 Path(plan['rows'][0]['target']).write_bytes(b'changed')
 with pytest.raises(ValueError,match='changed'):service.cleanup(plan,lambda p:p.unlink())
 assert all(Path(r['source']).exists() for r in plan['rows'])


@pytest.mark.parametrize('extension,codec',[('.mp3','libmp3lame'),('.flac','flac')])
def test_online_cover_repair_uses_saved_identity_and_preserves_audio(rb,tmp_path,extension,codec):
 from dj_library.downloads import ffmpeg_path
 from dj_library.artwork_repair import repair
 source=audio(tmp_path/'Source.wav');target=tmp_path/('Missing art'+extension)
 subprocess.run([str(ffmpeg_path('ffmpeg')),'-v','error','-i',str(source),'-c:a',codec,str(target)],check=True,creationflags=0x08000000)
 c=rb.catalogue;c.add_root(tmp_path);c.scan();row=next(f for f in c.files() if c.resolve(f)==target);track=remote();c.save_remote(track);c.decide_match(track['id'],row['recording_id']);before=audio_hash(target);calls=[]
 class Spotify:
  def request(self,path):assert path=='tracks/'+track['id'];return {'album':{'images':[{'url':'https://i.scdn.co/image/demo'}]}}
 class Response:
  status_code=200
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def raise_for_status(self):pass
  def iter_content(self,*a):yield png()
 class Session:
  def get(self,url,**kwargs):calls.append(url);return Response()
 result=repair(c,[row],Spotify(),session=Session());assert result['embedded']==1 and not result['errors'] and picture(target)
 assert calls==['https://i.scdn.co/image/demo'] and audio_hash(target)==before


def test_native_sync_does_not_duplicate_regular_and_preferred_extra(rb,tmp_path):
 c=rb.catalogue;c.add_root(tmp_path/'music');c.scan();one=next(f for f in c.files() if c.resolve(f).name=='one.wav');two=next(f for f in c.files() if c.resolve(f).name=='two.wav')
 playlist=c.create_playlist('Mixed versions');c.add_entries(playlist,[one['recording_id'],two['recording_id']]);c.prefer_version(one['recording_id'],two['recording_id'])
 native=rb.sync_local(playlist);songs=next(p for p in rb.view()['playlists'] if p['id']==native)['songs'];assert len(songs)==1 and songs[0]['track_id']=='2'
