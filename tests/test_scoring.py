"""Scoring tests.

Structured around the three ways this can go wrong in the field:
reversal applied to the wrong items, a cut-off that is off by one, and a missing
answer treated as an answer.
"""

from __future__ import annotations

import pytest

from sentinel.core.errors import DataFileError
from sentinel.packs.jsq import scoring
from tests.conftest import build_answers

# ---------------------------------------------------------------------------
# Golden case, transcribed from the primary source
# ---------------------------------------------------------------------------

# 厚生労働省「数値基準に基づいて『高ストレス者』を選定する方法」 p.2-3 の回答例。
# 同文書は答え合わせの数字まで載せている: 領域A=51, 領域B=92, 領域C=31、
# 基準㋐(B>=77)と基準㋑(A+C>=76 かつ B>=63)の両方を満たし高ストレス者。
# 下の生値は、同文書の「置き換え後の点数」欄から逆算した回答そのもの。
GOLDEN_ANSWERS: dict[int, int] = {
    # 領域A (items 1-17)
    1: 1,
    2: 1,
    3: 2,
    4: 3,
    5: 3,
    6: 1,
    7: 4,
    8: 4,
    9: 3,
    10: 3,
    11: 2,
    12: 3,
    13: 2,
    14: 4,
    15: 3,
    16: 3,
    17: 4,
    # 領域B (items 18-46 = B1-B29)
    18: 1,
    19: 1,
    20: 1,
    21: 2,
    22: 3,
    23: 3,
    24: 4,
    25: 4,
    26: 4,
    27: 3,
    28: 3,
    29: 4,
    30: 4,
    31: 4,
    32: 3,
    33: 3,
    34: 2,
    35: 2,
    36: 2,
    37: 2,
    38: 3,
    39: 4,
    40: 3,
    41: 4,
    42: 2,
    43: 3,
    44: 3,
    45: 3,
    46: 3,
    # 領域C (items 47-55 = C1-C9)
    47: 4,
    48: 3,
    49: 3,
    50: 4,
    51: 3,
    52: 4,
    53: 4,
    54: 3,
    55: 3,
    # 領域D (満足度) は判定に使わないので与えなくてよい
}


def test_golden_case_from_ministry_worked_example():
    result = scoring.score(GOLDEN_ANSWERS, variant="57")
    assert result.sums == {"A": 51, "B": 92, "C": 31}
    assert result.valid is True
    assert result.high_stress is True
    assert result.rule_hit == "B_only"


def test_golden_case_unaffected_by_satisfaction_items():
    """Adding the two satisfaction answers must not move any total."""
    with_d = {**GOLDEN_ANSWERS, 56: 4, 57: 4}
    assert scoring.score(with_d).sums == scoring.score(GOLDEN_ANSWERS).sums


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------


def test_reversal_recode_table():
    assert [scoring.apply_reverse(v, True) for v in (1, 2, 3, 4)] == [4, 3, 2, 1]
    assert [scoring.apply_reverse(v, False) for v in (1, 2, 3, 4)] == [1, 2, 3, 4]


def test_reversed_and_plain_items_move_in_opposite_directions():
    """Item 1 is reverse-worded; item 8 is not. Answering 1 to each must differ."""
    base = dict.fromkeys(range(1, 56), 2)

    low_on_reversed = {**base, 1: 1}
    low_on_plain = {**base, 8: 1}
    assert scoring.score(low_on_reversed).sums["A"] > scoring.score(low_on_plain).sums["A"]


def test_reverse_item_set_matches_the_source_list():
    """領域A 1-7, 11-13, 15 と領域B 1-3 -> 通し番号での集合。"""
    thresholds = scoring.load_thresholds().for_variant("57")
    assert thresholds.reverse_items == frozenset({1, 2, 3, 4, 5, 6, 7, 11, 12, 13, 15, 18, 19, 20})


# ---------------------------------------------------------------------------
# 57-item cut-offs: ㋐ B>=77 / ㋑ A+C>=76 and B>=63
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "c", "expected_high", "expected_rule"),
    [
        # ㋐ boundary, with A+C held at its minimum so only B can fire
        (17, 77, 9, True, "B_only"),
        (17, 76, 9, False, "none"),
        # ㋑ boundary on A+C, with B exactly at its companion cut-off
        (67, 63, 9, True, "AC_and_B"),  # A+C = 76
        (66, 63, 9, False, "none"),  # A+C = 75
        # ㋑ boundary on B, with A+C satisfied
        (67, 62, 9, False, "none"),
        # Both criteria met: the first criterion in the source order is reported
        (67, 77, 9, True, "B_only"),
        # Floor and ceiling
        (17, 29, 9, False, "none"),
        (68, 116, 36, True, "B_only"),
    ],
)
def test_57_item_cutoffs(a, b, c, expected_high, expected_rule):
    answers = build_answers("57", {"A": a, "B": b, "C": c})
    result = scoring.score(answers, variant="57")
    assert result.sums == {"A": a, "B": b, "C": c}
    assert result.high_stress is expected_high
    assert result.rule_hit == expected_rule
    assert result.valid is True
    assert result.missing == ()


# ---------------------------------------------------------------------------
# 23-item cut-offs: ㋐ B>=31 / ㋑ A+C>=39 and B>=23
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "c", "expected_high", "expected_rule"),
    [
        (6, 31, 6, True, "B_only"),
        (6, 30, 6, False, "none"),
        (21, 23, 18, True, "AC_and_B"),  # A+C = 39
        (20, 23, 18, False, "none"),  # A+C = 38
        (21, 22, 18, False, "none"),
        (21, 31, 18, True, "B_only"),
        (6, 11, 6, False, "none"),
        (24, 44, 24, True, "B_only"),
    ],
)
def test_23_item_cutoffs(a, b, c, expected_high, expected_rule):
    answers = build_answers("23", {"A": a, "B": b, "C": c})
    result = scoring.score(answers, variant="23")
    assert result.sums == {"A": a, "B": b, "C": c}
    assert result.high_stress is expected_high
    assert result.rule_hit == expected_rule


