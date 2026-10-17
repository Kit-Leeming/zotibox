"""All native writes use a disposable synthetic Rekordbox collection."""
import json
import sqlite3
import pytest
from test_rekordbox import rb
from dj_library.rekordbox import Rekordbox


def apply_playlist(rb,name):
    rb.catalogue.set_setting("rekordbox_write_enabled",True)
    rb.create_playlist(name)
    return rb.apply()


def test_own_changes_reuse_backup_across_adapter_restarts(rb):
    apply_playlist(rb,"First")
    baseline=rb.backups()
    rb=Rekordbox(rb.catalogue)
    assert "retained existing backup" in apply_playlist(rb,"Second")
    assert rb.backups()==baseline
    assert {p["name"] for p in rb.view()["playlists"]} >= {"First","Second"}


@pytest.mark.parametrize("external",["database","analysis","added","removed"])
def test_external_native_changes_require_new_baseline(rb,external):
    apply_playlist(rb,"First")
    assets=rb.source.parent/"share"/"analysis.DAT"
    if external=="database":
        with sqlite3.connect(rb.source) as db:db.execute("UPDATE djmdContent SET Title='Native edit' WHERE ID='1'")
        db.close()
    elif external=="analysis":assets.write_bytes(b"externally regenerated beatgrid")
    elif external=="added":(assets.parent/"new.EXT").write_bytes(b"new analysis")
    else:assets.unlink()
    rb.connect()
    assert "new backup" in apply_playlist(rb,"Second")
    assert len(rb.backups())==2


@pytest.mark.parametrize("damage",["missing","corrupt","unknown"])
def test_unknown_or_missing_recovery_backup_is_never_reused(rb,damage):
    apply_playlist(rb,"First")
    archive=rb.backups()[0]
    if damage=="missing":archive.unlink()
    elif damage=="corrupt":archive.write_bytes(b"damaged")
    else:(rb.root/"last-apply.json").write_text("{}",encoding="utf-8")
    assert "new backup" in apply_playlist(rb,"Second")
    saved=json.loads((rb.root/"last-apply.json").read_text(encoding="utf-8"))
    assert saved["backup"]!=archive.name


def test_restore_invalidates_own_write_provenance(rb):
    apply_playlist(rb,"First")
    rb.restore(rb.backups()[0])
    assert not (rb.root/"last-apply.json").exists()
    assert "new backup" in apply_playlist(rb,"After restore")


def test_running_warning_tracks_process_state(monkeypatch):
    from dj_library.rekordbox_ui import RekordboxPanel
    class Warning:
        def setVisible(self,value):self.visible=value
    class Panel:running_warning=Warning()
    panel=Panel()
    monkeypatch.setattr("dj_library.rekordbox_ui.running",lambda:True)
    RekordboxPanel.refresh_running_status(panel)
    assert panel.running_warning.visible
    monkeypatch.setattr("dj_library.rekordbox_ui.running",lambda:False)
    RekordboxPanel.refresh_running_status(panel)
    assert not panel.running_warning.visible


def test_link_existing_playlist_keeps_native_extras_name_and_song_identity(rb):
    from pathlib import Path
    from dj_library.rekordbox import database
    c=rb.catalogue
    with database(rb.source) as db:path=Path(db.get_content(ID="1").FolderPath)
    c.add_root(path.parent);c.scan()
    recording=next(r["recording_id"] for r in c.files() if c.resolve(r)==path)
    local=c.create_playlist("Spotify source");c.add_entries(local,[recording])
    target=rb.create_playlist("Native with SoundCloud extras")
    rb.add_tracks(target,["2","1"])
    before=next(p for p in rb.view()["playlists"] if p["id"]==target)
    ids={s["track_id"]:s["id"] for s in before["songs"]}
    assert rb.sync_local(local,target)==target
    merged=next(p for p in rb.view()["playlists"] if p["id"]==target)
    assert merged["name"]=="Native with SoundCloud extras"
    assert [s["track_id"] for s in merged["songs"]]==["1","2"]
    assert {s["track_id"]:s["id"] for s in merged["songs"]}==ids
    # Repeat the preview before Apply; extras must not become managed tracks.
    rb.sync_local(local,target)
    c.set_setting("rekordbox_write_enabled",True);rb.apply()
    entry=c.entries(local)[0];c.remove_entry(local,entry["id"])
    rb=Rekordbox(c);rb.sync_local(local)
    remaining=next(p for p in rb.view()["playlists"] if p["id"]==target)
    assert [s["track_id"] for s in remaining["songs"]]==["2"]
    assert remaining["songs"][0]["id"]==ids["2"]


def test_explicit_create_new_can_relink_without_deleting_old_native_playlist(rb):
    c=rb.catalogue;local=c.create_playlist("Source")
    old=rb.sync_local(local)
    new=rb.sync_local(local,"")
    assert new!=old
    assert {old,new} <= {p["id"] for p in rb.view()["playlists"]}


def test_analysis_paths_only_expose_selected_native_pair_without_writes(rb):
    from dj_library.catalogue import file_hash
    from dj_library.rekordbox import analysis_paths
    folder=rb.source.parent/"share"/"PIONEER"/"USBANLZ"/"track";folder.mkdir(parents=True)
    dat=folder/"ANLZ0000.DAT";ext=folder/"ANLZ0000.EXT";other=folder/"OTHER.DAT"
    for path in [dat,ext,other]:path.write_bytes(path.name.encode())
    reference="/PIONEER/USBANLZ/track/ANLZ0000.DAT"
    before={p:file_hash(p) for p in [rb.source,dat,ext,other]}
    assert analysis_paths(rb.source,reference)==[str(dat.resolve()),str(ext.resolve())]
    with sqlite3.connect(rb.source) as db:
        db.execute("UPDATE djmdContent SET AnalysisDataPath=? WHERE ID='1'",(reference,))
        db.execute("UPDATE djmdCue SET Kind=3,Color=16711680,Comment=NULL WHERE ID='cue'")
    db.close();rb.connect()
    source_hash=file_hash(rb.source)
    row=next(t for t in rb.view()["tracks"] if t["id"]=="1")
    assert row["analysis_paths"]==[str(dat.resolve()),str(ext.resolve())]
    assert row["cues"][0]["cue_type"]=="hot"
    assert row["cues"][0]["color"]==16711680
    assert row["cues"][0]["label"].startswith("Hot cue")
    assert file_hash(rb.source)==source_hash
    assert all(file_hash(p)==before[p] for p in [dat,ext,other])


@pytest.mark.parametrize("reference",["../secret.DAT","/../secret.DAT","PIONEER/../../secret.EXT","C:/secret.DAT","//server/share/file.DAT","\\\\server\\share\\file.DAT","/PIONEER/file.txt",None,""])
def test_analysis_paths_reject_external_or_unrelated_paths(rb,reference):
    from dj_library.rekordbox import analysis_paths
    assert analysis_paths(rb.source,reference)==[]
