#!/usr/bin/env python3
"""Refuse to commit files that belong to a private / local-only path.

.gitignore already covers these, but a `git add -f` or a stale index entry can
slip one through. This is the deterministic second layer: it fails the commit
instead of trusting that the ignore file was right.

Usage (pre-commit passes the staged filenames):
    python scripts/private_path_check.py <path> [<path> ...]
"""

from __future__ import annotations

import sys
from pathlib import PurePosixPath

# Directory names that must never appear anywhere in a committed path.
FORBIDDEN_DIRS = frozenset(
    {".claude", ".tmp", "tmp", "private", "secrets", "node_modules", "__pycache__"}
)

# Filename suffixes that must never be committed.
FORBIDDEN_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
)

# Exact filenames that must never be committed.
FORBIDDEN_NAMES = frozenset({"credentials.json"})


def offending_reason(path: str) -> str | None:
    """Return a human-readable reason if `path` must not be committed."""
    p = PurePosixPath(path.replace("\\", "/"))

    for part in p.parts[:-1]:
        if part in FORBIDDEN_DIRS:
            return f"private directory '{part}/'"

    if p.name in FORBIDDEN_NAMES:
        return f"forbidden filename '{p.name}'"

    if p.name == ".env" or p.name.startswith(".env."):
        return "environment file"

    for suffix in FORBIDDEN_SUFFIXES:
        if p.name.endswith(suffix):
            return f"forbidden suffix '{suffix}'"

    if "notes/private/" in p.as_posix():
        return "private notes"

    return None


def main(argv: list[str]) -> int:
    failures = [(path, reason) for path in argv if (reason := offending_reason(path))]
    if not failures:
        return 0

    print("private_path_check: refusing to commit local-only files", file=sys.stderr)
    for path, reason in failures:
        print(f"  {path}  ({reason})", file=sys.stderr)
    print(
        "\nThese paths are local-only by design. Remove them from the index"
        " (git restore --staged <path>) instead of forcing the commit.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
