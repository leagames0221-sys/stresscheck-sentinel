"""LLM provider tests.

Nothing here starts a server or contacts one. The point of the host allowlist is
that it refuses before any socket exists, which is testable without a network.
"""

from __future__ import annotations

import pytest

from sentinel.core.errors import DataFileError, LLMProviderError
from sentinel.core.llm import (
    ALLOWED_HOSTS,
    NullProvider,
    OllamaProvider,
    PromptLibrary,
    get_provider,
)


@pytest.fixture
def library(prompts_file):
    return PromptLibrary.load(prompts_file)


# ---------------------------------------------------------------------------
# The default is no LLM at all
# ---------------------------------------------------------------------------


def test_default_provider_is_null(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_provider(), NullProvider)


@pytest.mark.parametrize("value", ["", "null", "NULL", "none", "off"])
def test_null_is_selected_for_the_obvious_spellings(monkeypatch, value):
    monkeypatch.setenv("LLM_PROVIDER", value)
    assert isinstance(get_provider(), NullProvider)


def test_ollama_is_opt_in(monkeypatch, library):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert isinstance(get_provider(library), OllamaProvider)


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(LLMProviderError, match="unknown LLM_PROVIDER"):
        get_provider()


def test_null_provider_returns_the_fallback_text(library):
    output = NullProvider(library).generate("selfcare_general", {})
    assert output.fallback is True
    assert output.provider == "null"
    assert output.model is None
    assert "0120-061-338" in output.text


def test_prompt_without_a_fallback_is_a_configuration_error(library):
    with pytest.raises(LLMProviderError, match="no fallback_text"):
        NullProvider(library).generate("no_fallback", {})


def test_unknown_prompt_id_is_reported(library):
    with pytest.raises(LLMProviderError, match="unknown prompt id"):
        NullProvider(library).generate("does_not_exist", {})


# ---------------------------------------------------------------------------
# Host allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "http://example.com:11434",
        "https://api.openai.com",
        "http://10.0.0.5:11434",
        "http://192.168.1.20:11434",
        "http://ollama.internal:11434",
        "http://127.0.0.1.example.com:11434",
    ],
)
def test_non_loopback_hosts_are_refused(host, library):
    with pytest.raises(LLMProviderError, match="refusing to use LLM host"):
        OllamaProvider(host=host, prompts=library)


@pytest.mark.parametrize("host", ["http://localhost:11434", "http://127.0.0.1:11434"])
def test_loopback_hosts_are_accepted(host, library):
    assert OllamaProvider(host=host, prompts=library).endpoint == f"{host}/api/chat"


def test_the_allowlist_is_loopback_only():
    assert set(ALLOWED_HOSTS) == {"localhost", "127.0.0.1", "::1", "[::1]"}


def test_non_http_scheme_is_refused(library):
    with pytest.raises(LLMProviderError, match="unsupported scheme"):
        OllamaProvider(host="ftp://localhost", prompts=library)


def test_host_with_a_path_is_refused(library):
    with pytest.raises(LLMProviderError, match="without a path"):
        OllamaProvider(host="http://localhost:11434/v1/proxy", prompts=library)


def test_env_configured_remote_host_is_refused(monkeypatch, library):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "http://somewhere-else:11434")
    with pytest.raises(LLMProviderError, match="refusing to use LLM host"):
        get_provider(library)


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_unreachable_ollama_falls_back_to_canned_text(library, monkeypatch):
    import urllib.error

    def refuse(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    provider = OllamaProvider(prompts=library)
    # The transport is the hardened opener (proxy-ignoring, redirect-guarding),
    # not the global urlopen, so the seam the test drives is the opener (F4).
    monkeypatch.setattr(provider._opener, "open", refuse)
    output = provider.generate("selfcare_general", {"b_sum": 92})
    assert output.fallback is True
    assert "0120-061-338" in output.text


def test_a_malformed_response_is_raised_not_hidden(library, monkeypatch):
    class FakeResponse:
        def read(self):
            return b'{"unexpected": "shape"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    provider = OllamaProvider(prompts=library)
    monkeypatch.setattr(provider._opener, "open", lambda *a, **k: FakeResponse())
    with pytest.raises(LLMProviderError, match="unexpected response shape"):
        provider.generate("selfcare_general", {"b_sum": 92})


def test_a_well_formed_response_is_returned(library, monkeypatch):
    class FakeResponse:
        def read(self):
            return '{"message": {"content": "落ち着いて休む時間をとりましょう。"}}'.encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    provider = OllamaProvider(prompts=library)
    monkeypatch.setattr(provider._opener, "open", lambda *a, **k: FakeResponse())
    output = provider.generate("selfcare_general", {"b_sum": 92})
    assert output.fallback is False
    assert output.provider == "ollama"
    assert output.text == "落ち着いて休む時間をとりましょう。"


# ---------------------------------------------------------------------------
# Prompt library
# ---------------------------------------------------------------------------


def test_prompts_load_from_the_yaml_subset(library):
    assert library.ids() == ("no_fallback", "selfcare_general")
    prompt = library.get("selfcare_general")
    assert "診断はしません。" in prompt.system
    assert prompt.required_tokens == ("診断ではありません", "0120-061-338")


def test_variables_are_substituted(library):
    _, user = library.get("selfcare_general").render({"b_sum": 92})
    assert "領域Bの合計は 92 でした。" in user


def test_a_missing_variable_is_an_error_not_a_literal_brace(library):
    with pytest.raises(LLMProviderError, match="needs variable 'b_sum'"):
        library.get("selfcare_general").render({})


def test_a_missing_prompt_file_is_reported(tmp_path):
    with pytest.raises(DataFileError, match="prompt file not found"):
        PromptLibrary.load(tmp_path / "absent.yaml")


def test_a_file_without_a_prompts_mapping_is_rejected(tmp_path):
    path = tmp_path / "prompts.yaml"
    path.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(DataFileError, match="top-level 'prompts:' mapping"):
        PromptLibrary.load(path)


def test_an_empty_library_is_rejected():
    with pytest.raises(DataFileError, match="empty"):
        PromptLibrary({})
