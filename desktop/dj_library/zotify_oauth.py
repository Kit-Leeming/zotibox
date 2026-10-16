"""Loopback lifecycle adapter for the fork's OAuth/token implementation."""
import secrets
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from librespot.oauth import OAuth
import requests


class LoginError(RuntimeError):
    pass


class LoopbackOAuth(OAuth):
    def __init__(self, client_id, redirect, emit, timeout=180):
        super().__init__(client_id, redirect, None)
        self.redirect = redirect
        self.emit = emit
        self.login_timeout = timeout

    def refresh_token(self):
        if not self._OAuth__refresh_token or not self.has_token():
            raise LoginError('Saved Spotify authorization is incomplete. Start a fresh Zotify login in Settings → Downloads.')
        if self._OAuth__token_expires_at>datetime.now():return self
        try:
            response=requests.post('https://accounts.spotify.com/api/token',data={'grant_type':'refresh_token',
                'client_id':self._OAuth__client_id,'refresh_token':self._OAuth__refresh_token},timeout=30)
        except requests.RequestException as exc:
            raise LoginError('Could not reach Spotify to renew Zotify login. Check your connection and reconnect.') from exc
        if response.status_code!=200:
            raise LoginError(f'Spotify could not renew Zotify login (HTTP {response.status_code}). Start a fresh login in Settings → Downloads.')
        data=response.json()
        if not data.get('access_token'):raise LoginError('Spotify returned an incomplete login. Reconnect Zotify.')
        return self.ingest_token_response(data)

    def request_token(self):
        try:
            response = requests.post("https://accounts.spotify.com/api/token", data={
                "grant_type": "authorization_code", "client_id": self._OAuth__client_id,
                "redirect_uri": self.redirect, "code": self._OAuth__code,
                "code_verifier": self._OAuth__code_verifier}, timeout=30)
        except requests.RequestException as exc:
            raise LoginError("Could not reach Spotify to finish Zotify login. Check your connection and reconnect in Setup.") from exc
        if response.status_code != 200:
            raise LoginError(f"Spotify rejected the Zotify login (HTTP {response.status_code}). Start a fresh login in Setup.")
        data=response.json()
        if not data.get('access_token') or not data.get('refresh_token'):
            raise LoginError('Spotify returned an incomplete authorization. Start a fresh Zotify login.')
        return self.ingest_token_response(data)

    def flow(self):
        target = urlsplit(self.redirect)
        result = {}
        state = secrets.token_urlsafe(32)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                request = urlsplit(self.path)
                query = parse_qs(request.query)
                if request.path != target.path:
                    self.send_error(404)
                    return
                if query.get("state") != [state]:
                    self.send_error(400, "Invalid login state; start a fresh login in ZotiBox")
                    return
                if query.get("error"):
                    result["error"] = True
                    content = b"Spotify login was declined. Return to ZotiBox."
                elif query.get("code"):
                    result["code"] = query["code"][0]
                    content = b"Login received. You can return to ZotiBox."
                else:
                    self.send_error(400, "Missing login code")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *_):
                pass  # Callback query strings contain credentials.

        try:
            server = HTTPServer((target.hostname, target.port), Handler)
        except OSError as exc:
            raise LoginError(f"Zotify login port {target.port} is already in use. Pause the queue and close other ZotiBox login attempts, then reconnect.") from exc
        try:
            # Bind first: browser redirects can arrive immediately with saved consent.
            self.emit("auth_url", url=self.get_auth_url() + "&" + urlencode({"state": state}))
            deadline = time.monotonic() + self.login_timeout
            while not result:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LoginError("Zotify browser login timed out. Reconnect in Setup and complete the new browser login.")
                server.timeout = min(0.5, remaining)
                server.handle_request()
        finally:
            # handle_request() is synchronous; shutdown() would deadlock here.
            server.server_close()
        if result.get("error"):
            raise LoginError("Spotify login was declined. Reconnect in Setup to try again.")
        self.set_code(result["code"])
        self.emit("status", message="Zotify login received; connecting to Spotify")
        self.request_token()
        return self.get_credentials()
