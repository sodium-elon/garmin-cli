"""Assemble garmin-staged.json — the exact format alpharunner's db:sync-garmin reads.

Mirrors the four MCP tool calls documented in
alpharunner/scripts/garmin/README.md (list-activities, get-activity,
get-activity-splits, get-activity-hr-zones) into one offline process:

    { "activities": [ { "listItem": …, "detail": …, "splits": …, "hrZones": … } ] }
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .client import (
    Garmin,
    get_activity,
    get_activity_hr_zones,
    get_activity_splits,
    list_activities,
)


def stage_activity(garmin: Garmin, list_item: dict) -> dict:
    activity_id = str(list_item["activityId"])
    with ThreadPoolExecutor(max_workers=3) as executor:
        detail = executor.submit(get_activity, garmin, activity_id)
        splits = executor.submit(get_activity_splits, garmin, activity_id)
        hr_zones = executor.submit(get_activity_hr_zones, garmin, activity_id)

    return {
        "listItem": list_item,
        "detail": detail.result(),
        "splits": splits.result(),
        "hrZones": hr_zones.result(),
    }


def _is_running(list_item: dict) -> bool:
    type_key = (list_item.get("activityType") or {}).get("typeKey", "")
    return "running" in str(type_key)


def latest_running(garmin: Garmin) -> dict:
    """The most recent running-type activity, or a clear error."""
    activities = list_activities(garmin, limit=20, start=0) or []
    running = [a for a in activities if _is_running(a)]
    if not running:
        raise RuntimeError(
            "No running-type activity found in the 20 most recent activities"
        )
    return running[0]


def find_activity(garmin: Garmin, activity_id: str, max_pages: int = 5) -> dict:
    """Find an activity's complete list item in up to 100-item pages."""
    numeric_id = int(activity_id)
    for page in range(max_pages):
        activities = list_activities(garmin, limit=100, start=page * 100) or []
        match = next(
            (item for item in activities if int(item.get("activityId", -1)) == numeric_id),
            None,
        )
        if match is not None:
            return match
        if not activities:
            break
    raise RuntimeError(
        f"Activity {activity_id} not found in first {max_pages * 100} activities"
    )


def build_staged(garmin: Garmin, activity_id: str | None = None) -> dict:
    list_item = (
        latest_running(garmin)
        if activity_id is None
        else find_activity(garmin, activity_id)
    )
    return {"activities": [stage_activity(garmin, list_item)]}
