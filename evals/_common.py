"""Shared plumbing for the deterministic eval layer.

`evals/` is allowed to import the product (that is the point of layer 1) but the
product must never import `evals/` back — the dependency runs one way only, as
stated in docs/spec/design.md §2 Boundary/Depends.

Everything here is stdlib. The deterministic layer has to be runnable on a
machine with no node, no Ollama and no network, because that is the layer whose
result is allowed to gate a release.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
SRC_DIR = REPO_ROOT / "src"
GOLDSETS = EVALS_DIR / "goldsets"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def read_goldset(name: str) -> list[dict[str, str]]:
    """Read a goldset CSV, dropping the leading `#` provenance block."""
    path = GOLDSETS / name if not Path(name).is_absolute() else Path(name)
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    return list(csv.DictReader(lines))


def as_bool(value: str) -> bool:
    """Parse the goldset's `true`/`false` spelling. Anything else is a data bug."""
    text = (value or "").strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no", ""):
        return False
    raise ValueError(f"not a boolean goldset value: {value!r}")


def parse_answers(answers: str) -> dict[int, int]:
    """`"3 2 - 4 ..."` -> `{1: 3, 2: 2, 4: 4, ...}`. `-` means unanswered."""
    out: dict[int, int] = {}
    for index, token in enumerate(answers.split(), start=1):
        if token == "-":
            continue
        out[index] = int(token)
    return out


def independent_sums(answers: dict[int, int], variant: str) -> dict[str, int]:
    """Total the domains again, from the CSVs, without touching scoring.py.

    A goldset that is only ever compared against the implementation it is
    guarding cannot fail. This second, dumber implementation exists so that the
    comparison in `run_deterministic.py` has two independent sides: the expected
    sums in the goldset, and this recomputation, are both derived from the data
    files rather than from `score()`.
    """
    items_path = REPO_ROOT / "data" / f"jsq_items_{variant}.csv"
    rows = list(
        csv.DictReader(
            line
            for line in items_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("#")
        )
    )
    thresholds_path = REPO_ROOT / "data" / "jsq_thresholds.csv"
    reverse: set[int] = set()
    for row in csv.DictReader(
        line
        for line in thresholds_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ):
        if row["variant"] == variant and row["kind"] == "reverse_items":
            reverse.update(int(x) for x in row["items"].split())

    sums = {"A": 0, "B": 0, "C": 0}
    for row in rows:
        if row["scored"] != "1":
            continue
        item_no = int(row["item_no"])
        value = answers.get(item_no)
        if value is None:
            continue
        sums[row["domain"]] += (5 - value) if item_no in reverse else value
    return sums


def grading(passed: bool, reason: str, score: float | None = None) -> dict[str, Any]:
    """A promptfoo GradingResult."""
    return {
        "pass": passed,
        "score": (1.0 if passed else 0.0) if score is None else score,
        "reason": reason,
    }
