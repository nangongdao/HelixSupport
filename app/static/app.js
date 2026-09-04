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
  QUEUE_PAGE_SIZE_NORMAL,
  QUEUE_PAGE_SIZE_LOW,
  POLL_INTERVAL_NORMAL,
  POLL_INTERVAL_LOW,
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

function queuePageSize(...args) { return window.HelixModules?.['operatorSettings']?.['queuePageSize'](...args); }

function pollInterval(...args) { return window.HelixModules?.['operatorSettings']?.['pollInterval'](...args); }

function detectConstrainedDevice(...args) { return window.HelixModules?.['operatorSettings']?.['detectConstrainedDevice'](...args); }

function applyWorkspacePreferences(...args) { return window.HelixModules?.['operatorSettings']?.['applyWorkspacePreferences'](...args); }

function loadDraft(...args) { return window.HelixModules?.['composer']?.['loadDraft'](...args); }

function saveDraft(...args) { return window.HelixModules?.['composer']?.['saveDraft'](...args); }

function pruneExpiredDrafts(...args) { return window.HelixModules?.['composer']?.['pruneExpiredDrafts'](...args); }

function scheduleClaimRenewal(...args) { return window.HelixModules?.['composer']?.['scheduleClaimRenewal'](...args); }

function hideMacroSuggest(...args) { return window.HelixModules?.['composer']?.['hideMacroSuggest'](...args); }

function renderMacroSuggest(...args) { return window.HelixModules?.['composer']?.['renderMacroSuggest'](...args); }

function applyMacroFromSuggest(...args) { return window.HelixModules?.['composer']?.['applyMacroFromSuggest'](...args); }

// The toast timer and the search debounce timer moved to js/helpers.js and
// js/queue-filters.js respectively.

// @-mention autocomplete in the note composer: type @<prefix> and pick a
// colleague from the tenant roster (backlog M18 — orbiting the already-wired
// ``/api/mentions`` inbox with the missing input-side UX).
function hideMentionSuggest(...args) { return window.HelixModules?.['notes']?.['hideMentionSuggest'](...args); }

async function loadCollaborators(...args) { return window.HelixModules?.['notes']?.['loadCollaborators'](...args); }

// with the 15s timeout, FormData pass-through and X-Tenant-Id/Accept headers;
// baseHeaders arrive via configure). Thin wrappers keep every call site and
// every configure-injected module unchanged.
async function request(...args) { return window.HelixModules?.http?.request(...args); }

async function api(...args) { return window.HelixModules?.http?.api(...args); }

async function apiWithHeaders(...args) { return window.HelixModules?.http?.apiWithHeaders(...args); }

// formatSla/showToast/scheduleIdle/setFormBusy/newIdempotencyKey — the shared
// UI/format helpers; els is injected via configure). Thin wrappers keep every
// call site and every configure-injected module unchanged. newIdempotencyKey
// was already dead in app.js (defined but never called/injected), so it is
// exported from the module without a wrapper here.
function escapeHtml(...args) { return window.HelixModules?.helpers?.escapeHtml(...args); }

function icon(...args) { return window.HelixModules?.helpers?.icon(...args); }

function statusLabel(...args) { return window.HelixModules?.helpers?.statusLabel(...args); }

function roleLabel(...args) { return window.HelixModules?.helpers?.roleLabel(...args); }

function formatTime(...args) { return window.HelixModules?.helpers?.formatTime(...args); }

function formatSla(...args) { return window.HelixModules?.helpers?.formatSla(...args); }

function showToast(...args) { return window.HelixModules?.helpers?.showToast(...args); }

function scheduleIdle(...args) { return window.HelixModules?.helpers?.scheduleIdle(...args); }

function setFormBusy(...args) { return window.HelixModules?.helpers?.setFormBusy(...args); }

function renderMetrics(...args) { return window.HelixModules?.['refresh']?.['renderMetrics'](...args); }

function canOperate(...args) { return window.HelixModules?.['operatorSettings']?.['canOperate'](...args); }

// Backlog (多语言客服): language override + translate both require
// conversation:write (the console's write gate, distinct from canOperate's
// operator:act so channel/operator roles can still act).
function canWriteConversations(...args) { return window.HelixModules?.['operatorSettings']?.['canWriteConversations'](...args); }

// and apply — the whole saved-views domain lives with the CRUD lifecycle).
// Only the boot-time reload still needs a name in this scope.
async function loadSavedViews(...args) { return window.HelixModules?.['savedViews']?.['loadSavedViews'](...args); }

