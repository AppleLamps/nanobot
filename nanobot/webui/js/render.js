/* render.js — markdown rendering, message rows, empty state, attachments, sessions */

import { dom, state } from "./state.js";

/* --- Blob URL lifecycle (avoid memory leaks on repeated renders) --- */

function _makeObjectURL(file) {
  try {
    return URL.createObjectURL(file);
  } catch (_) {
    return "";
  }
}

function _revokeObjectURL(url) {
  if (!url || typeof url !== "string") return;
  if (!url.startsWith("blob:")) return;
  try {
    URL.revokeObjectURL(url);
  } catch (_) { }
}

const _attachmentPreviewUrls = new Map();

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

/* --- Markdown renderer --- */

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
  } catch (_) { }
  return null;
}

export function renderMarkdown(text) {
  const t = String(text ?? "");
  const root = document.createElement("div");

  /* --- Phase 1: Extract fenced code blocks line-by-line to avoid regex mis-matches --- */
  const segments = []; // { type: "text" | "code", content, lang? }
  const lines = t.split(/\r?\n/);
  let i = 0;
  let textBuf = [];

  function flushText() {
    if (textBuf.length) {
      segments.push({ type: "text", content: textBuf.join("\n") });
      textBuf = [];
    }
  }

  while (i < lines.length) {
    const fenceMatch = /^(`{3,})([\w+-]*)/.exec(lines[i]);
    if (fenceMatch) {
      flushText();
      const fence = fenceMatch[1]; // e.g. ``` or ````
      const lang = (fenceMatch[2] || "").trim();
      const codeBuf = [];
      i++;
      while (i < lines.length) {
        if (lines[i].trimEnd() === fence || lines[i].startsWith(fence)) {
          i++;
          break;
        }
        codeBuf.push(lines[i]);
        i++;
      }
      segments.push({ type: "code", content: codeBuf.join("\n"), lang });
    } else {
      textBuf.push(lines[i]);
      i++;
    }
  }
  flushText();

  /* --- Phase 2: Render segments --- */

  function inlineHtml(s) {
    let x = esc(s);
    // Protect inline code spans first — replace with placeholders
    const codeSpans = [];
    x = x.replace(/`([^`]+)`/g, (m, c) => {
      codeSpans.push(`<code>${c}</code>`);
      return `\x00CODE${codeSpans.length - 1}\x00`;
    });
    x = x.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    x = x.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    x = x.replace(
      /(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])/g,
      "<em>$1</em>"
    );
    x = x.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, url) => {
      const href = safeLink(url);
      if (!href) return "";
      return `<img src="${href}" alt="${alt}" class="msg-img" loading="lazy" />`;
    });
    x = x.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, label, url) => {
      const href = safeLink(url);
      if (!href) return `${label} (${url})`;
      return `<a href="${href}" target="_blank" rel="noreferrer noopener">${label}</a>`;
    });
    // Restore inline code spans
    x = x.replace(/\x00CODE(\d+)\x00/g, (_, idx) => codeSpans[parseInt(idx)]);
    return x;
  }

  function addTextBlock(s) {
    const blockLines = String(s || "").split(/\r?\n/);
    let para = [];
    let listEl = null;
    let listType = "";

    function flushPara() {
      if (!para.length) return;
      const p = document.createElement("p");
      p.innerHTML = inlineHtml(para.join(" ").trim());
      root.appendChild(p);
      para = [];
    }

    function flushList() {
      if (listEl) root.appendChild(listEl);
      listEl = null;
      listType = "";
    }

    for (let j = 0; j < blockLines.length; j++) {
      const line = blockLines[j];
      const trimmed = line.trim();

      if (!trimmed) {
        flushPara();
        // Don't flush list on blank lines — allow continuation paragraphs
        continue;
      }

      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        flushPara();
        flushList();
        root.appendChild(document.createElement("hr"));
        continue;
      }

      const heading = /^(#{1,6})\s+(.+)$/.exec(trimmed);
      if (heading) {
        flushPara();
        flushList();
        const h = document.createElement(`h${heading[1].length}`);
        h.innerHTML = inlineHtml(heading[2]);
        root.appendChild(h);
        continue;
      }

      if (trimmed.startsWith(">")) {
        flushPara();
        flushList();
        const parts = [];
        for (; j < blockLines.length; j++) {
          const l = blockLines[j];
          if (!l.trim().startsWith(">")) break;
          parts.push(l.replace(/^\s*>\s?/, "").trim());
        }
        j -= 1;
        const bq = document.createElement("blockquote");
        const p = document.createElement("p");
        p.innerHTML = inlineHtml(parts.join(" "));
        bq.appendChild(p);
        root.appendChild(bq);
        continue;
      }

      const ol = /^\d+\.\s+(.+)$/.exec(trimmed);
      const ul = /^[-*+]\s+(.+)$/.exec(trimmed);
      if (ol || ul) {
        flushPara();
        const nextType = ol ? "ol" : "ul";
        if (!listEl || listType !== nextType) {
          flushList();
          listEl = document.createElement(nextType);
          listType = nextType;
        }
        // Collect continuation lines (indented by 2+ spaces or tab)
        const liParts = [(ol || ul)[1]];
        while (j + 1 < blockLines.length) {
          const next = blockLines[j + 1];
          if (/^\s{2,}/.test(next) && next.trim()) {
            liParts.push(next.trim());
            j++;
          } else {
            break;
          }
        }
        const li = document.createElement("li");
        li.innerHTML = inlineHtml(liParts.join(" "));
        listEl.appendChild(li);
        continue;
      }

      /* --- Table detection --- */
      if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
        flushPara();
        flushList();
        const tableLines = [];
        for (; j < blockLines.length; j++) {
          const tl = blockLines[j].trim();
          if (tl.startsWith("|") && tl.endsWith("|")) {
            tableLines.push(tl);
          } else {
            break;
          }
        }
        j -= 1;

        if (tableLines.length >= 2) {
          const parseCells = (row) =>
            row.slice(1, -1).split("|").map((c) => c.trim());

          /* Detect separator row (second line) and extract alignment */
          const sepCells = parseCells(tableLines[1]);
          const isSep = sepCells.every((c) => /^:?-{1,}:?$/.test(c));
          const aligns = isSep
            ? sepCells.map((c) => {
              if (c.startsWith(":") && c.endsWith(":")) return "center";
              if (c.endsWith(":")) return "right";
              return "";
            })
            : [];

          const table = document.createElement("table");
          const headCells = parseCells(tableLines[0]);
          if (isSep) {
            const thead = document.createElement("thead");
            const tr = document.createElement("tr");
            headCells.forEach((c, ci) => {
              const th = document.createElement("th");
              if (aligns[ci]) th.style.textAlign = aligns[ci];
              th.innerHTML = inlineHtml(c);
              tr.appendChild(th);
            });
            thead.appendChild(tr);
            table.appendChild(thead);
          }

          const tbody = document.createElement("tbody");
          const dataStart = isSep ? 2 : 0;
          for (let r = dataStart; r < tableLines.length; r++) {
            const cells = parseCells(tableLines[r]);
            const tr = document.createElement("tr");
            cells.forEach((c, ci) => {
              const td = document.createElement("td");
              if (aligns[ci]) td.style.textAlign = aligns[ci];
              td.innerHTML = inlineHtml(c);
              tr.appendChild(td);
            });
            tbody.appendChild(tr);
          }
          table.appendChild(tbody);

          const wrapper = document.createElement("div");
          wrapper.className = "table-wrap";
          wrapper.appendChild(table);
          root.appendChild(wrapper);
        } else {
          /* Single pipe line — treat as paragraph */
          para.push(tableLines[0]);
        }
        continue;
      }

      /* --- Indented code block (4 spaces or 1 tab) --- */
      if (/^(    |\t)/.test(line) && !listEl) {
        flushPara();
        const codeLines = [line.replace(/^(    |\t)/, "")];
        while (j + 1 < blockLines.length && /^(    |\t)/.test(blockLines[j + 1])) {
          j++;
          codeLines.push(blockLines[j].replace(/^(    |\t)/, ""));
        }
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = codeLines.join("\n");
        pre.appendChild(code);
        root.appendChild(pre);
        continue;
      }

      para.push(trimmed);
    }

    flushPara();
    flushList();
  }

  for (const seg of segments) {
    if (seg.type === "text") {
      addTextBlock(seg.content);
    } else {
      const wrapper = document.createElement("div");
      wrapper.className = "code-block";
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      if (seg.lang) code.className = `language-${seg.lang}`;
      code.textContent = seg.content || "";
      pre.appendChild(code);

      const toolbar = document.createElement("div");
      toolbar.className = "code-toolbar";
      if (seg.lang) {
        const langLabel = document.createElement("span");
        langLabel.className = "code-lang";
        langLabel.textContent = seg.lang;
        toolbar.appendChild(langLabel);
      }
      const copyBtn = document.createElement("button");
      copyBtn.className = "code-copy";
      copyBtn.type = "button";
      copyBtn.title = "Copy code";
      copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span>Copy</span>';
      copyBtn.addEventListener("click", () => {
        const text = code.textContent || "";
        navigator.clipboard.writeText(text).then(() => {
          copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><span>Copied!</span>';
          copyBtn.classList.add("copied");
          setTimeout(() => {
            copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><span>Copy</span>';
            copyBtn.classList.remove("copied");
          }, 2000);
        }).catch(() => { });
      });
      toolbar.appendChild(copyBtn);

      wrapper.appendChild(toolbar);
      wrapper.appendChild(pre);
      root.appendChild(wrapper);
    }
  }
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
  const roleLabel = role === "user" ? "you" : "nanobot";
  const ts = opts && opts.ts ? new Date(opts.ts * 1000) : new Date();
  const timeStr = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  meta.textContent = `${roleLabel} \u00b7 ${timeStr}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble " + role;

  const content = document.createElement("div");
  content.className = "content";

  if (opts && (opts.media || opts.attachments)) {
    const list = opts.media || opts.attachments;
    if (Array.isArray(list) && list.length > 0) {
      const attachDiv = document.createElement("div");
      attachDiv.className = "msg-attachments";
      for (const item of list) {
        let url = "";
        let isBlob = false;
        if (item instanceof File) {
          const fileLooksImage =
            (item.type && item.type.startsWith("image/")) ||
            /\.(jpe?g|png|gif|webp|svg)$/i.test(item.name || "");
          if (fileLooksImage) {
            url = _makeObjectURL(item);
            isBlob = !!url;
          }
        } else if (typeof item === "string") {
          // Relative paths like "uploads/file.jpg" → "/uploads/file.jpg"
          url = item.startsWith("/") || item.startsWith("http") || item.startsWith("blob:") || item.startsWith("data:")
            ? item
            : "/" + item;
        } else if (item && item.url) {
          url = item.url;
        }

        if (url) {
          const isImage = /\.(jpe?g|png|gif|webp|svg)$/i.test(url) ||
            (item instanceof File && item.type && item.type.startsWith("image/")) ||
            /^(blob:|data:image\/)/.test(url);
          if (isImage) {
            const img = document.createElement("img");
            img.src = url;
            img.className = "msg-img";
            img.loading = "lazy";
            if (isBlob) {
              img.addEventListener("load", () => _revokeObjectURL(url), { once: true });
              img.addEventListener("error", () => _revokeObjectURL(url), { once: true });
            }
            attachDiv.appendChild(img);
          }
        }
      }
      if (attachDiv.children.length > 0) {
        content.appendChild(attachDiv);
      }
    }
  }

  content.appendChild(renderMarkdown(text));
  bubble.appendChild(content);

  wrap.appendChild(meta);
  wrap.appendChild(bubble);

  if (role === "assistant") {
    const feedback = document.createElement("div");
    feedback.className = "feedback";

    const thumbUp = document.createElement("button");
    thumbUp.className = "feedback-btn";
    thumbUp.type = "button";
    thumbUp.title = "Good response";
    thumbUp.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';

    const thumbDown = document.createElement("button");
    thumbDown.className = "feedback-btn";
    thumbDown.type = "button";
    thumbDown.title = "Poor response";
    thumbDown.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>';

    thumbUp.addEventListener("click", () => {
      thumbUp.classList.toggle("active");
      thumbDown.classList.remove("active");
    });
    thumbDown.addEventListener("click", () => {
      thumbDown.classList.toggle("active");
      thumbUp.classList.remove("active");
    });

    feedback.appendChild(thumbUp);
    feedback.appendChild(thumbDown);
    wrap.appendChild(feedback);
  }

  row.appendChild(wrap);

  dom.rows.appendChild(row);
  updateEmpty();

  if (!(opts && opts.skipScroll)) {
    if ((opts && opts.autoscroll) || nearBottom()) scrollToBottom();
    updateJump();
  }
  return row;
}

