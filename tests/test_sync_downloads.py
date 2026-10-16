import threading
from pathlib import Path
import pytest
from mutagen.wave import WAVE
from mutagen.id3 import TIT2,TPE1,TSRC,TALB
from dj_library.catalogue import Catalogue
from dj_library.matching import match_track
from dj_library.downloads import DownloadEngine,worker_config
from dj_library.workspace import relocate
from test_catalogue import audio

def tagged(path,title="Track",artist="Artist",album="Album",isrc="AUABC2600001",frames=8000):
    audio(path,frames)
    file=WAVE(path)
    file.add_tags()
    for cls,value in [(TIT2,title),(TPE1,artist),(TALB,album),(TSRC,isrc)]:
        file.tags.add(cls(encoding=3,text=[value]))
    file.save()
    return path

def remote(track_id="A"*22,title="Track",duration=1):
    return {"id":track_id,"title":title,"artist":"Artist","album":"Album","isrc":"AUABC2600001","duration":duration,"playable":True}

def test_matching_preserves_versions_and_requires_ambiguous_review(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    music=tmp_path/"music"
    tagged(music/"first.wav")
    catalogue.add_root(music)
    catalogue.scan()
    assert match_track(catalogue,remote())["state"]=="matched"
    extended=remote("B"*22,"Track (Extended Mix)")
    assert match_track(catalogue,extended)["state"]!="matched"
    tagged(music/"remix.wav",title="Track (Alpha Remix)")
    catalogue.scan()
    assert match_track(catalogue,remote("D"*22,"Track (Beta Remix)"))["state"]!="matched"
    tagged(music/"second.wav",album="Another release")
    catalogue.scan()
    assert match_track(catalogue,remote("C"*22))["state"]=="review"

def test_sync_removal_changes_references_only(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    music=tmp_path/"music"
    file=tagged(music/"track.wav")
    catalogue.add_root(music)
    catalogue.scan()
    track=remote()
    resolved={track["id"]:match_track(catalogue,track)}
    snapshot={"id":"P"*22,"name":"Set","snapshot":"s1","tracks":[track,track],"complete":True}
    report=catalogue.sync_playlist(snapshot,resolved)
    assert len(catalogue.entries(report["playlist_id"]))==2
    snapshot.update(snapshot="s2",tracks=[])
    assert catalogue.sync_playlist(snapshot,{})["removed"]==2
    assert file.exists() and len(catalogue.files())==1

def test_remove_folder_keeps_files_and_playlist_references(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    path=tagged(tmp_path/"music"/"track.wav")
    root=catalogue.add_root(path.parent)
    catalogue.scan()
    playlist=catalogue.create_playlist("Set")
    catalogue.add_entries(playlist,[catalogue.files()[0]["recording_id"]])
    catalogue.remove_root(root)
    assert path.exists() and catalogue.files()==[]
    assert len(catalogue.entries(playlist))==1
    old_recording=catalogue.entries(playlist)[0]["recording_id"]
    catalogue.add_root(path.parent)
    catalogue.scan()
    assert catalogue.files()[0]["recording_id"]==old_recording
    assert catalogue.available_recording(old_recording)

def test_integration_paths_are_reserved_but_download_preferences_are_editable(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    catalogue.set_setting("zotify_options",{"DOWNLOAD_RATE_LIMITER":0,"ROOT_PATH":"elsewhere","DOWNLOAD_PARENT_ALBUM":True,"DOWNLOAD_FORMAT":"mp3"})
    config=worker_config(catalogue,tmp_path/"stage")
    assert config["DOWNLOAD_RATE_LIMITER"]==0
    assert config["DOWNLOAD_PARENT_ALBUM"] is False
    assert config["ROOT_PATH"]==str(tmp_path/"stage"/"music")
    assert config["DOWNLOAD_FORMAT"]=="mp3"

def test_serial_queue_validates_indexes_and_deduplicates(tmp_path,monkeypatch):
    catalogue=Catalogue(tmp_path/"database")
    first,second=remote(),remote("B"*22,"Second",2)
    second["isrc"]="AUABC2600002"
    for track in [first,second]:catalogue.save_remote(track)
    first_job=catalogue.enqueue(first["id"])
    assert catalogue.enqueue(first["id"])==first_job
    catalogue.enqueue(second["id"])
    engine=DownloadEngine(catalogue)
    calls=[]
    def invoke(stage,track_id=None,**kwargs):
        calls.append(track_id)
        track=catalogue.remote_track(track_id)
        tagged(stage/"music"/"output.wav",track["title"],isrc=track["isrc"],frames=int(track["duration"]*8000))
        return "OGG_VORBIS_320"
    monkeypatch.setattr(engine,"invoke",invoke)
    assert engine.run_queue()=="Queue finished"
    assert calls==[first["id"],second["id"]]
    assert all(job["state"]=="complete" for job in catalogue.download_jobs())
    assert len(catalogue.files())==2
    assert match_track(catalogue,first)["state"]=="matched"
    catalogue.enqueue(first["id"])
    engine.run_queue()
    assert len(calls)==2

def test_bad_output_never_enters_music_library(tmp_path,monkeypatch):
    catalogue=Catalogue(tmp_path/"database")
    track=remote(duration=30)
    catalogue.save_remote(track)
    catalogue.enqueue(track["id"])
    engine=DownloadEngine(catalogue)
    monkeypatch.setattr(engine,"invoke",lambda stage,*args,**kwargs:(tagged(stage/"music"/"wrong.wav"),"VORBIS")[1])
    engine.run_queue()
    assert catalogue.download_jobs()[0]["state"]=="failed"
    assert catalogue.files()==[]
    assert not (catalogue.workspace/"Music").exists()

def test_relocation_preserves_ids_and_remaps_internal_music(tmp_path):
    catalogue=Catalogue(tmp_path/"old")
    path=tagged(catalogue.workspace/"Music"/"track.wav")
    catalogue.add_root(path.parent)
    catalogue.scan()
    original=catalogue.files()[0]
    catalogue.set_setting("download_folder",str(path.parent))
    catalogue.set_setting("playlist_exports",{"set":str(catalogue.workspace/"Set.m3u8")})
    destination=tmp_path/"new"/"ZotiBox Database"
    relocate(catalogue,destination)
    restored=Catalogue(destination)
    assert restored.files()[0]["id"]==original["id"]
    assert restored.resolve(restored.files()[0])==destination/"Music"/"track.wav"
    assert path.exists()
    assert restored.get_setting("download_folder","")==str(destination/"Music")
    assert restored.get_setting("playlist_exports",{})=={"set":str(destination/"Set.m3u8")}


def test_different_unicode_titles_never_collapse_into_a_strong_match(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    music=tmp_path/"music"
    tagged(music/"track.wav",title="東京",isrc="")
    catalogue.add_root(music)
    catalogue.scan()
    track=remote(title="大阪")
    track["isrc"]=""
    assert match_track(catalogue,track)["state"]!="matched"


def test_malformed_imported_queue_id_cannot_escape_staging(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    track=remote()
    catalogue.save_remote(track)
    with catalogue.connect() as db:
        db.execute("INSERT INTO downloads(id,spotify_id,created) VALUES (?,?,?)",("../../escape",track["id"],"now"))
    engine=DownloadEngine(catalogue)
    engine.run_queue()
    assert catalogue.download_jobs()[0]["state"]=="failed"
    assert not (catalogue.workspace/"escape").exists()
    with pytest.raises(ValueError,match="outside"):
        engine.invoke(tmp_path/"outside")
    assert not (tmp_path/"outside").exists()


def test_spotify_unavailable_track_can_still_reference_owned_local_audio(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    music=tmp_path/"music"
    tagged(music/"track.wav")
    catalogue.add_root(music)
    catalogue.scan()
    track=remote()
    track["playable"]=False
    assert match_track(catalogue,track)["state"]=="matched"
    with pytest.raises(ValueError,match="unavailable"):
        catalogue.enqueue(track["id"])
