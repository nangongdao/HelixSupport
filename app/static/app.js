const TENANT = document.documentElement.dataset.tenantId || "demo";
const BASE_HEADERS = {
  Accept: "application/json",
  "X-Tenant-Id": TENANT,
};
const QUEUE_PAGE_SIZE_NORMAL = 50;
const QUEUE_PAGE_SIZE_LOW = 20;
// ROADMAP §18.4: the thread view loads the newest tail of a long conversation
// and lazily fetches older messages upward on scroll-to-top, instead of
// pulling the whole transcript up front.
const THREAD_PAGE_LIMIT = 100;
const THREAD_OLDER_PAGE_SIZE = 50;
const POLL_INTERVAL_NORMAL = 30000;
const POLL_INTERVAL_LOW = 90000;
const PREF_DENSITY = "helix-queue-density";
const PREF_LOW_PERF = "helix-low-perf";
const PREF_INSPECTOR = "helix-inspector-collapsed";
// Backlog (多语言客服): ISO 639-1 code -> display name for the operator
// console language badge; falls back to the raw code when unmapped.
const LANGUAGE_NAMES = {
  zh: "中文",
  en: "English",
  ja: "日本語",
  ko: "한국어",
  ru: "Русский",
  ar: "العربية",
  hi: "हिन्दी",
  he: "עברית",
  th: "ไทย",
  el: "Ελληνικά",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  pt: "Português",
};

// Backlog (多语言客服): shared option rows for the language override select
// and every per-message translate bar. Built once so re-renders stay cheap.
const LANGUAGE_OPTIONS = Object.keys(LANGUAGE_NAMES)
  .sort((a, b) => LANGUAGE_NAMES[a].localeCompare(LANGUAGE_NAMES[b], "zh"))
  .map((code) => `<option value="${code}">${LANGUAGE_NAMES[code]}</option>`)
  .join("");

const state = {
  selectedId: null,
  conversations: [],
  detail: null,
  dashboard: null,
  labelCatalog: [],
  me: null,
  activeTab: "overview",
  refreshPromise: null,
  detailSequence: 0,
  queueHasMore: false,
  queueCursor: null,
  queueLoadingMore: false,
  // ROADMAP §18.4: upward lazy-load of the message thread — the opaque cursor
  // echoed from the detail response's X-Prev-Cursor and the in-flight guard.
  threadPrevCursor: null,
  threadLoadingOlder: false,
  bulkSelected: new Set(),
  cannedResponses: [],
  savedViews: [],
  lowPerf: false,
  inspectorCollapsed: false,
  pollTimer: null,
  queueEventSource: null,
  queueRelay: null,
  claimRenewTimer: null,
  draftTimer: null,
  queueReconnectTimer: null,
  inspectorRendered: { overview: false, evidence: false, audit: false },
  macroOpen: false,
  collaborators: [],
  collaboratorsLoadedAt: 0,
  mentionOpen: false,
  mentionToken: "",
  labelsLoadedAt: 0,
  cannedLoadedAt: 0,
  lastCopilotConv: null,
  lastQueueSignature: "",
  queueVirtual: false,
  queueRowHeight: 0,
  queueWindow: null,
  queueWindowSig: "",
  queueScrollRaf: 0,
  queueMeasuring: false,
  qualityBuckets: [],
  qualityGaps: [],
  qualityLoadedAt: 0,
  knowledgeArticles: [],
  knowledgeLoadedAt: 0,
  knowledgeLoadedForWriter: null,
  knowledgeEditingId: null,
  mentions: [],
  mentionsOpen: false,
  watchController: null,
};

const els = {
  metrics: document.getElementById("metrics"),
  list: document.getElementById("conversationList"),
  queueCount: document.getElementById("queueCount"),
  loadMore: document.getElementById("loadMore"),
  queuePane: document.getElementById("queuePane"),
  searchInput: document.getElementById("searchInput"),
  statusFilter: document.getElementById("statusFilter"),
  labelFilter: document.getElementById("labelFilter"),
  priorityFilter: document.getElementById("priorityFilter"),
  ownershipFilter: document.getElementById("ownershipFilter"),
  channelFilter: document.getElementById("channelFilter"),
  sortFilter: document.getElementById("sortFilter"),
  focusWaiting: document.getElementById("focusWaiting"),
  savedViewSelect: document.getElementById("savedViewSelect"),
  saveView: document.getElementById("saveView"),
  deleteView: document.getElementById("deleteView"),
  densityToggle: document.getElementById("densityToggle"),
  lowPerfToggle: document.getElementById("lowPerfToggle"),
  inspectorToggle: document.getElementById("inspectorToggle"),
  claimBtn: document.getElementById("claimBtn"),
  releaseBtn: document.getElementById("releaseBtn"),
  assignBtn: document.getElementById("assignBtn"),
  liveStatus: document.getElementById("liveStatus"),
  macroSuggest: document.getElementById("macroSuggest"),
  copilotBar: document.getElementById("copilotBar"),
  copilotSuggestBtn: document.getElementById("copilotSuggestBtn"),
  copilotTone: document.getElementById("copilotTone"),
  copilotStatus: document.getElementById("copilotStatus"),
  copilotSuggestions: document.getElementById("copilotSuggestions"),
  copilotKnowledge: document.getElementById("copilotKnowledge"),
  attachmentBar: document.getElementById("attachmentBar"),
  pendingAttachments: document.getElementById("pendingAttachments"),
  attachmentFile: document.getElementById("attachmentFile"),
  appNav: document.getElementById("appNav"),
  navItems: Array.from(document.querySelectorAll(".nav-item")),
  workspaceView: document.getElementById("workspaceView"),
  qualityView: document.getElementById("qualityView"),
  qualityViewBuckets: document.getElementById("qualityViewBuckets"),
  qualityViewGaps: document.getElementById("qualityViewGaps"),
  refreshQualityView: document.getElementById("refreshQualityView"),
  knowledgeView: document.getElementById("knowledgeView"),
  knowledgeSummary: document.getElementById("knowledgeSummary"),
  knowledgeSearch: document.getElementById("knowledgeSearch"),
  knowledgeStatusFilter: document.getElementById("knowledgeStatusFilter"),
  knowledgeLanguageFilter: document.getElementById("knowledgeLanguageFilter"),
  knowledgeResultCount: document.getElementById("knowledgeResultCount"),
  knowledgeReadOnly: document.getElementById("knowledgeReadOnly"),
  knowledgeListStatus: document.getElementById("knowledgeListStatus"),
  knowledgeList: document.getElementById("knowledgeList"),
  knowledgeEditor: document.getElementById("knowledgeEditor"),
  knowledgeForm: document.getElementById("knowledgeForm"),
  knowledgeEditorTitle: document.getElementById("knowledgeEditorTitle"),
  knowledgeTitle: document.getElementById("knowledgeTitle"),
  knowledgeContent: document.getElementById("knowledgeContent"),
  knowledgeTags: document.getElementById("knowledgeTags"),
  knowledgeCategory: document.getElementById("knowledgeCategory"),
  knowledgeLanguage: document.getElementById("knowledgeLanguage"),
  knowledgeSource: document.getElementById("knowledgeSource"),
  knowledgeSaveLabel: document.getElementById("knowledgeSaveLabel"),
  newKnowledgeDraft: document.getElementById("newKnowledgeDraft"),
  refreshKnowledge: document.getElementById("refreshKnowledge"),
  cancelKnowledgeEdit: document.getElementById("cancelKnowledgeEdit"),
  resetKnowledgeForm: document.getElementById("resetKnowledgeForm"),
  placeholderView: document.getElementById("placeholderView"),
  desktopVersion: document.getElementById("desktopVersion"),
  desktopBackendPort: document.getElementById("desktopBackendPort"),
  desktopBackendMode: document.getElementById("desktopBackendMode"),
  desktopDataDir: document.getElementById("desktopDataDir"),
  desktopEnvNote: document.getElementById("desktopEnvNote"),
  commandPalette: document.getElementById("commandPalette"),
  commandInput: document.getElementById("commandInput"),
  commandResults: document.getElementById("commandResults"),
  adminView: document.getElementById("adminView"),
  adminDenied: document.getElementById("adminDenied"),
  adminContent: document.getElementById("adminContent"),
  refreshAdmin: document.getElementById("refreshAdmin"),
  quotaReadout: document.getElementById("quotaReadout"),
  quotaForm: document.getElementById("quotaForm"),
  quotaConversations: document.getElementById("quotaConversations"),
  quotaStorageMb: document.getElementById("quotaStorageMb"),
  memberList: document.getElementById("memberList"),
  memberForm: document.getElementById("memberForm"),
  memberActorId: document.getElementById("memberActorId"),
  memberRole: document.getElementById("memberRole"),
  csatReadout: document.getElementById("csatReadout"),
  csatTrend: document.getElementById("csatTrend"),
  webhookList: document.getElementById("webhookList"),
  webhookForm: document.getElementById("webhookForm"),
  webhookUrl: document.getElementById("webhookUrl"),
  webhookEvents: document.getElementById("webhookEvents"),
  webhookSecret: document.getElementById("webhookSecret"),
  reportSubscriptionList: document.getElementById("reportSubscriptionList"),
  reportSubscriptionForm: document.getElementById("reportSubscriptionForm"),
  reportType: document.getElementById("reportType"),
  reportSchedule: document.getElementById("reportSchedule"),
  reportWindowDays: document.getElementById("reportWindowDays"),
  reportWebhook: document.getElementById("reportWebhook"),
  reportGenerateForm: document.getElementById("reportGenerateForm"),
  reportGenerateType: document.getElementById("reportGenerateType"),
  reportGenerateWindow: document.getElementById("reportGenerateWindow"),
  reportPreview: document.getElementById("reportPreview"),
  slaPolicyList: document.getElementById("slaPolicyList"),
  slaPolicyForm: document.getElementById("slaPolicyForm"),
  slaPriority: document.getElementById("slaPriority"),
  slaChannel: document.getElementById("slaChannel"),
  slaFirstResponse: document.getElementById("slaFirstResponse"),
  slaResolve: document.getElementById("slaResolve"),
  routingRuleList: document.getElementById("routingRuleList"),
  routingRuleForm: document.getElementById("routingRuleForm"),
  ruleIntent: document.getElementById("ruleIntent"),
  ruleLabel: document.getElementById("ruleLabel"),
  ruleChannel: document.getElementById("ruleChannel"),
  ruleGroup: document.getElementById("ruleGroup"),
  rulePriority: document.getElementById("rulePriority"),
  cannedBar: document.getElementById("cannedBar"),
  cannedList: document.getElementById("cannedList"),
  bulkToolbar: document.getElementById("bulkToolbar"),
  bulkCount: document.getElementById("bulkCount"),
  bulkAction: document.getElementById("bulkAction"),
  bulkLabelField: document.getElementById("bulkLabelField"),
  bulkLabelInput: document.getElementById("bulkLabelInput"),
  applyBulk: document.getElementById("applyBulk"),
  clearBulk: document.getElementById("clearBulk"),
  operatorIdentity: document.getElementById("operatorIdentity"),
  refreshList: document.getElementById("refreshList"),
  mobileQueue: document.getElementById("mobileQueue"),
  backToQueue: document.getElementById("backToQueue"),
  emptyState: document.getElementById("emptyState"),
  conversationView: document.getElementById("conversationView"),
  conversationTitle: document.getElementById("conversationTitle"),
  conversationStatus: document.getElementById("conversationStatus"),
  conversationSubtitle: document.getElementById("conversationSubtitle"),
  conversationLanguageSelect: document.getElementById("conversationLanguageSelect"),
  customerAvatar: document.getElementById("customerAvatar"),
  threadContext: document.getElementById("threadContext"),
  threadSla: document.getElementById("threadSla"),
  messages: document.getElementById("messages"),
  composerNotice: document.getElementById("composerNotice"),
  customerForm: document.getElementById("customerForm"),
  customerInput: document.getElementById("customerInput"),
  operatorForm: document.getElementById("operatorForm"),
  operatorInput: document.getElementById("operatorInput"),
  acceptBtn: document.getElementById("acceptBtn"),
  resolveBtn: document.getElementById("resolveBtn"),
  reopenBtn: document.getElementById("reopenBtn"),
  ticketBtn: document.getElementById("ticketBtn"),
  ticketBadge: document.getElementById("ticketBadge"),
  wsTabQueue: document.getElementById("wsTabQueue"),
  wsTabTickets: document.getElementById("wsTabTickets"),
  ticketPane: document.getElementById("ticketPane"),
  ticketList: document.getElementById("ticketList"),
  ticketStatusFilter: document.getElementById("ticketStatusFilter"),
  ticketDetailView: document.getElementById("ticketDetailView"),
  ticketBack: document.getElementById("ticketBack"),
  ticketDetailTitle: document.getElementById("ticketDetailTitle"),
  ticketDetailStatus: document.getElementById("ticketDetailStatus"),
  ticketDetailPriority: document.getElementById("ticketDetailPriority"),
  ticketDetailSubtitle: document.getElementById("ticketDetailSubtitle"),
  ticketDetailDescription: document.getElementById("ticketDetailDescription"),
  ticketDetailConvs: document.getElementById("ticketDetailConvs"),
  ticketTransitions: document.getElementById("ticketTransitions"),
  ticketLinkCurrent: document.getElementById("ticketLinkCurrent"),
  csatBanner: document.getElementById("csatBanner"),
  csatUrl: document.getElementById("csatUrl"),
  csatCopyBtn: document.getElementById("csatCopyBtn"),
  summaryBanner: document.getElementById("summaryBanner"),
  summaryTitle: document.getElementById("summaryTitle"),
  summaryText: document.getElementById("summaryText"),
  inspectorSurface: document.querySelector(".inspector-surface"),
  inspectorOverview: document.getElementById("inspectorOverview"),
  inspectorEvidence: document.getElementById("inspectorEvidence"),
  inspectorAudit: document.getElementById("inspectorAudit"),
  qualityPanel: document.getElementById("qualityPanel"),
  qualityBuckets: document.getElementById("qualityBuckets"),
  qualityGaps: document.getElementById("qualityGaps"),
  refreshQuality: document.getElementById("refreshQuality"),
  noteForm: document.getElementById("noteForm"),
  noteInput: document.getElementById("noteInput"),
  mentionSuggest: document.getElementById("mentionSuggest"),
  mentionsBadge: document.getElementById("mentionsBadge"),
  mentionsCount: document.getElementById("mentionsCount"),
  mentionsPanel: document.getElementById("mentionsPanel"),
  watchBanner: document.getElementById("watchBanner"),
  watchText: document.getElementById("watchText"),
  watchToggle: document.getElementById("watchToggle"),
  watchBtn: document.getElementById("watchBtn"),
  newConversation: document.getElementById("newConversation"),
  newConversationDialog: document.getElementById("newConversationDialog"),
  newConversationForm: document.getElementById("newConversationForm"),
  newCustomerName: document.getElementById("newCustomerName"),
  newCustomerRef: document.getElementById("newCustomerRef"),
  newChannel: document.getElementById("newChannel"),
  closeDialog: document.getElementById("closeDialog"),
  cancelDialog: document.getElementById("cancelDialog"),
  toast: document.getElementById("toast"),
};

