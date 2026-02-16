/* connection.js — WebSocket connection, message handling, file upload */

import { dom, state, randId, persist } from "./state.js";
import {
  setStatus,
  toastMsg,
  addRow,
  renderMarkdown,
  renderHistory,
  updateEmpty,
  renderSubagents,
} from "./render.js";

/* --- Waiter system for request/response over WS --- */

export function waitFor(type, pred, timeoutMs) {
  const to = Math.max(0, timeoutMs || 15000);
  return new Promise((resolve, reject) => {
    const w = {
      type,
      pred: pred || (() => true),
      resolve,
      reject,
      exp: Date.now() + to,
    };
    state.waiters.push(w);
    setTimeout(() => {
      const idx = state.waiters.indexOf(w);
      if (idx !== -1) state.waiters.splice(idx, 1);
      reject(new Error("timeout waiting for " + type));
    }, to + 50);
  });
}

/* --- WebSocket URL --- */

export function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const q = new URLSearchParams();
  q.set("chat_id", state.chatId);
  q.set("sender_id", state.senderId);
  q.set("session", state.sessionKey);
  if (state.token) q.set("token", state.token);
  return `${proto}//${location.host}/ws?${q.toString()}`;
}

/* --- Connect --- */

let _connectGen = 0;
let _reconnectDelay = 700;
const _RECONNECT_MIN = 700;
const _RECONNECT_MAX = 15000;

