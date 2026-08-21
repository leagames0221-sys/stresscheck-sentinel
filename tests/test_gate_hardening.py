"""Regression tests that pin the gate safety properties in both directions.

Each test asserts a concrete safety property of a gate and is written so a
regression that weakens the property turns it red: a green run is evidence the
property still holds, for both the danger side (an unsafe input is caught) and
the benign side (an ordinary input is not over-blocked).

No real person or organisation appears anywhere. Respondents are tokens like
`F1-1`; reviewers are neutral role strings like `reviewer-x`.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from sentinel.app.server import make_server
from sentinel.app.service import (
    STAGE_CRISIS_REVIEW,
    STAGE_RESULT_REVIEW,
    STATE_PENDING_REVIEW,
    STATE_RELEASED,
    SentinelService,
    derive_token,
)
from sentinel.core.errors import LLMProviderError
from sentinel.core.llm import OllamaProvider, PromptLibrary
from tests.conftest import build_answers

HIGH_STRESS = {"A": 40, "B": 90, "C": 30}
LOW_STRESS = {"A": 30, "B": 40, "C": 30}


def high_answers(omit=()):
    return build_answers("57", HIGH_STRESS, omit=omit)


def low_answers():
    return build_answers("57", LOW_STRESS)


@pytest.fixture
def service() -> Iterator[SentinelService]:
    with SentinelService(":memory:") as svc:
        yield svc


# ===========================================================================
# F1  Signature gate: approving only the crisis review must not release
#     the high-stress result.
# ===========================================================================


def test_f1_approving_only_the_crisis_review_does_not_release_the_result(service):
    submission = service.submit(high_answers(), "57", free_text="もう死にたい", token_seed="F1-1")
    assert submission.crisis_interrupt_id is not None
    assert submission.interrupt_id is not None  # the result_review interrupt

    # A reviewer signs ONLY the crisis escalation, never the result review.
    service.decide(submission.crisis_interrupt_id, "approve", actor="reviewer-x")

    released = service.release(submission.token)
    assert released["state"] == STATE_PENDING_REVIEW
    assert released["gate"] == "signature"
    assert released["gate_ok"] is False
    assert released["text"] == ""
    # The high-stress result and its domain sums are not in the pending payload.
    assert released.get("high_stress") is None
    assert not released.get("sums")


def test_f1_a_crisis_signature_does_not_satisfy_the_result_signature(service):
    submission = service.submit(high_answers(), "57", free_text="もう死にたい", token_seed="F1-2")
    service.decide(submission.crisis_interrupt_id, "approve", actor="reviewer-x")
    # A signature exists for this payload_hash, but only at crisis_review stage.
    assert service.has_signature(submission.payload_hash) is True
    assert service.has_signature(submission.payload_hash, stage=STAGE_CRISIS_REVIEW) is True
    assert service.has_signature(submission.payload_hash, stage=STAGE_RESULT_REVIEW) is False


def test_f1_the_result_is_released_only_after_the_result_review_is_signed(service):
    submission = service.submit(high_answers(), "57", free_text="もう死にたい", token_seed="F1-3")
    service.decide(submission.crisis_interrupt_id, "approve", actor="reviewer-x")
    assert service.release(submission.token)["state"] == STATE_PENDING_REVIEW

    service.decide(submission.interrupt_id, "approve", actor="implementer-a")
    released = service.release(submission.token)
    assert released["state"] == STATE_RELEASED
    assert released["gate_ok"] is True


def test_f1_high_stress_without_a_crisis_still_needs_a_result_signature(service):
    submission = service.submit(high_answers(), "57", token_seed="F1-4")
    assert submission.crisis_interrupt_id is None
    assert service.release(submission.token)["state"] == STATE_PENDING_REVIEW
    service.decide(submission.interrupt_id, "approve", actor="implementer-a")
    assert service.release(submission.token)["state"] == STATE_RELEASED


# ===========================================================================
# F3  Result token must not be computable offline from the issued code alone.
# ===========================================================================


def test_f3_token_is_not_offline_computable_without_the_server_salt(service):
    submission = service.submit(low_answers(), "57", token_seed="EMP0001")
    naive = hashlib.sha256(b"EMP0001").hexdigest()[:16]
    assert submission.token != naive
    assert submission.token == derive_token("EMP0001", salt=service._token_salt)
    assert submission.token != derive_token("EMP0001")


def test_f3_two_servers_derive_different_tokens_for_the_same_seed(monkeypatch):
    monkeypatch.delenv("SENTINEL_TOKEN_SALT", raising=False)
    with SentinelService(":memory:") as a, SentinelService(":memory:") as b:
        token_a = a.submit(low_answers(), "57", token_seed="same-seed").token
        token_b = b.submit(low_answers(), "57", token_seed="same-seed").token
        assert a._token_salt != b._token_salt
        assert token_a != token_b


def test_f3_an_env_salt_is_honoured(monkeypatch):
    monkeypatch.setenv("SENTINEL_TOKEN_SALT", "pinned-operator-salt")
    with SentinelService(":memory:") as svc:
        assert svc._token_salt == "pinned-operator-salt"
        submission = svc.submit(low_answers(), "57", token_seed="z")
        assert submission.token == derive_token("z", salt="pinned-operator-salt")


# ===========================================================================
# F4  LLM provider must ignore proxy env and refuse non-loopback redirects.
# ===========================================================================


@pytest.fixture
def library(prompts_file) -> PromptLibrary:
    return PromptLibrary.load(prompts_file)


def test_f4_provider_opener_ignores_proxy_environment(monkeypatch, library):
    """With a proxy set in the environment, the request must still target
    loopback directly and never the proxy (F4). Proven behaviourally: the host
    the transport actually dials is captured."""
    monkeypatch.setenv("http_proxy", "http://proxy.evil.example:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.evil.example:3128")

    captured: dict[str, str] = {}
    real_init = http.client.HTTPConnection.__init__

    def spy_init(self, host, *args, **kwargs):
        captured["host"] = host
        real_init(self, host, *args, **kwargs)

    monkeypatch.setattr(http.client.HTTPConnection, "__init__", spy_init)

    provider = OllamaProvider(prompts=library)
    # Connection to a local Ollama will usually be refused, which falls back to
    # canned text; either way the dialled host is what matters.
    provider.generate("selfcare_general", {"b_sum": 1})

    dialled = captured.get("host", "")
    assert "proxy.evil.example" not in dialled
    assert dialled.split(":")[0] in ("localhost", "127.0.0.1")


def test_f4_a_redirect_to_a_non_loopback_host_is_refused():
    from sentinel.core.llm import _LoopbackRedirectHandler

    handler = _LoopbackRedirectHandler()
    with pytest.raises(LLMProviderError, match="non-loopback"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://evil.example/exfiltrate")


def test_f4_a_loopback_redirect_is_still_allowed():
    from sentinel.core.llm import _LoopbackRedirectHandler

    handler = _LoopbackRedirectHandler()
    request = urllib.request.Request("http://127.0.0.1:11434/api/chat")
    # A loopback target does not raise; it produces a new Request (or None).
    result = handler.redirect_request(
        request, None, 302, "Found", {}, "http://127.0.0.1:11500/api/chat"
    )
    assert result is None or isinstance(result, urllib.request.Request)


def test_f4_a_tampered_host_is_revalidated_before_connecting(library):
    provider = OllamaProvider(prompts=library)
    # Bypass the constructor's validation, then confirm generate() re-checks.
    provider.host = "http://evil.example:11434"
    with pytest.raises(LLMProviderError, match="refusing to use LLM host"):
        provider.generate("selfcare_general", {"b_sum": 1})


# ===========================================================================
# F5 / F7  HTTP layer: CSRF / Host validation and strict answer typing.
# ===========================================================================


@pytest.fixture
def live() -> Iterator[int]:
    service = SentinelService(":memory:")
    httpd = make_server(service, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        service.close()


def _raw_post(
    port: int,
    path: str,
    body_obj: dict,
    *,
    content_type: str = "application/json",
    host: str | None = None,
    origin: str | None = None,
) -> int:
    """POST with full control over the Host/Origin/Content-Type headers."""
    body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
        conn.putheader("Content-Type", content_type)
        conn.putheader("Content-Length", str(len(body)))
        if origin is not None:
            conn.putheader("Origin", origin)
        conn.endheaders()
        conn.send(body)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def _json_post(port: int, path: str, body_obj: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body_obj, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


def test_f5_a_text_plain_content_type_is_refused(live):
    port = live
    body = {"variant": "57", "answers": build_answers("57", LOW_STRESS), "token_seed": "f5a"}
    assert _raw_post(port, "/api/submit", body, content_type="text/plain") == 415


def test_f5_a_cross_origin_post_is_refused(live):
    port = live
    body = {"variant": "57", "answers": build_answers("57", LOW_STRESS), "token_seed": "f5b"}
    assert _raw_post(port, "/api/submit", body, origin="http://evil.example") == 403


def test_f5_a_foreign_host_header_is_refused(live):
    port = live
    body = {"variant": "57", "answers": build_answers("57", LOW_STRESS), "token_seed": "f5c"}
    assert _raw_post(port, "/api/submit", body, host="evil.example") == 403


def test_f5_a_same_origin_json_post_is_accepted(live):
    port = live
    body = {"variant": "57", "answers": build_answers("57", LOW_STRESS), "token_seed": "f5d"}
    assert _raw_post(port, "/api/submit", body, origin=f"http://127.0.0.1:{port}") == 200


def test_f7_a_float_answer_is_refused(live):
    port = live
    answers = build_answers("57", LOW_STRESS)
    answers[1] = 2.5  # must be rejected, not silently coerced to int(2.5) == 2
    status, payload = _json_post(port, "/api/submit", {"variant": "57", "answers": answers})
    assert status == 400
    assert "integer" in payload["error"]


def test_f7_a_boolean_answer_is_refused(live):
    port = live
    answers = build_answers("57", LOW_STRESS)
    answers[1] = True  # must be rejected, not coerced to int(True) == 1
    status, payload = _json_post(port, "/api/submit", {"variant": "57", "answers": answers})
    assert status == 400
    assert "integer" in payload["error"]


# ===========================================================================
# F6  Adversarial gate corpus: every probe must be caught. Over-detection is the
#     safe direction, so crisis rows assert "not none" rather than an exact stage.
# ===========================================================================

from sentinel.packs.crisis import classify as crisis_mod  # noqa: E402
from sentinel.packs.crisis.classify import classify  # noqa: E402
from sentinel.packs.samdlint import lint as samd_mod  # noqa: E402
from sentinel.packs.samdlint.lint import lint  # noqa: E402

_ZW = "​"

# SaMD probes the lint must block (normalisation + vocabulary variants).
SAMD_MUST_BLOCK = [
    "あなたは鬱病の可能性が高いと考えられます。",  # kanji 鬱病
    "あなたはうつ病かもしれません。",  # かもしれ
    "うつ病の恐れがあります。",  # kanji 恐れ
    "うつ病の傾向が見られます。",  # 傾向
    "う つ 病 の 可 能 性 が 高いです。",  # space injection
    "鬱病のリスクは72パーセントです。",  # kanji + risk
    "You may have depression, about 72% likely.",  # english
    "躁鬱病の疑いがあります。",  # 躁鬱病
    "うつ" + _ZW + "病の可能性が高い。",  # zero-width split
    "うつ病、といってよい状態に近づいています。",  # euphemism
    "心療内科を受診されることをおすすめします。",  # referral reworded
    "ｳﾂ病の可能性が高いです。",  # half-width kana
    "本ツールがあなたを見立てた結果、うつ状態と判定されます。",  # 判定 reworded
]


@pytest.mark.parametrize("text", SAMD_MUST_BLOCK)
def test_f6_samd_probe_corpus_is_blocked(text):
    samd_mod.clear_caches()
    verdict = lint(text)
    assert verdict.ok is False, f"lint let this through: {text!r}"


# Crisis probes the classifier must detect (level != 'none').
CRISIS_MUST_DETECT = [
    "もうしにたいです。",  # hiragana
    "モウシニタイ。",  # katakana
    "死 に た い。",  # space injection
    "mou shinitai desu",  # romaji
    "I want to kill myself.",  # english
    "死" + _ZW + "にたい。",  # zero-width split
    "死にたいとは思わない、と言えば嘘になります。",  # negation abuse
    "死にたいと思ったことはない。でも毎晩そればかり考えています。",  # negation up front
    "もう全部終わりにしたい。",  # euphemism
    "消えてなくなりたい。",  # euphemism
    "明日の朝が来なければいいのに。",  # passive ideation
    "もうきえたい。",  # hiragana
    "ロープはもう買ってあります。",  # preparation reworded
    "家族あての手紙はもう書き終えました。",  # note reworded
    "この世から居なくなりたい。",  # kanji variant
]


@pytest.mark.parametrize("text", CRISIS_MUST_DETECT)
def test_f6_crisis_probe_corpus_is_detected(text):
    crisis_mod.clear_caches()
    assert classify(text).level != "none", f"crisis classifier missed: {text!r}"


def test_f6_a_genuine_negation_is_still_cancelled():
    """Over-detection is acceptable, but a real disclaimer must still cancel."""
    crisis_mod.clear_caches()
    assert classify("死にたいと思ったことはないです。").level == "none"
    assert classify("死にたいとは思わないです。").level == "none"


def test_f6_ordinary_wording_still_passes_the_lint():
    """The new vocabulary must not turn benign notices into blocked ones."""
    samd_mod.clear_caches()
    assert lint("睡眠と休養の時間を確保できているか振り返ってみてください。").ok is True
    assert lint("この結果は医学的な診断ではありません。").ok is True


# ===========================================================================
# G2  Natural-language ideation the classifier must fire on: euphemistic and
#     exhaustion-framed 希死念慮 with none of the explicit 全部/人生 markers.
# ===========================================================================

# Natural-language ideation that must fire (level != 'none').
G2_FIRES = [
    "もう終わりにしたい。",  # natural-language euphemism, no explicit 全部/人生
    "もう疲れた、終わりにしたい。",  # exhaustion + euphemism
    "生きるのがつらい。",  # not covered by 生きているのがつらい (I006)
    "生きているのがつらい。",  # I006, kept as a guard
    "生きるのが苦しい。",  # 苦しい variant
]


@pytest.mark.parametrize("text", G2_FIRES)
def test_g2_natural_language_ideation_is_detected(text):
    crisis_mod.clear_caches()
    assert classify(text).level != "none", f"crisis classifier missed: {text!r}"


# The line the widening must not cross: an ordinary task with 終わりにしたい is
# not a crisis. Over-detection is the safe direction, but these plain endings
# carry none of the ideation prefixes, so they must stay 'none'.
G2_STAYS_NONE = [
    "仕事を終わりにしたい。",
    "会議を終わりにしたい。",
    "電話を終わりにしたい。",
]


@pytest.mark.parametrize("text", G2_STAYS_NONE)
def test_g2_plain_task_endings_are_not_over_detected(text):
    crisis_mod.clear_caches()
    assert classify(text).level == "none", f"false crisis on: {text!r}"


# ===========================================================================
# G3  SaMD assertive / predictive diagnosis, any subject. A diagnosis with
#     「これは」「結果は」「あなたの状態は」 as the subject, or a bare 「うつ病です」,
#     must block regardless of the subject.
# ===========================================================================

# Assertive/predictive diagnoses the lint must block.
G3_BLOCKED = [
    "これはうつ病です。",
    "あなたの状態はうつ病です。",
    "結果はうつ病を示しています。",
    "これはうつ病の始まりです。",
    "あなたはうつ病になるでしょう。",
]


@pytest.mark.parametrize("text", G3_BLOCKED)
def test_g3_assertive_diagnosis_is_blocked_regardless_of_subject(text):
    samd_mod.clear_caches()
    assert lint(text).ok is False, f"lint let a diagnosis through: {text!r}"


# Negation and referral must not be swept up: naming a condition to deny it, or
# to point at a doctor, is exactly what an explanatory notice must stay able to do.
G3_PASSES = [
    "うつ病ではありません。",
    "うつ病かどうかは医師に相談してください。",
]


@pytest.mark.parametrize("text", G3_PASSES)
def test_g3_negation_and_referral_are_not_over_blocked(text):
    samd_mod.clear_caches()
    assert lint(text).ok is True, f"lint over-blocked benign text: {text!r}"


# ===========================================================================
# G1  DNS-rebinding: the Host pin must guard GET (reads) too, so a forged Host
#     on a GET to the review/result/kpi APIs is refused and returns no data.
# ===========================================================================


def _raw_get(port: int, path: str, *, host: str | None = None) -> tuple[int, bytes]:
    """GET with full control over the Host header."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        return response.status, body
    finally:
        conn.close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/review/pending",
        "/api/result?token=whatever",
        "/api/kpi",
        "/api/review/sample",
    ],
)
def test_g1_a_foreign_host_get_to_a_data_api_is_refused(live, path):
    port = live
    status, body = _raw_get(port, path, host="cross-site.example.com")
    assert status == 403, f"leaked via {path}: status={status} body={body[:200]!r}"


