"""Feed the deterministic goldsets to promptfoo as generated test cases.

promptfoo can read a CSV directly, but its parser has no comment syntax, and
every data file in this repository starts with a `#` provenance block naming the
source document and licence. Dropping those headers to suit a tool would be the
wrong trade: the headers are why the numbers in the goldsets can be checked
against the ministry PDFs at all.

So the CSVs stay as they are and promptfoo reads them through this generator,
which uses the same `read_goldset()` the standalone runner uses. One parser, one
set of rows, two runners.

Referenced from promptfooconfig.yaml as:

    tests:
      - file://tests_deterministic.py:generate_tests
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import read_goldset


def _case(row: dict[str, str], assertion: str, label: str) -> dict[str, Any]:
    return {
        "description": f"{row['case_id']} {label}",
        "vars": dict(row),
        # `echo` returns the rendered prompt without calling a model: these rows
        # are checked by calling the product directly, so an inference here would
        # be cost with no evidence attached.
        "provider": "echo",
        "assert": [
            {
                "type": "python",
                "value": f"file://{assertion}",
                "metric": label,
            }
        ],
    }


def generate_tests() -> list[dict[str, Any]]:
    """Every scoring row and every adversarial row, as promptfoo test cases."""
    cases: list[dict[str, Any]] = []
    for row in read_goldset("scoring_boundary.csv"):
        cases.append(_case(row, "assert_scoring.py", "採点"))
    for row in read_goldset("adversarial.csv"):
        cases.append(_case(row, "assert_gates.py", "決定論ゲート"))
    return cases


if __name__ == "__main__":
    import json

    generated = generate_tests()
    print(json.dumps(generated, ensure_ascii=False)[:400])
    print(f"\n{len(generated)} test cases", file=sys.stderr)
