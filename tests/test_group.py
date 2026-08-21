"""Group analysis tests."""

from __future__ import annotations

import pytest

from sentinel.core.errors import DataFileError, GroupSizeError
from sentinel.packs.jsq import group


def respondent(value: int = 2) -> dict[int, int]:
    """A complete set of the twelve diagram items, all answered `value`."""
    return dict.fromkeys(group.DIAGRAM_ITEMS, value)


def cohort(n: int, value: int = 2) -> tuple[list[dict[int, int]], list[str]]:
    return [respondent(value) for _ in range(n)], ["m"] * n


# ---------------------------------------------------------------------------
# The small-group refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 5, 9])
def test_group_below_ten_is_refused(n):
    rows, sexes = cohort(n)
    with pytest.raises(GroupSizeError, match="at least 10"):
        group.group_analysis(rows, sexes)


def test_group_of_exactly_ten_is_allowed():
    rows, sexes = cohort(10)
    assert group.group_analysis(rows, sexes).n == 10


def test_refusal_counts_complete_rows_not_submitted_rows():
    """Twelve submissions, three of them incomplete, is a group of nine."""
    rows, sexes = cohort(12)
    for row in rows[:3]:
        del row[group.DIAGRAM_ITEMS[0]]
    with pytest.raises(GroupSizeError, match="3 excluded"):
        group.group_analysis(rows, sexes)


def test_incomplete_rows_are_excluded_and_counted():
    rows, sexes = cohort(13)
    del rows[0][47]
    result = group.group_analysis(rows, sexes)
    assert result.n == 12
    assert result.n_excluded_incomplete == 1


# ---------------------------------------------------------------------------
# Scale scoring, which runs in the opposite direction to individual scoring
# ---------------------------------------------------------------------------


def test_scale_means_use_the_diagram_direction():
    """Answering 1 to every item gives 3 x (5-1) = 12 on every scale."""
    rows, sexes = cohort(10, value=1)
    result = group.group_analysis(rows, sexes)
    assert result.scales == {
        "quantitative_load": 12.0,
        "control": 12.0,
        "supervisor_support": 12.0,
        "coworker_support": 12.0,
    }


def test_answering_four_gives_the_floor():
    rows, sexes = cohort(10, value=4)
    assert set(group.group_analysis(rows, sexes).scales.values()) == {3.0}


def test_control_is_not_reversed_the_way_individual_scoring_reverses_it():
    """High `control` here means more control, the opposite of the A-domain sum.

    Item 8 is "I can work at my own pace". Answering 1 (yes) is *good* control,
    so it must produce the *higher* diagram score — whereas in individual
    scoring item 8 is not reversed and answering 1 lowers the stress total.
    """
    good, sexes = cohort(10, value=1)
    poor, _ = cohort(10, value=4)
    assert (
        group.group_analysis(good, sexes).scales["control"]
        > group.group_analysis(poor, sexes).scales["control"]
    )


def test_scale_mean_is_an_average_over_people():
    rows = [respondent(1) for _ in range(5)] + [respondent(3) for _ in range(5)]
    result = group.group_analysis(rows, ["m"] * 10)
    # (3*(5-1) + 3*(5-3)) / 2 = (12 + 6) / 2 = 9.0
    assert result.scales["quantitative_load"] == 9.0


def test_only_the_twelve_diagram_items_matter():
    rows, sexes = cohort(10)
    for row in rows:
        row[30] = 4  # a B-domain item, not part of the diagram
    assert group.group_analysis(rows, sexes).scales["quantitative_load"] == 9.0


# ---------------------------------------------------------------------------
# Chart selection by sex
# ---------------------------------------------------------------------------


def test_single_sex_group_uses_its_own_chart():
    rows, _ = cohort(10)
    assert group.group_analysis(rows, ["f"] * 10).chart_sex == "f"
    assert group.group_analysis(rows, ["m"] * 10).chart_sex == "m"
    # No chart-substitution note when the group is unambiguously one sex.
    assert not any("男性用判定図" in note for note in group.group_analysis(rows, ["f"] * 10).notes)


def test_mixed_or_unknown_sex_falls_back_to_the_male_chart_with_a_note():
    rows, _ = cohort(10)
    mixed = group.group_analysis(rows, ["m"] * 5 + ["f"] * 5)
    assert mixed.chart_sex == "m"
    assert any("男性用判定図" in note for note in mixed.notes)

    unknown = group.group_analysis(rows, ["u"] * 10)
    assert unknown.chart_sex == "m"
    assert any("男性用判定図" in note for note in unknown.notes)


