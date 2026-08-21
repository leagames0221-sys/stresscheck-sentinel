"""Command line entry point.

Five verbs, each a thin wrapper over something that already exists:

    score       one questionnaire, from JSON, deterministic
    group       one group, from CSV, refusing to run below ten people
    gate-check  one piece of text against every gate (exit 1 if refused)
    serve       the loopback HTTP app
    kpi         the review statistics, including the override rate

Output is JSON on stdout. Errors are one line on stderr and a non-zero exit,
because these commands are meant to be usable from a script and from CI without
parsing prose. `gate-check` in particular exits 1 when the text is refused, so
it can be wired into a pipeline as a check rather than as a report to read.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sentinel import __version__
from sentinel.app.server import DEFAULT_PORT, serve
from sentinel.app.service import SentinelService
from sentinel.core.errors import SentinelError
from sentinel.packs.jsq.group import group_analysis
from sentinel.packs.jsq.scoring import score

EXIT_OK = 0
EXIT_REFUSED = 1


def _emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _read_text(source: str) -> str:
    """Read from a path, or from stdin when the argument is `-`."""
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def _load_answers(raw: str) -> dict[int, int]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("answers must be a JSON object mapping item numbers to 1..4")
    return {int(k): int(v) for k, v in payload.items()}


def cmd_score(args: argparse.Namespace) -> int:
    raw = args.answers if args.answers is not None else _read_text(args.answers_file)
    result = score(_load_answers(raw), variant=args.variant)
    _emit(
        {
            "variant": result.variant,
            "sums": result.sums,
            "high_stress": result.high_stress,
            "rule_hit": result.rule_hit,
            "missing": list(result.missing),
            "valid": result.valid,
        }
    )
    return EXIT_OK


def _read_group_csv(path: str) -> tuple[list[dict[int, int]], list[str]]:
    """Read one respondent per row.

    Columns: `sex` (`m`/`f`/`u`) plus one column per item number. Blank cells are
    treated as unanswered rather than as zero, which is the same refusal to
    invent data that `scoring` makes.
    """
    rows: list[dict[int, int]] = []
    sexes: list[str] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(line for line in handle if not line.startswith("#")):
            answers: dict[int, int] = {}
            for key, value in row.items():
                if key is None or not key.strip().isdigit():
                    continue
                text = (value or "").strip()
                if text:
                    answers[int(key.strip())] = int(text)
            rows.append(answers)
            sexes.append((row.get("sex") or "u").strip().lower() or "u")
    return rows, sexes


def cmd_group(args: argparse.Namespace) -> int:
    rows, sexes = _read_group_csv(args.file)
    result = group_analysis(rows, sexes)
    _emit(
        {
            "n": result.n,
            "n_by_sex": result.n_by_sex,
            "n_excluded_incomplete": result.n_excluded_incomplete,
            "chart_sex": result.chart_sex,
            "scales": result.scales,
            "risk_a": result.risk_a,
            "risk_b": result.risk_b,
            "total_risk": result.total_risk,
            "coefficients_verified": result.coefficients_verified,
            "notes": list(result.notes),
        }
    )
    return EXIT_OK


def cmd_gate_check(args: argparse.Namespace) -> int:
    text = args.text if args.text is not None else _read_text(args.file)
    with SentinelService(args.db) as service:
        report = service.gate_check(text)
    _emit(report)
    return EXIT_OK if report["ok"] else EXIT_REFUSED


def cmd_serve(args: argparse.Namespace) -> int:
    with SentinelService(args.db) as service:
        serve(service, port=args.port)
    return EXIT_OK


def cmd_kpi(args: argparse.Namespace) -> int:
    with SentinelService(args.db) as service:
        _emit(service.kpi())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description=(
            "法定ストレスチェックの決定論コアと、その上のゲート・HITL・監査。"
            " 外部への通信は行いません。"
        ),
    )
    parser.add_argument("--version", action="version", version=f"sentinel {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_score = subparsers.add_parser("score", help="1件の回答を採点する（純関数）")
    p_score.add_argument("--variant", default="57", choices=["57", "23"])
    source = p_score.add_mutually_exclusive_group(required=True)
    source.add_argument("--answers", help='JSON文字列。例: \'{"1": 2, "2": 3}\'')
    source.add_argument("--answers-file", help="JSONファイルのパス。'-' で標準入力")
    p_score.set_defaults(func=cmd_score)

    p_group = subparsers.add_parser("group", help="集団分析（10人未満は拒否）")
    p_group.add_argument("--file", required=True, help="1行1名のCSV。列: sex と 項目番号")
    p_group.set_defaults(func=cmd_group)

    p_gate = subparsers.add_parser(
        "gate-check", help="文面を全ゲートに通す（差し止めなら終了コード1）"
    )
    gate_source = p_gate.add_mutually_exclusive_group(required=True)
    gate_source.add_argument("--text", help="検査する文字列")
    gate_source.add_argument("--file", help="検査するファイル。'-' で標準入力")
    p_gate.add_argument("--db", default=":memory:", help="SQLiteのパス（既定はメモリ）")
    p_gate.set_defaults(func=cmd_gate_check)

    p_serve = subparsers.add_parser("serve", help="ローカルHTTPサーバを起動する")
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_serve.add_argument("--db", default="sentinel.db", help="SQLiteのパス")
    p_serve.set_defaults(func=cmd_serve)

    p_kpi = subparsers.add_parser("kpi", help="レビュー統計（オーバーライド率など）")
    p_kpi.add_argument("--db", default="sentinel.db", help="SQLiteのパス")
    p_kpi.set_defaults(func=cmd_kpi)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (SentinelError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"sentinel: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
