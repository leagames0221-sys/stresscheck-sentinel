"""Run the deterministic eval layers with nothing but Python.

    python evals/run_deterministic.py            # summary, exit 0/1
    python evals/run_deterministic.py --verbose  # one line per case

Why this exists next to `promptfooconfig.yaml`: promptfoo needs node, and the
layer 2 judges need a running Ollama. Neither is available in every place this
has to be verifiable — CI, a fresh clone, a reviewer's laptop — and the checks
that are allowed to gate a release must not depend on either. So layer 1 is
runnable twice, by two different runners, over the same goldset files.

Four checks, in order of what they are worth:

1. Scoring.   Every row of goldsets/scoring_boundary.csv against `score()`.
   The row's expected sums are also recomputed by `independent_sums()`, which
   reads the item and threshold CSVs and knows nothing about scoring.py. A row
   passes only when the goldset, the second implementation, and the product
   agree. Two of the three sides can be wrong together only by coincidence.
2. Reversal.  The goldset records what a no-reversal implementation would have
   concluded. If no row differs, the reversal recode is not being tested by any
   of them, and that is reported as a failure of the goldset rather than a pass.
3. Gates.     Every row of goldsets/adversarial.csv against the crisis
   classifier and the SaMD lint, in both directions (the harmful reply is
   stopped; the reply the product actually sends is not).
4. Consistency. The layer 2 scenarios quote domain totals from layer 1 cases.
   Their stated verdicts are re-derived from the threshold CSV, so a scenario
   cannot drift into describing a verdict the rules do not produce.

The VERA-MH scorer's own selftest is invoked too, so one command covers
everything in `evals/` that does not need a model.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    EVALS_DIR,
    independent_sums,
    parse_answers,
    read_goldset,
)

GREEN_MARK = "ok  "
RED_MARK = "FAIL"


class Results:
    """Counts and failure lines for one section."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.passed = 0
        self.failed: list[str] = []
        self.lines: list[str] = []

    def record(self, ok: bool, label: str, detail: str = "") -> None:
        mark = GREEN_MARK if ok else RED_MARK
        self.lines.append(f"  [{mark}] {label}" + (f"   {detail}" if detail else ""))
        if ok:
            self.passed += 1
        else:
            self.failed.append(f"{label}: {detail}")

    @property
    def total(self) -> int:
        return self.passed + len(self.failed)


def check_scoring() -> tuple[Results, list[dict[str, str]]]:
    """Layer 1: the scoring goldset, three-way."""
    import assert_scoring

    results = Results("採点ゴールドセット (goldsets/scoring_boundary.csv)")
    rows = read_goldset("scoring_boundary.csv")

    for row in rows:
        case_id = row["case_id"]

        # Independent recomputation, for the rows that state expected sums.
        if not row["expected_error"]:
            answers = parse_answers(row["answers"])
            recomputed = independent_sums(answers, row["variant"])
            stated = {d: int(row[f"expected_{d}"]) for d in ("A", "B", "C")}
            if recomputed != stated:
                results.record(
                    False,
                    case_id,
                    f"ゴールドセットの合計点が独立再計算と食い違う: {stated} vs {recomputed}",
                )
                continue

        passed, reason = assert_scoring.check_row(row)
        results.record(passed, case_id, reason if not passed else row["description"])

    return results, rows


def check_reversal(rows: list[dict[str, str]]) -> Results:
    """Layer 1: does any row actually depend on the reversal recode?"""
    results = Results("逆転項目の効き")
    flips = [
        row
        for row in rows
        if not row["expected_error"]
        and not row["expected_missing"]
        and row["expected_high_stress"] != row["naive_high_stress"]
    ]
    results.record(
        bool(flips),
        "逆転換算の有無で判定が変わる行が存在する",
        f"{len(flips)} 行 ({', '.join(r['case_id'] for r in flips[:6])}"
        f"{' ...' if len(flips) > 6 else ''})"
        if flips
        else "0 行 = このゴールドセットは逆転項目を検査していない",
    )
    both_variants = {row["variant"] for row in flips}
    results.record(
        both_variants == {"57", "23"},
        "57項目版と23項目版の双方に反転行がある",
        f"variants={sorted(both_variants)}",
    )
    return results


def check_gates() -> Results:
    """Layer 1': the adversarial set against the deterministic gates."""
    import assert_gates

    results = Results("敵対的セット (goldsets/adversarial.csv)")
    available, why = assert_gates.gates_available()
    if not available:
        results.record(
            False,
            "packs.crisis / packs.samdlint が import できない",
            f"{why} — packs が import できない状態。緑ではない",
        )
        return results

    rows = read_goldset("adversarial.csv")
    for row in rows:
        passed, reason = assert_gates.check_row(row)
        results.record(bool(passed), row["case_id"], reason if not passed else row["category"])

    # A set with no benign rows would prove only that the gate says no to
    # everything, which is not the property anyone wants.
    benign = [r for r in rows if r["category"] == "benign_control"]
    results.record(
        len(benign) >= 3,
        "良性対照が3件以上ある (常時 block していないことの確認)",
        f"{len(benign)} 件",
    )
    crisis_levels = {r["expect_crisis_level"] for r in rows}
    results.record(
        {"explore", "ideation", "plan", "prepared", "none"} <= crisis_levels,
        "危機の全段階 (explore/ideation/plan/prepared) と none を含む",
        f"levels={sorted(crisis_levels)}",
    )
    return results


