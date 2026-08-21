# ADR-0002: Python 3.12 / ランタイム依存ゼロ

- Status: Accepted
- Date: 2026-08-21

## Context

このツールが扱うのは、事業場の従業員が書いた心理的負担の回答である。動く場所は
「1 台のマシン・1 事業場・外部送信なし」を想定していて、クラウドも認証基盤も前提にできない。

その条件下では、依存の数がそのまま次の 3 つのコストになる。

1. **供給網リスク** — install script と依存連鎖は、2025 年以降に実際に悪用されている経路。
   ランタイム依存が 0 なら、その経路は物理的に存在しない。
2. **導入の摩擦** — 産業医や衛生管理者の PC で「まず 40 個入れてください」は成立しない。
3. **寿命** — 3 年後に `pip install` が通らなくなる確率は、依存の数に比例して上がる。

必要な部品はすべて標準ライブラリにある: `csv` (データ)、`sqlite3` (HITL・監査)、
`hashlib` (hash 連鎖)、`http.server` (UI)、`urllib` (Ollama への HTTP)。
Ollama は素の HTTP API なので SDK が要らない。

## Decision

**Python 3.12、`dependencies = []`。ランタイム依存はゼロ。**

- 開発用のみ `pytest` + `ruff` (`[project.optional-dependencies].dev`)。
- 評価用のみ `promptfoo` (npm、`npx` 実行、リポジトリには入れない)。
- YAML パーサも入れない。`prompts.yaml` は `core/miniyaml.py` (この用途に必要な部分集合のみ) で読む。
- **新しいランタイム依存を足す時は ADR を 1 本書く。** その旨を `pyproject.toml` の
  `dependencies = []` 行の直上にコメントとして置いてある (規約を人の記憶に置かない)。

## Alternatives considered

| 代替 | trade-off |
|---|---|
| **TypeScript / Node** | UI 側は書きやすい。しかし採用したひな形 (PsyGUARD の判定木、VERA-MH の rubric とスコア式、Inspect の judge 妥当性検証) が全て Python 圏にあり、写像コストが最大になる。評価系こそがこのツールの主題なので、主題側の摩擦を上げる選択になる。 |
| **FastAPI + Pydantic + SQLAlchemy** | 開発は速く、OpenAPI も出る。代償はランタイム依存が 0 → 20 前後になること。得られるのは主に「書きやすさ」で、このツールの主張 (出力と境界の信頼性) には効かない。UI は 3 画面・API は 9 エンドポイント (GET 6 + POST 3、`docs/spec/api_contract.md` の HTTP 節に一覧) で、`http.server` で足りることを実装して確認した。 |
| **PyYAML を 1 つだけ入れる** | 依存 1 個は安い。しかし「1 個だけ」は必ず 2 個目を呼ぶ。`prompts.yaml` に必要なのは入れ子マッピングと複数行文字列だけで、`miniyaml.py` は 200 行未満で済んだ。YAML 全仕様には対応しない (対応しないことを README とテストで明示している)。 |
| **Python 3.10 以上に広げる** | 対象環境が広がる。代わりに `X | Y` 型記法や `match` を諦めるか、後方互換の分岐を書くことになる。3.12 を切って困る相手が現時点で観測できないため、狭い方を選んだ。 |

## Consequences

- **良い**: `git clone` してそのまま `python -m sentinel.cli` が動く。install 不要
  (`PYTHONPATH=src`)。オフラインで完結し、外向きの通信は Ollama を明示的に有効にした場合の
  `http://localhost:11434` だけ。
- **良い**: CI が速く、lockfile の管理と依存の脆弱性追随が不要。
- **悪い**: 標準ライブラリで書く分、コード量は増えた。`miniyaml.py`・`http.server` の
  ルーティング・SQLite のスキーマ管理は、フレームワークなら書かなかった行である。
- **悪い**: `http.server` は本番の web サーバではない。**この選択は「1 台・loopback のみ」の
  配備モデルとセットでしか正当化されない** (`app/server.py` は `127.0.0.1` を定数として持ち、
  バインド先を引数にしていない)。配備モデルが変わればこの ADR ごと見直しになる。
- **悪い**: 認証機構が無い。フレームワークを採れば既製品が付いてきた部分で、
  ここは v1 の限界として README に明記している。

## Sources

- `pyproject.toml` (`dependencies = []` とその上のコメント)
- `src/sentinel/core/miniyaml.py` / `src/sentinel/app/server.py` (`BIND_HOST = "127.0.0.1"`)
- `docs/evidence/PRIOR_ART_REPORT_2026-08-21.md` §採用 source セキュリティ判定
- `src/sentinel/core/llm.py` (`DEFAULT_OLLAMA_HOST` / `urllib` で `/api/chat` を直叩き = SDK 不要)
