"""End to end, in one process: answers in, screen out.

Every other test file checks one component in isolation. This one walks the
whole path — submit, score, gate, queue, review, sign, assemble, lint, display —
and asserts the properties that only exist when the pieces are joined:

* a high-stress result is unreachable until a person signs for it, and the
  refusal comes from the gate rather than from a caller remembering to check;
* the free text the respondent typed is absent from the database, the audit log
  and the reviewer's screen, while its classification is present in all three;
* the audit chain still verifies after the whole run, so the record of who
  signed what is evidence rather than narration.

Two of these run over real HTTP as well, because "the pipeline works" and "the
pipeline works when reached through the server" have been different claims
before.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from sentinel.app.server import SentinelHTTPServer, make_server
from sentinel.app.service import (
    SAFE_FALLBACK_NOTICE,
    STAGE_CRISIS_REVIEW,
    STAGE_RESULT_REVIEW,
    STATE_PENDING_REVIEW,
    STATE_RELEASED,
    SentinelService,
)
from sentinel.core.llm import LLMOutput, LLMProvider
from tests.conftest import build_answers

HIGH_STRESS = {"A": 40, "B": 90, "C": 30}
LOW_STRESS = {"A": 30, "B": 40, "C": 30}

FREE_TEXT = "納期が重なっていて、休みの日も気持ちが休まりません"


@pytest.fixture
def service(tmp_path) -> Iterator[SentinelService]:
    with SentinelService(tmp_path / "sentinel.db") as svc:
        yield svc


def test_the_whole_path_for_a_high_stress_respondent(service):
    # 1. 受検 — the respondent answers, and the score is arithmetic.
    submission = service.submit(
        build_answers("57", HIGH_STRESS), "57", free_text=FREE_TEXT, token_seed="e2e-01"
    )
    assert submission.score.high_stress is True
    assert submission.score.rule_hit == "B_only"
    assert submission.crisis.level == "none"

    # 2. 署名ゲート — the result does not reach the respondent yet, and the
    #    reason is a gate verdict, not a policy in a caller.
    withheld = service.release(submission.token)
    assert withheld["state"] == STATE_PENDING_REVIEW
    assert withheld["text"] == ""
    assert withheld["gate"] == "signature"

    # 3. HITL — the reviewer sees the actual text and edits it.
    queue = service.pending_reviews(STAGE_RESULT_REVIEW)
    assert len(queue) == 1
    assert queue[0]["payload"]["notice_text"] == submission.draft_notice

    edited = (
        "実施者が確認しました。面談をご希望の場合はご連絡ください。医学的な診断ではありません。"
    )
    service.decide(
        queue[0]["interrupt_id"], "edit", "implementer-a", edited={"notice_text": edited}
    )

    # 4. 表示 — released, through the chain, carrying the reviewer's wording.
    released = service.release(submission.token)
    assert released["state"] == STATE_RELEASED
    assert released["gate_ok"] is True
    assert released["text"] == edited
    assert released["source"] == "implementer"

    # 5. 監査 — the signature is in the log, bound to this data, and the chain
    #    still verifies.
    signatures = [record.payload for record in service.audit.records(kind="signature")]
    assert len(signatures) == 1
    assert signatures[0]["payload_hash"] == submission.payload_hash
    assert signatures[0]["decision"] == "edit"
    assert service.audit.verify_chain() is True

    # 6. KPI — one decision, and it was an override.
    kpi = service.kpi()
    assert kpi["total"] == 1
    assert kpi["override_rate"] == 1.0
    assert kpi["signatures"] == 1


def test_the_free_text_leaves_no_trace_anywhere_in_the_run(service):
    submission = service.submit(
        build_answers("57", HIGH_STRESS), "57", free_text=FREE_TEXT, token_seed="e2e-02"
    )
    service.decide(submission.interrupt_id, "approve", "implementer-a")
    released = service.release(submission.token)

    surfaces = {
        "stored record": json.dumps(service._load(submission.token), ensure_ascii=False),
        "audit log": json.dumps([r.payload for r in service.audit.records()], ensure_ascii=False),
        "review queue": json.dumps(service.pending_reviews(), ensure_ascii=False),
        "released screen": json.dumps(released, ensure_ascii=False),
        "database file": service.db_path,
    }
    for name, surface in surfaces.items():
        assert FREE_TEXT not in surface, f"free text survived in {name}"
        assert "納期" not in surface, f"free text survived in {name}"

    # Not only the accessors: the bytes on disk.
    raw = Path(service.db_path).read_bytes().decode("utf-8", errors="ignore")
    assert FREE_TEXT not in raw

    # ... while the classification of it is on the record.
    assert submission.crisis.text_sha256 in surfaces["stored record"]


def test_a_crisis_short_circuits_generation_and_still_queues_a_human(service):
    class Exploding(LLMProvider):
        name = "exploding"

        def generate(self, prompt_id, variables):
            raise AssertionError("a crisis must not reach a text generator")

    with SentinelService(":memory:", provider=Exploding()) as svc:
        submission = svc.submit(
            build_answers("57", HIGH_STRESS),
            "57",
            free_text="もう死にたい。遺書も書いた。",
            token_seed="e2e-03",
        )
        assert submission.crisis.level == "prepared"
        assert submission.draft_source == "fixed_text"

        # The respondent gets the helplines immediately, even though the result
        # itself is still waiting for a signature.
        released = svc.release(submission.token)
        assert released["state"] == STATE_PENDING_REVIEW
        assert released["text"] == ""
        assert "0120-061-338" in released["crisis_response"]["text"]
        assert released["crisis_response"]["gate_ok"] is True

        # And a person is asked to look at it.
        stages = {item["stage"] for item in svc.pending_reviews()}
        assert stages == {STAGE_RESULT_REVIEW, STAGE_CRISIS_REVIEW}


def test_a_model_that_breaks_the_rules_cannot_reach_the_screen(tmp_path):
    """R4-4 end to end: generation, review, and lint, in that order."""

    class Rulebreaker(LLMProvider):
        name = "rulebreaker"

        def generate(self, prompt_id, variables):
            return LLMOutput(
                text="うつ病を発症する可能性が 62% あります。受診が必要です。",
                provider=self.name,
                prompt_id=prompt_id,
                model="test",
                fallback=False,
            )

    with SentinelService(tmp_path / "s.db", provider=Rulebreaker()) as svc:
        submission = svc.submit(build_answers("57", HIGH_STRESS), "57", token_seed="e2e-04")
        # The reviewer is shown the draft — the lint runs on the way out, not on
        # the way in, so a human sees what the model actually produced.
        assert "62%" in submission.draft_notice
        svc.decide(submission.interrupt_id, "approve", "implementer-a")

        released = svc.release(submission.token)
        assert released["gate_ok"] is False
        assert released["gate"] == "samd_lint"
        assert released["text"] == SAFE_FALLBACK_NOTICE
        assert "62%" not in released["text"]
        assert "うつ病" not in released["text"]
        assert svc.kpi()["notice_blocked"] == 1


def test_the_audit_chain_survives_a_full_run_of_mixed_decisions(service):
    decisions = ["approve", "edit", "reject", "respond"]
    for index, decision in enumerate(decisions):
        submission = service.submit(
            build_answers("57", HIGH_STRESS), "57", token_seed=f"e2e-mix-{index}"
        )
        service.decide(
            submission.interrupt_id,
            decision,
            f"implementer-{index}",
            edited=(
                {"notice_text": "実施者の所見です。医学的な診断ではありません。"}
                if decision in ("edit", "respond")
                else None
            ),
        )

    assert service.audit.verify_chain() is True
    kpi = service.kpi()
    assert kpi["total"] == 4
    assert kpi["override_rate"] == 0.75
    assert kpi["signatures"] == 3  # reject signs nothing


# ---------------------------------------------------------------------------
# The same path, over the wire
# ---------------------------------------------------------------------------


@pytest.fixture
def live(tmp_path) -> Iterator[SentinelHTTPServer]:
    service = SentinelService(tmp_path / "http.db")
    httpd = make_server(service, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        service.close()


def _get(httpd: SentinelHTTPServer, path: str) -> dict:
    with urllib.request.urlopen(httpd.base_url + path, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(httpd: SentinelHTTPServer, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        httpd.base_url + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_the_whole_path_over_http(live):
    submitted = _post(
        live,
        "/api/submit",
        {
            "variant": "57",
            "answers": build_answers("57", HIGH_STRESS),
            "free_text": FREE_TEXT,
            "token_seed": "e2e-http",
        },
    )
    token = submitted["token"]
    assert submitted["high_stress"] is True
    assert submitted["requires_signature"] is True

    withheld = _get(live, "/api/result?token=" + token)
    assert withheld["state"] == "pending_review"
    assert withheld["text"] == ""

    queue = _get(live, "/api/review/pending")["pending"]
    assert len(queue) == 1
    decided = _post(
        live,
        "/api/review/decide",
        {
            "interrupt_id": queue[0]["interrupt_id"],
            "decision": "approve",
            "actor": "implementer-a",
        },
    )
    assert decided["state"] == "released"

    released = _get(live, "/api/result?token=" + token)
    assert released["state"] == "released"
    assert released["gate_ok"] is True
    assert "診断ではありません" in released["text"]
    assert "相談窓口" in released["text"]
    assert FREE_TEXT not in json.dumps(released, ensure_ascii=False)

    kpi = _get(live, "/api/kpi")
    assert kpi["total"] == 1
    assert kpi["signatures"] == 1
    assert kpi["audit_chain_ok"] is True


def test_a_low_stress_run_over_http_needs_no_review(live):
    submitted = _post(
        live,
        "/api/submit",
        {
            "variant": "23",
            "answers": build_answers("23", {"A": 20, "B": 20, "C": 20}),
            "token_seed": "e2e-http-low",
        },
    )
    assert submitted["high_stress"] is False
    released = _get(live, "/api/result?token=" + submitted["token"])
    assert released["state"] == "released"
    assert released["gate_ok"] is True
    assert _get(live, "/api/review/pending")["pending"] == []
