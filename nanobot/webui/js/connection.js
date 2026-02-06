/* connection.js — WebSocket connection, message handling, file upload */

import { dom, state, randId, persist } from "./state.js";
import {
  setStatus,
  toastMsg,
  addRow,
  renderMarkdownish,
  renderHistory,
  updateEmpty,
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

export function connect() {
  setStatus("", "connecting");
  if (dom.latency) dom.latency.textContent = "";

  try {
    if (state.ws) state.ws.close();
  } catch (_) { }

  state.ws = new WebSocket(wsUrl());

  state.ws.addEventListener("open", () => {
    setStatus("ok", "online");
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
      if (v) state.verbosity = v;
      if (state.currentModel) {
        state.modelDefault = state.currentModel;
        persist("modelDefault", state.modelDefault);
      }
      if (dom.modelPill) dom.modelPill.textContent = state.currentModel || "default";
      if (dom.modelInput) dom.modelInput.value = state.currentModel || state.modelDefault || "";
      if (dom.verbositySelect) dom.verbositySelect.value = state.verbosity || "normal";

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
      state.pendingNewChatDefaultModel = false;
      state.pendingNewChatDefaultVerbosity = false;
      return;
    }

    if (data.type === "status") {
      const c = String(data.content || "").trim();
      if (state.thinkingRow && c) {
        const node = state.thinkingRow.querySelector(".content");
        if (node) {
          node.innerHTML = "";
          node.appendChild(renderMarkdownish(c));
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

      const c = data.content || "";
      addRow("assistant", c);
      state.serverHistory.push({ role: "assistant", content: c });

      state.inflight = false;
      if (dom.sendBtn) dom.sendBtn.disabled = false;
      if (dom.input) dom.input.disabled = false;
      if (dom.input) dom.input.focus();

      const dt = performance.now() - state.t0;
      if (dom.latency)
        dom.latency.textContent = dt ? `reply in ${(dt / 1000).toFixed(2)}s` : "";
      return;
    }

    if (data.type === "error") {
      if (state.thinkingRow) {
        state.thinkingRow.remove();
        state.thinkingRow = null;
      }
      state.inflight = false;
      if (dom.sendBtn) dom.sendBtn.disabled = false;
      if (dom.input) dom.input.disabled = false;
      setStatus("bad", "error");
      addRow("assistant", "Error: " + (data.error || "unknown"));
    }
  });

  state.ws.addEventListener("close", () => {
    setStatus("bad", "offline");
    setTimeout(connect, 700);
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
