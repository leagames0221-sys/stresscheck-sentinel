# Architecture Decision Records

決定の記録であって、設計の説明書ではない。**なぜ他の選択肢を採らなかったか**が書いていない行は、
このディレクトリでは決定ではなく感想として扱う。

形式は 4 節固定 (Michael Nygard 形式):

| 節 | 何を書くか |
|---|---|
| **Context** | その時点で分かっていた事実と制約。後から分かったことを混ぜない。 |
| **Decision** | 何をすると決めたか。実装が違っていたら、実装かこの行のどちらかが間違っている。 |
| **Alternatives considered** | 退けた案と、その trade-off。「検討した」だけでなく**何を失う代わりに何を取ったか**。 |
| **Consequences** | 引き受けた結果。良い方だけでなく、この決定のせいで不便になったことも書く。 |

| ADR | 決定 |
|---|---|
| [0001](ADR-0001-prior-art-audit-as-the-baseline.md) | ひな形調査の結果を判断の基準線として固定する |
| [0002](ADR-0002-python-with-zero-runtime-dependencies.md) | Python 3.12 / ランタイム依存ゼロ |
| [0003](ADR-0003-hitl-implemented-in-house.md) | HITL は自作し、既存フレームワークの意味論を写像する |
| [0004](ADR-0004-missing-answers-withhold-the-verdict.md) | 欠損回答は補完せず、判定を保留する |
| [0005](ADR-0005-local-llm-optional-null-provider-default.md) | LLM はローカル (Ollama) 任意層・既定は生成なし |
| [0006](ADR-0006-safety-evaluation-deterministic-first.md) | 安全性の評価は決定論ゲート先行、judge は 2 本で一致度ごと報告 |
| [0007](ADR-0007-non-medical-device-by-function-not-disclaimer.md) | プログラム医療機器 非該当は機能設計で維持し、免責文言に頼らない |

根拠の本体は `docs/evidence/PRIOR_ART_REPORT_2026-08-21.md` (以下 PAR) と
`docs/evidence/SOURCES.md` (一次資料の版を sha256 で固定した表)。
