"""HTTP layer tests. A real server on a real loopback socket, driven by urllib.

Nothing is mocked here on purpose: the properties worth testing — that the bind
address cannot be changed, that the policy header is sent, that a token does not
appear in the log line — are properties of the actual socket and the actual
handler, and a fake would assert them about the fake.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from sentinel.app import server as server_module
from sentinel.app.server import BIND_HOST, SentinelHTTPServer, make_server
from sentinel.app.service import SentinelService
from tests.conftest import build_answers

HIGH_STRESS = {"A": 40, "B": 90, "C": 30}
LOW_STRESS = {"A": 30, "B": 40, "C": 30}


@pytest.fixture
def live() -> Iterator[tuple[SentinelHTTPServer, SentinelService]]:
    service = SentinelService(":memory:")
    httpd = make_server(service, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, service
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        service.close()


def get(httpd: SentinelHTTPServer, path: str) -> tuple[int, dict, dict]:
    request = urllib.request.Request(httpd.base_url + path)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
            return response.status, dict(response.headers), _maybe_json(body)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, dict(exc.headers), _maybe_json(exc.read().decode("utf-8"))


def post(httpd: SentinelHTTPServer, path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        httpd.base_url + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


def _maybe_json(body: str):
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_text": body}


# ---------------------------------------------------------------------------
# Binding and headers
# ---------------------------------------------------------------------------


def test_the_server_binds_loopback_only(live):
    httpd, _ = live
    assert httpd.server_address[0] == "127.0.0.1"
    assert BIND_HOST == "127.0.0.1"


def test_the_bind_host_is_not_a_parameter():
    """`make_server` takes a port and nothing else; there is no interface flag."""
    import inspect

    parameters = list(inspect.signature(make_server).parameters)
    assert parameters == ["service", "port"]


def test_every_response_carries_the_self_only_policy(live):
    httpd, _ = live
    _, headers, _ = get(httpd, "/")
    policy = headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"


def test_static_pages_contain_no_external_references(live):
    httpd, _ = live
    for path in ("/", "/result", "/review", "/static/app.css", "/static/form.js"):
        _, _, payload = get(httpd, path)
        body = payload.get("_text", "")
        assert "http://" not in body.replace("http://127.0.0.1", "")
        assert "https://" not in body
        assert "//cdn" not in body


def test_pages_use_no_inline_script_or_style(live):
    """An inline block would need `unsafe-inline`, which would void the policy."""
    httpd, _ = live
    for path in ("/", "/result", "/review"):
        _, _, payload = get(httpd, path)
        body = payload.get("_text", "")
        assert "<script>" not in body
        assert "style=" not in body
        assert "onclick" not in body


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_the_form_page_is_served(live):
    httpd, _ = live
    status, headers, payload = get(httpd, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "ストレスチェック受検" in payload["_text"]


def test_the_respondent_page_carries_the_standing_disclosure(live):
    """R4-3: the disclosure is on the page, not only inside generated text."""
    httpd, _ = live
    for path in ("/", "/result"):
        _, _, payload = get(httpd, path)
        body = payload["_text"]
        assert "AI が生成した文章" in body
        assert "医学的な診断ではありません" in body


def test_items_are_served_for_both_variants(live):
    httpd, _ = live
    for variant, count in (("57", 57), ("23", 23)):
        status, _, payload = get(httpd, f"/api/items?variant={variant}")
        assert status == 200
        assert len(payload["items"]) == count
        assert payload["items"][0]["choices"]


def test_an_unknown_variant_is_a_client_error(live):
    httpd, _ = live
    status, _, payload = get(httpd, "/api/items?variant=80")
    assert status == 400
    assert "error" in payload


def test_an_unknown_path_is_a_404(live):
    httpd, _ = live
    status, _, _ = get(httpd, "/api/nope")
    assert status == 404


def test_static_path_traversal_is_refused(live):
    httpd, _ = live
    status, _, _ = get(httpd, "/static/../service.py")
    assert status in (400, 404)


# ---------------------------------------------------------------------------
# The submission round trip
# ---------------------------------------------------------------------------


def test_a_low_stress_submission_is_readable_immediately(live):
    httpd, _ = live
    status, payload = post(
        httpd,
        "/api/submit",
        {"variant": "57", "answers": build_answers("57", LOW_STRESS), "token_seed": "h01"},
    )
    assert status == 200
    assert payload["high_stress"] is False

    status, _, result = get(httpd, "/api/result?token=" + payload["token"])
    assert status == 200
    assert result["state"] == "released"
    assert "相談窓口" in result["text"]


def test_a_high_stress_submission_is_withheld_then_released(live):
    httpd, _ = live
    _, submitted = post(
        httpd,
        "/api/submit",
        {"variant": "57", "answers": build_answers("57", HIGH_STRESS), "token_seed": "h02"},
    )
    token = submitted["token"]

    _, _, before = get(httpd, "/api/result?token=" + token)
    assert before["state"] == "pending_review"
    assert before["text"] == ""

    _, _, queue = get(httpd, "/api/review/pending")
    interrupt_id = queue["pending"][0]["interrupt_id"]

    status, decided = post(
        httpd,
        "/api/review/decide",
        {"interrupt_id": interrupt_id, "decision": "approve", "actor": "implementer-a"},
    )
    assert status == 200
    assert decided["state"] == "released"

    _, _, after = get(httpd, "/api/result?token=" + token)
    assert after["state"] == "released"
    assert "相談窓口" in after["text"]


def test_a_crisis_submission_returns_the_helplines_in_the_response(live):
    httpd, _ = live
    _, payload = post(
        httpd,
        "/api/submit",
        {
            "variant": "57",
            "answers": build_answers("57", LOW_STRESS),
            "free_text": "もう死にたい",
            "token_seed": "h03",
        },
    )
    assert payload["crisis_level"] == "ideation"
    assert payload["crisis_response"]["hotlines"]
    assert "0120-061-338" in payload["crisis_response"]["text"]


def test_replaying_a_decision_is_a_conflict(live):
    httpd, _ = live
    _, submitted = post(
        httpd,
        "/api/submit",
        {"variant": "57", "answers": build_answers("57", HIGH_STRESS), "token_seed": "h04"},
    )
    _, _, queue = get(httpd, "/api/review/pending")
    interrupt_id = queue["pending"][0]["interrupt_id"]
    body = {"interrupt_id": interrupt_id, "decision": "approve", "actor": "implementer-a"}
    assert post(httpd, "/api/review/decide", body)[0] == 200
    assert post(httpd, "/api/review/decide", body)[0] == 409
    assert submitted["requires_signature"] is True


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_a_result_request_without_a_token_is_refused(live):
    httpd, _ = live
    status, _, _ = get(httpd, "/api/result")
    assert status == 400


def test_an_unknown_token_is_a_404(live):
    httpd, _ = live
    status, _, _ = get(httpd, "/api/result?token=deadbeefdeadbeef")
    assert status == 404


def test_answers_must_be_an_object(live):
    httpd, _ = live
    status, payload = post(httpd, "/api/submit", {"answers": [1, 2, 3]})
    assert status == 400
    assert "answers" in payload["error"]


def test_an_answer_out_of_range_is_refused(live):
    httpd, _ = live
    answers = build_answers("57", LOW_STRESS)
    answers[1] = 9
    status, payload = post(httpd, "/api/submit", {"variant": "57", "answers": answers})
    assert status == 400
    assert "1..4" in payload["error"]


def test_a_body_that_is_not_json_is_refused(live):
    httpd, _ = live
    request = urllib.request.Request(
        httpd.base_url + "/api/submit",
        data=b"not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    caught.value.close()
    assert caught.value.code == 400


def test_an_oversized_body_is_refused(live, monkeypatch):
    """The body must be refused on Content-Length, before it is read.

    The property is "an oversized body is never processed". The server answers
    413 and closes without draining the socket, which is the whole point — a
    server that reads an unbounded body in order to reply politely has already
    lost. That close is observable in two shapes depending on how far the client
    got: usually a 413 response, occasionally the connection being cut from
    under the client mid-write (WinError 10053 on Windows). Both are the refusal.
    Asserting only the first made this test intermittently red for a reason that
    had nothing to do with the behaviour under test.
    """
    httpd, _ = live
    monkeypatch.setattr(server_module, "MAX_BODY_BYTES", 16)
    request = urllib.request.Request(
        httpd.base_url + "/api/submit",
        data=json.dumps({"answers": {"1": 1, "2": 2, "3": 3, "4": 4}}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises((urllib.error.HTTPError, ConnectionError)) as caught:
        urllib.request.urlopen(request, timeout=10)
    if isinstance(caught.value, urllib.error.HTTPError):
        caught.value.close()
        assert caught.value.code == 413

    # Either way, nothing was stored: the submission never reached the service.
    status, _headers, payload = get(httpd, "/api/kpi")
    assert status == 200
    assert payload["submissions"] == 0


# ---------------------------------------------------------------------------
# Diagnostics endpoints
# ---------------------------------------------------------------------------


def test_gate_check_over_http(live):
    httpd, _ = live
    status, payload = post(httpd, "/api/gate-check", {"text": "うつ病の可能性があります。"})
    assert status == 200
    assert payload["ok"] is False
    assert payload["blocked_by"] == "samd_lint"


def test_kpi_over_http(live):
    httpd, _ = live
    status, _, payload = get(httpd, "/api/kpi")
    assert status == 200
    assert payload["total"] == 0
    assert payload["audit_chain_ok"] is True


def test_the_log_line_drops_the_query_string(live, capsys):
    """The token travels as a query parameter and must not reach the log."""
    httpd, _ = live
    _, submitted = post(
        httpd,
        "/api/submit",
        {"variant": "57", "answers": build_answers("57", LOW_STRESS), "token_seed": "h05"},
    )
    get(httpd, "/api/result?token=" + submitted["token"])
    captured = capsys.readouterr()
    assert submitted["token"] not in captured.err
    assert submitted["token"] not in captured.out