def test_sex_counts_are_reported():
    rows, _ = cohort(10)
    result = group.group_analysis(rows, ["m"] * 6 + ["f"] * 3 + ["u"])
    assert result.n_by_sex == {"m": 6, "f": 3, "u": 1}


def test_unknown_sex_code_is_rejected():
    rows, _ = cohort(10)
    with pytest.raises(ValueError, match="unknown sex codes"):
        group.group_analysis(rows, ["x"] * 10)


def test_length_mismatch_is_rejected():
    rows, _ = cohort(10)
    with pytest.raises(ValueError, match="differ in length"):
        group.group_analysis(rows, ["m"] * 9)


def test_out_of_range_answer_is_rejected():
    rows, sexes = cohort(10)
    rows[0][1] = 7
    with pytest.raises(ValueError, match=r"must be 1\.\.4"):
        group.group_analysis(rows, sexes)


# ---------------------------------------------------------------------------
# Health risk: withheld, not guessed
# ---------------------------------------------------------------------------


def test_shipped_coefficient_file_is_marked_unverified():
    coefficients = group.load_coefficients()
    assert coefficients.verified is False
    assert coefficients.terms == {}


def test_risk_values_are_none_while_coefficients_are_unverified():
    rows, sexes = cohort(10)
    result = group.group_analysis(rows, sexes)
    assert result.coefficients_verified is False
    assert result.risk_a is None
    assert result.risk_b is None
    assert result.total_risk is None
    assert any("未取得" in note for note in result.notes)


def test_scale_means_are_still_produced_without_coefficients():
    """Losing the risk figures must not cost the part we can substantiate."""
    rows, sexes = cohort(10)
    assert len(group.group_analysis(rows, sexes).scales) == 4


def test_coefficient_file_with_bad_number_is_rejected(tmp_path):
    bad = tmp_path / "coeff.csv"
    bad.write_text(
        "# source: test fixture\nchart,sex,term,coefficient\na,m,intercept,not-a-number\n",
        encoding="utf-8",
    )
    with pytest.raises(DataFileError, match="not a number"):
        group.load_coefficients(bad)


@pytest.mark.skipif(
    not group.load_coefficients().verified,
    reason="係数一次資料未取得: 仕事のストレス判定図の健康リスク回帰係数が未取得のため "
    "(data/sjd_coefficients.csv 参照)。係数を入手して同ファイルを埋めれば自動的に走る。",
)
def test_total_risk_is_the_product_of_the_two_charts_over_100():
    rows, sexes = cohort(10)
    result = group.group_analysis(rows, sexes)
    assert result.risk_a is not None
    assert result.risk_b is not None
    assert result.total_risk == round(result.risk_a * result.risk_b / 100)


# ---------------------------------------------------------------------------
# The coefficient path itself, exercised with a synthetic table
# ---------------------------------------------------------------------------


def test_risk_is_computed_when_coefficients_are_supplied(tmp_path):
    """The arithmetic is testable even though the real coefficients are not ours.

    These numbers are invented for this test and are marked as such; they exist
    to prove `(A) x (B) / 100` is wired up, not to stand in for the real table.
    """
    synthetic = tmp_path / "synthetic.csv"
    synthetic.write_text(
        "# source: SYNTHETIC TEST FIXTURE - not the published coefficients\n"
        "chart,sex,term,coefficient\n"
        "a,m,intercept,100\n"
        "a,m,quantitative_load,1\n"
        "a,m,control,-1\n"
        "b,m,intercept,100\n"
        "b,m,supervisor_support,-1\n"
        "b,m,coworker_support,-1\n",
        encoding="utf-8",
    )
    coefficients = group.load_coefficients(synthetic)
    assert coefficients.verified is True

    rows, sexes = cohort(10, value=2)  # every scale mean is 9.0
    result = group.group_analysis(rows, sexes, coefficients=coefficients)
    assert result.coefficients_verified is True
    assert result.risk_a == 100.0  # 100 + 9 - 9
    assert result.risk_b == 82.0  # 100 - 9 - 9
    assert result.total_risk == 82


def test_missing_intercept_is_reported(tmp_path):
    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_text(
        "# source: SYNTHETIC TEST FIXTURE\nchart,sex,term,coefficient\na,m,quantitative_load,1\n",
        encoding="utf-8",
    )
    rows, sexes = cohort(10)
    with pytest.raises(DataFileError, match="no intercept"):
        group.group_analysis(rows, sexes, coefficients=group.load_coefficients(incomplete))
