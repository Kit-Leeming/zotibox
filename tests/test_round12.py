import time,json,sqlite3,threading
from pathlib import Path
import numpy as np
import pytest
from PySide6.QtCore import Qt,QItemSelectionModel
from PySide6.QtWidgets import QMenu,QMessageBox
from mutagen.wave import WAVE
from mutagen.id3 import TBPM
from dj_library.catalogue import Catalogue,file_hash
from dj_library.compatibility import compatible_path
from dj_library.downloads import ffmpeg_path,worker_config
from dj_library.playlist_compare import compare,playlist_sources,ComparisonDialog
from dj_library.ui import Window,ROLE
from dj_library.rekordbox import fingerprint,database
from test_catalogue import audio
from test_rekordbox import rb
from test_round6 import app,pump_until
from test_player_deck import PlayerOwner,synthetic_audio
from test_source_policy import bridge,AudioFile


def test_ogg_import_makes_verified_mp3_and_reuses_it_without_touching_original(rb,tmp_path):
 import subprocess
 source=tmp_path/'Ogg sources'/'Original.ogg';source.parent.mkdir()
 original=audio(tmp_path/'tone.wav',16000)
 subprocess.run([str(ffmpeg_path('ffmpeg')),'-v','error','-i',str(original),'-c:a','libvorbis',str(source)],check=True,creationflags=0x08000000)
 c=rb.catalogue;c.add_root(source.parent);c.scan();row=c.files()[0];sha=file_hash(source);before=fingerprint(rb.source)
 playlist=c.create_playlist('Ogg set');c.add_entries(playlist,[row['recording_id']]);target=rb.sync_local(playlist)
 view=rb.view();native=next(t for t in view['tracks'] if t['path'].endswith('.mp3'))
 assert Path(native['path']).is_file() and file_hash(source)==sha and fingerprint(rb.source)==before
 assert 'MP3 compatibility' in view['changes'][-1]
 first=file_hash(Path(native['path']));rb.sync_local(playlist)
 assert file_hash(Path(native['path']))==first
 assert len(list((c.workspace/'Music'/'Rekordbox compatible').glob('*.mp3')))==1
 assert len(next(p for p in rb.view()['playlists'] if p['id']==target)['songs'])==1
 sources=playlist_sources(c,rb.view());a=next(s for s in sources if s['id']=='local:'+playlist);b=next(s for s in sources if s['id']=='native:'+target)
 assert compare(a['rows'],b['rows'])[0]['status']=='Shared'


def test_bpm_tag_scan_and_unchanged_migration_preserve_ids(tmp_path):
 path=audio(tmp_path/'Music'/'Tagged.wav');f=WAVE(path);f.add_tags();f.tags.add(TBPM(encoding=3,text=['128.5']));f.save()
 c=Catalogue(tmp_path/'Database');c.add_root(path.parent);c.scan();row=c.files()[0];assert row['bpm']==128.5
 id_=c.create_playlist('Set');c.add_entries(id_,[row['recording_id']]);assert c.entries(id_)[0]['bpm']==128.5
 with c.connect() as db:db.execute('ALTER TABLE files DROP COLUMN bpm')
 sha=file_hash(path);c=Catalogue(c.workspace);result=c.scan();assert result['unchanged']==1
 assert c.files()[0]['bpm']==128.5 and c.files()[0]['id']==row['id'] and file_hash(path)==sha


def test_native_bpm_units_and_multi_delete_preserve_live_collection(rb):
 with database(rb.staged) as db:db.get_content(ID='1').BPM=12750;db.session.commit()
 assert next(t for t in rb.view()['tracks'] if t['id']=='1')['bpm']==127.5
 folder=rb.create_playlist('Folder',folder=True);one=rb.create_playlist('Child',parent=folder);two=rb.create_playlist('Other')
 before=fingerprint(rb.source);rb.delete_playlists([folder,one,two])
 assert not {folder,one,two}&{p['id'] for p in rb.view()['playlists']}
 assert fingerprint(rb.source)==before and len(rb.view()['tracks'])==2


