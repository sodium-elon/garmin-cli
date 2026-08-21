"""Tests for stage assembly and MFA code extraction (no network)."""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from garmin_cli.mfa import _strip_html, extract_code
from garmin_cli.stage import build_staged, find_activity, latest_running, stage_activity

LIST_ITEM = {
    "activityId": 23237542145,
    "activityName": "Long Run",
    "startTimeLocal": "2026-06-13 17:59:18",
    "activityType": {"typeKey": "treadmill_running"},
}

DETAIL = {"activityId": 23237542145, "summaryDTO": {"distance": 15700.0}}
SPLITS = {"activityId": 23237542145, "lapDTOs": [{"lapIndex": 1, "distance": 1000}]}
HR_ZONES = [{"zoneNumber": 1, "secsInZone": 0}, {"zoneNumber": 4, "secsInZone": 1113}]


class FakeGarmin:
    def __init__(self, list_result=None):
        self.list_result = list_result or [LIST_ITEM]
        self.calls: list[str] = []

    def connectapi(self, path, params=None):
        self.calls.append(path)
        if "activitylist-service" in path:
            return self.list_result
        if path.endswith("/splits"):
            return SPLITS
        if path.endswith("/hrTimeInZones"):
            return HR_ZONES
        return DETAIL


class ConcurrencyTrackingGarmin(FakeGarmin):
    def __init__(self):
        super().__init__()
        self.active_calls = 0
        self.max_active_calls = 0
        self.lock = threading.Lock()

    def connectapi(self, path, params=None):
        with self.lock:
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            time.sleep(0.05)
            return super().connectapi(path, params=params)
        finally:
            with self.lock:
                self.active_calls -= 1


class TestStage(unittest.TestCase):
    def test_stage_activity_fetches_resources_concurrently(self):
        fake = ConcurrencyTrackingGarmin()

        staged = stage_activity(fake, LIST_ITEM)

        self.assertEqual(staged["detail"], DETAIL)
        self.assertEqual(staged["splits"], SPLITS)
        self.assertEqual(staged["hrZones"], HR_ZONES)
        self.assertEqual(fake.max_active_calls, 3)

    def test_build_staged_latest_running_shape(self):
        fake = FakeGarmin()
        staged = build_staged(fake)
        self.assertEqual(
            staged,
            {
                "activities": [
                    {
                        "listItem": LIST_ITEM,
                        "detail": DETAIL,
                        "splits": SPLITS,
                        "hrZones": HR_ZONES,
                    }
                ]
            },
        )
        # The four parity endpoints, exactly as garmin-mcp calls them
        # (garth routes to connectapi.garmin.com; no connectapi/ prefix).
        self.assertIn("/activitylist-service/activities/search/activities", fake.calls)
        self.assertIn("/activity-service/activity/23237542145", fake.calls)
        self.assertIn("/activity-service/activity/23237542145/splits", fake.calls)
        self.assertIn("/activity-service/activity/23237542145/hrTimeInZones", fake.calls)

    def test_latest_running_skips_non_running(self):
        cycling = {"activityId": 1, "activityType": {"typeKey": "cycling"}}
        trail = {"activityId": 2, "activityType": {"typeKey": "trail_running"}}
        fake = FakeGarmin(list_result=[cycling, trail])
        self.assertEqual(latest_running(fake)["activityId"], 2)

    def test_latest_running_none_found(self):
        cycling = {"activityId": 1, "activityType": {"typeKey": "cycling"}}
        fake = FakeGarmin(list_result=[cycling])
        with self.assertRaises(RuntimeError):
            latest_running(fake)

    def test_find_activity_pages_until_full_list_item_is_found(self):
        older = {**LIST_ITEM, "activityId": 99, "powerTimeInZone_1": 321.0}

        class PaginatedGarmin(FakeGarmin):
            def connectapi(self, path, params=None):
                if "activitylist-service" in path:
                    self.calls.append(path)
                    return [LIST_ITEM] if params["start"] == 0 else [older]
                return super().connectapi(path, params=params)

        fake = PaginatedGarmin()

        self.assertEqual(find_activity(fake, "99", max_pages=2), older)

    def test_staged_json_serializable(self):
        staged = build_staged(FakeGarmin())
        json.dumps(staged)  # must not raise


class TestMfaExtraction(unittest.TestCase):
    def test_extracts_unique_code(self):
        rendered = "Your one-time code for your account is 482913. It expires soon."
        self.assertEqual(extract_code(rendered), "482913")

    def test_extracts_code_across_long_whitespace_gap(self):
        # Real-world Garmin email: HTML table-cell layout leaves >120 chars of
        # newline/indent whitespace between the phrase and the code.
        rendered = (
            "Use this one-time code for your account \r\n"
            + (" " * 200 + "\r\n") * 2
            + "   482913 This code will expire after 30 minutes."
        )
        self.assertEqual(extract_code(rendered), "482913")

    def test_ambiguous_codes_rejected(self):
        rendered = (
            "one-time code for your account: 111111 ... one-time code for your account: 222222"
        )
        self.assertIsNone(extract_code(rendered))

    def test_no_code(self):
        self.assertIsNone(extract_code("no codes here"))

    def test_strip_html_removes_hidden_style(self):
        html_body = (
            "<html><head><style>.x{content:'999999'}</style></head>"
            "<body>Your one-time code for your account is <b>736204</b></body></html>"
        )
        self.assertEqual(extract_code(_strip_html(html_body)), "736204")


if __name__ == "__main__":
    unittest.main()
