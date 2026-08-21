"""Append-only audit log with a hash chain.

Approval records are kept here, separate from whatever conversation or payload
produced them. That separation is the point: a decision log that lives inside
the thing being decided about can be rewritten by the same code path that writes
it, and then it is not evidence.

Two mechanisms, because either alone is weak:

* **SQLite triggers** refuse `UPDATE` and `DELETE` on the table. This stops the
  ordinary accident — a stray query, a migration, a helpful cleanup script.
* **A hash chain** makes tampering *detectable* rather than merely inconvenient.
  Each row hashes the previous row's hash, so editing row 5 invalidates 6..n.
  Someone with the database file can still rewrite the whole chain; what they
  cannot do is change one row and leave the rest verifying.

What must never go in here: free-text answers. Record the crisis classification
and a hash, never the sentence the respondent wrote.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    seq          INTEGER PRIMARY KEY,
    ts           TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    prev_hash    TEXT    NOT NULL,
    row_hash     TEXT    NOT NULL UNIQUE
);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only: UPDATE refused');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only: DELETE refused');
END;
"""


@dataclass(frozen=True)
class AuditRecord:
    """One row of the log."""

    seq: int
    ts: str
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    row_hash: str


def canonical_json(payload: Any) -> str:
    """Serialise deterministically, so the same payload always hashes the same.

    Sorted keys and no incidental whitespace; non-ASCII is kept as-is because
    the payloads are Japanese and escaping them would make the stored row
    unreadable for no benefit.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_row_hash(seq: int, ts: str, kind: str, payload_json: str, prev_hash: str) -> str:
    """Hash one row, binding it to its position and its predecessor."""
    material = "|".join([prev_hash, str(seq), ts, kind, payload_json])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only SQLite audit log."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self.db_path = str(db_path)
        # `check_same_thread=False` because the HTTP layer is a
        # `ThreadingHTTPServer` and every request thread reaches the same log.
        # Callers must serialise their writes; `app.service.SentinelService`
        # does so with a single lock around every method that touches a store.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def append(self, kind: str, payload: dict[str, Any]) -> str:
        """Append a record and return its hash.

        The hash is the receipt: hand it to whatever produced the event, and
        that thing can later prove which log entry it corresponds to.
        """
        if not kind:
            raise ValueError("audit record kind must be a non-empty string")

        payload_json = canonical_json(payload)
        ts = datetime.now(UTC).isoformat(timespec="microseconds")

        cur = self._conn.execute("SELECT seq, row_hash FROM audit ORDER BY seq DESC LIMIT 1")
        last = cur.fetchone()
        seq = 1 if last is None else int(last["seq"]) + 1
        prev_hash = GENESIS_HASH if last is None else str(last["row_hash"])

        row_hash = compute_row_hash(seq, ts, kind, payload_json, prev_hash)
        self._conn.execute(
            "INSERT INTO audit (seq, ts, kind, payload_json, prev_hash, row_hash)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (seq, ts, kind, payload_json, prev_hash, row_hash),
        )
        self._conn.commit()
        return row_hash

    def records(self, kind: str | None = None) -> Iterator[AuditRecord]:
        """Yield records in order, optionally filtered by kind."""
        if kind is None:
            cur = self._conn.execute("SELECT * FROM audit ORDER BY seq")
        else:
            cur = self._conn.execute("SELECT * FROM audit WHERE kind = ? ORDER BY seq", (kind,))
        for row in cur:
            yield AuditRecord(
                seq=int(row["seq"]),
                ts=str(row["ts"]),
                kind=str(row["kind"]),
                payload=json.loads(row["payload_json"]),
                prev_hash=str(row["prev_hash"]),
                row_hash=str(row["row_hash"]),
            )

    def __len__(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM audit")
        return int(cur.fetchone()["n"])

    def verify_chain(self) -> bool:
        """Recompute the whole chain and report whether it still holds.

        Checks three things: that sequence numbers are contiguous from 1, that
        each row's `prev_hash` is its predecessor's hash, and that each row's
        own hash matches its content.
        """
        expected_prev = GENESIS_HASH
        expected_seq = 1
        cur = self._conn.execute("SELECT * FROM audit ORDER BY seq")
        for row in cur:
            if int(row["seq"]) != expected_seq:
                return False
            if str(row["prev_hash"]) != expected_prev:
                return False
            recomputed = compute_row_hash(
                int(row["seq"]),
                str(row["ts"]),
                str(row["kind"]),
                str(row["payload_json"]),
                str(row["prev_hash"]),
            )
            if recomputed != str(row["row_hash"]):
                return False
            expected_prev = recomputed
            expected_seq += 1
        return True

    def head(self) -> str:
        """Return the hash of the newest record, or the genesis hash if empty."""
        cur = self._conn.execute("SELECT row_hash FROM audit ORDER BY seq DESC LIMIT 1")
        row = cur.fetchone()
        return GENESIS_HASH if row is None else str(row["row_hash"])
