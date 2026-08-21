"""Simple-sum scoring for the 職業性ストレス簡易調査票 (57-item and 23-item forms).

Pure functions. No LLM, no network, no randomness, no clock. Given the same
answers and the same threshold file, this returns the same result forever, which
is the only reason the rest of the system is allowed to trust it.

Two rules are worth stating out loud because they are where naive
implementations go wrong:

**Reversal.** Some items are worded so that a *low* answer means *high* stress
("I have to do an extremely large amount of work" — answering 1, "yes", is the
stressed answer). Those items are recoded 1<->4, 2<->3 before summing. The list
of which items is in `data/jsq_thresholds.csv`, not here.

**Missing answers are not filled in.** If any scored item is unanswered, the
result is returned with `valid=False` and `high_stress=False`. It is not a low
score and it is not a high score; it is not a score. Imputing a value would mean
inventing an answer on a respondent's behalf and then routing them — or not
routing them — to a physician on the strength of it.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sentinel.core.datafiles import data_path, read_rows, require_int
from sentinel.core.errors import DataFileError

VARIANTS = ("57", "23")
MIN_ANSWER = 1
MAX_ANSWER = 4

# The reversal recode. Written as a table rather than `5 - value` so that the
# transformation the source document describes ("1=>4, 2=>3, 3=>2, 4=>1") is
# visible verbatim in the code.
_REVERSE_MAP = {1: 4, 2: 3, 3: 2, 4: 1}


@dataclass(frozen=True)
class Item:
    """One questionnaire item as loaded from CSV."""

    item_no: int
    domain: str
    domain_item_no: int
    scale: str
    context: str
    text: str
    choices: tuple[str, ...]
    reverse: bool
    scored: bool


@dataclass(frozen=True)
class Condition:
    """One comparison inside a rule, e.g. `B >= 77`."""

    expr: str
    op: str
    value: int

    def evaluate(self, sums: Mapping[str, int]) -> bool:
        left = _evaluate_expr(self.expr, sums)
        if self.op == ">=":
            return left >= self.value
        if self.op == ">":
            return left > self.value
        if self.op == "<=":
            return left <= self.value
        if self.op == "<":
            return left < self.value
        raise DataFileError(f"unsupported operator in threshold file: {self.op!r}")


@dataclass(frozen=True)
class Rule:
    """A named set of conditions, all of which must hold (AND)."""

    name: str
    conditions: tuple[Condition, ...]

    def evaluate(self, sums: Mapping[str, int]) -> bool:
        return all(c.evaluate(sums) for c in self.conditions)


@dataclass(frozen=True)
class VariantThresholds:
    """The criteria for one questionnaire variant."""

    variant: str
    rules: tuple[Rule, ...]
    reverse_items: frozenset[int]


@dataclass(frozen=True)
class Thresholds:
    """Criteria for every variant found in a threshold file."""

    variants: Mapping[str, VariantThresholds]
    source: str = ""

    def for_variant(self, variant: str) -> VariantThresholds:
        try:
            return self.variants[variant]
        except KeyError as exc:
            raise DataFileError(
                f"threshold file has no rules for variant {variant!r} "
                f"(has: {sorted(self.variants)})"
            ) from exc


@dataclass(frozen=True)
class ScoreResult:
    """The outcome of scoring one questionnaire."""

    variant: str
    sums: dict[str, int]
    high_stress: bool
    rule_hit: str
    missing: tuple[int, ...]
    valid: bool


def _evaluate_expr(expr: str, sums: Mapping[str, int]) -> int:
    """Evaluate a domain expression such as `B` or `A+C`.

    Deliberately not `eval`. The grammar is "domain letters joined by +", which
    is all the source document ever uses, and anything else is a data error
    rather than something to be clever about.
    """
    total = 0
    for part in expr.split("+"):
        key = part.strip()
        if key not in sums:
            raise DataFileError(f"threshold expression refers to unknown domain {key!r}")
        total += sums[key]
    return total


def load_items(variant: str = "57") -> tuple[Item, ...]:
    """Load the item table for a variant from `data/jsq_items_<variant>.csv`."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}")
    path = data_path(f"jsq_items_{variant}.csv")
    items: list[Item] = []
    for row in read_rows(path):
        items.append(
            Item(
                item_no=require_int(row, "item_no", path),
                domain=row["domain"].strip(),
                domain_item_no=require_int(row, "domain_item_no", path),
                scale=row["scale"].strip(),
                context=row["context"].strip(),
                text=row["text"].strip(),
                choices=tuple(c for c in row["choices"].split("|") if c),
                reverse=require_int(row, "reverse", path) == 1,
                scored=require_int(row, "scored", path) == 1,
            )
        )
    if not items:
        raise DataFileError(f"{path.name} has no item rows")

    numbers = [i.item_no for i in items]
    if numbers != list(range(1, len(items) + 1)):
        raise DataFileError(f"{path.name}: item_no must be 1..n with no gaps, got {numbers}")
    expected = int(variant)
    if len(items) != expected:
        raise DataFileError(f"{path.name}: expected {expected} items, found {len(items)}")
    return tuple(items)


