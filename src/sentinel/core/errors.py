"""Exception hierarchy.

Every exception this project raises on purpose descends from `SentinelError`, so
a caller can distinguish "the tool refused" from "the tool crashed".
"""

from __future__ import annotations


class SentinelError(Exception):
    """Base class for every deliberate failure in this project."""


class GateSetupError(SentinelError, ValueError):
    """A gate was constructed with a configuration that cannot be trusted.

    Raised at construction time, not check time. An empty forbidden-word
    dictionary is the motivating case: a gate that silently passes everything is
    worse than no gate, because it looks like protection. Failing to start is
    the correct behaviour.

    Also a `ValueError`, because `api_contract.md` specifies R3-G4 as "empty
    dictionary raises ValueError at startup" and a caller written against that
    line should not have to know this project's exception hierarchy to catch it.
    """


class StateError(SentinelError):
    """An operation was attempted from a state that does not allow it.

    Chiefly: resuming an interrupt that has already been decided. This is what
    stops a replayed or double-submitted review from producing the side effect
    twice.
    """


class GroupSizeError(SentinelError):
    """A group analysis was requested for a group too small to be anonymous."""


class DataFileError(SentinelError):
    """A bundled CSV is missing, malformed, or internally inconsistent."""


class LLMProviderError(SentinelError):
    """An LLM provider was misconfigured or could not be reached."""
