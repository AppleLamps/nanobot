(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const feed = $("feed");
  const input = $("input");
  const sendBtn = $("send");
  const statusEl = $("status");
  const dot = $("dot");
  const sessionKeyEl = $("sessionkey");
  const toast = $("toast");
  const jumpWrap = $("jump");
  const jumpBtn = $("jump-btn");
  const latencyEl = $("latency");
  const fileInput = $("file");
  const attachBtn = $("attach");
  const attachmentsEl = $("attachments");

  const qs = new URLSearchParams(location.search);
  const storage = window.localStorage;

  function randId(prefix) {
    const s =
      Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2);
    return (prefix || "id") + ":" + s.slice(0, 16);
  }

  let token = (qs.get("token") || storage.getItem("nanobot.webui.token") || "")
    .trim();
  if (qs.get("token")) storage.setItem("nanobot.webui.token", token);

  let senderId = (storage.getItem("nanobot.webui.senderId") || "").trim();
  if (!senderId) {
    senderId = randId("u");
    storage.setItem("nanobot.webui.senderId", senderId);
  }

  let chatId = (qs.get("chat") || storage.getItem("nanobot.webui.chatId") || "")
    .trim();
  if (!chatId) {
    chatId = randId("c");
    storage.setItem("nanobot.webui.chatId", chatId);
  } else {
    storage.setItem("nanobot.webui.chatId", chatId);
  }

  let sessionKey = (
    qs.get("session") ||
    storage.getItem("nanobot.webui.sessionKey") ||
    ""
  ).trim();
  if (!sessionKey) sessionKey = "webui:" + chatId;
  storage.setItem("nanobot.webui.sessionKey", sessionKey);
  sessionKeyEl.textContent = sessionKey;

  function toastMsg(text) {
    toast.textContent = text;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 1600);
  }

  function nearBottom() {
    const threshold = 140;
    return feed.scrollHeight - feed.scrollTop - feed.clientHeight < threshold;
  }

  function scrollToBottom() {
    feed.scrollTop = feed.scrollHeight;
  }

  function updateJump() {
    if (nearBottom()) jumpWrap.classList.remove("show");
    else jumpWrap.classList.add("show");
  }

  feed.addEventListener("scroll", updateJump);
  jumpBtn.addEventListener("click", () => {
    scrollToBottom();
    updateJump();
  });

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

  function renderMarkdownish(text) {
    // Safe, tiny renderer: escape first, then add a few tags.
    // Handles fenced code blocks and inline code.
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

  function addRow(role, text, opts) {
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

    feed.appendChild(row);
    if ((opts && opts.autoscroll) || nearBottom()) scrollToBottom();
    updateJump();
    return row;
  }

  function setStatus(kind, text) {
    statusEl.textContent = text;
    dot.classList.remove("ok", "bad");
    if (kind === "ok") dot.classList.add("ok");
    if (kind === "bad") dot.classList.add("bad");
  }

  let serverHistory = [];
  function renderHistory(msgs) {
    serverHistory = Array.isArray(msgs) ? msgs : [];
    feed.innerHTML = "";
    for (const m of serverHistory) addRow(m.role, m.content);
    scrollToBottom();
    updateJump();
  }

  const attachments = [];
  function renderAttachments() {
    attachmentsEl.innerHTML = "";
    for (let i = 0; i < attachments.length; i++) {
      const f = attachments[i];
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
        attachments.splice(i, 1);
        renderAttachments();
      });

      chip.appendChild(name);
      chip.appendChild(x);
      attachmentsEl.appendChild(chip);
    }
  }

  let ws = null;
  let inflight = false;
  let t0 = 0;
  let thinkingRow = null;

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const q = new URLSearchParams();
    q.set("chat_id", chatId);
    q.set("sender_id", senderId);
    q.set("session", sessionKey);
    if (token) q.set("token", token);
    return `${proto}//${location.host}/ws?${q.toString()}`;
  }

  const waiters = [];
  function waitFor(type, pred, timeoutMs) {
    const to = Math.max(0, timeoutMs || 15000);
    return new Promise((resolve, reject) => {
      const w = {
        type,
        pred: pred || (() => true),
        resolve,
        reject,
        exp: Date.now() + to,
      };
      waiters.push(w);
      setTimeout(() => {
        const idx = waiters.indexOf(w);
        if (idx !== -1) waiters.splice(idx, 1);
        reject(new Error("timeout waiting for " + type));
      }, to + 50);
    });
  }

  function connect() {
    setStatus("", "connecting");
    latencyEl.textContent = "";
    try {
      if (ws) ws.close();
    } catch (_) {}

    ws = new WebSocket(wsUrl());

    ws.addEventListener("open", () => {
      setStatus("ok", "online");
      try {
        ws.send(
          JSON.stringify({ type: "hello", chat_id: chatId, sender_id: senderId })
        );
      } catch (_) {}
    });

    ws.addEventListener("message", (ev) => {
      let data = null;
      try {
        data = JSON.parse(ev.data);
      } catch (_) {
        return;
      }

      // Resolve any pending waiters (uploads, sessions list, etc.)
      if (data && data.type) {
        for (let i = waiters.length - 1; i >= 0; i--) {
          const w = waiters[i];
          if (w.type === data.type && w.pred(data)) {
            waiters.splice(i, 1);
            w.resolve(data);
          }
        }
      }

      if (data.type === "session") {
        if (data.chat_id && data.chat_id !== chatId) {
          chatId = String(data.chat_id);
          storage.setItem("nanobot.webui.chatId", chatId);
        }
        if (data.session_key && data.session_key !== sessionKey) {
          sessionKey = String(data.session_key);
          storage.setItem("nanobot.webui.sessionKey", sessionKey);
          sessionKeyEl.textContent = sessionKey;
        }
        if (data.sender_id && data.sender_id !== senderId) {
          senderId = String(data.sender_id);
          storage.setItem("nanobot.webui.senderId", senderId);
        }
        return;
      }

      if (data.type === "history") {
        if (data.session_key && String(data.session_key) !== sessionKey) return;
        renderHistory(data.messages || []);
        return;
      }

      if (data.type === "assistant") {
        if (thinkingRow) {
          thinkingRow.remove();
          thinkingRow = null;
        }
        const c = data.content || "";
        addRow("assistant", c);
        serverHistory.push({ role: "assistant", content: c });

        inflight = false;
        sendBtn.disabled = false;
        input.disabled = false;
        input.focus();

        const dt = performance.now() - t0;
        latencyEl.textContent = dt ? `reply in ${(dt / 1000).toFixed(2)}s` : "";
        return;
      }

      if (data.type === "error") {
        if (thinkingRow) {
          thinkingRow.remove();
          thinkingRow = null;
        }
        inflight = false;
        sendBtn.disabled = false;
        input.disabled = false;
        setStatus("bad", "error");
        addRow("assistant", "Error: " + (data.error || "unknown"));
      }
    });

    ws.addEventListener("close", () => {
      setStatus("bad", "offline");
      // Keep UI responsive, but disable send if not connected.
      setTimeout(connect, 700);
    });

    ws.addEventListener("error", () => {
      setStatus("bad", "offline");
    });
  }

  function autogrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  }

  function b64FromBytes(bytes) {
    // bytes: Uint8Array
    let s = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(s);
  }

  async function uploadFile(file) {
    if (!ws || ws.readyState !== 1) throw new Error("not connected");
    const clientId = randId("cup");

    ws.send(
      JSON.stringify({
        type: "upload_init",
        client_id: clientId,
        filename: file.name,
        mime: file.type || "",
        size: file.size || 0,
      })
    );

    const ready = await waitFor(
      "upload_ready",
      (m) => m && m.client_id === clientId,
      15000
    );
    const uploadId = ready.upload_id;

    const buf = await file.arrayBuffer();
    const u8 = new Uint8Array(buf);
    const step = 192 * 1024;
    for (let off = 0; off < u8.length; off += step) {
      const slice = u8.subarray(off, Math.min(off + step, u8.length));
      ws.send(
        JSON.stringify({
          type: "upload_chunk",
          upload_id: uploadId,
          data: b64FromBytes(slice),
        })
      );
    }

    const done = await waitFor(
      "upload_done",
      (m) => m && m.upload_id === uploadId,
      45000
    );
    return String(done.path || "");
  }

  async function send() {
    const text = (input.value || "").trim();
    if (!text && attachments.length === 0) return;
    if (!ws || ws.readyState !== 1) {
      toastMsg("Not connected yet.");
      return;
    }
    if (inflight) return;

    inflight = true;
    sendBtn.disabled = true;
    input.disabled = true;
    t0 = performance.now();
    latencyEl.textContent = "thinking…";

    addRow("user", text || "(attachment)", { autoscroll: true });
    serverHistory.push({ role: "user", content: text || "" });
    input.value = "";
    autogrow();

    thinkingRow = addRow("assistant", "_Thinking…_", { autoscroll: true });

    try {
      let media = [];
      if (attachments.length) {
        toastMsg("Uploading…");
        for (const f of attachments.splice(0, attachments.length)) {
          const p = await uploadFile(f);
          if (p) media.push(p);
        }
        renderAttachments();
      }
      ws.send(JSON.stringify({ type: "message", content: text, media }));
    } catch (e) {
      inflight = false;
      sendBtn.disabled = false;
      input.disabled = false;
      if (thinkingRow) {
        thinkingRow.remove();
        thinkingRow = null;
      }
      addRow("assistant", "Error sending message: " + String(e));
    }
  }

  input.addEventListener("input", autogrow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  sendBtn.addEventListener("click", send);

  attachBtn.addEventListener("click", () => {
    if (inflight) return;
    fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    const files = Array.from(fileInput.files || []);
    for (const f of files) {
      // Keep it simple: images and PDFs only.
      const t = (f.type || "").toLowerCase();
      if (t.startsWith("image/") || t === "application/pdf") attachments.push(f);
    }
    fileInput.value = "";
    renderAttachments();
  });

  $("clear").addEventListener("click", () => {
    renderHistory([]);
    toastMsg("Cleared view.");
  });

  $("new-chat").addEventListener("click", () => {
    if (inflight) return;
    chatId = randId("c");
    storage.setItem("nanobot.webui.chatId", chatId);
    sessionKey = "webui:" + chatId;
    storage.setItem("nanobot.webui.sessionKey", sessionKey);
    sessionKeyEl.textContent = sessionKey;
    renderHistory([]);
    connect();
    toastMsg("New session.");
  });

  $("sessions").addEventListener("click", async () => {
    if (!ws || ws.readyState !== 1) {
      toastMsg("Not connected yet.");
      return;
    }
    try {
      ws.send(JSON.stringify({ type: "list_sessions" }));
      const resp = await waitFor("sessions", () => true, 8000);
      const items = Array.isArray(resp.sessions) ? resp.sessions : [];
      if (!items.length) {
        toastMsg("No saved sessions.");
        return;
      }
      const lines = items.slice(0, 20).map((s, i) => {
        const k = s.key || "";
        const u = (s.updated_at || "").slice(0, 19).replace("T", " ");
        return `${i + 1}. ${k} ${u ? "(" + u + ")" : ""}`;
      });
      const pick = prompt(
        "Open session by number:\n\n" + lines.join("\n") + "\n\n(Shows up to 20)"
      );
      const n = parseInt(String(pick || "").trim(), 10);
      if (!n || n < 1 || n > Math.min(items.length, 20)) return;
      const key = String(items[n - 1].key || "").trim();
      if (!key) return;
      sessionKey = key;
      storage.setItem("nanobot.webui.sessionKey", sessionKey);
      sessionKeyEl.textContent = sessionKey;
      renderHistory([]);
      connect();
    } catch (e) {
      toastMsg("Failed to load sessions.");
    }
  });

  $("copy-link").addEventListener("click", async () => {
    const u = new URL(location.href);
    u.searchParams.set("chat", chatId);
    u.searchParams.set("session", sessionKey);
    if (token) u.searchParams.set("token", token);
    try {
      await navigator.clipboard.writeText(u.toString());
      toastMsg("Link copied.");
    } catch (_) {
      toastMsg("Clipboard blocked.");
    }
  });

  // First paint.
  autogrow();
  renderHistory([]);
  renderAttachments();
  connect();
  setTimeout(() => input.focus(), 60);
})();
