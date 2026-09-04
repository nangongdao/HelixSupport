/**
 * Helix Support — legacy boot assembly (app.js <500 campaign).
 *
 * The operator console's boot wiring, extracted verbatim from app.js: the
 * queue/inspector/density/low-perf listeners, the macro/canned chip clicks,
 * the desktop island bridges (helix-ticket-open/queue-select/workspace-tab,
 * mentions-jump/mark-read, summary-sync, conversations-sync, plus the admin
 * island write bridges), the admin form bindings, and the initial render +
 * preference
 * hydration.
 *
 * A single `bindLegacyBoot()` entry runs the whole assembly once; everything
 * arrives through configure because every referenced name is app.js-scoped.
 */

let ctx = null;

/** Inject the legacy app.js singletons (els/state/document/localStorage). */
function configure(deps) {
  ctx = deps;
}

function bindLegacyBoot() {
  const { state, els, document, window: win } = ctx;

  els.list.addEventListener("click", (event) => {
    const item = event.target.closest(".conversation-item");
    if (item?.dataset.id) ctx.selectConversation(item.dataset.id);
  });

  // ROADMAP §18.4: virtual-mode scroll keeps the windowed render aligned.
  els.list.addEventListener("scroll", ctx.handleQueueScroll, { passive: true });

  els.list.addEventListener("change", (event) => {
    const checkbox = event.target.closest(".conversation-checkbox");
    if (!checkbox?.dataset.selectId) return;
    if (checkbox.checked) state.bulkSelected.add(checkbox.dataset.selectId);
    else state.bulkSelected.delete(checkbox.dataset.selectId);
    checkbox.closest(".conversation-row")?.classList.toggle("is-selected", checkbox.checked);
    ctx.renderBulkToolbar();
  });

  document.querySelectorAll(".inspector-tab").forEach((button) => {
    button.addEventListener("click", () => ctx.switchInspectorTab(button.dataset.tab));
  });

  els.densityToggle.addEventListener("click", () => {
    // In low-perf the visible density is always compact (effectiveDensity);
    // cycling here would mutate the stored choice with no visible effect and
    // it would only materialize after low-perf is disabled (audit D3).
    if (state.lowPerf) return;
    ctx.setDensity(ctx.nextDensity(state.density));
    ctx.renderQueue();
  });
  if (els.lowPerfToggle) {
    els.lowPerfToggle.addEventListener("click", () => {
      state.lowPerf = !state.lowPerf;
      win.localStorage.setItem(ctx.PREF_LOW_PERF, state.lowPerf ? "1" : "0");
      ctx.applyWorkspacePreferences();
      // UI 升级 §17.2: low-perf forces compact through the density module
      // (effectiveDensity) without discarding the user's chosen level.
      ctx.setDensity(state.density, { persist: false });
      ctx.schedulePolling();
      ctx.showToast(state.lowPerf ? "已开启低配模式" : "已关闭低配模式");
      void ctx.refreshAll({ silent: true, refreshDetail: false });
    });
  }
  if (els.inspectorToggle) {
    els.inspectorToggle.addEventListener("click", () => {
      state.inspectorCollapsed = !state.inspectorCollapsed;
      win.localStorage.setItem(ctx.PREF_INSPECTOR, state.inspectorCollapsed ? "1" : "0");
      ctx.applyWorkspacePreferences();
      if (!state.inspectorCollapsed && state.detail) {
        ctx.renderInspector(state.detail);
      }
    });
  }
  els.bulkAction.addEventListener("change", ctx.renderBulkToolbar);
  els.clearBulk.addEventListener("click", () => {
    state.bulkSelected.clear();
    ctx.renderQueue();
  });

  if (els.macroSuggest) {
    els.macroSuggest.addEventListener("click", (event) => {
      const button = event.target.closest(".macro-option");
      if (button?.dataset.macroId) ctx.applyMacroFromSuggest(button.dataset.macroId);
    });
  }
  if (els.cannedList) {
    els.cannedList.addEventListener("click", (event) => {
      const button = event.target.closest(".canned-chip");
      if (button?.dataset.macroId) ctx.insertCannedResponse(button.dataset.macroId);
    });
  }

  // Module islands' own binders (mark activation); each owns its listeners.
  win.HelixModules?.qualityPanel?.bindQuality?.();
  win.HelixModules?.knowledgeView?.bindKnowledgeView?.();
  win.HelixModules?.queueView?.bindQueueDrawer?.();
  win.HelixModules?.savedViews?.bindSavedViews?.();
  win.HelixModules?.commandDispatch?.bindCommandDispatch?.();
  win.HelixModules?.refresh?.bindRefresh?.();
  win.HelixModules?.queueActions?.bindQueueActions?.();
  win.HelixModules?.queueFilters?.bindQueueFilters?.();
  win.HelixModules?.shortcuts?.bindShortcuts?.();
  win.HelixModules?.conversationActions?.bindConversationActions?.();
  win.HelixModules?.notes?.bindNotes?.();
  win.HelixModules?.thread?.bindThread?.();
  win.HelixModules?.composer?.bindComposer?.();
  // §17.1 palette: the shell island owns Ctrl+K (__HELIX_ISLAND_MODE__) so
  // the legacy <dialog> never stacks on top of it; in a browser tab the flag
  // is never set and js/command-dispatch.js bindCommandPalette is the handler.
  win.HelixModules?.commandDispatch?.bindCommandPalette?.();
  win.HelixModules?.ticketView?.bindTickets?.();
  win.HelixModules?.attachments?.bindAttachments?.();

  // UI 升级 §17.1: 全局导航栏 — view switching + quality refresh.
  if (els.appNav) {
    els.appNav.addEventListener("click", (event) => {
      const button = event.target.closest(".nav-item");
      if (button?.dataset.view) ctx.switchAppView(button.dataset.view);
    });
  }

  // D3 bridge: the React ticket island dispatches "helix-ticket-open" when a
  // row is clicked (the legacy #ticketList is yielded and hidden in the
  // desktop shell). Bridge it to the legacy detail opener so the ticket detail
  // view stays in ticket-view.js until a later D3 slice migrates it.
  win.addEventListener("helix-ticket-open", (event) => {
    const { ticketId } = event.detail || {};
    if (!ticketId) return;
    void win.HelixModules?.ticketView?.openTicketDetail?.(ticketId);
  });
  // D3 bridge: the React queue island dispatches "helix-queue-select" when a
  // row is clicked (the legacy #conversationList is yielded and hidden in the
  // desktop shell). Bridge it back to the legacy detail loader, which owns the
  // conversation thread view until a later D3 slice migrates it.
  win.addEventListener("helix-queue-select", (event) => {
    const { id } = event.detail || {};
    if (!id) return;
    void ctx.selectConversation(id);
  });
  // D3 bridge (workspace tabs island): the island dispatches
  // helix-workspace-tab on clicks; pane switching and the data side effects
  // (ticket loading, queue refresh) stay in legacy switchWorkspaceTab.
  win.addEventListener("helix-workspace-tab", (event) => {
    const { field } = event.detail || {};
    if (!["queue", "tickets"].includes(field)) return;
    win.HelixModules?.ticketView?.switchWorkspaceTab?.(field);
  });
  // D3 bridge (mentions island): the island owns the badge/panel; jumping to
  // a mentioned conversation and the mark-read POST+toast stay legacy.
  win.addEventListener("helix-mentions-open-jump", async (event) => {
    const { conversationId } = event.detail || {};
    if (conversationId) await ctx.selectConversation(conversationId);
  });
  win.addEventListener("helix-mentions-mark-read", async (event) => {
    const { id } = event.detail || {};
    if (id) await win.HelixModules?.session?.markMentionRead?.(id);
  });
  // D3 bridge (summary island): republish the current banner model on request.
  win.addEventListener("helix-summary-sync", () => {
    if (win.__HELIX_ISLAND_MODE__ && state.detail) ctx.renderSummaries(state.detail);
  });
  // D3 bridge: on mount the React queue island asks for the current queue
  // snapshot (helix-conversations-sync); re-render in island mode so the
  // freshly mounted island receives the latest list via renderQueue().
  win.addEventListener("helix-conversations-sync", () => {
    if (!win.__HELIX_ISLAND_MODE__) return;
    void win.HelixModules?.queueView?.renderQueue?.();
  });

  // UI 升级 §17.3: 管理页 — forms, member actions, webhook delete, refresh.
  ctx.renderWebhookEventCheckboxes();
  if (els.quotaForm) els.quotaForm.addEventListener("submit", (event) => void ctx.saveQuota(event));
  if (els.memberForm) els.memberForm.addEventListener("submit", (event) => void ctx.inviteMember(event));
  if (els.webhookForm) els.webhookForm.addEventListener("submit", (event) => void ctx.registerWebhook(event));
  win.HelixModules?.adminReport?.bindAdminReports?.();
  if (els.refreshAdmin) {
    els.refreshAdmin.addEventListener("click", () => {
      // The header button is not yielded (it sits outside the island mount),
      // so in island mode it drives the island's queries directly instead of
      // the legacy fetch chain.
      if (win.__HELIX_ISLAND_MODE__) {
        win.dispatchEvent(new CustomEvent("helix-admin-refresh", { detail: { force: true } }));
        return;
      }
      void ctx.loadAdminView();
    });
  }
  // D3 bridge (admin island): the React admin island dispatches helix-admin-*
  // write events with form payloads; the bridges own the api()/toast lifecycle
  // and answer with helix-admin-saved for the island's refetch.
  for (const [eventType, handler] of [
    ["helix-admin-save-quota", ctx.saveQuotaFromIsland],
    ["helix-admin-invite-member", ctx.inviteMemberFromIsland],
    ["helix-admin-member-role", ctx.changeMemberRoleFromIsland],
    ["helix-admin-member-deactivate", ctx.deactivateMemberFromIsland],
    ["helix-admin-register-webhook", ctx.registerWebhookFromIsland],
    ["helix-admin-delete-webhook", ctx.deleteWebhookFromIsland],
    ["helix-admin-create-subscription", ctx.createSubscriptionFromIsland],
    ["helix-admin-toggle-subscription", ctx.toggleSubscriptionFromIsland],
    ["helix-admin-delete-subscription", ctx.deleteSubscriptionFromIsland],
    ["helix-admin-generate-report", ctx.generateReportFromIsland],
    ["helix-admin-save-sla", ctx.saveSlaFromIsland],
    ["helix-admin-create-rule", ctx.createRuleFromIsland],
    ["helix-admin-delete-rule", ctx.deleteRuleFromIsland],
  ]) {
    win.addEventListener(eventType, (event) => void handler(event.detail || {}));
  }
  if (els.memberList) {
    els.memberList.addEventListener("change", (event) => {
      const select = event.target.closest(".member-role-select");
      if (select) void ctx.changeMemberRole(select.dataset.actor, select.value);
    });
    els.memberList.addEventListener("click", (event) => {
      const button = event.target.closest(".member-deactivate");
      if (button) void ctx.deactivateMember(button.dataset.actor);
    });
  }
  if (els.webhookList) {
    els.webhookList.addEventListener("click", (event) => {
      const button = event.target.closest(".webhook-delete");
      if (button) void ctx.deleteWebhook(button.dataset.id);
    });
  }

  // Initial paint.
  ctx.renderMetrics(null);
  ctx.renderLoadingQueue();

  // Preference hydration (low-perf / inspector / density).
  const storedLowPerf = win.localStorage.getItem(ctx.PREF_LOW_PERF);
  state.lowPerf = storedLowPerf === "1" || (storedLowPerf !== "0" && ctx.detectConstrainedDevice());
  if (storedLowPerf == null && state.lowPerf) {
    win.localStorage.setItem(ctx.PREF_LOW_PERF, "1");
  }
  state.inspectorCollapsed = win.localStorage.getItem(ctx.PREF_INSPECTOR) === "1";
  state.density = ctx.normalizeDensity(win.localStorage.getItem(ctx.PREF_DENSITY));
  if (state.lowPerf && win.localStorage.getItem(ctx.PREF_DENSITY) == null) {
    // Auto-detected low-perf must not persist a density the user never chose
    // (audit D2): effectiveDensity() forces compact while low-perf is active,
    // and the stored default stays untouched for when low-perf is disabled.
    state.density = "compact";
  }
  ctx.setDensity(state.density, { persist: false });
  ctx.applyWorkspacePreferences();
  ctx.schedulePolling();
  ctx.loadSavedViews();
  ctx.refreshAll();
}

export { configure, bindLegacyBoot };

export default { configure, bindLegacyBoot };