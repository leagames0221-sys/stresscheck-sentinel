# evals/

要件 R5 の実体。「AI に何を指示したか」ではなく「出てきたものが正しいと言える根拠は何か」を置く場所。

`evals/` は製品 (`src/sentinel/`) を import してよいが、製品が `evals/` を import することはない
(`docs/spec/design.md` §2 Boundary/Depends)。依存は一方向。

## 3つの層と、それぞれが持つ証拠の種類

| 層 | 対象 | 判定者 | ゴールドセット |
|---|---|---|---|
| 層1 決定論 | 57項目/23項目の採点と高ストレス判定 | `score()` を直接呼ぶ python assertion | `goldsets/scoring_boundary.csv` (47件) |
| 層1' 敵対的 | 危機検知と SaMD 禁止表現 lint が **生成より先に** 落とすか | `classify()` / `lint()` を直接呼ぶ python assertion | `goldsets/adversarial.csv` (35件) |
| 層2 品質 | セルフケア文面の共感・具体性・非診断・窓口案内 | llm-rubric × **ローカル judge 2モデル** + 出力への SaMD lint | `goldsets/selfcare_quality.csv` (10件) |
| 層3 安全 | 危機会話の5次元評価 | `vera_mh_ja/` (rubric + ペルソナ + 非対称スコア) | `vera_mh_ja/personas_ja.tsv` (20件) |

**層をまたいで合否を委ねない。** 安全性の可否は層1'の決定論ゲートが持ち、judge は品質だけを採点する。
判断根拠は `docs/evidence/PRIOR_ART_REPORT_2026-08-21.md` §規制・安全6 (LLM-judge は共感・安全性の軸が弱いという実測) と R5-3。
層2 の各テストには llm-rubric 8本に加えて `assert_output_lint.py` が付いていて、
judge が通した文面でも lint が block すれば、そのテストは落ちる。

## 走らせ方

### 層1・層1' (node も Ollama も不要。これが CI で回る側)

```bash
python evals/run_deterministic.py            # 集計のみ
python evals/run_deterministic.py --verbose  # 全ケース1行ずつ
```

終了コードだけが「全部通った」の根拠になる。散文の報告は根拠にならない。

このスクリプトは、ゴールドセットの期待値を `score()` と突き合わせるだけでなく、
`_common.independent_sums()` — 品目CSVと閾値CSVだけを読み、`scoring.py` を知らない別実装 — でも合計点を計算し直す。
ゴールドセット・別実装・製品の3者が一致したときだけ緑になる。
あわせて「逆転換算の有無で判定が変わる行が存在するか」も検査する。0件なら、そのゴールドセットは
逆転項目を一度も試していないので、緑ではなく赤にする。

### 層1・層1'・層2 をまとめて (promptfoo + ローカル Ollama)

```bash
PROMPTFOO_DISABLE_TELEMETRY=1 npx -y promptfoo@latest eval -c evals/promptfooconfig.yaml --no-cache -j 2
```

- `sharing: false` を config に明記。**クラウドへ送らない** (R7-2)。`promptfoo share` は使わない。
- テレメトリは上記の環境変数で切る。
- judge は `ollama:chat:gemma3:4b` と、日本語に強い 8B のローカルモデルの2本。
  provider も judge も `http://localhost:11434` のみ。課金要素ゼロ。
- 層1の行は `echo` provider (プロンプトをそのまま返し、モデルを呼ばない) で走る。
  決定論の行を増やしても推論は1回も増えない。
- `-j 1` を推奨。2モデルを交互に呼ぶと consumer laptop では毎回モデルの入れ替えが起きる
  (実測: 8B の初回ロードだけで 34.7 秒)。

### judge 2本の一致度 (R5-3)

```bash
python evals/judge_agreement.py <promptfoo の -o で出した JSON>
```

judge を2本走らせて合格率だけを見るのは、judge 1本のときと同じ証拠にしかならない。
基準ごとに単純一致率・Cohen's kappa・Krippendorff's alpha と、**割れた個別の行**を出す。
一致度は合格の基準ではない (2本が揃って間違うことはある)。

### 層3 のスコアラー

```bash
python evals/vera_mh_ja/score.py --selftest
```

詳細は `vera_mh_ja/README.md`。

## ファイル

```
evals/
├─ promptfooconfig.yaml      # 3層を1ファイルに。sharing:false / providers は Ollama のみ
├─ run_deterministic.py      # 層1・層1' を promptfoo 抜きで実行 (CI 用)
├─ tests_deterministic.py    # goldsets CSV -> promptfoo テストケース生成
├─ assert_scoring.py         # 層1 assertion: score() を直接検査
├─ assert_gates.py           # 層1' assertion: classify() / lint() を直接検査
├─ assert_output_lint.py     # 層2 assertion: 生成文が SaMD lint を通るか
├─ judge_agreement.py        # promptfoo の出力 JSON から judge 2本の一致度を出す (R5-3)
├─ _common.py                # goldset 読込 + 独立再計算 (製品を知らない側の実装)
├─ prompts/selfcare_ja.txt   # 層2 のプロンプト
├─ goldsets/
│  ├─ scoring_boundary.csv   # 判定境界・欠損・逆転・入力検証
│  ├─ adversarial.csv        # 診断要求 / リスク% / 危機表現 / 窓口質問 / 指示の上書き / 良性対照
│  └─ selfcare_quality.csv   # 層2 シナリオ (合計点は層1の実在ケースから取っている)
└─ vera_mh_ja/               # VERA-MH 日本語版 (rubric / ペルソナ / 非対称スコア)
```

## ゴールドセットの期待値はどこから来たか

- `scoring_boundary.csv` — 厚生労働省「数値基準に基づいて『高ストレス者』を選定する方法」
  (`docs/evidence/select_num.pdf` p.1) と実施マニュアル (`docs/evidence/manual_main.pdf` p.46-47) の判定基準文から導出。
  **実装の出力を採録したものではない** (それをやると、ゴールドセットは実装が壊れても一生緑になる)。
- `adversarial.csv` — `data/crisis_taxonomy.csv` と `data/samd_forbidden.csv` に書かれた規則から導出。
- `selfcare_quality.csv` — 合計点は `scoring_boundary.csv` の実在ケースの領域合計をそのまま使用。
  `run_deterministic.py` が、各シナリオに書かれた判定文を閾値CSVから再計算して突き合わせる。

## 検査器そのものが働いているかの確認

ゴールドセットの1行をわざと壊すと `run_deterministic.py` は exit 1 になる。
2026-08-21 の実測: `S57-A-02` の期待判定を `true/B_only` → `false/none` に、
`ADV-C-01` の期待危機水準を `ideation` → `none` に書き換えたところ、
その2件だけが赤になり (`105/107`)、戻すと `107/107` exit 0 に復帰した。
記録は `docs/evidence/EVALS_RUN_2026-08-21.md`。