def test_g1_a_foreign_host_get_to_a_static_page_is_refused(live):
    port = live
    status, _ = _raw_get(port, "/", host="cross-site.example.com")
    assert status == 403


def test_g1_a_loopback_host_get_is_still_accepted(live):
    port = live
    assert _raw_get(port, "/api/kpi", host=f"127.0.0.1:{port}")[0] == 200
    assert _raw_get(port, "/api/review/pending", host=f"localhost:{port}")[0] == 200
    assert _raw_get(port, "/", host=f"127.0.0.1:{port}")[0] == 200


def test_g1_a_foreign_host_get_does_not_leak_pending_health_data(live):
    """A high-stress crisis submission is queued for review, then a rebinding
    GET to the queue must be refused and must return none of the respondent's
    health data."""
    port = live
    body = {
        "variant": "57",
        "answers": build_answers("57", HIGH_STRESS),
        "free_text": "もう死にたい",
        "token_seed": "g1-leak",
    }
    assert _raw_post(port, "/api/submit", body, origin=f"http://127.0.0.1:{port}") == 200
    status, payload = _raw_get(port, "/api/review/pending", host="cross-site.example.com")
    assert status == 403
    assert b"high_stress" not in payload
    assert b"notice_text" not in payload


# ===========================================================================
# G4  release() must not leak high_stress / sums for states that are not
#     'released'. All non-released paths (rejected / invalid / withheld) omit
#     them uniformly.
# ===========================================================================


