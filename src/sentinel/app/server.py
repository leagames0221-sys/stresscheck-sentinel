"""A stdlib HTTP face for `service`. Loopback only, no framework, no CDN.

The binding address is a constant, not a parameter. A stress-check server that
can be told to listen on `0.0.0.0` is one flag away from publishing occupational
health data to a network, and there is no use for that flag here: the deployment
model is one machine, one workplace, no egress.

The Content-Security-Policy is `default-src 'self'`, which turns "no external
fonts, no CDN scripts, works offline" from a promise in a README into something
the browser enforces. That is also why there is no inline `<script>` or
`style=` anywhere in `static/` — an inline block would need `unsafe-inline`,
and then the policy would be decorative.

Authentication is deliberately absent, and that is a v1 limitation rather than
an oversight: the reviewer screen is protected by the same thing as the rest of
the server, which is that it is reachable only from the machine it runs on.
Anything beyond that needs a real identity story, and a half-built one would be
worse than the honest absence.
"""

from __future__ import annotations

import json
import mimetypes
import sys
import traceback
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sentinel.app.service import SentinelService, SubmissionNotFound
from sentinel.core.errors import SentinelError, StateError
from sentinel.packs.crisis.response import load_hotlines
from sentinel.packs.jsq.scoring import load_items

#: Not configurable, on purpose. See the module docstring.
BIND_HOST = "127.0.0.1"

DEFAULT_PORT = 8765

#: Enough for 57 answers and a paragraph of free text, and small enough that an
#: unbounded body cannot be used to exhaust memory.
MAX_BODY_BYTES = 256 * 1024

STATIC_DIR = Path(__file__).resolve().parent / "static"

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

PAGES: dict[str, str] = {
    "/": "index.html",
    "/index.html": "index.html",
    "/result": "result.html",
    "/result.html": "result.html",
    "/review": "review.html",
    "/review.html": "review.html",
}

#: State-changing POSTs must arrive as JSON. A cross-site HTML form can only send
#: text/plain, multipart or urlencoded bodies, so requiring JSON is what rules a
#: forged form POST out (F5).
JSON_CONTENT_TYPE = "application/json"


