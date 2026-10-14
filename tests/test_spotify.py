import copy
import threading
import time
import urllib.request
from urllib.parse import parse_qs,urlparse
import pytest
from dj_library.spotify import Spotify,SpotifyError,TokenStore,spotify_id
from dj_library.catalogue import Catalogue

CLIENT="a"*32

class MemoryStore:
    def __init__(self):
        self.token={"access_token":"test-token","refresh_token":"test-refresh","expires_at":time.time()+3600,"client_id":CLIENT}
    def load(self):return self.token
    def save(self,value):self.token=value

class Response:
    def __init__(self,data,status=200):self.data,self.status_code,self.headers=data,status,{}
    def json(self):return self.data

class Session:
    def __init__(self,responses):self.responses=responses;self.posts=[]
    def get(self,url,**kwargs):
        key=url.removeprefix("https://api.spotify.com/v1/")
        response=self.responses[key]
        if isinstance(response,list):response=response.pop(0)
        if isinstance(response,Exception):raise response
        return response
    def post(self,url,**kwargs):
        self.posts.append(kwargs["data"])
        return Response({"access_token":"new-token","refresh_token":"new-refresh","expires_in":3600})

def item(track_id="A"*22):
    return {"item":{"id":track_id,"name":"Track","artists":[{"name":"Artist"}],"album":{"name":"Album"},"external_ids":{"isrc":"AUABC2600001"},"duration_ms":1000,"type":"track"}}

def test_complete_snapshot_preserves_order_and_repeated_occurrences():
    meta={"id":"P"*22,"name":"Set","snapshot_id":"s1","items":{"total":3}}
    session=Session({"playlists/"+"P"*22:Response(meta),"playlists/"+"P"*22+"/items":Response({"items":[item(),item("B"*22)],"next":"https://api.spotify.com/v1/page2"}),"page2":Response({"items":[item()],"next":None})})
    snapshot=Spotify(CLIENT,MemoryStore(),session).snapshot("P"*22)
    assert snapshot["complete"]
    assert [t["id"] for t in snapshot["tracks"]]==["A"*22,"B"*22,"A"*22]

def test_incomplete_snapshot_does_not_clear_existing_playlist(tmp_path):
    catalogue=Catalogue(tmp_path/"workspace")
    playlist=catalogue.create_playlist("Keep")
    meta={"name":"Set","snapshot_id":"s1","items":{"total":2}}
    session=Session({"playlists/p":Response(meta),"playlists/p/items":Response({"items":[item()],"next":None})})
    with pytest.raises(SpotifyError,match="Incomplete"):
        Spotify(CLIENT,MemoryStore(),session).snapshot("p")
    assert catalogue.playlists()[0]["id"]==playlist
    with pytest.raises(ValueError,match="Incomplete"):
        catalogue.sync_playlist({"complete":False}, {})

def test_playlist_changes_during_fetch_are_retried():
    meta=lambda snapshot:{"name":"Set","snapshot_id":snapshot,"items":{"total":1}}
    session=Session({"playlists/p":[Response(meta("s1")),Response(meta("s2")),Response(meta("s2")),Response(meta("s2"))],"playlists/p/items":Response({"items":[item()],"next":None})})
    assert Spotify(CLIENT,MemoryStore(),session).snapshot("p")["snapshot"]=="s2"

def test_rate_limit_and_untrusted_pagination_are_reported():
    client=Spotify(CLIENT,MemoryStore(),Session({"me":Response({},429)}))
    with pytest.raises(SpotifyError,match="request limit"):
        client.request("me")
    with pytest.raises(SpotifyError,match="Unexpected"):
        client.request("https://evil.example/steal")

def test_refresh_keeps_client_identity():
    store=MemoryStore()
    store.token["expires_at"]=0
    session=Session({})
    assert Spotify(CLIENT,store,session).access_token()=="new-token"
    assert session.posts[0]["grant_type"]=="refresh_token"
    assert store.token["client_id"]==CLIENT

def test_dpapi_tokens_are_not_plaintext(tmp_path):
    store=TokenStore(tmp_path)
    value={"access_token":"private-test-token","client_id":CLIENT}
    store.save(value)
    assert b"private-test-token" not in store.path.read_bytes()
    assert store.load()==value
    store.clear()
    assert store.load() is None

def test_url_validation():
    assert spotify_id("https://open.spotify.com/track/"+"A"*22+"?si=anything","track")=="A"*22
    with pytest.raises(ValueError):spotify_id("https://evil.example/track/"+"A"*22,"track")

def test_pkce_state_and_verifier(monkeypatch):
    import dj_library.spotify as module
    from http.server import HTTPServer as NativeServer
    server={}
    def factory(address,handler):
        assert address==("127.0.0.1",4381)
        server["instance"]=NativeServer(("127.0.0.1",0),handler)
        return server["instance"]
    monkeypatch.setattr(module,"HTTPServer",factory)
    session=Session({"me":Response({"display_name":"Test"})})
    observed={}
    def opener(url):
        observed.update(parse_qs(urlparse(url).query))
        port=server["instance"].server_address[1]
        def callback():
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=test-code&state={observed['state'][0]}").read()
        threading.Thread(target=callback,daemon=True).start()
    assert Spotify(CLIENT,MemoryStore(),session).authenticate(threading.Event(),opener=opener)=="Test"
    import base64,hashlib
    verifier=session.posts[0]["code_verifier"]
    expected=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert observed["code_challenge"]==[expected]
    assert "client_secret" not in session.posts[0]
