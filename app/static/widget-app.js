import {
  clearSession,
  copy,
  makeChannelMessageId,
  mergeMessages,
  messageTone,
  parseSseFrames,
  readWidgetConfig,
  restoreSessionForLaunch,
  saveSession,
  sessionKey,
} from "/static/js/widget-core.js?v=1.3.9";

const config = readWidgetConfig();
const storage = globalThis.sessionStorage;
let requestController = new AbortController();
const state = {
  token: config.token,
  conversationId: null,
  messages: [],
  pendingAssistant: "",
  busy: false,
  handoff: false,
};

const $ = (id) => document.getElementById(id);
const prechatView = $("prechatView");
const chatView = $("chatView");
const fatalState = $("fatalState");
const messageLog = $("messageLog");
const scroller = $("messageScroller");
const messageInput = $("messageInput");
const sendButton = $("sendButton");
const messageForm = $("messageForm");
const connectionBanner = $("connectionBanner");

function setText(id, value) {
  const element = $(id);
  if (element) element.textContent = value;
}

function applyCopy() {
  document.documentElement.lang = config.locale === "en" ? "en" : "zh-CN";
  document.body.dataset.accent = config.accent;
  document.title = config.brand;
  setText("widgetBrand", config.brand);
  setText("headerKicker", copy(config.locale, "eyebrow"));
  setText("onlineLabel", copy(config.locale, "online"));
  setText("welcomeEyebrow", copy(config.locale, "eyebrow"));
  setText("welcomeTitle", config.locale === "en" ? "Start here" : "欢迎来到服务台");
  setText("welcomeCopy", config.greeting);
  setText("nameLabel", copy(config.locale, "nameLabel"));
  $("customerName").placeholder = copy(config.locale, "namePlaceholder");
  setText("startButton", copy(config.locale, "start"));
  setText("introCopy", copy(config.locale, "intro"));
  setText("inputLabel", copy(config.locale, "inputLabel"));
  setText("chatTitle", copy(config.locale, "chatTitle"));
  messageInput.placeholder = copy(config.locale, "inputPlaceholder");
  sendButton.title = copy(config.locale, "send");
  sendButton.setAttribute("aria-label", copy(config.locale, "send"));
  setText("poweredLabel", copy(config.locale, "powered"));
  setText("fatalTitle", copy(config.locale, "fatalTitle"));
  setText("fatalCopy", copy(config.locale, "expired"));
}

function showFatal(message = copy(config.locale, "expired")) {
  prechatView.hidden = true;
  chatView.hidden = true;
  fatalState.hidden = false;
  setText("fatalCopy", message);
}

function expireSession(message = copy(config.locale, "expired")) {
  clearSession(storage, sessionKey());
  state.token = "";
  state.conversationId = null;
  showFatal(message);
}

function showBanner(message = "", visible = Boolean(message)) {
  connectionBanner.hidden = !visible;
  connectionBanner.textContent = message;
}

function setChatVisible() {
  prechatView.hidden = true;
  fatalState.hidden = true;
  chatView.hidden = false;
  messageInput.focus({ preventScroll: true });
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Widget-Token", state.token);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    ...options,
    headers,
    cache: "no-store",
    signal: options.signal || requestController.signal,
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || body.title || detail;
    } catch (_error) {
      // Keep the status when an upstream response is not JSON.
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleTimeString(config.locale === "en" ? "en-US" : "zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function appendMessageElement(message, pending = false) {
  const row = document.createElement("article");
  const role = messageTone(message.role);
  row.className = `message-row ${role}${pending ? " pending" : ""}`;
  if (message.id) row.dataset.messageId = message.id;
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = message.content || "";
  row.appendChild(bubble);
  if (message.created_at && !pending) {
    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = formatTime(message.created_at);
    row.appendChild(meta);
  }
  messageLog.appendChild(row);
}

function appendSystemNote(text) {
  const note = document.createElement("p");
  note.className = "system-note";
  note.textContent = text;
  messageLog.appendChild(note);
}

function renderMessages() {
  messageLog.replaceChildren();
  if (!state.messages.length) appendSystemNote(copy(config.locale, "intro"));
  state.messages.forEach((message) => appendMessageElement(message));
  if (state.pendingAssistant) {
    appendMessageElement({ role: "assistant", content: state.pendingAssistant }, true);
  }
  if (state.handoff) appendSystemNote(copy(config.locale, "handoff"));
  scroller.scrollTop = scroller.scrollHeight;
}

function setBusy(value) {
  state.busy = value;
  messageForm.setAttribute("aria-busy", String(value));
  sendButton.disabled = value;
  messageInput.disabled = value;
  setText("composerState", value ? copy(config.locale, "sending") : "");
}

function saveCurrentSession() {
  saveSession(storage, { conversationId: state.conversationId, token: state.token }, sessionKey());
}

async function loadHistory() {
  const response = await request(`/api/widget/sessions/${encodeURIComponent(state.conversationId)}/messages?limit=200`);
  state.handoff = ["waiting_human", "human_active"].includes(
    response.headers.get("X-Conversation-Status"),
  );
  state.messages = mergeMessages(
    state.messages.filter((message) => !String(message.id || "").startsWith("local-")),
    await response.json(),
  );
  renderMessages();
}

function parseJobData(data) {
  try { return JSON.parse(data); } catch (_error) { return {}; }
}

async function readStream(response) {
  if (!response.body?.getReader) return { completed: false, reconnect: true };
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  let reconnect = false;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const parsed = parseSseFrames(buffer, done);
    buffer = parsed.remainder;
    for (const frame of parsed.events) {
      if (frame.event === "token") {
        const token = parseJobData(frame.data);
        state.pendingAssistant += String(token.content || "");
        renderMessages();
      } else if (frame.event === "job") {
        const job = parseJobData(frame.data);
        completed = job.status === "completed";
        if (job.status === "failed") throw new Error(job.error_message || copy(config.locale, "error"));
        if (job.result_json) {
          const result = parseJobData(job.result_json);
          state.handoff = ["waiting_human", "human_active"].includes(
            result?.conversation?.status,
          );
        }
      } else if (frame.event === "timeout") {
        showBanner(copy(config.locale, "timeout"), true);
        reconnect = true;
      } else if (frame.event === "shutdown") {
        showBanner(copy(config.locale, "reconnecting"), true);
        reconnect = true;
      } else if (frame.event === "error") {
        throw new Error(copy(config.locale, "error"));
      }
    }
    if (done) break;
  }
  return { completed, reconnect: reconnect || !completed };
}

