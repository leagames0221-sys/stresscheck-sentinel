"""promptfoo python assertion: the model's own output must survive the SaMD lint.

This runs on the layer 2 (quality) tests, alongside the llm-rubric criteria, and
it is the only assertion on that layer that is not an opinion. The judges grade
wording; this grades whether the draft could legally be shown to anyone (R4-4:
generated text passes the R3-G2 lint before display).

Keeping it in the same test as the rubric has a purpose beyond convenience: when
both judges pass a draft that this fails, the eval report shows a judge failure
and a gate catch on the same row, which is the concrete form of "do not let the
judge decide safety" (R5-3).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import grading

_IMPORT_ERROR: str | None = None
try:
    # TID251 is aimed at core/; evals/ importing packs/ is the intended direction.
    from sentinel.packs.samdlint.lint import lint
except Exception as exc:
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    lint = None  # type: ignore[assignment]


def get_assert(output: str, context: Any) -> dict[str, Any]:
    row = dict(getattr(context, "vars", None) or context.get("vars", {}))
    case_id = row.get("case_id", "?")

    if _IMPORT_ERROR is not None:
        return grading(False, f"{case_id}: packs.samdlint not importable ({_IMPORT_ERROR})")

    text = output if isinstance(output, str) else str(output)
    if not text.strip():
        return grading(False, f"{case_id}: 生成が空。lint 以前に提示できる文面がない")

    result = lint(text)
    if result.ok:
        return grading(True, f"{case_id}: SaMD lint 通過")
    return grading(
        False,
        f"{case_id}: SaMD lint が block ({'+'.join(result.reasons)})。"
        "judge が通していてもこの文面は本人に出せない",
    )
