"""Human-in-the-loop review: interrupt, wait, resume.

The state machine is small on purpose:

    PENDING --approve--> SIGNED
            --edit-----> SIGNED_EDITED     (the edit is stored as a diff)
            --reject---> REJECTED          (nothing proceeds downstream)
            --respond--> SIGNED_MANUAL     (the reviewer's own text is used;
                                            the generated text is discarded)

Four decisions rather than approve/reject, because a reviewer who can only say
yes or no will say yes to something they would have preferred to reword. `edit`
and `respond` are the two ways of saying "not that, this", and separating them
matters for the override statistics: an edit means the draft was close, a
respond means it was not usable.

**Idempotence.** An interrupt id is `sha256(token:stage)` — derived from where
in the flow it happened, not from a counter or a timestamp. Re-entering the same
stage for the same respondent finds the existing interrupt instead of opening a
second one, so a refreshed browser or a retried job does not queue two reviews.

**No double side effects.** Resuming anything that is not PENDING raises
`StateError`. The downstream effect happens after the transition, so a replayed
resume hits the state check first and never reaches the effect.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

from sentinel.core.audit import AuditLog, canonical_json
from sentinel.core.errors import StateError

Decision = Literal["approve", "edit", "reject", "respond"]
DECISIONS: tuple[str, ...] = get_args(Decision)

STATE_PENDING = "PENDING"
STATE_BY_DECISION: dict[str, str] = {
    "approve": "SIGNED",
    "edit": "SIGNED_EDITED",
    "reject": "REJECTED",
    "respond": "SIGNED_MANUAL",
}
#: States from which downstream work may proceed.
SIGNED_STATES = frozenset({"SIGNED", "SIGNED_EDITED", "SIGNED_MANUAL"})

INTERRUPT_ID_LENGTH = 16

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interrupts (
    id           TEXT PRIMARY KEY,
    token        TEXT NOT NULL,
    stage        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state        TEXT NOT NULL,
    created      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    interrupt_id TEXT NOT NULL,
    decision     TEXT NOT NULL,
    actor        TEXT NOT NULL,
    diff_json    TEXT NOT NULL,
    note         TEXT NOT NULL,
    ts           TEXT NOT NULL,
    PRIMARY KEY (interrupt_id, ts),
    FOREIGN KEY (interrupt_id) REFERENCES interrupts(id)
);

CREATE INDEX IF NOT EXISTS interrupts_state ON interrupts(state);
"""


@dataclass(frozen=True)
class Interrupt:
    """A paused piece of work awaiting a human decision."""

    id: str
    token: str
    stage: str
    payload: dict[str, Any]
    state: str
    created: str

    @property
    def is_pending(self) -> bool:
        return self.state == STATE_PENDING


@dataclass(frozen=True)
class DecisionRecord:
    """One decision as recorded."""

    interrupt_id: str
    decision: str
    actor: str
    diff: dict[str, Any]
    note: str
    ts: str


def interrupt_id(token: str, stage: str) -> str:
    """Derive the deterministic id for a (respondent, stage) pair."""
    digest = hashlib.sha256(f"{token}:{stage}".encode()).hexdigest()
    return digest[:INTERRUPT_ID_LENGTH]


