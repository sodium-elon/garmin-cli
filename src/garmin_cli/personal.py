"""Garmin Personal API transport; no browser/password authentication."""
from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
import threading
import time
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import unquote, urlencode, urlsplit

BASE_URL = 'https://personal.connectapi.garmin.com'
TOKEN_PATH = '/personalapitoken-service/personal-api/personal-api-key/token'


class SafeApiError(RuntimeError):
    """A diagnostic constructed locally, safe to print without remote details."""


class UnsupportedCapability(SafeApiError):
    """The Personal API does not expose this legacy capability."""


class AuthRequired(SafeApiError):
    """A Personal API key or renewed authorization is required."""


class NoRedirect(HTTPRedirectHandler):
    """Never forward credential-bearing requests, even to same-host redirects."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PersonalClient:
    def __init__(self, key: str, tokenstore: Path, *, transport=None):
        self.key = key
        self.tokenstore = Path(tokenstore)
        self._transport = transport or build_opener(NoRedirect()).open
        self._token = None
        self._expires_at = 0
        self._lock = threading.RLock()
        self._fit_cache = {}

    def activity_fit_fragments(self, activity_id):
        """Download/decode once per activity, including concurrent stage callers."""
        from .fit import decode_activity_fit

        activity_id = str(int(activity_id))
        with self._lock:
            if activity_id not in self._fit_cache:
                self._fit_cache[activity_id] = decode_activity_fit(
                    self.download_activity(activity_id), activity_id
                )
            return self._fit_cache[activity_id]

    def authenticate(self, rejected_token=None):
        """Reuse a private cache or exchange the key, serializing across processes."""
        with self._lock:
            self.tokenstore.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.tokenstore.chmod(0o700)
            lock_path = self.tokenstore / 'personal-token.lock'
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as lock:
                os.fchmod(lock.fileno(), 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                cache_path = self.tokenstore / 'personal-token.json'
                fingerprint = hashlib.sha256(self.key.encode()).hexdigest()
                try:
                    fd = os.open(cache_path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd) as cached:
                        os.fchmod(cached.fileno(), 0o600)
                        cache = json.load(cached)
                    token = cache['token']
                    expires = cache['expires_at']
                    valid = (cache['fingerprint'] == fingerprint and
                             isinstance(token, str) and token and
                             isinstance(expires, (int, float)) and
                             time.time() + 60 < expires and token != rejected_token)
                except (OSError, ValueError, KeyError, TypeError):
                    valid = False
                if not valid:
                    request = Request(BASE_URL + TOKEN_PATH, data=b'', headers={'Authorization': 'ApiKey ' + self.key}, method='POST')
                    try:
                        with self._transport(request, timeout=30) as response:
                            token = response.read().decode().strip()
                    except HTTPError as exc:
                        status = exc.code
                        exc.close()
                        if status in (401, 403):
                            raise AuthRequired('Personal API key rejected; check key permissions and expiry') from None
                        raise SafeApiError(f'Personal API token exchange failed (HTTP {status})') from None
                    except (URLError, OSError, ValueError):
                        raise SafeApiError('Personal API token exchange failed') from None
                    if not token or any(c.isspace() for c in token):
                        raise AuthRequired('Personal API returned an invalid token')
                    expires = time.time() + 86400
                    fd, temporary = tempfile.mkstemp(prefix='.personal-token-', dir=self.tokenstore)
                    try:
                        with os.fdopen(fd, 'w') as handle:
                            json.dump({'token': token, 'fingerprint': fingerprint, 'expires_at': expires}, handle)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temporary, cache_path)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                self._token, self._expires_at = token, expires
                return token

    def download_activity(self, activity_id, dl_fmt=None):
        return self._get(f'/download-service/files/activity/{int(activity_id)}', binary=True)

    def connectapi(self, path: str, params=None):
        return self._get(path, params)

    def _get(self, path: str, params=None, *, binary=False):
        decoded = unquote(path)
        parsed = urlsplit(decoded)
        if (parsed.scheme or parsed.netloc or decoded.startswith('//') or
                any(c in decoded for c in ('?', '#', '\\')) or
                any(ord(c) < 32 for c in decoded) or
                any(part in ('.', '..') for part in decoded.split('/'))):
            raise ValueError('Only relative Personal API service paths are allowed')
        token = self.authenticate()
        url = BASE_URL + '/' + path.lstrip('/')
        if params:
            url += '?' + urlencode(params)
        for attempt in range(2):
            request = Request(url, headers={'Authorization': 'Bearer ' + token})
            try:
                with self._transport(request, timeout=30) as response:
                    body = response.read()
                if binary:
                    return body
                return json.loads(body) if body else None
            except HTTPError as exc:
                status = exc.code
                exc.close()
                if status == 401 and attempt == 0:
                    token = self.authenticate(rejected_token=token)
                    continue
                if status in (401, 403):
                    raise AuthRequired('Personal API authorization rejected; check key permissions and expiry') from None
                raise SafeApiError(f'Personal API request failed (HTTP {status})') from None
            except (URLError, OSError, ValueError):
                raise SafeApiError('Personal API request failed') from None