def test_g4_a_rejected_release_omits_high_stress_and_sums(service):
    submission = service.submit(high_answers(), "57", token_seed="G4-1")
    service.decide(submission.interrupt_id, "reject", actor="implementer-a")
    released = service.release(submission.token)
    assert released["state"] == "rejected"
    assert "high_stress" not in released
    assert "sums" not in released


def test_g4_an_invalid_release_omits_high_stress_and_sums(service):
    submission = service.submit(high_answers(omit=(1,)), "57", token_seed="G4-2")
    released = service.release(submission.token)
    assert released["state"] == "invalid"
    assert "high_stress" not in released
    assert "sums" not in released


def test_g4_a_released_result_still_carries_high_stress_and_sums(service):
    submission = service.submit(high_answers(), "57", token_seed="G4-3")
    service.decide(submission.interrupt_id, "approve", actor="implementer-a")
    released = service.release(submission.token)
    assert released["state"] == STATE_RELEASED
    assert released["high_stress"] is True
    assert released["sums"]


# ===========================================================================
# H1  Educational use of a condition name is not over-blocked. A SaMD lint
#     must let 疾病の一般教育・説明・受診勧奨・否定 through; it only stops the tool
#     asserting a diagnosis *of the reader*. The assertive copula is scoped to
#     adjacency (dx kind), so a copula on some other word does not drag a
#     condition name into a block.
# ===========================================================================

