// ---- legacy app.js facade ----------------------------------------------
// The console constants (page sizes/poll intervals/pref keys/language and
// role label maps), the mutable state object, and the els DOM lookup map
// now live in js/app-core.js (registered on HelixModules.appCore). Read
// them back here so every name stays in scope — the same live object
// references the extracted js/ domain modules receive through configure().
const {
  TENANT,
  BASE_HEADERS,
  QUEUE_PAGE_SIZE_NORMAL,
  QUEUE_PAGE_SIZE_LOW,
  THREAD_PAGE_LIMIT,
  THREAD_OLDER_PAGE_SIZE,
  POLL_INTERVAL_NORMAL,
  POLL_INTERVAL_LOW,
  PREF_DENSITY,
  PREF_LOW_PERF,
  PREF_INSPECTOR,
  LANGUAGE_NAMES,
  LANGUAGE_OPTIONS,
  ROLE_LABELS,
  state,
  els,
} = window.HelixModules.appCore;


// ROADMAP §41.6 (ARC-001): the extracted js/ modules read the legacy app.js
// singletons through configure(); bindX() calls wire the DOM they own.
// ---- legacy wiring -------------------------------------------------------
// All 26 configure() calls for the extracted js/ domain modules now live in
// js/wire.js (wireModules). We build one flat bundle of the app.js-scoped
// names and hand it over; each module reads only the keys it needs via its
// configure(). vqueue/density accessors are read live from HelixModules so
// the late consts are never touched at bundle-build time. bindConversationDialog
// stays here (it is a one-time activation, not a wiring dependency).
const wiring = {
  window,
  document,
  state,
  els,
  // app-core constants
  TENANT,
  BASE_HEADERS,
  ROLE_LABELS,
  LANGUAGE_NAMES,
  LANGUAGE_OPTIONS,
  THREAD_PAGE_LIMIT,
  THREAD_OLDER_PAGE_SIZE,
  PREF_LOW_PERF,
  PREF_INSPECTOR,
  PREF_DENSITY,
  // transport
  request,
  api,
  apiWithHeaders,
  // helper wrappers (app-core + helpers module)
  escapeHtml,
  icon,
  statusLabel,
  roleLabel,
  formatTime,
  formatSla,
  showToast,
  scheduleIdle,
  setFormBusy,
  // queue / view helpers
  queuePageSize,
  pollInterval,
  handleQueueScroll,
  renderQueue,
  renderBulkToolbar,
  renderLoadingQueue,
  renderMetrics,
  conversationQuery,
  queueSignature,
  loadLabelCatalog,
  renderLabelFilter,
  // permission gates
  canOperate,
  canWriteConversations,
  canReadConversations,
  canManage,
  // conversation lifecycle
  loadDetail,
  selectConversation,
  clearSelection,
  renderSubtitle,
  renderLanguagePicker,
  renderDetail,
  renderSummaries,
  renderMessages,
  renderInspector,
  renderCannedResponses,
  renderAttachmentBar,
  renderCopilot,
  resetCopilot,
  hideMentionSuggest,
  stopWatching,
  windowedRowHeight,
  closeQueueDrawer,
  latestAssistant,
  insertCannedResponse,
  loadCannedResponses,
  loadAttachmentNames,
  enrichTicketBadge,
  scheduleClaimRenewal,
  pruneExpiredDrafts,
  loadDraft,
  loadMentions,
  loadCollaborators,
  switchAppView,
  renderLabelChips,
  // density / prefs
  setDensity,
  detectConstrainedDevice,
  applyWorkspacePreferences,
  schedulePolling,
  refreshAll,
  loadSavedViews,
  // admin
  loadAdminView,
  loadQualityPanel,
  renderQualityPanel,
  loadKnowledgeView,
  loadDesktopInfo,
  renderWebhookEventCheckboxes,
  saveQuota,
  inviteMember,
  registerWebhook,
  changeMemberRole,
  deactivateMember,
  deleteWebhook,
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
  applyMacroFromSuggest,
  switchInspectorTab,
  renderReportWebhookOptions,
  loadReportSubscriptions,
  loadRuleGroups,
  loadSlaPolicies,
  loadRoutingRules,
  loadCsatSummary,
  // live module namespaces for deferred helper access (avoid TDZ on consts)
  vqueue: window.HelixModules?.vqueue || undefined,
  density: window.HelixModules?.density || undefined,
};

window.HelixModules?.wire?.wireModules?.(wiring);

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
