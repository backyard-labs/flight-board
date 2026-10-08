import json
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/"
    "opensky-network/protocol/openid-connect/token"
)

_token = None
_expires_at = 0


def get_token():
    global _token, _expires_at

    if _token and time.monotonic() < _expires_at:
        return _token

    with open("/run/secrets/opensky.json", encoding="utf-8-sig") as file:
        credentials = json.load(file)

    client_id = credentials.get("clientId") or credentials.get("client_id")
    client_secret = (
        credentials.get("clientSecret") or credentials.get("client_secret")
    )
    if not client_id or not client_secret:
        raise ValueError("Credentials file is missing client ID or secret")

    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")

    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.load(response)

    token = result.get("access_token")
    lifetime = float(result.get("expires_in", 300))
    if not isinstance(token, str) or not token or lifetime <= 0:
        raise ValueError("Authentication returned an invalid token response")

    _token = token
    _expires_at = time.monotonic() + max(1, lifetime - 60)
    print("OpenSky authentication successful; token cached", flush=True)
    return _token


def open_authenticated(url, timeout=25):
    global _token, _expires_at

    # Retry once with a fresh token if the data API rejects the old one.
    for attempt in range(2):
        token = get_token()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "LocalFlightBoard/0.2",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                _token = None
                _expires_at = 0
                if attempt == 0:
                    exc.close()
                    continue
            raise