# Education/explanation that names a condition but asserts nothing about the
# reader. Every one must pass.
H1_EDUCATIONAL_PASSES = [
    "うつ病について学ぶことは大切です。",
    "うつ病は治療可能な病気です。",
    "これはうつ病の相談窓口です。",
    "うつ状態のように感じることは誰にでもあることです。",
    "うつ病やパニック障害は誰でもかかりうる病気です。",
    "適応障害という言葉を最近よく耳にするようになりました。",
]


@pytest.mark.parametrize("text", H1_EDUCATIONAL_PASSES)
def test_h1_educational_use_of_a_condition_name_is_not_over_blocked(text):
    samd_mod.clear_caches()
    assert lint(text).ok is True, f"lint over-blocked education: {text!r}"


# ===========================================================================
# H2  Assertive declaration: 「だ」「である」「に違いない」「と思われ」「を意味し」 glued
#     to a condition name are the same diagnosis as the 「です」 form and must
#     block — without over-blocking the H1 educational cases.
# ===========================================================================

H2_DIAGNOSIS_BLOCKS = [
    "これはうつ病だ",
    "あなたはうつ病だ",
    "うつ病である",
    "うつ病に違いない",
    "うつ状態と思われます",
    "うつ病を意味します。",
    "これはうつ病です。",  # the です copula must stay blocked
    "あなたの状態はうつ病です。",
]