def test_23_item_form_has_23_items_in_three_domains():
    items = scoring.load_items("23")
    assert len(items) == 23
    counts = {d: sum(1 for i in items if i.domain == d) for d in ("A", "B", "C")}
    assert counts == {"A": 6, "B": 11, "C": 6}


# ---------------------------------------------------------------------------
# Missing answers are never imputed
# ---------------------------------------------------------------------------


def test_missing_answer_suspends_judgement():
    answers = build_answers("57", {"A": 68, "B": 116, "C": 36}, omit=[30])
    result = scoring.score(answers)
    assert result.missing == (30,)
    assert result.valid is False
    # The remaining answers are as extreme as the form allows. It still does not
    # produce a judgement.
    assert result.high_stress is False
    assert result.rule_hit == "none"


def test_partial_sums_still_reported_for_progress_display():
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18}, omit=[1, 2])
    result = scoring.score(answers)
    assert result.missing == (1, 2)
    assert result.sums["A"] > 0


def test_every_missing_item_is_listed():
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18}, omit=[5, 40, 55])
    assert scoring.score(answers).missing == (5, 40, 55)


def test_empty_answers_are_missing_not_zero():
    result = scoring.score({}, variant="57")
    assert result.valid is False
    assert len(result.missing) == 55
    assert result.sums == {"A": 0, "B": 0, "C": 0}


def test_satisfaction_items_are_not_counted_as_missing():
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18})
    assert 56 not in answers
    assert scoring.score(answers).missing == ()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 5, -1, 100])
def test_out_of_range_answer_raises(bad):
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18})
    answers[3] = bad
    with pytest.raises(ValueError, match=r"must be 1\.\.4"):
        scoring.score(answers)


def test_non_integer_answer_raises():
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18})
    answers[3] = "3"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="must be an int"):
        scoring.score(answers)


def test_bool_is_not_accepted_as_an_answer():
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18})
    answers[3] = True  # type: ignore[assignment]
    with pytest.raises(ValueError, match="must be an int"):
        scoring.score(answers)


def test_unknown_item_number_raises():
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18})
    answers[99] = 2
    with pytest.raises(ValueError, match="not in the 57-item form"):
        scoring.score(answers)


def test_57_item_answers_rejected_by_23_item_variant():
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18})
    with pytest.raises(ValueError, match="not in the 23-item form"):
        scoring.score(answers, variant="23")


def test_unknown_variant_raises():
    with pytest.raises(ValueError, match="unknown variant"):
        scoring.score({}, variant="80")


# ---------------------------------------------------------------------------
# Thresholds are data, not code
# ---------------------------------------------------------------------------


def test_custom_threshold_file_changes_the_verdict(tmp_path):
    """An employer may set its own criteria; that must need no code change."""
    custom = tmp_path / "custom.csv"
    custom.write_text(
        "# source: test fixture / license: n/a\n"
        "variant,kind,rule,expr,op,value,items\n"
        "57,rule,B_only,B,>=,40,\n"
        "57,reverse_items,,,,,1 2 3 4 5 6 7 11 12 13 15 18 19 20\n",
        encoding="utf-8",
    )
    answers = build_answers("57", {"A": 17, "B": 45, "C": 9})

    assert scoring.score(answers).high_stress is False
    lenient = scoring.load_thresholds(custom)
    assert scoring.score(answers, thresholds=lenient).high_stress is True


def test_threshold_file_without_rules_is_rejected(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text(
        "# source: test fixture\nvariant,kind,rule,expr,op,value,items\n", encoding="utf-8"
    )
    with pytest.raises(DataFileError, match="no rule rows"):
        scoring.load_thresholds(empty)


def test_threshold_file_with_unknown_kind_is_rejected(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "# source: test fixture\nvariant,kind,rule,expr,op,value,items\n57,vibes,B_only,B,>=,77,\n",
        encoding="utf-8",
    )
    with pytest.raises(DataFileError, match="unknown kind"):
        scoring.load_thresholds(bad)


def test_threshold_expression_naming_an_unknown_domain_is_rejected(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "# source: test fixture\n"
        "variant,kind,rule,expr,op,value,items\n"
        "57,rule,Z_only,Z,>=,1,\n"
        "57,reverse_items,,,,,1\n",
        encoding="utf-8",
    )
    thresholds = scoring.load_thresholds(bad)
    with pytest.raises(DataFileError, match="unknown domain"):
        scoring.score(build_answers("57", {"A": 34, "B": 58, "C": 18}), thresholds=thresholds)


def test_missing_variant_in_threshold_file_is_reported(tmp_path):
    partial = tmp_path / "partial.csv"
    partial.write_text(
        "# source: test fixture\nvariant,kind,rule,expr,op,value,items\n57,rule,B_only,B,>=,77,\n",
        encoding="utf-8",
    )
    thresholds = scoring.load_thresholds(partial)
    with pytest.raises(DataFileError, match="no rules for variant '23'"):
        thresholds.for_variant("23")


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


def test_scoring_does_not_mutate_its_input():
    answers = build_answers("57", {"A": 34, "B": 58, "C": 18})
    snapshot = dict(answers)
    scoring.score(answers)
    assert answers == snapshot


def test_repeated_scoring_is_identical():
    answers = build_answers("57", {"A": 40, "B": 70, "C": 20})
    assert scoring.score(answers) == scoring.score(answers)