export function connect() {
  const gen = ++_connectGen;
  _reconnectDelay = _RECONNECT_MIN;
  setStatus("", "connecting");
  if (dom.latency) dom.latency.textContent = "";

  try {
    if (state.ws) {
      state.ws._noreconnect = true;
      state.ws.close();
    }
  } catch (_) { }

  state.ws = new WebSocket(wsUrl());

  state.ws.addEventListener("open", () => {
    setStatus("ok", "online");
    _reconnectDelay = _RECONNECT_MIN;
    try {
      state.ws.send(
        JSON.stringify({
          type: "hello",
          chat_id: state.chatId,
          sender_id: state.senderId,
        })
      );
    } catch (_) { }
  });

  state.ws.addEventListener("message", (ev) => {
    let data = null;
    try {
      data = JSON.parse(ev.data);
    } catch (_) {
      return;
    }

    /* Resolve any pending waiters */
    if (data && data.type) {
      for (let i = state.waiters.length - 1; i >= 0; i--) {
        const w = state.waiters[i];
        if (w.type === data.type && w.pred(data)) {
          state.waiters.splice(i, 1);
          w.resolve(data);
        }
      }
    }

    if (data.type === "session") {
      if (data.chat_id && String(data.chat_id) !== state.chatId) {
        state.chatId = String(data.chat_id);
        persist("chatId", state.chatId);
      }
      if (data.session_key && String(data.session_key) !== state.sessionKey) {
        state.sessionKey = String(data.session_key);
        persist("sessionKey", state.sessionKey);
        if (dom.sessionKey) dom.sessionKey.textContent = state.sessionKey;
      }
      if (data.sender_id && String(data.sender_id) !== state.senderId) {
        state.senderId = String(data.sender_id);
        persist("senderId", state.senderId);
      }
      return;
    }

    if (data.type === "history") {
      if (data.session_key && String(data.session_key) !== state.sessionKey) return;
      const msgs = data.messages || [];
      state.lastHistoryEmpty = !Array.isArray(msgs) || msgs.length === 0;
      renderHistory(msgs);
      return;
    }

    if (data.type === "settings") {
      if (data.session_key && String(data.session_key) !== state.sessionKey) return;
      state.currentModel = String(data.model || "").trim();
      const v = String(data.verbosity || "").trim();
      const rw = data.restrict_workspace;
      if (v) state.verbosity = v;
      if (typeof rw === "boolean") state.restrictWorkspace = rw;
      if (state.currentModel) {
        state.modelDefault = state.currentModel;
        persist("modelDefault", state.modelDefault);
      }
      if (dom.modelPill) dom.modelPill.textContent = state.currentModel || "default";
      if (dom.verbositySelect) dom.verbositySelect.value = state.verbosity || "normal";
      if (dom.restrictWorkspaceToggle)
        dom.restrictWorkspaceToggle.checked = !!state.restrictWorkspace;

      if (
        state.pendingNewChatDefaultModel &&
        state.lastHistoryEmpty &&
        !state.currentModel &&
        state.modelDefault
      ) {
        try {
          state.ws.send(JSON.stringify({ type: "set_model", model: state.modelDefault }));
        } catch (_) { }
      }
      if (
        state.pendingNewChatDefaultVerbosity &&
        state.lastHistoryEmpty &&
        !v &&
        state.verbosity
      ) {
        try {
          state.ws.send(JSON.stringify({ type: "set_verbosity", verbosity: state.verbosity }));
        } catch (_) { }
      }
      if (
        state.pendingNewChatDefaultRestrictWorkspace &&
        state.lastHistoryEmpty &&
        typeof rw !== "boolean" &&
        !state.restrictWorkspace
      ) {
        try {
          state.ws.send(
            JSON.stringify({
              type: "set_restrict_workspace",
              restrict_workspace: false,
            })
          );
        } catch (_) { }
      }
      state.pendingNewChatDefaultModel = false;
      state.pendingNewChatDefaultVerbosity = false;
      state.pendingNewChatDefaultRestrictWorkspace = false;
      return;
    }

    if (data.type === "subagents") {
      const list = (data.data && data.data.tasks) || [];
      state.subagents = Array.isArray(list) ? list : [];
      renderSubagents(state.subagents, {
        onCancel(taskId) {
          if (!taskId) return;
          if (!state.ws || state.ws.readyState !== 1) return;
          state.ws.send(JSON.stringify({ type: "subagent_cancel", task_id: taskId }));
        },
      });
      return;
    }

    if (data.type === "subagent_event") {
      const ok = data.data && data.data.ok;
      const msg = String(data.content || "").trim();
      if (msg) toastMsg(msg);
      else if (ok === true) toastMsg("Subagent updated.");
      else if (ok === false) toastMsg("Subagent action failed.");
      try {
        if (state.ws && state.ws.readyState === 1) {
          state.ws.send(JSON.stringify({ type: "subagent_list" }));
        }
      } catch (_) { }
      return;
    }

    if (data.type === "status") {
      const c = String(data.content || "").trim();
      if (state.thinkingRow && c) {
        const node = state.thinkingRow.querySelector(".content");
        if (node) {
          node.innerHTML = "";
          node.appendChild(renderMarkdown(c));
        }
      } else if (dom.latency && c) {
        dom.latency.textContent = c;
      }
      return;
    }

    if (data.type === "assistant") {
      if (state.thinkingRow) {
        state.thinkingRow.remove();
        state.thinkingRow = null;
      }

      /* Stop elapsed timer */
      if (state._timerInterval) {
        clearInterval(state._timerInterval);
        state._timerInterval = null;
      }

      const c = data.content || "";
      const msgTs = data.ts || (Date.now() / 1000);
      addRow("assistant", c, { ts: msgTs });
      state.serverHistory.push({ role: "assistant", content: c, ts: msgTs });

      state.inflight = false;
      if (dom.sendBtn) dom.sendBtn.disabled = false;
      if (dom.input) dom.input.disabled = false;
      if (dom.input) dom.input.focus();

      const dt = performance.now() - state.t0;
      if (dom.latency)
        dom.latency.textContent = dt ? `reply in ${(dt / 1000).toFixed(2)}s` : "";

      /* Show final elapsed time in status bar */
      if (dom.statusBar && dt) {
        const elapsed = Math.floor(dt / 1000);
        const m = Math.floor(elapsed / 60);
        const s = elapsed % 60;
        dom.statusBar.textContent = m > 0
          ? `Worked for ${m}m ${s}s`
          : `Worked for ${s}s`;
        setTimeout(() => {
          if (dom.statusBar) dom.statusBar.textContent = "";
        }, 10000);
      }
      return;
    }

    if (data.type === "error") {
      /* If server rejects restrict_workspace=false, reset toggle and warn */
      if (data.error && data.error.includes("restrict_workspace=false")) {
        state.restrictWorkspace = true;
        persist("restrictWorkspace", "true");
        if (dom.restrictWorkspaceToggle) dom.restrictWorkspaceToggle.checked = true;
        console.warn("[nanobot] restrict_workspace=false rejected by server; enable allowUnrestrictedWorkspace in config");
        setStatus("ok", "connected");
        return;
      }
      if (state.thinkingRow) {
        state.thinkingRow.remove();
        state.thinkingRow = null;
      }
      /* Stop elapsed timer */
      if (state._timerInterval) {
        clearInterval(state._timerInterval);
        state._timerInterval = null;
      }
      if (dom.statusBar) dom.statusBar.textContent = "";
      state.inflight = false;
      if (dom.sendBtn) dom.sendBtn.disabled = false;
      if (dom.input) dom.input.disabled = false;
      setStatus("bad", "error");
      addRow("assistant", "Error: " + (data.error || "unknown"));
    }
  });

  state.ws.addEventListener("close", (ev) => {
    /* Don't reconnect if this WS was intentionally replaced by a newer connect() call. */
    if (ev.target._noreconnect) return;
    if (gen !== _connectGen) return;

    /* Server kicked us as a duplicate session — another tab/connection owns this session.
       Show a recoverable status instead of permanently dying. The user can click to
       reconnect (useful when the other tab was stale or is now closed). */
    if (ev.code === 4400) {
      setStatus("bad", "duplicate session — click status to reconnect");
      /* Allow clicking the status dot / text to reconnect manually. */
      const _reconnectOnClick = () => {
        if (dom.dot) dom.dot.removeEventListener("click", _reconnectOnClick);
        if (dom.status) dom.status.removeEventListener("click", _reconnectOnClick);
        connect();
      };
      if (dom.dot) dom.dot.addEventListener("click", _reconnectOnClick, { once: true });
      if (dom.status) dom.status.addEventListener("click", _reconnectOnClick, { once: true });
      return;
    }

    setStatus("bad", "offline");
    setTimeout(connect, _reconnectDelay);
    _reconnectDelay = Math.min(_reconnectDelay * 2, _RECONNECT_MAX);
  });

  state.ws.addEventListener("error", () => {
    setStatus("bad", "offline");
  });
}

/* --- File upload --- */

function b64FromBytes(bytes) {
  let s = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(s);
}

export async function uploadFile(file) {
  if (!state.ws || state.ws.readyState !== 1) throw new Error("not connected");
  const clientId = randId("cup");

  state.ws.send(
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
    state.ws.send(
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
