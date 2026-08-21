"""Session management and Garmin Connect API access with endpoint parity.

Data calls use the *exact same* connectapi paths and query parameters as
garmin-mcp's ``src/tools.ts``, so responses (and therefore the staged-file
format consumed by alpharunner's ``db:sync-garmin``) are drop-in compatible.

Token store: garth-format OAuth tokens under ``~/.garmin-cli/tokens`` by
default (env ``GARMIN_CLI_TOKENSTORE``). Completely separate from garmin-mcp's
``~/.garmin-connect-mcp`` store; the MCP daemon is untouched by this CLI.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

from .mfa import OtpWaiter

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH_REQUIRED = 75  # mirrors garmin-mcp login.sh convention


class AuthRequired(Exception):
    """No valid session — the caller should tell the user to log in."""


def tokenstore_path() -> Path:
    raw = os.environ.get("GARMIN_CLI_TOKENSTORE")
    return Path(raw).expanduser() if raw else Path.home() / ".garmin-cli" / "tokens"


def _credentials() -> tuple[str | None, str | None]:
    return os.environ.get("GARMIN_EMAIL"), os.environ.get("GARMIN_PASSWORD")


def _mfa_prompt() -> str:
    """prompt_mfa callback used when no waiter was armed at login start."""
    from .mfa import MfaError

    address = os.environ.get("GARMIN_EMAIL")
    app_password = os.environ.get("GARMIN_GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise MfaError(
            "MFA code required, but GARMIN_GMAIL_APP_PASSWORD is not set; "
            "cannot fetch the email OTP automatically"
        )
    waiter = OtpWaiter(address, app_password)
    waiter.arm()
    code = waiter.wait()
    print("garmin-cli: OTP code obtained from email", file=sys.stderr)
    return code


def connect(require_auth: bool = True) -> Garmin:
    """Return an authenticated Garmin client, resuming the saved session."""
    store = tokenstore_path()
    if not store.exists():
        if require_auth:
            raise AuthRequired(
                f"No saved session at {store}. Run: garmin-cli auth login"
            )
        return Garmin()
    garmin = Garmin()
    try:
        garmin.login(str(store))
    except GarminConnectAuthenticationError as exc:
        if require_auth:
            raise AuthRequired(
                f"Saved session at {store} is no longer valid ({exc}). "
                "Run: garmin-cli auth login"
            ) from exc
        return Garmin()
    return garmin


def login_and_save() -> Garmin:
    """Fresh credential login (with automatic email-OTP MFA); save session.

    The OTP waiter is armed BEFORE the login attempt starts (recording IMAP
    UIDNEXT first), mirroring garmin-mcp's arm-before-login design — Garmin
    can deliver the OTP email before garth gets around to asking for the code.
    """
    email, password = _credentials()
    if not email or not password:
        raise AuthRequired(
            "GARMIN_EMAIL and GARMIN_PASSWORD must be set "
            "(use --env-file or export them)"
        )
    prompt_mfa = _mfa_prompt
    app_password = os.environ.get("GARMIN_GMAIL_APP_PASSWORD")
    if app_password:
        waiter = OtpWaiter(email, app_password)
        waiter.arm()

        def prompt_mfa() -> str:  # noqa: F811 — armed variant shadows the fallback
            code = waiter.wait()
            print("garmin-cli: OTP code obtained from email", file=sys.stderr)
            return code

    garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    garmin.login()
    store = tokenstore_path()
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(store, 0o700)
    garmin.client.dump(str(store))  # type: ignore[union-attr]
    print(f"garmin-cli: session saved to {store}", file=sys.stderr)
    return garmin


def check_session(verbose: bool = False) -> dict:
    """Cheap authenticated probe mirroring garmin-mcp's check-session."""
    garmin = connect()
    profile = get_user_profile(garmin)
    result = {"status": "ok", "userId": profile.get("id")}
    if verbose:
        result["tokenstore"] = str(tokenstore_path())
    return result


# ── endpoint-parity layer ─────────────────────────────────────────────


def raw_get(garmin: Garmin, path: str, params: dict | None = None):
    """GET a connectapi path with the same shape garmin-mcp's client uses.

    garth's connectapi() routes to connectapi.garmin.com itself, so paths must
    NOT carry a "connectapi/" prefix (garmin-mcp's client.get takes the bare
    service path too — both end up at the same URL).
    """
    normalized = path.lstrip("/")
    if normalized.startswith("connectapi/"):
        normalized = normalized[len("connectapi/"):]
    return garmin.connectapi("/" + normalized, params=params or None)


def get_user_profile(garmin: Garmin) -> dict:
    return raw_get(garmin, "userprofile-service/userprofile/user-settings/") or {}


def get_display_name(garmin: Garmin) -> str:
    profile = raw_get(garmin, "userprofile-service/userprofile/settings") or {}
    name = profile.get("displayName")
    if not name:
        raise RuntimeError("displayName missing from user profile response")
    return str(name)


def list_activities(
    garmin: Garmin, limit: int = 20, start: int = 0, activity_type: str | None = None
):
    params: dict = {"limit": limit, "start": start}
    if activity_type:
        params["activityType"] = activity_type
    return raw_get(garmin, "activitylist-service/activities/search/activities", params)


def get_activity(garmin: Garmin, activity_id: str):
    return raw_get(garmin, f"activity-service/activity/{activity_id}")


def get_activity_splits(garmin: Garmin, activity_id: str):
    return raw_get(garmin, f"activity-service/activity/{activity_id}/splits")


def get_activity_hr_zones(garmin: Garmin, activity_id: str):
    return raw_get(garmin, f"activity-service/activity/{activity_id}/hrTimeInZones")


def get_activity_details(garmin: Garmin, activity_id: str, max_chart_size: int = 10000):
    return raw_get(
        garmin,
        f"activity-service/activity/{activity_id}/details",
        {"maxChartSize": max_chart_size, "maxPolylineSize": 0, "maxHeatMapSize": 2000},
    )


def get_activity_weather(garmin: Garmin, activity_id: str):
    return raw_get(garmin, f"activity-service/activity/{activity_id}/weather")


def get_activity_polyline(garmin: Garmin, activity_id: str):
    return raw_get(garmin, f"activity-service/activity/{activity_id}/polyline/full-resolution/")
