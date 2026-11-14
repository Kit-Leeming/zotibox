import json
from types import SimpleNamespace

import pytest
import requests

from dj_library.diagnostics import Diagnostics
from dj_library.spotify import SpotifyError
from dj_library import spotify_token


def test_rolling_diagnostics_exclude_raw_data_and_can_be_disabled(tmp_path):
    log=Diagnostics(tmp_path,"0.15.2")
    log.LIMIT=700
    failure=requests.ConnectionError("SECRET",FileNotFoundError(2,"SECRET","C:/private-file"))
    for _ in range(30):
        log.record("Spotify renewal","failed",error=failure)
    report=log.report()
    rows=[json.loads(row) for row in report.splitlines()]
    assert 0<len(rows)<30 and len(report)<=1400
    assert "SECRET" not in report and "private-file" not in report
    assert {"type":"FileNotFoundError","errno":2} in rows[-1]["errors"]
    log.enabled=False
    log.record("Disabled","started")
    assert log.report()==report
    log.clear()
    assert not log.report()


def test_missing_file_token_error_uses_windows_fallback(monkeypatch):
    monkeypatch.setattr(spotify_token.os,"name","nt")
    monkeypatch.setattr(requests.utils,"get_environ_proxies",lambda url:{})
    data={"grant_type":"refresh_token","refresh_token":"private"}
    events=[]
    def fail(url,**kwargs):
        assert url==spotify_token.TOKEN_URL and not kwargs["allow_redirects"]
        raise requests.ConnectionError("Connection aborted.",FileNotFoundError(2,"Missing"))
    def native(form):
        assert form==data
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(spotify_token,"windows_token_post",native)
    response=spotify_token.token_post(SimpleNamespace(post=fail),data,"renewal",SpotifyError,
        lambda *a,**k: events.append((a,k)),fallback=True)
    assert response.status_code==200
    assert [e[0][1] for e in events]==["transport failed","Windows HTTPS response"]
    proxied=SimpleNamespace(post=fail,proxies={"https":"configured"})
    with pytest.raises(SpotifyError,match="Could not complete"):
        spotify_token.token_post(proxied,data,"renewal",SpotifyError,lambda *a,**k:None,fallback=True)


def test_native_form_secrets_only_sent_through_stdin(monkeypatch):
    def run(args,**kwargs):
        assert "private-token" not in str(args)
        assert kwargs["input"]==b"refresh_token=private-token"
        assert args[-1]==spotify_token.TOKEN_URL
        assert "--disable"==args[1] and "--insecure" not in args and "--location" not in args
        return SimpleNamespace(stdout=b'{"access_token":"sample"}\nZotiBoxHTTP:200',returncode=0)
    monkeypatch.setattr(spotify_token.subprocess,"run",run)
    assert spotify_token.windows_token_post({"refresh_token":"private-token"}).json()=={"access_token":"sample"}
