# ストレスチェック×AI ポートフォリオ — ひな形調査報告 (prior art scan)

調査日: 2026-08-21
一次資料PDF 18本: `evidence_pdfs\` (取得時sha: manual_main `3ece73e620716e20` / select_num `e8ccc8fff2cd5b23` / notice1 `6b2064891fba4d95` / manual_small `88757816bd013cd9` / sc80_total `d26a50fe7b48fc9d`)

## 結論

**完全一致のひな形は存在しないが、必要部品は全て無償・実装可能ライセンスで揃う。**
日本語圏の「ストレスチェック×LLM」OSS は実質ゼロ = 最大の差別化余地。

## 決定論コア (法定基準に接地)

- **職業性ストレス簡易調査票 57項目 + 単純合計法** が最適。数値基準が完全公開:
  - 逆転項目: 領域A 1-7,11-13,15 / 領域B 1-3 (1⇔4, 2⇔3)
  - 高ストレス: ㋐ B≥77 または ㋑ A+C≥76 かつ B≥63 (23項目版: ㋐B≥31 / ㋑A+C≥39かつB≥23)
  - ⚠ ㋐:㋑=8:2・約10%は「例」であり事業場が変更可能 (実施者意見+衛生委員会で事業者決定)
  - source: https://stresscheck.mhlw.go.jp/ 配布 select_num.pdf + 実施マニュアル (現行URL: https://www.mhlw.go.jp/content/11300000/001671531.pdf ※旧URL 000533925.pdf は404)
- **ライセンス**: mhlw.go.jp コンテンツ = 公共データ利用規約 PDL1.0 = CC BY 4.0 互換 (デジタル庁解説PDF明記)。出典+編集加工した旨の記載で商用可。公式多言語10版あり。
- **集団分析** = 仕事のストレス判定図 (12項目・男女別・総合健康リスク=(A)×(B)/100・全国平均100・120超で問題示唆)。10人未満は原則提供不可。⚠健康リスク斜線の回帰係数数値表は未取得 (東京医科大の無償Excel https://www.tmu-ph.ac/news/stress_table.php が実装参照先)。
- **法改正 (時事性)**: 令和7年法33号で50人未満も義務化、施行 **2028-04-01**。集団分析改正 (個人識別不可方法) 施行 2027-04-01 (基発0630第2号)。実施率: 50人以上89.8% / 30-49人58.4% / 10-29人55.9% (令和7年労働安全衛生調査) = 小規模層が伸びしろ。
- **厚労省版プログラム Ver.4.0**: 無償だが Windows専用・250MB・**3年利用期限 (2028-11-10失効)**・ソース非公開・80項目非対応 → そのまま差別化軸。二次利用可否の明文なし=参照しない。

### 尺度ライセンス
| 尺度 | 判定 | 根拠 |
|---|---|---|
| PHQ-9 / GAD-7 | 🟢 PD明言・実装可 | phqscreeners Instruction Manual p.8 "in the public domain"。PHQ-9公式日本語版あり / **GAD-7公式日本語版は無い** (75言語列挙確認) |
| K6 | 🟡 無償・許諾不要 (著作権Kessler保持) | Harvard公式。日本語=Furukawa 2008。**採点は逆転recode必須**。cut-off 13+ |
| WHO-5 | 🔴 **外す** | 2024年WHOへ権利移管、CC BY-NC-SA 3.0 IGO = NC+継承がライセンス方針を汚染。cut-offは「50未満」(50以下ではない) |
| ISO 45003 | 🔴 **使用不可** | 有償 CHF155 + ISO利用許諾がソフトウェア埋め込み/AI利用を明文禁止。JIS化もされていない (JSA表2025-04-01)。書誌言及のみ |
| WHO mental health at work guidelines 2022 | 🟡 参照のみ | CC BY-NC-SA 3.0 IGO。公式日本語版あり (IRIS 9789240053052-jpn)。data/who/ に分離しライセンス明示 |

## 採用ひな形 (全て「依存にせず読んで自作」= decomposed prior art)

| 部品 | 出所 | ライセンス | 抽出ポイント | red flag |
|---|---|---|---|---|
| 採点エンジン | code4fukui/stress-check | MIT | **計算式をCSVデータ化** (`15-(N1+N2+N3)`型を小型式評価器でparse) | ⭐0 / **性別判定バグ** (`length>=57\|\|`が常にtrue→女性列不使用) / import時外部HTTP / テスト2件のみ |
| 設問データ | code4fukui/qr-survey | MIT | 57問本文+選択肢CSV | ⭐0 |
| プライバシー設計 | sixteenthmoon/stresscheck-r8 | Apache-2.0 | **「個人情報を持たない」token-onlyスキーマ** (SECURITY.md/DB_SCHEMA.md)。設計文書一式の構成 | ⭐0 / WordPress前提=文書のみ抽出 |
| アプリ骨格 | KazKozDev/cbt-assistant | MIT | Ollama+FastAPI+SQLite+Markdown RAG / PHQ-9・GAD-7組込 / **SOSツール=危機時に決定論フローへ** / prompts.yaml外出し+test_prompts.py | ⭐11 / **プロンプト全文ロシア語+露窓口番号ハードコード** / edge-tts外部通信 |
| 危機判定木 | qiuhuachuan/PsyGUARD | MIT | 段階判定木 (意図→計画→準備→未遂、自傷/他害分離)。**前段=決定論分類器、LLMは後段** | ⭐24 / 中国語タクソノミ / データ側ライセンス未確認 |
| 危機Evals | SpringCare/VERA-MH | MIT改変 | **5次元rubric** (Detect/Confirm/Guide to Human/Supportive/AI Boundaries) + 100ペルソナTSV + **非対称スコア** `(50+%BP/2)×(1−%HPH/100)²`。**人間検証済 (臨床家IRR 0.77 / judge IRR 0.81** = arXiv 2602.05088) | 米国窓口前提→**日本版化が独自成果物** |
| HITL | langchain-ai/langgraph + langchain v1 middleware | MIT | `interrupt()` (中断=例外/再開=保存値/id=位置の決定論ハッシュ) + **4決定型** `approve/edit/reject/respond` + `when:`条件ゲート + **空decisions起動時ValueError** (ゲート黙殺事故防止)。checkpoint-sqlite=SQLite1ファイル・依存6個 | 再開時ノード先頭から**再実行** (副作用はinterrupt後か冪等に) / prebuilt HumanInterruptはdeprecated |
| Evals CLI | promptfoo | MIT ⭐24k | **Ollamaをローカルjudge化公式対応**。決定論assertion+llm-rubricが同一YAML同居。ゴールドセット=CSV `__expected` | **2026-03 OpenAI買収** (MIT維持明言・活発) |
| judge妥当性検証 | UKGovernmentBEIS/inspect_evals healthbench | MIT | `meta_evaluation.py` = judge採点 vs 医師多数決の **macro F1** — 「人手ゼロで審判信頼性を示す」一次実装 | Python前提 |
| 承認監査ログ | microsoft/agent-framework ADR-0006 | MIT | 「承認記録は会話履歴でなく独立監査ログに」の採択済ADR | 実装は借りない |

### 不採用 (理由付き)
- KokoroChat (電通大): **CC BY-NC-ND 4.0 = 使用不可**。構造 (20次元Likertフィードバック/品質層別) のみ参考。GitHub API spdx=NOASSERTIONの罠。
- HumanLayer: ⭐11.3k だが **README全文が廃止宣言**。star≠健全性の典型。
- Ragas (6ヶ月停止) / openai/evals (課金前提) / DeepEval (既定UXがクラウド送信誘導) / Langfuse・MLflow (consumer laptopに過剰) / NeMo Guardrails (過剰) / EmoLLM・MentaLLaMA等の中国語モデル群 (重みが別ライセンス+non-clinical限定)。
- 厚労省版プログラム: exe配布のみ・ソース非公開・二次利用可否不明 = 参照せず公開マニュアルから独立実装。

## 規制・安全の判断境界 (設計の背骨)

1. **実施者境界 (ストレスチェック指針 7-(2) 逐語)**: AIに委譲可=「調査票の回収、集計若しくは入力又は受検者との連絡調整等の実施の事務」/ 委譲不可=「面接指導を受ける必要があるか否かを確認しなければならない」(実施者=医師等)。→ **実施者の確認署名なしに結果が下流へ流れない機械ゲート**。source: https://www.mhlw.go.jp/content/11300000/001676923.pdf
2. **SaMD非該当維持 (厚生労働省 医療機器該当性ガイドライン / PMDA サイト掲載)**: 「疾病リスク◯%」表示=診断側に倒れる (別紙2)。**免責文言は根拠にならない (注記12)**。「セルフケア」という語はガイドラインに出現しない — 非該当類型(3)(4) (自己の健康情報の閲覧・低リスク) で書く。機能単位の該当性判定表を新機能追加時に必ず通す。source: https://www.pmda.go.jp/files/000240233.pdf
3. **職場での感情推論の回避**: EU AI Act 5(f) 原則禁止 + OpenAI policy同旨。「感情を推論してラベルを貼る」でなく「本人が自分の状態を記録・閲覧する」形に寄せる = SaMD非該当類型(3)と同方向。
4. **ウェルネス/臨床の線**: Anthropic AUP はウェルネス指導 (睡眠・ストレス・栄養・運動) を高リスクから明示除外。臨床助言に踏み込むと有資格者事前レビュー要件が発動。
5. **危機対応**: 検知→決定論エスカレーション (LLM生成に流さない)。窓口は厚労省「まもろうよこころ」経由で参照 (#いのちSOS 0120-061-338 = 24時間365日 等)。APA Health Advisory 2025-11: 代表サンプルの継続的人間監査 = サンプリング監査が標準。
6. **LLM-judgeの限界 (実測)**: MentalAlign-70k = judgeは認知的支援軸は強いが**共感・安全性軸が弱い** → 安全性は決定論ゲート+人間、品質はjudge、の役割分担が実測接地。judge妥当性は MT-Bench (80% agreement) / VERA-MH (IRR 0.81) 方式 = **人間は較正のみ、以降全自動**。

## 調査から導いた構成 (この調査の結論)

「57項目の採点アプリ」を作るのではなく、**法定ストレスチェックフローの上に AI ガバナンス層 (Evals + HITL + 判断境界) を実装して見せるツール**。求人票の中核 (Evals策定・HITL設計・役割/権限/判断境界の設計・プロンプト版管理) と1:1対応し、日本語圏に先行OSSゼロ。

構成: 決定論コア (57項目採点+高ストレス判定+集団分析、純関数+CSV外出し) / LLM任意層 (Ollama: セルフケア文面生成のみ、指針の「結果通知に含めることが望ましい」事項に接地) / 境界ゲート (実施者署名・SaMD禁止表現lint・危機検知→決定論エスカレーション) / Evals (promptfoo + ゴールドセット + 日本版VERA-MH + 非対称スコア + judge妥当性IRR) / HITL (interrupt()+4決定型+オーバーライドKPI+サンプリング監査)。

## 追加の一次資料

1. **Scoring_K6_K10.pdf** (Harvard Kessler 公式 FAQ、2頁、sha `936052ab2ea9cd7c`)
   - K6 採点 = 0-24 (各問 0-4)。**提示順は高頻度→無だが採点は逆転 recode 必須** (0=ストレスなし)。cut point **13+** が SMI 判別の最適 (偽陽性/偽陰性の均衡)。ただし「米国と同じ有病率の集団でのみ最適」と明記。
   - 🔴 **豪州は 1-5 採点 (K6=6-30、cut 19+) の別方式** — 0-24 方式と混同すると 1 点どころか体系ごとずれる。実装時は採点方式を必ず明記。
   - **欠損値の公式ルールは無い** ("We never made recommendations")。保守的選択肢 = 最頻値 ("none of the time"=0) へ recode。→ 欠損処理は自分で設計し ADR に書く領域。
2. **ILO『The psychosocial working environment: Global developments and pathways for action』Executive Summary** (2026-04-28 世界労働安全衛生デー発表、embargo 2026-04-22 解禁済、5頁、sha `093e1b63830a6484`、収録名 `ILO_psychosocial_working_environment_exec_summary_2026.pdf`)
   - 🟢 **Licensed under CC BY 4.0 © ILO 2026** (p.5 奥付) — 商用可・帰属表示のみ。従前の懸念「ILO psychosocial 主要文書 (2016-2022) はライセンス個別確認要」をこの最新報告書が解消。
   - 使える数値: 心理社会的リスク要因による死亡 **84万人/年**、**45M DALYs 喪失/年**、**世界GDP 1.37%/年 喪失**、週48h超労働 35%、暴力・ハラスメント経験 23% (心理的暴力 18%)。デジタル化と **AI がタスクの調整・監視・評価を変容させている**と名指し。
   - 3 層フレーム (The job / How work is managed and organized / Broader policies) + ILO-OSH 2001 を管理枠組みに使う整理 + 予防の優先順位 = 組織的措置が根本、個人向け措置は「補完であって代替ではない」。
   - → README の motivation (なぜ今このテーマか) の国際的アンカーとして採用。集団分析レポートの構成 (3 層) にも写像可。

## 採用 source セキュリティ判定 (2026-08-21 判定)

- **読んで自作する 9 ひな形 = 供給網リスク実質ゼロ**: install もコード実行もしない (テキストとして読むのみ)。Shai-Hulud 級ワームの感染経路 (install script / 依存連鎖) が存在しない。LICENSE は全件本文実読済み。clone した場合もコードは実行しない (clone 自体では git hooks は発火しない)。
- **実際に install する依存は 2 つだけ**: promptfoo (devDep、⭐24k、MIT、活発、OpenAI 買収は governance 注記でありセキュリティ red flag ではない) + langgraph (+checkpoint-sqlite、MIT、LangChain org、依存 6 個)。両方 version pin + lockfile + セキュリティ第一の標準セット (gitleaks/pre-commit/GitHub Actions)。langgraph は標準実装方針「依存ゼロ」の例外になるため ADR 必須 (Design stage で判断)。
- **データファイル採用** (VERA-MH の rubric.tsv/personas.tsv、厚労省 CSV): 非実行データ。取込前に内容目視。
- 判定: **問題なし** (条件 = 上記の「実行しない・install は 2 依存のみ・pin+lock」の運用)。

## 未確認事項 (書く前に要再確認)

- 仕事のストレス判定図の健康リスク数値式 (図のみ・係数表未取得)
- code4fukui の閾値が素点基準 (B≥77等) とどう対応するか (評価点ベース式のため未検証)
- 厚労省 Q&A の「ITシステム自動判定」原文 (指針逐語で代替可)
- RAND/Psychiatric Services 2025 の数値 (本文403・未確認)
- WHO LMMガイダンス個別勧告 / PsyEvalサブタスク内訳 / CounselBench応答数 (出典間不一致)
