"""CLI tests.

Exit codes matter more than wording here: `gate-check` is meant to be usable as
a step in a pipeline, so "refused" has to be something a shell can see.
"""

from __future__ import annotations

import json

import pytest

from sentinel.cli import main
from tests.conftest import build_answers

HIGH_STRESS = {"A": 40, "B": 90, "C": 30}
LOW_STRESS = {"A": 30, "B": 40, "C": 30}


def run(argv, capsys) -> tuple[int, dict]:
    """Run the CLI and parse stdout. `run.stderr` holds the error text.

    `capsys.readouterr()` drains both streams, so it is called exactly once and
    the error text is stashed rather than re-read.
    """
    code = main(argv)
    captured = capsys.readouterr()
    run.stderr = captured.err
    return code, (json.loads(captured.out) if captured.out.strip() else {})


run.stderr = ""


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def test_score_from_a_json_argument(capsys):
    answers = json.dumps({str(k): v for k, v in build_answers("57", HIGH_STRESS).items()})
    code, payload = run(["score", "--variant", "57", "--answers", answers], capsys)
    assert code == 0
    assert payload["high_stress"] is True
    assert payload["rule_hit"] == "B_only"
    assert payload["sums"]["B"] == 90


def test_score_from_a_file(tmp_path, capsys):
    path = tmp_path / "answers.json"
    path.write_text(
        json.dumps({str(k): v for k, v in build_answers("57", LOW_STRESS).items()}),
        encoding="utf-8",
    )
    code, payload = run(["score", "--answers-file", str(path)], capsys)
    assert code == 0
    assert payload["high_stress"] is False
    assert payload["valid"] is True


def test_score_reports_missing_answers_rather_than_guessing(capsys):
    answers = build_answers("57", HIGH_STRESS, omit=(4,))
    code, payload = run(
        ["score", "--answers", json.dumps({str(k): v for k, v in answers.items()})], capsys
    )
    assert code == 0
    assert payload["valid"] is False
    assert payload["missing"] == [4]
    assert payload["high_stress"] is False


def test_score_rejects_an_out_of_range_answer(capsys):
    code, _ = run(["score", "--answers", '{"1": 7}'], capsys)
    assert code == 1
    assert "1..4" in run.stderr


def test_score_requires_a_source():
    with pytest.raises(SystemExit) as caught:
        main(["score"])
    assert caught.value.code == 2


# ---------------------------------------------------------------------------
# group
# ---------------------------------------------------------------------------


def _group_csv(path, rows: int, sex: str = "m") -> str:
    items = [1, 2, 3, 8, 9, 10, 47, 48, 50, 51, 53, 54]
    header = "sex," + ",".join(str(i) for i in items)
    lines = [header]
    for _ in range(rows):
        lines.append(sex + "," + ",".join("2" for _ in items))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_group_analyses_ten_respondents(tmp_path, capsys):
    code, payload = run(["group", "--file", _group_csv(tmp_path / "g.csv", 10)], capsys)
    assert code == 0
    assert payload["n"] == 10
    assert payload["chart_sex"] == "m"
    assert set(payload["scales"]) == {
        "quantitative_load",
        "control",
        "supervisor_support",
        "coworker_support",
    }


def test_group_refuses_nine_respondents(tmp_path, capsys):
    code, _ = run(["group", "--file", _group_csv(tmp_path / "g.csv", 9)], capsys)
    assert code == 1
    assert "10" in run.stderr


def test_group_reports_the_unverified_coefficients_rather_than_inventing_them(tmp_path, capsys):
    _, payload = run(["group", "--file", _group_csv(tmp_path / "g.csv", 12)], capsys)
    assert payload["coefficients_verified"] is False
    assert payload["total_risk"] is None
    assert any("健康リスク" in note for note in payload["notes"])


# ---------------------------------------------------------------------------
# gate-check
# ---------------------------------------------------------------------------


def test_gate_check_exits_zero_for_acceptable_text(capsys):
    code, payload = run(["gate-check", "--text", "睡眠の時間を確保してみてください。"], capsys)
    assert code == 0
    assert payload["ok"] is True


def test_gate_check_exits_one_for_a_forbidden_expression(capsys):
    code, payload = run(["gate-check", "--text", "うつ病の可能性があります。"], capsys)
    assert code == 1
    assert payload["blocked_by"] == "samd_lint"


def test_gate_check_exits_one_for_a_crisis_expression(capsys):
    code, payload = run(["gate-check", "--text", "遺書を書いた。"], capsys)
    assert code == 1
    assert payload["blocked_by"] == "crisis"


def test_gate_check_reads_a_file(tmp_path, capsys):
    path = tmp_path / "draft.txt"
    path.write_text("うつ病のリスクは 40% です。", encoding="utf-8")
    code, payload = run(["gate-check", "--file", str(path)], capsys)
    assert code == 1
    assert "F201" in {r for gate in payload["gates"] for r in gate["reasons"]}


def test_gate_check_lists_every_gate(capsys):
    _, payload = run(["gate-check", "--text", "普通の文章です。"], capsys)
    assert [gate["gate"] for gate in payload["gates"]] == ["crisis", "samd_lint", "signature"]


# ---------------------------------------------------------------------------
# kpi
# ---------------------------------------------------------------------------


def test_kpi_on_an_empty_database(tmp_path, capsys):
    code, payload = run(["kpi", "--db", str(tmp_path / "sentinel.db")], capsys)
    assert code == 0
    assert payload["total"] == 0
    assert payload["override_rate"] == 0.0
    assert payload["audit_chain_ok"] is True


def test_kpi_reads_a_database_written_by_the_service(tmp_path, capsys):
    from sentinel.app.service import SentinelService

    db = str(tmp_path / "sentinel.db")
    with SentinelService(db) as service:
        submission = service.submit(build_answers("57", HIGH_STRESS), "57", token_seed="c01")
        service.decide(submission.interrupt_id, "approve", "implementer-a")

    _, payload = run(["kpi", "--db", db], capsys)
    assert payload["total"] == 1
    assert payload["submissions"] == 1
    assert payload["signatures"] == 1


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_a_missing_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def test_version_is_reported():
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0


def test_every_documented_subcommand_exists():
    from sentinel.cli import build_parser

    actions = build_parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    assert set(actions) == {"score", "group", "gate-check", "serve", "kpi"}
