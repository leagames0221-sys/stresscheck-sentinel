"""Gate tests."""

from __future__ import annotations

import pytest

from sentinel.core.errors import GateSetupError
from sentinel.core.gates import (
    CallableGate,
    Gate,
    GateChain,
    GateResult,
    SignatureGate,
    require_non_empty,
)


def passing(name: str) -> CallableGate:
    return CallableGate(name, lambda payload: GateResult(True, name, ()))


def failing(name: str, reason: str) -> CallableGate:
    return CallableGate(name, lambda payload: GateResult(False, name, (reason,)))


# ---------------------------------------------------------------------------
# Startup validation: a gate that cannot protect must not start
# ---------------------------------------------------------------------------


def test_empty_chain_is_refused():
    with pytest.raises(GateSetupError, match="no gates"):
        GateChain([])


def test_validate_config_runs_at_construction_not_at_check_time():
    calls: list[str] = []

    def validate() -> None:
        calls.append("validated")
        raise GateSetupError("dictionary is empty")

    with pytest.raises(GateSetupError, match="dictionary is empty"):
        GateChain([CallableGate("samd_lint", lambda p: GateResult(True, "samd_lint"), validate)])
    assert calls == ["validated"]


def test_require_non_empty_rejects_an_empty_rule_set():
    with pytest.raises(GateSetupError, match="empty rule set"):
        require_non_empty("samd_lint", [])
    require_non_empty("samd_lint", ["F001"])  # does not raise


def test_unnamed_gate_is_refused():
    class Nameless(Gate):
        def check(self, payload):
            return GateResult(True, "")

        def validate_config(self):
            return None

    with pytest.raises(GateSetupError, match="has no name"):
        GateChain([Nameless()])


def test_duplicate_gate_names_are_refused():
    with pytest.raises(GateSetupError, match="duplicate gate name"):
        GateChain([passing("crisis"), passing("crisis")])


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_chain_imposes_its_own_order():
    chain = GateChain([passing("signature"), passing("samd_lint"), passing("crisis")])
    assert chain.names == ("crisis", "samd_lint", "signature")


def test_unknown_gates_sort_after_the_known_ones():
    chain = GateChain([passing("experimental"), passing("crisis")])
    assert chain.names == ("crisis", "experimental")


def test_crisis_wins_over_a_later_failure():
    chain = GateChain([failing("samd_lint", "F001"), failing("crisis", "ideation")])
    result = chain.check({})
    assert result.gate == "crisis"
    assert result.reasons == ("ideation",)


# ---------------------------------------------------------------------------
# Chain behaviour
# ---------------------------------------------------------------------------


def test_all_passing_gives_an_ok_chain_result():
    chain = GateChain([passing("crisis"), passing("samd_lint")])
    assert chain.check({}) == GateResult(True, "chain", ())


def test_chain_short_circuits_at_the_first_refusal():
    ran: list[str] = []

    def record(name: str, ok: bool):
        def check(payload):
            ran.append(name)
            return GateResult(ok, name, () if ok else ("blocked",))

        return CallableGate(name, check)

    chain = GateChain([record("crisis", False), record("samd_lint", True)])
    assert chain.check({}).ok is False
    assert ran == ["crisis"]


def test_check_detailed_runs_every_gate():
    chain = GateChain([failing("crisis", "x"), failing("samd_lint", "y")])
    results = chain.check_detailed({})
    assert [r.gate for r in results] == ["crisis", "samd_lint"]
    assert all(r.ok is False for r in results)


def test_gate_result_defaults_to_no_reasons():
    assert GateResult(True, "crisis").reasons == ()


# ---------------------------------------------------------------------------
# SignatureGate
# ---------------------------------------------------------------------------


def test_unsigned_result_is_held():
    gate = SignatureGate(has_signature=lambda h: False)
    result = gate.check({"payload_hash": "abc"})
    assert result.ok is False
    assert result.reasons == ("no_implementer_signature",)


def test_signed_result_proceeds():
    gate = SignatureGate(has_signature=lambda h: h == "abc")
    assert gate.check({"payload_hash": "abc"}).ok is True


def test_signature_is_bound_to_the_data_not_the_person():
    """A signature for one payload must not release a different payload."""
    signed = {"abc"}
    gate = SignatureGate(has_signature=lambda h: h in signed)
    assert gate.check({"payload_hash": "abc"}).ok is True
    assert gate.check({"payload_hash": "abc-amended"}).ok is False


def test_payload_without_a_hash_is_held():
    gate = SignatureGate(has_signature=lambda h: True)
    result = gate.check({})
    assert result.ok is False
    assert result.reasons == ("missing_payload_hash",)


def test_gate_can_be_scoped_to_the_payloads_that_need_it():
    gate = SignatureGate(
        has_signature=lambda h: False,
        applies_when=lambda p: p.get("high_stress") is True,
    )
    assert gate.check({"high_stress": False}).ok is True
    assert gate.check({"high_stress": True, "payload_hash": "abc"}).ok is False


def test_signature_gate_rejects_a_non_callable_checker():
    gate = SignatureGate(has_signature="yes")  # type: ignore[arg-type]
    with pytest.raises(GateSetupError, match="callable"):
        gate.validate_config()


def test_signature_gate_in_a_chain_holds_the_pipeline():
    chain = GateChain([passing("crisis"), SignatureGate(has_signature=lambda h: False)])
    result = chain.check({"payload_hash": "abc"})
    assert result.ok is False
    assert result.gate == "signature"
