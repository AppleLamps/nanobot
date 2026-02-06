/* app.js — entry point: event wiring, send(), initialisation */

import { dom, state, randId, persist, MODEL_PRESETS } from "./state.js";
import {
  toastMsg,
  autogrow,
  addRow,
  renderHistory,
  renderAttachments,
  renderSessions,
  updateEmpty,
  updateJump,
  scrollToBottom,
} from "./render.js";
import { connect, uploadFile, waitFor } from "./connection.js";

/* --- Fill model datalist --- */

function fillModelDatalist() {
  if (!dom.modelData) return;
  dom.modelData.innerHTML = "";
  for (const m of MODEL_PRESETS) {
    const o = document.createElement("option");
    o.value = m;
    dom.modelData.appendChild(o);
  }
  /* "Custom" hint — users can type any OpenRouter model name */
  const custom = document.createElement("option");
  custom.value = "";
  custom.label = "Custom (type your openrouter model name)";
  dom.modelData.appendChild(custom);
}

/* --- Send message --- */

async function send() {
  const text = String(dom.input.value || "").trim();
  if (!text && state.attachments.length === 0) return;

  if (!state.ws || state.ws.readyState !== 1) {
    toastMsg("Not connected yet.");
    return;
  }
  if (state.inflight) return;

  state.inflight = true;
  if (dom.sendBtn) dom.sendBtn.disabled = true;
  if (dom.input) dom.input.disabled = true;
  state.t0 = performance.now();
  if (dom.latency) dom.latency.textContent = "thinking...";

  addRow("user", text || "(attachment)", { autoscroll: true });
  state.serverHistory.push({ role: "user", content: text || "" });

  dom.input.value = "";
  autogrow();

  state.thinkingRow = addRow("assistant", "_Thinking..._", { autoscroll: true });

  try {
    let media = [];
    if (state.attachments.length) {
      toastMsg("Uploading...");
      for (const f of state.attachments.splice(0, state.attachments.length)) {
        const p = await uploadFile(f);
        if (p) media.push(p);
      }
      renderAttachments();
    }

    const payload = { type: "message", content: text, media };
    if (state.currentModel) payload.model = state.currentModel;
    state.ws.send(JSON.stringify(payload));
  } catch (e) {
    state.inflight = false;
    if (dom.sendBtn) dom.sendBtn.disabled = false;
    if (dom.input) dom.input.disabled = false;
    if (state.thinkingRow) {
      state.thinkingRow.remove();
      state.thinkingRow = null;
    }
    addRow("assistant", "Error sending message: " + String(e));
  }
}

/* --- Model --- */

function applyModel() {
  if (!state.ws || state.ws.readyState !== 1) {
    toastMsg("Not connected yet.");
    return;
  }

  const v = String((dom.modelInput && dom.modelInput.value) || "").trim();
  if (v) {
    state.modelDefault = v;
    persist("modelDefault", state.modelDefault);
  }

  try {
    state.ws.send(JSON.stringify({ type: "set_model", model: v }));
    toastMsg(v ? "Model set." : "Model cleared.");
  } catch (_) {
    toastMsg("Failed to set model.");
  }
}

/* --- Sessions modal --- */

function openSessionsModal() {
  if (!dom.sessionsModal) return;
  dom.sessionsModal.classList.add("show");
  dom.sessionsModal.setAttribute("aria-hidden", "false");
}

function closeSessionsModal() {
  if (!dom.sessionsModal) return;
  dom.sessionsModal.classList.remove("show");
  dom.sessionsModal.setAttribute("aria-hidden", "true");
}

/* --- Wire up events --- */

/* Feed scroll / jump */
if (dom.feed) dom.feed.addEventListener("scroll", updateJump);
if (dom.jumpBtn)
  dom.jumpBtn.addEventListener("click", () => {
    scrollToBottom();
    updateJump();
  });

/* Input / send */
if (dom.input) {
  dom.input.addEventListener("input", autogrow);
  dom.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
}
if (dom.sendBtn) dom.sendBtn.addEventListener("click", send);

