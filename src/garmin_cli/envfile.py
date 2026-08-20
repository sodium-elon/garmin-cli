"""Environment-file loading.

Reads KEY=VALUE files (the wrapper's env-profiles/*.env format) without ever
printing values. Secrets stay in memory only.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file and return it as a dict."""
    loaded: dict[str, str] = {}
    text = Path(path).expanduser().read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            loaded[key] = value
    return loaded


def apply_env_file(path: str | Path) -> dict[str, str]:
    """Load an env file and export keys that are not already set in os.environ."""
    loaded = load_env_file(path)
    for key, value in loaded.items():
        os.environ.setdefault(key, value)
    return loaded
