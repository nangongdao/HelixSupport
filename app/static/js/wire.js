/**
 * Helix Support — legacy wiring (app.js <500 campaign facade).
 *
 * Routes the single legacy app.js bundle to every extracted js/ domain module
 * through their configure(). app.js builds one flat `bundle` object of its
 * in-scope names and calls wiring.wireModules(bundle) once; each module reads
 * only the keys it needs via configure(), so a superset bundle is safe (the
 * modules store the whole deps object and touch only nested state/els/actions
 * keys — never top-level presence).
 *
 * The nested `actions` maps, the vqueue/density helpers and the density
 * deferred accessors are all assembled here from the flat bundle so app.js
 * stays a compact key list. vqueue/density are read from HelixModules (always
 * loaded by main.js before app.js runs); the lazy `(module || fallback)`
 * pattern mirrors the legacy queueView/boot configure blocks so a missing
 * module cannot throw at wiring time.
 */

function withVqueue(b, fn) {
  const vq = b.vqueue || { estimatedRowHeight: () => 118, signatureOf: (c) => `${c.id}:${c.status}:${c.updated_at}:${c.version ?? 0}`, rowHeightFromElement: () => 118 };
  return fn(vq);
}
function withDensity(b, fn) {
  const d = b.density || { normalizeDensity: (v) => v, nextDensity: () => v, effectiveDensity: (l, lp) => lp, isCompactDensity: (l, lp) => !!lp };
  return fn(d);
}

