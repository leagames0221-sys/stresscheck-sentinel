# stresscheck-sentinel — Design (Spec-Driven stage 3)

> 起草 2026-08-21。requirements.md の R 番号に接地。根拠 = docs/evidence/PRIOR_ART_REPORT_2026-08-21.md (PAR)

## 1. スタック決定 (ADR-0002)

**Python 3.12 / ランタイム依存ゼロ (`dependencies = []`)**。
- 採点・ゲート・HITL・監査 = stdlib のみ (sqlite3, http.server, urllib, csv, hashlib)
- Ollama は HTTP (urllib) で直叩き = SDK 不要
- devDep のみ: pytest + ruff (Python)、promptfoo (npm, evals 専用)
- 代替 TS を退けた理由: HITL/評価系のひな形 (PsyGUARD, VERA-MH, Inspect) が Python 系で写像コストが最小。標準実装方針の「型・境界チェックを結合する」は `pytest + ruff` (banned-api による層規則) で満たす。

## 2. File Structure Plan (2層アーキ: domain 非依存 core + domain pack)

```
stresscheck-sentinel/
├─ src/sentinel/
│  ├─ core/                    # domain 非依存 (JSQ を知らない)
│  │  ├─ gates.py              # Gate ABC / GateResult / GateChain。起動時検証: 辞書0件=ValueError (R3-G4)
│  │  ├─ audit.py              # append-only 監査ログ (SQLite, hash連鎖)。承認記録は会話と分離 (ADR-0006相当のMS採択理由)
│  │  ├─ hitl.py               # interrupt/resume 状態機械 + 4決定型 approve/edit/reject/respond + override KPI (R6)
│  │  └─ llm.py                # LLMProvider ABC + OllamaProvider + NullProvider (定型文, 既定) (R4-2)
│  ├─ packs/
│  │  ├─ jsq/                  # 職業性ストレス簡易調査票 pack
│  │  │  ├─ scoring.py         # pure function。逆転→領域合計→判定 (R1)
│  │  │  └─ group.py           # ストレス判定図 (R2)。n<10 拒否
│  │  ├─ crisis/classify.py    # 段階判定木 (PsyGUARD 写像): 探索→意図→計画→準備 (R3-G3)
│  │  └─ samdlint/lint.py      # 禁止表現 lint (R3-G2)
│  ├─ app/                     # stdlib ThreadingHTTPServer + JSON API + static/ (受検UI/実施者レビューUI)
│  └─ cli.py                   # score / group / gate-check / serve / kpi
├─ data/                       # 全て出典ヘッダ付き CSV (PDL1.0 帰属表示)
│  ├─ jsq_items_57.csv / jsq_items_23.csv
│  ├─ jsq_thresholds.csv       # 判定閾値 (R1-3/R1-6: 差し替えのみでカスタマイズ)
│  ├─ crisis_taxonomy.csv / hotlines_ja.csv   # まもろうよこころ掲載窓口
│  └─ samd_forbidden.csv       # 禁止表現辞書
├─ evals/
│  ├─ promptfooconfig.yaml     # 決定論 assertion + llm-rubric (Ollama judge×2)
│  ├─ goldsets/                # scoring_boundary.csv (境界値網羅) / selfcare_quality.csv / adversarial.csv
│  └─ vera_mh_ja/              # rubric_ja.tsv + personas_ja.tsv + score.py (非対称スコア)
├─ tests/                      # pytest。prompts.yaml 必須トークン検査 (R4-3) 含む
├─ prompts/prompts.yaml        # 全プロンプト外出し (R4-3)
├─ docs/{spec, adr, evidence}
└─ scripts/                    # private_path_check.py 等、セキュリティ第一の標準セット
```

**Boundary/Depends**: `core` → 依存なし / `packs` → core のみ / `app`・`cli` → core+packs / `evals` → 外部 (promptfoo、製品コードに非依存)。逆方向 import は ruff で禁止 (lint ルール)。

## 3. データフロー (1 受検の一本道)

```
回答(1-4×57) → [R1 scoring (pure)] → 高ストレス候補
  → [R4 LLM任意層: 通知文の下書き (Ollama or 定型文)]
  → HITL キュー (interrupt) → 実施者UI: 実文面を見て approve/edit/reject/respond
  → [R3-G1 署名レコード (audit)]
  → release: GateChain.check を通す = crisis → samd_lint → signature の固定順
  → 本人へ表示
```
> ゲートの評価順は実装の SSoT = `core/gates.py` の `GATE_ORDER = ("crisis", "samd_lint", "signature")`。
> `GateChain` は与えられた順に関係なくこの順へ整列し、最初の refusal で短絡する。危機を最初に置くのは、
> パイプラインの他が満たされているかどうかに関わらず危機の本人が窓口へ到達できるようにするため。
> `app/service.py::release` はこの 1 本の chain を通してからでないと本人向けの文面を返せない。
> 署名ゲートは result_review 段階の署名 (payload_hash × stage の複合照合) のみで解錠される
> — crisis_review の承認は同じ payload_hash を持つが段階が違うので結果を解放しない (F1)。
>
> 文面の流れは「LLM 下書き → 実施者レビュー(署名) → 表示」: edit/respond は実文面がないと
> 成立しないため、生成を実施者レビューの前に置く。R3-G1 の本質 (署名なしに本人へ流れない) は
> release 時の signature gate で不変に担保する。
危機検知はどの段階でも最優先割込み: 検知 → LLM bypass → 窓口固定応答 (決定論)。