def test_comparison_identity_duplicates_missing_and_no_title_guess():
 def row(key,pos=0,available=True):return {'title':'Same title','artist':'Artist','position':pos,'keys':[key],'available':available,'bpm':128}
 rows=compare([row('A'),row('A',1),row('B',2,False)],[row('A'),row('C',1)])
 assert len(rows)==3
 shared=next(r for r in rows if r['status']=='Shared');assert shared['left']=='1, 2' and shared['right']=='1'
 assert next(r for r in rows if r['status']=='Only left')['missing']
 assert sum(r['status']=='Only right' for r in rows)==1


def test_comparison_local_and_native_paths_match_and_dialog_filters(rb):
 c=rb.catalogue;path=Path(rb.view()['tracks'][0]['path']);c.add_root(path.parent);c.scan();file=next(f for f in c.files() if str(c.resolve(f))==str(path))
 local=c.create_playlist('Local');c.add_entries(local,[file['recording_id']]);native=rb.create_playlist('Native');rb.add_tracks(native,['1'])
 sources=playlist_sources(c,rb.view());a=next(p for p in sources if p['id']=='local:'+local);b=next(p for p in sources if p['id']=='native:'+native)
 assert compare(a['rows'],b['rows'])[0]['status']=='Shared'
 app();dialog=ComparisonDialog(sources,initial=[a['id'],b['id']]);assert dialog.table.rowCount()==1
 dialog.filter.setCurrentText('Only left');assert dialog.table.rowCount()==0;dialog.close()


def test_bulk_playlist_context_preserves_selection_and_deletes_both(tmp_path,monkeypatch):
 app();window=Window(tmp_path/'Database');window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);window.show();window.navigation.setCurrentRow(3)
 ids=[window.catalogue.create_playlist(n) for n in ['First','Second']];window.refresh_playlists();app().processEvents()
 tree=window.playlist_list;tree.clearSelection()
 for i in tree.playlist_items():i.setSelected(True)
 item=tree.playlist_items()[0];seen=[]
 class Menu(QMenu):
  def exec(self,*args):
   seen.extend(a.text() for a in self.actions());assert len(tree.selectedItems())==2
   next(a for a in self.actions() if a.text()=='Remove selected playlists…').trigger()
 monkeypatch.setattr('dj_library.ui.QMenu',Menu);monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
 window.playlist_context(tree.visualItemRect(item).center())
 assert 'Rename playlist…' not in seen and 'Compare selected playlists…' in seen and not window.catalogue.playlists()
 window.close()


@pytest.mark.parametrize('policy,source,extension',[('auto',AudioFile.OGG_VORBIS_320,'mp3'),('original',AudioFile.OGG_VORBIS_320,'ogg'),('original',AudioFile.FLAC_FLAC,'flac'),('mp3',AudioFile.FLAC_FLAC,'mp3')])
def test_audio_output_choices_use_selected_source(policy,source,extension):
 track,config,stream,_,_=bridge([source],format_policy=policy,fallback='mp3',configured='copy' if policy=='original' else 'mp3' if policy=='mp3' else 'flac')
 track.download([]);assert track.path.endswith('.'+extension) and stream.closed
 if policy=='original':assert track.conversion[0]=='copy'


def test_flac_output_rejects_lossy_source_instead_of_fake_flac():
 track,_,stream,_,_=bridge([AudioFile.OGG_VORBIS_320],format_policy='flac')
 with pytest.raises(ValueError,match='No lossless'):track.download([])
 assert stream.closed and not hasattr(track,'registered')
 from dj_library.zotify_worker import public_error,OutputFormatUnavailable
 assert 'Choose Auto' in public_error(OutputFormatUnavailable('No lossless Spotify source is available. Choose Auto or MP3 for this track.'))
 assert 'private-token' not in public_error(RuntimeError('private-token'))