// controls and debounced search box — bindQueueFilters).
function renderLabelFilter(...args) { return window.HelixModules?.['queueFilters']?.['renderLabelFilter'](...args); }

function renderBulkToolbar(...args) { return window.HelixModules?.['queueView']?.['renderBulkToolbar'](...args); }

function renderLabelChips(...args) { return window.HelixModules?.['queueView']?.['renderLabelChips'](...args); }

// ROADMAP §18.4: build one queue row (shared by full and windowed modes).
function queueRowHtml(...args) { return window.HelixModules?.['queueView']?.['queueRowHtml'](...args); }

function windowedRowHeight(...args) { return window.HelixModules?.['queueView']?.['windowedRowHeight'](...args); }

function renderWindowedQueue(...args) { return window.HelixModules?.['queueView']?.['renderWindowedQueue'](...args); }

function renderQueue(...args) { return window.HelixModules?.['queueView']?.['renderQueue'](...args); }

function renderLoadingQueue(...args) { return window.HelixModules?.['queueView']?.['renderLoadingQueue'](...args); }

function handleQueueScroll(...args) { return window.HelixModules?.['queueView']?.['handleQueueScroll'](...args); }

function renderMessages(...args) { return window.HelixModules?.['thread']?.['renderMessages'](...args); }

async function submitFeedback(...args) { return window.HelixModules?.['thread']?.['submitFeedback'](...args); }

async function translateMessage(...args) { return window.HelixModules?.['thread']?.['translateMessage'](...args); }

function latestAssistant(...args) { return window.HelixModules?.['operatorSettings']?.['latestAssistant'](...args); }

async function updateLabels(...args) { return window.HelixModules?.['inspector']?.['updateLabels'](...args); }

async function updatePriority(...args) { return window.HelixModules?.['inspector']?.['updatePriority'](...args); }

function safeCitationUrl(value) {
  return window.HelixModules?.inspector?.safeCitationUrl
    ? window.HelixModules.inspector.safeCitationUrl(value)
    : "#";
}

function renderOverview(...args) { return window.HelixModules?.['inspector']?.['renderOverview'](...args); }

function renderEvidence(...args) { return window.HelixModules?.['inspector']?.['renderEvidence'](...args); }

function renderAudit(...args) { return window.HelixModules?.['inspector']?.['renderAudit'](...args); }

function resetInspectorRenderFlags(...args) { return window.HelixModules?.['inspector']?.['resetInspectorRenderFlags'](...args); }

function ensureInspectorTab(...args) { return window.HelixModules?.['inspector']?.['ensureInspectorTab'](...args); }

function renderInspector(...args) { return window.HelixModules?.['inspector']?.['renderInspector'](...args); }

function switchInspectorTab(...args) { return window.HelixModules?.['inspector']?.['switchInspectorTab'](...args); }

async function loadQualityPanel(...args) { return window.HelixModules?.['qualityPanel']?.['loadQualityPanel'](...args); }

// ROADMAP §17.3: build the native-SVG dashboard charts from the pure
// quality-charts module; empty string when the module is not loaded yet or
// there is not enough data to draw either chart.

function renderQualityPanel(...args) { return window.HelixModules?.['qualityPanel']?.['renderQualityPanel'](...args); }

function renderSubtitle(...args) { return window.HelixModules?.['conversationDetail']?.['renderSubtitle'](...args); }

// Backlog (多语言客服): header select lives in js/conversation-detail.js
// (renderLanguagePicker — injected once; PATCH rollback via renderDetail).
function renderLanguagePicker(...args) { return window.HelixModules?.['conversationDetail']?.['renderLanguagePicker'](...args); }

// across header/actions/composer/thread/inspector/attachments).
function renderDetail(...args) { return window.HelixModules?.['detail']?.['renderDetail'](...args); }

// model publish; summaryModel is the single source for both tracks).
function renderSummaries(...args) { return window.HelixModules?.summary?.renderSummaries(...args); }

// A duplicate clearSelection shadowed the live one below (the later function
// declaration wins in a classic script); it was removed — selectConversation
// and refresh call the surviving copy that also stopWatching/resetCopilot.
// the opaque X-Prev-Cursor transcript tail fetch).
async function loadDetail(...args) { return window.HelixModules?.['conversationDetail']?.['loadDetail'](...args); }

