"""Audit log tests."""

from __future__ import annotations

import itertools
import sqlite3

import pytest

from sentinel.core.audit import GENESIS_HASH, AuditLog, canonical_json


@pytest.fixture
def log():
    with AuditLog() as instance:
        yield instance


def test_empty_log_verifies():
    with AuditLog() as log:
        assert log.verify_chain() is True
        assert len(log) == 0
        assert log.head() == GENESIS_HASH


def test_append_returns_a_hash_and_extends_the_chain(log):
    first = log.append("signature", {"who": "implementer-a"})
    second = log.append("signature", {"who": "implementer-b"})
    assert first != second
    assert len(first) == 64
    assert log.head() == second
    assert log.verify_chain() is True


def test_first_record_links_to_the_genesis_hash(log):
    log.append("kind", {})
    assert next(log.records()).prev_hash == GENESIS_HASH


def test_each_record_links_to_its_predecessor(log):
    for i in range(5):
        log.append("kind", {"i": i})
    records = list(log.records())
    assert len(records) == 5
    for previous, current in itertools.pairwise(records):
        assert current.prev_hash == previous.row_hash


def test_records_round_trip_the_payload(log):
    log.append("gate.block", {"gate": "samd_lint", "reasons": ["F001", "F101"]})
    record = next(log.records())
    assert record.payload == {"gate": "samd_lint", "reasons": ["F001", "F101"]}
    assert record.kind == "gate.block"
    assert record.seq == 1


def test_records_can_be_filtered_by_kind(log):
    log.append("a", {})
    log.append("b", {})
    log.append("a", {})
    assert [r.seq for r in log.records(kind="a")] == [1, 3]


def test_identical_payloads_still_get_distinct_hashes(log):
    """Position is part of the hash, so a repeated event is not a duplicate row."""
    first = log.append("same", {"x": 1})
    second = log.append("same", {"x": 1})
    assert first != second


def test_empty_kind_is_rejected(log):
    with pytest.raises(ValueError, match="non-empty"):
        log.append("", {})


# ---------------------------------------------------------------------------
# Tamper resistance
# ---------------------------------------------------------------------------


def test_update_is_refused_by_the_database(tmp_path):
    path = tmp_path / "audit.db"
    with AuditLog(path) as log:
        log.append("signature", {"decision": "approve"})

    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit SET kind = 'nothing-happened'")
    conn.close()


def test_delete_is_refused_by_the_database(tmp_path):
    path = tmp_path / "audit.db"
    with AuditLog(path) as log:
        log.append("signature", {"decision": "approve"})

    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit")
    conn.close()


def test_tampering_around_the_triggers_is_still_detected(tmp_path):
    """Drop the triggers, rewrite a row, and the chain says so.

    Someone with the file can always defeat a trigger. What they cannot do is
    change one row and have `verify_chain` keep returning True.
    """
    path = tmp_path / "audit.db"
    with AuditLog(path) as log:
        log.append("signature", {"decision": "approve"})
        log.append("signature", {"decision": "reject"})
        log.append("signature", {"decision": "approve"})
        assert log.verify_chain() is True

    conn = sqlite3.connect(path)
    conn.executescript(
        "DROP TRIGGER audit_no_update;"
        ' UPDATE audit SET payload_json = \'{"decision":"approve"}\' WHERE seq = 2;'
    )
    conn.commit()
    conn.close()

    with AuditLog(path) as log:
        assert log.verify_chain() is False


def test_removing_a_row_breaks_the_chain(tmp_path):
    path = tmp_path / "audit.db"
    with AuditLog(path) as log:
        for i in range(3):
            log.append("kind", {"i": i})

    conn = sqlite3.connect(path)
    conn.executescript("DROP TRIGGER audit_no_delete; DELETE FROM audit WHERE seq = 2;")
    conn.commit()
    conn.close()

    with AuditLog(path) as log:
        assert log.verify_chain() is False


def test_log_survives_reopening(tmp_path):
    path = tmp_path / "audit.db"
    with AuditLog(path) as log:
        head = log.append("kind", {"x": 1})
    with AuditLog(path) as log:
        assert log.head() == head
        assert log.verify_chain() is True
        assert len(log) == 1


# ---------------------------------------------------------------------------
# Canonical serialisation
# ---------------------------------------------------------------------------


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_canonical_json_keeps_japanese_readable():
    assert canonical_json({"note": "所見"}) == '{"note":"所見"}'
