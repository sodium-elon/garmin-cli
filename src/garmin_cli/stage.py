"""Assemble garmin-staged.json — the exact format alpharunner's db:sync-garmin reads.

Mirrors the four MCP tool calls documented in
alpharunner/scripts/garmin/README.md (list-activities, get-activity,
get-activity-splits, get-activity-hr-zones) into one offline process:

    { "activities": [ { "listItem": …, "detail": …, "splits": …, "hrZones": … } ] }
"""

from __future__ import annotations

from .client import (
    Garmin,
    get_activity,
    get_activity_hr_zones,
    get_activity_splits,
    list_activities,
)


def stage_activity(garmin: Garmin, list_item: dict) -> dict:
    activity_id = list_item["activityId"]
    return {
        "listItem": list_item,
        "detail": get_activity(garmin, str(activity_id)),
        "splits": get_activity_splits(garmin, str(activity_id)),
        "hrZones": get_activity_hr_zones(garmin, str(activity_id)),
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


def build_staged(garmin: Garmin, activity_id: str | None = None) -> dict:
    if activity_id is None:
        list_item = latest_running(garmin)
    else:
        detail = get_activity(garmin, activity_id)
        list_item = {
            "activityId": detail.get("activityId", int(activity_id)),
            "activityName": detail.get("activityName"),
            "startTimeLocal": detail.get("startTimeLocal"),
            "activityType": detail.get("activityTypeDTO"),
        }
    return {"activities": [stage_activity(garmin, list_item)]}