@pytest.mark.parametrize("text", H2_DIAGNOSIS_BLOCKS)
def test_h2_an_assertive_diagnosis_copula_is_blocked(text):
    samd_mod.clear_caches()
    assert lint(text).ok is False, f"lint let a diagnosis through: {text!r}"


# The two must coexist: the same run that blocks 「うつ病だ」 must still pass
# 「治療可能な病気です」. Asserted together so a future edit cannot trade one for
# the other.
def test_h2_block_and_h1_pass_hold_together():
    samd_mod.clear_caches()
    assert lint("あなたはうつ病だ").ok is False
    assert lint("うつ病は治療可能な病気です。").ok is True


# ===========================================================================
# H3  Crisis vocabulary the classifier must catch: a bare 「死ねたら」 (no
#     trailing いい/楽) and the volitional 「終わりにしよう」.
# ===========================================================================

H3_CRISIS_FIRES = [
    "いっそ死ねたら",
    "死ねたらと思う",
    "毎日死ねたらと思っています。",
    "終わりにしよう",
    "もう終わりにしよう",
    "人生を終わりにしよう。",
]


@pytest.mark.parametrize("text", H3_CRISIS_FIRES)
def test_h3_bare_and_volitional_ideation_fires(text):
    crisis_mod.clear_caches()
    assert classify(text).level != "none", f"crisis classifier missed: {text!r}"


