# garmin-cli

Read-only CLI for Garmin's **Personal API**, with original FIT decoding for
AlphaRunner staging. It does not use garminconnect, garth, account passwords,
browser login, Gmail/IMAP, or MFA. Dependencies are hash-locked in `uv.lock`.

## Setup and authorization

The project pins Python 3.13 (`requires-python >=3.12,<3.14`). For a fresh
checkout, `uv sync --locked` creates the project environment. Then use:

```bash
garmin-cli --env-file /path/to/private.env auth login
garmin-cli --env-file /path/to/private.env auth check
```

| Variable | Purpose |
|---|---|
| `GARMIN_PERSONAL_API_KEY` | Personal API key with permissions for the requested data |
| `GARMIN_TOKEN` | Migration alias when the canonical key is absent |
| `GARMIN_CLI_TOKENSTORE` | Private bearer-cache directory; default `~/.garmin-cli/tokens` |

Use a private `KEY=VALUE` env file (mode 0600); never put secrets in command
arguments. `--tokenstore PATH` overrides the cache directory. Legacy password
variables are ignored; no password-login fallback exists.

The key is exchanged using `Authorization: ApiKey ...` at the Personal API token
endpoint. Token exchange deliberately omits `Accept: application/json`: Garmin
returns a raw text bearer token. GET requests use only the Personal API host and
`Authorization: Bearer ...`; redirects are not followed. Cached tokens are reused
for up to 24 hours (renewed earlier if rejected or near expiry), with a key
fingerprint and atomic writes. The cache directory is 0700; cache and lock files
are 0600. An expired/revoked key still requires user rotation. No refresh-token,
watchdog, cron, MCP-session reuse, or account-login recovery is involved.

## Supported commands

```text
garmin-cli auth login | auth check
garmin-cli profile
garmin-cli activities list [--limit N] [--start N] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--activity-type running]
garmin-cli activities get|details|splits|hr-zones ACTIVITY_ID
garmin-cli activities download-fit ACTIVITY_ID [--output-dir DIR]
garmin-cli daily summary|heart-rate|stress [--date YYYY-MM-DD]
garmin-cli wellness sleep|body-battery|hrv [--date YYYY-MM-DD]
garmin-cli fitness hr-zones
garmin-cli fitness sleep-stats START_DATE END_DATE
garmin-cli fitness hydration [--date YYYY-MM-DD]
garmin-cli weight [--start YYYY-MM-DD] [--end YYYY-MM-DD]
garmin-cli stage --latest-running [-o garmin-staged.json]
garmin-cli stage --activity ACTIVITY_ID [-o garmin-staged.json]
garmin-cli run-tests
```

Dates are validated before connecting. Omitted single dates default to today in
UTC. Search date filters are inclusive and preserve offset/limit pagination.
Sleep-stat ranges use Garmin's documented daily summary route (maximum 31 days).
Weight returns all measurements, not just daily latest measurements. Body Battery
and stress return the published Personal API report/stat shapes, not legacy MCP
chart payloads. There is no claim of universal MCP endpoint or response parity.

### Staging compatibility

`stage` preserves the `activities: [{listItem, detail, splits, hrZones}]` envelope
consumed by AlphaRunner. Unsupported legacy `/splits` and `/hrTimeInZones`
requests are replaced by downloading the original activity ZIP/FIT and decoding
laps and HR zones with `fitdecode >=0.11,<0.12`. Each PersonalClient caches decoded
fragments under an RLock, so the two concurrent stage consumers download/decode
once per activity. Native/developer power attribution is retained by the decoder;
missing fields are not fabricated. Activity `details` works in live Personal API
verification but is not part of the retrieved published Swagger contract.

Profile uses `/userprofile-service/userprofile/settings`; daily summary uses
`/usersummary-service/usersummary/daily?calendarDate=...`. Sleep is fetched from
`/sleep-service/sleep/dailySleep?date=...` and its direct DTO is wrapped as
`{"dailySleepDTO": ...}` for the existing sleep consumer. Downloaded activity FIT
files are written privately (0600).

## Explicitly unsupported legacy commands

Names and argument parsers remain for compatibility, but these commands return
exit **1** and an explicit `unsupported by Garmin Personal API` diagnostic,
without authenticating or issuing legacy requests:

- Activity weather and polyline.
- Daily intensity, movement, respiration, chart.
- Fitness personal-records, stats, vo2max, training-readiness, calendar, goals,
  badges, badge-leaderboard, power-zones.
- All standalone `workouts` commands, including list/get/download-fit and
  create/schedule/delete. Activity-associated workouts in Garmin's API are not
  equivalent to a standalone workout library.

Calendar and training-readiness returned live HTTP 404. Other unsupported
commands lack an implemented verified/published equivalent; this is not a claim
that every undocumented route was live-tested. No workout writes are attempted.
`run-tests` retains the 41-name legacy inventory and explicitly separates
`supported` and `unsupported`; it prints a plan, not a live test result.

## Output and failure contract

JSON data goes to stdout, diagnostics to stderr. Exit codes are `0` success,
`1` API/unsupported/other error, `2` invalid arguments, `75` missing or rejected
Personal API authorization. HTTP 401 gets at most one bearer renewal/retry;
HTTP 403 does not trigger password authentication. Locally constructed safe
status diagnostics are retained. Arbitrary exceptions, remote bodies and
tracebacks are never printed, even with `--verbose`.

## Development

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
uv lock --check
```

Official contract: https://developer.garmin.com/gc-developer-program/personal/swagger-docs/
