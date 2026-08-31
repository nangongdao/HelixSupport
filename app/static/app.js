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
_helixModules.http?.configure?.({ baseHeaders: BASE_HEADERS });
_helixModules.summary?.configure?.({ els });
_helixModules.helpers?.configure?.({ els });
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
_helixModules.adminReportBridge?.configure?.({ api, showToast });
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
_helixModules.queueHelpers?.configure?.({
  state,
  els,
  api,
  queuePageSize,
});
_helixModules.queueFilters?.configure?.({
  state,
  els,
  refreshAll,
  escapeHtml,
});
_helixModules.shortcuts?.configure?.({
  state,
  els,
  refreshAll,
  selectConversation,
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
_helixModules.appNav?.configure?.({
  els,
  loadDesktopInfo,
  renderQualityPanel,
  loadQualityPanel,
  loadAdminView,
  loadKnowledgeView,
  scheduleIdle,
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
_helixModules.conversationDetail?.configure?.({
  state,
  els,
  apiWithHeaders,
  renderDetail,
  renderQueue,
  stopWatching,
  resetCopilot,
  hideMentionSuggest,
  windowedRowHeight,
  closeQueueDrawer,
  canWriteConversations,
  showToast,
  languageNames: LANGUAGE_NAMES,
  languageOptions: LANGUAGE_OPTIONS,
  threadPageLimit: THREAD_PAGE_LIMIT,
});
_helixModules.boot?.configure?.({
  state,
  els,
  document,
  window,
  selectConversation,
  handleQueueScroll,
  renderBulkToolbar,
  switchInspectorTab,
  setDensity,
  // nextDensity/normalizeDensity are const-destructured from the density
  // module further down this file — defer the reference so configure-time is
  // TDZ-safe (same reason the queueView block uses arrow functions).
  nextDensity: (level) => nextDensity(level),
  renderQueue,
  applyWorkspacePreferences,
  schedulePolling,
  showToast,
  refreshAll,
  renderInspector,
  applyMacroFromSuggest,
  insertCannedResponse,
  switchAppView,
  renderSummaries,
  renderWebhookEventCheckboxes,
  saveQuota,
  inviteMember,
  registerWebhook,
  loadAdminView,
  saveQuotaFromIsland,
  inviteMemberFromIsland,
  changeMemberRoleFromIsland,
  deactivateMemberFromIsland,
  registerWebhookFromIsland,
  deleteWebhookFromIsland,
  createSubscriptionFromIsland,
  toggleSubscriptionFromIsland,
  deleteSubscriptionFromIsland,
  generateReportFromIsland,
  saveSlaFromIsland,
  createRuleFromIsland,
  deleteRuleFromIsland,
  changeMemberRole,
  deactivateMember,
  deleteWebhook,
  renderMetrics,
  renderLoadingQueue,
  detectConstrainedDevice,
  normalizeDensity: (level) => normalizeDensity(level),
  loadSavedViews,
  PREF_LOW_PERF,
  PREF_INSPECTOR,
  PREF_DENSITY,
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

// The toast timer and the search debounce timer moved to js/helpers.js and
// js/queue-filters.js respectively.

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

// moved to js/http.js (request/api/apiWithHeaders — the request transport
// with the 15s timeout, FormData pass-through and X-Tenant-Id/Accept headers;
// baseHeaders arrive via configure). Thin wrappers keep every call site and
// every configure-injected module unchanged.
async function request(path, options = {}) {
  return window.HelixModules?.http?.request(...arguments);
}

async function api(path, options = {}) {
  return window.HelixModules?.http?.api(...arguments);
}

async function apiWithHeaders(path, options = {}) {
  return window.HelixModules?.http?.apiWithHeaders(...arguments);
}

// moved to js/helpers.js (escapeHtml/icon/statusLabel/roleLabel/formatTime/
// formatSla/showToast/scheduleIdle/setFormBusy/newIdempotencyKey — the shared
// UI/format helpers; els is injected via configure). Thin wrappers keep every
// call site and every configure-injected module unchanged. newIdempotencyKey
// was already dead in app.js (defined but never called/injected), so it is
// exported from the module without a wrapper here.
function escapeHtml(value) {
  return window.HelixModules?.helpers?.escapeHtml(...arguments);
}

function icon(name) {
  return window.HelixModules?.helpers?.icon(...arguments);
}

function statusLabel(status) {
  return window.HelixModules?.helpers?.statusLabel(...arguments);
}

function roleLabel(role) {
  return window.HelixModules?.helpers?.roleLabel(...arguments);
}

function formatTime(value, includeDate = false) {
  return window.HelixModules?.helpers?.formatTime(...arguments);
}

function formatSla(conversation) {
  return window.HelixModules?.helpers?.formatSla(...arguments);
}

function showToast(message, isError = false) {
  return window.HelixModules?.helpers?.showToast(...arguments);
}

function scheduleIdle(fn, timeoutMs = 2000) {
  return window.HelixModules?.helpers?.scheduleIdle(...arguments);
}

function setFormBusy(form, busy) {
  return window.HelixModules?.helpers?.setFormBusy(...arguments);
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

// moved to js/queue-filters.js (label filter options + the queue filter
// controls and debounced search box — bindQueueFilters).
function renderLabelFilter() {
  return window.HelixModules?.['queueFilters']?.['renderLabelFilter'](...arguments);
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

// moved to js/conversation-detail.js (renderSubtitle/renderLanguagePicker)
function renderSubtitle(conversation) {
  return window.HelixModules?.['conversationDetail']?.['renderSubtitle'](conversation);
}

// Backlog (多语言客服): header select lives in js/conversation-detail.js
// (renderLanguagePicker — injected once; PATCH rollback via renderDetail).
function renderLanguagePicker(conversation) {
  return window.HelixModules?.['conversationDetail']?.['renderLanguagePicker'](conversation);
}

// moved to js/detail.js (renderDetail — the conversation-detail assembly
// across header/actions/composer/thread/inspector/attachments).
function renderDetail(detail) {
  return window.HelixModules?.['detail']?.['renderDetail'](...arguments);
}




// moved to js/summary.js (renderSummaries — legacy banner paint + island
// model publish; summaryModel is the single source for both tracks).
function renderSummaries(detail) {
  return window.HelixModules?.summary?.renderSummaries(...arguments);
}

// A duplicate clearSelection shadowed the live one below (the later function
// declaration wins in a classic script); it was removed — selectConversation
// and refresh call the surviving copy that also stopWatching/resetCopilot.
// moved to js/conversation-detail.js (loadDetail — the detail lifecycle with
// the opaque X-Prev-Cursor transcript tail fetch).
async function loadDetail(id) {
  return window.HelixModules?.['conversationDetail']?.['loadDetail'](id);
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

// moved to js/conversation-detail.js (selectConversation/ensureSelectedRowVisible/
// clearSelection — the conversation switch reset + virtualized-row nudge).
async function selectConversation(id) {
  return window.HelixModules?.['conversationDetail']?.['selectConversation'](id);
}

function ensureSelectedRowVisible(id) {
  return window.HelixModules?.['conversationDetail']?.['ensureSelectedRowVisible'](id);
}

function clearSelection() {
  return window.HelixModules?.['conversationDetail']?.['clearSelection']();
}

function conversationQuery() {
  return window.HelixModules?.['queueHelpers']?.['conversationQuery']();
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

// The nav DOM lifecycle (setNavActive/showAppView/switchAppView/currentAppView)
// lives in js/app-nav.js. switchAppView stays as a thin wrapper — the palette
// command-dispatch module and the rail click listener below both call it.

function switchAppView(view) {
  return window.HelixModules?.['appNav']?.['switchAppView'](view);
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

// moved to js/admin-report-bridge.js (report subscriptions, report
// generation, SLA policies and routing-rule island bridges — api/toast
// lifecycle plus helix-admin-saved / helix-admin-report-generated receipts).
async function createSubscriptionFromIsland({ reportType, schedule, windowDays, webhookEndpointId } = {}) {
  return window.HelixModules?.['adminReportBridge']?.['createSubscriptionFromIsland'](...arguments);
}

async function toggleSubscriptionFromIsland({ id, active } = {}) {
  return window.HelixModules?.['adminReportBridge']?.['toggleSubscriptionFromIsland'](...arguments);
}

async function deleteSubscriptionFromIsland({ id } = {}) {
  return window.HelixModules?.['adminReportBridge']?.['deleteSubscriptionFromIsland'](...arguments);
}

async function generateReportFromIsland({ reportType, windowDays } = {}) {
  return window.HelixModules?.['adminReportBridge']?.['generateReportFromIsland'](...arguments);
}

async function saveSlaFromIsland({ priority, channel, firstResponseMinutes, resolveMinutes } = {}) {
  return window.HelixModules?.['adminReportBridge']?.['saveSlaFromIsland'](...arguments);
}

async function createRuleFromIsland({ intent, label, channel, groupId, priority } = {}) {
  return window.HelixModules?.['adminReportBridge']?.['createRuleFromIsland'](...arguments);
}

async function deleteRuleFromIsland({ id } = {}) {
  return window.HelixModules?.['adminReportBridge']?.['deleteRuleFromIsland'](...arguments);
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
  return window.HelixModules?.['queueHelpers']?.['loadLabelCatalog']({ force });
}

function queueSignature(conversations) {
  return window.HelixModules?.['queueHelpers']?.['queueSignature'](conversations);
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

// The legacy boot wiring (queue/inspector/density listeners, desktop island
// bridges, admin form bindings, initial render + preference hydration) now
// lives in js/boot.js — a single bindLegacyBoot() call (configure-injected).

// moved to js/queue-view.js (mobile queue drawer: scrim/focus-trap/inert)
function closeQueueDrawer(options = {}) {
  return window.HelixModules?.['queueView']?.['closeQueueDrawer'](...arguments);
}

window.HelixModules?.boot?.bindLegacyBoot();
