"""Application flow tests.

Respondents are tokens, reviewers are role strings, and no real person or
organisation appears anywhere in this file.
"""

from __future__ import annotations

import pytest

from sentinel.app.service import (
    REJECTED_NOTICE,
    SAFE_FALLBACK_NOTICE,
    STAGE_CRISIS_REVIEW,
    STAGE_RESULT_REVIEW,
    STATE_INVALID,
    STATE_PENDING_REVIEW,
    STATE_REJECTED,
    STATE_RELEASED,
    SentinelService,
    SubmissionNotFound,
    derive_token,
)
from sentinel.core.errors import GateSetupError, StateError
from sentinel.core.gates import GateChain
from sentinel.core.llm import LLMOutput, LLMProvider, PromptLibrary
from sentinel.packs.crisis.classify import CrisisGate, Taxonomy
from sentinel.packs.samdlint.lint import SamdLintGate
from tests.conftest import build_answers

# Domain D (satisfaction) is excluded from the selection criteria and has no
# scored items, so it does not appear here.
HIGH_STRESS = {"A": 40, "B": 90, "C": 30}
LOW_STRESS = {"A": 30, "B": 40, "C": 30}


@pytest.fixture
def service():
    with SentinelService(":memory:") as svc:
        yield svc


def high_answers(omit=()):
    return build_answers("57", HIGH_STRESS, omit=omit)


def low_answers():
    return build_answers("57", LOW_STRESS)


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


def test_a_low_stress_submission_is_released_without_a_review(service):
    submission = service.submit(low_answers(), "57", token_seed="t01")
    assert submission.score.high_stress is False
    assert submission.state == STATE_RELEASED
    assert submission.interrupt_id is None
    assert service.release(submission.token)["state"] == STATE_RELEASED


def test_a_high_stress_submission_waits_for_a_signature(service):
    submission = service.submit(high_answers(), "57", token_seed="t02")
    assert submission.score.high_stress is True
    assert submission.state == STATE_PENDING_REVIEW
    assert submission.interrupt_id is not None

    released = service.release(submission.token)
    assert released["state"] == STATE_PENDING_REVIEW
    assert released["text"] == ""
    assert released["gate"] == "signature"
    assert "no_implementer_signature" in released["reasons"]


def test_a_missing_answer_holds_the_judgement(service):
    submission = service.submit(high_answers(omit=(5,)), "57", token_seed="t03")
    assert submission.score.valid is False
    assert submission.score.high_stress is False
    assert submission.state == STATE_INVALID
    released = service.release(submission.token)
    assert released["state"] == STATE_INVALID
    assert "もう一度回答" in released["text"]


def test_the_token_is_derived_and_the_seed_is_not_stored(service):
    seed = "issued-code-0001"
    submission = service.submit(low_answers(), "57", token_seed=seed)
    # The token is the salted derivation, and cannot be recomputed from the
    # issued code alone (F3): with the server salt it matches, without it does not.
    assert submission.token == derive_token(seed, salt=service._token_salt)
    assert submission.token != derive_token(seed)
    assert seed not in str(service._load(submission.token))


def test_two_submissions_without_a_seed_get_different_tokens(service):
    a = service.submit(low_answers(), "57")
    b = service.submit(low_answers(), "57")
    assert a.token != b.token


def test_unknown_token_is_refused(service):
    with pytest.raises(SubmissionNotFound):
        service.release("0000000000000000")


# ---------------------------------------------------------------------------
# R7: the free text does not survive the request
# ---------------------------------------------------------------------------


def test_the_free_text_is_never_stored(service):
    secret_sentence = "上司との面談で強く言われたことが頭から離れません"
    submission = service.submit(low_answers(), "57", free_text=secret_sentence, token_seed="t04")

    stored = str(service._load(submission.token))
    assert secret_sentence not in stored

    everything = "".join(str(record.payload) for record in service.audit.records())
    assert secret_sentence not in everything

    queue = str(service.pending_reviews())
    assert secret_sentence not in queue


def test_a_crisis_is_recorded_as_ids_and_a_hash(service):
    submission = service.submit(low_answers(), "57", free_text="もう死にたい", token_seed="t05")
    assert submission.crisis.level == "ideation"
    stored = str(service._load(submission.token))
    assert "死にたい" not in stored
    assert submission.crisis.text_sha256 in stored


# ---------------------------------------------------------------------------
# R3-G3: a crisis bypasses generation entirely
# ---------------------------------------------------------------------------


class ExplodingProvider(LLMProvider):
    """Fails if anything asks it to generate. Proves the bypass is a bypass."""

    name = "exploding"

    def generate(self, prompt_id, variables):
        raise AssertionError(f"generation was attempted for {prompt_id!r} during a crisis")


def test_no_generation_is_attempted_when_a_crisis_is_detected():
    with SentinelService(":memory:", provider=ExplodingProvider()) as svc:
        submission = svc.submit(low_answers(), "57", free_text="遺書を書いた", token_seed="t06")
        assert submission.crisis.level == "prepared"
        assert submission.draft_source == "fixed_text"


