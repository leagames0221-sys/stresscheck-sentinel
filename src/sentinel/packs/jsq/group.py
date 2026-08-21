"""仕事のストレス判定図 — group-level analysis.

Two things this module does, and one it deliberately refuses to do.

**Does: refuse small groups.** Below ten respondents a group result stops being
a group result and starts being a way to read one person's answers. The refusal
is an exception, not a warning, because a warning is something a caller can
ignore.

**Does: compute the four scale means.** The twelve items and their direction are
published, and the direction is the opposite of the individual scoring — here a
*high* score on "control" means *more* control. That inversion is the classic
implementation bug in this questionnaire, so it is spelled out below rather than
folded into a shared helper.

**Refuses: invent the health-risk figures.** The diagonal contour lines on the
published diagram encode a regression whose coefficients are not in any primary
source we hold. `data/sjd_coefficients.csv` is therefore empty and marked
unverified, and `risk_a` / `risk_b` / `total_risk` come back as `None`. See that
file for what is missing and how to complete it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.core.datafiles import data_path, read_header_comments, read_rows
from sentinel.core.errors import DataFileError, GroupSizeError

#: Minimum group size. Source: ストレスチェック制度実施マニュアル — groups are to
#: be at least 10 people, and the 2027-04 amendment tightens the surrounding
#: rules on identifiability further.
MIN_GROUP_SIZE = 10

#: The twelve items of the diagram, by 57-item numbering.
#: 仕事の量的負担 = A1-3, コントロール = A8-10,
#: 上司の支援 = C1/C4/C7 (47, 50, 53), 同僚の支援 = C2/C5/C8 (48, 51, 54).
SCALE_ITEMS: dict[str, tuple[int, ...]] = {
    "quantitative_load": (1, 2, 3),
    "control": (8, 9, 10),
    "supervisor_support": (47, 50, 53),
    "coworker_support": (48, 51, 54),
}

SCALE_LABELS_JA: dict[str, str] = {
    "quantitative_load": "仕事の量的負担",
    "control": "仕事のコントロール",
    "supervisor_support": "上司の支援",
    "coworker_support": "同僚の支援",
}

DIAGRAM_ITEMS: tuple[int, ...] = tuple(sorted(n for items in SCALE_ITEMS.values() for n in items))

VALID_SEXES = frozenset({"m", "f", "u"})


@dataclass(frozen=True)
class Coefficients:
    """Regression coefficients for the health-risk contours, if we have them."""

    verified: bool
    terms: Mapping[tuple[str, str, str], float] = field(default_factory=dict)
    source: str = ""

    def get(self, chart: str, sex: str, term: str) -> float | None:
        return self.terms.get((chart, sex, term))


@dataclass(frozen=True)
class GroupResult:
    """Group-level result.

    `risk_a`, `risk_b` and `total_risk` are `None` whenever
    `coefficients_verified` is False. A caller that renders them must render the
    absence, not a zero and not a dash that looks like a value.
    """

    n: int
    n_by_sex: dict[str, int]
    n_excluded_incomplete: int
    chart_sex: str
    scales: dict[str, float]
    risk_a: float | None
    risk_b: float | None
    total_risk: int | None
    coefficients_verified: bool
    notes: tuple[str, ...]


def load_coefficients(csv_path: Path | None = None) -> Coefficients:
    """Load health-risk coefficients.

    A file with no data rows means "we do not have these numbers", which is a
    legitimate and expected state — not an error. The loader reports it as
    `verified=False` and lets the caller decide.
    """
    path = Path(csv_path) if csv_path is not None else data_path("sjd_coefficients.csv")
    if not path.is_file():
        raise DataFileError(f"missing coefficient file: {path}")

    terms: dict[tuple[str, str, str], float] = {}
    for row in read_rows(path):
        chart = row.get("chart", "").strip()
        sex = row.get("sex", "").strip()
        term = row.get("term", "").strip()
        raw = row.get("coefficient", "").strip()
        if not (chart and sex and term and raw):
            continue
        try:
            terms[(chart, sex, term)] = float(raw)
        except ValueError as exc:
            raise DataFileError(f"{path.name}: coefficient is not a number: {raw!r}") from exc

    declared_unverified = any(
        c.replace(" ", "").lower().startswith("verified:no") for c in read_header_comments(path)
    )
    verified = bool(terms) and not declared_unverified
    return Coefficients(verified=verified, terms=terms, source=str(path))


def _diagram_score(answers: Mapping[int, int], items: tuple[int, ...]) -> int:
    """Score one scale for the diagram.

    The published instruction is そうだ=4 / まあそうだ=3 / ややちがう=2 / ちがう=1
    (and 非常に=4 ... 全くない=1), i.e. the reverse of the raw 1..4 answer code,
    for **all twelve items** — including the control and support items, which are
    *not* reversed when scoring an individual for high stress. Hence `5 - value`
    unconditionally here, and no reuse of `scoring.apply_reverse`.
    """
    return sum(5 - answers[n] for n in items)


def group_analysis(
    rows: Sequence[Mapping[int, int]],
    sexes: Sequence[str],
    coefficients: Coefficients | None = None,
) -> GroupResult:
    """Analyse a group.

    Args:
        rows: one mapping per respondent, `{item_no: 1..4}` in 57-item
            numbering. Rows missing any of the twelve diagram items are excluded
            from the means and counted in `n_excluded_incomplete`.
        sexes: `"m"`, `"f"` or `"u"` per row, same length as `rows`. A group that
            is not entirely one sex, or that contains any `"u"`, is charted
            against the male diagram — the published fallback when sex is not
            collected. That fallback is documented to over-state the
            load/control risk for women, and it is recorded in `notes` rather
            than left for the reader to know.

    Raises:
        GroupSizeError: fewer than `MIN_GROUP_SIZE` complete rows.
        ValueError: `sexes` length mismatch, unknown sex code, or an answer
            outside 1..4.
    """
    if len(rows) != len(sexes):
        raise ValueError(f"rows and sexes differ in length: {len(rows)} vs {len(sexes)}")

    normalised = [s.strip().lower() for s in sexes]
    bad = sorted({s for s in normalised if s not in VALID_SEXES})
    if bad:
        raise ValueError(f"unknown sex codes {bad}; expected {sorted(VALID_SEXES)}")

    complete: list[Mapping[int, int]] = []
    complete_sexes: list[str] = []
    excluded = 0
    for answers, sex in zip(rows, normalised, strict=True):
        for item_no in DIAGRAM_ITEMS:
            value = answers.get(item_no)
            if value is not None and not 1 <= value <= 4:
                raise ValueError(f"item {item_no}: answer must be 1..4, got {value}")
        if all(answers.get(n) is not None for n in DIAGRAM_ITEMS):
            complete.append(answers)
            complete_sexes.append(sex)
        else:
            excluded += 1

    n = len(complete)
    if n < MIN_GROUP_SIZE:
        raise GroupSizeError(
            f"group analysis needs at least {MIN_GROUP_SIZE} complete responses, got {n}"
            f" ({excluded} excluded for missing answers among the 12 diagram items)."
            " Below this size a group figure can be read back to an individual,"
            " so the output is withheld rather than shown with a caveat."
        )

    n_by_sex = {code: complete_sexes.count(code) for code in sorted(VALID_SEXES)}
    notes: list[str] = []

    distinct = set(complete_sexes)
    if distinct == {"m"}:
        chart_sex = "m"
    elif distinct == {"f"}:
        chart_sex = "f"
    else:
        chart_sex = "m"
        notes.append(
            "性別が単一でない、または不明を含むため男性用判定図を使用した。"
            "女性のデータを男性の判定図にあてはめた場合、量的負担とコントロールによる"
            "健康リスク値は過大に評価される可能性がある。"
        )

    scales = {
        name: round(sum(_diagram_score(a, items) for a in complete) / n, 2)
        for name, items in SCALE_ITEMS.items()
    }

    coeffs = coefficients if coefficients is not None else load_coefficients()
    if not coeffs.verified:
        notes.append(
            "健康リスク値は算出していない。仕事のストレス判定図の回帰係数の一次資料が"
            "未取得のため (data/sjd_coefficients.csv 参照)。推定値で埋めることはしない。"
        )
        return GroupResult(
            n=n,
            n_by_sex=n_by_sex,
            n_excluded_incomplete=excluded,
            chart_sex=chart_sex,
            scales=scales,
            risk_a=None,
            risk_b=None,
            total_risk=None,
            coefficients_verified=False,
            notes=tuple(notes),
        )

    risk_a = _linear_risk(coeffs, "a", chart_sex, scales)
    risk_b = _linear_risk(coeffs, "b", chart_sex, scales)
    total_risk = round(risk_a * risk_b / 100)
    return GroupResult(
        n=n,
        n_by_sex=n_by_sex,
        n_excluded_incomplete=excluded,
        chart_sex=chart_sex,
        scales=scales,
        risk_a=round(risk_a, 1),
        risk_b=round(risk_b, 1),
        total_risk=total_risk,
        coefficients_verified=True,
        notes=tuple(notes),
    )


def _linear_risk(coeffs: Coefficients, chart: str, sex: str, scales: Mapping[str, float]) -> float:
    """Evaluate `intercept + sum(coefficient * scale_mean)` for one chart.

    Only reachable once real coefficients exist; until then `group_analysis`
    returns before getting here.
    """
    intercept = coeffs.get(chart, sex, "intercept")
    if intercept is None:
        raise DataFileError(f"coefficient file has no intercept for chart={chart} sex={sex}")
    total = intercept
    for name, mean in scales.items():
        weight = coeffs.get(chart, sex, name)
        if weight is not None:
            total += weight * mean
    return total
