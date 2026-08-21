"""Deterministic crisis-stage classifier.

This runs before generation, never after, and nothing downstream is allowed to
overturn it. That ordering is the whole point: a model asked "is this person in
crisis?" answers differently on Tuesday, and a safety decision that varies by
sampling temperature is not a safety decision.

The four stages come from the published crisis-triage tree — 探索 → 意図 →
計画 → 準備 — mapped onto Japanese expressions in
`data/crisis_taxonomy.csv`. The table lives in data, not here, because the
phrases are the part that a deploying workplace will want to extend and the
matching logic is the part it should not touch.

Three properties worth stating:

**Most severe wins.** A message that mentions both 希死念慮 and a specific
method is classified at `plan`, not at whichever pattern happened to match
first.

**Negation cancels one occurrence, not the message.** 「死にたいと思ったことは
ない」 cancels that occurrence of 死にたい. If the same message says 死にたい
again elsewhere without a cancelling phrase, that occurrence still counts. A
negation that swallowed the whole message would be trivially exploitable by
putting a disclaimer in the first line.

**Adjacency is bounded and asymmetric in the safe direction.** A cancelling
phrase must sit next to the signal — no sentence boundary, at most a few
particles in between. Widening that window would silence real signals; leaving
it too narrow only produces a helpline screen for someone who did not need one,
which is the error this system is content to make.

Nothing here stores or returns the respondent's words. The result carries the
rule ids that fired and a hash of the input; that is what goes to the audit log.
"""

from __future__ import annotations

import functools
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sentinel.core.datafiles import data_path, read_rows
from sentinel.core.errors import DataFileError, GateSetupError
from sentinel.core.gates import Gate, GateResult, require_non_empty
from sentinel.core.textnorm import squeeze

#: Stages in increasing order of severity. `none` is the absence of a signal.
LEVELS: tuple[str, ...] = ("none", "explore", "ideation", "plan", "prepared")

#: Severity rank, used to pick a winner when several rules fire.
SEVERITY: dict[str, int] = {level: rank for rank, level in enumerate(LEVELS)}

SIGNAL_LEVELS: frozenset[str] = frozenset(LEVELS[1:])
NEGATION_LEVEL = "negation"

#: Characters that may sit between a signal and a phrase that cancels it. Case
#: particles and spacing only: no sentence-ending punctuation, no content words.
#: Anything outside this set means the two are in different clauses, and a
#: cancellation across clauses is not a cancellation.
ADJACENCY_FILLERS: frozenset[str] = frozenset("のをはがもにでとやへなど、 　")

#: How many filler characters may separate them. Three covers 「自殺報道を
#: ニュースで見た」 and 「死にたいなどとは思わない」 without reaching across a
#: clause.
MAX_ADJACENCY_GAP = 3

#: Phrases that reverse a preceding negation — 「死にたいとは思わない、と言えば嘘に
#: なる」, 「死にたいと思ったことはない。でも今は死にたい」. When one of these follows
#: the cancelling negation, the cancellation is void: the respondent has taken
#: the disclaimer back. Only strong contrastive/contradiction markers are listed,
#: so an ordinary 「〜ないですが、疲れています」 (genuinely negated) is left cancelled.
REAFFIRMATION_MARKERS: tuple[str, ...] = (
    "でも",
    "けど",
    "けれど",
    "けれども",
    "しかし",
    "実は",
    "本当は",
    "本当のところ",
    "ほんとうは",
    "やっぱり",
    "と言えば",
    "といえば",
    "嘘",
    "うそ",
)

TAXONOMY_FILE = "crisis_taxonomy.csv"


@dataclass(frozen=True)
class Signal:
    """One row of the taxonomy, compiled."""

    id: str
    level: str
    kind: str
    value: str
    note: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Occurrence:
    """One place in the text where a rule matched."""

    rule_id: str
    level: str
    start: int
    end: int
    cancelled_by: str = ""

    @property
    def cancelled(self) -> bool:
        return bool(self.cancelled_by)


@dataclass(frozen=True)
class Taxonomy:
    """The compiled contents of `crisis_taxonomy.csv`."""

    signals: tuple[Signal, ...]
    negations: tuple[Signal, ...]
    source: str = ""

    def validate(self) -> None:
        """Refuse a taxonomy that cannot detect anything (R3-G4).

        An empty negation list is *not* an error: with no cancelling phrases the
        classifier over-detects, and over-detection shows someone a helpline they
        did not need. An empty signal list under-detects silently, which is the
        failure this project exists to prevent.
        """
        require_non_empty("crisis", self.signals)


