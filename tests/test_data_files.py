"""Tests over the shipped data files themselves.

The CSVs are the part a reviewer is most likely to change and least likely to
have a test for, so the invariants are checked here rather than assumed: every
file names its source, the two questionnaire variants agree with each other, and
the reversal list in the threshold file agrees with the flag on each item.

The last one matters because reversal is stated twice — once per item, once as a
customisable list — and two statements of the same fact drift.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from sentinel.core.datafiles import data_dir, data_path, read_header_comments, read_rows
from sentinel.packs.jsq import group, scoring

SHIPPED = (
    "jsq_items_57.csv",
    "jsq_items_23.csv",
    "jsq_thresholds.csv",
    "hotlines_ja.csv",
    "samd_forbidden.csv",
    "crisis_taxonomy.csv",
    "sjd_coefficients.csv",
)


@pytest.mark.parametrize("name", SHIPPED)
def test_every_shipped_file_exists(name):
    assert data_path(name).is_file()


def test_no_shipped_file_is_undocumented():
    """A new CSV must be added to `SHIPPED`, so it cannot arrive unexamined."""
    on_disk = {p.name for p in data_dir().glob("*.csv")}
    assert on_disk == set(SHIPPED)


@pytest.mark.parametrize("name", SHIPPED)
def test_every_file_declares_its_provenance(name):
    comments = read_header_comments(data_path(name))
    assert comments, f"{name} has no comment header"
    joined = " ".join(comments)
    assert "source:" in joined or "UNVERIFIED" in joined
    assert "license:" in joined or "UNVERIFIED" in joined


@pytest.mark.parametrize("name", SHIPPED)
def test_every_file_is_utf8_without_a_bom(name):
    raw = data_path(name).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    raw.decode("utf-8")


@pytest.mark.parametrize("name", SHIPPED)
def test_no_file_has_ragged_rows(name):
    path = data_path(name)
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    reader = csv.reader(lines)
    header = next(reader)
    for i, row in enumerate(reader, start=2):
        assert len(row) == len(header), (
            f"{name} row {i} has {len(row)} fields, expected {len(header)}"
        )


# ---------------------------------------------------------------------------
# Questionnaire structure
# ---------------------------------------------------------------------------


def test_57_item_form_has_the_expected_shape():
    items = scoring.load_items("57")
    assert len(items) == 57
    counts = {d: sum(1 for i in items if i.domain == d) for d in ("A", "B", "C", "D")}
    assert counts == {"A": 17, "B": 29, "C": 9, "D": 2}


def test_only_the_satisfaction_items_are_excluded_from_scoring():
    unscored = [i.item_no for i in scoring.load_items("57") if not i.scored]
    assert unscored == [56, 57]


def test_domain_maximums_match_the_source_arithmetic():
    """B max = 4x29 = 116; A+C max = 4x17 + 4x9 = 104, both stated in the source."""
    items = [i for i in scoring.load_items("57") if i.scored]
    b_max = 4 * sum(1 for i in items if i.domain == "B")
    ac_max = 4 * sum(1 for i in items if i.domain in ("A", "C"))
    assert (b_max, ac_max) == (116, 104)


@pytest.mark.parametrize("variant", ["57", "23"])
def test_every_item_has_four_choices_and_text(variant):
    for item in scoring.load_items(variant):
        assert len(item.choices) == 4, f"item {item.item_no} has {len(item.choices)} choices"
        assert item.text.strip()
        assert item.scale.strip()


@pytest.mark.parametrize("variant", ["57", "23"])
def test_choices_are_consistent_within_a_domain(variant):
    by_domain: dict[str, set[tuple[str, ...]]] = {}
    for item in scoring.load_items(variant):
        by_domain.setdefault(item.domain, set()).add(item.choices)
    for domain, choice_sets in by_domain.items():
        assert len(choice_sets) == 1, f"domain {domain} has inconsistent choice labels"


def test_support_items_carry_their_shared_question_text():
    """The C items are one-word answers; without the lead-in they are meaningless."""
    for item in scoring.load_items("57"):
        if item.domain == "C":
            assert item.context.endswith("？")
        else:
            assert item.context == ""


# ---------------------------------------------------------------------------
# The two variants must not drift apart
# ---------------------------------------------------------------------------


def test_23_item_form_is_a_verbatim_subset_of_the_57_item_form():
    by_no = {i.item_no: i for i in scoring.load_items("57")}
    path = data_path("jsq_items_23.csv")
    for row in read_rows(path):
        source = by_no[int(row["source_item_57"])]
        assert row["text"].strip() == source.text
        assert row["choices"].strip() == "|".join(source.choices)
        assert row["domain"].strip() == source.domain
        assert row["context"].strip() == source.context
        assert int(row["domain_item_no"]) == source.domain_item_no


def test_23_item_source_numbers_are_the_published_selection():
    """A1-3, A8-10; B7-14, B16, B27, B29; C1, C2, C4, C5, C7, C8."""
    numbers = [int(row["source_item_57"]) for row in read_rows(data_path("jsq_items_23.csv"))]
    assert numbers == [
        1,
        2,
        3,
        8,
        9,
        10,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        33,
        44,
        46,
        47,
        48,
        50,
        51,
        53,
        54,
    ]


def test_23_item_reversal_is_exactly_the_inherited_subset():
    """No item may become reverse-worded merely by moving to the short form."""
    reverse_57 = {i.item_no for i in scoring.load_items("57") if i.reverse}
    rows = list(read_rows(data_path("jsq_items_23.csv")))
    inherited = {int(r["item_no"]) for r in rows if int(r["source_item_57"]) in reverse_57}
    declared = {int(r["item_no"]) for r in rows if r["reverse"] == "1"}
    assert declared == inherited == {1, 2, 3}


# ---------------------------------------------------------------------------
# The reversal list is stated twice; the statements must agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["57", "23"])
def test_item_reverse_flags_match_the_threshold_files_list(variant):
    from_items = {i.item_no for i in scoring.load_items(variant) if i.reverse}
    from_thresholds = scoring.load_thresholds().for_variant(variant).reverse_items
    assert from_items == set(from_thresholds)


def test_threshold_file_carries_both_variants():
    assert set(scoring.load_thresholds().variants) == {"57", "23"}


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        ("57", {"B_only": [("B", 77)], "AC_and_B": [("A+C", 76), ("B", 63)]}),
        ("23", {"B_only": [("B", 31)], "AC_and_B": [("A+C", 39), ("B", 23)]}),
    ],
)
def test_published_cutoffs_are_the_shipped_defaults(variant, expected):
    rules = scoring.load_thresholds().for_variant(variant).rules
    actual = {r.name: [(c.expr, c.value) for c in r.conditions] for r in rules}
    assert actual == expected


def test_criteria_are_listed_in_the_source_order():
    """㋐ (B alone) is stated first, so it is reported first when both hold."""
    names = [r.name for r in scoring.load_thresholds().for_variant("57").rules]
    assert names == ["B_only", "AC_and_B"]


# ---------------------------------------------------------------------------
# Group analysis data
# ---------------------------------------------------------------------------


def test_diagram_items_are_twelve_and_come_from_the_full_form():
    valid = {i.item_no for i in scoring.load_items("57")}
    assert len(group.DIAGRAM_ITEMS) == 12
    assert set(group.DIAGRAM_ITEMS) <= valid
    assert all(len(items) == 3 for items in group.SCALE_ITEMS.values())


def test_diagram_items_are_exactly_the_23_item_forms_load_control_and_support():
    """The twelve are the published overlap; a drift here would be silent."""
    assert set(group.DIAGRAM_ITEMS) == {1, 2, 3, 8, 9, 10, 47, 48, 50, 51, 53, 54}


def test_every_diagram_scale_has_a_japanese_label():
    assert set(group.SCALE_LABELS_JA) == set(group.SCALE_ITEMS)


def test_coefficient_file_is_declared_unverified_and_empty():
    """If someone fills this in, they must also flip the marker; both are checked."""
    path = data_path("sjd_coefficients.csv")
    comments = " ".join(read_header_comments(path)).replace(" ", "").lower()
    assert "verified:no" in comments
    assert list(read_rows(path)) == []


# ---------------------------------------------------------------------------
# Safety data
# ---------------------------------------------------------------------------


def test_hotlines_are_present_and_well_formed():
    rows = list(read_rows(data_path("hotlines_ja.csv")))
    assert len(rows) >= 4
    for row in rows:
        assert row["name"].strip()
        assert re.fullmatch(r"[0-9-]{10,}", row["phone"].strip()), row["phone"]
        assert row["hours"].strip()
        assert row["url"].startswith("https://")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["fetched_on"].strip())


def test_the_round_the_clock_helpline_is_included():
    """The crisis path must always have somewhere open to point at."""
    rows = list(read_rows(data_path("hotlines_ja.csv")))
    assert any("24時間" in row["hours"] for row in rows)


def test_forbidden_dictionary_covers_both_halves_of_the_rule():
    rows = list(read_rows(data_path("samd_forbidden.csv")))
    kinds = {row["kind"] for row in rows}
    # `dx` is the adjacency-scoped assertive copula (「…はうつ病だ」): a bare
    # copula that only blocks when glued to a condition name, split out from
    # `claim` so it cannot over-block an educational sentence.
    assert kinds == {"disease", "claim", "regex", "dx"}
    for kind in kinds:
        assert sum(1 for r in rows if r["kind"] == kind) >= 3


def test_forbidden_regexes_compile():
    for row in read_rows(data_path("samd_forbidden.csv")):
        if row["kind"] == "regex":
            re.compile(row["value"])


def test_forbidden_ids_are_unique():
    ids = [row["id"] for row in read_rows(data_path("samd_forbidden.csv"))]
    assert len(ids) == len(set(ids))


def test_crisis_taxonomy_covers_every_stage():
    rows = list(read_rows(data_path("crisis_taxonomy.csv")))
    levels = {row["level"] for row in rows}
    assert {"explore", "ideation", "plan", "prepared"} <= levels
    for level in ("explore", "ideation", "plan", "prepared"):
        assert sum(1 for r in rows if r["level"] == level) >= 5


def test_crisis_regexes_compile():
    for row in read_rows(data_path("crisis_taxonomy.csv")):
        if row["kind"] == "regex":
            re.compile(row["value"])


def test_crisis_ids_are_unique_and_values_non_empty():
    rows = list(read_rows(data_path("crisis_taxonomy.csv")))
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    assert all(row["value"].strip() for row in rows)


# ---------------------------------------------------------------------------
# Data location
# ---------------------------------------------------------------------------


def test_data_dir_can_be_redirected(tmp_path, monkeypatch):
    """Substituting criteria must not mean editing an installed package."""
    custom = tmp_path / "data"
    custom.mkdir()
    for name in ("jsq_items_57.csv", "jsq_thresholds.csv"):
        (custom / name).write_text(data_path(name).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("SENTINEL_DATA_DIR", str(custom))
    scoring.clear_caches()
    assert Path(scoring.load_thresholds().source).parent == custom


def test_a_bad_data_dir_is_reported(monkeypatch):
    monkeypatch.setenv("SENTINEL_DATA_DIR", "/no/such/directory")
    scoring.clear_caches()
    with pytest.raises(Exception, match="not a directory"):
        scoring.load_items("57")
