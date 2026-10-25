from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime as RealDatetime
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from garmin_cli.client import get_display_name
from garmin_cli.main import _date, main


class DateContractTests(unittest.TestCase):
    def test_default_date_uses_utc_like_mcp(self):
        fake = Mock(wraps=RealDatetime)
        fake.now.return_value = RealDatetime.fromisoformat('2030-01-02T00:30:00+00:00')
        with patch('garmin_cli.main.datetime', fake):
            self.assertEqual('2030-01-02', _date(None))

    def test_range_dates_fail_as_usage_errors_before_connecting(self):
        for argv in (
            ['fitness', 'stats', 'not-a-date', '2026-08-01'],
            ['weight', '--start', '2026-07-01', '--end', 'not-a-date'],
            ['activities', 'list', '--start-date', 'not-a-date'],
            ['activities', 'list', '--start-date', '20260801'],
            ['activities', 'list', '--start-date', '2026-08-02', '--end-date', '2026-08-01'],
        ):
            with self.subTest(argv=argv), patch('garmin_cli.main.connect') as connect, redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(argv))
                connect.assert_not_called()


class DisplayNameTests(unittest.TestCase):
    def test_display_name_uses_settings_endpoint(self):
        garmin = Mock()
        with patch('garmin_cli.client.raw_get', return_value={'displayName': 'runner'}) as raw:
            self.assertEqual('runner', get_display_name(garmin))
        raw.assert_called_once_with(garmin, 'userprofile-service/userprofile/settings')

    def test_check_session_preserves_profile_envelope(self):
        from garmin_cli.client import check_session
        with patch('garmin_cli.client.connect'), patch('garmin_cli.client.get_user_profile', return_value={'id': 42}):
            self.assertEqual({'status': 'ok', 'profile': {'id': 42}}, check_session())