@dataclass(frozen=True)
class CrisisResult:
    """The verdict for one piece of free text.

    Attributes:
        level: one of `LEVELS`.
        matched_ids: taxonomy ids that fired and survived negation.
        negated_ids: taxonomy ids whose every occurrence was cancelled. Kept so
            that a reviewer can see the classifier considered and dismissed
            them, without the text itself being stored anywhere.
        text_sha256: hash of the input. The receipt that lets an audit entry
            refer to a specific submission without retaining what it said.
    """

    level: str
    matched_ids: tuple[str, ...] = ()
    negated_ids: tuple[str, ...] = ()
    text_sha256: str = ""

    @property
    def detected(self) -> bool:
        """Whether generation must be bypassed for this input."""
        return self.level != "none"

    @property
    def severity(self) -> int:
        return SEVERITY[self.level]

    def as_dict(self) -> dict[str, object]:
        """Audit-safe projection. Contains no respondent text by construction."""
        return {
            "level": self.level,
            "matched_ids": list(self.matched_ids),
            "negated_ids": list(self.negated_ids),
            "text_sha256": self.text_sha256,
        }


def _compile(kind: str, value: str, rule_id: str, path: Path) -> re.Pattern[str]:
    if kind in ("keyword", "negation"):
        # Matched against the squeezed judgment copy, so the pattern is compiled
        # from the squeezed value: a rule and the text it checks are normalised
        # the same way, or a full-width/spaced variant would slip past a rule
        # written in the plain form.
        return re.compile(re.escape(squeeze(value)))
    if kind == "regex":
        try:
            return re.compile(value)
        except re.error as exc:
            raise DataFileError(f"{path.name}: rule {rule_id} has an invalid regex: {exc}") from exc
    raise DataFileError(f"{path.name}: rule {rule_id} has unknown kind {kind!r}")


def load_taxonomy(csv_path: Path | str | None = None) -> Taxonomy:
    """Load and compile the crisis taxonomy.

    Raises:
        DataFileError: the file is missing or a row is malformed.
        GateSetupError: the file parsed but contains no signals.
    """
    path = Path(csv_path) if csv_path is not None else data_path(TAXONOMY_FILE)
    if not path.is_file():
        raise DataFileError(f"missing crisis taxonomy: {path}")

    signals: list[Signal] = []
    negations: list[Signal] = []
    seen: set[str] = set()

    for row in read_rows(path):
        rule_id = row.get("id", "").strip()
        level = row.get("level", "").strip()
        kind = row.get("kind", "").strip()
        value = row.get("value", "").strip()
        if not rule_id and not value:
            continue
        if not rule_id:
            raise DataFileError(f"{path.name}: a rule row has no id")
        if rule_id in seen:
            raise DataFileError(f"{path.name}: duplicate rule id {rule_id!r}")
        seen.add(rule_id)
        if not value:
            raise DataFileError(f"{path.name}: rule {rule_id} has an empty value")

        signal = Signal(
            id=rule_id,
            level=level,
            kind=kind,
            value=value,
            note=row.get("note", "").strip(),
            pattern=_compile(kind, value, rule_id, path),
        )
        if level == NEGATION_LEVEL:
            if kind != NEGATION_LEVEL:
                raise DataFileError(
                    f"{path.name}: rule {rule_id} is level=negation but kind={kind!r}"
                )
            negations.append(signal)
        elif level in SIGNAL_LEVELS:
            signals.append(signal)
        else:
            raise DataFileError(
                f"{path.name}: rule {rule_id} has unknown level {level!r};"
                f" expected one of {sorted(SIGNAL_LEVELS | {NEGATION_LEVEL})}"
            )

    taxonomy = Taxonomy(signals=tuple(signals), negations=tuple(negations), source=str(path))
    taxonomy.validate()
    return taxonomy


@functools.cache
def _cached_taxonomy() -> Taxonomy:
    return load_taxonomy()


def clear_caches() -> None:
    """Drop the cached taxonomy. Used by tests that point at another data dir."""
    _cached_taxonomy.cache_clear()


def _gap_is_adjacent(text: str, left_end: int, right_start: int) -> bool:
    """Whether two spans are close enough for one to cancel the other."""
    if right_start <= left_end:
        return True  # overlapping: the signal is part of the cancelling phrase
    gap = text[left_end:right_start]
    if len(gap) > MAX_ADJACENCY_GAP:
        return False
    return all(ch in ADJACENCY_FILLERS for ch in gap)


