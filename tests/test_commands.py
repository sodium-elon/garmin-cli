from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from garmin_cli.client import get_display_name
from garmin_cli.main import main


class DisplayNameTests(unittest.TestCase):
    def test_display_name_uses_mcp_settings_endpoint_not_profile_endpoint(self):
        garmin = Mock()
        with patch("garmin_cli.client.raw_get", return_value={"displayName": "runner"}) as raw:
            self.assertEqual("runner", get_display_name(garmin))
        raw.assert_called_once_with(garmin, "userprofile-service/userprofile/settings")


class ReadCommandTests(unittest.TestCase):
    def run_cli(self, argv: list[str], response=None):
        garmin = Mock()
        output = io.StringIO()
        with (
            patch("garmin_cli.main.connect", return_value=garmin),
            patch("garmin_cli.main.get_display_name", return_value="runner"),
            patch("garmin_cli.main.raw_get", return_value=response) as raw_get,
            redirect_stdout(output),
        ):
            code = main(argv)
        return code, json.loads(output.getvalue()), raw_get, garmin

    def test_all_missing_read_commands_use_mcp_endpoints_and_parameters(self):
        cases = [
            (["fitness", "personal-records"], "personalrecord-service/personalrecord/prs/runner", {"includeHistory": "true"}),
            (["fitness", "stats", "2026-07-01", "2026-08-01"], "fitnessstats-service/activity", {
                "aggregation": "daily", "startDate": "2026-07-01", "endDate": "2026-08-01",
                "groupByActivityType": "true", "standardizedUnits": "true",
                "groupByParentActivityType": "false", "userFirstDay": "sunday", "metric": "duration",
            }),
            (["fitness", "vo2max", "--date", "2026-08-01"], "metrics-service/metrics/maxmet/latest/2026-08-01", None),
            (["fitness", "hr-zones"], "biometric-service/heartRateZones/", None),
            (["fitness", "training-readiness", "--date", "2026-08-01"], "metrics-service/metrics/trainingreadiness/2026-08-01", None),
            (["fitness", "sleep-stats", "2026-07-01", "2026-08-01"], "sleep-service/stats/sleep/daily/2026-07-01/2026-08-01", None),
            (["fitness", "calendar", "2026", "7"], "calendar-service/year/2026/month/7", None),
            (["fitness", "goals"], "goal-service/goal/goals", {"status": "active"}),
            (["fitness", "badges"], "badge-service/badge/earned", None),
            (["fitness", "badge-leaderboard"], "badge-service/badge/leaderboard", {"limit": 25}),
            (["fitness", "hydration", "--date", "2026-08-01"], "usersummary-service/usersummary/hydration/allData/2026-08-01", None),
            (["fitness", "power-zones"], "biometric-service/powerZones/sports/all", None),
            (["workouts", "list"], "workout-service/workouts", {"start": 0, "limit": 100}),
            (["workouts", "get", "42"], "workout-service/workout/42", None),
        ]
        for argv, path, params in cases:
            with self.subTest(argv=argv):
                code, data, raw_get, _ = self.run_cli(argv, {"ok": argv[-1]})
                self.assertEqual(0, code)
                self.assertEqual({"ok": argv[-1]}, data)
                if params is not None:
                    raw_get.assert_called_once_with(ANY, path, params)
                else:
                    raw_get.assert_called_once_with(ANY, path)

    def test_fitness_options_override_mcp_defaults(self):
        code, _, raw_get, _ = self.run_cli([
            "fitness", "stats", "2026-01-01", "2026-02-01",
            "--aggregation", "weekly", "--metric", "distance",
        ], [])
        self.assertEqual(0, code)
        self.assertEqual("weekly", raw_get.call_args.args[2]["aggregation"])
        self.assertEqual("distance", raw_get.call_args.args[2]["metric"])

    def test_calendar_month_is_restricted_to_mcp_zero_based_range(self):
        with self.assertRaises(SystemExit) as raised:
            main(["fitness", "calendar", "2026", "12"])
        self.assertEqual(2, raised.exception.code)


class DownloadCommandTests(unittest.TestCase):
    def test_activity_fit_download_extracts_original_zip(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("original.fit", b"FIT-ACTIVITY")
        garmin = Mock()
        garmin.download_activity.return_value = archive.getvalue()
        with tempfile.TemporaryDirectory() as directory, patch("garmin_cli.main.connect", return_value=garmin):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["activities", "download-fit", "99", "--output-dir", directory])
            result = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(b"FIT-ACTIVITY", Path(result["path"]).read_bytes())
            self.assertEqual(12, result["bytes"])
            self.assertEqual("99.fit", Path(result["path"]).name)

    def test_workout_fit_download_writes_bytes(self):
        garmin = Mock()
        garmin.download_workout.return_value = b"FIT-WORKOUT"
        with tempfile.TemporaryDirectory() as directory, patch("garmin_cli.main.connect", return_value=garmin):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["workouts", "download-fit", "42", "--output-dir", directory])
            result = json.loads(output.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(b"FIT-WORKOUT", Path(result["path"]).read_bytes())
            self.assertEqual("workout_42.fit", Path(result["path"]).name)


class WorkoutMutationTests(unittest.TestCase):
    def test_create_schedule_and_delete_preserve_payloads(self):
        workout = {"workoutName": "CLI Test", "workoutSegments": []}
        garmin = Mock()
        garmin.upload_workout.return_value = {"workoutId": 42}
        garmin.schedule_workout.return_value = {"scheduled": True}
        garmin.delete_workout.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "workout.json"
            source.write_text(json.dumps(workout))
            with patch("garmin_cli.main.connect", return_value=garmin):
                for argv, expected in [
                    (["workouts", "create", "--file", str(source)], {"workoutId": 42}),
                    (["workouts", "schedule", "42", "2026-08-22"], {"scheduled": True}),
                    (["workouts", "delete", "42"], {"workoutId": "42", "deleted": True}),
                ]:
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(0, main(argv))
                    self.assertEqual(expected, json.loads(output.getvalue()))
        garmin.upload_workout.assert_called_once_with(workout)
        garmin.schedule_workout.assert_called_once_with("42", "2026-08-22")
        garmin.delete_workout.assert_called_once_with("42")

    def test_create_accepts_inline_json(self):
        garmin = Mock()
        garmin.upload_workout.return_value = {"workoutId": 7}
        with patch("garmin_cli.main.connect", return_value=garmin):
            self.assertEqual(0, main(["workouts", "create", "--json", '{"workoutName":"Inline"}']))
        garmin.upload_workout.assert_called_once_with({"workoutName": "Inline"})


class TestPlanCommandTests(unittest.TestCase):
    def test_run_tests_returns_all_41_capabilities(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["run-tests"])
        result = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(41, result["total"])
        self.assertEqual(41, len(result["capabilities"]))
        self.assertIn("get-power-zones", result["capabilities"])
        self.assertIn("delete-workout", result["capabilities"])


if __name__ == "__main__":
    unittest.main()