def test_the_crisis_screen_is_shown_immediately_not_after_review(service):
    submission = service.submit(high_answers(), "57", free_text="もう死にたい", token_seed="t07")
    released = service.release(submission.token)
    # The result itself is still withheld pending the signature ...
    assert released["state"] == STATE_PENDING_REVIEW
    assert released["text"] == ""
    # ... but the helplines are on the screen now.
    assert released["crisis_response"]["hotlines"]
    assert "0120-061-338" in released["crisis_response"]["text"]


def test_a_crisis_also_opens_a_review_for_the_implementer(service):
    submission = service.submit(low_answers(), "57", free_text="もう死にたい", token_seed="t08")
    stages = [item["stage"] for item in service.pending_reviews()]
    assert STAGE_CRISIS_REVIEW in stages
    assert submission.crisis_interrupt_id is not None


# ---------------------------------------------------------------------------
# R6: the four decisions
# ---------------------------------------------------------------------------


def test_approve_releases_the_draft_unchanged(service):
    submission = service.submit(high_answers(), "57", token_seed="t10")
    draft = submission.draft_notice
    service.decide(submission.interrupt_id, "approve", "implementer-a")
    released = service.release(submission.token)
    assert released["state"] == STATE_RELEASED
    assert released["text"] == draft


def test_edit_releases_the_reviewers_wording(service):
    submission = service.submit(high_answers(), "57", token_seed="t11")
    service.decide(
        submission.interrupt_id,
        "edit",
        "implementer-a",
        edited={"notice_text": "実施者が調整した案内文です。医学的な診断ではありません。"},
    )
    released = service.release(submission.token)
    assert released["text"].startswith("実施者が調整した")
    assert released["source"] == "implementer"


def test_respond_discards_the_draft(service):
    submission = service.submit(high_answers(), "57", token_seed="t12")
    draft = submission.draft_notice
    service.decide(
        submission.interrupt_id,
        "respond",
        "implementer-a",
        edited={"notice_text": "面談の日程をご案内します。医学的な診断ではありません。"},
    )
    released = service.release(submission.token)
    assert released["text"] != draft
    assert "面談の日程" in released["text"]


def test_reject_shows_a_fixed_notice_and_never_the_draft(service):
    submission = service.submit(high_answers(), "57", token_seed="t13")
    draft = submission.draft_notice
    service.decide(submission.interrupt_id, "reject", "implementer-a", note="個別に連絡する")
    released = service.release(submission.token)
    assert released["state"] == STATE_REJECTED
    assert released["text"] == REJECTED_NOTICE
    assert draft not in released["text"]


def test_reject_records_no_signature(service):
    submission = service.submit(high_answers(), "57", token_seed="t14")
    service.decide(submission.interrupt_id, "reject", "implementer-a")
    assert service.has_signature(submission.payload_hash) is False


def test_a_decision_cannot_be_replayed(service):
    submission = service.submit(high_answers(), "57", token_seed="t15")
    service.decide(submission.interrupt_id, "approve", "implementer-a")
    with pytest.raises(StateError):
        service.decide(submission.interrupt_id, "approve", "implementer-a")


def test_a_replayed_decision_does_not_write_a_second_signature(service):
    submission = service.submit(high_answers(), "57", token_seed="t16")
    service.decide(submission.interrupt_id, "approve", "implementer-a")
    before = sum(1 for _ in service.audit.records(kind="signature"))
    with pytest.raises(StateError):
        service.decide(
            submission.interrupt_id, "edit", "implementer-b", edited={"notice_text": "x"}
        )
    after = sum(1 for _ in service.audit.records(kind="signature"))
    assert before == after == 1


def test_an_unknown_decision_is_refused(service):
    submission = service.submit(high_answers(), "57", token_seed="t17")
    with pytest.raises(ValueError, match="unknown decision"):
        service.decide(submission.interrupt_id, "maybe", "implementer-a")


def test_an_unattributed_decision_is_refused(service):
    submission = service.submit(high_answers(), "57", token_seed="t18")
    with pytest.raises(ValueError, match="actor"):
        service.decide(submission.interrupt_id, "approve", "")


# ---------------------------------------------------------------------------
# R3-G1: the signature is bound to the data
# ---------------------------------------------------------------------------


def test_a_signature_does_not_release_a_different_submission(service):
    first = service.submit(high_answers(), "57", token_seed="t20")
    second = service.submit(high_answers(), "57", token_seed="t21")
    service.decide(first.interrupt_id, "approve", "implementer-a")

    assert service.release(first.token)["state"] == STATE_RELEASED
    assert service.release(second.token)["state"] == STATE_PENDING_REVIEW


def test_the_signature_is_recorded_in_the_audit_log(service):
    submission = service.submit(high_answers(), "57", token_seed="t22")
    service.decide(submission.interrupt_id, "approve", "implementer-a")
    signatures = [r.payload for r in service.audit.records(kind="signature")]
    assert signatures[0]["payload_hash"] == submission.payload_hash
    assert signatures[0]["actor"] == "implementer-a"
    assert service.audit.verify_chain() is True


