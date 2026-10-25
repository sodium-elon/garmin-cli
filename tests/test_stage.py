"""Tests for Personal API stage assembly (no network or legacy MFA)."""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
        return DETAIL

    def activity_fit_fragments(self, activity_id):
        self.calls.append(f"FIT:{activity_id}")
        return {"splits": SPLITS, "hrZones": HR_ZONES}


class ConcurrencyTrackingGarmin(FakeGarmin):
    def __init__(self):
        super().__init__()
        self.active_calls = 0
        self.max_active_calls = 0
        self.lock = threading.Lock()

    def track(self, callback):
        with self.lock:
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            time.sleep(0.05)
            return callback()
        finally:
            with self.lock:
                self.active_calls -= 1

    def connectapi(self, path, params=None):
        return self.track(lambda: super(ConcurrencyTrackingGarmin, self).connectapi(path, params))

    def activity_fit_fragments(self, activity_id):
        return self.track(lambda: super(ConcurrencyTrackingGarmin, self).activity_fit_fragments(activity_id))


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
        self.assertEqual(staged, {"activities": [{
            "listItem": LIST_ITEM, "detail": DETAIL, "splits": SPLITS, "hrZones": HR_ZONES,
        }]})
        self.assertIn("/activitylist-service/activities/search/activities", fake.calls)
        self.assertIn("/activity-service/activity/23237542145", fake.calls)
        self.assertIn("FIT:23237542145", fake.calls)
        self.assertFalse(any(path.endswith(("/splits", "/hrTimeInZones")) for path in fake.calls))

    def test_latest_running_skips_non_running(self):
        cycling = {"activityId": 1, "activityType": {"typeKey": "cycling"}}
        trail = {"activityId": 2, "activityType": {"typeKey": "trail_running"}}
        self.assertEqual(latest_running(FakeGarmin([cycling, trail]))["activityId"], 2)

    def test_latest_running_none_found(self):
        with self.assertRaises(RuntimeError):
            latest_running(FakeGarmin([{"activityId": 1, "activityType": {"typeKey": "cycling"}}]))

    def test_find_activity_pages_until_full_list_item_is_found(self):
        older = {**LIST_ITEM, "activityId": 99, "powerTimeInZone_1": 321.0}

        class PaginatedGarmin(FakeGarmin):
            def connectapi(self, path, params=None):
                if "activitylist-service" in path:
                    self.calls.append(path)
                    return [LIST_ITEM] if params["start"] == 0 else [older]
                return super().connectapi(path, params=params)

        self.assertEqual(find_activity(PaginatedGarmin(), "99", max_pages=2), older)

    def test_staged_json_serializable(self):
        json.dumps(build_staged(FakeGarmin()))


if __name__ == "__main__":
    unittest.main()