// ROADMAP §18.4: fetch the page of messages before the loaded tail and prepend
// them, keeping the view anchored at the message the operator was reading.
// The base64 opaque cursor is echoed straight back from the detail response's
// X-Prev-Cursor — never parsed or synthesized client-side.
async function loadOlderMessages(...args) { return window.HelixModules?.['thread']?.['loadOlderMessages'](...args); }

// ------------------------------------------------------------- 坐席协作
// Mentions inbox, internal discussion threads, and the supervisor live view.

function canReadConversations(...args) { return window.HelixModules?.['operatorSettings']?.['canReadConversations'](...args); }

async function loadMentions(...args) { return window.HelixModules?.['session']?.['loadMentions'](...args); }

function stopWatching(...args) { return window.HelixModules?.['session']?.['stopWatching'](...args); }

window.HelixModules?.session?.bindSession?.();

// clearSelection — the conversation switch reset + virtualized-row nudge).
async function selectConversation(...args) { return window.HelixModules?.['conversationDetail']?.['selectConversation'](...args); }

function ensureSelectedRowVisible(...args) { return window.HelixModules?.['conversationDetail']?.['ensureSelectedRowVisible'](...args); }

function clearSelection(...args) { return window.HelixModules?.['conversationDetail']?.['clearSelection'](...args); }

function conversationQuery(...args) { return window.HelixModules?.['queueHelpers']?.['conversationQuery'](...args); }

function renderCannedResponses(...args) { return window.HelixModules?.['composer']?.['renderCannedResponses'](...args); }

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

async function loadCannedResponses(...args) { return window.HelixModules?.['composer']?.['loadCannedResponses'](...args); }

function resetCopilot(...args) { return window.HelixModules?.['composer']?.['resetCopilot'](...args); }

async function enrichTicketBadge(...args) { return window.HelixModules?.['ticketView']?.['enrichTicketBadge'](...args); }

/** Hide the ticket detail, restoring the conversation/empty mount point. */

function attachmentChips(...args) { return window.HelixModules?.['attachments']?.['attachmentChips'](...args); }

async function loadAttachmentNames(...args) { return window.HelixModules?.['attachments']?.['loadAttachmentNames'](...args); }

function renderAttachmentBar(...args) { return window.HelixModules?.['attachments']?.['renderAttachmentBar'](...args); }

// ---- ROADMAP §17: 知识运营页 ---------------------------------------------
// bridges); the pure helpers stay in js/knowledge.js.

// D1 桌面设置页 + D3 知识/管理岛事件桥：委托给 js/desktop-info.js 模块。
function loadDesktopInfo() {
  window.HelixModules?.desktopInfo?.loadDesktopInfo(els);
}

async function loadKnowledgeView(...args) { return window.HelixModules?.['knowledgeView']?.['loadKnowledgeView'](...args); }

// ---- UI 升级 §17.1: 全局导航栏 -------------------------------------------

// The nav DOM lifecycle (setNavActive/showAppView/switchAppView/currentAppView)
// lives in js/app-nav.js. switchAppView stays as a thin wrapper — the palette
// command-dispatch module and the rail click listener below both call it.

function switchAppView(...args) { return window.HelixModules?.['appNav']?.['switchAppView'](...args); }

// ---- UI 升级 §17.1: 命令面板 (Ctrl+K) ------------------------------------

// js/command-dispatch.js imports buildStaticCommands/conversationCommand/
// filterCommands straight from js/commands.js and owns the whole legacy
// palette dialog (state, load, render, open/close, Ctrl+K — bindCommandPalette,
// runCommand). Nothing of §17.1 stays in this scope.

// ---- UI 升级 §17.2: 三档密度 (comfortable/compact/dense) --------------------

// ---- UI 升级 §17.2/§17.3: density/prefs/permissions live in js/operator-settings.js ----


// ---- UI 升级 §17.3: 管理页 (tenant quota / members / webhooks) ------------

