(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const feed = $("feed");
  const input = $("input");
  const sendBtn = $("send");
  const statusEl = $("status");
  const dot = $("dot");
  const chatIdEl = $("chatid");
  const toast = $("toast");
  const jumpWrap = $("jump");
  const jumpBtn = $("jump-btn");
  const latencyEl = $("latency");

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

  chatIdEl.textContent = chatId.replace(/^c:/, "");

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

  function keyForChatMessages(cid) {
    return "nanobot.webui.messages." + cid;
  }

  function loadMessages(cid) {
    try {
      const raw = storage.getItem(keyForChatMessages(cid));
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (_) {
      return [];
    }
  }

  function saveMessages(cid, msgs) {
    try {
      storage.setItem(keyForChatMessages(cid), JSON.stringify(msgs.slice(-200)));
    } catch (_) {}
  }

  function pushMessage(role, content) {
    const msgs = loadMessages(chatId);
    msgs.push({ role, content: String(content ?? ""), ts: Date.now() });
    saveMessages(chatId, msgs);
  }

  function renderHistory() {
    feed.innerHTML = "";
    for (const m of loadMessages(chatId)) addRow(m.role, m.content);
    scrollToBottom();
    updateJump();
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
    if (token) q.set("token", token);
    return `${proto}//${location.host}/ws?${q.toString()}`;
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

      if (data.type === "session") {
        if (data.chat_id && data.chat_id !== chatId) {
          chatId = String(data.chat_id);
          storage.setItem("nanobot.webui.chatId", chatId);
          chatIdEl.textContent = chatId.replace(/^c:/, "");
          renderHistory();
        }
        if (data.sender_id && data.sender_id !== senderId) {
          senderId = String(data.sender_id);
          storage.setItem("nanobot.webui.senderId", senderId);
        }
        return;
      }

      if (data.type === "assistant") {
        if (thinkingRow) {
          thinkingRow.remove();
          thinkingRow = null;
        }
        const c = data.content || "";
        addRow("assistant", c);
        pushMessage("assistant", c);

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

  function send() {
    const text = (input.value || "").trim();
    if (!text) return;
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

    addRow("user", text, { autoscroll: true });
    pushMessage("user", text);
    input.value = "";
    autogrow();

    thinkingRow = addRow("assistant", "_Thinking…_", { autoscroll: true });

    try {
      ws.send(JSON.stringify({ type: "message", content: text }));
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

  $("clear").addEventListener("click", () => {
    feed.innerHTML = "";
    saveMessages(chatId, []);
    toastMsg("Cleared.");
  });

  $("new-chat").addEventListener("click", () => {
    if (inflight) return;
    chatId = randId("c");
    storage.setItem("nanobot.webui.chatId", chatId);
    chatIdEl.textContent = chatId.replace(/^c:/, "");
    renderHistory();
    connect();
    toastMsg("New session.");
  });

  $("copy-link").addEventListener("click", async () => {
    const u = new URL(location.href);
    u.searchParams.set("chat", chatId);
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
  renderHistory();
  connect();
  setTimeout(() => input.focus(), 60);
})();

