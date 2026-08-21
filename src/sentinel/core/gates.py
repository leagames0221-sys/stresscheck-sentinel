"""Boundary gates.

A gate answers one question — may this payload proceed? — and answers it the
same way every time. Gates are not advisory. There is no "warn" result, because
a warning is a block that someone forgot to implement.

Three properties are load-bearing:

**Configuration is validated at construction, not at check time.** A gate built
from an empty dictionary would pass everything while looking like protection.
`GateChain` calls `validate_config()` on every member as it is assembled, so a
misconfigured deployment fails to start rather than failing open. An empty chain
is itself a configuration error.

**Order is fixed, not caller-supplied.** Crisis detection runs first, because a
respondent in crisis must reach a helpline whether or not the rest of the
pipeline is happy. Then the forbidden-expression lint, then the signature check.
`GateChain` sorts its members into that order regardless of the order it was
given them.

**The chain short-circuits.** The first refusal is the answer. Running later
gates after a crisis has been detected would mean deciding what to do with text
that should already have stopped moving.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from typing import Any, NamedTuple

from sentinel.core.errors import GateSetupError

#: Fixed evaluation order. Names not listed here sort after the known ones, in
#: the order supplied, so an unfamiliar gate cannot silently pre-empt the crisis
#: check.
GATE_ORDER: tuple[str, ...] = ("crisis", "samd_lint", "signature")


class GateResult(NamedTuple):
    """The verdict of one gate, or of a chain.

    `reasons` is written straight to the audit log, so it must name *which rule*
    fired (an id), never quote the respondent's own words back.
    """

    ok: bool
    gate: str
    reasons: tuple[str, ...] = ()


class Gate(ABC):
    """Base class for a gate."""

    #: Must match an entry in `GATE_ORDER` for the chain to place it.
    name: str = ""

    @abstractmethod
    def check(self, payload: dict[str, Any]) -> GateResult:
        """Return whether `payload` may proceed."""

    @abstractmethod
    def validate_config(self) -> None:
        """Raise `GateSetupError` if this gate cannot do its job as configured."""


class GateChain:
    """An ordered, validated collection of gates."""

    def __init__(self, gates: Iterable[Gate]) -> None:
        members = list(gates)
        if not members:
            raise GateSetupError(
                "GateChain was constructed with no gates. An empty chain passes"
                " everything while appearing to protect the pipeline, so it is"
                " rejected at startup rather than at the first bad payload."
            )

        seen: set[str] = set()
        for gate in members:
            if not getattr(gate, "name", ""):
                raise GateSetupError(f"gate {type(gate).__name__} has no name")
            if gate.name in seen:
                raise GateSetupError(f"duplicate gate name in chain: {gate.name!r}")
            seen.add(gate.name)
            gate.validate_config()

        def rank(gate: Gate) -> int:
            return GATE_ORDER.index(gate.name) if gate.name in GATE_ORDER else len(GATE_ORDER)

        self.gates: tuple[Gate, ...] = tuple(sorted(members, key=rank))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(g.name for g in self.gates)

    def check(self, payload: dict[str, Any]) -> GateResult:
        """Run gates in order, stopping at the first refusal."""
        for gate in self.gates:
            result = gate.check(payload)
            if not result.ok:
                return result
        return GateResult(ok=True, gate="chain", reasons=())

    def check_detailed(self, payload: dict[str, Any]) -> tuple[GateResult, ...]:
        """Run every gate and return all verdicts.

        For review screens and eval reports, where seeing all the reasons at once
        is worth more than short-circuiting. Never use this to decide whether a
        payload proceeds — that is `check`.
        """
        return tuple(gate.check(payload) for gate in self.gates)


class SignatureGate(Gate):
    """Hold a result until an implementer has signed for it.

    The statutory division of labour is narrow and explicit: clerical handling
    of the questionnaire may be delegated, but confirming whether someone needs
    a physician interview may not. This gate is that sentence expressed as
    control flow — a high-stress result does not reach the respondent, and does
    not trigger an interview recommendation, until a signature record exists for
    exactly that data.

    Binding the signature to a hash of the data, rather than to a respondent id,
    is what stops a signature from being reused after the data changes.
    """

    name = "signature"

    def __init__(
        self,
        has_signature: Callable[[str], bool],
        payload_hash_key: str = "payload_hash",
        applies_when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        """
        Args:
            has_signature: given a payload hash, report whether a signature
                record exists for it. Injected so that `core` stays ignorant of
                where signatures are stored.
            payload_hash_key: key under which the payload carries its own hash.
            applies_when: optional predicate limiting the gate to payloads that
                need a signature. Defaults to "every payload needs one" —
                requiring a signature where none was needed is a nuisance,
                whereas the opposite is the failure this gate exists to prevent.
        """
        self._has_signature = has_signature
        self._payload_hash_key = payload_hash_key
        self._applies_when = applies_when

    def validate_config(self) -> None:
        if not callable(self._has_signature):
            raise GateSetupError("SignatureGate requires a callable has_signature")
        if not self._payload_hash_key:
            raise GateSetupError("SignatureGate requires a non-empty payload_hash_key")

    def check(self, payload: dict[str, Any]) -> GateResult:
        if self._applies_when is not None and not self._applies_when(payload):
            return GateResult(ok=True, gate=self.name, reasons=("not_applicable",))

        payload_hash = payload.get(self._payload_hash_key)
        if not payload_hash:
            return GateResult(
                ok=False,
                gate=self.name,
                reasons=("missing_payload_hash",),
            )
        if not self._has_signature(str(payload_hash)):
            return GateResult(
                ok=False,
                gate=self.name,
                reasons=("no_implementer_signature",),
            )
        return GateResult(ok=True, gate=self.name, reasons=())


class CallableGate(Gate):
    """Adapter turning a plain predicate into a gate.

    Lets a pack supply its classifier without importing anything from `core`
    beyond these types, and lets tests build a chain without stub classes.
    """

    def __init__(
        self,
        name: str,
        check_fn: Callable[[dict[str, Any]], GateResult],
        validate_fn: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self._check_fn = check_fn
        self._validate_fn = validate_fn

    def validate_config(self) -> None:
        if not self.name:
            raise GateSetupError("CallableGate requires a name")
        if not callable(self._check_fn):
            raise GateSetupError(f"gate {self.name!r} requires a callable check_fn")
        if self._validate_fn is not None:
            self._validate_fn()

    def check(self, payload: dict[str, Any]) -> GateResult:
        return self._check_fn(payload)


def require_non_empty(name: str, entries: Sequence[object]) -> None:
    """Raise `GateSetupError` when a gate's dictionary or rule set is empty.

    The one-line helper exists so that every gate refuses emptiness with the
    same wording, and so that "did we remember to check?" is answerable by
    grepping for one name.
    """
    if not entries:
        raise GateSetupError(
            f"gate {name!r} was configured with an empty rule set."
            " A gate with nothing to match passes every input while still"
            " reporting success, which is indistinguishable from having no gate"
            " at all. Load its data file or remove the gate deliberately."
        )
