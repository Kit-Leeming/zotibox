"""Spotify Web API + PKCE. Tokens remain local to this Windows user."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

REDIRECT_URI = "http://127.0.0.1:4381/callback"
SCOPES = "playlist-read-private playlist-read-collaborative"


class SpotifyError(RuntimeError):
    pass


def spotify_id(value, kind):
    match = re.fullmatch(r"[A-Za-z0-9]{22}", value.strip())
    if match:
        return match[0]
    match = re.fullmatch(rf"spotify:{kind}:([A-Za-z0-9]{{22}})", value.strip())
    if match:
        return match[1]
    parsed = urlparse(value.strip())
    match = re.fullmatch(rf"/(?:intl-[a-z]+/)?{kind}/([A-Za-z0-9]{{22}})/?", parsed.path)
    if parsed.scheme == "https" and parsed.hostname == "open.spotify.com" and match:
        return match[1]
    raise ValueError(f"Enter a Spotify {kind} URL or ID")


def dpapi(data, decrypt=False):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buffer)
    target = Blob()
    operation = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise SpotifyError("Windows could not protect the login data")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)


class TokenStore:
    def __init__(self, workspace):
        self.path = Path(workspace) / ".local" / "spotify.bin"

    def load(self):
        if not self.path.exists():
            return None
        try:
            return json.loads(dpapi(self.path.read_bytes(), True))
        except Exception:
            raise SpotifyError("Saved Spotify login is unavailable on this PC. Connect again.")

    def save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(dpapi(json.dumps(value).encode()))
        temporary.replace(self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)


def track_data(item):
    artists = item.get("artists") or []
    return {"id": item.get("id") or "", "title": item.get("name") or "Unavailable track",
            "artist": ", ".join(artist.get("name", "") for artist in artists),
            "album": (item.get("album") or {}).get("name", ""), "isrc": (item.get("external_ids") or {}).get("isrc", ""),
            "duration": float(item.get("duration_ms", 0)) / 1000,
            "playable": item.get("is_playable", True) and not item.get("is_local", False) and item.get("type", "track") == "track"}


class Spotify:
    def __init__(self, client_id, store, session=None):
        self.client_id = client_id.strip()
        self.store = store
        self.session = session or requests.Session()
        self.token_lock = threading.Lock()

    def authenticate(self, cancel, progress=lambda *args: None, opener=webbrowser.open):
        if not re.fullmatch(r"[a-fA-F0-9]{32}", self.client_id):
            raise SpotifyError("Enter the 32-character Client ID from Spotify Developer Dashboard")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)
        result = {}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                valid = parsed.path == "/callback" and params.get("state", [""])[0] == state
                if valid:
                    result.update({key: value[0] for key, value in params.items()})
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h2>ZotiBox</h2><p>You can return to the application.</p>" if valid else b"Invalid login callback")
            def log_message(self, *_):
                pass
        try:
            server = HTTPServer(("127.0.0.1", 4381), Handler)
        except OSError as exc:
            raise SpotifyError("Login port 4381 is busy. Close another Spotify/Zotify login and retry.") from exc
        server.timeout = 0.25
        query = {"client_id": self.client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
                 "scope": SCOPES, "state": state, "code_challenge_method": "S256", "code_challenge": challenge}
        progress(0, "Complete Spotify login in your browser")
        try:
            opener("https://accounts.spotify.com/authorize?" + urlencode(query))
            deadline = time.monotonic() + 180
            while not result and not cancel.is_set() and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if cancel.is_set():
            raise InterruptedError("Spotify login cancelled")
        if not result.get("code"):
            raise SpotifyError("Spotify login was declined or timed out. Check the registered redirect URI.")
        response = self.session.post("https://accounts.spotify.com/api/token", data={"grant_type": "authorization_code", "code": result["code"],
                                    "redirect_uri": REDIRECT_URI, "client_id": self.client_id, "code_verifier": verifier}, timeout=30)
        self.save_response(response)
        return self.request("me").get("display_name", "Connected")

    def save_response(self, response, previous=None):
        if response.status_code != 200:
            raise SpotifyError("Spotify authentication failed. Check your Client ID and reconnect.")
        value = response.json()
        if not value.get("access_token"):
            raise SpotifyError("Spotify returned an incomplete login response")
        value["refresh_token"] = value.get("refresh_token") or (previous or {}).get("refresh_token")
        value["expires_at"] = time.time() + value.get("expires_in", 3600) - 60
        value["client_id"] = self.client_id
        self.store.save(value)
        return value

    def access_token(self, force=False):
        with self.token_lock:
            token = self.store.load()
            if not token or token.get("client_id") != self.client_id:
                raise SpotifyError("Connect Spotify in Settings / Setup first")
            if force or token["expires_at"] <= time.time():
                response = self.session.post("https://accounts.spotify.com/api/token", data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": self.client_id}, timeout=30)
                token = self.save_response(response, token)
            return token["access_token"]

    def request(self, path, params=None):
        if path.startswith("https://api.spotify.com/v1/"):
            url = path
        elif "://" in path:
            raise SpotifyError("Unexpected API pagination URL")
        else:
            url = "https://api.spotify.com/v1/" + path.lstrip("/")
        for attempt in range(2):
            response = self.session.get(url, params=params, headers={"Authorization": "Bearer " + self.access_token(force=bool(attempt))}, timeout=30)
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code == 429:
                raise SpotifyError(f"Spotify request limit reached. Retry after {response.headers.get('Retry-After', 'a short wait')} seconds.")
            if response.status_code == 403:
                raise SpotifyError("Spotify denied access. Check Premium, the app allowlist, and playlist ownership/collaboration.")
            if response.status_code != 200:
                raise SpotifyError(f"Spotify request failed ({response.status_code}); existing playlists were kept")
            return response.json()
        raise SpotifyError("Spotify session expired. Reconnect in Setup.")

    def pages(self, path, params=None, cancel=None):
        items, visited = [], set()
        while path:
            if cancel and cancel.is_set():
                raise InterruptedError("Spotify fetch cancelled; existing playlist kept")
            if path in visited:
                raise SpotifyError("Repeated Spotify pagination; playlist was not updated")
            visited.add(path)
            page = self.request(path, params)
            if not isinstance(page.get("items"), list):
                raise SpotifyError("Spotify did not return playlist contents; existing playlist kept")
            items.extend(page["items"])
            path, params = page.get("next"), None
        return items

    def playlists(self, cancel=None):
        current_user=self.request("me").get("id")
        return [playlist for playlist in self.pages("me/playlists", {"limit": 50}, cancel)
                if playlist and (playlist.get("collaborative") or (playlist.get("owner") or {}).get("id")==current_user)]

    def snapshot(self, playlist_id, cancel=None):
        for _ in range(2):
            before = self.request(f"playlists/{playlist_id}")
            if not isinstance(before.get("items") or before.get("tracks"), dict):
                raise SpotifyError("Spotify does not expose this playlist's contents. Use an owned/collaborative playlist.")
            items = self.pages(f"playlists/{playlist_id}/items", {"limit": 50}, cancel)
            after = self.request(f"playlists/{playlist_id}")
            if before.get("snapshot_id") != after.get("snapshot_id"):
                continue
            total = (before.get("items") or before.get("tracks"))["total"]
            if len(items) != total:
                raise SpotifyError("Incomplete Spotify playlist; existing playlist kept")
            tracks = []
            for index, entry in enumerate(items):
                item = entry.get("item") or entry.get("track") or {}
                track = track_data(item)
                if not track["id"]:
                    track["id"] = f"unavailable-{playlist_id}-{index}"
                    track["playable"] = False
                tracks.append(track)
            return {"id": playlist_id, "name": before["name"], "snapshot": before["snapshot_id"], "tracks": tracks, "complete": True}
        raise SpotifyError("Playlist changed during retrieval; retry when editing has finished")

    def search(self, query, kind="track"):
        data = self.request("search", {"q": query, "type": kind, "limit": 10})
        return data.get(kind + "s", {}).get("items", [])

    def tracks_for_url(self, value, cancel=None):
        for kind in ["track", "album", "playlist"]:
            try:
                item_id = spotify_id(value, kind)
            except ValueError:
                continue
            if kind == "track":
                return [track_data(self.request(f"tracks/{item_id}"))]
            if kind == "playlist":
                snapshot=self.snapshot(item_id,cancel)
                return [dict(track,_download_source={'key':'spotify:playlist:'+item_id,'name':snapshot['name'],'position':i}) for i,track in enumerate(snapshot['tracks'])]
            album = self.request(f"albums/{item_id}")
            items = self.pages(f"albums/{item_id}/tracks", {"limit": 50}, cancel)
            # Fetch full track objects individually for ISRC and release identity.
            tracks = []
            for item in items:
                if cancel and cancel.is_set():
                    raise InterruptedError("Album fetch cancelled")
                track = track_data(self.request(f"tracks/{item['id']}"))
                track["album"] = album["name"]
                track['_download_source']={'key':'spotify:album:'+item_id,'name':album['name'],'position':len(tracks)}
                tracks.append(track)
            return tracks
        raise ValueError("Enter a Spotify track, album or playlist URL")
