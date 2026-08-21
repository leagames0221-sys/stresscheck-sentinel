"""The one path a submission takes, from answers to a screen.

    answers ─▶ score (pure) ─▶ crisis classify (deterministic, first)
            ─▶ draft notice  (LLM, or canned text — never on a crisis)
            ─▶ gate chain    (crisis → samd_lint → signature)
            ─▶ implementer review (approve / edit / reject / respond)
            ─▶ signature recorded in the audit log
            ─▶ release to the respondent

There is exactly one function that returns text for a respondent to read
(`release`), and it cannot return without having run the chain. That is the
structural claim this module makes: not that every call site remembers to lint,
but that there is no call site which could forget.

Three consequences worth naming.

**Generation never sees a crisis.** When the classifier fires, no prompt is
built and no provider is called. The respondent gets the fixed helpline text
immediately — not after a review, because a queue is the wrong place for that
message to sit.

**The reviewer reviews the actual text.** The draft, including any generated
wording, is assembled before the interrupt and stored in it. A reviewer given
only a score cannot meaningfully choose `edit` over `approve`, and `respond`
would have nothing to replace. (`design.md` §3 draws generation after the
signature; assembling before it is what makes the four decisions mean anything,
and the signature still governs release.)

**No free text is stored.** The respondent's own words are classified in memory
and then dropped. What persists is the classification, the rule ids and a hash
(R7).
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import secrets
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel.core.audit import AuditLog, canonical_json
from sentinel.core.errors import SentinelError, StateError
from sentinel.core.gates import GateChain, SignatureGate
from sentinel.core.hitl import DECISIONS, HitlStore
from sentinel.core.llm import LLMProvider, get_provider
from sentinel.packs.crisis.classify import CrisisGate, CrisisResult, classify
from sentinel.packs.crisis.response import CRISIS_HEADLINE, fixed_response, load_hotlines
from sentinel.packs.jsq.scoring import ScoreResult, score
from sentinel.packs.samdlint.lint import SamdLintGate

STAGE_RESULT_REVIEW = "result_review"
STAGE_CRISIS_REVIEW = "crisis_review"

STATE_RELEASED = "released"
STATE_PENDING_REVIEW = "pending_review"
STATE_REJECTED = "rejected"
STATE_INVALID = "invalid"

TOKEN_LENGTH = 16

PROMPT_PLAIN = "result_plain_language"
PROMPT_SELFCARE = "selfcare_advice"

PENDING_MESSAGE = "実施者の確認が終わるまで結果は表示されません。"

#: Shown when the implementer rejects a draft. Fixed wording: the respondent is
#: told a person is handling it, and is not shown the draft that was rejected.
REJECTED_NOTICE = (
    "この結果は実施者が個別に確認しています。本画面での結果表示はいたしません。"
    "ご不明な点は実施者へお問い合わせください。"
)

INVALID_NOTICE = (
    "未回答の項目があるため、判定を確定していません。お手数ですが、もう一度回答をお願いします。"
)

#: Shown when a draft fails the chain. Deliberately says nothing about the
#: result: a replacement that summarised the blocked text would be the blocked
#: text.
SAFE_FALLBACK_NOTICE = (
    "結果の文面を表示できませんでした。実施者へお問い合わせください。"
    "この画面は医学的な診断ではありません。"
)

NOTICE_CLOSING = (
    "この画面の内容は医学的な診断ではありません。"
    "結果の取り扱いについては実施者へお問い合わせいただけます。"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    token        TEXT PRIMARY KEY,
    variant      TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    record_json  TEXT NOT NULL,
    created      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS server_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SubmissionNotFound(SentinelError):
    """No submission exists for this token."""


def _synchronized(method: Callable[..., Any]) -> Callable[..., Any]:
    """Serialise a method against the service's lock.

    The HTTP layer is a `ThreadingHTTPServer`, so two requests can arrive at the
    same submission at once — most obviously two reviewers hitting approve on
    the same interrupt. The SQLite connections are opened with
    `check_same_thread=False`, which makes cross-thread use *possible*; this
    lock is what makes it *correct*. Reentrant, because `release` runs the gate
    chain, which calls back into `has_signature`.
    """

    @functools.wraps(method)
    def wrapper(self: SentinelService, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


@dataclass(frozen=True)
class Submission:
    """One processed submission.

    Carries no free text. `crisis` holds the classification and the rule ids;
    the sentence that produced them is gone by the time this object exists.
    """

    token: str
    variant: str
    payload_hash: str
    score: ScoreResult
    crisis: CrisisResult
    requires_signature: bool
    state: str
    interrupt_id: str | None
    crisis_interrupt_id: str | None
    crisis_response: dict[str, Any] | None
    draft_notice: str
    draft_source: str
    audit_hash: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "variant": self.variant,
            "payload_hash": self.payload_hash,
            "sums": dict(self.score.sums),
            "high_stress": self.score.high_stress,
            "rule_hit": self.score.rule_hit,
            "valid": self.score.valid,
            "missing": list(self.score.missing),
            "crisis": self.crisis.as_dict(),
            "requires_signature": self.requires_signature,
            "state": self.state,
            "interrupt_id": self.interrupt_id,
            "crisis_interrupt_id": self.crisis_interrupt_id,
            "crisis_response": self.crisis_response,
            "draft_notice": self.draft_notice,
            "draft_source": self.draft_source,
            "notes": list(self.notes),
        }


def derive_token(seed: str | None = None, salt: str = "") -> str:
    """Turn an issued code into the only identifier this system keeps.

    The seed — whatever code the workplace handed the respondent — is combined
    with a server-side `salt`, hashed, and dropped. Without the salt the token
    cannot be recomputed from the issued code alone (F3): a short issued code is
    guessable by anyone who knows the issuing scheme, and hashing it unsalted
    let an attacker who knew the scheme derive every token offline. The salt is
    generated per server and never leaves it, so `sha256(salt + seed)` is not
    something an outsider can reproduce. The other half of the protection is
    unchanged: no name, address or employee number is ever accepted (R7-1).
    """
    material = seed if seed else secrets.token_hex(16)
    return hashlib.sha256((salt + material).encode("utf-8")).hexdigest()[:TOKEN_LENGTH]


def compute_payload_hash(token: str, variant: str, result: ScoreResult) -> str:
    """Hash the thing a signature will be bound to.

    Binding to the data rather than to the respondent is what stops a signature
    from surviving a change to the data it signed for.
    """
    material = canonical_json(
        {
            "token": token,
            "variant": variant,
            "sums": dict(result.sums),
            "high_stress": result.high_stress,
            "rule_hit": result.rule_hit,
            "valid": result.valid,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def high_stress_label(result: ScoreResult) -> str:
    if not result.valid:
        return "判定保留"
    return "該当" if result.high_stress else "非該当"


def prompt_variables(variant: str, result: ScoreResult) -> dict[str, Any]:
    """The complete variable set every prompt is written against."""
    return {
        "variant": variant,
        "sum_a": result.sums.get("A", 0),
        "sum_b": result.sums.get("B", 0),
        "sum_c": result.sums.get("C", 0),
        "high_stress_label": high_stress_label(result),
    }


def helpline_block() -> str:
    """The helpline list appended to every notice, from the published table."""
    lines = "\n".join(f"・{h.name}　{h.phone}　{h.hours}" for h in load_hotlines())
    return f"【相談窓口】\n{lines}"


def helpline_numbers_only() -> str:
    """Names and numbers, no prose.

    The degraded form of the crisis screen. It carries no assertions at all, so
    there is nothing in it for a lint rule to object to — which matters because
    the one outcome worse than showing unchecked prose is showing a person in
    crisis an empty page where the numbers were.
    """
    lines = "\n".join(f"{h.name}　{h.phone}" for h in load_hotlines())
    return f"{CRISIS_HEADLINE}\n{lines}"


class SentinelService:
    """The application flow. Owns the stores; knows nothing about HTTP."""

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        provider: LLMProvider | None = None,
        chain: GateChain | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.db_path = str(db_path)
        self.audit = AuditLog(self.db_path)
        self.hitl = HitlStore(self.db_path, audit=self.audit)
        # Threaded HTTP layer; every write is serialised by `_synchronized`.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        #: Server-side token salt (F3). Env wins so a deployment can pin it;
        #: otherwise it is generated once and kept in the local DB, so tokens are
        #: stable for one server but not computable from the issued code alone.
        self._token_salt = self._load_or_create_salt()

        self.provider = provider if provider is not None else get_provider()
        # Built here, not supplied by callers, so that the membership and order
        # of the chain is a property of the application rather than of whoever
        # happened to call it. Construction validates every gate (R3-G4), so a
        # service holding an empty dictionary fails to start.
        self.chain = chain if chain is not None else self.build_chain()

    def _load_or_create_salt(self) -> str:
        """Return the server token salt, generating and persisting one if needed.

        `SENTINEL_TOKEN_SALT` takes priority, so an operator can pin the salt
        across restarts (and across replicas). With no env var, a 256-bit salt
        is generated with `secrets` and stored in the local DB — which is
        `.gitignore`d as `*.db`, so the salt is never committed (R7). An
        in-memory DB regenerates it per process, which is exactly right for a
        test: nothing to leak, nothing to persist.
        """
        env = os.environ.get("SENTINEL_TOKEN_SALT")
        if env:
            return env
        cur = self._conn.execute("SELECT value FROM server_config WHERE key = 'token_salt'")
        row = cur.fetchone()
        if row is not None:
            return str(row["value"])
        salt = secrets.token_hex(32)
        self._conn.execute(
            "INSERT INTO server_config (key, value) VALUES ('token_salt', ?)", (salt,)
        )
        self._conn.commit()
        return salt

    def build_chain(self) -> GateChain:
        return GateChain(
            [
                CrisisGate(),
                SamdLintGate(),
                SignatureGate(
                    # The gate is satisfied only by a signature recorded at the
                    # *result_review* stage. A crisis_review approval shares the
                    # submission's payload_hash but is a different stage, so it
                    # can never stand in for the result signature (F1).
                    has_signature=lambda payload_hash: self.has_signature(
                        payload_hash, stage=STAGE_RESULT_REVIEW
                    ),
                    applies_when=lambda payload: bool(payload.get("requires_signature")),
                ),
            ]
        )

    def close(self) -> None:
        self._conn.close()
        self.hitl.close()
        self.audit.close()

    def __enter__(self) -> SentinelService:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- signatures ---------------------------------------------------------

    @_synchronized
    def has_signature(self, payload_hash: str, stage: str | None = None) -> bool:
        """Whether a signature record exists for exactly this data and stage.

        Signatures live in the append-only audit log rather than in a table the
        application may update, which is what makes "the implementer signed
        this" a claim with evidence behind it (R3-G1).

        The `stage` is part of the match, not an afterthought (F1). A high-stress
        result is released only by a signature recorded at `result_review`; a
        `crisis_review` approval carries the same `payload_hash` but a different
        stage, and approving the crisis escalation must not release the result.
        Passing `stage=None` matches a signature at any stage, which is what a
        caller asking "was anything signed for this data at all?" wants.
        """
        return any(
            record.payload.get("payload_hash") == payload_hash
            and (stage is None or record.payload.get("stage") == stage)
            for record in self.audit.records(kind="signature")
        )

    # -- intake -------------------------------------------------------------

    @_synchronized
    def submit(
        self,
        answers: Mapping[int, int],
        variant: str = "57",
        free_text: str = "",
        token_seed: str | None = None,
    ) -> Submission:
        """Score one submission, classify its free text, and draft the notice.

        The free text is classified and then dropped. Nothing that reaches
        storage contains it.
        """
        result = score(answers, variant)
        crisis = classify(free_text or "")
        token = derive_token(token_seed, salt=self._token_salt)
        payload_hash = compute_payload_hash(token, variant, result)

        notes: list[str] = []
        crisis_payload: dict[str, Any] | None = None

        if crisis.detected:
            crisis_payload = fixed_response(crisis.level)
            draft = str(crisis_payload["text"])
            draft_source = "fixed_text"
            notes.append("危機シグナルを検知したため、生成を行わず固定の窓口案内を表示した。")
        else:
            draft, draft_source, generation_notes = self._draft_notice(variant, result)
            notes.extend(generation_notes)

        requires_signature = bool(result.high_stress and result.valid)

        interrupt_id: str | None = None
        if requires_signature:
            interrupt_id = self.hitl.interrupt(
                token,
                STAGE_RESULT_REVIEW,
                {
                    "token": token,
                    "variant": variant,
                    "payload_hash": payload_hash,
                    "sums": dict(result.sums),
                    "rule_hit": result.rule_hit,
                    "high_stress": result.high_stress,
                    "crisis_level": crisis.level,
                    "notice_text": draft,
                    "notice_source": draft_source,
                    "requires_signature": True,
                },
            )

        crisis_interrupt_id: str | None = None
        if crisis.detected:
            crisis_interrupt_id = self.hitl.interrupt(
                token,
                STAGE_CRISIS_REVIEW,
                {
                    "token": token,
                    "payload_hash": payload_hash,
                    "crisis_level": crisis.level,
                    "crisis_rule_ids": list(crisis.matched_ids),
                    "free_text_sha256": crisis.text_sha256,
                    "notice_text": draft,
                    "requires_signature": False,
                },
            )

        if not result.valid:
            state = STATE_INVALID
            notes.append("未回答の項目があるため判定を確定していない (R1-4)。")
        elif requires_signature:
            state = STATE_PENDING_REVIEW
        else:
            state = STATE_RELEASED

        audit_hash = self.audit.append(
            "submission",
            {
                "token": token,
                "variant": variant,
                "payload_hash": payload_hash,
                "sums": dict(result.sums),
                "high_stress": result.high_stress,
                "rule_hit": result.rule_hit,
                "valid": result.valid,
                "missing_count": len(result.missing),
                "crisis": crisis.as_dict(),
                "notice_source": draft_source,
                "state": state,
            },
        )

        submission = Submission(
            token=token,
            variant=variant,
            payload_hash=payload_hash,
            score=result,
            crisis=crisis,
            requires_signature=requires_signature,
            state=state,
            interrupt_id=interrupt_id,
            crisis_interrupt_id=crisis_interrupt_id,
            crisis_response=crisis_payload,
            draft_notice=draft,
            draft_source=draft_source,
            audit_hash=audit_hash,
            notes=tuple(notes),
        )
        self._store(submission)
        return submission

    def _draft_notice(self, variant: str, result: ScoreResult) -> tuple[str, str, list[str]]:
        """Assemble the notice body. Called only when no crisis was detected."""
        variables = prompt_variables(variant, result)
        plain = self.provider.generate(PROMPT_PLAIN, variables)
        selfcare = self.provider.generate(PROMPT_SELFCARE, variables)

        source = "fallback_text" if (plain.fallback and selfcare.fallback) else self.provider.name
        notes: list[str] = []
        if plain.fallback != selfcare.fallback:
            notes.append("生成文と定型文が混在した文面である。")

        body = "\n\n".join(
            [
                "【結果のご案内】",
                plain.text.strip(),
                "【セルフケアのご案内】",
                selfcare.text.strip(),
                NOTICE_CLOSING,
                helpline_block(),
            ]
        )
        return body, source, notes

    def _store(self, submission: Submission) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO submissions"
            " (token, variant, payload_hash, record_json, created) VALUES (?, ?, ?, ?, ?)",
            (
                submission.token,
                submission.variant,
                submission.payload_hash,
                canonical_json(submission.as_dict()),
                datetime.now(UTC).isoformat(timespec="microseconds"),
            ),
        )
        self._conn.commit()

    def _load(self, token: str) -> dict[str, Any]:
        cur = self._conn.execute("SELECT record_json FROM submissions WHERE token = ?", (token,))
        row = cur.fetchone()
        if row is None:
            raise SubmissionNotFound(f"no submission for token {token!r}")
        return dict(json.loads(row["record_json"]))

    # -- review -------------------------------------------------------------

    @_synchronized
    def pending_reviews(self, stage: str | None = None) -> list[dict[str, Any]]:
        """The implementer's queue. Contains no respondent free text."""
        return [
            {
                "interrupt_id": item.id,
                "token": item.token,
                "stage": item.stage,
                "state": item.state,
                "created": item.created,
                "payload": item.payload,
            }
            for item in self.hitl.pending(stage)
        ]

    @_synchronized
    def decide(
        self,
        interrupt_id: str,
        decision: str,
        actor: str,
        edited: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Apply one implementer decision and, unless rejected, record a signature.

        The signature is written *after* the state transition, so a replayed
        request hits `StateError` inside `resume` and never reaches this line —
        one decision produces one signature (R6-2).
        """
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision {decision!r}; expected one of {list(DECISIONS)}")

        record = self.hitl.get(interrupt_id)
        if record is None:
            raise StateError(f"no such interrupt: {interrupt_id}")
        token, stage = record.token, record.stage

        final = self.hitl.resume(interrupt_id, decision, actor, edited=edited, note=note)

        if decision == "reject":
            if stage == STAGE_RESULT_REVIEW:
                self._update_release(
                    token, state=STATE_REJECTED, text=REJECTED_NOTICE, source="fixed_text"
                )
            return {"state": STATE_REJECTED, "interrupt_id": interrupt_id, "decision": decision}

        payload_hash = str(final.get("payload_hash") or record.payload.get("payload_hash") or "")
        self.audit.append(
            "signature",
            {
                "payload_hash": payload_hash,
                "interrupt_id": interrupt_id,
                "stage": stage,
                "actor": actor,
                "decision": decision,
                "judgment": str(record.payload.get("rule_hit", "")),
            },
        )

        if stage == STAGE_RESULT_REVIEW:
            text = str(final.get("notice_text") or record.payload.get("notice_text") or "")
            source = (
                "implementer"
                if decision in ("edit", "respond")
                else str(record.payload.get("notice_source", ""))
            )
            self._update_release(token, state=STATE_RELEASED, text=text, source=source)

        return {
            "state": STATE_RELEASED,
            "interrupt_id": interrupt_id,
            "decision": decision,
            "payload_hash": payload_hash,
        }

    def _update_release(self, token: str, state: str, text: str, source: str) -> None:
        record = self._load(token)
        record["state"] = state
        record["draft_notice"] = text
        record["draft_source"] = source
        self._conn.execute(
            "UPDATE submissions SET record_json = ? WHERE token = ?",
            (canonical_json(record), token),
        )
        self._conn.commit()

    @_synchronized
    def sample_for_audit(self, n: int = 5, seed: str = "") -> list[dict[str, Any]]:
        """A reproducible sample of decided reviews, for spot-checking (R6-4)."""
        return [
            {
                "interrupt_id": item.id,
                "token": item.token,
                "stage": item.stage,
                "state": item.state,
                "created": item.created,
                "notice_text": item.payload.get("notice_text", ""),
            }
            for item in self.hitl.sample_for_audit(n, seed)
        ]

    # -- release ------------------------------------------------------------

    @_synchronized
    def release(self, token: str) -> dict[str, Any]:
        """Return what this respondent may see, having run the gate chain.

        The only function in the project that produces respondent-facing text,
        and it cannot produce any without a `GateChain.check` on the way out. A
        blocked text is replaced, never shown and never explained away.

        Which gate refused decides what happens next. `signature` means "not
        yet" and yields the waiting screen. Anything else means the text itself
        is unusable and is replaced.
        """
        record = self._load(token)
        state = str(record.get("state"))
        requires_signature = bool(record.get("requires_signature"))
        payload_hash = str(record.get("payload_hash", ""))
        text = str(record.get("draft_notice", ""))
        crisis_response = self._guarded_crisis_response(record)

        if state == STATE_REJECTED:
            # The rejection notice carries no result, so it needs no signature.
            text, requires_signature = REJECTED_NOTICE, False
        elif state == STATE_INVALID:
            text, requires_signature = INVALID_NOTICE, False

        verdict = self.chain.check(
            {
                "text": text,
                "payload_hash": payload_hash,
                "requires_signature": requires_signature,
            }
        )

        gate_notes: list[str] = []
        if not verdict.ok:
            # Two different events, deliberately two different record kinds. A
            # missing signature is the system waiting for a person, which is the
            # design working; a lint refusal is a draft that should never have
            # been drafted. Counting them together would bury the second in the
            # first, and the second is the number worth watching.
            self.audit.append(
                "notice.withheld" if verdict.gate == "signature" else "notice.blocked",
                {
                    "token": token,
                    "payload_hash": payload_hash,
                    "gate": verdict.gate,
                    "reasons": list(verdict.reasons),
                },
            )
            if verdict.gate == "signature":
                return {
                    "token": token,
                    "state": STATE_PENDING_REVIEW,
                    "text": "",
                    "crisis_response": crisis_response,
                    "message": PENDING_MESSAGE,
                    "gate": verdict.gate,
                    "gate_ok": False,
                    "reasons": list(verdict.reasons),
                    "notes": list(record.get("notes", [])),
                }
            text = SAFE_FALLBACK_NOTICE
            gate_notes.append(f"{verdict.gate} が文面を差し止めた: {', '.join(verdict.reasons)}")

        # The signature gate passed, so a pending record is pending no longer.
        effective_state = STATE_RELEASED if state == STATE_PENDING_REVIEW else state
        payload_out: dict[str, Any] = {
            "token": token,
            "state": effective_state,
            "text": text,
            "source": record.get("draft_source", ""),
            "crisis_response": crisis_response,
            "gate": verdict.gate,
            "gate_ok": verdict.ok,
            "reasons": list(verdict.reasons),
            "notes": [*record.get("notes", []), *gate_notes],
        }
        # The score and its domain sums accompany a result only when a result is
        # actually being shown. A rejected, invalid or withheld release carries a
        # fixed notice and no result, so it carries no high_stress/sums either —
        # the same minimal-information shape the withheld (signature) path above
        # already returns (G4). Leaking them here was an asymmetry, not a feature.
        if effective_state == STATE_RELEASED:
            payload_out["high_stress"] = record.get("high_stress")
            payload_out["sums"] = record.get("sums", {})
        return payload_out

    def _guarded_crisis_response(self, record: Mapping[str, Any]) -> dict[str, Any] | None:
        """Run the crisis screen through the chain before it is shown."""
        payload = record.get("crisis_response")
        if not isinstance(payload, dict):
            return None
        verdict = self.chain.check(
            {
                "text": str(payload.get("text", "")),
                "payload_hash": "crisis_response",
                "requires_signature": False,
            }
        )
        if verdict.ok:
            return {**payload, "gate_ok": True}
        self.audit.append(
            "notice.blocked",
            {
                "token": record.get("token"),
                "gate": verdict.gate,
                "reasons": list(verdict.reasons),
                "context": "crisis_response",
            },
        )
        return {**payload, "gate_ok": False, "text": helpline_numbers_only()}

    # -- diagnostics --------------------------------------------------------

    @_synchronized
    def gate_check(self, text: str, requires_signature: bool = False) -> dict[str, Any]:
        """Run every gate over a piece of text and report all verdicts.

        Used by `sentinel gate-check` and by the eval suite. Deliberately uses
        `check_detailed`: a report that stops at the first refusal hides the
        other reasons the text was unusable.
        """
        payload = {
            "text": text,
            "payload_hash": "gate-check",
            "requires_signature": requires_signature,
        }
        overall = self.chain.check(payload)
        return {
            "ok": overall.ok,
            "blocked_by": None if overall.ok else overall.gate,
            "gates": [
                {"gate": v.gate, "ok": v.ok, "reasons": list(v.reasons)}
                for v in self.chain.check_detailed(payload)
            ],
        }

    @_synchronized
    def kpi(self) -> dict[str, Any]:
        """Review statistics, plus the counts that say whether the gates fired."""
        data = dict(self.hitl.kpi())
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM submissions")
        data["submissions"] = int(cur.fetchone()["n"])
        data["crisis_detected"] = sum(
            1
            for record in self.audit.records(kind="submission")
            if str(record.payload.get("crisis", {}).get("level", "none")) != "none"
        )
        data["notice_blocked"] = sum(1 for _ in self.audit.records(kind="notice.blocked"))
        data["notice_withheld"] = sum(1 for _ in self.audit.records(kind="notice.withheld"))
        data["signatures"] = sum(1 for _ in self.audit.records(kind="signature"))
        data["audit_chain_ok"] = self.audit.verify_chain()
        return data
