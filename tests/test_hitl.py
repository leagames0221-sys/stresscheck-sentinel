"""HITL tests."""

from __future__ import annotations

import pytest

from sentinel.core.audit import AuditLog
from sentinel.core.errors import StateError
from sentinel.core.hitl import HitlStore, interrupt_id

DRAFT = {"text": "生成された下書き", "high_stress": True}


@pytest.fixture
def store():
    with HitlStore() as instance:
        yield instance


# ---------------------------------------------------------------------------
# Interrupt ids
# ---------------------------------------------------------------------------


def test_interrupt_id_is_deterministic():
    assert interrupt_id("t01", "review") == interrupt_id("t01", "review")
    assert len(interrupt_id("t01", "review")) == 16


def test_interrupt_id_depends_on_both_token_and_stage():
    assert interrupt_id("t01", "review") != interrupt_id("t02", "review")
    assert interrupt_id("t01", "review") != interrupt_id("t01", "notify")


def test_re_entering_a_stage_reuses_the_interrupt(store):
    first = store.interrupt("t01", "review", DRAFT)
    second = store.interrupt("t01", "review", DRAFT)
    assert first == second
    assert len(store.pending()) == 1


def test_re_entering_does_not_overwrite_the_reviewed_payload(store):
    """A retry must not swap out what the reviewer already looked at."""
    iid = store.interrupt("t01", "review", DRAFT)
    store.resume(iid, "approve", actor="implementer-a")
    store.interrupt("t01", "review", {"text": "別の下書き"})
    assert store.get(iid).payload == DRAFT
    assert store.get(iid).state == "SIGNED"


def test_empty_token_or_stage_is_rejected(store):
    with pytest.raises(ValueError, match="token"):
        store.interrupt("", "review", DRAFT)
    with pytest.raises(ValueError, match="stage"):
        store.interrupt("t01", "", DRAFT)


# ---------------------------------------------------------------------------
# The four decisions
# ---------------------------------------------------------------------------


def test_approve_passes_the_payload_through_unchanged(store):
    iid = store.interrupt("t01", "review", DRAFT)
    assert store.resume(iid, "approve", actor="implementer-a") == DRAFT
    assert store.get(iid).state == "SIGNED"


def test_edit_merges_the_reviewers_changes(store):
    iid = store.interrupt("t01", "review", DRAFT)
    final = store.resume(iid, "edit", actor="implementer-a", edited={"text": "手を入れた文面"})
    assert final == {"text": "手を入れた文面", "high_stress": True}
    assert store.get(iid).state == "SIGNED_EDITED"


def test_edit_records_a_diff(store):
    iid = store.interrupt("t01", "review", DRAFT)
    store.resume(iid, "edit", actor="implementer-a", edited={"text": "手を入れた文面"})
    diff = store.decisions(iid)[0].diff
    assert diff == {"text": {"from": "生成された下書き", "to": "手を入れた文面"}}


def test_respond_discards_the_draft_entirely(store):
    iid = store.interrupt("t01", "review", DRAFT)
    final = store.resume(iid, "respond", actor="implementer-a", edited={"text": "実施者の所見"})
    assert final == {"text": "実施者の所見"}
    assert "high_stress" not in final
    assert store.get(iid).state == "SIGNED_MANUAL"


def test_reject_lets_nothing_through(store):
    iid = store.interrupt("t01", "review", DRAFT)
    assert store.resume(iid, "reject", actor="implementer-a", note="要再確認") == {}
    assert store.get(iid).state == "REJECTED"
    assert store.is_signed(iid) is False


@pytest.mark.parametrize("decision", ["approve", "edit", "respond"])
def test_signed_states_release_downstream_work(store, decision):
    iid = store.interrupt("t01", "review", DRAFT)
    store.resume(iid, decision, actor="implementer-a", edited={"text": "x"})
    assert store.is_signed(iid) is True


def test_edit_without_content_is_rejected(store):
    iid = store.interrupt("t01", "review", DRAFT)
    with pytest.raises(ValueError, match="requires the reviewer's content"):
        store.resume(iid, "edit", actor="implementer-a")


def test_respond_without_content_is_rejected(store):
    iid = store.interrupt("t01", "review", DRAFT)
    with pytest.raises(ValueError, match="requires the reviewer's content"):
        store.resume(iid, "respond", actor="implementer-a", edited={})


def test_unknown_decision_is_rejected(store):
    iid = store.interrupt("t01", "review", DRAFT)
    with pytest.raises(ValueError, match="unknown decision"):
        store.resume(iid, "maybe", actor="implementer-a")  # type: ignore[arg-type]


def test_decision_must_be_attributed(store):
    iid = store.interrupt("t01", "review", DRAFT)
    with pytest.raises(ValueError, match="actor"):
        store.resume(iid, "approve", actor="")


# ---------------------------------------------------------------------------
# No double execution
# ---------------------------------------------------------------------------


def test_second_resume_raises(store):
    iid = store.interrupt("t01", "review", DRAFT)
    store.resume(iid, "approve", actor="implementer-a")
    with pytest.raises(StateError, match="already in state SIGNED"):
        store.resume(iid, "approve", actor="implementer-a")


@pytest.mark.parametrize("first", ["approve", "reject"])
def test_no_decision_can_follow_another(store, first):
    iid = store.interrupt("t01", "review", DRAFT)
    store.resume(iid, first, actor="implementer-a")
    with pytest.raises(StateError):
        store.resume(iid, "edit", actor="implementer-b", edited={"text": "後から差し替え"})