// ROADMAP §41.6 (ARC-001): the extracted js/ modules read the legacy app.js
// singletons through configure(); bindX() calls wire the DOM they own.
const _helixModules = window.HelixModules || {};
_helixModules.composer?.configure?.({
  state,
  els,
  api,
  loadDetail,
  refreshAll,
  canOperate,
  setFormBusy,
  showToast,
  escapeHtml,
});
_helixModules.session?.configure?.({
  state,
  els,
  api,
  baseHeaders: BASE_HEADERS,
  loadDetail,
  selectConversation,
  showToast,
  escapeHtml,
  formatTime,
  icon,
});
_helixModules.adminReport?.configure?.({ state, els, api, showToast, escapeHtml, formatTime });
_helixModules.ticketView?.configure?.({
  state,
  els,
  api,
  loadDetail,
  selectConversation,
  refreshAll,
  canOperate,
  showToast,
  escapeHtml,
  formatTime,
  statusLabel,
});
_helixModules.qualityPanel?.configure?.({ state, els, api, showToast, escapeHtml });
_helixModules.attachments?.configure?.({ state, els, api, showToast, escapeHtml, icon, canOperate });
_helixModules.queueView?.configure?.({
  state,
  els,
  canOperate,
  escapeHtml,
  statusLabel,
  formatSla,
  // vqueue/density helpers come straight from the module namespaces — the
  // destructured locals are declared further down this file and would be
  // TDZ-dead at configure time.
  estimatedRowHeight: (density, lowPerf) =>
    (_helixModules.vqueue || fallbackVqueueHelpers).estimatedRowHeight(density, lowPerf),
  signatureOf: (c) => (_helixModules.vqueue || fallbackVqueueHelpers).signatureOf(c),
  rowHeightFromElement: (el) =>
    (_helixModules.vqueue || fallbackVqueueHelpers).rowHeightFromElement(el),
  isCompactDensity: (level, lowPerf) =>
    (_helixModules.density || fallbackDensityHelpers).isCompactDensity(level, lowPerf),
  VIRTUAL_THRESHOLD: 200,
});

// Minimal fallbacks mirroring the legacy inline definitions so configure-time
// references stay safe even if the module entry has not loaded yet.
const fallbackVqueueHelpers = {
  estimatedRowHeight: () => 118,
  signatureOf: (c) => `${c.id}:${c.status}:${c.updated_at}:${c.version ?? 0}`,
  rowHeightFromElement: () => 118,
};
const fallbackDensityHelpers = {
  isCompactDensity: (level, lowPerf) => {
    const effective = lowPerf ? "compact" : level;
    return effective === "compact" || effective === "dense";
  },
};
_helixModules.inspector?.configure?.({
  state,
  els,
  api,
  loadDetail,
  refreshAll,
  loadQualityPanel,
  canOperate,
  setFormBusy,
  showToast,
  escapeHtml,
  formatTime,
  latestAssistant,
  renderLabelChips,
});

function queuePageSize() {
  return state.lowPerf ? QUEUE_PAGE_SIZE_LOW : QUEUE_PAGE_SIZE_NORMAL;
}

function pollInterval() {
  return state.lowPerf ? POLL_INTERVAL_LOW : POLL_INTERVAL_NORMAL;
}

function detectConstrainedDevice() {
  const cores = navigator.hardwareConcurrency || 8;
  const memory = navigator.deviceMemory || 8;
  const saveData = navigator.connection?.saveData === true;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return cores <= 4 || memory <= 4 || saveData || reducedMotion;
}

function applyWorkspacePreferences() {
  document.body.classList.toggle("is-low-perf", state.lowPerf);
  document.body.classList.toggle("is-inspector-collapsed", state.inspectorCollapsed);
  if (els.lowPerfToggle) {
    els.lowPerfToggle.setAttribute("aria-pressed", String(state.lowPerf));
    els.lowPerfToggle.title = state.lowPerf ? "关闭低配模式" : "开启低配模式";
    els.lowPerfToggle.setAttribute("aria-label", state.lowPerf ? "关闭低配模式" : "开启低配模式");
  }
  if (els.inspectorToggle) {
    els.inspectorToggle.setAttribute("aria-pressed", String(state.inspectorCollapsed));
    els.inspectorToggle.title = state.inspectorCollapsed ? "展开检查器" : "折叠检查器";
    els.inspectorToggle.setAttribute(
      "aria-label",
      state.inspectorCollapsed ? "展开检查器" : "折叠检查器",
    );
  }
  if (els.inspectorSurface) {
    els.inspectorSurface.hidden = state.inspectorCollapsed;
    els.inspectorSurface.setAttribute("aria-hidden", String(state.inspectorCollapsed));
  }
}

function relayClientId() {
  if (window.crypto?.randomUUID) return `tab-${window.crypto.randomUUID()}`;
  return `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// ROADMAP §18.4: stand for leadership of the shared SSE connection. Only the
// leader opens /api/events/queue; followers refresh through relayed events.
// Without BroadcastChannel (or in low-perf mode) each tab keeps its own SSE.
function initQueueRelay() {
  if (state.lowPerf || !broadcastModule?.createLeaderElector || !window.BroadcastChannel) {
    // No shared connection available — stand down any existing relay and
    // fall back to each tab opening its own stream (legacy behaviour).
    if (state.queueRelay?.isRunning()) {
      state.queueRelay.abandon("no-relay");
    }
    state.queueRelay = null;
    connectQueueEvents();
    return;
  }
  if (state.queueRelay?.isRunning()) return;
  const { PROTOCOL, createLeaderElector } = broadcastModule;
  const channel = new BroadcastChannel(PROTOCOL.channelName);
  const elector = createLeaderElector({
    channel: {
      postMessage: (message) => channel.postMessage(message),
      addListener: (fn) => {
        const handler = (event) => fn(event.data);
        channel.addEventListener("message", handler);
        return () => channel.removeEventListener("message", handler);
      },
    },
    clientId: relayClientId(),
    now: () => Date.now(),
    setTimer: (fn, ms) => window.setTimeout(fn, ms),
    clearTimer: (handle) => window.clearTimeout(handle),
    onBecomeLeader: () => connectQueueEvents(),
    onSteppedDown: () => {
      if (state.queueEventSource?.abort) state.queueEventSource.abort();
      setLiveStatus("poll");
    },
    onEvent: () => {
      if (!document.hidden) {
        void refreshAll({ silent: true, background: true, refreshDetail: false });
      }
    },
  });
  elector.start();
  state.queueRelay = elector;
}

function schedulePolling() {
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(() => {
    if (!document.hidden) {
      refreshAll({ silent: true, background: true, refreshDetail: false });
    }
  }, pollInterval());
  initQueueRelay();
}

function setLiveStatus(mode) {
  if (!els.liveStatus) return;
  els.liveStatus.textContent = mode === "live" ? "LIVE" : mode === "poll" ? "POLL" : "…";
  els.liveStatus.classList.toggle("is-poll", mode === "poll");
  els.liveStatus.classList.toggle("is-connecting", mode === "connecting");
}

function connectQueueEvents() {
  if (state.queueEventSource?.abort) {
    state.queueEventSource.abort();
    state.queueEventSource = null;
  }
  if (state.queueReconnectTimer) {
    window.clearTimeout(state.queueReconnectTimer);
    state.queueReconnectTimer = null;
  }
  if (state.lowPerf || document.hidden) {
    setLiveStatus("poll");
    return;
  }
  setLiveStatus("connecting");
  const controller = new AbortController();
  state.queueEventSource = controller;
  void (async () => {
    try {
      const response = await fetch("/api/events/queue?timeout=45", {
        headers: BASE_HEADERS,
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`queue stream failed (${response.status})`);
      }
      setLiveStatus("live");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          const lines = chunk.split("\n");
          let eventName = "message";
          for (const line of lines) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
          }
          if (eventName === "queue") {
            if (!document.hidden) {
              void refreshAll({ silent: true, background: true, refreshDetail: false });
              // Single shared connection: the leader tells every follower to
              // refresh too (ROADMAP §18.4).
              if (state.queueRelay?.isLeader()) {
                state.queueRelay.relay({ type: "queue" });
              }
            }
          } else if (eventName === "timeout") {
            break;
          }
        }
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      setLiveStatus("poll");
    } finally {
      if (state.queueEventSource === controller) state.queueEventSource = null;
      if (!document.hidden && !state.lowPerf) {
        state.queueReconnectTimer = window.setTimeout(() => connectQueueEvents(), 4000);
      }
    }
  })();
}


// moved to js/composer.js (draftKey)

// moved to js/composer.js (draftsEnabled)

// moved to js/composer.js (draftTtlMs)

function loadDraft(conversationId) {
  return window.HelixModules?.['composer']?.['loadDraft'](...arguments);
}

function saveDraft(conversationId, value) {
  return window.HelixModules?.['composer']?.['saveDraft'](...arguments);
}

// moved to js/composer.js (clearDraft)

function pruneExpiredDrafts() {
  return window.HelixModules?.['composer']?.['pruneExpiredDrafts'](...arguments);
}

function scheduleClaimRenewal(detail) {
  return window.HelixModules?.['composer']?.['scheduleClaimRenewal'](...arguments);
}

function hideMacroSuggest() {
  return window.HelixModules?.['composer']?.['hideMacroSuggest'](...arguments);
}

function renderMacroSuggest(query) {
  return window.HelixModules?.['composer']?.['renderMacroSuggest'](...arguments);
}

function applyMacroFromSuggest(responseId) {
  return window.HelixModules?.['composer']?.['applyMacroFromSuggest'](...arguments);
}

let toastTimer = null;
let searchTimer = null;

// @-mention autocomplete in the note composer: type @<prefix> and pick a
// colleague from the tenant roster (backlog M18 — orbiting the already-wired
// ``/api/mentions`` inbox with the missing input-side UX).
let mentionIndex = -1;
const MENTION_PATTERN = /(?:^|\s)@([A-Za-z0-9._:@/-]*)$/;

async function loadCollaborators() {
  if (!state.me) {
    state.collaborators = [];
    return;
  }
  try {
    const payload = await api("/api/collaborators");
    state.collaborators = Array.isArray(payload) ? payload : [];
    state.collaboratorsLoadedAt = Date.now();
    // roster 迟到达时若候选已打开,重渲染一次补上(修复与输入竞态的窗口)。
    if (state.mentionOpen && els.mentionSuggest) {
      renderMentionSuggest(state.mentionToken);
    }
  } catch (error) {
    // roster is best-effort; the composer still accepts plain @actor text.
    // 不设 collaboratorsLoadedAt → 下个刷新周期会重试(HIGH-2 修复)。
    state.collaborators = [];
  }
}

function hideMentionSuggest() {
  state.mentionOpen = false;
  state.mentionToken = "";
  mentionIndex = -1;
  if (els.mentionSuggest) {
    els.mentionSuggest.hidden = true;
    els.mentionSuggest.innerHTML = "";
  }
}

function renderMentionSuggest(token) {
  if (!els.mentionSuggest || !canOperate()) {
    hideMentionSuggest();
    return;
  }
  const needle = (token || "").toLowerCase();
  const me = state.me ? state.me.actor_id : "";
  const matches = state.collaborators
    .filter(
      (c) =>
        c.actor_id !== me &&
        (!needle || (c.actor_id || "").toLowerCase().includes(needle)),
    )
    .slice(0, 8);
  if (!matches.length) {
    hideMentionSuggest();
    return;
  }
  state.mentionToken = token;
  state.mentionOpen = true;
  els.mentionSuggest.hidden = false;
  mentionIndex = -1;
  els.mentionSuggest.innerHTML = matches
    .map(
      (c, i) =>
        `<button class="macro-option" type="button" role="option" data-mention-actor="${escapeHtml(
          c.actor_id,
        )}" data-mention-index="${i}">` +
        `<strong>${escapeHtml(c.actor_id)}</strong>` +
        `<span>${escapeHtml(ROLE_LABELS[c.role] || c.role)}</span></button>`,
    )
    .join("");
}

function setMentionActive(i) {
  if (!state.mentionOpen || !els.mentionSuggest) return;
  const options = [...els.mentionSuggest.querySelectorAll(".macro-option")];
  if (!options.length) return;
  mentionIndex = (i + options.length) % options.length;
  options.forEach((el, idx) => {
    el.classList.toggle("is-active", idx === mentionIndex);
    if (idx === mentionIndex) el.scrollIntoView({ block: "nearest" });
  });
}

function applyMentionFromSuggest(actorId) {
  const ta = els.noteInput;
  if (!ta) return;
  // apply 时以当前 value + caret 重新推导 @token 段,不信任 input 时捕获的
  // 位置——用户可能已用鼠标移动光标(WARNING-3 修复)。
  const caret = ta.selectionStart ?? ta.value.length;
  const head = ta.value.slice(0, caret);
  const match = head.match(MENTION_PATTERN);
  if (!match) {
    hideMentionSuggest();
    return;
  }
  const start = caret - match[0].length + match[0].lastIndexOf("@");
  if (start < 0) {
    hideMentionSuggest();
    return;
  }
  ta.value = `${ta.value.slice(0, start)}@${actorId} ${ta.value.slice(caret)}`;
  const pos = start + actorId.length + 2;
  ta.setSelectionRange(pos, pos);
  hideMentionSuggest();
  ta.focus();
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15000);
  // FormData(multipart 上传)必须由浏览器自动生成 Content-Type 边界;任何
  // JSON 之外的 body(FormData/blob)都不该被覆写为 application/json。
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  const requestHeaders = {
    ...BASE_HEADERS,
    ...(!isFormData && options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };
  try {
    const response = await fetch(path, {
      ...options,
      headers: requestHeaders,
      signal: controller.signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `请求失败（${response.status}）`);
    }
    const data = response.status === 204 ? null : await response.json();
    return { response, data };
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请稍后重试");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function api(path, options = {}) {
  const result = await request(path, options);
  return result.data;
}

async function apiWithHeaders(path, options = {}) {
  return request(path, options);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function icon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#${name}"></use></svg>`;
}

function statusLabel(status) {
  // Phase 26.3: prefer the i18n pack when the module layer has loaded.
  const i18n = window.HelixModules?.i18n;
  if (i18n) {
    const translated = i18n.t(`status.${status}`);
    return translated || status || "未知";
  }
  return (
    {
      open: "自动处理中",
      waiting_human: "等待人工",
      human_active: "人工处理中",
      resolved: "已解决",
    }[status] || status || "未知"
  );
}

function roleLabel(role) {
  const i18n = window.HelixModules?.i18n;
  if (i18n) return i18n.t(`role.${role}`);
  return (
    {
      admin: "管理员",
      supervisor: "主管",
      operator: "客服",
      channel: "渠道",
      viewer: "只读",
      auditor: "审计员",
    }[role] || role
  );
}

function formatTime(value, includeDate = false) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    ...(includeDate ? { month: "2-digit", day: "2-digit" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function formatSla(conversation) {
  if (conversation.status === "resolved") return { text: "已完成", breached: false };
  if (!conversation.sla_due_at) return { text: "SLA -", breached: false };
  const milliseconds = new Date(conversation.sla_due_at).getTime() - Date.now();
  const minutes = Math.ceil(Math.abs(milliseconds) / 60000);
  if (milliseconds < 0 || conversation.sla_breached) {
    return { text: `超时 ${minutes} 分钟`, breached: true };
  }
  if (minutes < 60) return { text: `剩余 ${minutes} 分钟`, breached: false };
  return { text: `剩余 ${Math.ceil(minutes / 60)} 小时`, breached: false };
}

function showToast(message, isError = false) {
  window.clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.classList.toggle("is-error", isError);
  els.toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    els.toast.hidden = true;
  }, 3600);
}

// ROADMAP §18.4 渲染预算: run non-critical background work when the browser
// is idle so operator interactions stay under budget; a short timeout is the
// fallback where requestIdleCallback is unavailable. Deferred work must be
// self-contained (the wrapped functions already guard their own state).
function scheduleIdle(fn, timeoutMs = 2000) {
  const run = () => {
    try {
      fn();
    } catch (error) {
      console.error("idle task failed", error);
    }
  };
  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(run, { timeout: timeoutMs });
  } else {
    window.setTimeout(run, 250);
  }
}

