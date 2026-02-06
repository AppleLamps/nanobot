/* state.js — DOM references, shared mutable state, constants */

export const $ = (id) => document.getElementById(id);

/* DOM element references */
export const dom = {
  feed: $("feed"),
  rows: $("rows"),
  empty: $("empty"),
  input: $("input"),
  sendBtn: $("send"),
  status: $("status"),
  dot: $("dot"),
  sessionKey: $("sessionkey"),
  latency: $("latency"),
  toast: $("toast"),
  modelPill: $("model-pill"),
  modelInput: $("model"),
  modelData: $("models"),
  applyModelBtn: $("apply-model"),
  jumpWrap: $("jump"),
  jumpBtn: $("jump-btn"),
  fileInput: $("file"),
  attachBtn: $("attach"),
  attachments: $("attachments"),
  sessionsModal: $("sessions-modal"),
  sessionsClose: $("sessions-close"),
  sessionsList: $("sessions-list"),
  clearBtn: $("clear"),
  newChatBtn: $("new-chat"),
  sessionsBtn: $("sessions"),
  copyBtn: $("copy-link"),
};

/* Model presets for datalist */
export const MODEL_PRESETS = [
  "x-ai/grok-4.1-fast",
  "x-ai/grok-code-fast-1",
  "anthropic/claude-haiku-4.5",
  "anthropic/claude-sonnet-4.5",
  "anthropic/claude-opus-4.6",
  "google/gemini-3-flash-preview",
  "google/gemini-3-pro-preview",
  "openai/gpt-5.2",
  "openai/o3",
  "qwen/qwen3-coder-next",
];

/* Shared mutable state — all modules read/write through this object */
export const state = {
  ws: null,
  inflight: false,
  t0: 0,
  thinkingRow: null,
  pendingNewChatDefaultModel: false,
  lastHistoryEmpty: true,
  serverHistory: [],
  attachments: [],
  waiters: [],

  token: "",
  senderId: "",
  chatId: "",
  sessionKey: "",
  currentModel: "",
  modelDefault: "",
};

/* Utilities */
export function randId(prefix) {
  const s =
    Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2);
  return (prefix || "id") + ":" + s.slice(0, 16);
}

/* Initialise identity from URL params / localStorage */
const qs = new URLSearchParams(location.search);
const storage = window.localStorage;

state.token = (qs.get("token") || storage.getItem("nanobot.webui.token") || "").trim();
if (qs.get("token")) storage.setItem("nanobot.webui.token", state.token);

state.senderId = (storage.getItem("nanobot.webui.senderId") || "").trim();
if (!state.senderId) {
  state.senderId = randId("u");
  storage.setItem("nanobot.webui.senderId", state.senderId);
}

state.chatId = (qs.get("chat") || storage.getItem("nanobot.webui.chatId") || "").trim();
if (!state.chatId) {
  state.chatId = randId("c");
}
storage.setItem("nanobot.webui.chatId", state.chatId);

state.sessionKey = (qs.get("session") || storage.getItem("nanobot.webui.sessionKey") || "").trim();
if (!state.sessionKey) state.sessionKey = "webui:" + state.chatId;
storage.setItem("nanobot.webui.sessionKey", state.sessionKey);

state.modelDefault = (qs.get("model") || storage.getItem("nanobot.webui.modelDefault") || "").trim();

/* Persist helpers */
export function persist(key, value) {
  storage.setItem("nanobot.webui." + key, value);
}