async function streamLatestTurn() {
  let completed = false;
  for (let attempt = 0; attempt < 3 && !completed; attempt += 1) {
    if (attempt > 0) {
      showBanner(copy(config.locale, "reconnecting"), true);
      await new Promise((resolve) => setTimeout(resolve, 400 * attempt));
    }
    state.pendingAssistant = "";
    const response = await request(`/api/widget/sessions/${encodeURIComponent(state.conversationId)}/stream?timeout=20`);
    const result = await readStream(response);
    completed = result.completed;
    if (!result.reconnect) break;
  }
  await loadHistory();
  state.pendingAssistant = "";
  showBanner(completed ? "" : copy(config.locale, "timeout"), !completed);
  renderMessages();
  return completed;
}

async function startSession(event) {
  event.preventDefault();
  if (!state.token) return showFatal();
  const button = $("startButton");
  button.disabled = true;
  try {
    const customerName = $("customerName").value.trim();
    const response = await request("/api/widget/sessions", {
      method: "POST",
      body: JSON.stringify(customerName ? { customer_name: customerName } : {}),
    });
    const session = await response.json();
    state.conversationId = session.conversation.id;
    state.token = session.widget_token;
    saveCurrentSession();
    history.replaceState({}, document.title, `${location.pathname}${location.search}`);
    setChatVisible();
    await loadHistory();
  } catch (error) {
    if (error.name === "AbortError") return;
    if (error.status === 401 || error.status === 404) expireSession();
    else showFatal(copy(config.locale, "error"));
  } finally {
    button.disabled = false;
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const content = messageInput.value.trim();
  if (!content || state.busy || !state.conversationId) return;
  const channelMessageId = makeChannelMessageId();
  state.messages = mergeMessages(state.messages, [{ id: `local-${channelMessageId}`, role: "customer", content, created_at: new Date().toISOString() }]);
  messageInput.value = "";
  state.pendingAssistant = "";
  renderMessages();
  setBusy(true);
  showBanner("", false);
  try {
    const queued = await request(`/api/widget/sessions/${encodeURIComponent(state.conversationId)}/messages?async_mode=true`, {
      method: "POST",
      body: JSON.stringify({ content, channel_message_id: channelMessageId }),
    });
    await queued.json();
    await streamLatestTurn();
  } catch (error) {
    if (error.name === "AbortError") return;
    state.pendingAssistant = "";
    if (error.status === 401 || error.status === 404) {
      expireSession();
      return;
    }
    try { await loadHistory(); } catch (_historyError) { /* Keep the original user-facing error. */ }
    showBanner(copy(config.locale, "error"), true);
  } finally {
    setBusy(false);
    renderMessages();
  }
}

function handleInputKey(event) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    messageForm.requestSubmit();
  }
}

function handleBootstrapNavigation() {
  const next = readWidgetConfig();
  if (!next.token) return;
  requestController.abort();
  requestController = new AbortController();
  clearSession(storage, sessionKey());
  state.token = next.token;
  state.conversationId = null;
  state.messages = [];
  state.pendingAssistant = "";
  state.handoff = false;
  $("customerName").value = "";
  messageInput.value = "";
  setBusy(false);
  showBanner("", false);
  messageLog.replaceChildren();
  chatView.hidden = true;
  fatalState.hidden = true;
  prechatView.hidden = false;
  $("customerName").focus({ preventScroll: true });
}

async function restoreOrPrepare() {
  const restored = restoreSessionForLaunch(storage, config.token, sessionKey());
  if (restored) {
    state.conversationId = restored.conversationId;
    state.token = restored.token;
    history.replaceState({}, document.title, `${location.pathname}${location.search}`);
    setChatVisible();
    setBusy(true);
    showBanner(copy(config.locale, "loading"), true);
    try {
      await loadHistory();
      showBanner("", false);
    } catch (error) {
      if (error.name === "AbortError") return;
      if (error.status === 401 || error.status === 404) expireSession();
      else showBanner(copy(config.locale, "error"), true);
    } finally {
      setBusy(false);
    }
    return;
  }
  if (!state.token) return showFatal();
}

applyCopy();
$("startForm").addEventListener("submit", startSession);
messageForm.addEventListener("submit", sendMessage);
messageInput.addEventListener("keydown", handleInputKey);
window.addEventListener("online", () => showBanner("", false));
window.addEventListener("offline", () => showBanner(copy(config.locale, "reconnecting"), true));
window.addEventListener("hashchange", handleBootstrapNavigation);
restoreOrPrepare();
