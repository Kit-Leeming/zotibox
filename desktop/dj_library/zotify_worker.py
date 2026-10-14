"""JSON progress bridge; actual downloads stay in the unmodified ZahBoy fork."""
import argparse
import json
import sys


def main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--track")
    parser.add_argument("--authenticate", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(arguments)
    output = sys.stdout
    def emit(event, **values):
        output.write(json.dumps({"event": event, **values}) + "\n")
        output.flush()
    class Quiet:
        def write(self, *_):
            return 0
        def flush(self):
            pass
        def isatty(self):
            return False
    sys.stdout = sys.stderr = Quiet()
    try:
        from zotify.config import LoginHandler, Zotify
        from .zotify_oauth import LoopbackOAuth, LoginError
        from zotify.const import SCOPES
        from zotify.termoutput import Printer
        from librespot.audio.decoders import FormatOnlyAudioQuality
        from librespot.proto.Metadata_pb2 import AudioFile
        from pathlib import Path
        from zotify.__main__ import main as upstream_main
        if args.check:
            import zotify.api
            emit("ready", version=Zotify.VERSION)
            return 0
        login_errors = []
        class WorkerOAuth(LoopbackOAuth):
            def flow(self):
                try:
                    return super().flow()
                except LoginError as exc:
                    login_errors.append(str(exc))
                    raise
        def create_oauth(client_id):
            if login_errors:
                raise LoginError(login_errors[-1])
            redirect = f"http://127.0.0.1:4381/login"
            return WorkerOAuth(client_id, redirect, emit).set_scopes(SCOPES)
        LoginHandler.create_oauth = staticmethod(create_oauth)
        def login(cls, arguments):
            emit("status", message="Connecting to Zotify; checking saved login")
            cls.attempt_login(arguments)
            if not cls.login_success():
                raise LoginError(login_errors[-1] if login_errors else "Zotify could not sign in to Spotify. Reconnect in Setup and complete both browser login steps.")
            cls.save_credentials()
            emit("connected")
            return cls.SESSION
        LoginHandler.login = classmethod(login)
        # Retain both native stream credentials and developer OAuth separately.
        def save_credentials(cls):
            if cls.SESSION:
                Path(Zotify.CONFIG.get_credentials_location()).write_text(json.dumps(cls.SESSION.credentials()), encoding="utf-8")
            if cls.OAUTH:
                cls.OAUTH.save_creds(Zotify.CONFIG.get_api_credentials_location())
        LoginHandler.save_credentials = classmethod(save_credentials)
        original_pbar = Printer.pbar
        def pbar(*parameters, **keywords):
            bar = original_pbar(*parameters, **keywords)
            class Progress:
                written = 0
                def update(self, count=1):
                    result = bar.update(count)
                    self.written += count
                    if keywords.get("unit") == "B" and getattr(bar, "total", 0):
                        emit("progress", percent=min(99, self.written / bar.total * 100))
                    return result
                def __iter__(self):
                    return iter(bar)
                def __getattr__(self, name):
                    return getattr(bar, name)
            return Progress()
        Printer.pbar = staticmethod(pbar)
        original_get = FormatOnlyAudioQuality.get_file
        def get_file(self, files):
            result = original_get(self, files)
            if result:
                emit("source", format=AudioFile.Format.Name(result.format))
            return result
        FormatOnlyAudioQuality.get_file = get_file
        if args.authenticate:
            import zotify.app
            def login_complete(*_):
                Zotify.end()
                emit("authenticated")
            zotify.app.client = login_complete
        elif not args.track or len(args.track) != 22 or not args.track.isalnum():
            raise ValueError("Invalid Spotify track ID")
        sys.argv = ["zotify", "--config-location", args.config]
        if not args.authenticate:
            sys.argv += [f"https://open.spotify.com/track/{args.track}"]
        upstream_main()
        emit("complete")
        return 0
    except (Exception, SystemExit) as exc:
        # Avoid forwarding upstream token/credential exception text.
        message = str(exc) if type(exc).__name__ == "LoginError" else f"Zotify request failed ({type(exc).__name__}). Check login, Spotify access, and format/quality."
        emit("error", message=message)
        return 1