class ReadCommandTests(unittest.TestCase):
    def test_supported_read_routes_and_parameters(self):
        cases = [
            (['fitness', 'hr-zones'], 'biometric-service/heartRateZones/v2', None),
            (['fitness', 'sleep-stats', '2026-07-01', '2026-07-28'], 'sleep-service/sleep/dailySleepSummariesByDate', {'startDate': '2026-07-01', 'endDate': '2026-07-28'}),
            (['fitness', 'hydration', '--date', '2026-08-01'], 'usersummary-service/stats/hydration/daily/2026-08-01/2026-08-01', None),
            (['daily', 'summary', '--date', '2026-08-01'], 'usersummary-service/usersummary/daily', {'calendarDate': '2026-08-01'}),
            (['daily', 'heart-rate', '--date', '2026-08-01'], 'wellness-service/wellness/dailyHeartRate', {'date': '2026-08-01'}),
            (['daily', 'stress', '--date', '2026-08-01'], 'usersummary-service/stats/stress/daily/2026-08-01/2026-08-01', None),
            (['wellness', 'hrv', '--date', '2026-08-01'], 'hrv-service/hrv/2026-08-01', None),
            (['wellness', 'body-battery', '--date', '2026-08-01'], 'wellness-service/wellness/bodyBattery/reports/daily', {'startDate': '2026-08-01', 'endDate': '2026-08-01'}),
            (['weight', '--start', '2026-07-01', '--end', '2026-08-01'], 'weight-service/weight/all/2026-07-01/2026-08-01', None),
        ]
        for argv, path, params in cases:
            with self.subTest(argv=argv), patch('garmin_cli.main.connect'), patch('garmin_cli.main.raw_get', return_value=[]) as raw, redirect_stdout(io.StringIO()) as out:
                self.assertEqual(0, main(argv))
                self.assertEqual([], json.loads(out.getvalue()))
                if params is None:
                    raw.assert_called_once_with(ANY, path)
                else:
                    raw.assert_called_once_with(ANY, path, params)

    def test_sleep_wraps_direct_dto_for_existing_consumer(self):
        with patch('garmin_cli.main.connect'), patch('garmin_cli.main.raw_get', return_value={'sleepTimeSeconds': 100}) as raw, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, main(['wellness', 'sleep', '--date', '2026-08-01']))
        self.assertEqual({'dailySleepDTO': {'sleepTimeSeconds': 100}}, json.loads(out.getvalue()))
        raw.assert_called_once_with(ANY, 'sleep-service/sleep/dailySleep', {'date': '2026-08-01'})

    def test_activity_search_filters_preserve_pagination(self):
        garmin = Mock()
        garmin.connectapi.return_value = []
        with patch('garmin_cli.main.connect', return_value=garmin), redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(['activities', 'list', '--start', '5', '--limit', '100', '--start-date', '2026-08-01', '--end-date', '2026-08-02', '--activity-type', 'running']))
        garmin.connectapi.assert_called_once_with('/activitylist-service/activities/search/activities', params={'start': 5, 'limit': 100, 'startDate': '2026-08-01', 'endDate': '2026-08-02', 'activityType': 'running'})

    def test_calendar_month_is_restricted_to_zero_based_range(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(['fitness', 'calendar', '2026', '12'])
        self.assertEqual(2, raised.exception.code)

    def test_unsupported_commands_fail_without_connecting_or_legacy_requests(self):
        cases = [
            ['fitness', 'personal-records'], ['fitness', 'stats', '2026-01-01', '2026-02-01', '--aggregation', 'weekly', '--metric', 'distance'],
            ['fitness', 'vo2max'], ['fitness', 'training-readiness'], ['fitness', 'calendar', '2026', '7'],
            ['fitness', 'goals'], ['fitness', 'badges'], ['fitness', 'badge-leaderboard'], ['fitness', 'power-zones'],
            ['workouts', 'list'], ['workouts', 'get', '42'], ['workouts', 'download-fit', '42'],
            ['workouts', 'create', '--json', '{"workoutName":"Inline"}'], ['workouts', 'create', '--file', '/nonexistent.json'],
            ['workouts', 'schedule', '42', '2026-08-22'], ['workouts', 'delete', '42'],
            ['activities', 'weather', '42'], ['activities', 'polyline', '42'],
            *[['daily', command] for command in ('intensity', 'movement', 'respiration', 'chart')],
        ]
        for argv in cases:
            with self.subTest(argv=argv), patch('garmin_cli.main.connect') as connect, redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(1, main(argv))
                self.assertEqual('', out.getvalue())
                self.assertIn('unsupported by Garmin Personal API', err.getvalue())
                connect.assert_not_called()


class DownloadCommandTests(unittest.TestCase):
    def test_malformed_env_file_fails_without_raw_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'bad.env'
            source.write_bytes(b'GARMIN_PERSONAL_API_KEY=SECRET\xff')
            with redirect_stderr(io.StringIO()) as err, patch('garmin_cli.main.connect') as connect:
                self.assertEqual(2, main(['--verbose', '--env-file', str(source), 'profile']))
            self.assertNotIn('SECRET', err.getvalue())
            self.assertNotIn('Traceback', err.getvalue())
            connect.assert_not_called()

    def test_activity_fit_download_extracts_original_zip(self):
        from test_fit import synthetic_fit
        payload = synthetic_fit([(18, [])])
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('original.fit', payload)
        garmin = Mock()
        garmin.download_activity.return_value = archive.getvalue()
        with tempfile.TemporaryDirectory() as directory, patch('garmin_cli.main.connect', return_value=garmin), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, main(['activities', 'download-fit', '99', '--output-dir', directory]))
            result = json.loads(out.getvalue())
            self.assertEqual(payload, Path(result['path']).read_bytes())
            self.assertEqual(len(payload), result['bytes'])
            self.assertEqual('99.fit', Path(result['path']).name)
            self.assertEqual(0o600, Path(result['path']).stat().st_mode & 0o777)

    def test_activity_fit_download_accepts_raw_fit_without_changing_bytes(self):
        from test_fit import synthetic_fit
        payload = synthetic_fit([(18, [])])
        garmin = Mock()
        garmin.download_activity.return_value = payload
        with tempfile.TemporaryDirectory() as directory, patch('garmin_cli.main.connect', return_value=garmin), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, main(['activities', 'download-fit', '99', '--output-dir', directory]))
            result = json.loads(out.getvalue())
            self.assertEqual(payload, Path(result['path']).read_bytes())
            self.assertEqual(0o600, Path(result['path']).stat().st_mode & 0o777)

    def test_activity_fit_download_rejects_unsafe_paths_and_resource_limits(self):
        from test_fit import synthetic_fit
        payload = synthetic_fit([(18, [])])
        for name, limit, value in [
            ('../activity.fit', 'MAX_ZIP_MEMBERS', 32),
            ('/activity.fit', 'MAX_ZIP_MEMBERS', 32),
            ('..\\activity.fit', 'MAX_ZIP_MEMBERS', 32),
            ('activity.fit', 'MAX_ZIP_MEMBERS', 0),
            ('activity.fit', 'MAX_FIT_BYTES', len(payload) - 1),
            ('activity.fit', 'MAX_ARCHIVE_BYTES', 1),
        ]:
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr(name, payload)
            garmin = Mock()
            garmin.download_activity.return_value = archive.getvalue()
            with self.subTest(name=name, limit=limit), tempfile.TemporaryDirectory() as directory, patch('garmin_cli.main.connect', return_value=garmin), patch('garmin_cli.fit.' + limit, value), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(1, main(['activities', 'download-fit', '99', '--output-dir', directory]))
                self.assertEqual([], list(Path(directory).iterdir()))

    def test_activity_fit_download_rejects_multiple_fit_members(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('first.fit', b'FIT-ACTIVITY')
            zf.writestr('second.fit', b'FIT-ACTIVITY')
        garmin = Mock()
        garmin.download_activity.return_value = archive.getvalue()
        with tempfile.TemporaryDirectory() as directory, patch('garmin_cli.main.connect', return_value=garmin), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(1, main(['activities', 'download-fit', '99', '--output-dir', directory]))
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_activity_fit_download_rejects_archives_without_fit_data(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('activity.tcx', b'NOT-FIT')
        garmin = Mock()
        garmin.download_activity.return_value = archive.getvalue()
        with tempfile.TemporaryDirectory() as directory, patch('garmin_cli.main.connect', return_value=garmin), redirect_stderr(io.StringIO()):
            self.assertEqual(1, main(['activities', 'download-fit', '99', '--output-dir', directory]))
            self.assertEqual([], list(Path(directory).iterdir()))


class TestPlanCommandTests(unittest.TestCase):
    def test_inventory_retains_command_names_but_marks_unsupported(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, main(['run-tests']))
        result = json.loads(out.getvalue())
        self.assertEqual(41, result['total'])
        self.assertIn('delete-workout', result['unsupported'])
        self.assertIn('get-activity-splits', result['supported'])
        self.assertIn('Call check-session first', result['testPlan'])
        self.assertIn('| Tool | Status | Notes |', result['testPlan'])


if __name__ == '__main__':
    unittest.main()
