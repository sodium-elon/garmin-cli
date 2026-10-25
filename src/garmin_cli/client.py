"""Personal API compatibility layer preserving the staging contract."""
from __future__ import annotations
import os
from pathlib import Path
from .personal import AuthRequired, PersonalClient, SafeApiError, UnsupportedCapability

Garmin = PersonalClient
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH_REQUIRED = 75


def tokenstore_path() -> Path:
    raw = os.environ.get("GARMIN_CLI_TOKENSTORE")
    return Path(raw).expanduser() if raw else Path.home() / ".garmin-cli" / "tokens"


def connect(require_auth: bool = True) -> PersonalClient:
    key = os.environ.get("GARMIN_PERSONAL_API_KEY") or os.environ.get("GARMIN_TOKEN")
    if not key or not key.strip():
        raise AuthRequired("Set GARMIN_PERSONAL_API_KEY (or migration alias GARMIN_TOKEN) using --env-file or the environment")
    return PersonalClient(key.strip(), tokenstore_path())


def login_and_save() -> PersonalClient:
    garmin = connect()
    garmin.authenticate()
    return garmin


def check_session(verbose: bool = False) -> dict:
    """Cheap authenticated probe mirroring garmin-mcp's check-session."""
    garmin = connect()
    profile = get_user_profile(garmin)
    result = {"status": "ok", "profile": profile}
    if verbose:
        result["tokenstore"] = str(tokenstore_path())
    return result


# ── endpoint-parity layer ─────────────────────────────────────────────


def raw_get(garmin: Garmin, path: str, params: dict | None = None):
    """Normalize API-relative paths; transport uses only Garmin's Personal host."""
    normalized = path.lstrip("/")
    normalized = normalized.removeprefix("connectapi/")
    return garmin.connectapi("/" + normalized, params=params or None)


def get_user_profile(garmin: Garmin) -> dict:
    return raw_get(garmin, "userprofile-service/userprofile/settings") or {}


def get_display_name(garmin: Garmin) -> str:
    profile = raw_get(garmin, "userprofile-service/userprofile/settings") or {}
    name = profile.get("displayName")
    if not name:
        raise RuntimeError("displayName missing from user profile response")
    return str(name)


def list_activities(
    garmin: Garmin, limit: int = 20, start: int = 0, activity_type: str | None = None,
    start_date: str | None = None, end_date: str | None = None,
):
    params: dict = {"limit": limit, "start": start}
    if activity_type:
        params["activityType"] = activity_type
    if start_date:
        params["startDate"] = start_date
    if end_date:
        params["endDate"] = end_date
    return raw_get(garmin, "activitylist-service/activities/search/activities", params)


def get_activity(garmin: Garmin, activity_id: str):
    return raw_get(garmin, f"activity-service/activity/{activity_id}")


def get_activity_splits(garmin: Garmin, activity_id: str):
    return garmin.activity_fit_fragments(activity_id)["splits"]


def get_activity_hr_zones(garmin: Garmin, activity_id: str):
    return garmin.activity_fit_fragments(activity_id)["hrZones"]


def get_activity_details(garmin: Garmin, activity_id: str, max_chart_size: int = 10000):
    return raw_get(
        garmin,
        f"activity-service/activity/{activity_id}/details",
        {"maxChartSize": max_chart_size, "maxPolylineSize": 0, "maxHeatMapSize": 2000},
    )


def get_activity_weather(garmin: Garmin, activity_id: str):
    raise UnsupportedCapability('activities weather: unsupported by Garmin Personal API')


def get_activity_polyline(garmin: Garmin, activity_id: str):
    raise UnsupportedCapability('activities polyline: unsupported by Garmin Personal API')