def _origin_of(url: str) -> str:
    """The scheme://authority of a URL, or '' if it has no origin.

    Used to compare an `Origin`/`Referer` header against this server's own
    origin. A `Referer` is a full URL; an `Origin` is already just the origin;
    both reduce to the same thing here.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


class SentinelHTTPServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer` carrying the service the handlers talk to."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, service: SentinelService) -> None:
        self.service = service
        super().__init__((BIND_HOST, port), SentinelRequestHandler)

    @property
    def base_url(self) -> str:
        return f"http://{BIND_HOST}:{self.server_address[1]}"


class SentinelRequestHandler(BaseHTTPRequestHandler):
    """Routes. Every response goes out through `_send_json` or `_send_file`."""

    server_version = "sentinel"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def service(self) -> SentinelService:
        server: Any = self.server
        return server.service

    # -- plumbing -----------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        """Log the path without its query string.

        The respondent token travels as a query parameter, and a log line is a
        place data goes to be forgotten about. Stripping it here means the token
        is not written to disk by the web layer at all.
        """
        first = str(args[0]) if args else ""
        sys.stderr.write(f"{self.address_string()} {first.split('?')[0]}\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any] | list[Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_file(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or not target.is_relative_to(STATIC_DIR.resolve()):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        content_type, _ = mimetypes.guess_type(target.name)
        charset = "; charset=utf-8" if target.suffix in (".html", ".css", ".js") else ""
        self._send(200, target.read_bytes(), f"{content_type or 'text/plain'}{charset}")

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return None
        if length <= 0:
            self._error(HTTPStatus.BAD_REQUEST, "empty request body")
            return None
        if length > MAX_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "body is not valid JSON")
            return None
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
            return None
        return payload

    # -- routing ------------------------------------------------------------

    # Method names follow BaseHTTPRequestHandler's dispatch convention.
    def do_GET(self) -> None:
        # DNS-rebinding defence is method-independent (G1). A GET to
        # /api/review/pending or /api/result returns the same respondent health
        # data a forged POST never could, so the Host pin guards reads as well
        # as writes — static pages and GET APIs alike, before any routing.
        if self._reject_bad_host():
            return

        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)

        if path in PAGES:
            self._send_file(PAGES[path])
            return
        if path.startswith("/static/"):
            self._send_file(path[len("/static/") :])
            return

        routes: dict[str, Callable[[dict[str, list[str]]], None]] = {
            "/api/items": self._get_items,
            "/api/hotlines": self._get_hotlines,
            "/api/result": self._get_result,
            "/api/review/pending": self._get_pending,
            "/api/review/sample": self._get_sample,
            "/api/kpi": self._get_kpi,
        }
        handler = routes.get(path)
        if handler is None:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        self._guard(lambda: handler(query))

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        routes: dict[str, Callable[[dict[str, Any]], None]] = {
            "/api/submit": self._post_submit,
            "/api/review/decide": self._post_decide,
            "/api/gate-check": self._post_gate_check,
        }
        handler = routes.get(path)
        if handler is None:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if self._reject_bad_host():
            return
        if self._reject_cross_site():
            return
        payload = self._read_json()
        if payload is None:
            return
        self._guard(lambda: handler(payload))

    def _expected_authorities(self) -> set[str]:
        """The loopback `host:port` authorities this server answers to.

        `127.0.0.1` and `localhost` on the port we are actually listening on,
        and nothing else. Shared by the Host pin (`_reject_bad_host`) and the
        Origin/Referer check (`_reject_cross_site`) so the two cannot drift.
        """
        server: Any = self.server
        port = server.server_address[1]
        return {f"{host}:{port}" for host in ("127.0.0.1", "localhost")}

    def _reject_bad_host(self) -> bool:
        """Refuse any request whose Host is not loopback on our port (F5, G1).

        DNS rebinding does not care about the HTTP method: a name that resolves
        to 127.0.0.1 arrives with that name in the Host header on a GET exactly
        as on a POST. Because a GET to the review or result APIs returns the
        respondent health data a forged POST never could, this pin is applied to
        *every* request — do_GET (static pages included) and do_POST alike —
        rather than only to the state-changing POSTs.

        Returns True (having already sent the 403) when the Host is unexpected.
        """
        host_header = (self.headers.get("Host") or "").strip().lower()
        if host_header not in self._expected_authorities():
            self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return True
        return False

    def _reject_cross_site(self) -> bool:
        """Refuse a state-changing POST a page on another origin could forge (F5).

        The Host pin is enforced for every method by `_reject_bad_host`; this
        method carries the two checks that apply only to a state-changing POST:

        * **Content-Type must be JSON.** A cross-site `<form>` cannot set it to
          `application/json`, so this alone stops the classic CSRF form post.
        * **Origin/Referer, if present, must be our own origin.** The browser
          attaches `Origin` on a cross-origin POST; a mismatch is a forgery. An
          absent header (a non-browser client, or a same-origin navigation that
          sent none) is allowed — the Content-Type and Host checks still stand.

        Returns True (having already sent the error) when the request is unsafe.
        """
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != JSON_CONTENT_TYPE:
            self._error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "state-changing requests must be application/json",
            )
            return True

        origins = {f"http://{authority}" for authority in self._expected_authorities()}
        for header in ("Origin", "Referer"):
            value = self.headers.get(header)
            if value and _origin_of(value) not in origins:
                self._error(HTTPStatus.FORBIDDEN, f"cross-origin {header} refused")
                return True

        return False

    def _guard(self, action: Callable[[], None]) -> None:
        """Turn a deliberate refusal into a 4xx and anything else into a 500.

        A `SentinelError` means the tool declined; the caller should see why. An
        unexpected exception means a bug, and its text does not go to the client.
        """
        try:
            action()
        except SubmissionNotFound as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
        except StateError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (SentinelError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            # The boundary. The traceback goes to the operator's terminal; the
            # client gets three words. Swallowing it entirely would make a bug
            # here indistinguishable from a refusal, which is how a broken gate
            # gets mistaken for a working one.
            self.log_error("unhandled error while serving %s", self.path.split("?")[0])
            sys.stderr.write(traceback.format_exc())
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    # -- handlers -----------------------------------------------------------

    def _get_items(self, query: dict[str, list[str]]) -> None:
        variant = (query.get("variant") or ["57"])[0]
        items = load_items(variant)
        self._send_json(
            {
                "variant": variant,
                "items": [
                    {
                        "item_no": item.item_no,
                        "domain": item.domain,
                        "scale": item.scale,
                        "context": item.context,
                        "text": item.text,
                        "choices": list(item.choices),
                        "scored": item.scored,
                    }
                    for item in items
                ],
            }
        )

    def _get_hotlines(self, _query: dict[str, list[str]]) -> None:
        self._send_json({"hotlines": [h.as_dict() for h in load_hotlines()]})

    def _get_result(self, query: dict[str, list[str]]) -> None:
        token = (query.get("token") or [""])[0]
        if not token:
            self._error(HTTPStatus.BAD_REQUEST, "token is required")
            return
        self._send_json(self.service.release(token))

    def _get_pending(self, query: dict[str, list[str]]) -> None:
        stage = (query.get("stage") or [""])[0] or None
        self._send_json({"pending": self.service.pending_reviews(stage)})

    def _get_sample(self, query: dict[str, list[str]]) -> None:
        try:
            n = int((query.get("n") or ["5"])[0])
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "n must be an integer")
            return
        seed = (query.get("seed") or [""])[0]
        self._send_json({"sample": self.service.sample_for_audit(n, seed)})

    def _get_kpi(self, _query: dict[str, list[str]]) -> None:
        self._send_json(self.service.kpi())

    def _post_submit(self, payload: dict[str, Any]) -> None:
        raw = payload.get("answers")
        if not isinstance(raw, dict):
            self._error(HTTPStatus.BAD_REQUEST, "answers must be an object of item_no -> 1..4")
            return
        answers: dict[int, int] = {}
        for key, value in raw.items():
            # `int(value)` would quietly coerce `true` -> 1 and `2.5` -> 2,
            # slipping a non-answer past the 1..4 range check in `score()` (F7).
            # A JSON boolean is an `int` subclass, so it is excluded explicitly.
            if isinstance(value, bool) or not isinstance(value, int):
                self._error(HTTPStatus.BAD_REQUEST, "each answer must be an integer 1..4")
                return
            try:
                item_no = int(key)
            except (TypeError, ValueError):
                self._error(HTTPStatus.BAD_REQUEST, "answer keys must be integers")
                return
            answers[item_no] = value

        submission = self.service.submit(
            answers=answers,
            variant=str(payload.get("variant") or "57"),
            free_text=str(payload.get("free_text") or ""),
            token_seed=(str(payload["token_seed"]) if payload.get("token_seed") else None),
        )
        self._send_json(
            {
                "token": submission.token,
                "state": submission.state,
                "high_stress": submission.score.high_stress,
                "valid": submission.score.valid,
                "missing": list(submission.score.missing),
                "crisis_level": submission.crisis.level,
                "crisis_response": submission.crisis_response,
                "requires_signature": submission.requires_signature,
            }
        )

    def _post_decide(self, payload: dict[str, Any]) -> None:
        interrupt_id = str(payload.get("interrupt_id") or "")
        decision = str(payload.get("decision") or "")
        actor = str(payload.get("actor") or "")
        if not interrupt_id:
            self._error(HTTPStatus.BAD_REQUEST, "interrupt_id is required")
            return
        edited_text = payload.get("notice_text")
        edited = {"notice_text": str(edited_text)} if edited_text else None
        self._send_json(
            self.service.decide(
                interrupt_id=interrupt_id,
                decision=decision,
                actor=actor,
                edited=edited,
                note=str(payload.get("note") or ""),
            )
        )

    def _post_gate_check(self, payload: dict[str, Any]) -> None:
        self._send_json(self.service.gate_check(str(payload.get("text") or "")))


def make_server(service: SentinelService, port: int = DEFAULT_PORT) -> SentinelHTTPServer:
    """Build a server bound to loopback. `port=0` picks a free one."""
    return SentinelHTTPServer(port, service)


def serve(service: SentinelService, port: int = DEFAULT_PORT) -> None:
    """Run until interrupted."""
    httpd = make_server(service, port)
    print(f"sentinel: listening on {httpd.base_url} (loopback only)", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
