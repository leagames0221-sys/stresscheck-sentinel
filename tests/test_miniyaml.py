"""Tests for the restricted YAML reader."""

from __future__ import annotations

import pytest

from sentinel.core import miniyaml
from sentinel.core.errors import DataFileError


def test_flat_mapping():
    assert miniyaml.parse("a: 1\nb: two\n") == {"a": 1, "b": "two"}


def test_nested_mapping():
    text = "outer:\n  inner:\n    leaf: value\n"
    assert miniyaml.parse(text) == {"outer": {"inner": {"leaf": "value"}}}


def test_sequence_of_scalars():
    text = "tokens:\n  - alpha\n  - beta\n"
    assert miniyaml.parse(text) == {"tokens": ["alpha", "beta"]}


def test_literal_block_scalar_keeps_line_breaks():
    text = "body: |\n  first\n  second\n"
    assert miniyaml.parse(text) == {"body": "first\nsecond\n"}


def test_stripping_block_scalar_drops_the_trailing_newline():
    text = "body: |-\n  only line\n"
    assert miniyaml.parse(text) == {"body": "only line"}


def test_block_scalar_preserves_inner_indentation():
    text = "body: |\n  outer\n    indented\n"
    assert miniyaml.parse(text)["body"] == "outer\n  indented\n"


def test_block_scalar_keeps_a_hash_as_content():
    """Inside a block scalar a `#` is text, not a comment."""
    text = "body: |\n  see #いのちSOS\n"
    assert miniyaml.parse(text)["body"] == "see #いのちSOS\n"


def test_full_line_comments_and_blank_lines_are_ignored():
    text = "# leading comment\n\na: 1\n\n# another\nb: 2\n"
    assert miniyaml.parse(text) == {"a": 1, "b": 2}


def test_scalar_types():
    text = "i: 42\nf: 1.5\nt: true\nfa: false\nn: null\ns: plain\nq: 'quoted'\n"
    assert miniyaml.parse(text) == {
        "i": 42,
        "f": 1.5,
        "t": True,
        "fa": False,
        "n": None,
        "s": "plain",
        "q": "quoted",
    }


def test_quoted_number_stays_a_string():
    assert miniyaml.parse('phone: "0120-061-338"\n') == {"phone": "0120-061-338"}


def test_empty_document():
    assert miniyaml.parse("") == {}
    assert miniyaml.parse("# only a comment\n") == {}


def test_key_with_no_value_is_none():
    assert miniyaml.parse("a:\nb: 1\n") == {"a": None, "b": 1}


def test_japanese_keys_and_values_survive():
    assert miniyaml.parse("窓口: いのちSOS\n") == {"窓口": "いのちSOS"}


def test_crlf_is_handled():
    assert miniyaml.parse("a: 1\r\nb: 2\r\n") == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Refusals: better to fail than to guess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a: &anchor value\n",
        "a: *alias\n",
        "a: !!python/object:os.system\n",
        "a: {inline: map}\n",
        "a: [inline, list]\n",
        "a: >\n  folded\n",
    ],
)
def test_unsupported_constructs_raise(text):
    with pytest.raises(DataFileError, match="unsupported YAML construct"):
        miniyaml.parse(text)


def test_tab_indentation_raises():
    with pytest.raises(DataFileError, match="tab indentation"):
        miniyaml.parse("a:\n\tb: 1\n")


def test_duplicate_key_raises():
    with pytest.raises(DataFileError, match="duplicate key"):
        miniyaml.parse("a: 1\na: 2\n")


def test_line_without_a_colon_raises():
    with pytest.raises(DataFileError, match="expected 'key: value'"):
        miniyaml.parse("a: 1\nnonsense\n")


def test_empty_key_raises():
    with pytest.raises(DataFileError, match="empty key"):
        miniyaml.parse(": 1\n")


def test_ragged_indentation_raises():
    with pytest.raises(DataFileError, match="unexpected indentation"):
        miniyaml.parse("outer:\n  a: 1\n   b: 2\n")
