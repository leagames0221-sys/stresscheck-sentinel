"""Crisis classifier tests.

The text used here is written for the test. It is not quoted from anyone.
"""

from __future__ import annotations

import pytest

from sentinel.core.errors import DataFileError, GateSetupError
from sentinel.core.gates import GateChain
from sentinel.packs.crisis import classify as crisis
from sentinel.packs.crisis.classify import CrisisGate, Taxonomy, classify, load_taxonomy
from sentinel.packs.crisis.response import fixed_response, load_hotlines
from sentinel.packs.samdlint.lint import lint


@pytest.fixture(autouse=True)
def _clear_caches():
    crisis.clear_caches()
    yield
    crisis.clear_caches()


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def test_neutral_text_is_not_a_crisis():
    result = classify("最近は残業が多くて疲れていますが、週末は休めています。")
    assert result.level == "none"
    assert result.detected is False
    assert result.matched_ids == ()


def test_empty_text_is_not_a_crisis():
    assert classify("").level == "none"
    assert classify("").text_sha256  # the hash of the empty string is still a receipt


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("職場の自殺予防の研修を受けました。", "explore"),
        ("希死念慮という言葉を最近知りました。", "explore"),
        ("もう死にたいと毎日思っています。", "ideation"),
        ("自分なんていない方がいいと感じます。", "ideation"),
        ("楽になりたいとしか考えられません。", "ideation"),
        ("帰りに踏切の前で立ち止まってしまいました。", "plan"),
        ("方法を調べてしまいました。", "plan"),
        ("今夜すべて終わりにするつもりです。", "plan"),
        ("遺書を書き終えました。", "prepared"),
        ("身辺整理をしています。", "prepared"),
        ("もう準備はできた。", "prepared"),
        ("薬をためています。", "prepared"),
    ],
)
def test_each_stage_is_reached(text: str, expected: str):
    assert classify(text).level == expected


@pytest.mark.parametrize(
    "text",
    [
        "もう準備はできた。",
        "もう準備はできました。",
        "もう準備は整った。",
        "もう準備は整いました。",
        "用意はできました。",
        "用意は終わりました。",
    ],
)
def test_polite_and_plain_forms_reach_the_same_stage(text: str):
    """A rule that only knew the plain form would miss half of what people write.

    Japanese free text in a workplace questionnaire skews polite. 「準備は整いました」
    and 「準備は整った」 say the same thing, and a detector that catches one and not
    the other is not detecting the statement — it is detecting a register.
    """
    assert classify(text).level == "prepared"
    assert "R009" in classify(text).matched_ids


def test_the_most_severe_stage_wins_not_the_first_match():
    # ideation appears first in the sentence, plan appears second.
    result = classify("死にたいと思っていて、飛び降りることばかり考えます。")
    assert result.level == "plan"
    assert "I001" in result.matched_ids
    assert "P001" in result.matched_ids


def test_severity_ordering_is_total():
    assert crisis.SEVERITY["none"] < crisis.SEVERITY["explore"] < crisis.SEVERITY["ideation"]
    assert crisis.SEVERITY["ideation"] < crisis.SEVERITY["plan"] < crisis.SEVERITY["prepared"]


# ---------------------------------------------------------------------------
# Negation
# ---------------------------------------------------------------------------


def test_adjacent_negation_cancels_the_signal():
    result = classify("死にたいと思ったことはないです。")
    assert result.level == "none"
    assert result.matched_ids == ()
    assert "I001" in result.negated_ids


@pytest.mark.parametrize(
    "text",
    [
        "死にたいとは思わないです。",
        "死にたいわけではないです。",
        "死にたいほどではないです。",
        "死にたいなどとは思わないです。",
    ],
)
def test_negation_variants(text: str):
    assert classify(text).level == "none"


def test_third_party_framing_cancels_a_general_mention():
    assert classify("自殺報道をニュースで見た。").level == "none"
    assert classify("自殺予防の記事を読んだ。").level == "none"


def test_negation_cancels_one_occurrence_not_the_whole_message():
    """A disclaimer in the first sentence must not silence the second."""
    result = classify("死にたいと思ったことはない。でも今は死にたい。")
    assert result.level == "ideation"
    assert "I001" in result.matched_ids


def test_negation_does_not_reach_across_a_sentence_boundary():
    result = classify("死にたい。仕事のことは、とは思わない。")
    assert result.level == "ideation"


def test_negation_does_not_reach_across_a_long_gap():
    result = classify("死にたい気持ちが強い日もあるが、そこまでのことだとは思わない。")
    assert result.level == "ideation"


# ---------------------------------------------------------------------------
# Occurrences: why a verdict came out that way
# ---------------------------------------------------------------------------


def test_occurrences_report_spans_and_cancellation():
    hits = crisis.occurrences("死にたいと思ったことはない。")
    assert [h.rule_id for h in hits] == ["I001"]
    assert hits[0].start == 0
    assert hits[0].cancelled is True
    assert hits[0].cancelled_by == "N001"


# ---------------------------------------------------------------------------
# Privacy: the result carries ids and a hash, never the words
# ---------------------------------------------------------------------------


