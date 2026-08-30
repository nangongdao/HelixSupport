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

const ROLE_LABELS = {
  admin: "管理员",
  supervisor: "主管",
  operator: "客服",
  channel: "渠道",
  viewer: "只读",
  auditor: "审计员",
};


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
_helixModules.composerIslandBridge?.configure?.({
  state,
  els,
  canOperate,
});
_helixModules.drafts?.configure?.({ state });
_helixModules.composerIslandBridge?.bindIslandBridge?.();
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
_helixModules.thread?.configure?.({
  state,
  els,
  api,
  apiWithHeaders,
  showToast,
  escapeHtml,
  icon,
  formatTime,
  attachmentChips,
  canWriteConversations,
  languageNames: LANGUAGE_NAMES,
  languageOptions: LANGUAGE_OPTIONS,
  olderPageSize: THREAD_OLDER_PAGE_SIZE,
});
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
  roleLabels: ROLE_LABELS,
});
_helixModules.detail?.configure?.({
  state,
  els,
  canOperate,
  canReadConversations,
  escapeHtml,
  actions: {
    formatSla,
    statusLabel,
    renderSubtitle,
    renderLanguagePicker,
    renderCannedResponses,
    loadDraft,
    renderMessages,
    renderInspector,
    renderSummaries,
    renderCopilot,
    renderAttachmentBar,
    loadAttachmentNames,
    scheduleClaimRenewal,
    scheduleIdle,
    enrichTicketBadge,
    stopWatching,
  },
});
// The saved-views domain (filter snapshot, select render, list reload, apply
// and CRUD) now lives in js/saved-views.js.
_helixModules.savedViews?.configure?.({
  state,
  els,
  api,
  request,
  showToast,
  escapeHtml,
  refreshAll,
});
_helixModules.adminActions?.configure?.({
  state,
  els,
  api,
  showToast,
  escapeHtml,
  TENANT,
  roleLabels: ROLE_LABELS,
  canManage,
  actions: {
    renderReportWebhookOptions,
    loadReportSubscriptions,
    loadRuleGroups,
    loadSlaPolicies,
    loadRoutingRules,
    loadCsatSummary,
  },
});
_helixModules.refresh?.configure?.({
  state,
  els,
  api,
  apiWithHeaders,
  showToast,
  escapeHtml,
  TENANT,
  BASE_HEADERS,
  actions: {
    pollInterval,
    renderLoadingQueue,
    renderQueue,
    conversationQuery,
    queueSignature,
    loadLabelCatalog,
    pruneExpiredDrafts,
    scheduleIdle,
    loadMentions,
    loadCollaborators,
    loadCannedResponses,
    renderLabelFilter,
    loadDetail,
    selectConversation,
    clearSelection,
    roleLabel,
  },
});
_helixModules.queueActions?.configure?.({
  state,
  els,
  api,
  apiWithHeaders,
  showToast,
  setFormBusy,
  refreshAll,
  actions: { conversationQuery, renderQueue, renderBulkToolbar },
});
_helixModules.knowledgeView?.configure?.({
  state,
  els,
  api,
  showToast,
  setFormBusy,
  escapeHtml,
  formatTime,
  languageNames: LANGUAGE_NAMES,
});
_helixModules.notes?.configure?.({
  state,
  els,
  api,
  canOperate,
  setFormBusy,
  escapeHtml,
  roleLabels: ROLE_LABELS,
});
_helixModules.commandDispatch?.configure?.({
  state,
  els,
  api,
  showToast,
  escapeHtml,
  switchAppView,
  refreshAll,
  actions: { loadDetail },
});
_helixModules.conversationActions?.configure?.({
  state,
  els,
  api,
  showToast,
  setFormBusy,
  loadDetail,
  refreshAll,
  renderSubtitle,
  renderLanguagePicker,
  scheduleIdle,
  languageNames: LANGUAGE_NAMES,
  actions: { renderQueue },
});
window.HelixModules?.conversationActions?.bindConversationDialog?.();

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
// moved to js/notes.js (mention suggest, collaborators, note composer)
function hideMentionSuggest() {
  return window.HelixModules?.['notes']?.['hideMentionSuggest'](...arguments);
}