/* Attach */
if (dom.attachBtn)
  dom.attachBtn.addEventListener("click", () => {
    if (state.inflight) return;
    if (dom.fileInput) dom.fileInput.click();
  });

if (dom.fileInput)
  dom.fileInput.addEventListener("change", () => {
    const files = Array.from(dom.fileInput.files || []);
    for (const f of files) {
      const t = String(f.type || "").toLowerCase();
      if (t.startsWith("image/") || t === "application/pdf") state.attachments.push(f);
    }
    dom.fileInput.value = "";
    renderAttachments();
  });

/* Clear */
if (dom.clearBtn)
  dom.clearBtn.addEventListener("click", () => {
    renderHistory([]);
    toastMsg("Cleared view.");
  });

/* New chat */
if (dom.newChatBtn)
  dom.newChatBtn.addEventListener("click", () => {
    if (state.inflight) return;
    if (state.ws && state.ws.readyState === 1) {
      state.pendingNewChatDefaultModel = true;
      renderHistory([]);
      state.ws.send(JSON.stringify({ type: "new_chat" }));
      toastMsg("New session.");
      return;
    }

    state.chatId = randId("c");
    persist("chatId", state.chatId);
    state.sessionKey = "webui:" + state.chatId;
    persist("sessionKey", state.sessionKey);
    if (dom.sessionKey) dom.sessionKey.textContent = state.sessionKey;
    state.pendingNewChatDefaultModel = true;
    renderHistory([]);
    connect();
    toastMsg("New session.");
  });

/* Sessions */
if (dom.sessionsBtn)
  dom.sessionsBtn.addEventListener("click", async () => {
    if (!state.ws || state.ws.readyState !== 1) {
      toastMsg("Not connected yet.");
      return;
    }

    try {
      state.ws.send(JSON.stringify({ type: "list_sessions" }));
      const resp = await waitFor("sessions", () => true, 8000);
      const items = Array.isArray(resp.sessions) ? resp.sessions : [];
      renderSessions(items.slice(0, 100), {
        onSwitch(key) {
          if (!state.ws || state.ws.readyState !== 1) return;
          try {
            renderHistory([]);
            state.ws.send(JSON.stringify({ type: "switch_session", session_key: key }));
            closeSessionsModal();
            toastMsg("Opened session.");
          } catch (_) { }
        },
      });
      openSessionsModal();
    } catch (_) {
      toastMsg("Failed to load sessions.");
    }
  });

if (dom.sessionsClose) dom.sessionsClose.addEventListener("click", closeSessionsModal);
if (dom.sessionsModal)
  dom.sessionsModal.addEventListener("click", (e) => {
    if (e.target === dom.sessionsModal) closeSessionsModal();
  });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSessionsModal();
});

/* Copy link */
if (dom.copyBtn)
  dom.copyBtn.addEventListener("click", async () => {
    const u = new URL(location.href);
    u.searchParams.set("chat", state.chatId);
    u.searchParams.set("session", state.sessionKey);
    if (state.token) u.searchParams.set("token", state.token);
    if (state.currentModel) u.searchParams.set("model", state.currentModel);

    try {
      await navigator.clipboard.writeText(u.toString());
      toastMsg("Link copied.");
    } catch (_) {
      toastMsg("Clipboard blocked.");
    }
  });

/* Model */
if (dom.applyModelBtn) dom.applyModelBtn.addEventListener("click", applyModel);
if (dom.modelInput)
  dom.modelInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      applyModel();
    }
  });

/* Quick-start chips */
for (const b of Array.from(document.querySelectorAll(".chipbtn"))) {
  b.addEventListener("click", () => {
    const p = String(b.getAttribute("data-prompt") || "");
    if (!p) return;
    dom.input.value = p;
    autogrow();
    dom.input.focus();
  });
}

/* --- Init --- */

if (dom.sessionKey) dom.sessionKey.textContent = state.sessionKey;
if (dom.modelPill) dom.modelPill.textContent = "default";
if (dom.modelInput) dom.modelInput.value = "";

fillModelDatalist();
autogrow();
renderHistory([]);
renderAttachments();
updateEmpty();
connect();
setTimeout(() => dom.input && dom.input.focus(), 60);
