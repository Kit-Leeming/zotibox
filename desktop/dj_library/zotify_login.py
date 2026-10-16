"""Keep failed or incomplete OAuth objects out of the downloader's login state."""
from .zotify_oauth import LoginError


def install_login_adapter(handler,zotify,create_oauth,emit):
    def attempt(cls,args):
        cls.OAUTH=None
        if not cls.SESSION:
            try:
                creds=cls.get_creds_from_file(zotify.CONFIG.get_credentials_location())
                if creds and creds.get('credentials'):cls.login5_cred_login(creds)
            except Exception:
                emit('status',message='Saved streaming login needs renewal')
        if not cls.SESSION:
            cls.login5_cred_login(cls.get_login5_from_args(args))
        if not cls.SESSION:
            emit('status',message='Opening Spotify streaming login')
            cls.login5_link_login()
        if not zotify.CONFIG.permit_client_api():return
        client_id=zotify.CONFIG.get_api_client_id()
        try:
            creds=cls.get_creds_from_file(zotify.CONFIG.get_api_credentials_location())
            if not creds or not creds.get('access_token') or not creds.get('refresh_token') or creds.get('client_id')!=client_id:
                raise LoginError('Saved developer login is incomplete or belongs to another Client ID')
            oauth=create_oauth(client_id).ingest_token_response(creds)
            oauth.token() # Expired/revoked tokens must fail before assigning the logged-in state.
        except Exception:
            emit('status',message='Opening Spotify developer login; the saved authorization needs renewal')
            oauth=create_oauth(client_id);oauth.flow();oauth.token()
        cls.OAUTH=oauth
    handler.attempt_login=classmethod(attempt)


def failure_message(exc):
    if isinstance(exc,LoginError):return str(exc)
    text=str(exc).casefold()
    if any(word in text for word in ['token','authenticated','credential','401']):
        return 'Spotify authorization is no longer valid. In Settings → Downloads, use Fresh Zotify login and complete the browser login.'
    if any(word in text for word in ['apresolve','connection','timed out','receive packet','bad mac','session is closed']):
        return 'The Spotify streaming connection failed. Check your connection, reconnect Zotify, then retry the failed tracks.'
    if '403' in text:return 'Spotify denied access. Check the developer app user allowlist and your account access, then reconnect Zotify.'
    if '429' in text:return 'Spotify is rate limiting requests. Pause the queue and retry later.'
    if any(word in text for word in ['ffmpeg','encoder','codec','format','audio key']):
        return 'Spotify could not provide or convert the requested audio. Try another format/quality in Downloads, then retry.'
    return f'Zotify could not complete the request ({type(exc).__name__}). Reconnect Zotify and retry; a safe diagnostic is saved in Settings → Downloads.'