function setFormBusy(form, busy) {
  form.dataset.busy = String(busy);
  form.setAttribute("aria-busy", String(busy));
  form.querySelectorAll("button, input, textarea, select").forEach((control) => {
    control.disabled = busy;
  });
}

function newIdempotencyKey() {
  if (window.crypto?.randomUUID) return `ui-${window.crypto.randomUUID()}`;
  return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function renderMetrics(data) {
  const items = [
    ["自动", data?.open ?? 0, false],
    ["待人工", data?.waiting_human ?? 0, (data?.waiting_human ?? 0) > 0],
    ["认领中", data?.claimed_active ?? 0, false],
    ["SLA 超时", data?.sla_breached ?? 0, (data?.sla_breached ?? 0) > 0],
  ];
  els.metrics.innerHTML = items
    .map(
      ([label, value, alert]) =>
        `<div class="metric${alert ? " is-alert" : ""}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`,
    )
    .join("");
  const responseMetric = els.metrics.children[1];
  if (responseMetric) {
    responseMetric.querySelector("span").textContent = "待响应";
    responseMetric.querySelector("strong").textContent = String(data?.needs_response ?? 0);
    responseMetric.classList.toggle("is-alert", (data?.needs_response ?? 0) > 0);
  }
}

function canOperate() {
  return state.me?.permissions?.includes("operator:act") === true;
}

// Backlog (多语言客服): language override + translate both require
// conversation:write (the console's write gate, distinct from canOperate's
// operator:act so channel/operator roles can still act).
function canWriteConversations() {
  return state.me?.permissions?.includes("conversation:write") === true;
}

function currentViewFilters() {
  return {
    ...(els.searchInput.value.trim() ? { search: els.searchInput.value.trim() } : {}),
    ...(els.statusFilter.value ? { status: els.statusFilter.value } : {}),
    ...(els.labelFilter.value ? { label: els.labelFilter.value } : {}),
    ...(els.priorityFilter.value ? { priority: els.priorityFilter.value } : {}),
    ...(els.channelFilter?.value ? { channel: els.channelFilter.value } : {}),
    ...(els.sortFilter?.value && els.sortFilter.value !== "priority"
      ? { sort: els.sortFilter.value }
      : {}),
    ...(els.ownershipFilter.value ? { ownership: els.ownershipFilter.value } : {}),
  };
}

function renderSavedViews() {
  const selected = els.savedViewSelect.value;
  els.savedViewSelect.innerHTML = `<option value="">保存的视图</option>${state.savedViews
    .map((view) => `<option value="${escapeHtml(view.id)}">${escapeHtml(view.name)}</option>`)
    .join("")}`;
  els.savedViewSelect.value = state.savedViews.some((view) => view.id === selected) ? selected : "";
  els.deleteView.disabled = !els.savedViewSelect.value;
}

async function loadSavedViews() {
  try {
    state.savedViews = await api("/api/saved-views");
  } catch {
    state.savedViews = [];
  }
  renderSavedViews();
}

function applySavedView(view) {
  const filters = view?.filters || {};
  els.searchInput.value = filters.search || "";
  els.statusFilter.value = filters.status || "";
  els.labelFilter.value = filters.label || "";
  els.priorityFilter.value = filters.priority || "";
  if (els.channelFilter) els.channelFilter.value = filters.channel || "";
  if (els.sortFilter) els.sortFilter.value = filters.sort || "priority";
  els.ownershipFilter.value = filters.ownership || "";
  els.focusWaiting.setAttribute("aria-pressed", String(filters.ownership === "needs_response"));
  refreshAll();
}

function renderLabelFilter() {
  const selected = els.labelFilter.value;
  const options = state.labelCatalog.map(
    (item) => `<option value="${escapeHtml(item.label)}">${escapeHtml(item.label)} · ${escapeHtml(item.conversation_count)}</option>`,
  );
  if (selected && !state.labelCatalog.some((item) => item.label === selected)) {
    options.unshift(`<option value="${escapeHtml(selected)}">${escapeHtml(selected)}</option>`);
  }
  els.labelFilter.innerHTML = `<option value="">全部标签</option>${options.join("")}`;
  els.labelFilter.value = selected;
}

function renderBulkToolbar() {
  return window.HelixModules?.['queueView']?.['renderBulkToolbar'](...arguments);
}

// moved to js/queue-view.js (renderLabelChips)
function renderLabelChips(labels, emptyText) {
  return window.HelixModules?.['queueView']?.['renderLabelChips'](...arguments);
}

// ROADMAP §18.4: build one queue row (shared by full and windowed modes).
function queueRowHtml(conversation, opts) {
  return window.HelixModules?.['queueView']?.['queueRowHtml'](...arguments);
}

// moved to js/queue-view.js (renderFullQueue)

function windowedRowHeight() {
  return window.HelixModules?.['queueView']?.['windowedRowHeight']();
}

// moved to js/queue-view.js (currentQueueWindow)

function renderWindowedQueue(win, rowHeight, opts) {
  return window.HelixModules?.['queueView']?.['renderWindowedQueue'](...arguments);
}

function renderQueue() {
  return window.HelixModules?.['queueView']?.['renderQueue'](...arguments);
}

function renderLoadingQueue() {
  return window.HelixModules?.['queueView']?.['renderLoadingQueue'](...arguments);
}

// moved to js/queue-view.js (scheduleQueueWindowUpdate)

function handleQueueScroll() {
  return window.HelixModules?.['queueView']?.['handleQueueScroll'](...arguments);
}

function renderMessages(messages, { preserveAnchor = false } = {}) {
  const oldScrollHeight = preserveAnchor ? els.messages.scrollHeight : 0;
  if (!messages.length) {
    els.messages.innerHTML = '<div class="thread-empty">等待第一条客户消息</div>';
    return;
  }
  const roleNames = {
    customer: "客户",
    assistant: "自动客服",
    operator: "人工客服",
    internal_note: "内部备注",
  };
  const visibleMessages = state.lowPerf && messages.length > 80 ? messages.slice(-80) : messages;
  const noteIds = new Set(visibleMessages.filter((m) => m.role === "internal_note").map((m) => m.id));
  const parts = new Array(visibleMessages.length);
  for (let index = 0; index < visibleMessages.length; index += 1) {
    const message = visibleMessages[index];
    const metadata = message.metadata || {};
    const agent = message.role === "assistant" && metadata.agent
      ? `<span class="agent-chip">${escapeHtml(metadata.agent)}</span>`
      : "";
    const feedback = message.role === "assistant"
      ? `<div class="feedback-actions" aria-label="回答反馈">
            <button class="feedback-button" type="button" data-feedback="1" data-message-id="${escapeHtml(message.id)}" title="有帮助" aria-label="有帮助" aria-pressed="false">${icon("thumbs-up")}</button>
            <button class="feedback-button" type="button" data-feedback="-1" data-message-id="${escapeHtml(message.id)}" title="需改进" aria-label="需改进" aria-pressed="false">${icon("thumbs-down")}</button>
          </div>`
      : "";
    // Backlog (多语言客服): customer messages carry a per-message translate
    // bar. Without a configured provider the backend echoes the original text
    // with was_translated=false and the result line degrades to a notice.
    const translateBar = message.role === "customer" && canWriteConversations()
      ? `<div class="translate-bar" data-message-id="${escapeHtml(message.id)}">
            <select class="translate-lang" aria-label="翻译目标语言">${LANGUAGE_OPTIONS}</select>
            <button class="translate-button" type="button">翻译</button>
            <span class="translate-result"></span>
          </div>`
      : "";
    const isReply = message.role === "internal_note" && message.reply_to && noteIds.has(message.reply_to);
    const replyMark = isReply
      ? `<span class="note-reply-mark" title="回复了 ${escapeHtml(message.reply_to)}">↳ 回复</span>`
      : "";
    const contentHtml = escapeHtml(message.content).replace(
      /@([A-Za-z0-9._:@/-]{2,64})/g,
      '<span class="mention-chip">@$1</span>',
    );
    const attachments = metadata.attachment_ids?.length
      ? `<div class="message-attachments">${attachmentChips(metadata.attachment_ids)}</div>`
      : "";
    parts[index] = `
        <div class="message-row ${escapeHtml(message.role)}${isReply ? " is-reply" : ""}">
          <div class="message-card">
            <div class="message-meta">
              <span>${escapeHtml(roleNames[message.role] || message.role)}</span>
              ${agent}
              ${replyMark}
              <time datetime="${escapeHtml(message.created_at)}">${escapeHtml(formatTime(message.created_at))}</time>
            </div>
            <div class="message-bubble">${contentHtml}</div>
            ${attachments}
            ${feedback}
            ${translateBar}
          </div>
        </div>`;
  }
  const truncation = visibleMessages.length < messages.length
    ? `<div class="thread-empty">低配模式仅显示最近 ${visibleMessages.length} 条消息</div>`
    : "";
  // ROADMAP §18.4: a thread with a live X-Prev-Cursor header can grow upward —
  // the affordance doubles as the scroll-to-top trigger anchor.
  const loadOlder = state.threadPrevCursor
    ? '<div class="thread-load-older"><button class="thread-load-older-btn" type="button">加载更早消息 ↑</button></div>'
    : "";
  els.messages.innerHTML = loadOlder + truncation + parts.join("");
  els.messages.querySelector(".thread-load-older-btn")?.addEventListener("click", loadOlderMessages);
  // Preserving the anchor matters for the upward merge: keep the view where it
  // was instead of snapping to the newest message after prepending older ones.
  els.messages.scrollTop = preserveAnchor
    ? els.messages.scrollHeight - oldScrollHeight
    : els.messages.scrollHeight;
}

async function submitFeedback(button) {
  if (!state.selectedId) return;
  button.disabled = true;
  try {
    await api(`/api/conversations/${encodeURIComponent(state.selectedId)}/feedback`, {
      method: "POST",
      body: JSON.stringify({
        message_id: button.dataset.messageId,
        rating: Number(button.dataset.feedback),
      }),
    });
    button.classList.add("is-recorded");
    button.setAttribute("aria-pressed", "true");
    button.title = "已记录";
    showToast("反馈已记录");
  } catch (error) {
    button.disabled = false;
    showToast(error.message, true);
  }
}

// Backlog (多语言客服): POST a per-message translation and inline the result.
// was_translated=false (no provider / target == service language) degrades to
// a notice instead of a misleading "translation".
async function translateMessage(button) {
  if (!state.selectedId) return;
  const bar = button.closest(".translate-bar");
  if (!bar) return;
  const language = bar.querySelector(".translate-lang").value;
  const result = bar.querySelector(".translate-result");
  button.disabled = true;
  result.textContent = "";
  try {
    const out = await api(
      `/api/conversations/${encodeURIComponent(state.selectedId)}/messages/${encodeURIComponent(bar.dataset.messageId)}/translate`,
      {
        method: "POST",
        body: JSON.stringify({ target_language: language }),
      },
    );
    if (out.was_translated) {
      result.innerHTML =
        `<span class="translate-text">${escapeHtml(out.translated)}</span>` +
        `<span class="translate-tag">已翻译为 ${escapeHtml(LANGUAGE_NAMES[language] || language)}</span>`;
    } else if (out.source === "none") {
      // Backlog (多语言客服): target == service language — nothing to translate,
      // don't blame a missing model.
      result.textContent = "目标语言与当前服务语言一致，无需翻译";
    } else {
      result.textContent = "当前无翻译模型，已返回原文";
    }
  } catch (error) {
    result.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function latestAssistant(messages) {
  return [...messages].reverse().find((message) => message.role === "assistant") || null;
}

// moved to js/inspector.js (renderOverview/updateLabels/updatePriority/renderEvidence/renderAudit/renderInspector/switchInspectorTab)

async function updateLabels(event) {
  return window.HelixModules?.['inspector']?.['updateLabels'](...arguments);
}

async function updatePriority(button) {
  return window.HelixModules?.['inspector']?.['updatePriority'](...arguments);
}

function safeCitationUrl(value) {
  return window.HelixModules?.inspector?.safeCitationUrl
    ? window.HelixModules.inspector.safeCitationUrl(value)
    : "#";
}

function renderOverview(detail) {
  return window.HelixModules?.['inspector']?.['renderOverview'](...arguments);
}

function renderEvidence(detail) {
  return window.HelixModules?.['inspector']?.['renderEvidence'](...arguments);
}

function renderAudit(detail) {
  return window.HelixModules?.['inspector']?.['renderAudit'](...arguments);
}

function resetInspectorRenderFlags() {
  return window.HelixModules?.['inspector']?.['resetInspectorRenderFlags'](...arguments);
}

function ensureInspectorTab(tab, detail = state.detail) {
  return window.HelixModules?.['inspector']?.['ensureInspectorTab'](...arguments);
}

function renderInspector(detail) {
  return window.HelixModules?.['inspector']?.['renderInspector'](...arguments);
}

function switchInspectorTab(tab) {
  return window.HelixModules?.['inspector']?.['switchInspectorTab'](...arguments);
}

async function loadQualityPanel() {
  return window.HelixModules?.['qualityPanel']?.['loadQualityPanel'](...arguments);
}

// moved to js/qualityPanel.js (renderQualityViewIfVisible)

// ROADMAP §17.3: build the native-SVG dashboard charts from the pure
// quality-charts module; empty string when the module is not loaded yet or
// there is not enough data to draw either chart.
// moved to js/qualityPanel.js (renderQualityCharts)

function renderQualityPanel(targetBuckets = els.qualityBuckets, targetGaps = els.qualityGaps) {
  return window.HelixModules?.['qualityPanel']?.['renderQualityPanel'](...arguments);
}

// moved to js/qualityPanel.js (createKnowledgeDraftFromFeedback)

function renderSubtitle(conversation) {
  const languageName = conversation.language
    ? LANGUAGE_NAMES[conversation.language] || conversation.language
    : "";
  const subtitleParts = [
    conversation.id,
    conversation.channel,
    conversation.customer_ref || "未绑定身份",
  ];
  if (languageName) subtitleParts.push(`语言:${languageName}`);
  els.conversationSubtitle.textContent = subtitleParts.join(" · ");
}

// Backlog (多语言客服): the header select mirrors the stored manual override
// (empty = auto). Options are injected once; renderDetail sets the value so a
// PATCH error can roll back by re-rendering.
function renderLanguagePicker(conversation) {
  if (!els.conversationLanguageSelect) return;
  // viewer/auditor have no conversation:write — hide the override control so
  // read-only roles don't get a control that would always 403.
  els.conversationLanguageSelect.hidden = !canWriteConversations();
  if (!els.conversationLanguageSelect.dataset.built) {
    els.conversationLanguageSelect.insertAdjacentHTML("beforeend", LANGUAGE_OPTIONS);
    els.conversationLanguageSelect.dataset.built = "1";
  }
  els.conversationLanguageSelect.value = conversation.language || "";
}

function renderDetail(detail) {
  state.detail = detail;
  const conversation = detail.conversation;
  const sla = formatSla(conversation);
  els.emptyState.hidden = true;
  els.conversationView.hidden = false;
  els.conversationTitle.textContent = conversation.customer_name;
  els.conversationStatus.textContent = statusLabel(conversation.status);
  els.conversationStatus.className = `status-pill ${conversation.status}`;
  renderSubtitle(conversation);
  renderLanguagePicker(conversation);
  els.customerAvatar.textContent = [...conversation.customer_name][0]?.toUpperCase() || "?";
  els.threadContext.textContent = `${conversation.intent || "待识别"} · ${conversation.assigned_agent || "未分配"}`;
  els.threadSla.textContent = sla.text;
  els.threadSla.classList.toggle("is-breached", sla.breached);

  const resolved = conversation.status === "resolved";
  const human = ["waiting_human", "human_active"].includes(conversation.status);
  const claimedByMe = conversation.claim_active && conversation.claimed_by === state.me?.actor_id;
  els.claimBtn.hidden = resolved || claimedByMe || !canOperate();
  els.releaseBtn.hidden = !claimedByMe || !canOperate();
  if (els.assignBtn) els.assignBtn.hidden = resolved || !canOperate();
  els.acceptBtn.hidden = resolved || conversation.status === "human_active";
  els.resolveBtn.hidden = resolved;
  els.reopenBtn.hidden = !resolved;
  // Backlog (工单化): convert button hides once the conversation belongs to
  // a ticket; the badge shows the ticket id and, once fetched, its status.
  if (els.ticketBtn) els.ticketBtn.hidden = !canOperate() || Boolean(conversation.ticket_id);
  if (els.ticketBadge) {
    if (conversation.ticket_id) {
      els.ticketBadge.textContent = `工单 ${conversation.ticket_id}`;
      els.ticketBadge.hidden = false;
      scheduleIdle(() => enrichTicketBadge(conversation.ticket_id));
    } else {
      els.ticketBadge.hidden = true;
    }
  }
  if (els.watchBtn) els.watchBtn.hidden = resolved || !canReadConversations();
  if (resolved || !canReadConversations()) stopWatching();
  els.operatorForm.hidden = !human;
  els.composerNotice.hidden = !human;
  els.cannedBar.hidden = !human || !canOperate();
  els.noteForm.hidden = resolved;
  renderCannedResponses();
  if (human && canOperate()) {
    const draft = loadDraft(conversation.id);
    if (!els.operatorInput.value || els.operatorInput.dataset.conversationId !== conversation.id) {
      els.operatorInput.value = draft;
    }
    els.operatorInput.dataset.conversationId = conversation.id;
  }
  const customerBusy = els.customerForm.dataset.busy === "true";
  els.customerInput.disabled = resolved || customerBusy;
  els.customerForm.querySelector("button").disabled = resolved || customerBusy;
  els.customerInput.placeholder = resolved ? "会话已解决，请先重开" : "输入一条模拟客户消息…";
  renderMessages(detail.messages);
  renderInspector(detail);
  renderSummaries(detail);
  renderCopilot(detail);
  renderAttachmentBar(detail);
  if (conversation.id) scheduleIdle(() => loadAttachmentNames(conversation.id));
  scheduleClaimRenewal(detail);
}

function renderSummaries(detail) {
  const summaries = detail.summaries || [];
  if (!summaries.length) {
    els.summaryBanner.hidden = true;
    return;
  }
  const context = summaries.find((entry) => entry.kind === "context");
  const disposition = summaries.find((entry) => entry.kind === "disposition");
  const entry = context || disposition;
  if (!entry) {
    els.summaryBanner.hidden = true;
    return;
  }
  els.summaryTitle.textContent = entry.kind === "context" ? "前情摘要（接入参考）" : "处置记录草稿";
  const badge = entry.source === "model" ? "（AI 生成草稿）" : "（自动投影）";
  els.summaryText.textContent = `${entry.content}${badge}`;
  els.summaryBanner.hidden = false;
}

function clearSelection() {
  state.detailSequence += 1;
  state.selectedId = null;
  state.detail = null;
  hideMentionSuggest();
  els.emptyState.hidden = false;
  els.conversationView.hidden = true;
  els.noteForm.hidden = true;
  renderQueue();
}

async function loadDetail(id) {
  const sequence = ++state.detailSequence;
  // ROADMAP §18.4: always load the newest tail of the transcript, then lazily
  // fetch older messages upward. The detail response echoes an opaque
  // X-Prev-Cursor header (docs/API_POLICY.md §3 — clients never parse cursors).
  const limit = state.lowPerf ? 80 : THREAD_PAGE_LIMIT;
  const { response, data: detail } = await apiWithHeaders(
    `/api/conversations/${encodeURIComponent(id)}?message_limit=${limit}&messages_before=true`,
  );
  if (state.selectedId !== id || sequence !== state.detailSequence) return false;
  state.threadPrevCursor = response.headers.get("X-Prev-Cursor") || null;
  state.threadLoadingOlder = false;
  renderDetail(detail);
  return true;
}

// ROADMAP §18.4: fetch the page of messages before the loaded tail and prepend
// them, keeping the view anchored at the message the operator was reading.
// The base64 opaque cursor is echoed straight back from the detail response's
// X-Prev-Cursor — never parsed or synthesized client-side.
async function loadOlderMessages() {
  if (!state.selectedId || state.threadLoadingOlder || !state.threadPrevCursor) return;
  state.threadLoadingOlder = true;
  const cursor = state.threadPrevCursor;
  const oldScrollHeight = els.messages.scrollHeight;
  try {
    const { response, data } = await apiWithHeaders(
      `/api/conversations/${encodeURIComponent(state.selectedId)}/messages?limit=${THREAD_OLDER_PAGE_SIZE}&before=true&cursor=${encodeURIComponent(cursor)}`,
    );
    const older = Array.isArray(data) ? data : [];
    const merged = [...older, ...(state.detail?.messages || [])];
    // X-Has-More is the authoritative "older messages exist" signal: a partial
    // page (fewer than requested) means we have reached the top.
    const hasMore = response.headers.get("X-Has-More") === "true";
    if (older.length) {
      state.threadPrevCursor = hasMore ? response.headers.get("X-Prev-Cursor") || null : null;
      state.detail = { ...(state.detail || {}), messages: merged };
    } else {
      state.threadPrevCursor = null; // nothing older — drop the affordance
    }
    renderMessages(merged, { preserveAnchor: true });
  } catch (error) {
    showToast(error.message || "加载更早消息失败");
  } finally {
    state.threadLoadingOlder = false;
  }
}

// ------------------------------------------------------------- 坐席协作
// Mentions inbox, internal discussion threads, and the supervisor live view.

function canReadConversations() {
  return Boolean(state.me && state.me.permissions && state.me.permissions.includes("conversation:read"));
}

async function loadMentions() {
  return window.HelixModules?.['session']?.['loadMentions'](...arguments);
}

// moved to js/session.js (renderMentionsPanel)

// moved to js/session.js (toggleMentionsPanel)

// moved to js/session.js (closeMentionsPanel)

// moved to js/session.js (markMentionRead)

// moved to js/session.js (startWatching)

function stopWatching() {
  return window.HelixModules?.['session']?.['stopWatching'](...arguments);
}

// moved to js/session.js (isWatching)

window.HelixModules?.session?.bindSession?.();

// moved to js/session.js (toggleWatching)

async function selectConversation(id) {
  state.selectedId = id;
  // 切换会话时收起 note 的候选列表(旧会话的 @token 不再适用)。
  hideMentionSuggest();
  // Nudge first so a virtual-mode re-render centers on the just-selected row.
  ensureSelectedRowVisible(id);
  renderQueue();
  els.emptyState.hidden = true;
  els.conversationView.hidden = false;
  els.messages.innerHTML = '<div class="thread-empty">正在加载会话</div>';
  stopWatching();
  try {
    await loadDetail(id);
    if (els.queuePane.classList.contains("is-open")) closeQueueDrawer({ restoreFocus: false });
  } catch (error) {
    showToast(error.message, true);
    clearSelection();
  }
}

// ROADMAP §18.4: in virtual mode the selected row may sit outside the rendered
// window — nudge the scrollport so it lands inside the viewport band.
function ensureSelectedRowVisible(id) {
  if (!state.queueVirtual) return;
  const index = state.conversations.findIndex((c) => c.id === id);
  if (index < 0) return;
  const rowHeight = windowedRowHeight();
  const top = index * rowHeight;
  const bottom = top + rowHeight;
  if (top < els.list.scrollTop) els.list.scrollTop = top;
  else if (bottom > els.list.scrollTop + els.list.clientHeight) {
    els.list.scrollTop = bottom - els.list.clientHeight;
  }
}

function clearSelection() {
  stopWatching();
  state.detailSequence += 1;
  state.selectedId = null;
  state.detail = null;
  els.emptyState.hidden = false;
  els.conversationView.hidden = true;
  els.noteForm.hidden = true;
  resetCopilot();
  renderQueue();
}

function conversationQuery() {
  const params = new URLSearchParams();
  const search = els.searchInput.value.trim();
  if (search) params.set("search", search);
  if (els.statusFilter.value) params.set("status", els.statusFilter.value);
  if (els.labelFilter.value) params.set("label", els.labelFilter.value);
  if (els.priorityFilter.value) params.set("priority", els.priorityFilter.value);
  if (els.channelFilter?.value) params.set("channel", els.channelFilter.value);
  if (els.sortFilter?.value) params.set("sort", els.sortFilter.value);
  const ownership = els.ownershipFilter.value;
  if (ownership === "mine") params.set("mine", "true");
  if (ownership === "unassigned") params.set("unassigned", "true");
  if (ownership === "unclaimed") params.set("unclaimed", "true");
  if (ownership === "claimed_by_me" && state.me?.actor_id) {
    params.set("claimed_by", state.me.actor_id);
  }
  if (ownership === "sla_breached") params.set("sla_breached", "true");
  if (ownership === "needs_response") params.set("needs_response", "true");
  params.set("limit", String(queuePageSize()));
  return params.toString();
}

function renderCannedResponses() {
  if (!els.cannedList) return;
  if (!state.cannedResponses.length) {
    els.cannedList.innerHTML = '<span class="canned-empty">暂无快捷回复</span>';
    return;
  }
  els.cannedList.innerHTML = state.cannedResponses
    .slice(0, 8)
    .map(
      (item) =>
        `<button class="canned-chip" type="button" data-macro-id="${escapeHtml(item.id)}" title="${escapeHtml(item.body)}">${escapeHtml(item.title)}${item.shortcut ? ` /${escapeHtml(item.shortcut)}` : ""}</button>`,
    )
    .join("");
}

async function insertCannedResponse(responseId) {
  const macro = state.cannedResponses.find((item) => item.id === responseId);
  if (!macro) return;
  const prefix = els.operatorInput.value.trim();
  els.operatorInput.value = prefix ? `${prefix}\n${macro.body}` : macro.body;
  els.operatorInput.focus();
  try {
    await api(`/api/canned-responses/${encodeURIComponent(responseId)}/use`, { method: "POST" });
  } catch {
    // usage tracking is best-effort
  }
}

async function loadCannedResponses({ force = false } = {}) {
  return window.HelixModules?.['composer']?.['loadCannedResponses'](...arguments);
}



// moved to js/composer.js (setCopilotStatus)

// moved to js/composer.js (fetchCopilotSuggestions)

// moved to js/composer.js (loadCopilotKnowledge)

// moved to js/composer.js (applyCopilotTone)

function resetCopilot() {
  return window.HelixModules?.['composer']?.['resetCopilot'](...arguments);
}


async function enrichTicketBadge(ticketId) {
  return window.HelixModules?.['ticketView']?.['enrichTicketBadge'](...arguments);
}

// moved to js/ticketView.js (convertToTicket)



// moved to js/ticketView.js (switchWorkspaceTab)

/** Hide the ticket detail, restoring the conversation/empty mount point. */
// moved to js/ticketView.js (closeTicketDetail)

// moved to js/ticketView.js (loadTickets)

// moved to js/ticketView.js (refreshTicketsList)

// moved to js/ticketView.js (renderTicketList)

// moved to js/ticketView.js (openTicketDetail)

// moved to js/ticketView.js (renderTicketDetail)

// moved to js/ticketView.js (renderTicketTransitions)

// moved to js/ticketView.js (renderTicketConvs)

// moved to js/ticketView.js (transitionActiveTicket)

// moved to js/ticketView.js (linkActiveTicketConversation)

// moved to js/ticketView.js (jumpToTicketConversation)


function attachmentChips(ids) {
  return window.HelixModules?.['attachments']?.['attachmentChips'](...arguments);
}

// moved to js/attachments.js (patchAttachmentChips)

async function loadAttachmentNames(conversationId) {
  return window.HelixModules?.['attachments']?.['loadAttachmentNames'](...arguments);
}

// moved to js/attachments.js (uploadPendingAttachment)

// moved to js/attachments.js (renderPendingAttachments)

// moved to js/attachments.js (clearPendingAttachments)

function renderAttachmentBar(detail) {
  return window.HelixModules?.['attachments']?.['renderAttachmentBar'](...arguments);
}

// ---- ROADMAP §17: 知识运营页 ---------------------------------------------

// The module layer (main.js) is the single source of truth; this fallback
// mirrors js/knowledge.js exactly so a module-load failure never silently
// changes filter/summary/review behaviour (audit: review backlog 2).
const knowledgeModule = window.HelixModules?.knowledge || {
  KNOWLEDGE_STATUS_LABELS: {
    published: "已发布",
    draft: "草稿",
    pending_review: "待审核",
    retired: "已停用",
  },
  normalizeKnowledgeArticle(article = {}) {
    const status = article.status || (article.active === false ? "retired" : "published");
    const tags = Array.isArray(article.tags)
      ? article.tags
      : String(article.tags || "")
          .split(/[\s,，]+/)
          .filter(Boolean);
    return {
      ...article,
      title: String(article.title || ""),
      content: String(article.content || ""),
      category: String(article.category || "general"),
      source_url: String(article.source_url || ""),
      language: article.language || null,
      status,
      tags: [...new Set(tags.map((tag) => String(tag).trim()).filter(Boolean))],
    };
  },
  parseKnowledgeTags(value) {
    return [
      ...new Set(
        String(value || "")
          .split(/[\s,，]+/)
          .map((tag) => tag.trim())
          .filter(Boolean),
      ),
    ];
  },
  matchesKnowledgeArticle(article, filters = {}) {
    const normalized = knowledgeModule.normalizeKnowledgeArticle(article);
    const status = ["all", "published", "draft", "pending_review", "retired"].includes(
      filters.status,
    )
      ? filters.status
      : "all";
    const language = String(filters.language || "").trim();
    if (status !== "all" && normalized.status !== status) return false;
    if (language && normalized.language && normalized.language !== language) return false;
    const query = String(filters.query || "").trim().toLocaleLowerCase();
    if (!query) return true;
    return [
      normalized.title,
      normalized.content,
      normalized.category,
      normalized.source_url,
      normalized.language || "",
      ...normalized.tags,
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes(query);
  },
  filterKnowledgeArticles(articles, filters = {}) {
    return (Array.isArray(articles) ? articles : [])
      .map(knowledgeModule.normalizeKnowledgeArticle)
      .filter((article) => knowledgeModule.matchesKnowledgeArticle(article, filters));
  },
  summarizeKnowledgeArticles(articles) {
    const summary = { total: 0, published: 0, draft: 0, pending_review: 0, retired: 0 };
    for (const article of Array.isArray(articles) ? articles : []) {
      const status = knowledgeModule.normalizeKnowledgeArticle(article).status;
      summary.total += 1;
      if (Object.hasOwn(summary, status)) summary[status] += 1;
    }
    return summary;
  },
  reviewActionsFor(article) {
    const status = knowledgeModule.normalizeKnowledgeArticle(article).status;
    if (status === "draft" || status === "pending_review") return ["publish", "retire"];
    if (status === "published") return ["retire"];
    return [];
  },
  knowledgeFormPayload(values = {}) {
    return {
      title: String(values.title || "").trim(),
      content: String(values.content || "").trim(),
      tags: knowledgeModule.parseKnowledgeTags(values.tags),
      category: String(values.category || "general").trim() || "general",
      source_url: String(values.sourceUrl || "").trim(),
      language: String(values.language || "").trim() || null,
    };
  },
};

function syncKnowledgeLanguageSelects() {
  // The editor + filter selects must cover the full LANGUAGE_NAMES set, or an
  // article in an unlisted language (ru/ar/hi/…) would silently lose its
  // language when edited through the form (audit: review backlog 1).
  for (const select of [els.knowledgeLanguage, els.knowledgeLanguageFilter]) {
    if (!select) continue;
    const covered = new Set([...select.options].map((option) => option.value));
    const missing = Object.keys(LANGUAGE_NAMES)
      .sort((a, b) => LANGUAGE_NAMES[a].localeCompare(LANGUAGE_NAMES[b], "zh"))
      .filter((code) => !covered.has(code));
    for (const code of missing) {
      select.insertAdjacentHTML("beforeend", `<option value="${code}">${LANGUAGE_NAMES[code]}</option>`);
    }
  }
}

const {
  KNOWLEDGE_STATUS_LABELS,
  normalizeKnowledgeArticle,
  filterKnowledgeArticles,
  summarizeKnowledgeArticles,
  reviewActionsFor,
  knowledgeFormPayload,
} = knowledgeModule;

function canWriteKnowledge() {
  return state.me?.permissions?.includes("knowledge:write") === true;
}

// D1 桌面设置页 + D3 知识/管理岛事件桥：委托给 js/desktop-info.js 模块。
function loadDesktopInfo() {
  window.HelixModules?.desktopInfo?.loadDesktopInfo(els);
}

function knowledgeFilters() {
  return {
    query: els.knowledgeSearch?.value || "",
    status: els.knowledgeStatusFilter?.value || "all",
    language: els.knowledgeLanguageFilter?.value || "",
  };
}

function renderKnowledgeSummary() {
  if (!els.knowledgeSummary) return;
  const summary = summarizeKnowledgeArticles(state.knowledgeArticles);
  const writer = canWriteKnowledge();
  const rows = [
    ["全部", summary.total],
    ["已发布", summary.published],
    ["草稿", writer ? summary.draft : "—"],
    ["待审核", writer ? summary.pending_review : "—"],
    ["已停用", writer ? summary.retired : "—"],
  ];
  els.knowledgeSummary.innerHTML = rows
    .map(
      ([label, count]) =>
        `<div class="knowledge-summary-item"><span>${label}</span><strong>${escapeHtml(count)}</strong></div>`,
    )
    .join("");
}

function knowledgeSourceMarkup(article) {
  const source = String(article.source_url || "");
  if (/^https?:\/\//i.test(source)) {
    return `<a class="knowledge-source" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(source)}">${escapeHtml(source)}</a>`;
  }
  return `<span class="knowledge-source" title="${escapeHtml(source)}">${escapeHtml(source || "未记录来源")}</span>`;
}

function renderKnowledgeArticles() {
  if (!els.knowledgeList) return;
  const articles = filterKnowledgeArticles(state.knowledgeArticles, knowledgeFilters());
  const writer = canWriteKnowledge();
  if (els.knowledgeResultCount) {
    els.knowledgeResultCount.textContent = `${articles.length} / ${state.knowledgeArticles.length} 篇`;
  }
  if (!articles.length) {
    els.knowledgeList.innerHTML = "";
    if (els.knowledgeListStatus) {
      els.knowledgeListStatus.textContent = state.knowledgeArticles.length
        ? "没有符合当前筛选条件的文章。"
        : "当前租户还没有知识文章。";
    }
    return;
  }
  if (els.knowledgeListStatus) els.knowledgeListStatus.textContent = "";
  els.knowledgeList.innerHTML = articles
    .map((raw) => {
      const article = normalizeKnowledgeArticle(raw);
      const status = Object.hasOwn(KNOWLEDGE_STATUS_LABELS, article.status)
        ? article.status
        : "retired";
      const statusLabel = KNOWLEDGE_STATUS_LABELS[status] || status;
      const language = article.language ? LANGUAGE_NAMES[article.language] || article.language : "通用";
      const tags = article.tags.length
        ? article.tags.map((tag) => `<span class="knowledge-tag">${escapeHtml(tag)}</span>`).join("")
        : '<span class="knowledge-tag">未分类</span>';
      const reviewButtons = writer
        ? reviewActionsFor(article)
            .map((action) => {
              const label = action === "publish" ? "发布" : "停用";
              return `<button type="button" class="knowledge-action" data-action="${action}" data-article-id="${escapeHtml(article.id)}">${label}</button>`;
            })
            .join("")
        : "";
      const editButton = writer
        ? `<button type="button" class="knowledge-action" data-action="edit" data-article-id="${escapeHtml(article.id)}">编辑</button>`
        : "";
      return `<article class="knowledge-article" role="listitem" data-article-id="${escapeHtml(article.id)}">
        <div class="knowledge-article-head">
          <h3>${escapeHtml(article.title)}</h3>
          <span class="knowledge-status ${status}">${escapeHtml(statusLabel)}</span>
        </div>
        <div class="knowledge-article-meta">
          <span>${escapeHtml(article.category)}</span><span>·</span>
          <span>${escapeHtml(language)}</span><span>·</span>
          <span>v${escapeHtml(article.version || 1)}</span><span>·</span>
          <time>${escapeHtml(formatTime(article.updated_at, true))}</time>
        </div>
        <div class="knowledge-tag-list">${tags}</div>
        <details>
          <summary>查看正文</summary>
          <p class="knowledge-article-content">${escapeHtml(article.content)}</p>
        </details>
        ${knowledgeSourceMarkup(article)}
        ${writer ? `<div class="knowledge-article-actions">${editButton}${reviewButtons}</div>` : ""}
      </article>`;
    })
    .join("");
}

function updateKnowledgeAccessState() {
  const writer = canWriteKnowledge();
  if (els.newKnowledgeDraft) els.newKnowledgeDraft.hidden = !writer;
  if (els.knowledgeReadOnly) els.knowledgeReadOnly.hidden = writer;
  if (!writer && els.knowledgeEditor) els.knowledgeEditor.hidden = true;
  if (!writer && els.knowledgeStatusFilter) {
    for (const option of els.knowledgeStatusFilter.options) {
      option.disabled = option.value !== "all" && option.value !== "published";
    }
    if (!["all", "published"].includes(els.knowledgeStatusFilter.value)) {
      els.knowledgeStatusFilter.value = "published";
    }
  } else if (writer && els.knowledgeStatusFilter) {
    for (const option of els.knowledgeStatusFilter.options) option.disabled = false;
  }
}

async function loadKnowledgeView({ force = false } = {}) {
  if (!els.knowledgeView) return;
  syncKnowledgeLanguageSelects();
  updateKnowledgeAccessState();
  // Cache the article list per permission state so a mid-session role change
  // never shows drafts to a reader from an earlier writer fetch (audit:
  // review backlog 4).
  if (
    !force &&
    state.knowledgeLoadedForWriter === canWriteKnowledge() &&
    state.knowledgeLoadedAt &&
    Date.now() - state.knowledgeLoadedAt < 15000
  ) {
    renderKnowledgeSummary();
    renderKnowledgeArticles();
    return;
  }
  if (els.knowledgeList) els.knowledgeList.setAttribute("aria-busy", "true");
  if (els.knowledgeListStatus) els.knowledgeListStatus.textContent = "正在加载文章…";
  try {
    const path = canWriteKnowledge() ? "/api/knowledge?include_inactive=true" : "/api/knowledge";
    const articles = await api(path);
    state.knowledgeArticles = Array.isArray(articles)
      ? articles.map(normalizeKnowledgeArticle)
      : [];
    state.knowledgeLoadedForWriter = canWriteKnowledge();
    state.knowledgeLoadedAt = Date.now();
    renderKnowledgeSummary();
    renderKnowledgeArticles();
  } catch (error) {
    state.knowledgeArticles = [];
    state.knowledgeLoadedForWriter = null;
    state.knowledgeLoadedAt = 0;
    if (els.knowledgeList) els.knowledgeList.innerHTML = "";
    if (els.knowledgeListStatus) {
      els.knowledgeListStatus.textContent = `知识文章加载失败：${error.message || error}`;
    }
  } finally {
    if (els.knowledgeList) els.knowledgeList.setAttribute("aria-busy", "false");
  }
}

function resetKnowledgeEditor({ close = false } = {}) {
  state.knowledgeEditingId = null;
  els.knowledgeForm?.reset();
  if (els.knowledgeCategory) els.knowledgeCategory.value = "general";
  if (els.knowledgeEditorTitle) els.knowledgeEditorTitle.textContent = "新建知识草稿";
  if (els.knowledgeSaveLabel) els.knowledgeSaveLabel.textContent = "保存草稿";
  if (els.knowledgeEditor) els.knowledgeEditor.hidden = close;
}

function editKnowledgeArticle(articleId) {
  if (!canWriteKnowledge()) return;
  const raw = state.knowledgeArticles.find((article) => article.id === articleId);
  if (!raw) return;
  const article = normalizeKnowledgeArticle(raw);
  state.knowledgeEditingId = article.id;
  if (els.knowledgeTitle) els.knowledgeTitle.value = article.title;
  if (els.knowledgeContent) els.knowledgeContent.value = article.content;
  if (els.knowledgeTags) els.knowledgeTags.value = article.tags.join(", ");
  if (els.knowledgeCategory) els.knowledgeCategory.value = article.category;
  if (els.knowledgeLanguage) els.knowledgeLanguage.value = article.language || "";
  if (els.knowledgeSource) els.knowledgeSource.value = article.source_url;
  if (els.knowledgeEditorTitle) els.knowledgeEditorTitle.textContent = "编辑知识文章";
  if (els.knowledgeSaveLabel) els.knowledgeSaveLabel.textContent = "保存修改";
  if (els.knowledgeEditor) els.knowledgeEditor.hidden = false;
  els.knowledgeTitle?.focus({ preventScroll: true });
}

async function saveKnowledgeArticle(event) {
  event.preventDefault();
  if (!canWriteKnowledge() || !els.knowledgeForm) return;
  const payload = knowledgeFormPayload({
    title: els.knowledgeTitle?.value,
    content: els.knowledgeContent?.value,
    tags: els.knowledgeTags?.value,
    category: els.knowledgeCategory?.value,
    sourceUrl: els.knowledgeSource?.value,
    language: els.knowledgeLanguage?.value,
  });
  if (!payload.tags.length) {
    showToast("请至少填写一个知识标签", true);
    els.knowledgeTags?.focus();
    return;
  }
  // HTML minlength counts raw characters; a trimmed payload can fall short of
  // the backend schema (min 2 title / 10 content). Surface it before the 422.
  if (payload.title.length < 2) {
    showToast("标题至少需要 2 个字符", true);
    els.knowledgeTitle?.focus();
    return;
  }
  if (payload.content.length < 10) {
    showToast("正文至少需要 10 个字符", true);
    els.knowledgeContent?.focus();
    return;
  }
  const editingId = state.knowledgeEditingId;
  setFormBusy(els.knowledgeForm, true);
  try {
    const path = editingId
      ? `/api/knowledge/${encodeURIComponent(editingId)}`
      : "/api/knowledge/drafts";
    await api(path, {
      method: editingId ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    resetKnowledgeEditor({ close: true });
    state.knowledgeLoadedAt = 0;
    await loadKnowledgeView({ force: true });
    showToast(editingId ? "知识文章已更新" : "知识草稿已创建");
  } catch (error) {
    showToast(`知识文章保存失败：${error.message || error}`, true);
  } finally {
    setFormBusy(els.knowledgeForm, false);
  }
}

async function reviewKnowledgeArticle(articleId, action) {
  if (!canWriteKnowledge() || !["publish", "retire"].includes(action)) return;
  if (action === "retire" && !window.confirm("确认停用该知识文章？停用后将不再参与检索。")) {
    return;
  }
  if (els.knowledgeList) els.knowledgeList.setAttribute("aria-busy", "true");
  try {
    await api(`/api/knowledge/${encodeURIComponent(articleId)}/review`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    state.knowledgeLoadedAt = 0;
    await loadKnowledgeView({ force: true });
    showToast(action === "publish" ? "知识文章已发布" : "知识文章已停用");
  } catch (error) {
    showToast(`审核操作失败：${error.message || error}`, true);
  } finally {
    if (els.knowledgeList) els.knowledgeList.setAttribute("aria-busy", "false");
  }
}

// ---- UI 升级 §17.1: 全局导航栏 -------------------------------------------

function setNavActive(view) {
  for (const item of els.navItems) {
    item.classList.toggle("is-active", item.dataset.view === view);
  }
}

function showAppView(name) {
  const views = {
    workspace: els.workspaceView,
    quality: els.qualityView,
    knowledge: els.knowledgeView,
    admin: els.adminView,
  };
  for (const [key, element] of Object.entries(views)) {
    if (element) element.hidden = key !== name;
  }
  // D1 设置页：真实的桌面运行时信息（版本/端口/数据目录），不再是占位文案。
  if (els.placeholderView) {
    els.placeholderView.hidden = name !== "settings";
    if (els.placeholderView.hidden === false) loadDesktopInfo();
  }
}

function switchAppView(view) {
  setNavActive(view);
  showAppView(view);
  if (view === "quality") {
    // Reuse the Phase 21 aggregates; the parametrized renderer fills the
    // standalone view containers. The fresh fetch is non-critical — the
    // cached buckets render immediately, so it is scheduled for idle time.
    renderQualityPanel(els.qualityViewBuckets, els.qualityViewGaps);
    scheduleIdle(() => loadQualityPanel());
  }
  if (view === "admin") {
    void loadAdminView();
  }
  if (view === "knowledge") {
    void loadKnowledgeView();
  }
}

function currentAppView() {
  const active = els.navItems.find((item) => item.classList.contains("is-active"));
  return active ? active.dataset.view : "workspace";
}

// ---- UI 升级 §17.1: 命令面板 (Ctrl+K) ------------------------------------

const commandModule = window.HelixModules?.commands || {
  buildStaticCommands: () => [],
  conversationCommand: () => ({}),
  filterCommands: (list) => list,
};
const { buildStaticCommands, conversationCommand, filterCommands } = commandModule;

let commandItems = [];
let commandIndex = 0;
let conversationCommands = [];
let conversationCommandsLoadedAt = 0;

async function loadConversationCommands({ force = false } = {}) {
  if (!force && conversationCommandsLoadedAt && Date.now() - conversationCommandsLoadedAt < 60000) {
    return conversationCommands;
  }
  try {
    const rows = await api("/api/conversations?limit=50");
    conversationCommands = (Array.isArray(rows) ? rows : []).map(conversationCommand);
    conversationCommandsLoadedAt = Date.now();
  } catch {
    conversationCommands = [];
  }
  return conversationCommands;
}

function renderCommandResults() {
  if (!els.commandResults) return;
  const query = els.commandInput ? els.commandInput.value : "";
  const filtered = filterCommands([...buildStaticCommands(), ...conversationCommands], query);
  commandItems = filtered;
  commandIndex = Math.min(commandIndex, Math.max(0, filtered.length - 1));
  if (!filtered.length) {
    els.commandResults.innerHTML = '<div class="command-empty">没有匹配的命令或会话</div>';
    return;
  }
  const groups = new Map();
  for (const command of filtered) {
    const list = groups.get(command.group) || [];
    list.push(command);
    groups.set(command.group, list);
  }
  let html = "";
  let flatIndex = 0;
  for (const [group, commands] of groups.entries()) {
    html += `<div class="command-group-label">${escapeHtml(group)}</div>`;
    for (const command of commands) {
      const selected = flatIndex === commandIndex;
      html += `<button type="button" class="command-item${selected ? " is-selected" : ""}" role="option" aria-selected="${selected}" data-command-index="${flatIndex}">${escapeHtml(command.label)}</button>`;
      flatIndex += 1;
    }
  }
  els.commandResults.innerHTML = html;
  els.commandResults
    .querySelectorAll(".command-item")
    .forEach((item) => item.addEventListener("click", () => runCommand(commandItems[Number(item.dataset.commandIndex)])));
}

async function openCommandPalette() {
  if (!els.commandPalette) return;
  // Always re-arm the conversation jump list: loadConversationCommands has a
  // 60s TTL of its own, so the palette shows fresh conversations instead of
  // freezing on the first fetch for the whole session (audit D1).
  await loadConversationCommands();
  if (els.commandInput) els.commandInput.value = "";
  commandIndex = 0;
  renderCommandResults();
  if (typeof els.commandPalette.showModal === "function") {
    els.commandPalette.showModal();
  } else {
    els.commandPalette.setAttribute("open", "");
  }
  if (els.commandInput) els.commandInput.focus();
}

function closeCommandPalette() {
  if (!els.commandPalette) return;
  if (typeof els.commandPalette.close === "function") {
    els.commandPalette.close();
  } else {
    els.commandPalette.removeAttribute("open");
  }
}

function runCommand(command) {
  if (!command) return;
  closeCommandPalette();
  const { run } = command;
  if (run.startsWith("view:")) {
    switchAppView(run.slice("view:".length));
    return;
  }
  if (run.startsWith("conversation:")) {
    switchAppView("workspace");
    void loadDetail(run.slice("conversation:".length));
    return;
  }
  const actions = {
    "action:new_conversation": () => els.newConversation?.click(),
    "action:refresh": () => refreshAll(),
    "action:toggle_theme": () => document.getElementById("themeToggle")?.click(),
    "action:toggle_lowperf": () => els.lowPerfToggle?.click(),
    "action:toggle_inspector": () => els.inspectorToggle?.click(),
    "action:save_view": () => els.saveView?.click(),
  };
  const action = actions[run];
  if (action) action();
}

// ---- UI 升级 §17.2: 三档密度 (comfortable/compact/dense) --------------------

const densityModule = window.HelixModules?.density || {
  normalizeDensity: (value) => (["comfortable", "compact", "dense"].includes(value) ? value : "comfortable"),
  nextDensity: (level) => {
    const levels = ["comfortable", "compact", "dense"];
    const index = Math.max(0, levels.indexOf(level));
    return levels[(index + 1) % levels.length];
  },
  effectiveDensity: (level, lowPerf) => (lowPerf ? "compact" : level),
  isCompactDensity: (level, lowPerf) => {
    const effective = lowPerf ? "compact" : level;
    return effective === "compact" || effective === "dense";
  },
};
const { normalizeDensity, nextDensity, effectiveDensity, isCompactDensity } = densityModule;

// ROADMAP §18.4: queue virtualization helpers (pure module, fallback keeps the
// legacy column rendering working if the module entry has not loaded).
const vqueueModule = window.HelixModules?.vqueue || {
  VIRTUAL_THRESHOLD: 200,
  computeWindow: ({ total, scrollTop = 0, viewport = 0, rowHeight = 118, overscan = 4 }) => {
    const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
    const last = Math.min(total - 1, Math.ceil((scrollTop + viewport) / rowHeight) + overscan - 1);
    return { first, last, count: Math.max(0, last - first + 1), topPad: first * rowHeight, bottomPad: Math.max(0, (total - 1 - last) * rowHeight) };
  },
  estimatedRowHeight: () => 118,
  signatureOf: (c) => `${c.id}:${c.status}:${c.updated_at}:${c.version ?? 0}`,
  diffRows: () => ({ updates: [], adds: [], removes: [], keeps: [] }),
  rowHeightFromElement: () => 118,
};
const { VIRTUAL_THRESHOLD, computeWindow, estimatedRowHeight, signatureOf, rowHeightFromElement } =
  vqueueModule;

// ROADMAP §18.4: single-SSE multi-tab relay (pure module). Null fallback keeps
// per-tab SSE when the module entry has not loaded or the browser lacks
// BroadcastChannel — each tab then opens its own stream as before.
const broadcastModule = window.HelixModules?.broadcast || null;

const DENSITY_LABELS = { comfortable: "舒适", compact: "紧凑", dense: "密集" };

// ---- UI 升级 §17.3: 管理页 (tenant quota / members / webhooks) ------------

const WEBHOOK_EVENTS = [
  ["conversation.created", "会话创建"],
  ["conversation.escalated", "升级人工"],
  ["conversation.resolved", "会话解决"],
  ["conversation.sla_breached", "SLA 违约"],
  ["conversation.sla_impending", "SLA 临近"],
  ["report.generated", "报表生成"],
];

const ROLE_LABELS = {
  admin: "管理员",
  supervisor: "主管",
  operator: "客服",
  channel: "渠道",
  viewer: "只读",
  auditor: "审计员",
};

function canManage() {
  // The page includes Webhook/report/routing operations whose backend
  // contract requires admin:manage. A tenant:manage-only principal may use
  // the API's quota/member routes directly but must see the page denial
  // rather than enter a partially forbidden workspace.
  return state.me?.permissions?.includes("admin:manage") === true;
}

function adminTenantId() {
  return state.me?.tenant_id || TENANT;
}

function renderWebhookEventCheckboxes() {
  if (!els.webhookEvents) return;
  els.webhookEvents.innerHTML = WEBHOOK_EVENTS
    .map(([event, label]) => `<label><input type="checkbox" value="${escapeHtml(event)}" />${escapeHtml(label)}</label>`)
    .join("");
}

function renderQuota(quota) {
  if (!els.quotaReadout) return;
  const storageMb = quota.storage_quota_bytes != null
    ? Math.round(quota.storage_quota_bytes / (1024 * 1024))
    : "—";
  els.quotaReadout.innerHTML = `
    <dt>租户</dt><dd>${escapeHtml(quota.name || quota.tenant_id)}</dd>
    <dt>会话配额</dt><dd>${quota.conversation_quota ?? "—"}</dd>
    <dt>存储配额</dt><dd>${storageMb} MB</dd>
    <dt>每日 turn 预算</dt><dd>${quota.daily_turn_budget ?? "—"}</dd>
    <dt>允许模型</dt><dd>${(quota.allowed_models || []).join(", ") || "全部"}</dd>`;
}

async function loadCsatSummary() {
  return window.HelixModules?.['qualityPanel']?.['loadCsatSummary'](...arguments);
}

// moved to js/qualityPanel.js (renderCsatSummary)

function renderMembers(members) {
  if (!els.memberList) return;
  if (!members.length) {
    els.memberList.innerHTML = '<li class="admin-empty">暂无成员</li>';
    return;
  }
  const selfActor = state.me?.actor_id || "";
  els.memberList.innerHTML = members
    .map((member) => {
      const isSelf = member.actor_id === selfActor;
      // Phase 32.1 audit: self-deactivation guard mirrors the backend. The
      // role select and deactivate button are disabled for the current
      // admin so the last tenant:manage holder cannot lock the tenant.
      const selfGuard = isSelf ? " disabled" : "";
      const selfHint = isSelf ? "（你）" : "";
      return `
      <li class="admin-member">
        <span class="admin-member-actor">${escapeHtml(member.actor_id)}${selfHint}</span>
        <span class="admin-member-role">${escapeHtml(ROLE_LABELS[member.role] || member.role)}</span>
        <span class="admin-member-actions">
          <select class="admin-ghost-button member-role-select" data-actor="${escapeHtml(member.actor_id)}" aria-label="变更角色"${selfGuard}>
            ${Object.entries(ROLE_LABELS).map(([value, label]) => `<option value="${value}"${value === member.role ? " selected" : ""}>${label}</option>`).join("")}
          </select>
          ${member.status !== "deactivated"
            ? `<button type="button" class="admin-ghost-button member-deactivate" data-actor="${escapeHtml(member.actor_id)}"${selfGuard}>停用</button>`
            : '<span class="admin-member-role">已停用</span>'}
        </span>
      </li>`;
    })
    .join("");
}

function renderWebhooks(webhooks) {
  if (!els.webhookList) return;
  if (!webhooks.length) {
    els.webhookList.innerHTML = '<li class="admin-empty">暂无 Webhook</li>';
    return;
  }
  els.webhookList.innerHTML = webhooks
    .map((hook) => `
      <li class="admin-webhook">
        <span class="admin-webhook-url" title="${escapeHtml(hook.url)}">${escapeHtml(hook.url)}</span>
        <span class="admin-webhook-events">${escapeHtml(hook.events.join(" · "))}</span>
        <button type="button" class="admin-ghost-button webhook-delete" data-id="${escapeHtml(hook.id)}">删除</button>
      </li>`)
    .join("");
}

async function loadAdminView() {
  if (!els.adminView || !canManage()) {
    if (els.adminDenied) els.adminDenied.hidden = false;
    if (els.adminContent) els.adminContent.hidden = true;
    return;
  }
  if (els.adminDenied) els.adminDenied.hidden = true;
  if (els.adminContent) els.adminContent.hidden = false;
  const tenantId = adminTenantId();
  try {
    const [quota, members, webhooks] = await Promise.all([
      api(`/api/admin/tenants/${encodeURIComponent(tenantId)}/quota`),
      api(`/api/admin/tenants/${encodeURIComponent(tenantId)}/members`),
      api("/api/webhooks"),
    ]);
    renderQuota(quota);
    renderMembers(Array.isArray(members) ? members : []);
    renderWebhooks(Array.isArray(webhooks) ? webhooks : []);
    renderReportWebhookOptions(webhooks);
    await loadReportSubscriptions();
    // 先填充 agent-groups cache 再渲染路由规则,否则首屏每条规则
    // group_id 落入 id fallback(组名不可解析)。
    await loadRuleGroups();
    await loadSlaPolicies();
    await loadRoutingRules();
    await loadCsatSummary();
  } catch (error) {
    showToast(`管理数据加载失败：${error.message || error}`, true);
  }
}

async function saveQuota(event) {
  event.preventDefault();
  const body = {};
  if (els.quotaConversations?.value !== "") body.conversation_quota = Number(els.quotaConversations.value);
  if (els.quotaStorageMb?.value !== "") body.storage_quota_bytes = Number(els.quotaStorageMb.value) * 1024 * 1024;
  if (!Object.keys(body).length) return;
  try {
    const quota = await api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/quota`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    renderQuota(quota);
    if (els.quotaConversations) els.quotaConversations.value = "";
    if (els.quotaStorageMb) els.quotaStorageMb.value = "";
    showToast("配额已更新");
  } catch (error) {
    showToast(`配额保存失败：${error.message || error}`, true);
  }
}

async function inviteMember(event) {
  event.preventDefault();
  const actorId = els.memberActorId?.value.trim();
  if (!actorId) return;
  try {
    await api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members`, {
      method: "POST",
      body: JSON.stringify({ actor_id: actorId, role: els.memberRole?.value || "operator" }),
    });
    if (els.memberActorId) els.memberActorId.value = "";
    await loadAdminView();
    showToast("成员已邀请");
  } catch (error) {
    showToast(`邀请失败：${error.message || error}`, true);
  }
}

async function changeMemberRole(actorId, role) {
  // Phase 32.1 audit (client-side self-guard): the backend rejects demoting
  // yourself; surface the message before the round-trip so the UX is clear.
  if (actorId === (state.me?.actor_id || "") && role !== "admin") {
    showToast("不能把自己的角色降级——至少保留一位管理员", true);
    return;
  }
  try {
    await api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members/${encodeURIComponent(actorId)}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    });
    await loadAdminView();
  } catch (error) {
    showToast(`角色变更失败：${error.message || error}`, true);
  }
}

async function deactivateMember(actorId) {
  // Phase 32.1 audit (client-side self-guard): the backend rejects this.
  if (actorId === (state.me?.actor_id || "")) {
    showToast("不能停用自己——请先指派另一位管理员", true);
    return;
  }
  try {
    await api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members/${encodeURIComponent(actorId)}/deactivate`, {
      method: "POST",
      body: "{}",
    });
    await loadAdminView();
  } catch (error) {
    showToast(`停用失败：${error.message || error}`, true);
  }
}

async function registerWebhook(event) {
  event.preventDefault();
  const url = els.webhookUrl?.value.trim();
  const secret = els.webhookSecret?.value.trim();
  const events = Array.from(els.webhookEvents?.querySelectorAll('input[type="checkbox"]:checked') || [])
    .map((input) => input.value);
  if (!url || !secret || !events.length) {
    showToast("请填写 URL、密钥并至少选择一个事件", true);
    return;
  }
  try {
    await api("/api/webhooks", {
      method: "POST",
      body: JSON.stringify({ url, events, secret }),
    });
    if (els.webhookUrl) els.webhookUrl.value = "";
    if (els.webhookSecret) els.webhookSecret.value = "";
    if (els.webhookEvents) {
      els.webhookEvents.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = false; });
    }
    await loadAdminView();
    showToast("Webhook 已注册");
  } catch (error) {
    showToast(`Webhook 注册失败：${error.message || error}`, true);
  }
}

async function deleteWebhook(id) {
  // Phase 32.1 audit: destructive action — confirm before sending. Avoids
  // accidental double-clicks wiping an active delivery target.
  if (!window.confirm("确认删除该 Webhook 端点？已注册的待投递事件将进入死信。")) return;
  try {
    await api(`/api/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadAdminView();
  } catch (error) {
    showToast(`删除失败：${error.message || error}`, true);
  }
}


function renderReportWebhookOptions(webhooks) {
  return window.HelixModules?.['adminReport']?.['renderReportWebhookOptions'](...arguments);
}

async function loadReportSubscriptions() {
  return window.HelixModules?.['adminReport']?.['loadReportSubscriptions'](...arguments);
}

// moved to js/adminReport.js (renderReportSubscriptions)

// moved to js/adminReport.js (createReportSubscription)

// moved to js/adminReport.js (toggleReportSubscription)

// moved to js/adminReport.js (deleteReportSubscription)

// moved to js/adminReport.js (generateReportPreview)

// moved to js/adminReport.js (exportReportCsv)


// moved to js/adminReport.js (slaPolicyLabel)

// moved to js/adminReport.js (renderSlaPolicies)

async function loadSlaPolicies() {
  return window.HelixModules?.['adminReport']?.['loadSlaPolicies'](...arguments);
}

// moved to js/adminReport.js (fillSlaPolicyForm)

// moved to js/adminReport.js (saveSlaPolicy)

// moved to js/adminReport.js (renderRuleGroups)

async function loadRuleGroups() {
  return window.HelixModules?.['adminReport']?.['loadRuleGroups'](...arguments);
}

// moved to js/adminReport.js (routingRuleLabel)

// moved to js/adminReport.js (renderRoutingRules)

async function loadRoutingRules() {
  return window.HelixModules?.['adminReport']?.['loadRoutingRules'](...arguments);
}

// moved to js/adminReport.js (createRoutingRule)

// moved to js/adminReport.js (deleteRoutingRule)

function setDensity(level, { persist = true } = {}) {
  state.density = normalizeDensity(level);
  // Row heights change with density — force a live re-measure next render.
  state.queueRowHeight = 0;
  const effective = effectiveDensity(state.density, state.lowPerf);
  document.body.setAttribute("data-density", effective);
  if (persist) window.localStorage.setItem(PREF_DENSITY, state.density);
  if (els.densityToggle) {
    const i18n = window.HelixModules?.i18n;
    const label = DENSITY_LABELS[effective] || effective;
    const title = i18n?.t
      ? i18n.t("density.toggle", { level: label })
      : `密度:${label}（点击切换）`;
    els.densityToggle.setAttribute("aria-pressed", String(effective !== "comfortable"));
    els.densityToggle.title = title;
    els.densityToggle.setAttribute("aria-label", title);
  }
}

function renderCopilot(detail) {
  return window.HelixModules?.['composer']?.['renderCopilot'](...arguments);
}

async function loadLabelCatalog({ force = false } = {}) {
  if (!force && state.labelsLoadedAt && Date.now() - state.labelsLoadedAt < 60000 && state.labelCatalog.length) {
    return state.labelCatalog;
  }
  state.labelCatalog = await api("/api/conversation-labels");
  state.labelsLoadedAt = Date.now();
  return state.labelCatalog;
}

function queueSignature(conversations) {
  return conversations
    .map(
      (item) =>
        `${item.id}:${item.version || 0}:${item.updated_at || ""}:${item.status}:${item.claim_active ? 1 : 0}:${item.needs_response ? 1 : 0}`,
    )
    .join("|");
}

async function refreshAll({ silent = false, refreshDetail = true, background = false } = {}) {
  if (state.refreshPromise) return state.refreshPromise;
  const refreshTask = runRefresh({ silent, refreshDetail, background });
  state.refreshPromise = refreshTask;
  try {
    return await refreshTask;
  } finally {
    if (state.refreshPromise === refreshTask) state.refreshPromise = null;
  }
}

async function runRefresh({ silent = false, refreshDetail = true, background = false } = {}) {
  if (!silent) {
    els.refreshList.classList.add("is-spinning");
    if (!state.conversations.length) renderLoadingQueue();
  }
  try {
    const meRequest = state.me ? Promise.resolve(state.me) : api("/api/me");
    const queueOnly = background;
    const staleDashboard = !state.dashboard || !background;
    const requests = [
      meRequest,
      staleDashboard ? api("/api/dashboard") : Promise.resolve(state.dashboard),
      apiWithHeaders(`/api/conversations?${conversationQuery()}`),
      queueOnly
        ? Promise.resolve(state.labelCatalog)
        : loadLabelCatalog({ force: !state.labelsLoadedAt }),
    ];
    const [me, dashboard, conversationPage, labelCatalog] = await Promise.all(requests);
    const conversations = conversationPage.data;
    const signature = queueSignature(conversations);
    const queueChanged = signature !== state.lastQueueSignature;
    const firstMe = !state.me;
    state.me = me;
    if (firstMe) pruneExpiredDrafts();
    if (firstMe) scheduleIdle(() => loadMentions());
    // roster 节流刷新:首次失败下个周期重试,成功后在后台周期更新,
    // 新邀请的同事最多 ~2 分钟出现在 @ 候选(HIGH-2 修复)。
    if (firstMe || !state.collaboratorsLoadedAt || Date.now() - state.collaboratorsLoadedAt > 120000) {
      scheduleIdle(() => loadCollaborators());
    }
    if (dashboard) state.dashboard = dashboard;
    if (labelCatalog) state.labelCatalog = labelCatalog;
    state.conversations = conversations;
    state.lastQueueSignature = signature;
    if (!queueOnly) await loadCannedResponses({ force: !state.cannedLoadedAt });
    const visibleIds = new Set(conversations.map((conversation) => conversation.id));
    state.bulkSelected = new Set(
      [...state.bulkSelected].filter((conversationId) => visibleIds.has(conversationId)),
    );
    state.queueHasMore = conversationPage.response.headers.get("X-Has-More") === "true";
    state.queueCursor = conversationPage.response.headers.get("X-Next-Cursor");
    els.operatorIdentity.textContent = `${me.actor_id} · ${roleLabel(me.role)}`;
    if (!queueOnly) renderLabelFilter();
    els.focusWaiting.setAttribute("aria-pressed", String(els.ownershipFilter.value === "needs_response"));
    if (state.dashboard && (!background || staleDashboard)) renderMetrics(state.dashboard);
    if (!background || queueChanged || !els.list.children.length) renderQueue();

    const selected = state.selectedId
      ? conversations.find((conversation) => conversation.id === state.selectedId)
      : null;
    if (selected) {
      const detailStale =
        !state.detail ||
        state.detail.conversation?.id !== selected.id ||
        state.detail.conversation?.version !== selected.version ||
        state.detail.conversation?.updated_at !== selected.updated_at;
      if (refreshDetail && !background && detailStale) await loadDetail(state.selectedId);
      else if (refreshDetail && background && detailStale) void loadDetail(state.selectedId);
    } else if (conversations.length) {
      if (!background && !window.HelixModules?.ticketView?.autoSelectSuppressed?.()) await selectConversation(conversations[0].id);
    } else if (!background) {
      clearSelection();
    }
  } catch (error) {
    if (!background) showToast(error.message, true);
    if (!state.conversations.length) {
      els.list.innerHTML = `<div class="queue-empty">${escapeHtml(error.message)}</div>`;
    }
  } finally {
    els.refreshList.classList.remove("is-spinning");
  }
}

async function loadMoreConversations() {
  if (!state.queueHasMore || !state.queueCursor || state.queueLoadingMore) return;
  const query = new URLSearchParams(conversationQuery());
  const queryKey = `${query.get("search") || ""}|${query.get("status") || ""}|${query.get("label") || ""}|${query.get("priority") || ""}|${els.ownershipFilter.value}|${els.channelFilter?.value || ""}|${els.sortFilter?.value || "priority"}`;
  query.set("cursor", state.queueCursor);
  state.queueLoadingMore = true;
  renderQueue();
  try {
    const page = await apiWithHeaders(`/api/conversations?${query.toString()}`);
    const activeQueryKey = `${els.searchInput.value.trim()}|${els.statusFilter.value}|${els.labelFilter.value}|${els.priorityFilter.value}|${els.ownershipFilter.value}|${els.channelFilter?.value || ""}|${els.sortFilter?.value || "priority"}`;
    if (activeQueryKey !== queryKey) return;
    const existing = new Set(state.conversations.map((conversation) => conversation.id));
    state.conversations = [
      ...state.conversations,
      ...page.data.filter((conversation) => !existing.has(conversation.id)),
    ];
    state.queueHasMore = page.response.headers.get("X-Has-More") === "true";
    state.queueCursor = page.response.headers.get("X-Next-Cursor");
    renderQueue();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    state.queueLoadingMore = false;
    renderQueue();
  }
}

async function performConversationAction(action, successMessage) {
  if (!state.selectedId) return;
  const conversationId = state.selectedId;
  try {
    const result = await api(`/api/conversations/${encodeURIComponent(conversationId)}/${action}`, {
      method: "POST",
    });
    if (action === "resolve") {
      const surveyUrl = result && result.survey_url;
      if (surveyUrl) {
        els.csatUrl.textContent = surveyUrl;
        els.csatBanner.hidden = false;
      } else {
        els.csatBanner.hidden = true;
      }
    }
    showToast(successMessage);
    await loadDetail(conversationId);
    await refreshAll({ refreshDetail: false });
  } catch (error) {
    showToast(error.message, true);
  }
}

async function applyBulkAction() {
  const conversationIds = [...state.bulkSelected];
  if (!conversationIds.length) return;
  const selectedAction = els.bulkAction.value;
  const payload = { conversation_ids: conversationIds };
  if (selectedAction === "priority-high" || selectedAction === "priority-normal") {
    payload.action = "set_priority";
    payload.priority = selectedAction === "priority-high" ? "high" : "normal";
  } else if (selectedAction === "claim" || selectedAction === "release") {
    payload.action = selectedAction;
  } else {
    const labels = els.bulkLabelInput.value
      .split(/[,，]/)
      .map((label) => label.trim())
      .filter(Boolean);
    if (!labels.length) {
      showToast("请输入标签", true);
      els.bulkLabelInput.focus();
      return;
    }
    payload.action = selectedAction === "add-label" ? "add_labels" : "remove_labels";
    payload.labels = labels;
  }
  setFormBusy(els.bulkToolbar, true);
  try {
    const result = await api("/api/conversations/bulk-actions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    showToast(`已更新 ${result.updated} 个会话`);
    state.bulkSelected.clear();
    els.bulkLabelInput.value = "";
    if (["add-label", "remove-label"].includes(selectedAction)) state.labelsLoadedAt = 0;
    await refreshAll({ silent: true });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setFormBusy(els.bulkToolbar, false);
    renderBulkToolbar();
  }
}

els.list.addEventListener("click", (event) => {
  const item = event.target.closest(".conversation-item");
  if (item?.dataset.id) selectConversation(item.dataset.id);
});

// ROADMAP §18.4: virtual-mode scroll keeps the windowed render aligned.
els.list.addEventListener("scroll", handleQueueScroll, { passive: true });

// ROADMAP §18.4: the "加载更早消息" affordance above the thread is the single
// lazy-load trigger — explicit, and free of scroll-event double-fires.
els.messages.addEventListener("click", (event) => {
  const path = typeof event.composedPath === "function" ? event.composedPath() : [];
  const button =
    path.find((node) => node instanceof HTMLElement && node.classList?.contains("feedback-button")) ||
    event.target.closest?.(".feedback-button");
  if (button) submitFeedback(button);
  const translateButton =
    path.find((node) => node instanceof HTMLElement && node.classList?.contains("translate-button")) ||
    event.target.closest?.(".translate-button");
  if (translateButton) void translateMessage(translateButton);
});

els.list.addEventListener("change", (event) => {
  const checkbox = event.target.closest(".conversation-checkbox");
  if (!checkbox?.dataset.selectId) return;
  if (checkbox.checked) state.bulkSelected.add(checkbox.dataset.selectId);
  else state.bulkSelected.delete(checkbox.dataset.selectId);
  checkbox.closest(".conversation-row")?.classList.toggle("is-selected", checkbox.checked);
  renderBulkToolbar();
});

document.querySelectorAll(".inspector-tab").forEach((button) => {
  button.addEventListener("click", () => switchInspectorTab(button.dataset.tab));
});

els.customerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedId) return;
  const conversationId = state.selectedId;
  const content = els.customerInput.value.trim();
  if (!content) return;
  setFormBusy(els.customerForm, true);
  try {
    const result = await api(
      `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        headers: { "Idempotency-Key": newIdempotencyKey() },
        body: JSON.stringify({ content }),
      },
    );
    els.customerInput.value = "";
    if (!result.assistant_message) showToast("客户消息已进入人工队列");
    await loadDetail(conversationId);
    void refreshAll({ silent: true, refreshDetail: false });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setFormBusy(els.customerForm, false);
  }
});



els.operatorInput.addEventListener("input", () => {
  const conversationId = state.selectedId;
  if (conversationId) {
    window.clearTimeout(state.draftTimer);
    state.draftTimer = window.setTimeout(() => {
      saveDraft(conversationId, els.operatorInput.value);
    }, 300);
  }
  const match = els.operatorInput.value.match(/(^|\s)\/([^\s]*)$/);
  if (match) renderMacroSuggest(match[2] || "");
  else hideMacroSuggest();
});

els.operatorInput.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    els.operatorForm.requestSubmit();
    return;
  }
  if (event.key === "Escape" && state.macroOpen) {
    event.preventDefault();
    hideMacroSuggest();
  }
});

els.noteInput.addEventListener("input", (event) => {
  // IME 组合期间(input 事件带中间拼音/片假名)不渲染也不收起;避免候选
  // 列表干扰选字(中文客服台第一优先,HIGH-1 修复)。
  if (event.isComposing) return;
  const ta = els.noteInput;
  const caret = ta.selectionStart ?? ta.value.length;
  const head = ta.value.slice(0, caret);
  const match = head.match(MENTION_PATTERN);
  if (match) {
    mentionIndex = -1;
    renderMentionSuggest(match[1]);
  } else {
    hideMentionSuggest();
  }
});

els.noteInput.addEventListener("keydown", (event) => {
  if (event.isComposing) return;
  if (!state.mentionOpen) return;
  if (event.key === "Escape") {
    event.preventDefault();
    hideMentionSuggest();
    return;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const options = els.mentionSuggest ? els.mentionSuggest.querySelectorAll(".macro-option") : [];
    const delta = event.key === "ArrowDown" ? 1 : -1;
    setMentionActive(mentionIndex < 0 ? (delta > 0 ? 0 : options.length - 1) : mentionIndex + delta);
    return;
  }
  if (event.key === "Enter" || event.key === "Tab") {
    const active = els.mentionSuggest ? els.mentionSuggest.querySelector(".macro-option.is-active") : null;
    if (active?.dataset.mentionActor) {
      event.preventDefault();
      applyMentionFromSuggest(active.dataset.mentionActor);
    }
  }
});

els.noteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedId) return;
  const conversationId = state.selectedId;
  const content = els.noteInput.value.trim();
  if (!content) return;
  setFormBusy(els.noteForm, true);
  try {
    await api(`/api/conversations/${encodeURIComponent(conversationId)}/notes`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    els.noteInput.value = "";
    hideMentionSuggest();
    showToast("内部备注已添加");
    await loadDetail(conversationId);
    void refreshAll({ silent: true, refreshDetail: false });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setFormBusy(els.noteForm, false);
  }
});

els.claimBtn.addEventListener("click", () => performConversationAction("claim", "会话已认领"));
if (els.conversationLanguageSelect) {
  // Backlog (多语言客服): PATCH the manual override; the select rolls back on
  // failure and a background refresh re-syncs the whole view.
  els.conversationLanguageSelect.addEventListener("change", async () => {
    if (!state.selectedId) return;
    const language = els.conversationLanguageSelect.value || null;
    try {
      await api(`/api/conversations/${encodeURIComponent(state.selectedId)}/language`, {
        method: "PATCH",
        body: JSON.stringify({ language }),
      });
      if (state.detail) state.detail.conversation.language = language;
      renderSubtitle(state.detail.conversation);
      renderLanguagePicker(state.detail.conversation);
      showToast(
        language
          ? `会话语言已设为 ${LANGUAGE_NAMES[language] || language}`
          : "会话语言已恢复自动检测",
      );
      scheduleIdle(() => refreshAll());
    } catch (error) {
      if (state.detail) renderLanguagePicker(state.detail.conversation);
      showToast(error.message, true);
    }
  });
}
els.releaseBtn.addEventListener("click", () => performConversationAction("release", "认领已释放"));
if (els.assignBtn) {
  els.assignBtn.addEventListener("click", async () => {
    if (!state.selectedId || !state.me?.actor_id) return;
    const conversationId = state.selectedId;
    try {
      await api(`/api/conversations/${encodeURIComponent(conversationId)}/assign`, {
        method: "POST",
        body: JSON.stringify({ assignee_id: state.me.actor_id }),
      });
      showToast("会话已转派给自己");
      await loadDetail(conversationId);
      await refreshAll({ refreshDetail: false });
    } catch (error) {
      showToast(error.message, true);
    }
  });
}
els.acceptBtn.addEventListener("click", () => performConversationAction("accept", "会话已接入"));
els.resolveBtn.addEventListener("click", () => performConversationAction("resolve", "会话已解决"));

els.csatCopyBtn.addEventListener("click", async () => {
  const url = els.csatUrl.textContent;
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
    showToast("满意度链接已复制");
  } catch (error) {
    window.prompt("请手动复制满意度链接：", url);
  }
});
els.reopenBtn.addEventListener("click", () => performConversationAction("reopen", "会话已重开"));

els.newConversation.addEventListener("click", () => {
  els.newConversationForm.reset();
  els.newConversationDialog.showModal();
  window.setTimeout(() => els.newCustomerName.focus(), 0);
});

function closeConversationDialog() {
  els.newConversationDialog.close();
}

els.closeDialog.addEventListener("click", closeConversationDialog);
els.cancelDialog.addEventListener("click", closeConversationDialog);

els.newConversationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    customer_name: els.newCustomerName.value.trim(),
    channel: els.newChannel.value,
  };
  const customerRef = els.newCustomerRef.value.trim();
  if (customerRef) payload.customer_ref = customerRef;
  if (!payload.customer_name) return;
  setFormBusy(els.newConversationForm, true);
  try {
    const created = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    closeConversationDialog();
    state.selectedId = created.id;
    state.conversations = [
      created,
      ...state.conversations.filter((conversation) => conversation.id !== created.id),
    ];
    renderQueue();
    await loadDetail(created.id);
    void refreshAll({ silent: true, refreshDetail: false });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setFormBusy(els.newConversationForm, false);
  }
});

els.refreshList.addEventListener("click", () => refreshAll());
els.loadMore.addEventListener("click", loadMoreConversations);
els.statusFilter.addEventListener("change", () => refreshAll());
els.labelFilter.addEventListener("change", () => refreshAll());
els.priorityFilter.addEventListener("change", () => refreshAll());
els.ownershipFilter.addEventListener("change", () => refreshAll());
if (els.channelFilter) els.channelFilter.addEventListener("change", () => refreshAll());
if (els.sortFilter) els.sortFilter.addEventListener("change", () => refreshAll());
els.focusWaiting.addEventListener("click", () => {
  els.ownershipFilter.value = els.ownershipFilter.value === "needs_response" ? "" : "needs_response";
  refreshAll();
});
els.savedViewSelect.addEventListener("change", () => {
  const view = state.savedViews.find((item) => item.id === els.savedViewSelect.value);
  els.deleteView.disabled = !view;
  if (view) applySavedView(view);
});
els.saveView.addEventListener("click", async () => {
  const name = window.prompt("保存视图名称");
  if (!name?.trim()) return;
  try {
    const created = await api("/api/saved-views", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), filters: currentViewFilters() }),
    });
    await loadSavedViews();
    els.savedViewSelect.value = created.id;
    els.deleteView.disabled = false;
    showToast("视图已保存");
  } catch (error) {
    showToast(error.message, true);
  }
});
els.deleteView.addEventListener("click", async () => {
  const viewId = els.savedViewSelect.value;
  if (!viewId) return;
  try {
    await request(`/api/saved-views/${encodeURIComponent(viewId)}`, { method: "DELETE" });
    await loadSavedViews();
    showToast("视图已删除");
  } catch (error) {
    showToast(error.message, true);
  }
});
els.densityToggle.addEventListener("click", () => {
  // In low-perf the visible density is always compact (effectiveDensity);
  // cycling here would mutate the stored choice with no visible effect and
  // it would only materialize after low-perf is disabled (audit D3).
  if (state.lowPerf) return;
  setDensity(nextDensity(state.density));
  renderQueue();
});
  if (els.lowPerfToggle) {
    els.lowPerfToggle.addEventListener("click", () => {
      state.lowPerf = !state.lowPerf;
      window.localStorage.setItem(PREF_LOW_PERF, state.lowPerf ? "1" : "0");
      applyWorkspacePreferences();
      // UI 升级 §17.2: low-perf forces compact through the density module
      // (effectiveDensity) without discarding the user's chosen level.
      setDensity(state.density, { persist: false });
      schedulePolling();
      showToast(state.lowPerf ? "已开启低配模式" : "已关闭低配模式");
      void refreshAll({ silent: true, refreshDetail: false });
    });
  }
if (els.inspectorToggle) {
  els.inspectorToggle.addEventListener("click", () => {
    state.inspectorCollapsed = !state.inspectorCollapsed;
    window.localStorage.setItem(PREF_INSPECTOR, state.inspectorCollapsed ? "1" : "0");
    applyWorkspacePreferences();
    if (!state.inspectorCollapsed && state.detail) {
      renderInspector(state.detail);
    }
  });
}
els.bulkAction.addEventListener("change", renderBulkToolbar);
els.applyBulk.addEventListener("click", applyBulkAction);
els.clearBulk.addEventListener("click", () => {
  state.bulkSelected.clear();
  renderQueue();
});
els.searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  const delay = state.lowPerf ? 450 : 260;
  searchTimer = window.setTimeout(() => refreshAll({ silent: true, refreshDetail: false }), delay);
});
let queueScrim = null;

function isQueueDrawerMode() {
  return window.matchMedia("(max-width: 900px)").matches;
}

function setBackgroundInert(inert) {
  const main = document.querySelector(".conversation-pane");
  if (!main) return;
  if (inert) main.setAttribute("inert", "");
  else main.removeAttribute("inert");
}

function trapQueueFocus(event) {
  if (event.key !== "Tab") return;
  const focusable = els.queuePane.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function openQueueDrawer() {
  els.queuePane.classList.add("is-open");
  if (!queueScrim) {
    queueScrim = document.createElement("button");
    queueScrim.type = "button";
    queueScrim.className = "queue-scrim";
    queueScrim.setAttribute("aria-label", "关闭会话队列");
    queueScrim.addEventListener("click", closeQueueDrawer);
    els.queuePane.parentElement.insertBefore(queueScrim, els.queuePane);
  }
  queueScrim.hidden = false;
  if (isQueueDrawerMode()) {
    els.queuePane.setAttribute("role", "dialog");
    els.queuePane.setAttribute("aria-modal", "true");
    setBackgroundInert(true);
    els.queuePane.addEventListener("keydown", trapQueueFocus);
  }
  els.mobileQueue.setAttribute("aria-expanded", "true");
  document.getElementById("queueClose")?.focus({ preventScroll: true });
}

function closeQueueDrawer(options = {}) {
  els.queuePane.classList.remove("is-open");
  if (queueScrim) queueScrim.hidden = true;
  els.queuePane.removeAttribute("role");
  els.queuePane.removeAttribute("aria-modal");
  els.queuePane.removeEventListener("keydown", trapQueueFocus);
  setBackgroundInert(false);
  els.mobileQueue.setAttribute("aria-expanded", "false");
  if (options.restoreFocus !== false) els.mobileQueue.focus({ preventScroll: true });
}

els.mobileQueue.addEventListener("click", () => {
  if (els.queuePane.classList.contains("is-open")) closeQueueDrawer();
  else openQueueDrawer();
});
els.backToQueue.addEventListener("click", openQueueDrawer);
document.getElementById("queueClose")?.addEventListener("click", closeQueueDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && els.queuePane.classList.contains("is-open") && queueScrim && !queueScrim.hidden) {
    closeQueueDrawer();
  }
});

document.addEventListener("keydown", (event) => {
  const target = event.target;
  const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
  if (editing || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "/") {
    event.preventDefault();
    els.searchInput.focus();
  } else if (event.key === "c") {
    event.preventDefault();
    els.newConversation.click();
  } else if (event.key === "r") {
    event.preventDefault();
    refreshAll();
  } else if (event.key === "i") {
    event.preventDefault();
    els.inspectorToggle?.click();
  } else if (event.key === "l") {
    event.preventDefault();
    els.lowPerfToggle?.click();
  } else if (["j", "k"].includes(event.key) && state.conversations.length) {
    event.preventDefault();
    const index = Math.max(0, state.conversations.findIndex((item) => item.id === state.selectedId));
    const next = Math.max(0, Math.min(state.conversations.length - 1, index + (event.key === "j" ? 1 : -1)));
    selectConversation(state.conversations[next].id);
  }
});

window.addEventListener("unhandledrejection", (event) => {
  showToast(event.reason?.message || "操作失败", true);
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    // Abort our own stream and release group leadership so a visible tab
    // (which still needs queue updates) can take over immediately instead of
    // waiting out our lease.
    if (state.queueEventSource?.abort) state.queueEventSource.abort();
    if (state.queueRelay?.isRunning()) {
      state.queueRelay.abandon("hidden"); // also fires onSteppedDown -> clean-up
      state.queueEventSource = null;
    }
    return;
  }
  if (state.queueRelay) {
    state.queueRelay.start(); // no-op when still running; re-joins after abandon
  } else {
    connectQueueEvents();
  }
  refreshAll({ silent: true, background: true, refreshDetail: false });
});

if (els.macroSuggest) {
  els.macroSuggest.addEventListener("click", (event) => {
    const button = event.target.closest(".macro-option");
    if (button?.dataset.macroId) applyMacroFromSuggest(button.dataset.macroId);
  });
}
if (els.mentionSuggest) {
  els.mentionSuggest.addEventListener("click", (event) => {
    const button = event.target.closest(".macro-option");
    if (button?.dataset.mentionActor) applyMentionFromSuggest(button.dataset.mentionActor);
  });
}
if (els.cannedList) {
  els.cannedList.addEventListener("click", (event) => {
    const button = event.target.closest(".canned-chip");
    if (button?.dataset.macroId) insertCannedResponse(button.dataset.macroId);
  });
}
window.HelixModules?.qualityPanel?.bindQuality?.();
window.HelixModules?.composer?.bindComposer?.();
// UI 升级 §17.1: 命令面板 — Ctrl+K opens anywhere; arrows/Enter navigate.
// D3 take-over: in the desktop shell the React command-palette island owns
// Ctrl+K (window.__HELIX_ISLAND_MODE__ is set by main.js). Yield here so the
// legacy <dialog> never opens on top of the island overlay. In a browser
// tab the flag is never set and the legacy palette stays the handler.
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    if (window.__HELIX_ISLAND_MODE__) return; // island owns the palette
    event.preventDefault();
    void openCommandPalette();
    return;
  }
  if (!els.commandPalette?.open) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeCommandPalette();
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    commandIndex = Math.min(commandIndex + 1, commandItems.length - 1);
    renderCommandResults();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    commandIndex = Math.max(commandIndex - 1, 0);
    renderCommandResults();
  } else if (event.key === "Enter") {
    event.preventDefault();
    runCommand(commandItems[commandIndex]);
  }
});
if (els.commandInput) {
  els.commandInput.addEventListener("input", () => {
    commandIndex = 0;
    renderCommandResults();
  });
}

window.HelixModules?.ticketView?.bindTickets?.();
window.HelixModules?.attachments?.bindAttachments?.();
// UI 升级 §17.1: 全局导航栏 — view switching + quality refresh.
if (els.appNav) {
  els.appNav.addEventListener("click", (event) => {
    const button = event.target.closest(".nav-item");
    if (button?.dataset.view) switchAppView(button.dataset.view);
  });
}

// ROADMAP §17: knowledge operations — browse/filter for readers, lifecycle
// mutations only when /api/me grants knowledge:write.
if (els.knowledgeSearch) {
  els.knowledgeSearch.addEventListener("input", () => renderKnowledgeArticles());
}
if (els.knowledgeStatusFilter) {
  els.knowledgeStatusFilter.addEventListener("change", () => renderKnowledgeArticles());
}
if (els.knowledgeLanguageFilter) {
  els.knowledgeLanguageFilter.addEventListener("change", () => renderKnowledgeArticles());
}
if (els.newKnowledgeDraft) {
  els.newKnowledgeDraft.addEventListener("click", () => {
    resetKnowledgeEditor();
    els.knowledgeTitle?.focus({ preventScroll: true });
  });
}
if (els.refreshKnowledge) {
  els.refreshKnowledge.addEventListener("click", () => {
    state.knowledgeLoadedAt = 0;
    void loadKnowledgeView({ force: true });
  });
}
if (els.cancelKnowledgeEdit) {
  els.cancelKnowledgeEdit.addEventListener("click", () => resetKnowledgeEditor({ close: true }));
}
if (els.resetKnowledgeForm) {
  els.resetKnowledgeForm.addEventListener("click", () => resetKnowledgeEditor());
}
if (els.knowledgeForm) {
  els.knowledgeForm.addEventListener("submit", (event) => void saveKnowledgeArticle(event));
}
if (els.knowledgeList) {
  els.knowledgeList.addEventListener("click", (event) => {
    const button = event.target.closest(".knowledge-action");
    if (!button) return;
    const articleId = button.dataset.articleId;
    if (button.dataset.action === "edit") editKnowledgeArticle(articleId);
    else void reviewKnowledgeArticle(articleId, button.dataset.action);
  });
}
// UI 升级 §17.3: 管理页 — forms, member actions, webhook delete, refresh.
renderWebhookEventCheckboxes();
if (els.quotaForm) els.quotaForm.addEventListener("submit", (event) => void saveQuota(event));
if (els.memberForm) els.memberForm.addEventListener("submit", (event) => void inviteMember(event));
if (els.webhookForm) els.webhookForm.addEventListener("submit", (event) => void registerWebhook(event));
window.HelixModules?.adminReport?.bindAdminReports?.();
if (els.refreshAdmin) {
  els.refreshAdmin.addEventListener("click", () => void loadAdminView());
}
if (els.memberList) {
  els.memberList.addEventListener("change", (event) => {
    const select = event.target.closest(".member-role-select");
    if (select) void changeMemberRole(select.dataset.actor, select.value);
  });
  els.memberList.addEventListener("click", (event) => {
    const button = event.target.closest(".member-deactivate");
    if (button) void deactivateMember(button.dataset.actor);
  });
}
if (els.webhookList) {
  els.webhookList.addEventListener("click", (event) => {
    const button = event.target.closest(".webhook-delete");
    if (button) void deleteWebhook(button.dataset.id);
  });
}

renderMetrics(null);
renderLoadingQueue();

const storedLowPerf = window.localStorage.getItem(PREF_LOW_PERF);
state.lowPerf = storedLowPerf === "1" || (storedLowPerf !== "0" && detectConstrainedDevice());
if (storedLowPerf == null && state.lowPerf) {
  window.localStorage.setItem(PREF_LOW_PERF, "1");
}
state.inspectorCollapsed = window.localStorage.getItem(PREF_INSPECTOR) === "1";
state.density = normalizeDensity(window.localStorage.getItem(PREF_DENSITY));
if (state.lowPerf && window.localStorage.getItem(PREF_DENSITY) == null) {
  // Auto-detected low-perf must not persist a density the user never chose
  // (audit D2): effectiveDensity() forces compact while low-perf is active,
  // and the stored default stays untouched for when low-perf is disabled.
  state.density = "compact";
}
setDensity(state.density, { persist: false });
applyWorkspacePreferences();
schedulePolling();
loadSavedViews();

refreshAll();
