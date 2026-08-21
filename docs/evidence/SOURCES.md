# 一次資料マニフェスト (evidence PDFs)

`data/` 配下の CSV は、ここに列挙した PDF から書き起こしている。

**PDF 本体は git 管理外** (`.gitignore` で `docs/evidence/*.pdf` を除外)。合計 37MB あり、
いずれも公開されている配布物で、再取得できるため。代わりに **sha256 先頭16桁で版を固定**する。
CSV のヘッダが `docs/evidence/<name>.pdf` を参照している時は、この表の当該行の版を指す。

取得日: 2026-08-21。ページ数・サイズ・ハッシュは同日の実測値。

**頁番号の凡例**: 本表の「頁」列と、他資料 (EVALS_RUN 等) の `p.N` 参照は、いずれも **PDF の物理ページ番号** (1 始まり・表紙を p.1 とする通し番号) を指す。冊子に印刷された版面ノンブル (印刷頁) とは一致しないことがあるため、参照は PDF 頁で統一する。印刷頁を指す必要がある場合はその旨を明記する。

| ファイル | 頁 | サイズ | sha256 (先頭16) | 出所 |
|---|---:|---:|---|---|
| `manual_main.pdf` | 204 | 6602 KB | `3ece73e620716e20` | 厚生労働省「労働安全衛生法に基づくストレスチェック制度実施マニュアル」 https://www.mhlw.go.jp/content/11300000/001671531.pdf |
| `select_num.pdf` | 5 | 239 KB | `e8ccc8fff2cd5b23` | 厚生労働省「数値基準に基づいて『高ストレス者』を選定する方法」 ストレスチェック等実施プログラム配布サイト https://stresscheck.mhlw.go.jp/ 経由で取得 |
| `tmu_manual2.pdf` | 32 | 1644 KB | `705792cfd43c6c3f` | 東京医科大学公衆衛生学分野「職業性ストレス簡易調査票マニュアル」 https://www.tmu-ph.ac/news/data/manual2.pdf (配布ページ https://www.tmu-ph.ac/news/stress_table.php) |
| `manual_small.pdf` | 48 | 15343 KB | `88757816bd013cd9` | 厚生労働省 実施マニュアル (小規模事業場向け)。取得元 URL 未記録 |
| `mh_shishin.pdf` | 14 | 334 KB | `dcdb117d86c90048` | 厚生労働省「労働者の心の健康の保持増進のための指針」(メンタルヘルス指針。平成18年3月31日 健康保持増進のための指針公示第3号 / 改正 平成27年11月30日 同第6号)。取得元 URL 未記録 |
| `shudan.pdf` | 8 | 585 KB | `62ec263b26bf070c` | 厚生労働省 集団分析関連資料。取得元 URL 未記録 |
| `mensetsu.pdf` | 56 | 1958 KB | `1553c91da3dcaa90` | 厚生労働省 面接指導関連資料。取得元 URL 未記録 |
| `chosa07.pdf` | 25 | 1068 KB | `85fafc9d01b8b018` | 労働安全衛生調査。取得元 URL 未記録 |
| `kaisei.pdf` | 5 | 2172 KB | `dfeddb6e97b6c578` | 法改正関連資料。取得元 URL 未記録 |
| `leaflet50.pdf` | 4 | 384 KB | `d81cbba6d6eae10b` | 50人未満事業場向けリーフレット。取得元 URL 未記録 |
| `list_sample.pdf` | 15 | 3099 KB | `70194c49574b2672` | 様式例。取得元 URL 未記録 |
| `notice1.pdf` | 2 | 87 KB | `6b2064891fba4d95` | 通達。取得元 URL 未記録 |
| `notice2.pdf` | 2 | 563 KB | `2084db55d0279a23` | 通達。取得元 URL 未記録 |
| `pdl.pdf` | 3 | 202 KB | `48153b4bf7130d07` | デジタル庁 公共データ利用規約 (PDL1.0) 解説。取得元 URL 未記録 |
| `en57.pdf` | 2 | 83 KB | `10416486df72422a` | 職業性ストレス簡易調査票 英語版。取得元 URL 未記録 |
| `sc80_j.pdf` | 5 | 256 KB | `d91a528726046e75` | 新職業性ストレス簡易調査票 (80項目) 日本語版。取得元 URL 未記録 |
| `sc80_total.pdf` | 7 | 369 KB | `d26a50fe7b48fc9d` | 同 80項目版 資料。取得元 URL 未記録 |
| `sc80_validity.pdf` | 11 | 1612 KB | `16b05ea14048d81e` | 同 80項目版 妥当性資料。取得元 URL 未記録 |
| `Scoring_K6_K10.pdf` | 2 | 129 KB | `936052ab2ea9cd7c` | Harvard (Kessler) K6/K10 公式 FAQ。取得元 URL 未記録 |
| `ILO_psychosocial_working_environment_exec_summary_2026.pdf` | 5 | 203 KB | `093e1b63830a6484` | ILO "The psychosocial working environment: Global developments and pathways for action" Executive Summary (2026)。CC BY 4.0 (c) ILO 2026。取得元 URL 未記録 |

> **2026-08-21 訂正**: `mh_shishin.pdf` の説明を「ストレスチェック指針」から実際の表題へ直した。
> 全 URL の再取得確認をした際に、手元の PDF の 1 頁目が別の指針であることが分かったもの
> (`labour_shishin` と `mh_shishin` は名前が近く、取り違えていた)。
> **ストレスチェック指針**の正しい表題は「心理的な負担の程度を把握するための検査及び面接指導の
> 実施並びに面接指導結果に基づき事業者が講ずべき措置に関する指針」で、こちらは手元に PDF を
> 置いておらず、[ADR-0003](../adr/ADR-0003-hitl-implemented-in-house.md) が
> <https://www.mhlw.go.jp/content/11300000/001676923.pdf> を直接引いている
> (同 URL は再取得確認済み・7-(2) の逐語が `PRIOR_ART_REPORT_2026-08-21.md:55` と一致)。
> 誤っていたのはこの表のラベル 1 行だけで、論証側は正しい文書を引いていた。

## 「取得元 URL 未記録」について

これらは 2026-08-21 の調査時に取得済みで手元にあるが、**URL が記録として残っていない**。
心当たりの URL を書けば表は埋まるが、それは検証できない情報を検証済みの表に混ぜることになる
ので、埋めていない。sha256 があるので、後日 URL が判明した時に「同じ版か」は機械的に確認できる。

現時点で `data/` の CSV が実際に典拠にしているのは上位 3 件 (`manual_main` / `select_num` /
`tmu_manual2`) と、URL 確認済みの厚労省「まもろうよ こころ」 https://www.mhlw.go.jp/mamorouyokokoro/
だけである。残りは背景資料。

## ライセンス

mhlw.go.jp のコンテンツは公共データ利用規約 (第1.0版) = CC BY 4.0 互換で、出典明示と
「編集・加工した旨」の記載により商用利用まで可能。ILO 文書は CC BY 4.0。
本リポジトリはこれらの文書を再配布せず、`data/` の CSV に書き起こした内容の出典として参照する。
