"use strict";

// 結果画面。実施者の署名が済むまで結果本文は表示されない (R3-G1)。

const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";

const statusEl = document.getElementById("status");
const noticeCard = document.getElementById("notice-card");
const noticeEl = document.getElementById("notice");
const noticeSourceEl = document.getElementById("notice-source");
const notesCard = document.getElementById("notes-card");
const notesEl = document.getElementById("notes");
const crisisEl = document.getElementById("crisis");

const STATE_TEXT = {
  pending_review: ["実施者が確認しています。確認が終わるまで結果は表示されません。", "warn"],
  released: ["結果を表示しています。", "ok"],
  rejected: ["実施者が個別に対応しています。本画面では結果を表示しません。", "warn"],
  invalid: ["未回答の項目があるため、判定を確定していません。", "warn"],
};

const SOURCE_TEXT = {
  fallback_text: "この案内文は、あらかじめ用意された定型文です（AI は使用していません）。",
  fixed_text: "この案内文は、あらかじめ用意された固定の文面です。",
  implementer: "この案内文は実施者が確認・修正したものです。",
  ollama: "この案内文は、ローカルで動作する AI が生成し、実施者が確認したものです。",
};

function setStatus(message, kind) {
  statusEl.textContent = message;
  statusEl.className = kind ? "status " + kind : "status";
}

function renderCrisis(payload) {
  if (!payload) {
    crisisEl.classList.add("hidden");
    return;
  }
  crisisEl.classList.remove("hidden");
  document.getElementById("crisis-headline").textContent = payload.headline || "";
  const body = document.getElementById("crisis-hotlines");
  body.textContent = "";
  (payload.hotlines || []).forEach((hotline) => {
    const row = document.createElement("tr");
    [hotline.name, hotline.phone, hotline.hours].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value || "";
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
  document.getElementById("crisis-note").textContent =
    "この案内は自動的な文面判定によって表示されています。内容の判定に AI は使用しておらず、" +
    "医学的な診断ではありません。";
}

function renderNotes(notes) {
  notesEl.textContent = "";
  if (!notes || notes.length === 0) {
    notesCard.classList.add("hidden");
    return;
  }
  notes.forEach((note) => {
    const li = document.createElement("li");
    li.textContent = note;
    notesEl.appendChild(li);
  });
  notesCard.classList.remove("hidden");
}

function renderHotlines() {
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
    .catch(() => {});
}

function load() {
  if (!token) {
    setStatus("受検コードが指定されていません。受検からやり直してください。", "error");
    return;
  }
  document.getElementById("token").textContent = token;
  setStatus("読み込み中です…", "");

  fetch("/api/result?token=" + encodeURIComponent(token))
    .then((response) => response.json())
    .then((payload) => {
      if (payload.error) {
        throw new Error(payload.error);
      }
      renderCrisis(payload.crisis_response);

      const state = STATE_TEXT[payload.state] || ["状態を判別できませんでした。", "warn"];
      setStatus(state[0], state[1]);

      if (payload.text) {
        noticeEl.textContent = payload.text;
        noticeSourceEl.textContent = SOURCE_TEXT[payload.source] || "";
        noticeCard.classList.remove("hidden");
      } else {
        noticeCard.classList.add("hidden");
      }
      renderNotes(payload.notes);
    })
    .catch((error) => {
      setStatus("結果を取得できませんでした: " + error.message, "error");
    });
}

document.getElementById("reload").addEventListener("click", load);

load();
renderHotlines();