# ===========================================================================
# H4  Ending a *thing* (仕事/会議/電話/作業/授業) is not a crisis; ending 人生/自分/命
#     is. Both directions are pinned so the classifier does not drift.
# ===========================================================================

H4_TASK_ENDINGS_STAY_NONE = [
    "仕事を終わりにしよう。",
    "会議を終わりにしよう。",
    "電話を終わりにしよう。",
    "もう電話を終わりにしたい。",  # task ending with an exhaustion framing — still none
    "疲れたので仕事を終わりにしたい。",  # exhaustion prefix, but a task — still none
    "そろそろ終わりにしよう。",  # benign wrap-up, no ideation prefix
]


@pytest.mark.parametrize("text", H4_TASK_ENDINGS_STAY_NONE)
def test_h4_ending_a_task_is_not_over_detected(text):
    crisis_mod.clear_caches()
    assert classify(text).level == "none", f"false crisis on: {text!r}"


H4_LIFE_ENDINGS_FIRE = [
    "人生を終わりにしたい。",
    "自分を終わりにしたい。",
    "もう全部終わりにしよう。",
]


@pytest.mark.parametrize("text", H4_LIFE_ENDINGS_FIRE)
def test_h4_ending_a_life_still_fires(text):
    crisis_mod.clear_caches()
    assert classify(text).level != "none", f"crisis classifier missed: {text!r}"


# ===========================================================================
# H5  A bare assertive copula (だ/である/です) glued to a condition name must not
#     over-block ordinary topic/theme sentences where a continuation particle
#     follows (だから/だけ/だと/だが/である可能性): there the condition is the topic
#     and the copula continues the clause. The *bare* copula is scoped to a
#     clause-final position; the content-bearing dx phrases (に違いない/と思われ/
#     を意味し) fire wherever they attach.
# ===========================================================================

# Continuation / topic sentences where the copula is glued to the condition name
# but a continuation particle follows. Every one must PASS.
H5_CONTINUATION_PASSES = [
    "うつ病だからといって特別扱いする必要はありません。",  # だから
    "問題はうつ病だけではありません。",  # だけ
    "うつ病だと思っていました。",  # だと (personal recollection, not 推量)
    "うつ病だが、しっかり働けています。",  # だが
    "うつ病である可能性は誰にでもあります。",  # である + 可能性 (no claim word)
    "うつ病だという話を同僚から聞きました。",  # だという
    "適応障害だけれど通院しながら働いています。",  # だけれど
]


@pytest.mark.parametrize("text", H5_CONTINUATION_PASSES)
def test_h5_a_continuation_copula_is_not_over_blocked(text):
    samd_mod.clear_caches()
    assert lint(text).ok is True, f"lint over-blocked a topic sentence: {text!r}"


# Clause-final assertive diagnoses that MUST stay blocked: the copula sits at a
# clause end, immediately after the condition name.
H5_CLAUSE_FINAL_BLOCKS = [
    "これはうつ病だ",  # だ, clause end
    "あなたはうつ病だ",  # だ, clause end
    "うつ病である",  # である, clause end
    "これはうつ病です。",  # です, clause end
    "あなたの状態はうつ病だね。",  # だ + sentence-final particle ね
    "これはうつ病だよ。",  # だ + sentence-final particle よ
]


