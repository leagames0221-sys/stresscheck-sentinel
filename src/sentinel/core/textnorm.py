"""Deterministic normalisation of the *judgment copy* of a piece of text.

The respondent's original text is never mutated, stored, or shown from here.
These functions build a throwaway copy used only to decide whether a rule
matches, so that the cheap evasions — a space between every character, a
zero-width joiner inside a word, a half-width or compatibility variant of a
character — cannot walk a signal past a gate that a human reader would have
caught at a glance.

Three transforms, each with a reason:

* **NFKC** folds full-width/half-width and other compatibility forms onto one
  representative, so 「７２％」 and 「72%」, or half-width ｶﾅ and full-width カナ,
  are the same string to a rule.
* **Zero-width removal** drops the characters that occupy no visual space and
  exist mainly to break substring matching.
* **Case folding** lowers the Latin fragments that appear inside an otherwise
  Japanese corpus, so an English disease name is matched regardless of case.

`fold` keeps whitespace (including newlines), because the SaMD lint splits into
sentences on newlines before it looks for a forbidden pair, and that boundary
must survive normalisation. `squeeze` removes every whitespace character, and is
what a rule matches against once the sentence boundaries have already been
decided — it is the step that defeats space injection.
"""

from __future__ import annotations

import unicodedata

#: Characters that take up no width and are used to split a word so a literal
#: match misses it. Dropped wholesale from the judgment copy.
_ZERO_WIDTH = frozenset(
    {
        "​",  # zero-width space
        "‌",  # zero-width non-joiner
        "‍",  # zero-width joiner
        "⁠",  # word joiner
        "﻿",  # zero-width no-break space / BOM
        "᠎",  # Mongolian vowel separator
        "‎",  # left-to-right mark
        "‏",  # right-to-left mark
        "­",  # soft hyphen
        "⁡",  # function application
        "⁢",  # invisible times
        "⁣",  # invisible separator
        "⁤",  # invisible plus
    }
)


def _drop_zero_width(text: str) -> str:
    return "".join(ch for ch in text if ch not in _ZERO_WIDTH)


def fold(text: str) -> str:
    """NFKC + zero-width removal + case fold. Whitespace is preserved."""
    return _drop_zero_width(unicodedata.normalize("NFKC", text)).casefold()


def squeeze(text: str) -> str:
    """`fold`, then every Unicode whitespace character removed.

    This is the form a keyword or a disease/claim rule matches against: with the
    spaces gone, 「う つ 病」 and 「うつ病」 are one string.
    """
    return "".join(ch for ch in fold(text) if not ch.isspace())