def test_result_dict_contains_no_respondent_text():
    text = "死にたいと毎日思っています。"
    payload = classify(text).as_dict()
    serialised = repr(payload)
    assert text not in serialised
    assert "死にたい" not in serialised
    assert payload["text_sha256"] and len(str(payload["text_sha256"])) == 64


def test_hash_is_stable_and_input_dependent():
    a = classify("死にたい")
    b = classify("死にたい")
    c = classify("元気です")
    assert a.text_sha256 == b.text_sha256
    assert a.text_sha256 != c.text_sha256


# ---------------------------------------------------------------------------
# R3-G4: a taxonomy that cannot detect anything must not start
# ---------------------------------------------------------------------------


def test_empty_taxonomy_raises_at_load_time(tmp_path):
    path = tmp_path / "crisis_taxonomy.csv"
    path.write_text("# source: test\nid,level,kind,value,note\n", encoding="utf-8")
    with pytest.raises(GateSetupError, match="empty rule set"):
        load_taxonomy(path)


def test_empty_taxonomy_is_also_a_valueerror():
    """api_contract.md states R3-G4 as ValueError; keep that literally true."""
    with pytest.raises(ValueError):
        Taxonomy(signals=(), negations=()).validate()


def test_gate_with_an_empty_taxonomy_refuses_to_join_a_chain():
    gate = CrisisGate(taxonomy=Taxonomy(signals=(), negations=()))
    with pytest.raises(GateSetupError):
        GateChain([gate])


def test_taxonomy_with_no_negations_still_loads():
    """Over-detection is the safe direction, so no-negations is legal."""
    path_rows = "# source: test\nid,level,kind,value,note\nI001,ideation,keyword,死にたい,x\n"
    taxonomy = _write_and_load(path_rows)
    assert taxonomy.negations == ()
    assert classify("死にたいと思ったことはない", taxonomy).level == "ideation"


def test_unknown_level_is_rejected(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text(
        "# source: test\nid,level,kind,value,note\nZ001,urgent,keyword,x,note\n", encoding="utf-8"
    )
    with pytest.raises(DataFileError, match="unknown level"):
        load_taxonomy(path)


def test_duplicate_rule_id_is_rejected(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text(
        "# source: test\nid,level,kind,value,note\n"
        "I001,ideation,keyword,死にたい,a\n"
        "I001,ideation,keyword,消えたい,b\n",
        encoding="utf-8",
    )
    with pytest.raises(DataFileError, match="duplicate rule id"):
        load_taxonomy(path)


def test_invalid_regex_is_rejected(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text(
        '# source: test\nid,level,kind,value,note\nP001,plan,regex,"(unclosed",note\n',
        encoding="utf-8",
    )
    with pytest.raises(DataFileError, match="invalid regex"):
        load_taxonomy(path)


def _write_and_load(content: str) -> Taxonomy:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.csv"
        path.write_text(content, encoding="utf-8")
        return load_taxonomy(path)


# ---------------------------------------------------------------------------
# Gate behaviour inside a chain
# ---------------------------------------------------------------------------


def test_crisis_gate_blocks_and_names_the_rules():
    result = CrisisGate().check({"text": "遺書を書きました。"})
    assert result.ok is False
    assert result.gate == "crisis"
    assert result.reasons[0] == "crisis:prepared"
    assert "R001" in result.reasons


def test_crisis_gate_passes_neutral_text():
    assert CrisisGate().check({"text": "特に問題ありません。"}).ok is True


def test_crisis_gate_treats_a_missing_key_as_no_text():
    assert CrisisGate().check({}).ok is True


def test_crisis_gate_reasons_contain_no_respondent_text():
    result = CrisisGate().check({"text": "死にたいです。"})
    assert all("死にたい" not in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# The fixed response
# ---------------------------------------------------------------------------


def test_fixed_response_lists_every_published_helpline():
    hotlines = load_hotlines()
    response = fixed_response("ideation")
    assert len(response["hotlines"]) == len(hotlines)
    for hotline in hotlines:
        assert hotline.phone in str(response["text"])


def test_fixed_response_is_not_generated():
    assert fixed_response("plan")["generated_by"] == "fixed_text"


def test_fixed_response_survives_the_forbidden_expression_lint():
    """The safety text must not be blocked by the safety lint."""
    verdict = lint(str(fixed_response("prepared")["text"]))
    assert verdict.ok is True, verdict.reasons


def test_fixed_response_wording_does_not_vary_by_stage():
    """Triaging the wording by severity would be triage by text generator."""
    a = fixed_response("explore")
    b = fixed_response("prepared")
    assert a["headline"] == b["headline"]
    assert a["hotlines"] == b["hotlines"]


def test_empty_helpline_file_is_refused(tmp_path):
    path = tmp_path / "hotlines_ja.csv"
    path.write_text(
        "# source: test\nid,name,phone,alt_phone,hours,scope,url,fetched_on\n", encoding="utf-8"
    )
    with pytest.raises(GateSetupError, match="empty rule set"):
        load_hotlines(path)
