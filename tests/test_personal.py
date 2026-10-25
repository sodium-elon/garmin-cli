import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from garmin_cli import client


class PersonalTests(unittest.TestCase):
    def test_legacy_passwords_cannot_replace_a_personal_key(self):
        from garmin_cli.personal import AuthRequired
        with patch.dict(os.environ, {'GARMIN_EMAIL': 'old', 'GARMIN_PASSWORD': 'secret'}, clear=True):
            with self.assertRaises(AuthRequired):
                client.connect()

    def test_key_alias_is_supported_but_canonical_key_wins(self):
        with patch.dict(os.environ, {'GARMIN_TOKEN': 'alias'}, clear=True):
            self.assertEqual('alias', client.connect().key)
            os.environ['GARMIN_PERSONAL_API_KEY'] = 'canonical'
            self.assertEqual('canonical', client.connect().key)

    def test_connect_uses_key_without_legacy_session(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'GARMIN_PERSONAL_API_KEY': 'test-key', 'GARMIN_CLI_TOKENSTORE': directory,
        }, clear=True):
            garmin = client.connect()
            self.assertEqual('PersonalClient', type(garmin).__name__)


class Response(io.BytesIO):
    status = 200


def response(body):
    return Response(body)


class CliPersonalTests(unittest.TestCase):
    def test_unsupported_client_helpers_never_request_legacy_routes(self):
        from unittest.mock import Mock
        from garmin_cli.personal import UnsupportedCapability
        garmin = Mock()
        for helper in (client.get_activity_weather, client.get_activity_polyline):
            with self.assertRaises(UnsupportedCapability):
                helper(garmin, '42')
        garmin.connectapi.assert_not_called()

    def test_profile_uses_personal_settings_route(self):
        from unittest.mock import Mock
        garmin = Mock()
        garmin.connectapi.return_value = {'displayName': 'runner'}
        self.assertEqual({'displayName': 'runner'}, client.get_user_profile(garmin))
        garmin.connectapi.assert_called_once_with('/userprofile-service/userprofile/settings', params=None)

    def test_safe_api_status_message_is_preserved(self):
        from garmin_cli.personal import SafeApiError
        from garmin_cli.main import main
        from contextlib import redirect_stderr
        err = io.StringIO()
        with patch('garmin_cli.main.connect', side_effect=SafeApiError('Personal API request failed (HTTP 429)')), redirect_stderr(err):
            self.assertEqual(1, main(['--verbose', 'profile']))
        self.assertIn('HTTP 429', err.getvalue())

    def test_fit_fragments_share_one_download_and_decode_across_threads(self):
        from concurrent.futures import ThreadPoolExecutor
        from garmin_cli.personal import PersonalClient
        fragments = {'splits': {'lapDTOs': []}, 'hrZones': []}
        with tempfile.TemporaryDirectory() as directory:
            garmin = PersonalClient('key', Path(directory))
            with patch.object(garmin, 'download_activity', return_value=b'archive') as download, patch('garmin_cli.fit.decode_activity_fit', return_value=fragments) as decode:
                with ThreadPoolExecutor(2) as pool:
                    a = pool.submit(client.get_activity_splits, garmin, '42')
                    b = pool.submit(client.get_activity_hr_zones, garmin, '42')
                    self.assertEqual(fragments['splits'], a.result())
                    self.assertEqual(fragments['hrZones'], b.result())
                download.assert_called_once_with('42')
                decode.assert_called_once_with(b'archive', '42')

    def test_runtime_has_no_legacy_auth_imports_or_mfa_implementation(self):
        import ast
        import garmin_cli.main as module
        root = Path(module.__file__).parent
        for filename in ('main.py', 'client.py', 'mfa.py'):
            source = (root / filename).read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(node.module, ('garminconnect', 'garth', 'imaplib'))
                if isinstance(node, ast.Import):
                    self.assertFalse(any(n.name in ('garminconnect','garth','imaplib') for n in node.names))

    def test_verbose_never_prints_raw_exception_or_traceback(self):
        from contextlib import redirect_stderr
        from garmin_cli.main import main
        err = io.StringIO()
        with patch('garmin_cli.main.connect', side_effect=RuntimeError('SECRET')), redirect_stderr(err):
            self.assertEqual(1, main(['--verbose', 'profile']))
        self.assertNotIn('SECRET', err.getvalue())
        self.assertNotIn('Traceback', err.getvalue())