def check_selfcare_consistency() -> Results:
    """Layer 2 scenarios must quote verdicts the threshold file actually gives."""
    # TID251 is aimed at core/; evals/ importing packs/ is the intended direction.
    from sentinel.packs.jsq.scoring import load_thresholds

    results = Results("セルフケア文面シナリオ (goldsets/selfcare_quality.csv)")
    criteria = load_thresholds().for_variant("57")
    criteria_23 = load_thresholds().for_variant("23")

    for row in read_goldset("selfcare_quality.csv"):
        sums = {d: int(row[f"sum_{d.lower()}"]) for d in ("A", "B", "C")}
        # The 23-item scenarios are the ones whose totals cannot occur on the
        # 57-item form (its minimum per domain is 17/29/9).
        variant = (
            criteria if sums["A"] >= 17 and sums["B"] >= 29 and sums["C"] >= 9 else criteria_23
        )
        hit = next((rule.name for rule in variant.rules if rule.evaluate(sums)), "none")
        stated_high = "選定されました" in row["verdict"]
        ok = (hit != "none") == stated_high
        results.record(
            ok,
            row["case_id"],
            f"{sums} -> rule={hit} / 記載={row['verdict']}",
        )
    return results


def check_vera_selftest() -> Results:
    """The asymmetric scorer's own selftest, run as a subprocess."""
    results = Results("VERA-MH 日本語版スコアラー (evals/vera_mh_ja/score.py --selftest)")
    script = EVALS_DIR / "vera_mh_ja" / "score.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--selftest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    results.record(
        proc.returncode == 0,
        "score.py --selftest",
        tail[-1] if tail else f"exit={proc.returncode}",
    )
    return results


def check_ja_data_files() -> Results:
    """The Japanese VERA-MH data files must keep the upstream shape."""
    import csv

    results = Results("VERA-MH 日本語版データ (rubric_ja.tsv / personas_ja.tsv)")
    base = EVALS_DIR / "vera_mh_ja"

    def read_tsv(name: str) -> list[list[str]]:
        path = base / name
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("#")
        ]
        return list(csv.reader(lines, delimiter="\t"))

    rubric = read_tsv("rubric_ja.tsv")
    results.record(
        len(rubric) == 70 and len(rubric[0]) == 10,
        "rubric_ja.tsv は 10 列 / ヘッダ+69 行 (原典と同じ形)",
        f"{len(rubric) - 1} 行 x {len(rubric[0])} 列",
    )
    dimensions = {row[1] for row in rubric[1:] if row[1]}
    results.record(
        len(dimensions) == 5,
        "rubric_ja.tsv の次元は 5 つ",
        f"{sorted(dimensions)}",
    )
    severities = {row[2] for row in rubric[1:] if row[2]}
    results.record(
        severities == {"Red", "Yellow"},
        "Severity は Red / Yellow のまま (HPH / SLPH への写像に使う)",
        f"{sorted(severities)}",
    )
    untranslated = [row[0] or "(cont)" for row in rubric[1:] if row[4].strip() and row[4].isascii()]
    results.record(
        not untranslated,
        "Question 列に未翻訳の行がない",
        f"未翻訳: {untranslated}" if untranslated else "",
    )

    personas = read_tsv("personas_ja.tsv")
    header, body = personas[0], personas[1:]
    results.record(len(body) == 20, "personas_ja.tsv は 20 件", f"{len(body)} 件")
    results.record(
        "Final Seed Phrase" in header and "source_index" in header,
        "原典の列 + source_index を保持",
        f"{len(header)} 列",
    )
    levels = [row[header.index("Short Current Suicide Risk Level")] for row in body]
    counts = {lvl: levels.count(lvl) for lvl in ("None", "Low", "High", "Immediate")}
    results.record(
        counts == {"None": 2, "Low": 6, "High": 6, "Immediate": 6},
        "リスク水準の層化 (None 2 / Low 6 / High 6 / Immediate 6)",
        f"{counts}",
    )
    names = [row[header.index("Name")] for row in body]
    results.record(
        all(n.startswith("JP-") for n in names) and len(set(names)) == 20,
        "Name 列は識別子のみ (実在しうる人名を持ち込まない)",
        f"{names[0]}..{names[-1]}",
    )
    hotlines = (EVALS_DIR.parent / "data" / "hotlines_ja.csv").read_text(encoding="utf-8")
    # Body only: the `#` header names the US services it replaced, and that
    # sentence is the provenance record, not a leftover.
    rubric_text = "\n".join(
        line
        for line in (base / "rubric_ja.tsv").read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    )
    us_only = [token for token in ("988", "911", "Crisis Text Line") if token in rubric_text]
    results.record(
        not us_only,
        "rubric_ja.tsv に米国固有の窓口が残っていない",
        f"残存: {us_only}" if us_only else "",
    )
    results.record(
        "0120-061-338" in rubric_text and "0120-061-338" in hotlines,
        "rubric_ja.tsv の窓口が data/hotlines_ja.csv 掲載のものである",
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="deterministic evals, no node and no Ollama")
    parser.add_argument(
        "--verbose", action="store_true", help="print every case, not only failures"
    )
    args = parser.parse_args(argv)

    scoring, rows = check_scoring()
    sections = [
        scoring,
        check_reversal(rows),
        check_gates(),
        check_selfcare_consistency(),
        check_vera_selftest(),
        check_ja_data_files(),
    ]

    for section in sections:
        print(f"\n== {section.name}")
        if args.verbose:
            for line in section.lines:
                print(line)
        else:
            for line in section.lines:
                if RED_MARK in line:
                    print(line)
        print(f"  -> {section.passed}/{section.total} passed")

    total_passed = sum(s.passed for s in sections)
    total = sum(s.total for s in sections)
    failed = [f for s in sections for f in s.failed]

    print("\n" + "=" * 72)
    print(f"deterministic evals: {total_passed}/{total} passed")
    if failed:
        print(f"failures ({len(failed)}):")
        for line in failed:
            print(f"  - {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
