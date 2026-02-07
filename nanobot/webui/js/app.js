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

/* --- Model picker helpers --- */

function fmtPrice(perToken) {
  const n = parseFloat(perToken || "0");
  if (n === 0) return "Free";
  const perM = n * 1_000_000;
  if (perM < 0.01) return `$${perM.toFixed(4)}`;
  if (perM < 1) return `$${perM.toFixed(2)}`;
  return `$${perM.toFixed(2)}`;
}

function fmtCtx(n) {
  if (!n) return "";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

function shortName(name, id) {
  /* Strip provider prefix from display name, e.g. "Anthropic: Claude Opus 4.6" -> "Claude Opus 4.6" */
  const colon = name.indexOf(":");
  if (colon > 0 && colon < name.length - 1) return name.slice(colon + 1).trim();
  return name;
}

function providerOf(id) {
  const slash = id.indexOf("/");
  return slash > 0 ? id.slice(0, slash) : id;
}

const PRESET_SET = new Set(MODEL_PRESETS);

async function fetchModels() {
  if (state.allModels) return state.allModels;
  try {
    const qs = new URLSearchParams();
    if (state.token) qs.set("token", state.token);
    const url = "/api/models" + (qs.toString() ? `?${qs}` : "");
    const resp = await fetch(url, { cache: "default" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    state.allModels = await resp.json();
  } catch (e) {
    toastMsg("Failed to load models.");
    state.allModels = [];
  }
  return state.allModels;
}

function renderModelsList(query, toolsOnly, freeOnly) {
  if (!dom.modelsBody) return;
  const models = state.allModels || [];
  const q = (query || "").toLowerCase();

  /* filter */
  const filtered = models.filter((m) => {
    if (q && !m.id.toLowerCase().includes(q) && !m.name.toLowerCase().includes(q)) return false;
    if (toolsOnly && !m.tools) return false;
    if (freeOnly && parseFloat(m.prompt || "0") === 0 && parseFloat(m.completion || "0") === 0) {
      /* keep free models */
    } else if (freeOnly) {
      return false;
    }
    return true;
  });

  /* group: recommended first, then by provider */
  const recommended = [];
  const groups = new Map();

  for (const m of filtered) {
    if (PRESET_SET.has(m.id)) recommended.push(m);
    const prov = providerOf(m.id);
    if (!groups.has(prov)) groups.set(prov, []);
    groups.get(prov).push(m);
  }

  /* sort groups alphabetically */
  const sortedProviders = [...groups.keys()].sort((a, b) => a.localeCompare(b));

  /* build DOM */
  const frag = document.createDocumentFragment();

  /* "Use default" row */
  const defRow = document.createElement("button");
  defRow.className = "model-item model-item--default";
  defRow.innerHTML = `<span class="mi-name">Use server default</span><span class="mi-id">Clears model override</span>`;
  defRow.addEventListener("click", () => selectModel(""));
  frag.appendChild(defRow);

  /* Recommended group */
  if (recommended.length > 0 && !q) {
    const grp = buildGroup("Recommended", recommended);
    frag.appendChild(grp);
  }

  /* Provider groups */
  for (const prov of sortedProviders) {
    const items = groups.get(prov);
    const label = prov.charAt(0).toUpperCase() + prov.slice(1);
    frag.appendChild(buildGroup(label, items));
  }

  if (filtered.length === 0) {
    const empty = document.createElement("div");
    empty.className = "models-empty";
    empty.textContent = "No models match your search.";
    frag.appendChild(empty);
  }

  dom.modelsBody.innerHTML = "";
  dom.modelsBody.appendChild(frag);
}

function buildGroup(label, items) {
  const grp = document.createElement("div");
  grp.className = "models-group";
  const head = document.createElement("div");
  head.className = "models-group-head";
  head.textContent = label;
  grp.appendChild(head);

  for (const m of items) {
    const row = document.createElement("button");
    row.className = "model-item" + (!m.tools ? " model-item--dim" : "");
    if (m.id === (state.currentModel || state.modelDefault)) row.classList.add("model-item--active");

    const isFree = parseFloat(m.prompt || "0") === 0 && parseFloat(m.completion || "0") === 0;
    const badges = [];
    if (isFree) badges.push(`<span class="mi-badge mi-badge--free">Free</span>`);
    if (m.tools) badges.push(`<span class="mi-badge mi-badge--tools" title="Tools support">fn</span>`);
    if (m.vision) badges.push(`<span class="mi-badge mi-badge--vision" title="Vision/image input">img</span>`);

    const priceStr = isFree
      ? ""
      : `<span class="mi-price">${fmtPrice(m.prompt)} in / ${fmtPrice(m.completion)} out</span>`;
    const ctxStr = m.ctx ? `<span class="mi-ctx">${fmtCtx(m.ctx)} ctx</span>` : "";

    row.innerHTML = `
      <div class="mi-left">
        <span class="mi-name">${shortName(m.name, m.id)}</span>
        <span class="mi-id">${m.id}</span>
      </div>
      <div class="mi-right">
        ${badges.join("")}
        ${priceStr}
        ${ctxStr}
      </div>`;

    row.addEventListener("click", () => selectModel(m.id));
    grp.appendChild(row);
  }
  return grp;
}

/* --- Model selection --- */

function selectModel(modelId) {
  const v = (modelId || "").trim();
  applyModel(v);
  closeModelsModal();
}

function applyModel(modelId) {
  if (!state.ws || state.ws.readyState !== 1) {
    toastMsg("Not connected yet.");
    return;
  }

  const v = (typeof modelId === "string" ? modelId : "").trim();
  state.modelDefault = v;
  persist("modelDefault", state.modelDefault);
  if (dom.modelPill) dom.modelPill.textContent = v || "default";

  try {
    state.ws.send(JSON.stringify({ type: "set_model", model: v }));
    toastMsg(v ? "Model set." : "Model cleared.");
  } catch (_) {
    toastMsg("Failed to set model.");
  }
}

/* --- Models modal --- */

async function openModelsModal() {
  if (!dom.modelsModal) return;
  dom.modelsModal.classList.add("show");
  dom.modelsModal.setAttribute("aria-hidden", "false");
  if (dom.modelsSearch) {
    dom.modelsSearch.value = "";
    dom.modelsSearch.focus();
  }
  await fetchModels();
  const toolsOnly = dom.modelsToolsOnly ? dom.modelsToolsOnly.checked : false;
  const freeOnly = dom.modelsFreeOnly ? dom.modelsFreeOnly.checked : false;
  renderModelsList("", toolsOnly, freeOnly);
}

function closeModelsModal() {
  if (!dom.modelsModal) return;
  dom.modelsModal.classList.remove("show");
  dom.modelsModal.setAttribute("aria-hidden", "true");
}

function onModelsFilter() {
  const q = dom.modelsSearch ? dom.modelsSearch.value : "";
  const toolsOnly = dom.modelsToolsOnly ? dom.modelsToolsOnly.checked : false;
  const freeOnly = dom.modelsFreeOnly ? dom.modelsFreeOnly.checked : false;
  renderModelsList(q, toolsOnly, freeOnly);
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

  /* Elapsed timer in status bar */
  if (state._timerInterval) clearInterval(state._timerInterval);
  if (dom.statusBar) {
    dom.statusBar.textContent = "Working for 0s...";
    state._timerInterval = setInterval(() => {
      const elapsed = Math.floor((performance.now() - state.t0) / 1000);
      const m = Math.floor(elapsed / 60);
      const s = elapsed % 60;
      dom.statusBar.textContent = m > 0
        ? `Working for ${m}m ${s}s...`
        : `Working for ${s}s...`;
    }, 1000);
  }

  const userAttachments = [...state.attachments];
  addRow("user", text || "(attachment)", { autoscroll: true, attachments: userAttachments });
  state.serverHistory.push({ role: "user", content: text || "", attachments: userAttachments });

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

    const effectiveModel = state.currentModel || state.modelDefault;
    if (effectiveModel && state.currentModel !== effectiveModel) {
      try {
        state.ws.send(JSON.stringify({ type: "set_model", model: effectiveModel }));
      } catch (_) { }
    }

    const payload = { type: "message", content: text, media };
    if (effectiveModel) payload.model = effectiveModel;
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

/* --- Settings modal --- */

function openSettingsModal() {
  if (!dom.settingsModal) return;
  dom.settingsModal.classList.add("show");
  dom.settingsModal.setAttribute("aria-hidden", "false");
  requestSubagents();
}

function closeSettingsModal() {
  if (!dom.settingsModal) return;
  dom.settingsModal.classList.remove("show");
  dom.settingsModal.setAttribute("aria-hidden", "true");
}

function applyVerbosity() {
  if (!dom.verbositySelect) return;
  const v = String(dom.verbositySelect.value || "normal").trim();
  state.verbosity = v || "normal";
  persist("verbosity", state.verbosity);

  if (!state.ws || state.ws.readyState !== 1) {
    toastMsg("Not connected yet.");
    return;
  }

  try {
    state.ws.send(JSON.stringify({ type: "set_verbosity", verbosity: state.verbosity }));
    toastMsg("Verbosity set.");
  } catch (_) {
    toastMsg("Failed to set verbosity.");
  }
}

function applyRestrictWorkspace() {
  if (!dom.restrictWorkspaceToggle) return;
  state.restrictWorkspace = !!dom.restrictWorkspaceToggle.checked;
  persist("restrictWorkspace", state.restrictWorkspace ? "true" : "false");

  if (!state.ws || state.ws.readyState !== 1) {
    toastMsg("Not connected yet.");
    return;
  }

  try {
    state.ws.send(
      JSON.stringify({
        type: "set_restrict_workspace",
        restrict_workspace: !!state.restrictWorkspace,
      })
    );
    toastMsg("Workspace restriction updated.");
  } catch (_) {
    toastMsg("Failed to update restriction.");
  }
}

async function downloadLogs() {
  const qs = new URLSearchParams();
  if (state.token) qs.set("token", state.token);
  const url = "/logs" + (qs.toString() ? `?${qs.toString()}` : "");

  try {
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) {
      const msg = await resp.text().catch(() => "");
      const detail = msg.trim() ? ` ${msg.trim()}` : "";
      throw new Error(`HTTP ${resp.status}${detail}`);
    }
    const blob = await resp.blob();
    const a = document.createElement("a");
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    a.href = URL.createObjectURL(blob);
    a.download = `nanobot-log-${ts}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
    toastMsg("Logs downloaded.");
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    toastMsg("Failed to download logs." + (msg ? " " + msg : ""));
  }
}

function requestSubagents() {
  if (!state.ws || state.ws.readyState !== 1) return;
  try {
    state.ws.send(JSON.stringify({ type: "subagent_list" }));
  } catch (_) { }
}

function spawnSubagent() {
  if (!state.ws || state.ws.readyState !== 1) {
    toastMsg("Not connected yet.");
    return;
  }
  const task = String(dom.subagentTaskInput && dom.subagentTaskInput.value || "").trim();
  const label = String(dom.subagentLabelInput && dom.subagentLabelInput.value || "").trim();
  if (!task) {
    toastMsg("Add a task to spawn.");
    return;
  }
  try {
    state.ws.send(JSON.stringify({ type: "subagent_spawn", task, label }));
    if (dom.subagentTaskInput) dom.subagentTaskInput.value = "";
    if (dom.subagentLabelInput) dom.subagentLabelInput.value = "";
    toastMsg("Subagent spawned.");
    setTimeout(requestSubagents, 400);
  } catch (_) {
    toastMsg("Failed to spawn subagent.");
  }
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
      state.pendingNewChatDefaultVerbosity = true;
      state.pendingNewChatDefaultRestrictWorkspace = true;
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
    state.pendingNewChatDefaultVerbosity = true;
    state.pendingNewChatDefaultRestrictWorkspace = true;
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
  if (e.key === "Escape") {
    closeSessionsModal();
    closeSettingsModal();
    closeModelsModal();
  }
});

/* Settings */
if (dom.settingsBtn) dom.settingsBtn.addEventListener("click", openSettingsModal);
if (dom.settingsClose) dom.settingsClose.addEventListener("click", closeSettingsModal);
if (dom.settingsModal)
  dom.settingsModal.addEventListener("click", (e) => {
    if (e.target === dom.settingsModal) closeSettingsModal();
  });
if (dom.verbositySelect)
  dom.verbositySelect.addEventListener("change", applyVerbosity);
if (dom.restrictWorkspaceToggle)
  dom.restrictWorkspaceToggle.addEventListener("change", applyRestrictWorkspace);
if (dom.downloadLogsBtn)
  dom.downloadLogsBtn.addEventListener("click", downloadLogs);
if (dom.subagentSpawnBtn)
  dom.subagentSpawnBtn.addEventListener("click", spawnSubagent);
if (dom.subagentRefreshBtn)
  dom.subagentRefreshBtn.addEventListener("click", requestSubagents);

/* Model picker */
if (dom.modelTrigger) dom.modelTrigger.addEventListener("click", openModelsModal);
if (dom.modelsClose) dom.modelsClose.addEventListener("click", closeModelsModal);
if (dom.modelsModal)
  dom.modelsModal.addEventListener("click", (e) => {
    if (e.target === dom.modelsModal) closeModelsModal();
  });
if (dom.modelsSearch)
  dom.modelsSearch.addEventListener("input", onModelsFilter);
if (dom.modelsToolsOnly)
  dom.modelsToolsOnly.addEventListener("change", onModelsFilter);
if (dom.modelsFreeOnly)
  dom.modelsFreeOnly.addEventListener("change", onModelsFilter);
if (dom.modelsCustomApply)
  dom.modelsCustomApply.addEventListener("click", () => {
    const v = String(dom.modelsCustomInput && dom.modelsCustomInput.value || "").trim();
    if (!v) { toastMsg("Enter a model ID."); return; }
    selectModel(v);
  });
if (dom.modelsCustomInput)
  dom.modelsCustomInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const v = String(dom.modelsCustomInput.value || "").trim();
      if (v) selectModel(v);
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

/* Pre-fetch all models and populate datalist for autocomplete */
fetchModels().then(() => {
  populateDatalist();
});

function populateDatalist() {
  const dl = document.getElementById("model-presets-list");
  if (!dl) return;
  dl.innerHTML = "";
  const models = state.allModels || [];
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.label = m.name || m.id;
    dl.appendChild(opt);
  }
}

if (dom.sessionKey) dom.sessionKey.textContent = state.sessionKey;
if (dom.modelPill) dom.modelPill.textContent = state.modelDefault || "default";
if (dom.verbositySelect) dom.verbositySelect.value = state.verbosity || "normal";
if (dom.restrictWorkspaceToggle)
  dom.restrictWorkspaceToggle.checked = !!state.restrictWorkspace;

autogrow();
renderHistory([]);
renderAttachments();
updateEmpty();
connect();
setTimeout(() => dom.input && dom.input.focus(), 60);
