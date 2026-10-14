import json
import zipfile

import pytest

from dj_library.catalogue import Catalogue
from dj_library.transfer import export_workspace, import_workspace, inspect_package


def rewrite(package, target, transform):
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(target, "w") as dest:
        contents = {name: source.read(name) for name in source.namelist()}
        transform(contents)
        for name, data in contents.items():
            dest.writestr(name, data)


def test_import_never_overwrites_existing_workspace(tmp_path):
    source = Catalogue(tmp_path / "source")
    package = tmp_path / "source.zotibox"
    export_workspace(source, package)
    destination = Catalogue(tmp_path / "destination")
    marker = destination.create_playlist("Keep this")
    with pytest.raises(ValueError, match="never overwritten"):
        import_workspace(package, destination.workspace)
    assert destination.playlists()[0]["id"] == marker


def test_database_tampering_rejected_without_committing_import(tmp_path):
    source = Catalogue(tmp_path / "source")
    package = tmp_path / "source.zotibox"
    export_workspace(source, package)
    bad = tmp_path / "bad.zotibox"
    rewrite(package, bad, lambda contents: contents.__setitem__("catalogue.sqlite3", b"bad database"))
    with pytest.raises(ValueError, match="checksum"):
        import_workspace(bad, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()
    assert not list(tmp_path.glob(".zotibox-import-*"))


def test_unexpected_path_rejected(tmp_path):
    source = Catalogue(tmp_path / "source")
    package = tmp_path / "source.zotibox"
    export_workspace(source, package)
    bad = tmp_path / "bad.zotibox"
    rewrite(package, bad, lambda contents: contents.__setitem__("../escape.txt", b"no"))
    with pytest.raises(ValueError, match="unexpected"):
        inspect_package(bad)


def test_machine_paths_and_credentials_not_exported(tmp_path):
    source = Catalogue(tmp_path / "source")
    source.set_setting("playlist_exports",{"external-playlist":"C:/Users/source/Set.m3u8"})
    source.set_setting("download_folder","C:/Users/source/Music")
    (source.workspace / "credentials.json").write_text('{"secret": "never-export"}')
    package = tmp_path / "source.zotibox"
    export_workspace(source, package)
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == {"catalogue.sqlite3", "manifest.json"}
    import_workspace(package,tmp_path/"imported")
    restored=Catalogue(tmp_path/"imported")
    assert restored.get_setting("playlist_exports",{})=={}
    assert restored.get_setting("download_folder","")==""


def test_export_rejects_wrong_extension(tmp_path):
    source = Catalogue(tmp_path / "source")
    with pytest.raises(ValueError, match="destination"):
        export_workspace(source, source.db_path)