async function loadCollaborators() {
  return window.HelixModules?.['notes']?.['loadCollaborators'](...arguments);
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

// moved to js/refresh.js (metric tiles painter)
function renderMetrics(data) {
  return window.HelixModules?.['refresh']?.['renderMetrics'](...arguments);
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

// moved to js/saved-views.js (filter snapshot, select render, list reload
// and apply — the whole saved-views domain lives with the CRUD lifecycle).
// Only the boot-time reload still needs a name in this scope.
async function loadSavedViews() {
  return window.HelixModules?.['savedViews']?.['loadSavedViews'](...arguments);
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

// moved to js/thread.js (renderMessages — legacy transcript paint + island snapshot publish)
function renderMessages(messages, { preserveAnchor = false } = {}) {
  return window.HelixModules?.['thread']?.['renderMessages'](...arguments);
}

// moved to js/thread.js (submitFeedback + recordFeedback shared core)
async function submitFeedback(button) {
  return window.HelixModules?.['thread']?.['submitFeedback'](...arguments);
}

// moved to js/thread.js (translateMessage + requestTranslation/buildTranslateResultHtml)
async function translateMessage(button) {
  return window.HelixModules?.['thread']?.['translateMessage'](...arguments);
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

// moved to js/detail.js (renderDetail — the conversation-detail assembly
// across header/actions/composer/thread/inspector/attachments).
function renderDetail(detail) {
  return window.HelixModules?.['detail']?.['renderDetail'](...arguments);
}




function renderSummaries(detail) {
  // Single model source (js/summary.js): the legacy banner paints from it in
  // a plain browser tab; island mode publishes it to the summary island.
  const model = window.HelixModules?.summary?.summaryModel?.(detail.summaries) || {
    visible: false,
    title: "",
    text: "",
  };
  if (window.__HELIX_ISLAND_MODE__) {
    window.dispatchEvent(new CustomEvent(window.HelixModules?.summary?.SUMMARY_EVENT || "helix-summary-state", { detail: model }));
    return;
  }
  els.summaryBanner.hidden = !model.visible;
  if (!model.visible) return;
  els.summaryTitle.textContent = model.title;
  els.summaryText.textContent = model.text;
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
// moved to js/thread.js (loadOlderMessages — §18.4 upward keyset pagination)
async function loadOlderMessages() {
  return window.HelixModules?.['thread']?.['loadOlderMessages'](...arguments);
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
  // Island mode: the thread island renders the loading state from a
  // helix-thread-state snapshot; the legacy container is hidden.
  if (window.__HELIX_ISLAND_MODE__) {
    window.HelixModules?.thread?.publishThreadState?.({ loading: true });
  } else {
    els.messages.innerHTML = '<div class="thread-empty">正在加载会话</div>';
  }
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

// moved to js/composer.js (renderCannedResponses — island republish +
function renderCannedResponses() {
  return window.HelixModules?.['composer']?.['renderCannedResponses'](...arguments);
}

// legacy chip paint); insertCannedResponse stays with its binding.

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
// moved to js/knowledge-view.js (knowledge lifecycle + listeners + island
// bridges); the pure helpers stay in js/knowledge.js.

// D1 桌面设置页 + D3 知识/管理岛事件桥：委托给 js/desktop-info.js 模块。
function loadDesktopInfo() {
  window.HelixModules?.desktopInfo?.loadDesktopInfo(els);
}

// moved to js/knowledge-view.js (loadKnowledgeView — dual-track fetch/cache)
async function loadKnowledgeView({ force = false } = {}) {
  return window.HelixModules?.['knowledgeView']?.['loadKnowledgeView'](...arguments);
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
    // Island mode: the quality island owns the buckets (yieldsLegacy) and
    // fetches via react-query — hand the refresh over instead of fetching
    // into the hidden legacy containers (unforced refresh reuses the
    // island's 10s throttle; the header refresh button sends force).
    if (window.__HELIX_ISLAND_MODE__) {
      window.dispatchEvent(new CustomEvent("helix-quality-refresh", { detail: { force: false } }));
    } else {
      // Reuse the Phase 21 aggregates; the parametrized renderer fills the
      // standalone view containers. The fresh fetch is non-critical — the
      // cached buckets render immediately, so it is scheduled for idle time.
      renderQualityPanel(els.qualityViewBuckets, els.qualityViewGaps);
      scheduleIdle(() => loadQualityPanel());
    }
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

// js/command-dispatch.js imports buildStaticCommands/conversationCommand/
// filterCommands straight from js/commands.js and owns the whole legacy
// palette dialog (state, load, render, open/close, Ctrl+K — bindCommandPalette,
// runCommand). Nothing of §17.1 stays in this scope.


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

// moved to js/admin-actions.js (quota/member/webhook CRUD + render + island
// bridge handlers); thin wrappers keep the bridge table and legacy bindings.
function canManage() {
  return window.HelixModules?.['adminActions']?.['canManage'](...arguments);
}
async function loadCsatSummary() {
  return window.HelixModules?.['qualityPanel']?.['loadCsatSummary'](...arguments);
}
function loadAdminView() {
  return window.HelixModules?.['adminActions']?.['loadAdminView'](...arguments);
}
function renderWebhookEventCheckboxes() {
  return window.HelixModules?.['adminActions']?.['renderWebhookEventCheckboxes'](...arguments);
}
async function saveQuota(event) {
  return window.HelixModules?.['adminActions']?.['saveQuota'](...arguments);
}
async function inviteMember(event) {
  return window.HelixModules?.['adminActions']?.['inviteMember'](...arguments);
}
async function changeMemberRole(actorId, role) {
  return window.HelixModules?.['adminActions']?.['changeMemberRole'](...arguments);
}
async function deactivateMember(actorId) {
  return window.HelixModules?.['adminActions']?.['deactivateMember'](...arguments);
}
async function registerWebhook(event) {
  return window.HelixModules?.['adminActions']?.['registerWebhook'](...arguments);
}
async function deleteWebhook(id) {
  return window.HelixModules?.['adminActions']?.['deleteWebhook'](...arguments);
}
async function saveQuotaFromIsland(detail) {
  return window.HelixModules?.['adminActions']?.['saveQuotaFromIsland'](detail);
}
async function inviteMemberFromIsland(detail) {
  return window.HelixModules?.['adminActions']?.['inviteMemberFromIsland'](detail);
}
async function changeMemberRoleFromIsland(detail) {
  return window.HelixModules?.['adminActions']?.['changeMemberRoleFromIsland'](detail);
}
async function deactivateMemberFromIsland(detail) {
  return window.HelixModules?.['adminActions']?.['deactivateMemberFromIsland'](detail);
}
async function registerWebhookFromIsland(detail) {
  return window.HelixModules?.['adminActions']?.['registerWebhookFromIsland'](detail);
}
async function deleteWebhookFromIsland(detail) {
  return window.HelixModules?.['adminActions']?.['deleteWebhookFromIsland'](detail);
}

async function createSubscriptionFromIsland({ reportType, schedule, windowDays, webhookEndpointId } = {}) {
  if (!webhookEndpointId) {
    showToast("请先选择 Webhook 端点", true);
    return;
  }
  let ok = false;
  try {
    await api("/api/admin/report-subscriptions", {
      method: "POST",
      body: JSON.stringify({
        report_type: reportType || "quality",
        schedule: schedule || "daily",
        window_days: Number(windowDays || 7),
        webhook_endpoint_id: webhookEndpointId,
      }),
    });
    ok = true;
    showToast("报表订阅已创建");
  } catch (error) {
    showToast(`创建订阅失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["subscriptions"]);
  }
}

async function toggleSubscriptionFromIsland({ id, active } = {}) {
  if (!id) return;
  let ok = false;
  try {
    await api(`/api/admin/report-subscriptions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ active: Boolean(active) }),
    });
    ok = true;
  } catch (error) {
    showToast(`订阅状态变更失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["subscriptions"]);
  }
}

async function deleteSubscriptionFromIsland({ id } = {}) {
  if (!id) return;
  let ok = false;
  try {
    await api(`/api/admin/report-subscriptions/${encodeURIComponent(id)}`, { method: "DELETE" });
    ok = true;
    showToast("订阅已删除");
  } catch (error) {
    showToast(`删除订阅失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["subscriptions"]);
  }
}

async function generateReportFromIsland({ reportType, windowDays } = {}) {
  const type = reportType || "quality";
  try {
    const report = await api("/api/admin/reports/generate", {
      method: "POST",
      body: JSON.stringify({ report_type: type, window_days: Number(windowDays || 7) }),
    });
    // 预览文案与 js/admin-report.js generateReportPreview 逐字一致。
    const rows = Array.isArray(report.rows) ? report.rows : [];
    const header = `${ADMIN_REPORT_TYPE_LABELS[type] || type} ${report.from_date} → ${report.to_date}：${rows.length} 行`;
    const text = rows.length
      ? `${header}\n${rows.slice(0, 5).map((row) => JSON.stringify(row)).join("\n")}`
      : `${header}\n（窗口内暂无数据）`;
    window.dispatchEvent(new CustomEvent("helix-admin-report-generated", { detail: { ok: true, text } }));
  } catch (error) {
    showToast(`报表生成失败：${error.message || error}`, true);
    window.dispatchEvent(new CustomEvent("helix-admin-report-generated", { detail: { ok: false } }));
  }
}

async function saveSlaFromIsland({ priority, channel, firstResponseMinutes, resolveMinutes } = {}) {
  const firstResponse = Number(firstResponseMinutes || 0);
  const resolve = Number(resolveMinutes || 0);
  if (!firstResponse || !resolve) {
    showToast("请填写首响与解决时限", true);
    return;
  }
  let ok = false;
  try {
    await api("/api/admin/sla-policies", {
      method: "PUT",
      body: JSON.stringify({
        priority: priority || null,
        channel: (channel || "").trim() || null,
        first_response_minutes: firstResponse,
        resolve_minutes: resolve,
      }),
    });
    ok = true;
    showToast("SLA 策略已保存");
  } catch (error) {
    showToast(`SLA 策略保存失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["sla"]);
  }
}

async function createRuleFromIsland({ intent, label, channel, groupId, priority } = {}) {
  if (!groupId) {
    showToast("请先选择分配组", true);
    return;
  }
  const body = { group_id: groupId, priority: Number(priority || 0) };
  for (const [key, value] of [["intent", intent], ["label", label], ["channel", channel]]) {
    const trimmed = (value || "").trim();
    if (trimmed) body[key] = trimmed;
  }
  let ok = false;
  try {
    await api("/api/admin/routing-rules", { method: "POST", body: JSON.stringify(body) });
    ok = true;
    showToast("路由规则已添加");
  } catch (error) {
    showToast(`路由规则添加失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["rules"]);
  }
}

async function deleteRuleFromIsland({ id } = {}) {
  if (!id) return;
  let ok = false;
  try {
    await api(`/api/admin/routing-rules/${encodeURIComponent(id)}`, { method: "DELETE" });
    ok = true;
    showToast("路由规则已删除");
  } catch (error) {
    showToast(`路由规则删除失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["rules"]);
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

// moved to js/refresh.js (refreshAll dedup + runRefresh fan-out; the
// SSE/relay/polling stream lifecycle and tab visibility live there too).
async function refreshAll({ silent = false, refreshDetail = true, background = false } = {}) {
  return window.HelixModules?.['refresh']?.['refreshAll'](...arguments);
}

function setLiveStatus(mode) {
  return window.HelixModules?.['refresh']?.['setLiveStatus'](...arguments);
}

function schedulePolling() {
  return window.HelixModules?.['refresh']?.['schedulePolling'](...arguments);
}

// moved to js/queue-actions.js (loadMoreConversations + applyBulkAction)

els.list.addEventListener("click", (event) => {
  const item = event.target.closest(".conversation-item");
  if (item?.dataset.id) selectConversation(item.dataset.id);
});

// ROADMAP §18.4: virtual-mode scroll keeps the windowed render aligned.
els.list.addEventListener("scroll", handleQueueScroll, { passive: true });

// The thread's feedback/translate click routing moved to js/thread.js
// (bindThread), which also owns the island bridges.

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

// moved to js/conversation-actions.js (new-conversation dialog lifecycle:
// open/close/createConversation + legacy form binding + island bridge).

els.refreshList.addEventListener("click", () => refreshAll());
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
/**
 * Saved-view data lifecycle — shared by the legacy controls and the
 * island's helix-saved-views-save/-delete bridges. Returns the created view
 * id on save so the bridge can tell the island which entry to select.
 */
// moved to js/saved-views.js (saved-view CRUD + legacy bindings + island
// bridges, plus the filter snapshot / select render / reload / apply).

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
// The applyBulk click binding (with its no-MouseEvent-leak guard) moved to
// js/queue-actions.js bindQueueActions.

els.clearBulk.addEventListener("click", () => {
  state.bulkSelected.clear();
  renderQueue();
});
els.searchInput.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  const delay = state.lowPerf ? 450 : 260;
  searchTimer = window.setTimeout(() => refreshAll({ silent: true, refreshDetail: false }), delay);
});
// moved to js/conversation-actions.js (new-conversation dialog lifecycle)
async function createConversation(payload) {
  return window.HelixModules?.['conversationActions']?.['createConversation'](...arguments);
}

// moved to js/queue-view.js (mobile queue drawer: scrim/focus-trap/inert)
function closeQueueDrawer(options = {}) {
  return window.HelixModules?.['queueView']?.['closeQueueDrawer'](...arguments);
}

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

// Tab lifecycle (unhandledrejection/visibilitychange) moved to js/refresh.js bindRefresh.

if (els.macroSuggest) {
  els.macroSuggest.addEventListener("click", (event) => {
    const button = event.target.closest(".macro-option");
    if (button?.dataset.macroId) applyMacroFromSuggest(button.dataset.macroId);
  });
}
if (els.cannedList) {
  els.cannedList.addEventListener("click", (event) => {
    const button = event.target.closest(".canned-chip");
    if (button?.dataset.macroId) insertCannedResponse(button.dataset.macroId);
  });
}
window.HelixModules?.qualityPanel?.bindQuality?.();
window.HelixModules?.knowledgeView?.bindKnowledgeView?.();
window.HelixModules?.queueView?.bindQueueDrawer?.();
window.HelixModules?.savedViews?.bindSavedViews?.();
window.HelixModules?.commandDispatch?.bindCommandDispatch?.();
window.HelixModules?.refresh?.bindRefresh?.();
window.HelixModules?.queueActions?.bindQueueActions?.();
window.HelixModules?.conversationActions?.bindConversationActions?.();
window.HelixModules?.notes?.bindNotes?.();
window.HelixModules?.thread?.bindThread?.();
window.HelixModules?.composer?.bindComposer?.();
// §17.1 palette: the shell island owns Ctrl+K (__HELIX_ISLAND_MODE__) so the
// legacy <dialog> never stacks on top of it; in a browser tab the flag is
// never set and js/command-dispatch.js bindCommandPalette is the handler.
window.HelixModules?.commandDispatch?.bindCommandPalette?.();

window.HelixModules?.ticketView?.bindTickets?.();
window.HelixModules?.attachments?.bindAttachments?.();
// UI 升级 §17.1: 全局导航栏 — view switching + quality refresh.
if (els.appNav) {
  els.appNav.addEventListener("click", (event) => {
    const button = event.target.closest(".nav-item");
    if (button?.dataset.view) switchAppView(button.dataset.view);
  });
}

// moved to js/notes.js (note composer listeners) and
// js/composer-island-bridge.js (operator draft/macro typing).
// Knowledge page listeners + knowledge-island bridges moved to
// js/knowledge-view.js (bindKnowledgeView — bound at boot below).
// D3 bridge: the React ticket island dispatches "helix-ticket-open" when a
// row is clicked (the legacy #ticketList is yielded and hidden in the
// desktop shell). Bridge it to the legacy detail opener so the ticket detail
// view stays in ticket-view.js until a later D3 slice migrates it.
window.addEventListener("helix-ticket-open", (event) => {
  const { ticketId } = event.detail || {};
  if (!ticketId) return;
  void window.HelixModules?.ticketView?.openTicketDetail?.(ticketId);
});
// D3 bridge: the React queue island dispatches "helix-queue-select" when a
// row is clicked (the legacy #conversationList is yielded and hidden in the
// desktop shell). Bridge it back to the legacy detail loader, which owns the
// conversation thread view until a later D3 slice migrates it.
window.addEventListener("helix-queue-select", (event) => {
  const { id } = event.detail || {};
  if (!id) return;
  void selectConversation(id);
});
// D3 bridge (workspace tabs island): the island dispatches
// helix-workspace-tab on clicks; pane switching and the data side effects
// (ticket loading, queue refresh) stay in legacy switchWorkspaceTab.
window.addEventListener("helix-workspace-tab", (event) => {
  const { field } = event.detail || {};
  if (!["queue", "tickets"].includes(field)) return;
  window.HelixModules?.ticketView?.switchWorkspaceTab?.(field);
});
// D3 bridge (mentions island): the island owns the badge/panel; jumping to
// a mentioned conversation and the mark-read POST+toast stay legacy.
window.addEventListener("helix-mentions-open-jump", async (event) => {
  const { conversationId } = event.detail || {};
  if (conversationId) await selectConversation(conversationId);
});
window.addEventListener("helix-mentions-mark-read", async (event) => {
  const { id } = event.detail || {};
  if (id) await window.HelixModules?.session?.markMentionRead?.(id);
});
// D3 bridge (summary island): republish the current banner model on request.
window.addEventListener("helix-summary-sync", () => {
  if (window.__HELIX_ISLAND_MODE__ && state.detail) renderSummaries(state.detail);
});
// D3 bridge (command palette island): the palette island dispatches
// helix-command {id} for every executed command. This consumer was missing
// since the palette was island-activated, so desktop commands were inert;
// each id maps onto the same handlers the legacy surfaces use.
// moved to js/command-dispatch.js (checkBackendHealth + the palette island's
// helix-command consumer; switchAppView/refreshAll arrive via configure).

// D3 bridge: on mount the React queue island asks for the current queue
// snapshot (helix-conversations-sync); re-render in island mode so the
// freshly mounted island receives the latest list via renderQueue().
window.addEventListener("helix-conversations-sync", () => {
  if (!window.__HELIX_ISLAND_MODE__) return;
  void window.HelixModules?.queueView?.renderQueue?.();
});
// UI 升级 §17.3: 管理页 — forms, member actions, webhook delete, refresh.
renderWebhookEventCheckboxes();
if (els.quotaForm) els.quotaForm.addEventListener("submit", (event) => void saveQuota(event));
if (els.memberForm) els.memberForm.addEventListener("submit", (event) => void inviteMember(event));
if (els.webhookForm) els.webhookForm.addEventListener("submit", (event) => void registerWebhook(event));
window.HelixModules?.adminReport?.bindAdminReports?.();
if (els.refreshAdmin) {
  els.refreshAdmin.addEventListener("click", () => {
    // The header button is not yielded (it sits outside the island mount),
    // so in island mode it drives the island's queries directly instead of
    // the legacy fetch chain.
    if (window.__HELIX_ISLAND_MODE__) {
      window.dispatchEvent(new CustomEvent("helix-admin-refresh", { detail: { force: true } }));
      return;
    }
    void loadAdminView();
  });
}
// D3 bridge (admin island): the React admin island dispatches helix-admin-*
// write events with form payloads; the bridges above own the api()/toast
// lifecycle and answer with helix-admin-saved for the island's refetch.
for (const [eventType, handler] of [
  ["helix-admin-save-quota", saveQuotaFromIsland],
  ["helix-admin-invite-member", inviteMemberFromIsland],
  ["helix-admin-member-role", changeMemberRoleFromIsland],
  ["helix-admin-member-deactivate", deactivateMemberFromIsland],
  ["helix-admin-register-webhook", registerWebhookFromIsland],
  ["helix-admin-delete-webhook", deleteWebhookFromIsland],
  ["helix-admin-create-subscription", createSubscriptionFromIsland],
  ["helix-admin-toggle-subscription", toggleSubscriptionFromIsland],
  ["helix-admin-delete-subscription", deleteSubscriptionFromIsland],
  ["helix-admin-generate-report", generateReportFromIsland],
  ["helix-admin-save-sla", saveSlaFromIsland],
  ["helix-admin-create-rule", createRuleFromIsland],
  ["helix-admin-delete-rule", deleteRuleFromIsland],
]) {
  window.addEventListener(eventType, (event) => void handler(event.detail || {}));
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
