# garmin-cli

CLI for Garmin Connect — the **inner-loop companion to
[garmin-mcp](https://github.com/etweisberg/garmin-connect-mcp)**. Where the MCP
server serves external/third-party agent clients, this CLI serves local
consumers: coding agents (via skills), ops scripts, cron/systemd timers, and
humans over ssh. See the CircleCI "MCP vs CLI" framing: CLIs for the inner
loop, MCP for the outer loop — this repo is the inner loop.

Built on [garminconnect](https://pypi.org/project/garminconnect/) (pure-Python
SSO — no Playwright and no Node). Dependencies are resolved and hash-locked in
`uv.lock`.

## Guarantees

- **Endpoint parity** — data commands call the *same* connectapi paths and
  query parameters as garmin-mcp's `src/tools.ts`, so responses (and the
  staged-file format) are drop-in compatible with existing consumers.
- **Isolated session** — garth OAuth tokens live in `~/.garmin-cli/tokens`
  (garmin-mcp uses its own store at `~/.garmin-connect-mcp`). Running this CLI
  never touches the running MCP daemon or its session.
- **Script-friendly** — data on stdout as JSON, diagnostics on stderr, exit
  codes: `0` ok, `1` error, `2` usage, `75` auth required.

## Install

```bash
uv python install 3.13
uv sync
garmin-cli --help
```

The repository pins Python 3.13 through `.python-version`; `uv sync` creates a
clean project environment from `uv.lock`. Python 3.14 is intentionally excluded
until the Garmin dependency stack supports it.

## Auth

Login uses email/password with automatic email-OTP MFA, fully non-interactive:

```bash
garmin-cli --env-file /path/to/local.env auth login
garmin-cli auth check
```

Env vars (file format `KEY=VALUE`):

| Variable | Purpose |
|---|---|
| `GARMIN_EMAIL` | Garmin account email (also the OTP mailbox) |
| `GARMIN_PASSWORD` | Garmin account password |
| `GARMIN_GMAIL_APP_PASSWORD` | Gmail IMAP app password to fetch OTP emails |
| `GARMIN_CLI_TOKENSTORE` | Optional token-dir override (default `~/.garmin-cli/tokens`) |

OTP emails are only trusted when the envelope sender is
`alerts@account.garmin.com`, the subject is `Your Security Passcode`, and
Gmail's topmost `Authentication-Results` shows `spf=pass`/`dkim=pass` for
garmin.com — mirroring garmin-mcp's `mfa-email.ts` rules.

## Commands

```text
garmin-cli auth login | auth check
garmin-cli activities list [--limit N] [--start N]
garmin-cli activities get|details|splits|hr-zones|weather|polyline <activityId>
garmin-cli daily summary|heart-rate|stress|intensity|movement|respiration|chart [--date YYYY-MM-DD]
garmin-cli wellness sleep|body-battery|hrv [--date YYYY-MM-DD]
garmin-cli weight [--start YYYY-MM-DD] [--end YYYY-MM-DD]
garmin-cli profile
garmin-cli stage --latest-running [-o garmin-staged.json]   # or --activity <id>
```

### `stage` — one-shot alpharunner staging

`stage` replaces the four-call MCP flow (`list-activities` → `get-activity` →
`get-activity-splits` → `get-activity-hr-zones` + hand-assembly) with one
process that writes the exact `garmin-staged.json` consumed by
`pnpm db:sync-garmin`:

```bash
garmin-cli --env-file ../env-profiles/local.env stage --latest-running \
  -o /path/to/garmin-staged.json
```

## Full MCP-equivalent surface

`garmin-cli run-tests` prints the complete 41-capability inventory. The 20 capabilities that were previously MCP-only are available through:

```text
garmin-cli activities download-fit <activity-id> [--output-dir DIR]
garmin-cli fitness personal-records|stats|vo2max|hr-zones|goals|badges|badge-leaderboard|power-zones
garmin-cli fitness training-readiness|hydration YYYY-MM-DD
garmin-cli fitness sleep-stats START_DATE END_DATE
garmin-cli fitness calendar YEAR ZERO_BASED_MONTH
garmin-cli workouts list|get|download-fit|create|schedule|delete ...
garmin-cli run-tests
```

All commands preserve the JSON-stdout, diagnostics-stderr, and `0`/`1`/`2`/`75` exit-code contract. Sleep ranges may contain at most 28 inclusive calendar dates. Use `--tokenstore /home/john/.garmin-cli/tokens` under isolated Hermes profiles.

## Development

```bash
.venv/bin/python -m unittest tests.test_stage tests.test_commands -v
.venv/bin/python -m compileall -q src tests
```

## Why a CLI (and why the MCP remains installed as rollback)

Consumer-first analysis lives in the workspace `INVESTIGATION.md`. Local
consumers now use the CLI for fast, exit-code-driven workflows. The MCP package
remains installed and manually startable as a rollback, but local autostart is
not required after migration.