@pytest.mark.parametrize("text", H5_CLAUSE_FINAL_BLOCKS)
def test_h5_a_clause_final_diagnosis_is_still_blocked(text):
    samd_mod.clear_caches()
    assert lint(text).ok is False, f"lint let a clause-final diagnosis through: {text!r}"


# The content-bearing dx phrases must fire even across a bridging copula
# (だと思われ / であると思われ), so 推量-form diagnoses stay blocked.
H5_CONTENT_DX_BLOCKS = [
    "うつ病に違いない",  # に違いない, glued
    "うつ状態と思われます",  # と思われ, glued
    "うつ病を意味します。",  # を意味し, glued
    "うつ病だと思われます。",  # と思われ across a bridging だ
    "うつ病であると思われます。",  # と思われ across a bridging である
]


@pytest.mark.parametrize("text", H5_CONTENT_DX_BLOCKS)
def test_h5_a_content_dx_phrase_still_fires(text):
    samd_mod.clear_caches()
    assert lint(text).ok is False, f"lint let a diagnostic phrase through: {text!r}"


def test_h5_dato_recollection_passes_while_dato_omowa_diagnosis_blocks():
    """The same run that PASSES 「うつ病だと思っていました」 (personal recollection)
    must still BLOCK 「うつ病だと思われます」 (passive 推量 diagnosis). Pinned
    together so a future edit cannot trade one for the other."""
    samd_mod.clear_caches()
    assert lint("うつ病だと思っていました。").ok is True
    assert lint("うつ病だと思われます。").ok is False


def test_h5_the_topic_theme_residual_is_documented_and_accepted():
    """Known, disclosed residual: a clause-final copula on a topic sentence
    (「テーマはうつ病です」) is indistinguishable from a diagnosis at the level of
    an adjacent copula, so it is over-blocked on the safe side. This test pins
    the residual as *intended* — if a future change makes it pass, that is a new
    silent behaviour and the README §5 disclosure must be revisited."""
    samd_mod.clear_caches()
    assert lint("今日のテーマはうつ病です。").ok is False


# ===========================================================================
# H7  Safe-side vocabulary: a claim word 「断定できます」 (asserting clinical
#     definitiveness) and a crisis keyword 「いなくなってしまいたい」, each confirmed
#     to introduce no benign over-block / over-detection. 「断定できます」 also
#     covers 「うつ病だと断定できます」, which the clause-final rule would otherwise
#     let through.
# ===========================================================================

H7_SAMD_BLOCKS = [
    "あなたはうつ病だと断定できます。",  # だと (continuation) + 断定できます claim
    "これはうつ病と断定できます。",
]


@pytest.mark.parametrize("text", H7_SAMD_BLOCKS)
def test_h7_an_assertion_of_clinical_definiteness_is_blocked(text):
    samd_mod.clear_caches()
    assert lint(text).ok is False, f"lint let a definitive claim through: {text!r}"


H7_SAMD_BENIGN_PASSES = [
    "うつ病かどうかは自分では断定できません。",  # 断定できません, negative form
    "この検査結果だけでは何も断定できません。",  # no condition name
]


@pytest.mark.parametrize("text", H7_SAMD_BENIGN_PASSES)
def test_h7_the_definiteness_claim_does_not_over_block(text):
    samd_mod.clear_caches()
    assert lint(text).ok is True, f"lint over-blocked benign text: {text!r}"


def test_h7_iyanakunatte_shimaitai_is_detected():
    crisis_mod.clear_caches()
    assert classify("いなくなってしまいたい。").level != "none"
    assert classify("もういなくなってしまいたい。").level != "none"


H7_CRISIS_BENIGN_STAY_NONE = [
    "今日中に全部終わらせたい。",  # 全部終わらせたい was NOT added (benign work over-detect)
    "仕事を全部終わらせたい。",
]


@pytest.mark.parametrize("text", H7_CRISIS_BENIGN_STAY_NONE)
def test_h7_task_completion_wording_is_not_over_detected(text):
    crisis_mod.clear_caches()
    assert classify(text).level == "none", f"false crisis on: {text!r}"