export function renderHistory(msgs) {
  state.serverHistory = Array.isArray(msgs) ? msgs : [];
  state.lastHistoryEmpty = state.serverHistory.length === 0;
  clearRows();
  for (const m of state.serverHistory) {
    addRow(m.role, m.content, { media: m.media, attachments: m.attachments, ts: m.ts, skipScroll: true });
  }
  updateEmpty();
  scrollToBottom();
  updateJump();
}

/* --- Attachment chips --- */

export function renderAttachments() {
  if (!dom.attachments) return;
  // Revoke preview URLs for files that are no longer attached.
  for (const [file, url] of _attachmentPreviewUrls) {
    if (!state.attachments.includes(file)) {
      _revokeObjectURL(url);
      _attachmentPreviewUrls.delete(file);
    }
  }
  dom.attachments.innerHTML = "";
  for (let i = 0; i < state.attachments.length; i++) {
    const f = state.attachments[i];
    const isImage = f.type && f.type.startsWith("image/");

    if (isImage) {
      const card = document.createElement("div");
      card.className = "attach-card";

      const img = document.createElement("img");
      img.className = "attach-card-img";
      // Reuse one preview URL per File and revoke when attachment leaves state.
      let previewUrl = _attachmentPreviewUrls.get(f);
      if (!previewUrl) {
        previewUrl = _makeObjectURL(f);
        if (previewUrl) _attachmentPreviewUrls.set(f, previewUrl);
      }
      img.src = previewUrl || "";
      img.alt = f.name;
      card.appendChild(img);

      const x = document.createElement("button");
      x.className = "attach-card-x";
      x.type = "button";
      x.title = "Remove";
      x.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
      x.addEventListener("click", () => {
        const currentUrl = _attachmentPreviewUrls.get(f);
        if (currentUrl) {
          _revokeObjectURL(currentUrl);
          _attachmentPreviewUrls.delete(f);
        }
        state.attachments.splice(i, 1);
        renderAttachments();
      });
      card.appendChild(x);

      dom.attachments.appendChild(card);
    } else {
      const chip = document.createElement("div");
      chip.className = "chip";

      const name = document.createElement("div");
      name.className = "name";
      name.textContent = f.name;

      const x = document.createElement("button");
      x.className = "x";
      x.type = "button";
      x.textContent = "×";
      x.addEventListener("click", () => {
        state.attachments.splice(i, 1);
        renderAttachments();
      });

      chip.appendChild(name);
      chip.appendChild(x);
      dom.attachments.appendChild(chip);
    }
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

/* --- Subagent list --- */

function fmtElapsed(startedAt) {
  const ts = Number(startedAt || 0);
  if (!ts) return "";
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  const m = Math.floor(elapsed / 60);
  const s = elapsed % 60;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function renderSubagents(items, { onCancel } = {}) {
  if (!dom.subagentList) return;
  dom.subagentList.innerHTML = "";

  const list = Array.isArray(items) ? items : [];
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "subagent-empty";
    empty.textContent = "No running subagents.";
    dom.subagentList.appendChild(empty);
    return;
  }

  for (const it of list) {
    const row = document.createElement("div");
    row.className = "subagent-item";

    const meta = document.createElement("div");
    meta.className = "meta";

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = `${it.label || it.id || "subagent"} (${fmtElapsed(it.started_at)})`;

    const task = document.createElement("div");
    task.className = "task";
    task.textContent = it.task || "";

    meta.appendChild(label);
    meta.appendChild(task);

    const actions = document.createElement("div");
    const btn = document.createElement("button");
    btn.className = "btn";
    btn.type = "button";
    btn.textContent = "Cancel";
    btn.addEventListener("click", () => {
      if (onCancel) onCancel(it.id || "");
    });
    actions.appendChild(btn);

    row.appendChild(meta);
    row.appendChild(actions);
    dom.subagentList.appendChild(row);
  }
}
