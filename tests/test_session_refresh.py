from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "refresh-session-if-needed.sh"


class SessionRefreshScriptTests(unittest.TestCase):
    def make_fake_cli(self, directory: Path) -> tuple[Path, Path, Path]:
        state = directory / "session-valid"
        calls = directory / "calls.log"
        cli = directory / "garmin-cli"
        cli.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >> {calls!s}
                if [[ "${{1:-}} ${{2:-}}" == "auth check" ]]; then
                  [[ -f {state!s} ]]
                  exit
                fi
                if [[ "${{1:-}}" == "--env-file" && "${{3:-}} ${{4:-}}" == "auth login" ]]; then
                  touch {state!s}
                  exit 0
                fi
                exit 2
                """
            ),
            encoding="utf-8",
        )
        cli.chmod(0o755)
        return cli, state, calls

    def run_script(self, cli: Path, directory: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "GARMIN_CLI_BIN": str(cli),
                "GARMIN_ENV_FILE": str(directory / "local.env"),
                "GARMIN_REFRESH_LOCK_FILE": str(directory / "refresh.lock"),
                "GARMIN_LOGIN_TIMEOUT_SECONDS": "5",
            }
        )
        (directory / "local.env").write_text("GARMIN_EMAIL=test@example.com\n", encoding="utf-8")
        return subprocess.run(
            ["bash", str(SCRIPT)],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_healthy_session_is_silent_and_does_not_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            cli, state, calls = self.make_fake_cli(directory)
            state.touch()

            result = self.run_script(cli, directory)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", result.stdout)
            self.assertEqual(["--version", "auth check"], calls.read_text().splitlines())

    def test_invalid_session_performs_one_login_then_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            cli, state, calls = self.make_fake_cli(directory)

            result = self.run_script(cli, directory)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(state.exists())
            self.assertEqual("", result.stdout)
            self.assertEqual(
                [
                    "--version",
                    "auth check",
                    "auth check",
                    f"--env-file {directory / 'local.env'} auth login",
                    "auth check",
                ],
                calls.read_text().splitlines(),
            )


if __name__ == "__main__":
    unittest.main()
