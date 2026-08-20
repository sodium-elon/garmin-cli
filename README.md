# garmin-cli

CLI for Garmin Connect — the **inner-loop companion to
[garmin-mcp](https://github.com/etweisberg/garmin-connect-mcp)**. Where the MCP
server serves external/third-party agent clients, this CLI serves local
consumers: coding agents (via skills), ops scripts, cron/systemd timers, and
humans over ssh. See the CircleCI "MCP vs CLI" framing: CLIs for the inner
loop, MCP for the outer loop — this repo is the inner loop.

Built on [garminconnect](https://pypi.org/project/garminconnect/) /
[garth](https://pypi.org/project/garth/) (pure-Python SSO — no Playwright, no
Node). Note: garth is currently marked deprecated upstream; pinned and working,
tracked as a maintenance risk.

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
uv venv && uv pip install -e .          # or: pip install .
garmin-cli --help
```

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

## Why a CLI (and why the MCP stays)

Consumer-first analysis lives in the workspace `INVESTIGATION.md`. Short
version: every *local* consumer of Garmin data is an inner-loop consumer
(personal, fast, exit-code driven); external npm consumers of the MCP package
run their own server instance and never needed the local daemon. Both
interfaces coexist; consumers migrate when they choose to.
