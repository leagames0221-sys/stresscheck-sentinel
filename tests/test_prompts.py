"""`prompts/prompts.yaml` is data, and this is the test that treats it as data.

R4-3: every prompt is version-controlled and carries three mandatory tokens —
that the text was machine-generated, that it is not a medical judgement, and
where to get help. A prompt that loses one of them is a shipped product that
quietly stops disclosing, which is not the kind of regression a code review
catches.

The fallback text is held to exactly the same standard as the model prompt.
With no model installed the fallback *is* the product, so a fallback that omits
the disclosure is not a degraded mode — it is the default mode.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sentinel.core.llm import NullProvider, PromptLibrary
from sentinel.packs.samdlint.lint import lint

PROMPTS_PATH = Path(__file__).resolve().parents[1] / "prompts" / "prompts.yaml"

#: The three disclosures every prompt must carry (R4-3).
MANDATORY_TOKENS: tuple[str, ...] = ("AIが生成", "診断ではありません", "相談窓口")

#: Every prompt id the application relies on. Renaming one without updating the
#: caller would otherwise fail at request time rather than at test time.
REQUIRED_PROMPT_IDS: tuple[str, ...] = ("selfcare_advice", "result_plain_language")

#: A complete variable set. Every prompt must render with exactly these.
SAMPLE_VARIABLES: dict[str, object] = {
    "sum_a": 42,
    "sum_b": 78,
    "sum_c": 25,
    "high_stress_label": "該当",
    "variant": "57",
}


@pytest.fixture(scope="module")
def library() -> PromptLibrary:
    return PromptLibrary.load(PROMPTS_PATH)


def prompt_ids() -> tuple[str, ...]:
    return PromptLibrary.load(PROMPTS_PATH).ids()


def test_the_shipped_prompt_file_exists_and_parses():
    assert PROMPTS_PATH.is_file()
    assert len(PromptLibrary.load(PROMPTS_PATH)) >= 2


def test_the_file_is_utf8():
    PROMPTS_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("prompt_id", REQUIRED_PROMPT_IDS)
def test_required_prompt_ids_are_present(library: PromptLibrary, prompt_id: str):
    assert prompt_id in library


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_every_prompt_has_system_user_and_fallback(library: PromptLibrary, prompt_id: str):
    prompt = library.get(prompt_id)
    assert prompt.system.strip(), f"{prompt_id}: empty system prompt"
    assert prompt.user.strip(), f"{prompt_id}: empty user prompt"
    assert prompt.fallback_text.strip(), f"{prompt_id}: empty fallback_text (R4-2)"


# ---------------------------------------------------------------------------
# The mandatory tokens (R4-3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_every_prompt_declares_the_mandatory_tokens(library: PromptLibrary, prompt_id: str):
    declared = set(library.get(prompt_id).required_tokens)
    missing = [t for t in MANDATORY_TOKENS if t not in declared]
    assert not missing, f"{prompt_id}: required_tokens is missing {missing}"


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_every_declared_token_is_present_in_the_fallback(library: PromptLibrary, prompt_id: str):
    prompt = library.get(prompt_id)
    missing = [t for t in prompt.required_tokens if t not in prompt.fallback_text]
    assert not missing, f"{prompt_id}: fallback_text is missing {missing}"


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_every_prompt_instructs_the_model_to_emit_the_tokens(
    library: PromptLibrary, prompt_id: str
):
    """A token the fallback carries but the prompt never asks for is a token the
    generated path silently drops."""
    system = library.get(prompt_id).system
    missing = [t for t in MANDATORY_TOKENS if t not in system]
    assert not missing, f"{prompt_id}: system prompt never asks for {missing}"


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_every_prompt_forbids_condition_names_and_figures(library: PromptLibrary, prompt_id: str):
    system = library.get(prompt_id).system
    assert "疾病名を書かない" in system, f"{prompt_id}: no instruction against condition names"
    assert "パーセント表示" in system, f"{prompt_id}: no instruction against numeric likelihoods"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_every_prompt_renders_with_the_documented_variable_set(
    library: PromptLibrary, prompt_id: str
):
    system, user = library.get(prompt_id).render(dict(SAMPLE_VARIABLES))
    assert "{" not in user, f"{prompt_id}: an unsubstituted placeholder survived"
    assert system


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_the_fallback_renders_and_keeps_its_tokens(library: PromptLibrary, prompt_id: str):
    output = NullProvider(library).generate(prompt_id, dict(SAMPLE_VARIABLES))
    assert output.fallback is True
    assert output.provider == "null"
    for token in MANDATORY_TOKENS:
        assert token in output.text, f"{prompt_id}: rendered fallback lost {token!r}"
    assert "{" not in output.text


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_the_fallback_substitutes_the_numbers(library: PromptLibrary, prompt_id: str):
    output = NullProvider(library).generate(prompt_id, dict(SAMPLE_VARIABLES))
    assert "78" in output.text


# ---------------------------------------------------------------------------
# R4-4: the fallback must survive the lint that every output passes through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_the_rendered_fallback_survives_the_forbidden_expression_lint(
    library: PromptLibrary, prompt_id: str
):
    output = NullProvider(library).generate(prompt_id, dict(SAMPLE_VARIABLES))
    verdict = lint(output.text)
    assert verdict.ok is True, f"{prompt_id}: fallback blocked by {verdict.reasons}"


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_the_fallback_is_honest_about_not_being_generated(library: PromptLibrary, prompt_id: str):
    """The canned text carries the AI-disclosure token, so it must not read as a
    claim that a model wrote it."""
    text = library.get(prompt_id).fallback_text
    assert "AIが生成した文章ではなく" in text


@pytest.mark.parametrize("prompt_id", prompt_ids())
def test_no_prompt_names_a_helpline_number_of_its_own(library: PromptLibrary, prompt_id: str):
    """Numbers live in data/hotlines_ja.csv. A number copied into a prompt is a
    number that will not be updated when the portal changes."""
    prompt = library.get(prompt_id)
    for field in (prompt.system, prompt.user, prompt.fallback_text):
        assert "0120-" not in field
        assert "0570-" not in field