def _reaffirmed_after(text: str, index: int) -> bool:
    """Whether a marker that takes a negation back appears at or after `index`."""
    tail = text[index:]
    return any(marker in tail for marker in REAFFIRMATION_MARKERS)


def _cancelling_negation(
    text: str, occurrence_span: tuple[int, int], negation_spans: list[tuple[str, int, int]]
) -> str:
    """Return the id of a negation adjacent to this occurrence, or ``""``.

    A negation that is itself reversed by a later contradiction marker does not
    cancel: 「死にたいとは思わない、と言えば嘘になる」 is not a negation of the signal,
    it is a negation of the negation, and the safe reading is that the signal
    stands.
    """
    start, end = occurrence_span
    for neg_id, neg_start, neg_end in negation_spans:
        adjacent = (
            (neg_start >= end and _gap_is_adjacent(text, end, neg_start))
            or (neg_end <= start and _gap_is_adjacent(text, neg_end, start))
            or (neg_start < end and neg_end > start)
        )
        if not adjacent:
            continue
        if _reaffirmed_after(text, neg_end):
            continue
        return neg_id
    return ""


def occurrences(text: str, taxonomy: Taxonomy | None = None) -> tuple[Occurrence, ...]:
    """Return every rule match in `text`, each marked cancelled or not.

    Exposed because a reviewer screen and an eval report both need to see *why*
    a classification came out the way it did, and re-deriving that from the level
    alone is guesswork.
    """
    table = taxonomy if taxonomy is not None else _cached_taxonomy()
    # Match against the squeezed judgment copy: NFKC-folded, zero-width stripped,
    # whitespace removed. The original `text` is never mutated or stored; this is
    # a throwaway used only to decide the level, so 「死 に た い」 and a zero-width
    # split cannot walk a signal past the classifier.
    text = squeeze(text)
    if not text:
        return ()

    negation_spans = [
        (neg.id, m.start(), m.end()) for neg in table.negations for m in neg.pattern.finditer(text)
    ]

    found: list[Occurrence] = []
    for signal in table.signals:
        for match in signal.pattern.finditer(text):
            span = (match.start(), match.end())
            found.append(
                Occurrence(
                    rule_id=signal.id,
                    level=signal.level,
                    start=span[0],
                    end=span[1],
                    cancelled_by=_cancelling_negation(text, span, negation_spans),
                )
            )
    found.sort(key=lambda o: (o.start, o.end, o.rule_id))
    return tuple(found)


def classify(text: str, taxonomy: Taxonomy | None = None) -> CrisisResult:
    """Classify free text into a crisis stage.

    Args:
        text: the respondent's free-text answer. Never stored by this function.
        taxonomy: an alternative rule table. Defaults to the bundled CSV.

    Returns:
        A `CrisisResult`. `level == "none"` means no rule survived; anything else
        means the caller must skip generation and return the fixed helpline
        response (R3-G3).
    """
    digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    hits = occurrences(text, taxonomy)

    live = [o for o in hits if not o.cancelled]
    matched_ids = tuple(sorted({o.rule_id for o in live}))
    cancelled_ids = tuple(sorted({o.rule_id for o in hits if o.cancelled} - set(matched_ids)))

    level = "none"
    for occurrence in live:
        if SEVERITY[occurrence.level] > SEVERITY[level]:
            level = occurrence.level

    return CrisisResult(
        level=level,
        matched_ids=matched_ids,
        negated_ids=cancelled_ids,
        text_sha256=digest,
    )


class CrisisGate(Gate):
    """Gate form of the classifier, for `GateChain`.

    Runs first in the chain. A refusal here is not a rejection of the
    respondent's text — it is the instruction to stop generating and show the
    helplines instead.
    """

    name = "crisis"

    def __init__(self, taxonomy: Taxonomy | None = None, text_key: str = "text") -> None:
        self._taxonomy = taxonomy
        self._text_key = text_key

    @property
    def taxonomy(self) -> Taxonomy:
        if self._taxonomy is None:
            self._taxonomy = _cached_taxonomy()
        return self._taxonomy

    def validate_config(self) -> None:
        if not self._text_key:
            raise GateSetupError("CrisisGate requires a non-empty text_key")
        self.taxonomy.validate()

    def check(self, payload: dict[str, object]) -> GateResult:
        text = payload.get(self._text_key) or ""
        result = classify(str(text), self.taxonomy)
        if not result.detected:
            return GateResult(ok=True, gate=self.name, reasons=())
        return GateResult(
            ok=False,
            gate=self.name,
            reasons=(f"crisis:{result.level}", *result.matched_ids),
        )
