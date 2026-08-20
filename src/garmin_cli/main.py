"""garmin-cli entrypoint.

Conventions (agent- and script-friendly):
- Data goes to stdout as JSON; diagnostics go to stderr.
- Exit codes: 0 ok, 1 error, 2 usage, 75 auth required (run `garmin-cli auth login`).
- Endpoint parity: identical connectapi paths/params as garmin-mcp, so output
  is drop-in compatible with consumers of the MCP tools.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta

from garminconnect import GarminConnectAuthenticationError

from . import __version__
from .client import (
    EXIT_AUTH_REQUIRED,
    EXIT_ERROR,
    EXIT_OK,
    AuthRequired,
    check_session,
    connect,
    get_activity_details,
    get_activity_hr_zones,
    get_activity_polyline,
    get_activity_splits,
    get_activity_weather,
    get_display_name,
    get_user_profile,
    list_activities,
    login_and_save,
    raw_get,
    tokenstore_path,
)
from .envfile import apply_env_file
from .stage import build_staged


def _out_json(data) -> None:
    json.dump(data, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _date(value: str | None) -> str:
    if value is None:
        return date.today().isoformat()
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def _fail(message: str, code: int = EXIT_ERROR) -> int:
    print(f"garmin-cli: {message}", file=sys.stderr)
    return code


# ── command handlers ──────────────────────────────────────────────────


def cmd_auth(args) -> int:
    if args.auth_cmd == "login":
        garmin = login_and_save()
        profile = get_user_profile(garmin)
        print(
            f"garmin-cli: logged in as {profile.get('displayName', '?')}",
            file=sys.stderr,
        )
        return EXIT_OK
    # check
    result = check_session(verbose=args.verbose)
    _out_json(result)
    return EXIT_OK


def cmd_activities(args) -> int:
    garmin = connect()
    if args.activities_cmd == "list":
        data = list_activities(garmin, limit=args.limit, start=args.start)
    else:
        activity_id = str(args.activity_id)
        if args.activities_cmd == "get":
            data = get_activity_raw(garmin, activity_id)
        elif args.activities_cmd == "details":
            data = get_activity_details(garmin, activity_id)
        elif args.activities_cmd == "splits":
            data = get_activity_splits(garmin, activity_id)
        elif args.activities_cmd == "hr-zones":
            data = get_activity_hr_zones(garmin, activity_id)
        elif args.activities_cmd == "weather":
            data = get_activity_weather(garmin, activity_id)
        elif args.activities_cmd == "polyline":
            data = get_activity_polyline(garmin, activity_id)
        else:  # pragma: no cover
            return _fail(f"unknown activities subcommand: {args.activities_cmd}", 2)
    _out_json(data)
    return EXIT_OK


def get_activity_raw(garmin, activity_id):
    from .client import get_activity

    return get_activity(garmin, activity_id)


def cmd_daily(args) -> int:
    garmin = connect()
    d = _date(args.date)
    cmd = args.daily_cmd
    if cmd == "summary":
        name = get_display_name(garmin)
        data = raw_get(garmin, f"usersummary-service/usersummary/daily/{name}", {"calendarDate": d})
    elif cmd == "heart-rate":
        data = raw_get(garmin, "wellness-service/wellness/dailyHeartRate", {"date": d})
    elif cmd == "stress":
        data = raw_get(garmin, f"wellness-service/wellness/dailyStress/{d}")
    elif cmd == "intensity":
        data = raw_get(garmin, f"wellness-service/wellness/daily/im/{d}")
    elif cmd == "movement":
        data = raw_get(garmin, "wellness-service/wellness/dailyMovement", {"calendarDate": d})
    elif cmd == "respiration":
        data = raw_get(garmin, f"wellness-service/wellness/daily/respiration/{d}")
    elif cmd == "chart":
        data = raw_get(garmin, "wellness-service/wellness/dailySummaryChart/", {"date": d})
    else:  # pragma: no cover
        return _fail(f"unknown daily subcommand: {cmd}", 2)
    _out_json(data)
    return EXIT_OK


def cmd_wellness(args) -> int:
    garmin = connect()
    d = _date(args.date)
    if args.wellness_cmd == "sleep":
        data = raw_get(
            garmin,
            "sleep-service/sleep/dailySleepData",
            {"date": d, "nonSleepBufferMinutes": 60},
        )
    elif args.wellness_cmd == "body-battery":
        data = raw_get(garmin, "wellness-service/wellness/bodyBattery/messagingToday")
    elif args.wellness_cmd == "hrv":
        data = raw_get(garmin, f"hrv-service/hrv/{d}")
    else:  # pragma: no cover
        return _fail(f"unknown wellness subcommand: {args.wellness_cmd}", 2)
    _out_json(data)
    return EXIT_OK


def cmd_weight(args) -> int:
    garmin = connect()
    end = _date(args.end)
    if args.start:
        start = _date(args.start)
    else:
        start = (date.fromisoformat(end) - timedelta(days=30)).isoformat()
    data = raw_get(garmin, f"weight-service/weight/range/{start}/{end}")
    _out_json(data)
    return EXIT_OK


def cmd_profile(args) -> int:
    garmin = connect()
    _out_json(get_user_profile(garmin))
    return EXIT_OK


def cmd_stage(args) -> int:
    garmin = connect()
    if not args.activity and not args.latest_running:
        return _fail("stage requires --activity <id> or --latest-running", 2)
    if args.activity and args.latest_running:
        return _fail("stage takes either --activity or --latest-running, not both", 2)
    staged = build_staged(garmin, activity_id=args.activity)
    out_path = args.out or "garmin-staged.json"
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(staged, handle, indent=2, default=str)
        handle.write("\n")
    activities = staged["activities"]
    for item in activities:
        list_item = item["listItem"]
        print(
            f"garmin-cli: staged activity {list_item.get('activityId')} "
            f"({list_item.get('activityName')}) -> {out_path}",
            file=sys.stderr,
        )
    return EXIT_OK


# ── argument tree ─────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garmin-cli",
        description=(
            "CLI for Garmin Connect. Endpoint-compatible with garmin-mcp; "
            "auth via garth OAuth tokens (email/password login + automatic "
            "email-OTP MFA). Data on stdout as JSON, diagnostics on stderr. "
            "Exit codes: 0 ok, 1 error, 2 usage, 75 auth required."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="KEY=VALUE file providing GARMIN_EMAIL, GARMIN_PASSWORD, "
        "GARMIN_GMAIL_APP_PASSWORD (values are never printed)",
    )
    parser.add_argument(
        "--tokenstore",
        metavar="PATH",
        help="garth token directory (default: $GARMIN_CLI_TOKENSTORE or ~/.garmin-cli/tokens)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log diagnostics to stderr"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="login / check the saved session")
    auth_sub = p_auth.add_subparsers(dest="auth_cmd", required=True)
    auth_login = auth_sub.add_parser("login", help="fresh credential login (auto email-OTP MFA)")
    auth_login.set_defaults(func=cmd_auth)
    auth_check = auth_sub.add_parser("check", help="verify the saved session works")
    auth_check.add_argument(
        "-v", "--verbose", dest="verbose_check", action="store_true", help="show tokenstore path"
    )
    auth_check.set_defaults(func=cmd_auth)

    p_act = sub.add_parser("activities", help="activity data (parity with MCP tools)")
    act_sub = p_act.add_subparsers(dest="activities_cmd", required=True)
    p_list = act_sub.add_parser("list", help="list activities (MCP: list-activities)")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--start", type=int, default=0)
    p_list.set_defaults(func=cmd_activities)
    for name, help_text in [
        ("get", "full activity summary (MCP: get-activity)"),
        ("details", "time-series metrics (MCP: get-activity-details)"),
        ("splits", "lap/split data (MCP: get-activity-splits)"),
        ("hr-zones", "HR time-in-zone breakdown (MCP: get-activity-hr-zones)"),
        ("weather", "weather during activity (MCP: get-activity-weather)"),
        ("polyline", "GPS track (MCP: get-activity-polyline)"),
    ]:
        p = act_sub.add_parser(name, help=help_text)
        p.add_argument("activity_id", help="activity ID")
        p.set_defaults(func=cmd_activities)

    p_daily = sub.add_parser("daily", help="daily wellness data")
    daily_sub = p_daily.add_subparsers(dest="daily_cmd", required=True)
    for name, help_text in [
        ("summary", "daily summary (MCP: get-daily-summary)"),
        ("heart-rate", "daily heart rate (MCP: get-daily-heart-rate)"),
        ("stress", "daily stress (MCP: get-daily-stress)"),
        ("intensity", "intensity minutes (MCP: get-daily-intensity-minutes)"),
        ("movement", "daily movement (MCP: get-daily-movement)"),
        ("respiration", "daily respiration (MCP: get-daily-respiration)"),
        ("chart", "daily summary chart (MCP: get-daily-summary-chart)"),
    ]:
        p = daily_sub.add_parser(name, help=help_text)
        p.add_argument("--date", help="YYYY-MM-DD (default: today)")
        p.set_defaults(func=cmd_daily)

    p_well = sub.add_parser("wellness", help="sleep / body battery / HRV")
    well_sub = p_well.add_subparsers(dest="wellness_cmd", required=True)
    for name, help_text in [
        ("sleep", "sleep data (MCP: get-sleep)"),
        ("body-battery", "body battery (MCP: get-body-battery)"),
        ("hrv", "HRV value (MCP: get-hrv)"),
    ]:
        p = well_sub.add_parser(name, help=help_text)
        p.add_argument("--date", help="YYYY-MM-DD (default: today)")
        p.set_defaults(func=cmd_wellness)

    p_weight = sub.add_parser("weight", help="weight range (MCP: get-weight)")
    p_weight.add_argument("--start", help="YYYY-MM-DD (default: 30 days back)")
    p_weight.add_argument("--end", help="YYYY-MM-DD (default: today)")
    p_weight.set_defaults(func=cmd_weight)

    p_profile = sub.add_parser("profile", help="user profile (MCP: get-user-profile)")
    p_profile.set_defaults(func=cmd_profile)

    p_stage = sub.add_parser(
        "stage",
        help="assemble garmin-staged.json (alpharunner db:sync-garmin input) "
        "in one process — replaces the 4-call MCP staging flow",
    )
    p_stage.add_argument("--activity", metavar="ID", help="stage this activity ID")
    p_stage.add_argument(
        "--latest-running", action="store_true", help="stage the most recent run"
    )
    p_stage.add_argument(
        "-o", "--out", default="garmin-staged.json", help="output path (default: ./garmin-staged.json)"
    )
    p_stage.set_defaults(func=cmd_stage)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "verbose_check", False):
        args.verbose = True
    if args.tokenstore:
        import os

        os.environ["GARMIN_CLI_TOKENSTORE"] = args.tokenstore
    if getattr(args, "date", None) is not None:
        try:
            _date(args.date)
        except ValueError:
            return _fail(f"invalid date (want YYYY-MM-DD): {args.date}", 2)
    if args.env_file:
        try:
            apply_env_file(args.env_file)
        except FileNotFoundError:
            return _fail(f"env file not found: {args.env_file}", 2)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING, stream=sys.stderr
    )

    try:
        return args.func(args)
    except AuthRequired as exc:
        return _fail(str(exc), EXIT_AUTH_REQUIRED)
    except GarminConnectAuthenticationError as exc:
        return _fail(f"authentication error: {exc}", EXIT_AUTH_REQUIRED)
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        if args.verbose:
            logging.exception("command failed")
        return _fail(f"{type(exc).__name__}: {exc}", EXIT_ERROR)


if __name__ == "__main__":
    sys.exit(main())