def test_audio_preferences_upgrade_and_both_ui_editors_agree(tmp_path):
 app();c=Catalogue(tmp_path/'Database');c.set_setting('audio_preferences_v2',False);c.set_setting('zotify_options',{'DOWNLOAD_FORMAT':'flac','DOWNLOAD_QUALITY':'auto'})
 c=Catalogue(c.workspace);assert c.get_setting('zotify_options',{})['DOWNLOAD_FORMAT']=='auto'
 window=Window(c.workspace);assert [window.codec.itemText(i) for i in range(window.codec.count())]==['auto','mp3','flac','original']
 window.codec.setCurrentText('original');window.quality.setCurrentIndex(window.quality.findData('very_high'))
 assert window.catalogue.get_setting('zotify_options',{})['DOWNLOAD_QUALITY']=='very_high'
 config=worker_config(window.catalogue,tmp_path/'stage');assert config['DOWNLOAD_FORMAT']=='copy' and config['DOWNLOAD_QUALITY']=='very_high'
 window.zotify_panel.load();assert window.zotify_panel.editors['DOWNLOAD_FORMAT'].currentText()=='original'
 window.close()


def test_waveform_scrolling_reuses_image_and_live_playhead_ticks_smoothly(tmp_path):
 app();owner=PlayerOwner();owner.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);owner.resize(1200,300);owner.show();player=owner.player;player.audio.setVolume(0)
 path=synthetic_audio(tmp_path/'Preview.wav',seconds=3);player.load(path,'Test');pump_until(lambda:not player.jobs and player.media.position()>100)
 wave=player.wave;wave.grab();builds=wave.cache_builds
 for i in range(30):wave.position=500+i*16;wave.grab()
 assert wave.cache_builds==builds
 frames=[];player.frame_timer.timeout.connect(lambda:frames.append(wave.position));deadline=time.monotonic()+.5
 while time.monotonic()<deadline:app().processEvents();time.sleep(.004)
 assert len(set(frames))>=12
 player.toggle();app().processEvents();assert not player.frame_timer.isActive()
 assert player.shutdown();owner.close()
def test_native_bulk_context_preserves_selection_and_stages_both_deletions(rb,monkeypatch):
 app();ids=[rb.create_playlist(name) for name in ['First native','Second native']];before=fingerprint(rb.source)
 window=Window(rb.catalogue.workspace);window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen);panel=window.rb_panel;panel.backend=rb;panel.loaded(rb.view());window.show();window.navigation.setCurrentRow(3);window.playlist_tabs.setCurrentIndex(1);app().processEvents()
 panel.tree.clearSelection();items=[]
 def visit(item):
  if item.data(0,ROLE) in ids:item.setSelected(True);items.append(item)
  for i in range(item.childCount()):visit(item.child(i))
 for i in range(panel.tree.topLevelItemCount()):visit(panel.tree.topLevelItem(i))
 panel.refresh_tree();assert {i.data(0,ROLE) for i in panel.tree.selectedItems()}==set(ids)
 item=next(i for i in panel.tree.selectedItems());seen=[]
 class Menu(QMenu):
  def exec(self,*args):
   seen.extend(a.text() for a in self.actions());assert len(panel.tree.selectedItems())==2
   next(a for a in self.actions() if a.text()=='Delete selected playlists / folders…').trigger()
 monkeypatch.setattr('dj_library.rekordbox_ui.QMenu',Menu);monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
 panel.tree_context(panel.tree.visualItemRect(item).center());pump_until(lambda:window.job is None)
 assert not set(ids)&{p['id'] for p in rb.view()['playlists']} and fingerprint(rb.source)==before
 assert 'Rename…' not in seen;window.close()


def test_local_playlist_refresh_retains_multi_selection(tmp_path):
 app();window=Window(tmp_path/'Database');[window.catalogue.create_playlist(n) for n in ['One','Two']];window.refresh_playlists()
 for item in window.playlist_list.playlist_items():item.setSelected(True)
 window.refresh_playlists();assert len(window.playlist_list.selectedItems())==2
 window.close()
