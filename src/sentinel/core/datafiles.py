"""Locating and reading the CSVs under `data/`.

Every number that a workplace is allowed to change lives in a CSV, not in code.
That only works if reading those CSVs is boring and uniform, which is what this
module is for.

Resolution order for the data directory:

1. `$SENTINEL_DATA_DIR`, if set (used by tests and by anyone substituting their
   own criteria without touching the installed package).
2. `sentinel/_data/`, present in an installed wheel.
3. `<repo>/data/`, present in a source checkout.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterator
from pathlib import Path

from sentinel.core.errors import DataFileError

_PACKAGE_DATA = Path(__file__).resolve().parent.parent / "_data"
_REPO_DATA = Path(__file__).resolve().parents[3] / "data"


def data_dir() -> Path:
    """Return the directory holding the bundled CSVs."""
    override = os.environ.get("SENTINEL_DATA_DIR")
    if override:
        path = Path(override)
        if not path.is_dir():
            raise DataFileError(f"SENTINEL_DATA_DIR is not a directory: {path}")
        return path
    for candidate in (_PACKAGE_DATA, _REPO_DATA):
        if candidate.is_dir():
            return candidate
    raise DataFileError(
        "no data directory found; looked for "
        f"{_PACKAGE_DATA} and {_REPO_DATA}. Set SENTINEL_DATA_DIR to override."
    )


def data_path(name: str) -> Path:
    """Return the path of a bundled CSV, checking that it exists."""
    path = data_dir() / name
    if not path.is_file():
        raise DataFileError(f"missing data file: {path}")
    return path


def read_header_comments(path: Path) -> tuple[str, ...]:
    """Return the leading `#` comment lines of a CSV, without the `#`.

    These carry the provenance (`# source:` / `# license:`) that makes the file
    quotable. A test asserts that every shipped CSV has them, so they are part
    of the contract rather than decoration.
    """
    comments: list[str] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            comments.append(line[1:].strip())
    return tuple(comments)


def read_rows(path: Path) -> Iterator[dict[str, str]]:
    """Yield the data rows of a CSV, skipping the leading comment block.

    `csv.DictReader` has no notion of comments, so the comment lines are dropped
    before it ever sees them; otherwise the first `#` line would be taken as the
    header.
    """
    with path.open(encoding="utf-8", newline="") as fh:
        lines = [line for line in fh if not line.startswith("#")]
    if not lines:
        raise DataFileError(f"no header row in {path}")
    reader = csv.DictReader(lines)
    if reader.fieldnames is None:
        raise DataFileError(f"no header row in {path}")
    for row in reader:
        yield {k: (v if v is not None else "") for k, v in row.items()}


def require_int(row: dict[str, str], field: str, path: Path) -> int:
    """Read an int column, blaming the file and field when it is not one."""
    raw = row.get(field, "").strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise DataFileError(f"{path.name}: column '{field}' is not an integer: {raw!r}") from exc
