"""Forbidden-expression lint.

A workplace stress check is not a medical device, and the line it must not cross
is narrow and specific: presenting the likelihood of a named condition. The
regulator is explicit that adding a disclaimer does not move a function back
across that line — so this project does not rely on one. Every outbound
sentence, generated or canned, passes through here first.

Two rule shapes, from `data/samd_forbidden.csv`:

**`regex` — refuse on sight.** These encode whole forbidden constructions:
a percentage next to the word リスク, a sentence that names itself a diagnosis,
an instruction to go and be treated.

**`disease` + `claim` — refuse the combination, within one sentence.** Neither
half is forbidden alone, and that asymmetry is the design. Self-care wording has
to be able to say ストレス反応, and an explanatory page has to be able to name a
condition in a sentence about what this tool does *not* do. What it may never do
is put a condition and a likelihood in the same sentence. Scoping the pair to a
sentence — not to the whole document — is what keeps that distinction usable.

**`disease` + `dx` — refuse an assertive copula glued to the condition name.**
A plain 「です」「だ」「である」 cannot be a claim word on its own: 「うつ病について
学ぶことは大切です」 and 「うつ病は治療可能な病気です」 are education, not a
diagnosis, and a sentence-scoped pair rule would wrongly block them. The line a
diagnosis crosses is asserting the condition *of the reader*: 「これはうつ病です」
「あなたの状態はうつ病だ」「うつ病である」. Two things separate the two:

*Adjacency.* In a diagnosis the copula sits immediately after the condition name,
with nothing between; in the educational sentences the condition is the topic and
the copula lands on some other word (病気です, 相談窓口です, 大切です). So a `dx`
term fires only when its match begins exactly where a `disease` match ends.

*Clause position, for the bare copulas.* だ/である/です double as continuation
forms, so being glued is not enough: 「うつ病だからといって…」「うつ病だけではない」
「うつ病だと思っていました」「うつ病だが働けています」「うつ病である可能性は誰にでも
ある」 all glue a bare copula to the name but continue the clause, and none is a
diagnosis of the reader. A bare copula (`_CLAUSE_FINAL_DX`) therefore fires only
at a clause end (`_is_clause_final`); a continuation particle after it cancels it.
The content-bearing phrases 「に違いない」「と思われ」「を意味し」 assert a diagnosis
wherever they attach, so they skip the clause-final check and may reach across one
bridging copula (「うつ病だと思われます」 fires).

*Known residual — honest, not "solved".* A bare copula at a clause end is
indistinguishable, at the level of the adjacent copula, from a topic sentence that
happens to end there: 「今日のテーマはうつ病です」 is over-blocked on the safe side.
And a dictionary is not exhaustive: an assertive form not in the table (断定します,
のようだ, 患っています) or a diagnosis with a particle wedged between the name and the
copula can be missed. This rule narrows the diagnosis-of-the-reader construction; it
does not claim to catch every one, nor to pass every topic sentence.

The reasons this returns are rule ids. The offending sentence is deliberately
not included: these results go to the audit log, and the text being linted may
be a respondent's own words.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

from sentinel.core.datafiles import data_path, read_rows
from sentinel.core.errors import DataFileError, GateSetupError
from sentinel.core.gates import Gate, GateResult, require_non_empty
from sentinel.core.textnorm import fold, squeeze

FORBIDDEN_FILE = "samd_forbidden.csv"

GATE_NAME = "samd_lint"

#: Sentence boundaries. Full stops only — a comma-spliced sentence is still one
#: sentence, and splitting on commas would let a forbidden pair hide behind 、.
_SENTENCE_SPLIT = re.compile(r"[。．!?！？\n\r]+")

KIND_DISEASE = "disease"
KIND_CLAIM = "claim"
KIND_REGEX = "regex"
#: An assertive copula that only counts when it is glued to a condition name.
#: Matched like disease/claim (squeezed literal), but scored by adjacency, not by
#: mere co-occurrence in the sentence.
KIND_DX = "dx"
KINDS: frozenset[str] = frozenset({KIND_DISEASE, KIND_CLAIM, KIND_REGEX, KIND_DX})

#: dx copulas that only assert a diagnosis at a clause end. The bare assertive
#: copulas だ/である/です double as continuation forms (だから, である可能性,
#: ですが), so gluing one to a condition name is not by itself a diagnosis;
#: restricting them to a clause-final position is what lets a topic sentence like
#: 「うつ病だと思っていました」 pass while 「これはうつ病だ」 still blocks. The
#: content-bearing dx phrases (に違いない/と思われ/を意味し …) are deliberately
#: absent — they assert a diagnosis wherever they attach. This set lists the
#: bare-copula dx *values* from `samd_forbidden.csv`; a new bare copula added
#: there also belongs here.
_CLAUSE_FINAL_DX: frozenset[str] = frozenset({"だ", "である", "です"})

#: Characters that end a clause for the bare-copula check. Sentence-final
#: punctuation (。!?) has already been consumed as a boundary by `_SENTENCE_SPLIT`
#: and `squeeze` has removed whitespace, so within one squeezed sentence a clause
#: end is either the string end, a comma that survived, or a sentence-final
#: particle (ね/よ/わ). Continuation particles (だから の か, だけ の け, だと の
#: と, だが の が …) are deliberately not here — that absence is what lets the
#: topic sentences pass.
_CLAUSE_FINAL_FOLLOWERS: frozenset[str] = frozenset("、，,ねよわ")

#: Bare copulas the content-bearing dx phrases may reach across. 「うつ病だと思わ
#: れます」 is a 推量-form diagnosis: と思われ attaches after the bridging だ, not
#: directly to the condition name, so the diagnosis anchor set is extended by one
#: copula. Longest first, so である is tried before だ.
_COPULA_BRIDGES: tuple[str, ...] = ("である", "です", "だ")


def _is_clause_final(sentence: str, end: int) -> bool:
    """True when a bare copula ending at ``end`` sits at a clause boundary."""
    if end >= len(sentence):
        return True
    return sentence[end] in _CLAUSE_FINAL_FOLLOWERS


@dataclass(frozen=True)
class Term:
    """One row of the forbidden-expression table, compiled."""

    id: str
    kind: str
    value: str
    severity: str
    note: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Dictionary:
    """The compiled contents of `samd_forbidden.csv`."""

    diseases: tuple[Term, ...]
    claims: tuple[Term, ...]
    regexes: tuple[Term, ...]
    dx: tuple[Term, ...] = ()
    source: str = ""

    def validate(self) -> None:
        """Refuse a dictionary that cannot block anything (R3-G4).

        All four groups must be populated. A dictionary with condition names
        but no likelihood words blocks nothing, and would still report success
        on every check — the exact failure mode this project treats as worse
        than having no gate.
        """
        require_non_empty(f"{GATE_NAME}:disease", self.diseases)
        require_non_empty(f"{GATE_NAME}:claim", self.claims)
        require_non_empty(f"{GATE_NAME}:regex", self.regexes)
        require_non_empty(f"{GATE_NAME}:dx", self.dx)


def _compile(kind: str, value: str, term_id: str, path: Path) -> re.Pattern[str]:
    if kind in (KIND_DISEASE, KIND_CLAIM, KIND_DX):
        # Disease and claim terms are matched against the squeezed judgment copy,
        # so the pattern is squeezed too — a rule and the text it checks are
        # normalised the same way. Regex terms keep their raw form (they carry
        # their own `\s*` and character classes) and match the squeezed text
        # directly.
        return re.compile(re.escape(squeeze(value)))
    try:
        return re.compile(value)
    except re.error as exc:
        raise DataFileError(f"{path.name}: term {term_id} has an invalid regex: {exc}") from exc


def load_dictionary(csv_path: Path | str | None = None) -> Dictionary:
    """Load and compile the forbidden-expression dictionary."""
    path = Path(csv_path) if csv_path is not None else data_path(FORBIDDEN_FILE)
    if not path.is_file():
        raise DataFileError(f"missing forbidden-expression file: {path}")

    groups: dict[str, list[Term]] = {
        KIND_DISEASE: [],
        KIND_CLAIM: [],
        KIND_REGEX: [],
        KIND_DX: [],
    }
    seen: set[str] = set()

    for row in read_rows(path):
        term_id = row.get("id", "").strip()
        kind = row.get("kind", "").strip()
        value = row.get("value", "").strip()
        if not term_id and not value:
            continue
        if not term_id:
            raise DataFileError(f"{path.name}: a term row has no id")
        if term_id in seen:
            raise DataFileError(f"{path.name}: duplicate term id {term_id!r}")
        seen.add(term_id)
        if kind not in KINDS:
            raise DataFileError(
                f"{path.name}: term {term_id} has unknown kind {kind!r}; expected {sorted(KINDS)}"
            )
        if not value:
            raise DataFileError(f"{path.name}: term {term_id} has an empty value")

        groups[kind].append(
            Term(
                id=term_id,
                kind=kind,
                value=value,
                severity=row.get("severity", "").strip(),
                note=row.get("note", "").strip(),
                pattern=_compile(kind, value, term_id, path),
            )
        )

    dictionary = Dictionary(
        diseases=tuple(groups[KIND_DISEASE]),
        claims=tuple(groups[KIND_CLAIM]),
        regexes=tuple(groups[KIND_REGEX]),
        dx=tuple(groups[KIND_DX]),
        source=str(path),
    )
    dictionary.validate()
    return dictionary


@functools.cache
def _cached_dictionary() -> Dictionary:
    return load_dictionary()


def clear_caches() -> None:
    """Drop the cached dictionary. Used by tests that redirect the data dir."""
    _cached_dictionary.cache_clear()


def sentences(text: str) -> tuple[str, ...]:
    """Split the folded text into sentences for the pair rule.

    The split is on the folded copy (NFKC + zero-width stripped + case folded)
    so a full-width 「！」 divides a sentence the same as an ASCII `!`, but
    whitespace — newlines included — is kept, because a newline is one of the
    boundaries. Each returned sentence is `squeeze`d by the caller before it is
    searched, which is what removes injected spaces *within* a sentence without
    dissolving the boundaries *between* them.
    """
    return tuple(part for part in _SENTENCE_SPLIT.split(fold(text)) if part.strip())


def lint(text: str, dictionary: Dictionary | None = None) -> GateResult:
    """Check one piece of outbound text.

    Args:
        text: anything about to be shown to a person — a generated draft, a
            canned fallback, or a reviewer's own wording. Reviewer text is
            linted too: the rule is about what the product displays, not about
            who typed it.

    Returns:
        `GateResult(ok=True, ...)` when nothing fired. Otherwise `ok=False` with
        `reasons` naming the rules: `"F201"` for a regex rule, `"F001+F102"` for
        a disease/claim pair found in one sentence.
    """
    table = dictionary if dictionary is not None else _cached_dictionary()
    if not text:
        return GateResult(ok=True, gate=GATE_NAME, reasons=())

    reasons: list[str] = []

    # Regex rules run over the whole squeezed copy: NFKC-folded, zero-width
    # stripped, whitespace removed. Their own `\s*` still matches (against
    # nothing), and space injection can no longer hold a percentage apart from
    # the word it qualifies.
    squeezed = squeeze(text)
    for term in table.regexes:
        if term.pattern.search(squeezed):
            reasons.append(term.id)

    # The pair rule is scoped to a sentence. Boundaries are decided on the folded
    # text (so a newline still splits), then each sentence is squeezed so a
    # forbidden pair cannot hide behind injected spaces.
    for sentence in sentences(text):
        squeezed_sentence = "".join(ch for ch in sentence if not ch.isspace())
        # Every place a condition name ends, keyed by end offset. Needed both to
        # decide the sentence has a disease at all and to score `dx` adjacency.
        disease_ends: dict[int, str] = {}
        for term in table.diseases:
            for match in term.pattern.finditer(squeezed_sentence):
                disease_ends.setdefault(match.end(), term.id)
        if not disease_ends:
            continue
        hit_diseases = sorted({term_id for term_id in disease_ends.values()})
        hit_claims = [t.id for t in table.claims if t.pattern.search(squeezed_sentence)]
        reasons.extend(f"{d}+{c}" for d in hit_diseases for c in hit_claims)

        # An assertive copula counts only when it is glued to a condition name:
        # its match must begin exactly where a disease match ends. 「これはうつ病
        # です」 fires; 「うつ病は治療可能な病気です」 (です on 病気, not on the
        # condition) does not. A *bare* copula (だ/である/です) additionally has to
        # sit at a clause end — when a continuation particle follows (だから/だけ/
        # だと/だが/である可能性) the condition is the topic, not the predicate, so
        # the sentence is not a diagnosis. The content-bearing dx phrases
        # (に違いない/と思われ/を意味し) are exempt from the clause-final check and
        # may also reach across one bridging copula, so 「うつ病だと思われます」
        # (推量-form diagnosis) still fires while 「うつ病だと思っていました」
        # (思って is not a dx term, だ is a continuation) passes.
        bridged_ends = dict(disease_ends)
        for end, disease_id in disease_ends.items():
            for copula in _COPULA_BRIDGES:
                if squeezed_sentence.startswith(copula, end):
                    bridged_ends.setdefault(end + len(copula), disease_id)
                    break
        for term in table.dx:
            bare = term.value in _CLAUSE_FINAL_DX
            anchors = disease_ends if bare else bridged_ends
            for match in term.pattern.finditer(squeezed_sentence):
                disease_id = anchors.get(match.start())
                if disease_id is None:
                    continue
                if bare and not _is_clause_final(squeezed_sentence, match.end()):
                    continue
                reasons.append(f"{disease_id}+{term.id}")

    unique = tuple(sorted(set(reasons)))
    return GateResult(ok=not unique, gate=GATE_NAME, reasons=unique)


class SamdLintGate(Gate):
    """Gate form of the lint, for `GateChain`."""

    name = GATE_NAME

    def __init__(self, dictionary: Dictionary | None = None, text_key: str = "text") -> None:
        self._dictionary = dictionary
        self._text_key = text_key

    @property
    def dictionary(self) -> Dictionary:
        if self._dictionary is None:
            self._dictionary = _cached_dictionary()
        return self._dictionary

    def validate_config(self) -> None:
        if not self._text_key:
            raise GateSetupError("SamdLintGate requires a non-empty text_key")
        self.dictionary.validate()

    def check(self, payload: dict[str, object]) -> GateResult:
        text = payload.get(self._text_key) or ""
        return lint(str(text), self.dictionary)
