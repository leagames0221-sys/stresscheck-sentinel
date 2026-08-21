#!/usr/bin/env python3
"""Export one promptfoo run from its local SQLite store into a review-able JSON.

Why this exists: `promptfoo eval -o run.json` writes the raw results next to the
run, but that file is easy to lose, and the run behind
`docs/evidence/EVALS_RUN_2026-08-21.md` took 27 minutes of local generation to
produce. promptfoo also keeps every run in `~/.promptfoo/promptfoo.db`, so the
raw output can be recovered without paying for the run again. This reads that
store and writes the same shape `-o` writes, which is the shape
`evals/judge_agreement.py` reads.

Two things it does beyond a dump:

**Absolute local paths are redacted.** The stored config records the machine the
run happened on. `<repo>` and `<home>` are substituted so that the committed
evidence describes the run and not the workstation.

**Nothing is recomputed.** Verdicts, scores and judge reasons are copied
verbatim. If this script disagreed with the run it exported, the export would be
worthless as evidence.

Usage:
    python scripts/export_promptfoo_run.py --eval-id eval-llK-2026-08-21T08:18:16 \
        -o docs/evidence/promptfoo_run_2026-08-21.json
    python scripts/export_promptfoo_run.py --list
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path.home() / ".promptfoo" / "promptfoo.db"

#: promptfoo's ResultFailureReason.ERROR.
FAILURE_REASON_ERROR = 2

REPO_ROOT = Path(__file__).resolve().parent.parent


def _loads(value: object) -> object:
    if isinstance(value, str | bytes):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return value
    return value


def redact(node: object, replacements: list[tuple[str, str]]) -> object:
    """Replace machine-specific absolute paths anywhere in the structure."""
    if isinstance(node, str):
        text = node
        for needle, token in replacements:
            text = text.replace(needle, token)
            text = text.replace(needle.replace("\\", "\\\\"), token)
            text = text.replace(needle.replace("\\", "/"), token)
        return text
    if isinstance(node, list):
        return [redact(item, replacements) for item in node]
    if isinstance(node, dict):
        return {key: redact(item, replacements) for key, item in node.items()}
    return node


def list_evals(conn: sqlite3.Connection) -> int:
    query = "SELECT id, created_at, description FROM evals ORDER BY created_at"
    rows = conn.execute(query).fetchall()
    for row in rows:
        n = conn.execute(
            "SELECT COUNT(*) FROM eval_results WHERE eval_id = ?", (row["id"],)
        ).fetchone()[0]
        print(f"{row['id']}  results={n:>4}  {row['description']}")
    return 0


def export(conn: sqlite3.Connection, eval_id: str) -> dict[str, object]:
    header = conn.execute("SELECT * FROM evals WHERE id = ?", (eval_id,)).fetchone()
    if header is None:
        raise SystemExit(f"no such eval id in the local store: {eval_id}")

    rows = conn.execute(
        "SELECT * FROM eval_results WHERE eval_id = ? ORDER BY test_idx, prompt_idx",
        (eval_id,),
    ).fetchall()

    results: list[dict[str, object]] = []
    successes = failures = errors = 0
    for row in rows:
        grading = _loads(row["grading_result"]) or {}
        # promptfoo's ResultFailureReason: 0 = none, 1 = assertion, 2 = error.
        # The `error` column also carries the *reason text* of a plain assertion
        # failure, so counting non-empty `error` as an error would report four
        # judge disagreements as four crashes.
        if row["failure_reason"] == FAILURE_REASON_ERROR:
            errors += 1
        elif row["success"]:
            successes += 1
        else:
            failures += 1
        results.append(
            {
                "promptIdx": row["prompt_idx"],
                "testIdx": row["test_idx"],
                "testCase": _loads(row["test_case"]),
                "prompt": _loads(row["prompt"]),
                "provider": _loads(row["provider"]),
                "response": _loads(row["response"]),
                "error": row["error"],
                "success": bool(row["success"]),
                "score": row["score"],
                "latencyMs": row["latency_ms"],
                "cost": row["cost"],
                "gradingResult": grading,
                "namedScores": _loads(row["named_scores"]),
            }
        )

    config = _loads(header["config"])
    if isinstance(config, dict):
        # Where this machine happened to write its copy. Not a property of the run.
        config.pop("outputPath", None)

    payload = {
        "evalId": eval_id,
        "config": config,
        "results": {
            "timestamp": header["created_at"],
            "results": results,
            "stats": {
                "successes": successes,
                "failures": failures,
                "errors": errors,
                **(_loads(header["results"]) or {}),  # type: ignore[dict-item]
            },
        },
    }
    replacements = [(str(REPO_ROOT), "<repo>"), (str(Path.home()), "<home>")]
    redacted = redact(payload, replacements)

    # Redaction that is only attempted is redaction that eventually misses. The
    # account name is the thing worth not publishing, so check for it by name and
    # refuse to write rather than emit a file someone has to re-read.
    username = Path.home().name
    text = json.dumps(redacted, ensure_ascii=False)
    if username and username in text:
        index = text.find(username)
        raise SystemExit(
            "export aborted: an un-redacted local path survived redaction near "
            f"...{text[max(0, index - 120) : index + 80]}..."
        )
    return redacted  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="promptfoo SQLite store")
    parser.add_argument("--eval-id", help="eval id to export")
    parser.add_argument("--list", action="store_true", help="list the runs in the store")
    parser.add_argument("-o", "--out", type=Path, help="output file (default: stdout)")
    args = parser.parse_args(argv)

    if not args.db.is_file():
        raise SystemExit(f"promptfoo store not found: {args.db}")

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if args.list or not args.eval_id:
            return list_evals(conn)
        payload = export(conn, args.eval_id)
    finally:
        conn.close()

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text.encode('utf-8')):,} bytes)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