class TransportTests(unittest.TestCase):
    def test_non_auth_http_errors_preserve_only_safe_status(self):
        from urllib.error import HTTPError
        from garmin_cli.personal import PersonalClient, SafeApiError
        def send(request, timeout):
            raise HTTPError(request.full_url, 429, 'SECRET', {}, io.BytesIO(b'SECRET'))
        with tempfile.TemporaryDirectory() as directory:
            garmin = PersonalClient('SECRET', Path(directory), transport=send)
            with self.assertRaisesRegex(SafeApiError, r'^Personal API token exchange failed \(HTTP 429\)$'):
                garmin.authenticate()
            with patch.object(garmin, 'authenticate', return_value='private-token'):
                with self.assertRaisesRegex(SafeApiError, r'^Personal API request failed \(HTTP 429\)$'):
                    garmin.connectapi('/userprofile-service/userprofile/settings')

    def test_private_persistent_cache_reused_and_invalidated_by_key_or_age(self):
        from garmin_cli.personal import PersonalClient
        calls = []
        def send(request, timeout):
            calls.append(request)
            return response(b'private-bearer')
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / 'tokens'
            with patch('garmin_cli.personal.time.time', return_value=100):
                PersonalClient('key', store, transport=send).authenticate()
                PersonalClient('key', store, transport=send).authenticate()
            self.assertEqual(1, len(calls))
            self.assertEqual(0o700, stat.S_IMODE(store.stat().st_mode))
            cache = store / 'personal-token.json'
            self.assertEqual(0o600, stat.S_IMODE(cache.stat().st_mode))
            self.assertNotIn('key', cache.read_text())
            with patch('garmin_cli.personal.time.time', return_value=100):
                PersonalClient('different', store, transport=send).authenticate()
            self.assertEqual(2, len(calls))
            with patch('garmin_cli.personal.time.time', return_value=90000):
                PersonalClient('different', store, transport=send).authenticate()
            self.assertEqual(3, len(calls))

    def test_foreign_urls_and_redirects_never_receive_credentials(self):
        from garmin_cli.personal import PersonalClient
        calls = []
        def send(request, timeout):
            calls.append(request)
            return response(b'token')
        with tempfile.TemporaryDirectory() as directory:
            garmin = PersonalClient('secret', Path(directory), transport=send)
            for path in ('https://evil.invalid/x', '//evil.invalid/x', '/a/../x', '/a?x=y', '/a\\\\b'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    garmin.connectapi(path)
            self.assertEqual([], calls)
            from garmin_cli.personal import NoRedirect
            self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))

    def test_token_exchange_errors_are_redacted(self):
        from urllib.error import HTTPError
        from garmin_cli.personal import PersonalClient, AuthRequired
        def send(request, timeout):
            raise HTTPError(request.full_url, 403, 'SECRET', {}, io.BytesIO(b'SECRET'))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AuthRequired) as caught:
                PersonalClient('SECRET', Path(directory), transport=send).authenticate()
            self.assertNotIn('SECRET', str(caught.exception))

    def test_request_401_renews_once_then_fails_without_secrets(self):
        from urllib.error import HTTPError
        from garmin_cli.personal import PersonalClient, AuthRequired
        calls = []
        def send(request, timeout):
            calls.append(request)
            if request.get_method() == 'POST':
                return response(('token-' + str(len(calls))).encode())
            raise HTTPError(request.full_url, 401, 'key SECRET token', {}, None)
        with tempfile.TemporaryDirectory() as directory:
            garmin = PersonalClient('SECRET', Path(directory), transport=send)
            with self.assertRaises(AuthRequired) as caught:
                garmin.connectapi('/userprofile-service/userprofile/settings')
            self.assertNotIn('SECRET', str(caught.exception))
            self.assertEqual(['POST','GET','POST','GET'], [r.get_method() for r in calls])

    def test_activity_download_uses_personal_export_bytes(self):
        from garmin_cli.personal import PersonalClient
        calls = []
        def send(request, timeout):
            calls.append(request)
            return response(b'token' if request.get_method() == 'POST' else b'PK-ZIP')
        with tempfile.TemporaryDirectory() as directory:
            garmin = PersonalClient('key', Path(directory), transport=send)
            self.assertEqual(b'PK-ZIP', garmin.download_activity('42'))
            self.assertEqual('https://personal.connectapi.garmin.com/download-service/files/activity/42', calls[-1].full_url)

    def test_raw_token_exchange_and_authenticated_get(self):
        from garmin_cli.personal import PersonalClient
        calls = []
        def send(request, timeout):
            calls.append(request)
            return response(b'raw-bearer' if request.get_method() == 'POST' else b'{"id":42}')
        with tempfile.TemporaryDirectory() as directory:
            garmin = PersonalClient('key', Path(directory), transport=send)
            self.assertEqual({'id':42}, garmin.connectapi('/userprofile-service/userprofile/settings'))
            self.assertEqual('ApiKey key', calls[0].get_header('Authorization'))
            self.assertIsNone(calls[0].get_header('Accept'))
            self.assertEqual('Bearer raw-bearer', calls[1].get_header('Authorization'))
            self.assertEqual('https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token', calls[0].full_url)


if __name__ == '__main__':
    unittest.main()
