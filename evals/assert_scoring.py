"""promptfoo python assertion for eval layer 1 (scoring).

Called once per row of `goldsets/scoring_boundary.csv`. It ignores the model
output entirely and calls `sentinel.packs.jsq.scoring.score()` directly: the
questionnaire verdict is a pure function of the answers and the threshold CSV,
and a language model is never in that path. Running these rows through promptfoo
buys one thing only — the scoring goldset and the LLM goldsets live in the same
config, are run by the same command, and fail the same build (R5-1).

`evals/run_deterministic.py` checks the same rows without promptfoo or node, so
this file being unreachable never means the check is unreachable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import as_bool, grading, parse_answers


def check_row(row: dict[str, str]) -> tuple[bool, str]:
    """Verify one goldset row against `score()`. Returns (passed, reason)."""
    # TID251 is aimed at core/; evals/ importing packs/ is the intended direction.
    from sentinel.packs.jsq.scoring import score

    case_id = row["case_id"]
    variant = row["variant"]
    answers = parse_answers(row["answers"])

    expected_error = (row.get("expected_error") or "").strip()
    if expected_error:
        try:
            result = score(answers, variant)
        except Exception as exc:
            if type(exc).__name__ == expected_error:
                return True, f"{case_id}: raised {expected_error} as expected"
            return (
                False,
                f"{case_id}: expected {expected_error}, raised {type(exc).__name__}: {exc}",
            )
        return False, f"{case_id}: expected {expected_error}, but returned {result}"

    result = score(answers, variant)

    problems: list[str] = []
    if result.valid is not as_bool(row["expected_valid"]):
        problems.append(f"valid={result.valid} expected={row['expected_valid']}")
    got_missing = " ".join(str(m) for m in result.missing)
    if got_missing != row["expected_missing"].strip():
        problems.append(f"missing=[{got_missing}] expected=[{row['expected_missing'].strip()}]")
    for domain in ("A", "B", "C"):
        expected = int(row[f"expected_{domain}"])
        got = result.sums.get(domain)
        if got != expected:
            problems.append(f"{domain}={got} expected={expected}")
    if result.high_stress is not as_bool(row["expected_high_stress"]):
        problems.append(f"high_stress={result.high_stress} expected={row['expected_high_stress']}")
    if result.rule_hit != row["expected_rule_hit"]:
        problems.append(f"rule_hit={result.rule_hit} expected={row['expected_rule_hit']}")

    if problems:
        return False, f"{case_id} ({row['description']}): " + "; ".join(problems)
    return True, (
        f"{case_id}: A={result.sums['A']} B={result.sums['B']} C={result.sums['C']} "
        f"high_stress={result.high_stress} rule={result.rule_hit}"
    )


def get_assert(output: str, context: Any) -> dict[str, Any]:
    """promptfoo entry point. `context.vars` carries the CSV row."""
    row = dict(getattr(context, "vars", None) or context.get("vars", {}))
    passed, reason = check_row(row)
    return grading(passed, reason)
