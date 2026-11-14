"""Retry interrupted reads without exposing raw transport details in the UI."""
import requests


def send(call, service, error_type=ValueError, *, retry=False, **options):
    attempts = 2 if retry else 1
    for attempt in range(attempts):
        try:
            return call(**options)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
            # A certificate or proxy configuration error cannot be fixed by replaying.
            if attempt + 1 < attempts and not isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.ProxyError)):
                continue
            raise error_type(f"Could not connect to {service}. Check your connection or proxy settings, then retry.") from exc
        except requests.RequestException as exc:
            raise error_type(f"The request to {service} failed. Try again in a moment.") from exc