# ---------------------------------------------------------------------------
# R4-4: every outbound text goes through the chain
# ---------------------------------------------------------------------------


class ForbiddenProvider(LLMProvider):
    """A provider that emits exactly what the lint exists to stop."""

    name = "forbidden"

    def generate(self, prompt_id, variables):
        return LLMOutput(
            text="あなたはうつ病の可能性が高い状態です。",
            provider=self.name,
            prompt_id=prompt_id,
            model="test",
            fallback=False,
        )


def test_a_forbidden_generation_never_reaches_the_respondent():
    with SentinelService(":memory:", provider=ForbiddenProvider()) as svc:
        submission = svc.submit(low_answers(), "57", token_seed="t30")
        released = svc.release(submission.token)
        assert released["gate_ok"] is False
        assert released["gate"] == "samd_lint"
        assert released["text"] == SAFE_FALLBACK_NOTICE
        assert "うつ病" not in released["text"]
        assert svc.kpi()["notice_blocked"] == 1


def test_a_reviewers_own_wording_is_linted_too(service):
    """The rule is about what the product displays, not about who typed it."""
    submission = service.submit(high_answers(), "57", token_seed="t31")
    service.decide(
        submission.interrupt_id,
        "respond",
        "implementer-a",
        edited={"notice_text": "適応障害の疑いがあります。"},
    )
    released = service.release(submission.token)
    assert released["gate_ok"] is False
    assert released["text"] == SAFE_FALLBACK_NOTICE


def test_the_chain_order_is_crisis_then_lint_then_signature(service):
    assert service.chain.names == ("crisis", "samd_lint", "signature")


def test_a_service_cannot_start_with_an_empty_dictionary():
    empty = CrisisGate(taxonomy=Taxonomy(signals=(), negations=()))
    with pytest.raises(GateSetupError):
        GateChain([empty, SamdLintGate()])


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def test_gate_check_reports_every_gate_not_only_the_first(service):
    report = service.gate_check("死にたい。うつ病の可能性があります。")
    assert report["ok"] is False
    assert report["blocked_by"] == "crisis"
    assert [g["gate"] for g in report["gates"]] == ["crisis", "samd_lint", "signature"]
    blocked = {g["gate"] for g in report["gates"] if not g["ok"]}
    assert blocked == {"crisis", "samd_lint"}


def test_gate_check_passes_ordinary_text(service):
    assert service.gate_check("休養の時間を確保できているか振り返ってみてください。")["ok"] is True


def test_kpi_reports_the_override_rate(service):
    for index, decision in enumerate(["approve", "approve", "edit", "reject"]):
        submission = service.submit(high_answers(), "57", token_seed=f"k{index}")
        service.decide(
            submission.interrupt_id,
            decision,
            "implementer-a",
            edited={"notice_text": "調整済みの案内文です。"} if decision == "edit" else None,
        )
    kpi = service.kpi()
    assert kpi["total"] == 4
    assert kpi["approve"] == 2
    assert kpi["override_rate"] == 0.5
    assert kpi["submissions"] == 4
    assert kpi["audit_chain_ok"] is True


def test_the_audit_sample_is_reproducible(service):
    for index in range(6):
        submission = service.submit(high_answers(), "57", token_seed=f"s{index}")
        service.decide(submission.interrupt_id, "approve", "implementer-a")
    first = [item["interrupt_id"] for item in service.sample_for_audit(3, seed="2026-08")]
    second = [item["interrupt_id"] for item in service.sample_for_audit(3, seed="2026-08")]
    third = [item["interrupt_id"] for item in service.sample_for_audit(3, seed="2026-09")]
    assert first == second
    assert len(first) == 3
    assert first != third or len(set(first) & set(third)) < 3


# ---------------------------------------------------------------------------
# The provider default
# ---------------------------------------------------------------------------


def test_the_default_provider_generates_nothing(service, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    submission = service.submit(low_answers(), "57", token_seed="t40")
    assert submission.draft_source == "fallback_text"
    assert "AIが生成した文章ではなく" in submission.draft_notice


def test_the_notice_carries_the_helplines(service):
    submission = service.submit(low_answers(), "57", token_seed="t41")
    assert "【相談窓口】" in submission.draft_notice
    assert "0120-061-338" in submission.draft_notice


def test_the_notice_states_it_is_not_a_diagnosis(service):
    submission = service.submit(low_answers(), "57", token_seed="t42")
    assert "診断ではありません" in submission.draft_notice


def test_prompts_are_looked_up_by_id_not_composed_in_code(service):
    library = PromptLibrary.load()
    assert "selfcare_advice" in library
    assert "result_plain_language" in library


def test_the_review_queue_shows_the_text_the_respondent_would_see(service):
    submission = service.submit(high_answers(), "57", token_seed="t43")
    queued = service.pending_reviews(STAGE_RESULT_REVIEW)[0]
    assert queued["payload"]["notice_text"] == submission.draft_notice
