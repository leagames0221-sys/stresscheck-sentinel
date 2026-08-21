"""Report how much the two llm-rubric judges agreed, from a promptfoo output file.

    PROMPTFOO_DISABLE_TELEMETRY=1 npx -y promptfoo@latest eval \
        -c evals/promptfooconfig.yaml --no-cache -j 1 -o run.json
    python evals/judge_agreement.py run.json

R5-3 asks for two judges *and* the agreement between them. Running two judges
and reporting only the pass rate would be the same evidence as running one, so
this closes that gap: it pairs every criterion graded by both judges on the same
output and reports raw agreement, Cohen's kappa and Krippendorff's alpha, plus
the individual disagreements — which are the rows worth reading, because a
criterion the two models split on is a criterion whose wording is doing less
work than it looks.

The pairing convention comes from the config: a metric named `X` is judge 1's
grade and `X(judge2)` is judge 2's grade of the same criterion.

Agreement is not a pass mark. Two judges that agree can be wrong together; that
is why the safety verdict lives in the deterministic gates and not here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vera_mh_ja"))

from score import agreement

JUDGE2_SUFFIX = "(judge2)"


def load_grades(path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """-> ({metric: {unit: "pass"|"fail"}} per judge, warnings)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", {}).get("results", data.get("results", []))

    judge1: dict[str, dict[str, str]] = defaultdict(dict)
    judge2: dict[str, dict[str, str]] = defaultdict(dict)
    warnings: list[str] = []

    for result in results:
        test = result.get("testCase", {})
        unit = test.get("description") or (test.get("vars", {}) or {}).get("case_id") or "?"
        grading = result.get("gradingResult") or {}
        for component in grading.get("componentResults", []) or []:
            metric = (component.get("assertion") or {}).get("metric")
            if not metric:
                continue
            verdict = "pass" if component.get("pass") else "fail"
            if metric.endswith(JUDGE2_SUFFIX):
                judge2[metric[: -len(JUDGE2_SUFFIX)]][unit] = verdict
            else:
                judge1[metric][unit] = verdict

    for metric in sorted(set(judge1) & set(judge2)):
        only_1 = set(judge1[metric]) - set(judge2[metric])
        only_2 = set(judge2[metric]) - set(judge1[metric])
        if only_1 or only_2:
            warnings.append(
                f"{metric}: 片方の judge にしか結果が無いユニット "
                f"judge1のみ={sorted(only_1)} judge2のみ={sorted(only_2)}"
            )
    return {"judge1": dict(judge1), "judge2": dict(judge2)}, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="promptfoo -o output JSON")
    parser.add_argument("--json", action="store_true", help="print as JSON")
    args = parser.parse_args(argv)

    grades, warnings = load_grades(args.results)
    judge1, judge2 = grades["judge1"], grades["judge2"]
    shared = sorted(set(judge1) & set(judge2))
    if not shared:
        print("2つの judge が共通で採点した基準が無い。judge を1つしか走らせていない可能性がある。")
        return 1

    report: dict[str, object] = {}
    for metric in shared:
        stats = agreement(judge1[metric], judge2[metric])
        report[metric] = {
            **stats.as_dict(),
            "disagreements": [
                {"unit": unit, "judge1": a, "judge2": b} for unit, a, b in stats.disagreements
            ],
        }

    pooled_a = {f"{m}|{u}": v for m in shared for u, v in judge1[m].items()}
    pooled_b = {f"{m}|{u}": v for m in shared for u, v in judge2[m].items()}
    report["ALL"] = agreement(pooled_a, pooled_b).as_dict()
    if warnings:
        report["warnings"] = warnings

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"judge 一致度 ({args.results.name})\n")
    header = f"{'基準':<12} {'n':>3} {'一致率':>7} {'kappa':>7} {'alpha':>7}  不一致"
    print(header)
    print("-" * len(header))

    def fmt(stats: dict[str, object], key: str) -> str:
        value = stats[key]
        return "  -  " if value is None else f"{value:6.3f}"

    for metric in [*shared, "ALL"]:
        stats: dict[str, object] = report[metric]  # type: ignore[assignment]
        units = ""
        if metric != "ALL":
            units = ", ".join(d["unit"] for d in stats["disagreements"])  # type: ignore[index]
        print(
            f"{metric:<12} {stats['n_units']:>3} {fmt(stats, 'raw_agreement')} "
            f"{fmt(stats, 'cohen_kappa')} {fmt(stats, 'krippendorff_alpha')}  {units}"
        )
    for warning in warnings:
        print(f"\n⚠ {warning}")
    print(
        "\n注: 一致度は合格の基準ではない。2つの judge が揃って間違うことはあるため、"
        "安全性の可否は決定論ゲート (packs/crisis, packs/samdlint) が持つ。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