def test_resuming_an_unknown_interrupt_raises(store):
    with pytest.raises(StateError, match="no such interrupt"):
        store.resume("0" * 16, "approve", actor="implementer-a")


def test_only_one_decision_is_ever_recorded(store):
    iid = store.interrupt("t01", "review", DRAFT)
    store.resume(iid, "approve", actor="implementer-a")
    with pytest.raises(StateError):
        store.resume(iid, "approve", actor="implementer-a")
    assert len(store.decisions(iid)) == 1


# ---------------------------------------------------------------------------
# Queue and KPI
# ---------------------------------------------------------------------------


def test_pending_queue_excludes_decided_items(store):
    a = store.interrupt("t01", "review", DRAFT)
    store.interrupt("t02", "review", DRAFT)
    store.resume(a, "approve", actor="implementer-a")
    assert [i.token for i in store.pending()] == ["t02"]


def test_pending_can_be_filtered_by_stage(store):
    store.interrupt("t01", "review", DRAFT)
    store.interrupt("t01", "notify", DRAFT)
    assert [i.stage for i in store.pending(stage="notify")] == ["notify"]


def test_kpi_of_an_empty_store(store):
    assert store.kpi() == {
        "total": 0,
        "override_rate": 0.0,
        "pending": 0,
        "approve": 0,
        "edit": 0,
        "reject": 0,
        "respond": 0,
    }


def test_override_rate_counts_everything_that_is_not_a_plain_approve(store):
    for i, decision in enumerate(["approve", "approve", "edit", "reject"]):
        iid = store.interrupt(f"t{i:02d}", "review", DRAFT)
        store.resume(iid, decision, actor="implementer-a", edited={"text": "x"})
    kpi = store.kpi()
    assert kpi["total"] == 4
    assert kpi["approve"] == 2
    assert kpi["override_rate"] == 0.5


def test_all_approvals_give_a_zero_override_rate(store):
    for i in range(3):
        iid = store.interrupt(f"t{i:02d}", "review", DRAFT)
        store.resume(iid, "approve", actor="implementer-a")
    assert store.kpi()["override_rate"] == 0.0


# ---------------------------------------------------------------------------
# Sampling audit queue
# ---------------------------------------------------------------------------


def test_sample_is_reproducible(store):
    for i in range(20):
        iid = store.interrupt(f"t{i:02d}", "review", DRAFT)
        store.resume(iid, "approve", actor="implementer-a")
    first = [i.id for i in store.sample_for_audit(5, seed="2026-08")]
    second = [i.id for i in store.sample_for_audit(5, seed="2026-08")]
    assert first == second
    assert len(first) == 5


def test_a_different_seed_gives_a_different_sample(store):
    for i in range(20):
        iid = store.interrupt(f"t{i:02d}", "review", DRAFT)
        store.resume(iid, "approve", actor="implementer-a")
    assert [i.id for i in store.sample_for_audit(5, seed="a")] != [
        i.id for i in store.sample_for_audit(5, seed="b")
    ]


def test_sample_ignores_undecided_items(store):
    store.interrupt("t01", "review", DRAFT)
    assert store.sample_for_audit(5) == []


def test_sample_larger_than_the_population_returns_everything(store):
    iid = store.interrupt("t01", "review", DRAFT)
    store.resume(iid, "approve", actor="implementer-a")
    assert len(store.sample_for_audit(50)) == 1


def test_negative_sample_size_is_rejected(store):
    with pytest.raises(ValueError, match="negative"):
        store.sample_for_audit(-1)


# ---------------------------------------------------------------------------
# Audit integration
# ---------------------------------------------------------------------------


def test_decisions_are_written_to_the_audit_log():
    with AuditLog() as audit, HitlStore(audit=audit) as store:
        iid = store.interrupt("t01", "review", DRAFT)
        store.resume(iid, "edit", actor="implementer-a", edited={"text": "手を入れた文面"})

        kinds = [r.kind for r in audit.records()]
        assert kinds == ["hitl.interrupt", "hitl.decision"]
        assert audit.verify_chain() is True

        decision = next(iter(audit.records(kind="hitl.decision")))
        assert decision.payload["decision"] == "edit"
        assert decision.payload["actor"] == "implementer-a"
        assert decision.payload["changed_keys"] == ["text"]


def test_audit_entry_names_changed_keys_but_not_their_contents():
    """The log says what moved, not what the respondent or reviewer wrote."""
    with AuditLog() as audit, HitlStore(audit=audit) as store:
        iid = store.interrupt("t01", "review", DRAFT)
        store.resume(iid, "respond", actor="implementer-a", edited={"text": "実施者の所見"})
        payload = next(iter(audit.records(kind="hitl.decision"))).payload
        assert "実施者の所見" not in str(payload)
        assert "生成された下書き" not in str(payload)


def test_store_survives_reopening(tmp_path):
    path = tmp_path / "hitl.db"
    with HitlStore(path) as store:
        iid = store.interrupt("t01", "review", DRAFT)
    with HitlStore(path) as store:
        assert store.get(iid).payload == DRAFT
        store.resume(iid, "approve", actor="implementer-a")
    with HitlStore(path) as store, pytest.raises(StateError):
        store.resume(iid, "approve", actor="implementer-a")
