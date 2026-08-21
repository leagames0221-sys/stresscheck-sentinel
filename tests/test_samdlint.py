"""Forbidden-expression lint tests."""

from __future__ import annotations

import pytest

from sentinel.core.errors import DataFileError, GateSetupError
from sentinel.core.gates import GateChain
from sentinel.packs.samdlint import lint as samdlint
from sentinel.packs.samdlint.lint import Dictionary, SamdLintGate, lint, load_dictionary


@pytest.fixture(autouse=True)
def _clear_caches():
    samdlint.clear_caches()
    yield
    samdlint.clear_caches()


# ---------------------------------------------------------------------------
# What must pass. These are the sentences the product actually needs to say.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "領域Bの合計点は 45 点でした。",
        "この結果は医学的な診断ではありません。",
        "睡眠と休養の時間を確保できているか、振り返ってみてください。",
        "気になることがあれば、相談窓口に連絡してください。",
        "職場の相談体制について、産業保健スタッフに問い合わせることができます。",
        "高ストレス者の選定基準に該当しました。",
        "この文章はAIが生成しました。",
        "",
    ],
)
def test_ordinary_wording_passes(text: str):
    verdict = lint(text)
    assert verdict.ok is True, verdict.reasons
    assert verdict.gate == "samd_lint"


# ---------------------------------------------------------------------------
# What must be blocked.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "うつ病の可能性があります。",
        "適応障害が疑われます。",
        "不安障害を発症するおそれがあります。",
        "メンタル不調のリスクが高い状態です。",
        "バーンアウトの兆候がみられます。",
    ],
)
def test_a_condition_and_a_likelihood_in_one_sentence_is_blocked(text: str):
    verdict = lint(text)
    assert verdict.ok is False
    assert any("+" in reason for reason in verdict.reasons)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("うつのリスクは 32% です。", "F201"),
        ("35% のリスクがあります。", "F202"),
        ("抑うつスコア: 18 です。", "F203"),
        ("この結果から診断できます。", "F204"),
        ("受診が必要です。", "F205"),
        ("心療内科に受診してください。", "F206"),
        ("あなたは軽度の抑うつ症です。", "F207"),
    ],
)
def test_forbidden_constructions_are_blocked_on_sight(text: str, rule: str):
    verdict = lint(text)
    assert verdict.ok is False
    assert rule in verdict.reasons


def test_a_disclaimer_does_not_unblock_anything():
    """The regulator says a disclaimer is not a defence; neither is it here."""
    text = "うつ病の可能性があります。ただしこれは診断ではなく、参考情報です。"
    assert lint(text).ok is False


# ---------------------------------------------------------------------------
# The pair rule is scoped to a sentence, and that scoping is load-bearing.
# ---------------------------------------------------------------------------


def test_a_condition_alone_is_not_blocked():
    assert lint("このツールはうつ病を判定するものではありません。").ok is True


def test_a_likelihood_word_alone_is_not_blocked():
    assert lint("業務量が増える可能性があります。").ok is True


def test_the_pair_must_be_in_the_same_sentence():
    separated = "このツールはうつ病を扱いません。業務量が増える可能性があります。"
    assert lint(separated).ok is True


def test_a_comma_does_not_split_a_sentence():
    """Otherwise a forbidden pair could hide behind a 、."""
    assert lint("うつ病、その可能性があります").ok is False


def test_newline_splits_sentences():
    assert lint("うつ病について\n可能性を検討する").ok is True


# ---------------------------------------------------------------------------
# Reasons: rule ids, never the text
# ---------------------------------------------------------------------------


def test_reasons_name_rules_and_do_not_quote_the_input():
    verdict = lint("パニック障害の可能性があります。")
    assert verdict.reasons
    assert all("パニック" not in reason for reason in verdict.reasons)
    assert all(reason[0] == "F" for reason in verdict.reasons)


def test_reasons_are_sorted_and_deduplicated():
    verdict = lint("うつ病の可能性があります。うつ病の可能性があります。")
    assert list(verdict.reasons) == sorted(set(verdict.reasons))


# ---------------------------------------------------------------------------
# R3-G4
# ---------------------------------------------------------------------------


def test_dictionary_missing_a_group_is_refused():
    with pytest.raises(GateSetupError, match="empty rule set"):
        Dictionary(diseases=(), claims=(), regexes=()).validate()


def test_empty_dictionary_is_also_a_valueerror():
    with pytest.raises(ValueError):
        Dictionary(diseases=(), claims=(), regexes=()).validate()


def test_empty_dictionary_file_is_refused(tmp_path):
    path = tmp_path / "samd_forbidden.csv"
    path.write_text("# source: test\nid,kind,value,severity,note\n", encoding="utf-8")
    with pytest.raises(GateSetupError, match="empty rule set"):
        load_dictionary(path)


def test_a_dictionary_with_conditions_but_no_claims_is_refused(tmp_path):
    """The half-loaded case: it would report success on every check."""
    path = tmp_path / "samd_forbidden.csv"
    path.write_text(
        "# source: test\nid,kind,value,severity,note\nF001,disease,うつ病,high,x\n",
        encoding="utf-8",
    )
    with pytest.raises(GateSetupError, match="claim"):
        load_dictionary(path)


def test_gate_with_an_empty_dictionary_refuses_to_join_a_chain():
    gate = SamdLintGate(dictionary=Dictionary(diseases=(), claims=(), regexes=()))
    with pytest.raises(GateSetupError):
        GateChain([gate])


def test_unknown_kind_is_rejected(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text(
        "# source: test\nid,kind,value,severity,note\nF001,forbidden,うつ病,high,x\n",
        encoding="utf-8",
    )
    with pytest.raises(DataFileError, match="unknown kind"):
        load_dictionary(path)


def test_duplicate_term_id_is_rejected(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text(
        "# source: test\nid,kind,value,severity,note\n"
        "F001,disease,うつ病,high,a\nF001,claim,可能性,high,b\n",
        encoding="utf-8",
    )
    with pytest.raises(DataFileError, match="duplicate term id"):
        load_dictionary(path)


# ---------------------------------------------------------------------------
# Gate wiring
# ---------------------------------------------------------------------------


def test_gate_reads_the_configured_key():
    gate = SamdLintGate(text_key="notice")
    assert gate.check({"notice": "うつ病の可能性があります。"}).ok is False
    assert gate.check({"text": "うつ病の可能性があります。"}).ok is True


def test_gate_with_a_missing_key_passes():
    assert SamdLintGate().check({}).ok is True


def test_bundled_dictionary_has_all_three_groups():
    dictionary = load_dictionary()
    assert dictionary.diseases and dictionary.claims and dictionary.regexes
    dictionary.validate()