function configureModules(b) {
  const H = () => (typeof window !== "undefined" ? window.HelixModules : null);
  const m = (name) => H()?.[name];

  m("http")?.configure?.({ baseHeaders: b.BASE_HEADERS });
  m("summary")?.configure?.({ els: b.els });
  m("helpers")?.configure?.({ els: b.els });
  m("operatorSettings")?.configure?.({
    state: b.state,
    els: b.els,
    QUEUE_PAGE_SIZE_NORMAL: b.QUEUE_PAGE_SIZE_NORMAL,
    QUEUE_PAGE_SIZE_LOW: b.QUEUE_PAGE_SIZE_LOW,
    POLL_INTERVAL_NORMAL: b.POLL_INTERVAL_NORMAL,
    POLL_INTERVAL_LOW: b.POLL_INTERVAL_LOW,
    PREF_DENSITY: b.PREF_DENSITY,
  });
  m("composer")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    loadDetail: b.loadDetail,
    refreshAll: b.refreshAll,
    canOperate: b.canOperate,
    setFormBusy: b.setFormBusy,
    showToast: b.showToast,
    escapeHtml: b.escapeHtml,
  });
  m("composerIslandBridge")?.configure?.({ state: b.state, els: b.els, canOperate: b.canOperate });
  m("drafts")?.configure?.({ state: b.state });
  m("composerIslandBridge")?.bindIslandBridge?.();
  m("session")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    baseHeaders: b.BASE_HEADERS,
    loadDetail: b.loadDetail,
    selectConversation: b.selectConversation,
    showToast: b.showToast,
    escapeHtml: b.escapeHtml,
    formatTime: b.formatTime,
    icon: b.icon,
  });
  m("adminReport")?.configure?.({ state: b.state, els: b.els, api: b.api, showToast: b.showToast, escapeHtml: b.escapeHtml, formatTime: b.formatTime });
  m("adminReportBridge")?.configure?.({ api: b.api, showToast: b.showToast });
  m("governanceBridge")?.configure?.({ api: b.api, showToast: b.showToast });
  m("ticketView")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    loadDetail: b.loadDetail,
    selectConversation: b.selectConversation,
    refreshAll: b.refreshAll,
    canOperate: b.canOperate,
    showToast: b.showToast,
    escapeHtml: b.escapeHtml,
    formatTime: b.formatTime,
    statusLabel: b.statusLabel,
  });
  m("qualityPanel")?.configure?.({ state: b.state, els: b.els, api: b.api, showToast: b.showToast, escapeHtml: b.escapeHtml });
  m("thread")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    apiWithHeaders: b.apiWithHeaders,
    showToast: b.showToast,
    escapeHtml: b.escapeHtml,
    icon: b.icon,
    formatTime: b.formatTime,
    attachmentChips: b.attachmentChips,
    canWriteConversations: b.canWriteConversations,
    languageNames: b.LANGUAGE_NAMES,
    languageOptions: b.LANGUAGE_OPTIONS,
    olderPageSize: b.THREAD_OLDER_PAGE_SIZE,
  });
  m("attachments")?.configure?.({ state: b.state, els: b.els, api: b.api, showToast: b.showToast, escapeHtml: b.escapeHtml, icon: b.icon, canOperate: b.canOperate });
  m("queueView")?.configure?.(withVqueue(b, (vq) => ({
    state: b.state,
    els: b.els,
    canOperate: b.canOperate,
    escapeHtml: b.escapeHtml,
    statusLabel: b.statusLabel,
    formatSla: b.formatSla,
    estimatedRowHeight: (density, lowPerf) => vq.estimatedRowHeight(density, lowPerf),
    signatureOf: (c) => vq.signatureOf(c),
    rowHeightFromElement: (el) => vq.rowHeightFromElement(el),
    isCompactDensity: (level, lowPerf) => withDensity(b, (d) => d.isCompactDensity(level, lowPerf)),
    VIRTUAL_THRESHOLD: 200,
  })));
  m("inspector")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    loadDetail: b.loadDetail,
    refreshAll: b.refreshAll,
    loadQualityPanel: b.loadQualityPanel,
    canOperate: b.canOperate,
    setFormBusy: b.setFormBusy,
    showToast: b.showToast,
    escapeHtml: b.escapeHtml,
    formatTime: b.formatTime,
    latestAssistant: b.latestAssistant,
    renderLabelChips: b.renderLabelChips,
    roleLabels: b.ROLE_LABELS,
  });
  m("detail")?.configure?.({
    state: b.state,
    els: b.els,
    canOperate: b.canOperate,
    canReadConversations: b.canReadConversations,
    escapeHtml: b.escapeHtml,
    actions: {
      formatSla: b.formatSla,
      statusLabel: b.statusLabel,
      renderSubtitle: b.renderSubtitle,
      renderLanguagePicker: b.renderLanguagePicker,
      renderCannedResponses: b.renderCannedResponses,
      loadDraft: b.loadDraft,
      renderMessages: b.renderMessages,
      renderInspector: b.renderInspector,
      renderSummaries: b.renderSummaries,
      renderCopilot: b.renderCopilot,
      renderAttachmentBar: b.renderAttachmentBar,
      loadAttachmentNames: b.loadAttachmentNames,
      scheduleClaimRenewal: b.scheduleClaimRenewal,
      scheduleIdle: b.scheduleIdle,
      enrichTicketBadge: b.enrichTicketBadge,
      stopWatching: b.stopWatching,
    },
  });
  m("savedViews")?.configure?.({ state: b.state, els: b.els, api: b.api, request: b.request, showToast: b.showToast, escapeHtml: b.escapeHtml, refreshAll: b.refreshAll });
  m("adminActions")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    showToast: b.showToast,
    escapeHtml: b.escapeHtml,
    TENANT: b.TENANT,
    roleLabels: b.ROLE_LABELS,
    canManage: b.canManage,
    actions: {
      renderReportWebhookOptions: b.renderReportWebhookOptions,
      loadReportSubscriptions: b.loadReportSubscriptions,
      loadRuleGroups: b.loadRuleGroups,
      loadSlaPolicies: b.loadSlaPolicies,
      loadRoutingRules: b.loadRoutingRules,
      loadCsatSummary: b.loadCsatSummary,
    },
  });
  m("refresh")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    apiWithHeaders: b.apiWithHeaders,
    showToast: b.showToast,
    escapeHtml: b.escapeHtml,
    TENANT: b.TENANT,
    BASE_HEADERS: b.BASE_HEADERS,
    actions: {
      pollInterval: b.pollInterval,
      renderLoadingQueue: b.renderLoadingQueue,
      renderQueue: b.renderQueue,
      conversationQuery: b.conversationQuery,
      queueSignature: b.queueSignature,
      loadLabelCatalog: b.loadLabelCatalog,
      pruneExpiredDrafts: b.pruneExpiredDrafts,
      scheduleIdle: b.scheduleIdle,
      loadMentions: b.loadMentions,
      loadCollaborators: b.loadCollaborators,
      loadCannedResponses: b.loadCannedResponses,
      renderLabelFilter: b.renderLabelFilter,
      loadDetail: b.loadDetail,
      selectConversation: b.selectConversation,
      clearSelection: b.clearSelection,
      roleLabel: b.roleLabel,
    },
  });
  m("queueActions")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    apiWithHeaders: b.apiWithHeaders,
    showToast: b.showToast,
    setFormBusy: b.setFormBusy,
    refreshAll: b.refreshAll,
    actions: { conversationQuery: b.conversationQuery, renderQueue: b.renderQueue, renderBulkToolbar: b.renderBulkToolbar },
  });
  m("queueHelpers")?.configure?.({ state: b.state, els: b.els, api: b.api, queuePageSize: b.queuePageSize });
  m("queueFilters")?.configure?.({ state: b.state, els: b.els, refreshAll: b.refreshAll, escapeHtml: b.escapeHtml });
  m("shortcuts")?.configure?.({ state: b.state, els: b.els, refreshAll: b.refreshAll, selectConversation: b.selectConversation });
  m("knowledgeView")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    showToast: b.showToast,
    setFormBusy: b.setFormBusy,
    escapeHtml: b.escapeHtml,
    formatTime: b.formatTime,
    languageNames: b.LANGUAGE_NAMES,
  });
  m("appNav")?.configure?.({
    els: b.els,
    loadDesktopInfo: b.loadDesktopInfo,
    renderQualityPanel: b.renderQualityPanel,
    loadQualityPanel: b.loadQualityPanel,
    loadAdminView: b.loadAdminView,
    loadKnowledgeView: b.loadKnowledgeView,
    scheduleIdle: b.scheduleIdle,
  });
  m("notes")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    canOperate: b.canOperate,
    setFormBusy: b.setFormBusy,
    escapeHtml: b.escapeHtml,
    roleLabels: b.ROLE_LABELS,
  });
  m("commandDispatch")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    showToast: b.showToast,
    escapeHtml: b.escapeHtml,
    switchAppView: b.switchAppView,
    refreshAll: b.refreshAll,
    actions: { loadDetail: b.loadDetail },
  });
  m("conversationActions")?.configure?.({
    state: b.state,
    els: b.els,
    api: b.api,
    showToast: b.showToast,
    setFormBusy: b.setFormBusy,
    loadDetail: b.loadDetail,
    refreshAll: b.refreshAll,
    renderSubtitle: b.renderSubtitle,
    renderLanguagePicker: b.renderLanguagePicker,
    scheduleIdle: b.scheduleIdle,
    languageNames: b.LANGUAGE_NAMES,
    actions: { renderQueue: b.renderQueue },
  });
  m("conversationDetail")?.configure?.({
    state: b.state,
    els: b.els,
    apiWithHeaders: b.apiWithHeaders,
    renderDetail: b.renderDetail,
    renderQueue: b.renderQueue,
    stopWatching: b.stopWatching,
    resetCopilot: b.resetCopilot,
    hideMentionSuggest: b.hideMentionSuggest,
    windowedRowHeight: b.windowedRowHeight,
    closeQueueDrawer: b.closeQueueDrawer,
    canWriteConversations: b.canWriteConversations,
    showToast: b.showToast,
    languageNames: b.LANGUAGE_NAMES,
    languageOptions: b.LANGUAGE_OPTIONS,
    threadPageLimit: b.THREAD_PAGE_LIMIT,
  });
  m("boot")?.configure?.(withDensity(b, (d) => ({
    state: b.state,
    els: b.els,
    document: b.document,
    window: b.window,
    selectConversation: b.selectConversation,
    handleQueueScroll: b.handleQueueScroll,
    renderBulkToolbar: b.renderBulkToolbar,
    switchInspectorTab: b.switchInspectorTab,
    setDensity: b.setDensity,
    nextDensity: (level) => d.nextDensity(level),
    renderQueue: b.renderQueue,
    applyWorkspacePreferences: b.applyWorkspacePreferences,
    schedulePolling: b.schedulePolling,
    showToast: b.showToast,
    refreshAll: b.refreshAll,
    renderInspector: b.renderInspector,
    applyMacroFromSuggest: b.applyMacroFromSuggest,
    insertCannedResponse: b.insertCannedResponse,
    switchAppView: b.switchAppView,
    renderSummaries: b.renderSummaries,
    renderWebhookEventCheckboxes: b.renderWebhookEventCheckboxes,
    saveQuota: b.saveQuota,
    inviteMember: b.inviteMember,
    registerWebhook: b.registerWebhook,
    loadAdminView: b.loadAdminView,
    saveQuotaFromIsland: b.saveQuotaFromIsland,
    inviteMemberFromIsland: b.inviteMemberFromIsland,
    changeMemberRoleFromIsland: b.changeMemberRoleFromIsland,
    deactivateMemberFromIsland: b.deactivateMemberFromIsland,
    registerWebhookFromIsland: b.registerWebhookFromIsland,
    deleteWebhookFromIsland: b.deleteWebhookFromIsland,
    createSubscriptionFromIsland: b.createSubscriptionFromIsland,
    toggleSubscriptionFromIsland: b.toggleSubscriptionFromIsland,
    deleteSubscriptionFromIsland: b.deleteSubscriptionFromIsland,
    generateReportFromIsland: b.generateReportFromIsland,
    saveSlaFromIsland: b.saveSlaFromIsland,
    createRuleFromIsland: b.createRuleFromIsland,
    deleteRuleFromIsland: b.deleteRuleFromIsland,
    governanceDecideFromIsland: b.governanceDecideFromIsland,
    governanceFeedbackReviewFromIsland: b.governanceFeedbackReviewFromIsland,
    changeMemberRole: b.changeMemberRole,
    deactivateMember: b.deactivateMember,
    deleteWebhook: b.deleteWebhook,
    renderMetrics: b.renderMetrics,
    renderLoadingQueue: b.renderLoadingQueue,
    detectConstrainedDevice: b.detectConstrainedDevice,
    normalizeDensity: (level) => d.normalizeDensity(level),
    loadSavedViews: b.loadSavedViews,
    PREF_LOW_PERF: b.PREF_LOW_PERF,
    PREF_INSPECTOR: b.PREF_INSPECTOR,
    PREF_DENSITY: b.PREF_DENSITY,
  })));
}

/** Wire every domain module from a single flat legacy bundle. */
export function wireModules(bundle) {
  configureModules(bundle);
}

export default { wireModules, configureModules };