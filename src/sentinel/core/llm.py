"""LLM providers. Local-only, optional, and off by default.

The default provider generates nothing. `NullProvider` returns the fallback text
that every prompt is required to carry, which means the whole product works with
no model installed — and, more usefully, means the fallback path is the one
exercised on every developer machine and in CI, rather than the untested branch
you discover is broken the day Ollama is down.

`OllamaProvider` talks to a local Ollama over `urllib`. The host allowlist is
enforced in the constructor, not by convention and not by a comment: a
non-loopback host raises before any request is made. There is no code path in
this file that can reach a remote endpoint, which is a property you can check by
reading the file rather than by trusting the deployment.

Prompts live in `prompts/prompts.yaml`, under version control, and are looked up
by id. Nothing in this module composes prompt text.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sentinel.core import miniyaml
from sentinel.core.errors import DataFileError, LLMProviderError

#: The only hosts a provider may talk to. Loopback only, by design.
ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _is_loopback(host: str) -> bool:
    return host.lower() in ALLOWED_HOSTS


class _LoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow a redirect whose target is not a loopback host.

    Without this, a local process answering on 11434 could 302 the client to an
    external URL and the default handler would follow it, carrying the request
    body off the machine (F4). Loopback-only is re-checked on the redirect
    target, not just on the original host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _is_loopback(urlsplit(newurl).hostname or ""):
            raise LLMProviderError(
                f"refusing to follow a redirect to a non-loopback host: {newurl!r}."
                " This tool is designed so that no respondent text can leave the machine."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener() -> urllib.request.OpenerDirector:
    """An opener that ignores proxy environment variables and guards redirects.

    `ProxyHandler({})` is an *empty* proxy map, which disables the default
    behaviour of honouring `http_proxy`/`https_proxy` — otherwise a proxy set in
    the environment would become an egress path the host allowlist never sees
    (F4).
    """
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _LoopbackRedirectHandler(),
    )


DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:4b"
DEFAULT_TIMEOUT_SECONDS = 60.0

_DEFAULT_PROMPTS_PATH = Path(__file__).resolve().parents[3] / "prompts" / "prompts.yaml"


@dataclass(frozen=True)
class LLMOutput:
    """One generation result."""

    text: str
    provider: str
    prompt_id: str
    model: str | None = None
    fallback: bool = False


@dataclass(frozen=True)
class Prompt:
    """One versioned prompt."""

    id: str
    system: str = ""
    user: str = ""
    fallback_text: str = ""
    required_tokens: tuple[str, ...] = field(default_factory=tuple)

    def render(self, variables: dict[str, Any]) -> tuple[str, str]:
        """Substitute `{name}` placeholders into the system and user text.

        `str.format_map` with a strict mapping, so a missing variable is an
        error rather than a prompt that silently ships the literal `{name}` to a
        model.
        """
        return _format(self.system, variables, self.id), _format(self.user, variables, self.id)


def _format(template: str, variables: dict[str, Any], prompt_id: str) -> str:
    try:
        return template.format_map(_StrictDict(variables))
    except KeyError as exc:
        raise LLMProviderError(
            f"prompt {prompt_id!r} needs variable {exc.args[0]!r}, which was not supplied"
        ) from exc


class _StrictDict(dict):
    def __missing__(self, key: str) -> Any:
        raise KeyError(key)


class PromptLibrary:
    """The contents of `prompts.yaml`, keyed by prompt id."""

    def __init__(self, prompts: dict[str, Prompt], source: str = "") -> None:
        if not prompts:
            raise DataFileError("prompt library is empty")
        self._prompts = prompts
        self.source = source

    @classmethod
    def load(cls, path: Path | str | None = None) -> PromptLibrary:
        target = Path(path) if path is not None else _DEFAULT_PROMPTS_PATH
        if not target.is_file():
            raise DataFileError(
                f"prompt file not found: {target}."
                " Prompts are version-controlled data, not literals in code;"
                " point at one with LLM_PROMPTS_PATH or create the file."
            )
        parsed = miniyaml.parse(target.read_text(encoding="utf-8"))
        raw = parsed.get("prompts")
        if not isinstance(raw, dict):
            raise DataFileError(f"{target}: expected a top-level 'prompts:' mapping")

        prompts: dict[str, Prompt] = {}
        for prompt_id, body in raw.items():
            if not isinstance(body, dict):
                raise DataFileError(f"{target}: prompt {prompt_id!r} is not a mapping")
            tokens = body.get("required_tokens") or []
            if isinstance(tokens, str):
                tokens = [tokens]
            prompts[prompt_id] = Prompt(
                id=prompt_id,
                system=str(body.get("system") or ""),
                user=str(body.get("user") or ""),
                fallback_text=str(body.get("fallback_text") or ""),
                required_tokens=tuple(str(t) for t in tokens),
            )
        return cls(prompts, source=str(target))

    def __contains__(self, prompt_id: object) -> bool:
        return prompt_id in self._prompts

    def __len__(self) -> int:
        return len(self._prompts)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._prompts))

    def get(self, prompt_id: str) -> Prompt:
        try:
            return self._prompts[prompt_id]
        except KeyError as exc:
            raise LLMProviderError(
                f"unknown prompt id {prompt_id!r}; known ids: {self.ids()}"
            ) from exc


class LLMProvider(ABC):
    """Base class for a text generator."""

    name: str = ""

    def __init__(self, prompts: PromptLibrary | None = None) -> None:
        self._prompts = prompts

    @property
    def prompts(self) -> PromptLibrary:
        if self._prompts is None:
            self._prompts = PromptLibrary.load(os.environ.get("LLM_PROMPTS_PATH") or None)
        return self._prompts

    @abstractmethod
    def generate(self, prompt_id: str, variables: dict[str, Any]) -> LLMOutput:
        """Produce text for a prompt id."""

    def _fallback(self, prompt_id: str, variables: dict[str, Any]) -> LLMOutput:
        prompt = self.prompts.get(prompt_id)
        if not prompt.fallback_text:
            raise LLMProviderError(
                f"prompt {prompt_id!r} has no fallback_text."
                " Every prompt needs one: the product is required to work with no"
                " model installed, so a prompt without a fallback is a prompt that"
                " breaks the tool for anyone who has not set up Ollama."
            )
        return LLMOutput(
            text=_format(prompt.fallback_text, variables, prompt_id),
            provider=self.name,
            prompt_id=prompt_id,
            model=None,
            fallback=True,
        )


class NullProvider(LLMProvider):
    """The default. Returns each prompt's canned fallback text, never a model."""

    name = "null"

    def generate(self, prompt_id: str, variables: dict[str, Any]) -> LLMOutput:
        return self._fallback(prompt_id, variables)


