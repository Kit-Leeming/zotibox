"""Verify HTTPS from the packaged runtime, with an optional live Spotify probe."""
import argparse
import ssl


def main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--spotify", action="store_true",
                        help="Check Spotify's public endpoints without credentials")
    args = parser.parse_args(arguments)
    from urllib3.util.ssl_ import create_urllib3_context
    context = create_urllib3_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("HTTPS certificate and hostname verification must be enabled")
    print("Packaged HTTPS context ready")
    if args.spotify:
        import requests
        with requests.Session() as session:
            for url, expected in (
                ("https://accounts.spotify.com/.well-known/openid-configuration", 200),
                ("https://api.spotify.com/v1/me", 401),
            ):
                with session.get(url, timeout=(8, 15), allow_redirects=False) as response:
                    if response.status_code != expected:
                        raise RuntimeError(f"Unexpected HTTP {response.status_code} from {url}")
                    print(f"Verified {url}: HTTP {response.status_code}")
    return 0
