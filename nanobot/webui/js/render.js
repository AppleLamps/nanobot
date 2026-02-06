/* render.js — markdown rendering, message rows, empty state, attachments, sessions */

import { dom, state } from "./state.js";

/* --- Scroll helpers --- */

export function nearBottom() {
  const threshold = 140;
  return dom.feed.scrollHeight - dom.feed.scrollTop - dom.feed.clientHeight < threshold;
}

export function scrollToBottom() {
  dom.feed.scrollTop = dom.feed.scrollHeight;
}

export function updateJump() {
  if (!dom.jumpWrap) return;
  if (nearBottom()) dom.jumpWrap.classList.remove("show");
  else dom.jumpWrap.classList.add("show");
}

/* --- Toast --- */

export function toastMsg(text) {
  if (!dom.toast) return;
  dom.toast.textContent = text;
  dom.toast.classList.add("show");
  setTimeout(() => dom.toast.classList.remove("show"), 1600);
}

/* --- Status indicator --- */

export function setStatus(kind, text) {
  if (dom.status) dom.status.textContent = text;
  if (!dom.dot) return;
  dom.dot.classList.remove("ok", "bad");
  if (kind === "ok") dom.dot.classList.add("ok");
  if (kind === "bad") dom.dot.classList.add("bad");
}

/* --- Textarea auto-grow --- */

export function autogrow() {
  if (!dom.input) return;
  dom.input.style.height = "auto";
  dom.input.style.height = Math.min(dom.input.scrollHeight, 160) + "px";
}

/* --- Markdown-ish renderer --- */

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function safeLink(url) {
  try {
    const u = new URL(url, location.href);
    if (u.protocol === "http:" || u.protocol === "https:") return u.href;
  } catch (_) {}
  return null;
}

export function renderMarkdownish(text) {
  const t = String(text ?? "");
  const root = document.createElement("div");
  const re = /```([\w+-]*)\n([\s\S]*?)```/g;
  let last = 0;

  function addTextBlock(s) {
    let x = esc(s);
    x = x.replace(/`([^`]+)`/g, (m, c) => `<code>${esc(c)}</code>`);
    x = x.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    x = x.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    x = x.replace(
      /(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])/g,
      "<em>$1</em>"
    );
    x = x.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, label, url) => {
      const href = safeLink(url);
      if (!href) return `${label} (${url})`;
      return `<a href="${href}" target="_blank" rel="noreferrer noopener">${label}</a>`;
    });
    x = x.replace(/\n/g, "<br/>");
    const span = document.createElement("span");
    span.innerHTML = x;
    root.appendChild(span);
  }

  for (let m; (m = re.exec(t)); ) {
    if (m.index > last) addTextBlock(t.slice(last, m.index));
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = m[2] || "";
    pre.appendChild(code);
    root.appendChild(pre);
    last = m.index + m[0].length;
  }
  if (last < t.length) addTextBlock(t.slice(last));
  return root;
}

/* --- Message rows --- */

export function clearRows() {
  if (!dom.rows) return;
  while (dom.rows.firstChild) dom.rows.removeChild(dom.rows.firstChild);
}

export function updateEmpty() {
  if (!dom.empty) return;
  const has = dom.rows && dom.rows.children && dom.rows.children.length > 0;
  dom.empty.setAttribute("aria-hidden", has ? "true" : "false");
}

export function addRow(role, text, opts) {
  const row = document.createElement("div");
  row.className = "row " + role;

  const wrap = document.createElement("div");
  wrap.style.display = "flex";
  wrap.style.flexDirection = "column";
  wrap.style.maxWidth = "100%";

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = role === "user" ? "you" : "nanobot";

  const bubble = document.createElement("div");
  bubble.className = "bubble " + role;

  const content = document.createElement("div");
  content.className = "content";
  content.appendChild(renderMarkdownish(text));
  bubble.appendChild(content);

  wrap.appendChild(meta);
  wrap.appendChild(bubble);
  row.appendChild(wrap);

  dom.rows.appendChild(row);
  updateEmpty();

  if ((opts && opts.autoscroll) || nearBottom()) scrollToBottom();
  updateJump();
  return row;
}

export function renderHistory(msgs) {
  state.serverHistory = Array.isArray(msgs) ? msgs : [];
  clearRows();
  for (const m of state.serverHistory) addRow(m.role, m.content);
  updateEmpty();
  scrollToBottom();
  updateJump();
}

/* --- Attachment chips --- */

export function renderAttachments() {
  if (!dom.attachments) return;
  dom.attachments.innerHTML = "";
  for (let i = 0; i < state.attachments.length; i++) {
    const f = state.attachments[i];
    const chip = document.createElement("div");
    chip.className = "chip";

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = f.name;

    const x = document.createElement("button");
    x.className = "x";
    x.type = "button";
    x.textContent = "x";
    x.addEventListener("click", () => {
      state.attachments.splice(i, 1);
      renderAttachments();
    });

    chip.appendChild(name);
    chip.appendChild(x);
    dom.attachments.appendChild(chip);
  }
}

/* --- Session list --- */

function fmtTime(s) {
  const x = String(s || "").trim();
  if (!x) return "";
  return x.slice(0, 19).replace("T", " ");
}

export function renderSessions(items, { onSwitch }) {
  if (!dom.sessionsList) return;
  dom.sessionsList.innerHTML = "";

  for (const it of items) {
    const key = String(it.key || "").trim();
    if (!key) continue;

    const updated = fmtTime(it.updated_at || "");

    const row = document.createElement("button");
    row.type = "button";
    row.className = "sessionitem";
    row.title = "Open " + key;

    const left = document.createElement("div");
    left.className = "k";
    left.textContent = key;

    const right = document.createElement("div");
    right.className = "t";
    right.textContent = updated || "";

    row.appendChild(left);
    row.appendChild(right);

    row.addEventListener("click", () => onSwitch(key));
    dom.sessionsList.appendChild(row);
  }

  if (!dom.sessionsList.children.length) {
    const d = document.createElement("div");
    d.className = "modal-sub";
    d.textContent = "No saved sessions.";
    dom.sessionsList.appendChild(d);
  }
}
