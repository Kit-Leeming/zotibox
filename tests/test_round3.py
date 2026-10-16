import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from dj_library.catalogue import Catalogue
from dj_library.zotify_oauth import LoopbackOAuth, LoginError
from dj_library.renaming import rename_plan, apply_renames
from dj_library.downloads import download_destination
from dj_library.updates import find_update
from test_sync_downloads import tagged, remote


def test_two_immediate_oauth_callbacks_release_same_port(monkeypatch):
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port=sock.getsockname()[1]
    callbacks=[]
    with ThreadPoolExecutor() as pool:
        for _ in range(2):
            def emit(event,**values):
                if event=="auth_url":
                    state=parse_qs(urlsplit(values["url"]).query)["state"][0]
                    callbacks.append(pool.submit(requests.get,f"http://127.0.0.1:{port}/login",params={"code":"test-code","state":state},timeout=3))
            oauth=LoopbackOAuth("client",f"http://127.0.0.1:{port}/login",emit)
            monkeypatch.setattr(oauth,"request_token",lambda:oauth.ingest_token_response({"access_token":"test","expires_in":3600}))
            assert oauth.flow().auth_data==b"test"
            assert callbacks[-1].result().status_code==200
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",port))


def test_oauth_decline_timeout_and_state_validation(monkeypatch):
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    with ThreadPoolExecutor() as pool:
        responses=[]
        def emit(event,**values):
            if event=="auth_url":
                state=parse_qs(urlsplit(values["url"]).query)["state"][0]
                def callback():
                    responses.append(requests.get(f"http://127.0.0.1:{port}/login",params={"code":"bad","state":"wrong"},timeout=3).status_code)
                    return requests.get(f"http://127.0.0.1:{port}/login",params={"error":"access_denied","state":state},timeout=3)
                pool.submit(callback)
        with pytest.raises(LoginError,match="declined"):
            LoopbackOAuth("client",f"http://127.0.0.1:{port}/login",emit).flow()
        assert responses==[400]
    with pytest.raises(LoginError,match="timed out"):
        LoopbackOAuth("client",f"http://127.0.0.1:{port}/login",lambda *a,**kw:None,timeout=.05).flow()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",port));sock.listen()
        with pytest.raises(LoginError,match="already in use"):
            LoopbackOAuth("client",f"http://127.0.0.1:{port}/login",lambda *a,**kw:pytest.fail("Browser opened before binding")).flow()


def test_local_update_versions_and_no_downgrade(tmp_path):
    for name in ["ZotiBox-Setup-0.3.0.exe","ZotiBox-Setup-0.10.0.exe","ZotiBox-Setup-0.4.0.exe","other.exe"]:
        (tmp_path/name).write_bytes(b"test")
    assert find_update(tmp_path,current='0.2.0')["version"]=="0.10.0"
    assert find_update(tmp_path,"0.10.0") is None


def test_rename_keeps_file_recording_playlist_and_audio(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    music=catalogue.workspace/"Music"
    path=tagged(music/"old [id].wav")
    catalogue.add_root(music);catalogue.scan()
    row=catalogue.files()[0]
    playlist=catalogue.create_playlist("Set")
    catalogue.add_entries(playlist,[row["recording_id"]])
    before=path.read_bytes()
    plan=rename_plan(catalogue)
    assert apply_renames(catalogue,plan)==1
    renamed=catalogue.files()[0]
    assert renamed["id"]==row["id"]
    assert renamed["recording_id"]==catalogue.entries(playlist)[0]["recording_id"]
    assert catalogue.resolve(renamed).name=="Artist - Track.wav"
    assert catalogue.resolve(renamed).read_bytes()==before
    assert apply_renames(catalogue,plan)==0


def test_rename_collision_and_other_roots_are_untouched(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    music=catalogue.workspace/"Music"
    first=tagged(music/"old.wav")
    occupied=tagged(music/"Artist - Track.wav",frames=16000)
    other=tagged(tmp_path/"other"/"old.wav")
    catalogue.add_root(music);catalogue.add_root(other.parent);catalogue.scan()
    assert apply_renames(catalogue,rename_plan(catalogue))==0
    assert first.exists() and occupied.exists() and other.exists()
    staged=tagged(tmp_path/"stage"/"output.wav",frames=24000)
    assert download_destination(music,remote(),staged).name=="Artist - Track (2).wav"


def test_download_indexing_can_commit_during_directory_scan(tmp_path):
    catalogue=Catalogue(tmp_path/"database")
    music=tmp_path/"music"
    tagged(music/"existing.wav")
    root=catalogue.add_root(music)
    discovered=threading.Event();resume=threading.Event()
    def progress(*_):
        discovered.set()
        assert resume.wait(5)
    with ThreadPoolExecutor() as pool:
        scan=pool.submit(catalogue.scan,progress)
        assert discovered.wait(5)
        downloaded=tagged(music/"downloaded.wav",title="New")
        indexing=pool.submit(catalogue.index_file,downloaded,root)
        try:
            assert indexing.result(timeout=3)["indexed"]==1
            assert len(catalogue.files())==2
        finally:
            resume.set()
        assert scan.result()["missing"]==0
    assert all(row["available"] for row in catalogue.files())
