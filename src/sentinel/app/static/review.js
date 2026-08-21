"use strict";

// 実施者レビュー画面。4 決定型 approve / edit / reject / respond (R6-1)。
// edit と respond は文面が必要なため、テキストエリアの内容を送る。

const statusEl = document.getElementById("status");
const pendingEl = document.getElementById("pending");
const kpiEl = document.getElementById("kpi");
const actorEl = document.getElementById("actor");

const KPI_LABELS = {
  total: "決定件数",
  override_rate: "オーバーライド率",
  pending: "未確認",
  approve: "承認",
  edit: "修正",
  reject: "差し戻し",
  respond: "所見で置換",
  submissions: "受検件数",
  crisis_detected: "危機検知",
  notice_blocked: "文面差し止め",
  notice_withheld: "署名待ちで非表示",
  signatures: "署名記録",
};

const STAGE_LABELS = {
  result_review: "高ストレス判定の確認",
  crisis_review: "危機シグナルの確認",
};

const DECISION_LABELS = {
  approve: "承認して本人へ",
  edit: "修正して本人へ",
  respond: "所見に置き換えて本人へ",
  reject: "本人画面には出さない",
};

function setStatus(message, kind) {
  statusEl.textContent = message;
  statusEl.className = kind ? "status " + kind : "status";
  statusEl.classList.toggle("hidden", !message);
}

function renderKpi(payload) {
  kpiEl.textContent = "";
  Object.keys(KPI_LABELS).forEach((key) => {
    if (!(key in payload)) {
      return;
    }
    const cell = document.createElement("div");
    const value = document.createElement("span");
    value.className = "value";
    value.textContent =
      key === "override_rate" ? Math.round(payload[key] * 1000) / 10 + "%" : String(payload[key]);
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = KPI_LABELS[key];
    cell.appendChild(value);
    cell.appendChild(label);
    kpiEl.appendChild(cell);
  });

  if (payload.audit_chain_ok === false) {
    const warn = document.createElement("div");
    warn.className = "label";
    warn.textContent = "監査ログの連鎖検証に失敗しています。";
    kpiEl.appendChild(warn);
  }
}

function summaryRow(label, value) {
  const row = document.createElement("tr");
  const head = document.createElement("th");
  head.textContent = label;
  const cell = document.createElement("td");
  cell.textContent = value;
  row.appendChild(head);
  row.appendChild(cell);
  return row;
}

function renderItem(item) {
  const card = document.createElement("section");
  card.className = "card";

  const title = document.createElement("h2");
  title.textContent = STAGE_LABELS[item.stage] || item.stage;
  card.appendChild(title);

  const payload = item.payload || {};
  const table = document.createElement("table");
  table.appendChild(summaryRow("受検コード", item.token));
  table.appendChild(summaryRow("受付", item.created));
  if (payload.sums) {
    table.appendChild(
      summaryRow(
        "領域合計",
        "A " + payload.sums.A + " ／ B " + payload.sums.B + " ／ C " + payload.sums.C
      )
    );
  }
  if (payload.rule_hit) {
    table.appendChild(summaryRow("該当した基準", payload.rule_hit));
  }
  if (payload.crisis_level) {
    table.appendChild(summaryRow("危機シグナル", payload.crisis_level));
  }
  if (payload.crisis_rule_ids) {
    table.appendChild(summaryRow("検知した規則", payload.crisis_rule_ids.join(", ")));
  }
  if (payload.free_text_sha256) {
    table.appendChild(summaryRow("自由記述のハッシュ", payload.free_text_sha256.slice(0, 16)));
  }
  table.appendChild(summaryRow("文面の作成元", payload.notice_source || "定型文"));
  card.appendChild(table);

  const label = document.createElement("label");
  label.className = "field";
  label.textContent = "本人へ表示する文面";
  card.appendChild(label);

  const area = document.createElement("textarea");
  area.value = payload.notice_text || "";
  area.rows = 12;
  card.appendChild(area);

  const noteLabel = document.createElement("label");
  noteLabel.className = "field";
  noteLabel.textContent = "所見メモ（記録に残ります・本人には表示しません）";
  card.appendChild(noteLabel);

  const note = document.createElement("input");
  note.type = "text";
  card.appendChild(note);

  const actions = document.createElement("div");
  actions.className = "actions";
  ["approve", "edit", "respond", "reject"].forEach((decision) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = DECISION_LABELS[decision];
    if (decision !== "approve") {
      button.className = "secondary";
    }
    button.addEventListener("click", () => {
      decide(item.interrupt_id, decision, area.value, note.value);
    });
    actions.appendChild(button);
  });
  card.appendChild(actions);

  return card;
}

function decide(interruptId, decision, text, note) {
  const actor = actorEl.value.trim();
  if (!actor) {
    setStatus("実施者IDを入力してください。記録されない決定は決定として扱いません。", "warn");
    return;
  }
  setStatus("送信しています…", "");
  fetch("/api/review/decide", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      interrupt_id: interruptId,
      decision: decision,
      actor: actor,
      notice_text: decision === "edit" || decision === "respond" ? text : "",
      note: note,
    }),
  })
    .then((response) => response.json())
    .then((payload) => {
      if (payload.error) {
        throw new Error(payload.error);
      }
      setStatus("記録しました（" + DECISION_LABELS[decision] + "）。", "ok");
      load();
    })
    .catch((error) => {
      setStatus("記録できませんでした: " + error.message, "error");
    });
}

function load() {
  fetch("/api/kpi")
    .then((response) => response.json())
    .then(renderKpi)
    .catch(() => {
      kpiEl.textContent = "集計を取得できませんでした。";
    });

  fetch("/api/review/pending")
    .then((response) => response.json())
    .then((payload) => {
      pendingEl.textContent = "";
      if (!payload.pending || payload.pending.length === 0) {
        pendingEl.textContent = "未確認の項目はありません。";
        return;
      }
      payload.pending.forEach((item) => {
        pendingEl.appendChild(renderItem(item));
      });
    })
    .catch((error) => {
      pendingEl.textContent = "キューを取得できませんでした: " + error.message;
    });
}

document.getElementById("sample").addEventListener("click", () => {
  const seed = document.getElementById("sample-seed").value;
  fetch("/api/review/sample?n=5&seed=" + encodeURIComponent(seed))
    .then((response) => response.json())
    .then((payload) => {
      const target = document.getElementById("sample-result");
      target.textContent = "";
      if (!payload.sample || payload.sample.length === 0) {
        target.textContent = "決定済みの項目がまだありません。";
        return;
      }
      const table = document.createElement("table");
      payload.sample.forEach((item) => {
        table.appendChild(summaryRow(item.token, item.state + " / " + item.stage));
      });
      target.appendChild(table);
    })
    .catch(() => {
      document.getElementById("sample-result").textContent = "抽出できませんでした。";
    });
});

load();