## 4. ADR 一覧 (canonical 4-field で個別ファイル化)

| ADR | 決定 | 退けた代替 (trade-off) |
|---|---|---|
| 0001 | prior-art 監査結果の固定 (PAR 全文を根拠化) | — (ひな形調査の結論を ADR-0001 として据え、以降の判断の起点にする方針そのもの) |
| 0002 | Python 3.12 + ランタイム依存ゼロ | TS (evals ひな形との写像コスト大) / FastAPI (依存増・stdlib で足りる) |
| 0003 | **HITL 自作** — LangGraph `interrupt()` 意味論 (中断=例外/再開=保存値/id=位置決定論hash) + LangChain v1 4決定型を stdlib で写像 | langgraph 依存採用 (依存6個は健全だが標準実装方針「依存ゼロ」を破る。写像実装+ADR 引用の方が framework 理解の証明になる) |
| 0004 | 欠損 = 判定保留 (補完しない) | 最頻値補完 (K6 FAQ は「開発者が指図しない」= 法定判定では保守側が正) / MI (過剰) |
| 0005 | LLM = Ollama (gemma3:4b 系) + **NullProvider 既定** = LLM なしが default-safe | Workers AI (無料枠だが外部送信が R7-2 と衝突) |
| 0006 | 安全評価 = 決定論ゲート先行 + 日本版 VERA-MH + judge×2 IRR + 非対称スコア | LLM-judge 単独 (MentalAlign-70k で安全軸の弱さが実測 = 不採用) |
| 0007 | SaMD 非該当維持 = 機能設計 (禁止語 lint + 表示制限) | 免責文言 (医療機器該当性ガイドライン 注記12 が明文で無効化 = 不採用) |

## 5. HITL 状態機械 (core/hitl.py、R6)

```
PENDING --(approve)--> SIGNED           # 署名レコード生成 → 下流解禁
        --(edit)-----> SIGNED(edited)   # 差分を audit に保存
        --(reject)---> REJECTED         # status=error 相当。再試行抑止
        --(respond)--> SIGNED(manual)   # AI出力を使わず実施者所見をそのまま採用
```
- interrupt id = `sha256(受検token + stage名)` 先頭16 = 冪等 (LangGraph の xxh3(checkpoint_ns) 写像)
- 状態は SQLite。副作用 (通知組み立て) は SIGNED 遷移後のみ = 再開二重実行なし (R6-2)
- override KPI = `1 - approve/全決定` を cli `kpi` と実施者UIに表示 (NIST MS-4.2-004)

## 6. Evals 設計 (evals/、R5)

- **層1 決定論**: 採点境界値 goldset (B=76/77、A+C=75/76×B=62/63、欠損、全ケース男女) → promptfoo `python` assertion で pure function を直検査
- **層2 LLM 品質**: セルフケア文面 goldset → llm-rubric (共感・具体性・非診断) を Ollama judge 2 モデルで採点、一致度 (IRR) を報告に出す
- **層3 安全**: vera_mh_ja — VERA-MH 5次元 rubric 日本語化 + ペルソナ日本語化 (窓口=hotlines_ja.csv) + 非対称スコア。敵対的セット (診断を求める・危機表現・境界を越えさせる指示) は samdlint/crisis の**決定論ゲートが先に落とす**ことを assert (= LLM-judge に安全合否を委ねない)
- CI (GHA): 層1 は毎 push。層2/3 は Ollama 必要のためローカル実行 + 結果 JSON を docs/evidence に commit する運用 (CI では検査済み JSON の整合のみ検証)

## 7. プロジェクト制約への適合 (再確認)

このリポジトリは 4 つの自己課した制約 (project constraints) の下で作る。

| 制約 | 適合 |
|---|---|
| **無料 (Free)** | 採用 source は全て無償・再配布可 (MIT / Apache-2.0 / 公共データ利用規約 1.0 / CC BY 4.0)。帰属表示は `data/` の各ファイル冒頭ヘッダと README。 |
| **クレカ不要 (No card on file)** | 課金要素ゼロ。アカウント登録も API キーも要らない。 |
| **ローカル LLM 優先 (Local-first LLM)** | 決定論 core + Ollama 任意層 + NullProvider 既定。モデルが無い環境でも全機能が動く。 |
| **セキュリティ第一 (Security-first)** | 公開/非公開の 2 系統 .gitignore + gitleaks + pre-commit + GitHub Actions + 依存を増やさず「読んで自作」。install するのは開発用の promptfoo のみで、langgraph 不採用によりランタイム install はゼロ。 |

## 8. 標準実装方針 (この規模の repo で毎回踏む形)

依存ゼロ / 決定論 core + LLM 任意層 / provider を ABC で抽象し環境変数で差し替え / 型・境界チェックを CI に結合。
新しいランタイム依存を足す時は ADR を 1 本書く (`pyproject.toml` の `dependencies = []` にその旨コメント済)。
