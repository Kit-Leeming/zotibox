"""Spotify OAuth form exchange and a Windows fallback for transport failures."""
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode

import requests

from .diagnostics import error_fields


TOKEN_URL = "https://accounts.spotify.com/api/token"


class NativeTransportError(OSError):
    def __init__(self, exit_code):
        super().__init__("The Windows HTTPS client did not complete the token exchange")
        self.exit_code = exit_code


def windows_token_post(data):
    # Use the OS copy, disable curl config files and redirects. The form (including
    # login secrets) is sent over stdin, never in arguments, logs or temporary files.
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    marker = b"\nZotiBoxHTTP:"
    result = subprocess.run(
        [str(executable), "--disable", "--silent", "--show-error", "--proto", "=https",
         "--connect-timeout", "8", "--max-time", "16", "--max-filesize", "1048576",
         "--data-binary", "@-", "--write-out", marker.decode("ascii") + "%{http_code}", TOKEN_URL],
        input=urlencode(data).encode("ascii"), capture_output=True, timeout=18,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    body, _, status = result.stdout.rpartition(marker)
    if result.returncode or len(status)!=3 or not status.isdigit() or not 100 <= int(status) <= 599:
        raise NativeTransportError(result.returncode)
    response = requests.Response()
    response.status_code = int(status)
    response.url = TOKEN_URL
    response.encoding = "utf-8"
    response._content = body
    return response


def token_post(session, data, operation, error_type, record, *, fallback=False):
    try:
        response = session.post(TOKEN_URL, data=data, timeout=(8, 20), allow_redirects=False)
    except requests.RequestException as original:
        record(operation, "transport failed", error=original)
        chain = error_fields(original)
        missing = any(e["type"] == "FileNotFoundError" for e in chain)
        config_error = isinstance(original, requests.exceptions.ProxyError) or bool(
            getattr(session, "proxies", None) or requests.utils.get_environ_proxies(TOKEN_URL)
        )
        can_fallback = missing or isinstance(original, (requests.ConnectTimeout, requests.exceptions.SSLError))
        if not (fallback and os.name == "nt" and can_fallback and not config_error):
            raise error_type("Could not complete the Spotify token request. Try again, or copy diagnostics from Settings → Spotify account.") from original
        try:
            response = windows_token_post(data)
        except (OSError, subprocess.SubprocessError) as exc:
            record(operation, "Windows HTTPS failed", error=exc)
            raise error_type("Both connection methods failed to reach Spotify login. Copy diagnostics from Settings → Spotify account.") from exc
        record(operation, "Windows HTTPS response", status=response.status_code)
    else:
        record(operation, "response", status=response.status_code)
    return response
