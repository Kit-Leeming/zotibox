import shutil
import threading
import wave
from pathlib import Path

import pytest

from dj_library.catalogue import Catalogue
from dj_library.transfer import export_workspace, import_workspace


def audio(path, frames=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(b"\x01\x00" * frames)
    return path


@pytest.fixture
def library(tmp_path):
    catalogue = Catalogue(tmp_path / "workspace")
    music = tmp_path / "music"
    audio(music / "Artist" / "Track.wav")
    root = catalogue.add_root(music)
    catalogue.scan()
    return catalogue, music, root


def test_scan_preserves_ids_and_never_changes_music(library):
    catalogue, music, root = library
    before = (music / "Artist" / "Track.wav").read_bytes()
    row = catalogue.files()[0]
    result = catalogue.scan()
    assert result["unchanged"] == 1
    assert catalogue.files()[0]["id"] == row["id"]
    assert catalogue.files()[0]["recording_id"] == row["recording_id"]
    assert (music / "Artist" / "Track.wav").read_bytes() == before


def test_identical_copies_share_recording_and_playlist_references(library, tmp_path):
    catalogue, music, root = library
    shutil.copyfile(music / "Artist" / "Track.wav", music / "Copy.wav")
    catalogue.scan()
    rows = catalogue.files(duplicates=True)
    assert len(rows) == 2
    assert len({row["recording_id"] for row in rows}) == 1
    playlist = catalogue.create_playlist("Set")
    catalogue.add_entries(playlist, [rows[0]["recording_id"]] * 2)
    entries = catalogue.entries(playlist)
    assert len(entries) == 2 and entries[0]["id"] != entries[1]["id"]
    catalogue.reorder(playlist, [entries[1]["id"], entries[0]["id"]])
    assert catalogue.entries(playlist)[0]["id"] == entries[1]["id"]
    output = tmp_path / "set.m3u8"
    assert catalogue.export_m3u8(playlist, output) == []
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[2] == lines[4]
    catalogue.remove_entry(playlist, entries[1]["id"])
    assert len(catalogue.files()) == 2 and len(catalogue.entries(playlist)) == 1


def test_missing_files_retained_and_restored(library, tmp_path):
    catalogue, music, root = library
    path = music / "Artist" / "Track.wav"
    saved = path.read_bytes()
    original = catalogue.files()[0]
    playlist = catalogue.create_playlist("Set")
    catalogue.add_entries(playlist, [original["recording_id"]])
    path.unlink()
    assert catalogue.scan()["missing"] == 1
    assert catalogue.files()[0]["available"] == 0
    assert catalogue.export_m3u8(playlist, tmp_path / "set.m3u8") == ["Track"]
    path.write_bytes(saved)
    catalogue.scan()
    assert catalogue.files()[0]["available"] == 1
    assert catalogue.files()[0]["recording_id"] == original["recording_id"]


def test_unavailable_root_does_not_mark_files_missing(library):
    catalogue, music, root = library
    music.rename(music.with_name("offline"))
    assert catalogue.scan()["errors"]
    assert catalogue.files()[0]["available"] == 1


def test_cancelled_scan_does_not_reconcile_missing_files(library):
    catalogue, music, root = library
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        catalogue.scan(cancel=cancel)
    assert catalogue.files()[0]["available"] == 1


def test_changed_audio_does_not_silently_replace_playlist_recording(library):
    catalogue, music, root = library
    original = catalogue.files()[0]
    playlist = catalogue.create_playlist("Set")
    catalogue.add_entries(playlist, [original["recording_id"]])
    audio(music / "Artist" / "Track.wav", frames=16000)
    catalogue.scan()
    assert catalogue.files()[0]["recording_id"] != original["recording_id"]
    assert catalogue.entries(playlist)[0]["recording_id"] == original["recording_id"]


def test_music_root_overlap_rejected(library):
    catalogue, music, root = library
    with pytest.raises(ValueError, match="overlap"):
        catalogue.add_root(music / "Artist")
    assert catalogue.add_root(music) == root


def test_portable_package_restores_ids_order_and_music(library, tmp_path):
    catalogue, music, root = library
    recording_id = catalogue.files()[0]["recording_id"]
    playlist = catalogue.create_playlist("Laptop set")
    catalogue.add_entries(playlist, [recording_id, recording_id])
    before = catalogue.entries(playlist)
    package = tmp_path / "library.zotibox"
    export_workspace(catalogue, package, include_music=True)
    destination = tmp_path / "laptop"
    import_workspace(package, destination)
    restored = Catalogue(destination)
    assert restored.entries(playlist) == before
    assert restored.files()[0]["id"] == catalogue.files()[0]["id"]
    assert restored.resolve(restored.files()[0]).is_relative_to(destination)
    output = destination / "set.m3u8"
    assert restored.export_m3u8(playlist, output) == []
    assert str(music) not in output.read_text(encoding="utf-8")
    restored.scan()
    assert restored.files()[0]["recording_id"] == recording_id


def test_catalogue_only_import_can_remap_without_changing_ids(library, tmp_path):
    catalogue, music, root = library
    package = tmp_path / "library.zotibox"
    export_workspace(catalogue, package)
    import_workspace(package, tmp_path / "laptop")
    restored = Catalogue(tmp_path / "laptop")
    assert restored.bindings() == {}
    assert restored.resolve(restored.files()[0]) is None
    restored.map_root(root, music)
    assert restored.resolve(restored.files()[0]).is_file()


def test_reorder_rejects_incomplete_order(library):
    catalogue, _, _ = library
    playlist = catalogue.create_playlist("Set")
    catalogue.add_entries(playlist, [catalogue.files()[0]["recording_id"]])
    with pytest.raises(ValueError, match="changed"):
        catalogue.reorder(playlist, [])


def test_m3u8_export_rejects_audio_destination(library):
    catalogue, music, _ = library
    playlist = catalogue.create_playlist("Set")
    original = (music / "Artist" / "Track.wav").read_bytes()
    with pytest.raises(ValueError, match="extension"):
        catalogue.export_m3u8(playlist, music / "Artist" / "Track.wav")
    assert (music / "Artist" / "Track.wav").read_bytes() == original


def test_wave_id3_tags_are_read_without_modification(tmp_path):
    from mutagen.wave import WAVE
    from mutagen.id3 import TIT2, TPE1, TSRC
    music = tmp_path / "music"
    path = audio(music / "untitled.wav")
    tagged = WAVE(path)
    tagged.add_tags()
    tagged.tags.add(TIT2(encoding=3, text=["Tagged title"]))
    tagged.tags.add(TPE1(encoding=3, text=["Tagged artist"]))
    tagged.tags.add(TSRC(encoding=3, text=["AUABC2600001"]))
    tagged.save()
    before = path.read_bytes()
    catalogue = Catalogue(tmp_path / "workspace")
    catalogue.add_root(music)
    catalogue.scan()
    row = catalogue.files()[0]
    assert (row["title"], row["artist"], row["isrc"]) == ("Tagged title", "Tagged artist", "AUABC2600001")
    assert path.read_bytes() == before


def test_unsupported_database_is_rejected_without_changes(tmp_path):
    import sqlite3
    path=tmp_path/"prototype"
    path.mkdir()
    database=path/"catalogue.sqlite3"
    db=sqlite3.connect(database)
    db.execute("CREATE TABLE workspace(id TEXT PRIMARY KEY,schema_version INTEGER)")
    db.execute("INSERT INTO workspace VALUES ('old',1)")
    db.commit()
    db.close()
    before=database.read_bytes()
    with pytest.raises(ValueError,match="Unsupported"):
        Catalogue(path)
    assert database.read_bytes()==before