def load_thresholds(csv_path: Path | None = None) -> Thresholds:
    """Load high-stress criteria from a CSV.

    The published cut-offs are an *example* that each workplace may replace on
    the advice of its implementer, so swapping this file is the supported way to
    customise the判定 — no code change, no redeploy of logic.
    """
    path = Path(csv_path) if csv_path is not None else data_path("jsq_thresholds.csv")
    if not path.is_file():
        raise DataFileError(f"missing threshold file: {path}")

    rules: dict[str, dict[str, list[Condition]]] = {}
    reverse: dict[str, set[int]] = {}

    for row in read_rows(path):
        variant = row["variant"].strip()
        kind = row["kind"].strip()
        if not variant:
            continue
        if kind == "rule":
            name = row["rule"].strip()
            if not name:
                raise DataFileError(f"{path.name}: rule row without a rule name")
            condition = Condition(
                expr=row["expr"].strip(),
                op=row["op"].strip(),
                value=require_int(row, "value", path),
            )
            rules.setdefault(variant, {}).setdefault(name, []).append(condition)
        elif kind == "reverse_items":
            entries = row["items"].split()
            try:
                numbers = {int(e) for e in entries}
            except ValueError as exc:
                raise DataFileError(
                    f"{path.name}: reverse_items must be space-separated integers, got {entries!r}"
                ) from exc
            reverse.setdefault(variant, set()).update(numbers)
        else:
            raise DataFileError(f"{path.name}: unknown kind {kind!r}")

    if not rules:
        raise DataFileError(f"{path.name}: no rule rows found")

    variants = {
        variant: VariantThresholds(
            variant=variant,
            # Insertion order, not sorted: the threshold file lists the criteria
            # in the order the source document does (㋐ then ㋑), and when a
            # respondent meets both, `rule_hit` should name the first one the
            # document names.
            rules=tuple(Rule(name=name, conditions=tuple(conds)) for name, conds in named.items()),
            reverse_items=frozenset(reverse.get(variant, set())),
        )
        for variant, named in rules.items()
    }
    return Thresholds(variants=variants, source=str(path))


@functools.cache
def _cached_items(variant: str) -> tuple[Item, ...]:
    return load_items(variant)


@functools.cache
def _cached_thresholds() -> Thresholds:
    return load_thresholds()


def clear_caches() -> None:
    """Drop cached data files. Used by tests that point at a different data dir."""
    _cached_items.cache_clear()
    _cached_thresholds.cache_clear()


def apply_reverse(value: int, reverse: bool) -> int:
    """Recode one answer, 1<->4 and 2<->3, when the item is reverse-worded."""
    return _REVERSE_MAP[value] if reverse else value


def score(
    answers: Mapping[int, int],
    variant: str = "57",
    thresholds: Thresholds | None = None,
) -> ScoreResult:
    """Score one questionnaire.

    Args:
        answers: `{item_no: 1..4}`, 1-origin, using the numbering of `variant`.
            Absent keys are missing answers. Extra or out-of-range keys, and
            values outside 1..4, are a caller bug and raise `ValueError`.
        variant: `"57"` or `"23"`.
        thresholds: criteria to apply. Defaults to the bundled file.

    Returns:
        A `ScoreResult`. When any scored item is missing, `valid` is False,
        `high_stress` is False and `rule_hit` is `"none"`; `sums` still reports
        the partial totals so a UI can show progress, but they are not a
        judgement and must not be presented as one.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}")

    items = _cached_items(variant)
    by_no = {item.item_no: item for item in items}
    criteria = (thresholds or _cached_thresholds()).for_variant(variant)

    unknown = sorted(set(answers) - set(by_no))
    if unknown:
        raise ValueError(f"answers contain item numbers not in the {variant}-item form: {unknown}")

    for item_no, value in answers.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"item {item_no}: answer must be an int, got {value!r}")
        if not MIN_ANSWER <= value <= MAX_ANSWER:
            raise ValueError(
                f"item {item_no}: answer must be {MIN_ANSWER}..{MAX_ANSWER}, got {value}"
            )

    scored_items = [item for item in items if item.scored]
    missing = tuple(sorted(item.item_no for item in scored_items if item.item_no not in answers))

    sums: dict[str, int] = {}
    for item in scored_items:
        sums.setdefault(item.domain, 0)
        raw = answers.get(item.item_no)
        if raw is None:
            continue
        sums[item.domain] += apply_reverse(raw, item.item_no in criteria.reverse_items)

    if missing:
        return ScoreResult(
            variant=variant,
            sums=sums,
            high_stress=False,
            rule_hit="none",
            missing=missing,
            valid=False,
        )

    rule_hit = "none"
    for rule in criteria.rules:
        if rule.evaluate(sums):
            rule_hit = rule.name
            break

    return ScoreResult(
        variant=variant,
        sums=sums,
        high_stress=rule_hit != "none",
        rule_hit=rule_hit,
        missing=(),
        valid=True,
    )
