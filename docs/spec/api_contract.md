# core API 契約 (実装間の整合 SSoT)

> 実装はこの署名に従う。変更したくなったら実装を曲げず、このファイルの改訂を提案すること。

## packs/jsq/scoring.py (pure、stdlib のみ)

```python
@dataclass(frozen=True)
class ScoreResult:
    variant: str              # "57" | "23"
    sums: dict[str, int]      # {"A": int, "B": int, "C": int} (逆転換算後)
    high_stress: bool         # 単純合計法
    rule_hit: str             # "none" | "B_only"(㋐) | "AC_and_B"(㋑)
    missing: tuple[int, ...]  # 欠損項目番号 (1-origin)。非空なら判定無効
    valid: bool               # missing が空か

def score(answers: Mapping[int, int], variant: str = "57",
          thresholds: Thresholds | None = None) -> ScoreResult
    # answers: {項目番号(1-origin): 1..4}。範囲外値は ValueError。
    # 欠損 = キー不在。missing 非空なら high_stress=False, valid=False (R1-4: 補完しない)
def load_thresholds(csv_path: Path) -> Thresholds   # R1-3/R1-6
```

## packs/jsq/group.py

```python
def group_analysis(rows: Sequence[Mapping[int, int]], sexes: Sequence[str]) -> GroupResult
    # len(rows) < 10 → GroupSizeError (R2-2)。sexes: "m"|"f"|"u" ("u"は男性図=保守側)
    # GroupResult: 4尺度平均, risk_a, risk_b, total_risk = round(risk_a*risk_b/100)
    # risk_a/risk_b/total_risk は `| None`、
    # coefficients_verified: bool / n_excluded_incomplete: int / chart_sex: str / notes を持つ
    # (係数一次資料未取得を型で表現。係数 CSV 補充で skip が自動解除される構造)
```

## core/gates.py

```python
class GateResult(NamedTuple):
    ok: bool
    gate: str                 # "signature" | "samd_lint" | "crisis"
    reasons: tuple[str, ...]  # block 理由 (禁止語 hit 等)。audit へそのまま記録

class Gate(ABC):
    name: str
    def check(self, payload: dict) -> GateResult: ...
    def validate_config(self) -> None: ...   # 辞書0件等 → ValueError (R3-G4)。GateChain.__init__ で全件呼ぶ

class GateChain:  # 順序固定: crisis → samd_lint → signature。1つでも ng なら全体 ng
```

## core/hitl.py

```python
Decision = Literal["approve", "edit", "reject", "respond"]

class HitlStore:  # SQLite。表: interrupts(id, token, stage, payload_json, state, created)
                  #         decisions(interrupt_id, decision, actor, diff_json, note, ts)
    def interrupt(self, token: str, stage: str, payload: dict) -> str
        # id = sha256(f"{token}:{stage}").hexdigest()[:16]。既存 id は再利用 (冪等)
    def resume(self, interrupt_id: str, decision: Decision, actor: str,
               edited: dict | None = None, note: str = "") -> dict
        # 戻り値 = 下流へ渡す確定 payload。respond は edited(所見) をそのまま採用
        # reject の戻り値は空 dict {}
        # PENDING 以外への resume は StateError (二重実行防止 R6-2)
    def kpi(self) -> dict     # {"total": n, "override_rate": 1 - approve/total, ...}
```

## core/audit.py

```python
class AuditLog:  # SQLite append-only。各行 prev_hash を持つ hash 連鎖
    def append(self, kind: str, payload: dict) -> str   # 戻り値 = 行 hash
    def verify_chain(self) -> bool
```

## core/llm.py

```python
class LLMProvider(ABC):
    def generate(self, prompt_id: str, variables: dict) -> LLMOutput  # prompts.yaml の id を引く
class NullProvider(LLMProvider):   # 既定。prompts.yaml の fallback_text を返す
class OllamaProvider(LLMProvider): # urllib で http://localhost:11434/api/chat のみ。他ホスト禁止
def get_provider() -> LLMProvider  # env LLM_PROVIDER: 未設定/"null"→Null, "ollama"→Ollama
```

## packs/crisis/classify.py

```python
def classify(text: str) -> CrisisResult
    # CrisisResult: level: "none"|"explore"|"ideation"|"plan"|"prepared" (段階判定木、csv 駆動)
    # level != "none" → 呼び出し側は LLM bypass + hotlines 固定応答 (R3-G3)
```

## packs/samdlint/lint.py

```python
def lint(text: str) -> GateResult   # samd_forbidden.csv 駆動。%表示+疾病名の組合せ regex を含む
```

## app/server.py HTTP エンドポイント (9 本、loopback のみ)

`http.server` の素の実装。`BIND_HOST = "127.0.0.1"` 定数バインド (引数化しない)。全レスポンスに
`Content-Security-Policy: default-src 'self'` 他のセキュリティヘッダを付与。状態変更 POST は
Content-Type=application/json 必須・Origin/Referer は自オリジンか不在のみ許可・Host は
`127.0.0.1|localhost:<port>` に限定 (CSRF / DNS リビンディング防御)。

| メソッド | パス | 用途 | 状態変更 |
|---|---|---|---|
| GET | `/api/items?variant=57\|23` | 調査票の項目一覧 | no |
| GET | `/api/hotlines` | 窓口一覧 (`hotlines_ja.csv`) | no |
| GET | `/api/result?token=<token>` | 受検結果 (署名ゲートを通した文面) | no |
| GET | `/api/review/pending?stage=<stage>` | 実施者レビュー待ち行列 | no |
| GET | `/api/review/sample?n=&seed=` | 監査サンプリング (再現可能) | no |
| GET | `/api/kpi` | レビュー統計 (override 率等) | no |
| POST | `/api/submit` | 回答の投入・採点・危機分類・下書き | yes |
| POST | `/api/review/decide` | 実施者の 4 決定 (approve/edit/reject/respond) | yes |
| POST | `/api/gate-check` | 任意文面を全ゲートに通す (診断用) | no (副作用なし) |

静的ページは `/`・`/result`・`/review` (+ `/static/...`)。エラーは `SentinelError`/`ValueError`→4xx、
それ以外→500 (本文はクライアントへ出さない)。受検 token はクエリで渡るがログ行からは除去する。

## 命名・共通規則

- 例外は `sentinel.core.errors` に集約 (GateSetupError / StateError / GroupSizeError)
- CSV は全て 1 行目 `# source: <URL> / license: <...>` コメントヘッダ
- ログ・DB に受検者の自由記述原文を保存しない (crisis 判定結果と hash のみ) — R7
