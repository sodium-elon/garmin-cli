"""garmin-cli entrypoint.

Conventions (agent- and script-friendly):
- Data goes to stdout as JSON; diagnostics go to stderr.
- Exit codes: 0 ok, 1 error, 2 usage, 75 Personal API authorization required.
- Read-only Personal API routes; staging retains the existing consumer envelope.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

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
)
from .envfile import apply_env_file
from .personal import SafeApiError, UnsupportedCapability
from .stage import build_staged

logger = logging.getLogger(__name__)


def _out_json(data) -> None:
    json.dump(data, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _date(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).date().isoformat()
    if len(value) != 10 or value[4] != '-' or value[7] != '-':
        raise ValueError('expected YYYY-MM-DD')
    return date.fromisoformat(value).isoformat()


def _fail(message: str, code: int = EXIT_ERROR) -> int:
    print(f"garmin-cli: {message}", file=sys.stderr)
    return code


def _mcp_empty_response(data, path: str):
    if data is None:
        return {"noData": True, "status": 204, "path": path}
    return data


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
    if args.activities_cmd in {'weather', 'polyline'}:
        raise UnsupportedCapability(f'activities {args.activities_cmd}: unsupported by Garmin Personal API')
    garmin = connect()
    if args.activities_cmd == "list":
        data = list_activities(garmin, limit=args.limit, start=args.start,
                               activity_type=args.activity_type,
                               start_date=args.start_date, end_date=args.end_date)
    elif args.activities_cmd == "download-fit":
        archive = garmin.download_activity(
            str(args.activity_id)
        )
        from .fit import _fit_payload

        try:
            payload = _fit_payload(archive)
        except Exception:
            raise ValueError("Invalid activity FIT archive") from None
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{args.activity_id}.fit"
        import os
        fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
        _out_json({"path": str(output_path), "bytes": len(payload)})
        return EXIT_OK
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


def cmd_fitness(args) -> int:
    cmd = args.fitness_cmd
    if cmd not in {'hr-zones', 'sleep-stats', 'hydration'}:
        raise UnsupportedCapability(f'fitness {cmd}: unsupported by Garmin Personal API')
    garmin = connect()
    if cmd == "hr-zones":
        data = raw_get(garmin, "biometric-service/heartRateZones/v2")
    elif cmd == "sleep-stats":
        data = raw_get(
            garmin, "sleep-service/sleep/dailySleepSummariesByDate",
            {"startDate": _date(args.start_date), "endDate": _date(args.end_date)},
        )
    else:  # hydration
        d = _date(args.date)
        data = raw_get(garmin, f"usersummary-service/stats/hydration/daily/{d}/{d}")
    _out_json(data)
    return EXIT_OK


def cmd_workouts(args) -> int:
    raise UnsupportedCapability(f'workouts {args.workouts_cmd}: unsupported by Garmin Personal API')


CAPABILITIES = [
    "garmin-login", "check-session", "list-activities", "get-activity",
    "get-activity-details", "get-activity-splits", "get-activity-hr-zones",
    "get-activity-polyline", "get-activity-weather", "download-fit",
    "get-daily-summary", "get-daily-heart-rate", "get-daily-stress",
    "get-daily-summary-chart", "get-daily-intensity-minutes", "get-daily-movement",
    "get-daily-respiration", "get-sleep", "get-body-battery", "get-hrv",
    "get-weight", "get-personal-records", "get-fitness-stats", "get-vo2max",
    "get-hr-zones-config", "get-user-profile", "get-training-readiness",
    "get-sleep-stats", "get-calendar", "get-goals", "get-badges",
    "get-badge-leaderboard", "get-hydration", "get-power-zones", "list-workouts",
    "get-workout", "download-workout-fit", "create-workout", "schedule-workout",
    "delete-workout", "run-tests",
]


def cmd_run_tests(_args) -> int:
    supported = {
        'garmin-login', 'check-session', 'list-activities', 'get-activity',
        'get-activity-details', 'get-activity-splits', 'get-activity-hr-zones',
        'download-fit', 'get-daily-summary', 'get-daily-heart-rate',
        'get-daily-stress', 'get-sleep', 'get-body-battery', 'get-hrv',
        'get-weight', 'get-hr-zones-config', 'get-user-profile',
        'get-sleep-stats', 'get-hydration', 'run-tests',
    }
    test_plan = "\n".join(
        [
            "# garmin-cli Test Plan",
            "",
            "Call check-session first. If it fails, call garmin-login to authenticate.",
            "Verify supported capabilities below; unsupported legacy commands must fail safely without requests.",
            "",
            *[f"- {capability}" for capability in CAPABILITIES if capability != "run-tests"],
            "",
            "Present results as a markdown table: | Tool | Status | Notes |",
            "Count total passed vs failed at the end.",
        ]
    )
    _out_json(
        {
            "total": len(CAPABILITIES),
            "capabilities": CAPABILITIES,
            "supported": [name for name in CAPABILITIES if name in supported],
            "unsupported": [name for name in CAPABILITIES if name not in supported],
            "testPlan": test_plan,
        }
    )
    return EXIT_OK


def cmd_daily(args) -> int:
    if args.daily_cmd not in {'summary', 'heart-rate', 'stress'}:
        raise UnsupportedCapability(f'daily {args.daily_cmd}: unsupported by Garmin Personal API')
    garmin = connect()
    d = _date(args.date)
    cmd = args.daily_cmd
    if cmd == "summary":
        data = raw_get(garmin, "usersummary-service/usersummary/daily", {"calendarDate": d})
    elif cmd == "heart-rate":
        data = raw_get(garmin, "wellness-service/wellness/dailyHeartRate", {"date": d})
    elif cmd == "stress":
        data = raw_get(garmin, f"usersummary-service/stats/stress/daily/{d}/{d}")

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
            "sleep-service/sleep/dailySleep",
            {"date": d},
        )
        data = {"dailySleepDTO": data}
    elif args.wellness_cmd == "body-battery":
        data = raw_get(garmin, "wellness-service/wellness/bodyBattery/reports/daily", {"startDate": d, "endDate": d})
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
    data = raw_get(
        garmin,
        f"weight-service/weight/all/{start}/{end}",
    )
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
            "Read-only CLI for Garmin Personal API. Authentication uses a Personal API key, "
            "never account passwords or email MFA. Data on stdout as JSON, diagnostics on stderr. "
            "Exit codes: 0 ok, 1 error, 2 usage, 75 auth required."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="KEY=VALUE file providing GARMIN_PERSONAL_API_KEY (or GARMIN_TOKEN alias); values are never printed",
    )
    parser.add_argument(
        "--tokenstore",
        metavar="PATH",
        help="private Personal API bearer cache (default: $GARMIN_CLI_TOKENSTORE or ~/.garmin-cli/tokens)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log diagnostics to stderr"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="login / check the saved session")
    auth_sub = p_auth.add_subparsers(dest="auth_cmd", required=True)
    auth_login = auth_sub.add_parser("login", help="exchange Personal API key or reuse its private bearer cache")
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
    p_list.add_argument("--start-date", help="inclusive YYYY-MM-DD")
    p_list.add_argument("--end-date", help="inclusive YYYY-MM-DD")
    p_list.add_argument("--activity-type", help="activity type key, e.g. running")
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

    ap = act_sub.add_parser("download-fit", help="download an activity's original FIT file")
    ap.add_argument("activity_id")
    ap.add_argument("--output-dir", default="./fit_files")
    ap.set_defaults(func=cmd_activities)

    p_fitness = sub.add_parser("fitness", help="fitness, recovery, calendar, and achievement data")
    fitness_sub = p_fitness.add_subparsers(dest="fitness_cmd", required=True)

    p = fitness_sub.add_parser("personal-records", help="personal records with history")
    p.set_defaults(func=cmd_fitness)
    p = fitness_sub.add_parser("stats", help="aggregated fitness stats")
    p.add_argument("start_date")
    p.add_argument("end_date")
    p.add_argument("--aggregation", choices=("daily", "weekly", "monthly"), default="daily")
    p.add_argument("--metric", choices=("duration", "distance", "calories"), default="duration")
    p.set_defaults(func=cmd_fitness)
    for name in ("vo2max", "training-readiness", "hydration"):
        p = fitness_sub.add_parser(name)
        p.add_argument("--date", help="YYYY-MM-DD (default: today)")
        p.set_defaults(func=cmd_fitness)
    for name in ("hr-zones", "badges", "power-zones"):
        fitness_sub.add_parser(name).set_defaults(func=cmd_fitness)
    p = fitness_sub.add_parser("sleep-stats")
    p.add_argument("start_date")
    p.add_argument("end_date")
    p.set_defaults(func=cmd_fitness)
    p = fitness_sub.add_parser("calendar")
    p.add_argument("year", type=int)
    p.add_argument("month", type=int, choices=range(12), help="zero-based month (0=January)")
    p.set_defaults(func=cmd_fitness)
    p = fitness_sub.add_parser("goals")
    p.add_argument("--status", choices=("active", "future", "past"), default="active")
    p.set_defaults(func=cmd_fitness)
    p = fitness_sub.add_parser("badge-leaderboard")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_fitness)

    p_workouts = sub.add_parser("workouts", help="saved workout read/write operations")
    workout_sub = p_workouts.add_subparsers(dest="workouts_cmd", required=True)
    p = workout_sub.add_parser("list")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_workouts)
    p = workout_sub.add_parser("get")
    p.add_argument("workout_id")
    p.set_defaults(func=cmd_workouts)
    p = workout_sub.add_parser("download-fit")
    p.add_argument("workout_id")
    p.add_argument("--output-dir", default="./fit_files")
    p.set_defaults(func=cmd_workouts)
    p = workout_sub.add_parser("create")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="path to workout JSON")
    source.add_argument("--json", help="inline workout JSON")
    p.set_defaults(func=cmd_workouts)
    p = workout_sub.add_parser("schedule")
    p.add_argument("workout_id")
    p.add_argument("date")
    p.set_defaults(func=cmd_workouts)
    p = workout_sub.add_parser("delete")
    p.add_argument("workout_id")
    p.set_defaults(func=cmd_workouts)

    sub.add_parser("run-tests", help="print the 41-capability verification plan").set_defaults(func=cmd_run_tests)

    p_daily = sub.add_parser("daily", help="daily health data")
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
    for field in ("date", "start", "end", "start_date", "end_date"):
        value = getattr(args, field, None)
        if value is None or not isinstance(value, str):
            continue
        try:
            _date(value)
        except ValueError:
            print(
                f"garmin-cli: invalid --{field.replace('_', '-')}: {value} "
                "(expected YYYY-MM-DD)",
                file=sys.stderr,
            )
            return 2
    start = getattr(args, 'start_date', None)
    end = getattr(args, 'end_date', None)
    if start and end and start > end:
        return _fail('start date must not be after end date', 2)
    if args.env_file:
        try:
            apply_env_file(args.env_file)
        except FileNotFoundError:
            return _fail(f"env file not found: {args.env_file}", 2)
        except Exception:
            return _fail('unable to load env file; verify permissions and KEY=VALUE format', 2)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING, stream=sys.stderr
    )

    try:
        return args.func(args)
    except AuthRequired as exc:
        return _fail(str(exc), EXIT_AUTH_REQUIRED)
    except SafeApiError as exc:
        return _fail(str(exc), EXIT_ERROR)
    except Exception:
        # Third-party/network exceptions can contain credential-bearing URLs/headers.
        return _fail('command failed; verify arguments, Personal API availability and key permissions', EXIT_ERROR)


if __name__ == "__main__":
    sys.exit(main())