// bridge handlers); thin wrappers keep the bridge table and legacy bindings.
function canManage(...args) { return window.HelixModules?.['adminActions']?.['canManage'](...args); }
async function loadCsatSummary(...args) { return window.HelixModules?.['qualityPanel']?.['loadCsatSummary'](...args); }
function loadAdminView(...args) { return window.HelixModules?.['adminActions']?.['loadAdminView'](...args); }
function renderWebhookEventCheckboxes(...args) { return window.HelixModules?.['adminActions']?.['renderWebhookEventCheckboxes'](...args); }
async function saveQuota(...args) { return window.HelixModules?.['adminActions']?.['saveQuota'](...args); }
async function inviteMember(...args) { return window.HelixModules?.['adminActions']?.['inviteMember'](...args); }
async function changeMemberRole(...args) { return window.HelixModules?.['adminActions']?.['changeMemberRole'](...args); }
async function deactivateMember(...args) { return window.HelixModules?.['adminActions']?.['deactivateMember'](...args); }
async function registerWebhook(...args) { return window.HelixModules?.['adminActions']?.['registerWebhook'](...args); }
async function deleteWebhook(...args) { return window.HelixModules?.['adminActions']?.['deleteWebhook'](...args); }
async function saveQuotaFromIsland(...args) { return window.HelixModules?.['adminActions']?.['saveQuotaFromIsland'](...args); }
async function inviteMemberFromIsland(...args) { return window.HelixModules?.['adminActions']?.['inviteMemberFromIsland'](...args); }
async function changeMemberRoleFromIsland(...args) { return window.HelixModules?.['adminActions']?.['changeMemberRoleFromIsland'](...args); }
async function deactivateMemberFromIsland(...args) { return window.HelixModules?.['adminActions']?.['deactivateMemberFromIsland'](...args); }
async function registerWebhookFromIsland(...args) { return window.HelixModules?.['adminActions']?.['registerWebhookFromIsland'](...args); }
async function deleteWebhookFromIsland(...args) { return window.HelixModules?.['adminActions']?.['deleteWebhookFromIsland'](...args); }

// generation, SLA policies and routing-rule island bridges — api/toast
// lifecycle plus helix-admin-saved / helix-admin-report-generated receipts).
async function createSubscriptionFromIsland(...args) { return window.HelixModules?.['adminReportBridge']?.['createSubscriptionFromIsland'](...args); }

async function toggleSubscriptionFromIsland(...args) { return window.HelixModules?.['adminReportBridge']?.['toggleSubscriptionFromIsland'](...args); }

async function deleteSubscriptionFromIsland(...args) { return window.HelixModules?.['adminReportBridge']?.['deleteSubscriptionFromIsland'](...args); }

async function generateReportFromIsland(...args) { return window.HelixModules?.['adminReportBridge']?.['generateReportFromIsland'](...args); }

async function saveSlaFromIsland(...args) { return window.HelixModules?.['adminReportBridge']?.['saveSlaFromIsland'](...args); }

async function createRuleFromIsland(...args) { return window.HelixModules?.['adminReportBridge']?.['createRuleFromIsland'](...args); }

async function deleteRuleFromIsland(...args) { return window.HelixModules?.['adminReportBridge']?.['deleteRuleFromIsland'](...args); }

function renderReportWebhookOptions(...args) { return window.HelixModules?.['adminReport']?.['renderReportWebhookOptions'](...args); }

async function loadReportSubscriptions(...args) { return window.HelixModules?.['adminReport']?.['loadReportSubscriptions'](...args); }

async function loadSlaPolicies(...args) { return window.HelixModules?.['adminReport']?.['loadSlaPolicies'](...args); }

async function loadRuleGroups(...args) { return window.HelixModules?.['adminReport']?.['loadRuleGroups'](...args); }

async function loadRoutingRules(...args) { return window.HelixModules?.['adminReport']?.['loadRoutingRules'](...args); }

function setDensity(...args) { return window.HelixModules?.['operatorSettings']?.['setDensity'](...args); }

function renderCopilot(...args) { return window.HelixModules?.['composer']?.['renderCopilot'](...args); }

async function loadLabelCatalog({ force = false } = {}) {
  return window.HelixModules?.['queueHelpers']?.['loadLabelCatalog']({ force });
}

function queueSignature(...args) { return window.HelixModules?.['queueHelpers']?.['queueSignature'](...args); }

// SSE/relay/polling stream lifecycle and tab visibility live there too).
async function refreshAll(...args) { return window.HelixModules?.['refresh']?.['refreshAll'](...args); }

function setLiveStatus(...args) { return window.HelixModules?.['refresh']?.['setLiveStatus'](...args); }

function schedulePolling(...args) { return window.HelixModules?.['refresh']?.['schedulePolling'](...args); }

// The legacy boot wiring (queue/inspector/density listeners, desktop island
// bridges, admin form bindings, initial render + preference hydration) now
// lives in js/boot.js — a single bindLegacyBoot() call (configure-injected).

function closeQueueDrawer(...args) { return window.HelixModules?.['queueView']?.['closeQueueDrawer'](...args); }

window.HelixModules?.boot?.bindLegacyBoot();
