"use strict";

// 受検フォーム。外部ライブラリなし・外部通信なし。
// Content-Security-Policy が default-src 'self' のため、インライン script も
// 外部 CDN も動かない。ここを含め、すべて同一オリジンのファイルで完結する。

const itemsEl = document.getElementById("items");
const variantEl = document.getElementById("variant");
const statusEl = document.getElementById("status");
const submitEl = document.getElementById("submit");
const formEl = document.getElementById("survey");

function setStatus(message, kind) {
  statusEl.textContent = message;
  statusEl.className = kind ? "status " + kind : "status";
  statusEl.classList.toggle("hidden", !message);
}

function domainTitle(domain) {
  const titles = {
    A: "領域A ── 仕事のストレス要因について",
    B: "領域B ── 心身のストレス反応について",
    C: "領域C ── 周囲のサポートについて",
    D: "領域D ── 満足度について（判定には使用しません）",
  };
  return titles[domain] || "領域" + domain;
}

function renderItems(payload) {
  itemsEl.textContent = "";
  let currentDomain = null;
  let currentContext = null;
  let fieldset = null;

  payload.items.forEach((item) => {
    if (item.domain !== currentDomain) {
      currentDomain = item.domain;
      currentContext = null;
      fieldset = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.textContent = domainTitle(item.domain);
      fieldset.appendChild(legend);
      itemsEl.appendChild(fieldset);
    }
    if (item.context && item.context !== currentContext) {
      currentContext = item.context;
      const lead = document.createElement("p");
      lead.className = "hint";
      lead.textContent = item.context;
      fieldset.appendChild(lead);
    }

    const wrap = document.createElement("div");
    wrap.className = "item";

    const question = document.createElement("span");
    question.className = "q";
    const number = document.createElement("span");
    number.className = "no";
    number.textContent = "問" + item.item_no;
    question.appendChild(number);
    question.appendChild(document.createTextNode(item.text));
    wrap.appendChild(question);

    const choices = document.createElement("div");
    choices.className = "choices";
    item.choices.forEach((choice, index) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "q" + item.item_no;
      input.value = String(index + 1);
      input.required = true;
      label.appendChild(input);
      label.appendChild(document.createTextNode(" " + choice));
      choices.appendChild(label);
    });
    wrap.appendChild(choices);
    fieldset.appendChild(wrap);
  });
}

function loadItems() {
  setStatus("設問を読み込んでいます…", "");
  fetch("/api/items?variant=" + encodeURIComponent(variantEl.value))
    .then((response) => response.json())
    .then((payload) => {
      if (payload.error) {
        throw new Error(payload.error);
      }
      renderItems(payload);
      setStatus("", "");
    })
    .catch((error) => {
      itemsEl.textContent = "設問を読み込めませんでした。";
      setStatus("設問の読み込みに失敗しました: " + error.message, "error");
    });
}

function loadHotlines() {
  fetch("/api/hotlines")
    .then((response) => response.json())
    .then((payload) => {
      const target = document.getElementById("hotlines");
      target.textContent = "相談窓口: ";
      payload.hotlines.forEach((hotline, index) => {
        if (index > 0) {
          target.appendChild(document.createTextNode(" ／ "));
        }
        target.appendChild(document.createTextNode(hotline.name + " " + hotline.phone));
      });
    })
    .catch(() => {
      document.getElementById("hotlines").textContent =
        "相談窓口の読み込みに失敗しました。";
    });
}

function collectAnswers() {
  const answers = {};
  const checked = formEl.querySelectorAll("input[type=radio]:checked");
  checked.forEach((input) => {
    answers[Number(input.name.slice(1))] = Number(input.value);
  });
  return answers;
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  const answers = collectAnswers();
  const total = formEl.querySelectorAll(".item").length;
  if (Object.keys(answers).length < total) {
    // 欠損は補完せず保留になる (R1-4)。送信前に本人へ知らせる。
    setStatus(
      "未回答の設問があります。すべてに回答してください（未回答があると判定は確定しません）。",
      "warn"
    );
    return;
  }

  submitEl.disabled = true;
  setStatus("送信しています…", "");

  fetch("/api/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      variant: variantEl.value,
      answers: answers,
      free_text: document.getElementById("free-text").value,
      token_seed: document.getElementById("token-seed").value,
    }),
  })
    .then((response) => response.json())
    .then((payload) => {
      if (payload.error) {
        throw new Error(payload.error);
      }
      window.location.href = "/result?token=" + encodeURIComponent(payload.token);
    })
    .catch((error) => {
      submitEl.disabled = false;
      setStatus("送信に失敗しました: " + error.message, "error");
    });
});

variantEl.addEventListener("change", loadItems);

loadItems();
loadHotlines();