class OllamaProvider(LLMProvider):
    """Local Ollama over HTTP. Loopback hosts only."""

    name = "ollama"

    def __init__(
        self,
        host: str = DEFAULT_OLLAMA_HOST,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        prompts: PromptLibrary | None = None,
    ) -> None:
        super().__init__(prompts)
        self.host = _validate_host(host)
        self.model = model
        self.timeout = timeout
        #: Ignores proxy env vars and refuses non-loopback redirects (F4).
        self._opener = _build_opener()

    @property
    def endpoint(self) -> str:
        return f"{self.host}/api/chat"

    def generate(self, prompt_id: str, variables: dict[str, Any]) -> LLMOutput:
        """Generate, falling back to canned text if Ollama is not reachable.

        Only connection-level failures fall back. A malformed response is raised,
        because that means the model or the API changed and quietly serving
        canned text would hide it.
        """
        # Re-validate the destination immediately before connecting (F4). The
        # constructor validated `self.host`, but this re-check means a host
        # mutated after construction cannot reach a request.
        _validate_host(self.host)

        prompt = self.prompts.get(prompt_id)
        system, user = prompt.render(variables)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body = json.dumps(
            {"model": self.model, "messages": messages, "stream": False},
            ensure_ascii=False,
        ).encode("utf-8")

        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError):
            return self._fallback(prompt_id, variables)

        try:
            payload = json.loads(raw)
            text = payload["message"]["content"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LLMProviderError(
                f"unexpected response shape from {self.endpoint}: {raw[:200]!r}"
            ) from exc

        return LLMOutput(
            text=str(text),
            provider=self.name,
            prompt_id=prompt_id,
            model=self.model,
            fallback=False,
        )


def _validate_host(host: str) -> str:
    """Reject any host that is not loopback, before a request can be made."""
    parts = urlsplit(host)
    if parts.scheme not in ("http", "https"):
        raise LLMProviderError(f"unsupported scheme in LLM host {host!r}: {parts.scheme!r}")
    hostname = parts.hostname or ""
    if hostname.lower() not in ALLOWED_HOSTS:
        raise LLMProviderError(
            f"refusing to use LLM host {host!r}. This tool handles occupational"
            " health responses and is designed so that no respondent text can"
            f" leave the machine; only {sorted(ALLOWED_HOSTS)} are permitted."
        )
    if parts.path.rstrip("/"):
        raise LLMProviderError(f"LLM host must be an origin without a path, got {host!r}")
    return f"{parts.scheme}://{parts.netloc}"


def get_provider(prompts: PromptLibrary | None = None) -> LLMProvider:
    """Return the configured provider.

    `LLM_PROVIDER` unset or `"null"` gives `NullProvider`; `"ollama"` gives
    `OllamaProvider`. Defaulting to no-LLM means that forgetting to configure
    this produces canned text, not an unreviewed generation.
    """
    choice = (os.environ.get("LLM_PROVIDER") or "null").strip().lower()
    if choice in ("", "null", "none", "off"):
        return NullProvider(prompts)
    if choice == "ollama":
        return OllamaProvider(
            host=os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST,
            model=os.environ.get("OLLAMA_MODEL") or DEFAULT_MODEL,
            prompts=prompts,
        )
    raise LLMProviderError(f"unknown LLM_PROVIDER {choice!r}; expected 'null' or 'ollama'")