def _diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Record what changed, as `{key: {"from": ..., "to": ...}}`."""
    changes: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old != new:
            changes[key] = {"from": old, "to": new}
    return changes


class HitlStore:
    """SQLite-backed interrupt/resume store."""

    def __init__(self, db_path: Path | str = ":memory:", audit: AuditLog | None = None) -> None:
        self.db_path = str(db_path)
        # See the note in `core.audit`: the HTTP layer is threaded, and writes
        # are serialised by the caller (`app.service.SentinelService`).
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._audit = audit

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> HitlStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def interrupt(self, token: str, stage: str, payload: dict[str, Any]) -> str:
        """Pause work and return the interrupt id.

        Idempotent: calling this again for the same `(token, stage)` returns the
        same id and leaves the stored payload alone, including after the
        interrupt has been decided. Overwriting the payload of an already-decided
        interrupt would mean the reviewer signed for something other than what
        the record now says.
        """
        if not token:
            raise ValueError("token must be a non-empty string")
        if not stage:
            raise ValueError("stage must be a non-empty string")

        iid = interrupt_id(token, stage)
        existing = self.get(iid)
        if existing is not None:
            return iid

        created = datetime.now(UTC).isoformat(timespec="microseconds")
        self._conn.execute(
            "INSERT INTO interrupts (id, token, stage, payload_json, state, created)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (iid, token, stage, canonical_json(payload), STATE_PENDING, created),
        )
        self._conn.commit()
        self._record_audit(
            "hitl.interrupt",
            {"interrupt_id": iid, "stage": stage, "state": STATE_PENDING},
        )
        return iid

    def resume(
        self,
        interrupt_id: str,
        decision: Decision,
        actor: str,
        edited: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Apply a human decision and return the payload that proceeds.

        Returns:
            `approve` — the original payload, unchanged.
            `edit` — the original payload with `edited` merged over it.
            `respond` — `edited` verbatim; the generated draft is discarded.
            `reject` — an empty dict. Nothing proceeds; the caller must treat an
            empty result as "stop", and `get()` reports `REJECTED`.

        Raises:
            StateError: the interrupt is unknown, or already decided.
            ValueError: unknown decision, empty actor, or `edit`/`respond`
                without `edited`.
        """
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision {decision!r}; expected one of {list(DECISIONS)}")
        if not actor:
            raise ValueError(
                "actor must be a non-empty string: an unattributed decision is not one"
            )

        record = self.get(interrupt_id)
        if record is None:
            raise StateError(f"no such interrupt: {interrupt_id}")
        if not record.is_pending:
            raise StateError(
                f"interrupt {interrupt_id} is already in state {record.state};"
                " it cannot be decided twice. This is the guard that stops a"
                " replayed or double-submitted review from acting twice."
            )
        if decision in ("edit", "respond") and not edited:
            raise ValueError(f"decision {decision!r} requires the reviewer's content in `edited`")

        if decision == "approve":
            final: dict[str, Any] = dict(record.payload)
            diff: dict[str, Any] = {}
        elif decision == "edit":
            final = {**record.payload, **(edited or {})}
            diff = _diff(record.payload, final)
        elif decision == "respond":
            final = dict(edited or {})
            diff = _diff(record.payload, final)
        else:  # reject
            final = {}
            diff = {}

        new_state = STATE_BY_DECISION[decision]
        ts = datetime.now(UTC).isoformat(timespec="microseconds")
        self._conn.execute(
            "UPDATE interrupts SET state = ? WHERE id = ?", (new_state, interrupt_id)
        )
        self._conn.execute(
            "INSERT INTO decisions (interrupt_id, decision, actor, diff_json, note, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (interrupt_id, decision, actor, canonical_json(diff), note, ts),
        )
        self._conn.commit()

        self._record_audit(
            "hitl.decision",
            {
                "interrupt_id": interrupt_id,
                "decision": decision,
                "actor": actor,
                "state": new_state,
                "changed_keys": sorted(diff),
            },
        )
        return final

    def get(self, interrupt_id: str) -> Interrupt | None:
        cur = self._conn.execute("SELECT * FROM interrupts WHERE id = ?", (interrupt_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return Interrupt(
            id=str(row["id"]),
            token=str(row["token"]),
            stage=str(row["stage"]),
            payload=json.loads(row["payload_json"]),
            state=str(row["state"]),
            created=str(row["created"]),
        )

    def pending(self, stage: str | None = None) -> list[Interrupt]:
        """Return the review queue, oldest first."""
        if stage is None:
            cur = self._conn.execute(
                "SELECT * FROM interrupts WHERE state = ? ORDER BY created", (STATE_PENDING,)
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM interrupts WHERE state = ? AND stage = ? ORDER BY created",
                (STATE_PENDING, stage),
            )
        return [
            Interrupt(
                id=str(r["id"]),
                token=str(r["token"]),
                stage=str(r["stage"]),
                payload=json.loads(r["payload_json"]),
                state=str(r["state"]),
                created=str(r["created"]),
            )
            for r in cur
        ]

    def decisions(self, interrupt_id: str | None = None) -> list[DecisionRecord]:
        if interrupt_id is None:
            cur = self._conn.execute("SELECT * FROM decisions ORDER BY ts")
        else:
            cur = self._conn.execute(
                "SELECT * FROM decisions WHERE interrupt_id = ? ORDER BY ts", (interrupt_id,)
            )
        return [
            DecisionRecord(
                interrupt_id=str(r["interrupt_id"]),
                decision=str(r["decision"]),
                actor=str(r["actor"]),
                diff=json.loads(r["diff_json"]),
                note=str(r["note"]),
                ts=str(r["ts"]),
            )
            for r in cur
        ]

    def is_signed(self, interrupt_id: str) -> bool:
        """Whether this interrupt reached a state that releases downstream work."""
        record = self.get(interrupt_id)
        return record is not None and record.state in SIGNED_STATES

    def kpi(self) -> dict[str, Any]:
        """Decision counts and the override rate.

        The override rate — the share of decisions that were not a plain
        approve — is the number that tells you whether the human in the loop is
        reviewing or rubber-stamping. A rate near zero is not obviously good
        news; it is the shape a loop takes just before it stops being a loop.
        """
        counts = dict.fromkeys(DECISIONS, 0)
        cur = self._conn.execute("SELECT decision, COUNT(*) AS n FROM decisions GROUP BY decision")
        for row in cur:
            counts[str(row["decision"])] = int(row["n"])

        total = sum(counts.values())
        approve = counts["approve"]
        override_rate = 0.0 if total == 0 else round(1 - approve / total, 4)
        return {
            "total": total,
            "override_rate": override_rate,
            "pending": len(self.pending()),
            **counts,
        }

    def sample_for_audit(self, n: int, seed: str = "") -> list[Interrupt]:
        """Draw a reproducible sample of decided interrupts for spot-checking.

        Deterministic rather than random: the sample is ordered by
        `sha256(seed + id)`, so the same seed always yields the same sample and
        a reviewer can be asked to justify a selection after the fact. A sample
        drawn with `random` cannot be re-derived, which makes it useless as
        evidence that the sampling was not steered.
        """
        if n < 0:
            raise ValueError("sample size must not be negative")
        cur = self._conn.execute("SELECT * FROM interrupts WHERE state != ?", (STATE_PENDING,))
        records = [
            Interrupt(
                id=str(r["id"]),
                token=str(r["token"]),
                stage=str(r["stage"]),
                payload=json.loads(r["payload_json"]),
                state=str(r["state"]),
                created=str(r["created"]),
            )
            for r in cur
        ]
        records.sort(key=lambda rec: hashlib.sha256(f"{seed}{rec.id}".encode()).hexdigest())
        return records[:n]

    def _record_audit(self, kind: str, payload: dict[str, Any]) -> None:
        if self._audit is not None:
            self._audit.append(kind, payload)
