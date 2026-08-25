/**
 * Helix Support — operator console glue (D3 trimmed, <500 lines)
 *
 * D3 (DESKTOP_TAURI_PLAN.md §D3): all rendering is migrated to React islands.
 * This file retains only the glue that coordinates state, API networking,
 * SSE/queue relay, view switching, and mobile drawer a11y. The React
 * islands render into parallel mount divs; this file dispatches state
 * updates to them via custom events.
 */

const TENANT = document.documentElement.dataset.tenantId || "demo";
const BASE_HEADERS = { Accept: "application/json", "X-Tenant-Id": TENANT };
const POLL_INTERVAL_NORMAL = 30000;
const POLL_INTERVAL_LOW = 90000;
const PREF_DENSITY = "helix-queue-density";
const PREF_LOW_PERF = "helix-low-perf";
const PREF_INSPECTOR = "helix-inspector-collapsed";
const THREAD_PAGE_LIMIT = 100;
const QUEUE_PAGE_SIZE_NORMAL = 50;
const QUEUE_PAGE_SIZE_LOW = 20;

const state = {
  selectedId: null, conversations: [], detail: null, dashboard: null,
  labelCatalog: [], me: null, activeTab: "overview", refreshPromise: null,
  detailSequence: 0, queueHasMore: false, queueCursor: null,
  queueLoadingMore: false, threadPrevCursor: null, threadLoadingOlder: false,
  bulkSelected: new Set(), cannedResponses: [], savedViews: [],
  lowPerf: false, inspectorCollapsed: false, pollTimer: null,
  queueEventSource: null, queueRelay: null, claimRenewTimer: null,
  draftTimer: null, queueReconnectTimer: null,
  inspectorRendered: { overview: false, evidence: false, audit: false },
  macroOpen: false, collaborators: [], collaboratorsLoadedAt: 0,
  mentionOpen: false, mentionToken: "", labelsLoadedAt: 0, cannedLoadedAt: 0,
  lastCopilotConv: null, lastQueueSignature: "", queueVirtual: false,
  queueRowHeight: 0, queueWindow: null, queueWindowSig: "", queueScrollRaf: 0,
  queueMeasuring: false, qualityBuckets: [], qualityGaps: [], qualityLoadedAt: 0,
  knowledgeArticles: [], knowledgeLoadedAt: 0, knowledgeLoadedForWriter: null,
  knowledgeEditingId: null, mentions: [], mentionsOpen: false,
  watchController: null, density: "comfortable",
};

const $ = (id) => document.getElementById(id);
const els = {
  metrics: $("metrics"), list: $("conversationList"), queueCount: $("queueCount"),
  loadMore: $("loadMore"), queuePane: $("queuePane"), searchInput: $("searchInput"),
  statusFilter: $("statusFilter"), labelFilter: $("labelFilter"), priorityFilter: $("priorityFilter"),
  ownershipFilter: $("ownershipFilter"), channelFilter: $("channelFilter"), sortFilter: $("sortFilter"),
  focusWaiting: $("focusWaiting"), savedViewSelect: $("savedViewSelect"),
  saveView: $("saveView"), deleteView: $("deleteView"),
  densityToggle: $("densityToggle"), lowPerfToggle: $("lowPerfToggle"),
  inspectorToggle: $("inspectorToggle"), claimBtn: $("claimBtn"), releaseBtn: $("releaseBtn"),
  assignBtn: $("assignBtn"), liveStatus: $("liveStatus"), operatorIdentity: $("operatorIdentity"),
  refreshList: $("refreshList"), mobileQueue: $("mobileQueue"), backToQueue: $("backToQueue"),
  emptyState: $("emptyState"), conversationView: $("conversationView"),
  customerForm: $("customerForm"), customerInput: $("customerInput"),
  operatorForm: $("operatorForm"), operatorInput: $("operatorInput"),
  acceptBtn: $("acceptBtn"), resolveBtn: $("resolveBtn"), reopenBtn: $("reopenBtn"),
  ticketBtn: $("ticketBtn"), ticketBadge: $("ticketBadge"),
  newConversation: $("newConversation"), newConversationDialog: $("newConversationDialog"),
  newConversationForm: $("newConversationForm"), newCustomerName: $("newCustomerName"),
  newCustomerRef: $("newCustomerRef"), newChannel: $("newChannel"),
  closeDialog: $("closeDialog"), cancelDialog: $("cancelDialog"), toast: $("toast"),
  appNav: $("appNav"), workspaceView: $("workspaceView"), qualityView: $("qualityView"),
  knowledgeView: $("knowledgeView"), adminView: $("adminView"), placeholderView: $("placeholderView"),
  desktopVersion: $("desktopVersion"), desktopBackendPort: $("desktopBackendPort"),
  desktopBackendMode: $("desktopBackendMode"), desktopDataDir: $("desktopDataDir"),
  desktopEnvNote: $("desktopEnvNote"), refreshAdmin: $("refreshAdmin"),
  quotaForm: $("quotaForm"), memberForm: $("memberForm"), webhookForm: $("webhookForm"),
  webhookList: $("webhookList"), memberList: $("memberList"),
  knowledgeSearch: $("knowledgeSearch"), knowledgeStatusFilter: $("knowledgeStatusFilter"),
  knowledgeLanguageFilter: $("knowledgeLanguageFilter"), knowledgeForm: $("knowledgeForm"),
  knowledgeList: $("knowledgeList"), newKnowledgeDraft: $("newKnowledgeDraft"),
  refreshKnowledge: $("refreshKnowledge"), cancelKnowledgeEdit: $("cancelKnowledgeEdit"),
  resetKnowledgeForm: $("resetKnowledgeForm"),
};

/* ── pure helpers ────────────────────────────────────────────────────── */

function escapeHtml(v) { return String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;"); }
function statusLabel(s) { return {open:"待处理",pending:"待响应",active:"处理中",resolved:"已解决",closed:"已关闭"}[s]||s; }
function formatTime(v) { if (!v) return "—"; const d = new Date(v); return isNaN(d) ? String(v) : d.toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}); }
function formatSla(c) { if (!c?.sla_deadline) return {text:"SLA -",breached:false}; const ms=new Date(c.sla_deadline)-Date.now(); return {text:ms>0?`SLA ${Math.ceil(ms/60000)}m`:"SLA 超时",breached:ms<=0}; }
function canOperate() { return ["operator","supervisor","admin","platform"].includes(state.me?.role||""); }
function canManage() { return ["admin","platform"].includes(state.me?.role||""); }
function canWriteConversations() { return canOperate(); }
function canWriteKnowledge() { return ["admin","platform"].includes(state.me?.role||""); }
function showToast(msg, err=false) { if(!els.toast)return; els.toast.textContent=msg; els.toast.classList.toggle("is-error",err); els.toast.classList.add("is-visible"); clearTimeout(els.toast._t); els.toast._t=setTimeout(()=>els.toast.classList.remove("is-visible"),4000); }
function newIdempotencyKey() { return window.crypto?.randomUUID ? `ui-${crypto.randomUUID()}` : `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function setFormBusy(f,b) { f.querySelectorAll("button").forEach(b2=>b2.disabled=b); }
function scheduleIdle(fn,ms=2000) { return "requestIdleCallback" in window ? requestIdleCallback(fn,{timeout:ms}) : setTimeout(fn,ms); }
function queuePageSize() { return state.lowPerf ? QUEUE_PAGE_SIZE_LOW : QUEUE_PAGE_SIZE_NORMAL; }
function pollInterval() { return state.lowPerf ? POLL_INTERVAL_LOW : POLL_INTERVAL_NORMAL; }
function detectConstrainedDevice() { const c=navigator.hardwareConcurrency||4, m=navigator.deviceMemory||4, ct=navigator.connection?.effectiveType||"4g"; return c<=4||m<=2||ct==="slow-2g"||ct==="2g"||navigator.saveData; }
function normalizeDensity(v) { return ["comfortable","compact","dense"].includes(v)?v:"comfortable"; }
function nextDensity(d) { return {comfortable:"compact",compact:"dense",dense:"comfortable"}[d]||"comfortable"; }

/* ── API layer ────────────────────────────────────────────────────────── */

async function request(path, options = {}) {
  const headers = { ...BASE_HEADERS, ...options.headers };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (options.idempotent) headers["Idempotency-Key"] = newIdempotencyKey();
  const res = await fetch(path, { ...options, headers });
  if (res.status === 204) return null;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.detail || data?.message || `HTTP ${res.status}`);
  return data;
}
function api(path, opts = {}) { return request(path, opts); }
function dispatchIslandEvent(name, detail) { window.dispatchEvent(new CustomEvent(name, { detail })); }

/* ── state coordination ────────────────────────────────────────────────── */

function currentViewFilters() {
  return { status:els.statusFilter?.value||"", label:els.labelFilter?.value||"", priority:els.priorityFilter?.value||"", ownership:els.ownershipFilter?.value||"", channel:els.channelFilter?.value||"", sort:els.sortFilter?.value||"", search:els.searchInput?.value||"" };
}
function conversationQuery() {
  const f = currentViewFilters();
  const p = new URLSearchParams();
  if (f.status) p.set("status",f.status); if (f.search) p.set("search",f.search); if (f.priority) p.set("priority",f.priority);
  if (f.ownership) p.set("ownership",f.ownership); if (f.channel) p.set("channel",f.channel); if (f.sort) p.set("sort",f.sort);
  p.set("limit", String(queuePageSize()));
  return p.toString();
}

/* ── SSE / queue relay ────────────────────────────────────────────────── */

function relayClientId() {
  try { return localStorage.getItem("helix-relay-id") || crypto.randomUUID(); } catch { return "anon"; }
}
function setLiveStatus(mode) { if (els.liveStatus) els.liveStatus.dataset.mode = mode; }
function initQueueRelay() { /* delegate to sse.js module via HelixModules */ }

function connectQueueEvents() {
  state.queueEventSource?.abort?.();
  const controller = new AbortController();
  state.queueEventSource = controller;
  const url = `/api/events/queue?timeout=45&${conversationQuery()}`;
  fetch(url, { signal: controller.signal, headers: BASE_HEADERS })
    .then(async (res) => {
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop();
        for (const evt of events) {
          const lines = evt.split("\n");
          const type = lines.find((l) => l.startsWith("event:"))?.slice(6).trim() || "message";
          const data = lines.find((l) => l.startsWith("data:"))?.slice(5).trim();
          if (data) handleQueueEvent(type, JSON.parse(data));
        }
      }
    })
    .catch(() => { if (!controller.signal.aborted) scheduleReconnect(); });
}

function handleQueueEvent(type, data) {
  if (type === "conversation.updated" || type === "conversation.created") {
    const idx = state.conversations.findIndex((c) => c.id === data.id);
    if (idx >= 0) state.conversations[idx] = { ...state.conversations[idx], ...data };
    else state.conversations.unshift(data);
    dispatchIslandEvent("helix-queue-update", { conversations: state.conversations });
  }
}
function scheduleReconnect() {
  clearTimeout(state.queueReconnectTimer);
  state.queueReconnectTimer = setTimeout(connectQueueEvents, 5000);
}
function schedulePolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(() => { if (!state.queueEventSource) refreshAll({ silent: true, background: true }); }, pollInterval());
}

/* ── refresh orchestration ───────────────────────────────────────────── */

function queueSignature(c) { return c.map((x) => `${x.id}:${x.status}:${x.updated_at}:${x.version ?? 0}`).join("|"); }

async function refreshAll({ silent = false, refreshDetail = true, background = false } = {}) {
  if (state.refreshPromise) return state.refreshPromise;
  state.refreshPromise = (async () => {
    try {
      const [me, dashboard, conversations] = await Promise.all([
        api("/api/me").catch(() => null),
        api("/api/dashboard").catch(() => null),
        api(`/api/conversations?${conversationQuery()}`),
      ]);
      state.me = me;
      state.dashboard = dashboard;
      // Expose the current operator role so desktop React islands (terminal,
      // §4.1 RBAC) can pass it to Tauri IPC without re-fetching /api/me.
      if (typeof window !== "undefined") window.__HELIX_ROLE__ = me?.role || "guest";
      const convs = Array.isArray(conversations) ? conversations : conversations?.items || [];
      state.conversations = convs;
      state.lastQueueSignature = queueSignature(convs);
      dispatchIslandEvent("helix-queue-update", { conversations: convs, dashboard });
      if (els.operatorIdentity) els.operatorIdentity.textContent = me?.actor_id || "访客";
      if (refreshDetail && state.selectedId) await loadDetail(state.selectedId);
    } catch (e) { if (!silent) showToast(e.message, true); }
    finally { state.refreshPromise = null; }
  })();
  return state.refreshPromise;
}

async function loadDetail(id) {
  if (!id) return;
  state.detailSequence++;
  const seq = state.detailSequence;
  try {
    const detail = await api(`/api/conversations/${encodeURIComponent(id)}`);
    if (seq !== state.detailSequence) return;
    state.detail = detail;
    dispatchIslandEvent("helix-detail-loaded", { detail });
  } catch (e) { showToast(e.message, true); }
}

async function selectConversation(id) {
  state.selectedId = id;
  dispatchIslandEvent("helix-queue-select", { id });
  if (els.mobileQueue && els.queuePane?.classList.contains("is-open")) closeQueueDrawer();
  await loadDetail(id);
}

async function loadMoreConversations() {
  if (state.queueLoadingMore || !state.queueHasMore) return;
  state.queueLoadingMore = true;
  try {
    const params = conversationQuery();
    if (state.queueCursor) params.set("cursor", state.queueCursor);
    const res = await api(`/api/conversations?${params}`);
    const items = Array.isArray(res) ? res : res?.items || [];
    state.conversations.push(...items);
    state.queueHasMore = !!res?.has_more;
    state.queueCursor = res?.next_cursor || null;
    dispatchIslandEvent("helix-queue-update", { conversations: state.conversations });
  } finally { state.queueLoadingMore = false; }
}

async function performConversationAction(action, successMessage) {
  if (!state.selectedId || !canOperate()) return;
  try {
    await api(`/api/conversations/${encodeURIComponent(state.selectedId)}/${action}`, {
      method: "POST", idempotent: true,
    });
    showToast(successMessage || "操作成功");
    await loadDetail(state.selectedId);
    void refreshAll({ silent: true, refreshDetail: false });
  } catch (e) { showToast(e.message, true); }
}

/* ── view switching ───────────────────────────────────────────────────── */

function setNavActive(view) {
  els.appNav?.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.view === view);
  });
}
function showAppView(name) {
  const views = { workspace: els.workspaceView, quality: els.qualityView, knowledge: els.knowledgeView, admin: els.adminView };
  for (const [key, el] of Object.entries(views)) if (el) el.hidden = key !== name;
  if (els.placeholderView) {
    els.placeholderView.hidden = name !== "settings";
    if (!els.placeholderView.hidden) loadDesktopInfo();
  }
}
function switchAppView(view) {
  setNavActive(view);
  showAppView(view);
  if (view === "quality") dispatchIslandEvent("helix-quality-refresh");
  if (view === "knowledge") void loadKnowledgeView();
  if (view === "admin") void loadAdminView();
}
function currentAppView() {
  return els.appNav?.querySelector(".nav-item.is-active")?.dataset.view || "workspace";
}

/* ── desktop info (D1) + knowledge/admin glue — delegated to desktop-info.js ─ */

function loadDesktopInfo() { HelixModules.desktopInfo.loadDesktopInfo(els); }
function knowledgeFilters() { return HelixModules.desktopInfo.knowledgeFilters(els); }
async function loadKnowledgeView({ force = false } = {}) {
  await HelixModules.desktopInfo.loadKnowledgeView(els, { api, dispatchIslandEvent, showToast });
  if (force) state.knowledgeLoadedAt = Date.now();
}
async function loadAdminView() {
  await HelixModules.desktopInfo.loadAdminView(els, { api, dispatchIslandEvent, showToast, state, canManage });
}

/* ── density & workspace prefs ───────────────────────────────────────── */

function setDensity(level, { persist = true } = {}) {
  state.density = normalizeDensity(level);
  document.body.dataset.density = state.density;
  if (persist) localStorage.setItem(PREF_DENSITY, state.density);
}
function applyWorkspacePreferences() {
  document.body.classList.toggle("is-low-perf", state.lowPerf);
  document.body.dataset.density = state.lowPerf ? "compact" : state.density;
  if (els.inspectorSurface) els.inspectorSurface.classList.toggle("is-collapsed", state.inspectorCollapsed);
}

/* ── mobile drawer (ui_smoke-critical) ────────────────────────────────── */

let queueScrim = null;
function isQueueDrawerMode() { return window.matchMedia("(max-width: 900px)").matches; }
function setBackgroundInert(inert) {
  const main = document.querySelector(".conversation-pane");
  if (main) inert ? main.setAttribute("inert", "") : main.removeAttribute("inert");
}
function trapQueueFocus(event) {
  if (event.key !== "Tab" || !els.queuePane) return;
  const focusable = els.queuePane.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])');
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
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

/* ── event binding ────────────────────────────────────────────────────── */

let searchTimer;
els.refreshList?.addEventListener("click", () => refreshAll());
els.loadMore?.addEventListener("click", loadMoreConversations);
els.statusFilter?.addEventListener("change", () => refreshAll());
els.labelFilter?.addEventListener("change", () => refreshAll());
els.priorityFilter?.addEventListener("change", () => refreshAll());
els.ownershipFilter?.addEventListener("change", () => refreshAll());
els.channelFilter?.addEventListener("change", () => refreshAll());
els.sortFilter?.addEventListener("change", () => refreshAll());
els.searchInput?.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => refreshAll({ silent: true, refreshDetail: false }), state.lowPerf ? 450 : 260);
});
els.densityToggle?.addEventListener("click", () => {
  if (state.lowPerf) return;
  setDensity(nextDensity(state.density));
  dispatchIslandEvent("helix-queue-update", { conversations: state.conversations });
});
els.lowPerfToggle?.addEventListener("click", () => {
  state.lowPerf = !state.lowPerf;
  localStorage.setItem(PREF_LOW_PERF, state.lowPerf ? "1" : "0");
  applyWorkspacePreferences();
  setDensity(state.density, { persist: false });
  schedulePolling();
  showToast(state.lowPerf ? "已开启低配模式" : "已关闭低配模式");
  void refreshAll({ silent: true, refreshDetail: false });
});
els.inspectorToggle?.addEventListener("click", () => {
  state.inspectorCollapsed = !state.inspectorCollapsed;
  localStorage.setItem(PREF_INSPECTOR, state.inspectorCollapsed ? "1" : "0");
  applyWorkspacePreferences();
  if (!state.inspectorCollapsed && state.detail) dispatchIslandEvent("helix-detail-loaded", { detail: state.detail });
});
els.claimBtn?.addEventListener("click", () => performConversationAction("claim", "会话已认领"));
els.releaseBtn?.addEventListener("click", () => performConversationAction("release", "会话已释放"));
els.acceptBtn?.addEventListener("click", () => performConversationAction("accept", "会话已接入"));
els.resolveBtn?.addEventListener("click", () => performConversationAction("resolve", "会话已解决"));
els.reopenBtn?.addEventListener("click", () => performConversationAction("reopen", "会话已重开"));
els.mobileQueue?.addEventListener("click", () => {
  els.queuePane.classList.contains("is-open") ? closeQueueDrawer() : openQueueDrawer();
});
els.backToQueue?.addEventListener("click", openQueueDrawer);
document.getElementById("queueClose")?.addEventListener("click", closeQueueDrawer);
els.appNav?.addEventListener("click", (e) => {
  const btn = e.target.closest(".nav-item");
  if (btn?.dataset.view) switchAppView(btn.dataset.view);
});
els.newConversation?.addEventListener("click", () => {
  els.newConversationForm?.reset();
  els.newConversationDialog?.showModal();
  setTimeout(() => els.newCustomerName?.focus(), 0);
});
els.closeDialog?.addEventListener("click", () => els.newConversationDialog?.close());
els.cancelDialog?.addEventListener("click", () => els.newConversationDialog?.close());
els.newConversationForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = { customer_name: els.newCustomerName.value.trim(), channel: els.newChannel.value };
  const ref = els.newCustomerRef.value.trim();
  if (ref) payload.customer_ref = ref;
  if (!payload.customer_name) return;
  setFormBusy(els.newConversationForm, true);
  try {
    const created = await api("/api/conversations", { method: "POST", body: JSON.stringify(payload) });
    els.newConversationDialog.close();
    state.selectedId = created.id;
    state.conversations = [created, ...state.conversations.filter((c) => c.id !== created.id)];
    dispatchIslandEvent("helix-queue-update", { conversations: state.conversations });
    await loadDetail(created.id);
    void refreshAll({ silent: true, refreshDetail: false });
  } catch (err) { showToast(err.message, true); }
  finally { setFormBusy(els.newConversationForm, false); }
});
els.knowledgeSearch?.addEventListener("input", () => dispatchIslandEvent("helix-knowledge-filter", knowledgeFilters()));
els.knowledgeStatusFilter?.addEventListener("change", () => dispatchIslandEvent("helix-knowledge-filter", knowledgeFilters()));
els.knowledgeLanguageFilter?.addEventListener("change", () => dispatchIslandEvent("helix-knowledge-filter", knowledgeFilters()));
els.refreshKnowledge?.addEventListener("click", () => { state.knowledgeLoadedAt = 0; void loadKnowledgeView({ force: true }); });
els.refreshAdmin?.addEventListener("click", () => void loadAdminView());
els.customerForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = els.customerInput.value.trim();
  if (!msg || !state.selectedId) return;
  setFormBusy(els.customerForm, true);
  try { await api(`/api/conversations/${encodeURIComponent(state.selectedId)}/messages`, { method: "POST", body: JSON.stringify({ content: msg, sender: "customer" }) }); els.customerInput.value = ""; await loadDetail(state.selectedId); }
  catch (err) { showToast(err.message, true); }
  finally { setFormBusy(els.customerForm, false); }
});
els.operatorForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = els.operatorInput.value.trim();
  if (!msg || !state.selectedId || !canOperate()) return;
  setFormBusy(els.operatorForm, true);
  try { await api(`/api/conversations/${encodeURIComponent(state.selectedId)}/messages`, { method: "POST", body: JSON.stringify({ content: msg, sender: "operator" }), idempotent: true }); els.operatorInput.value = ""; await loadDetail(state.selectedId); }
  catch (err) { showToast(err.message, true); }
  finally { setFormBusy(els.operatorForm, false); }
});

function nextDensity(d) { return { comfortable: "compact", compact: "dense", dense: "comfortable" }[d] || "comfortable"; }

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && els.queuePane?.classList.contains("is-open") && queueScrim && !queueScrim.hidden) closeQueueDrawer();
  const target = e.target;
  const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
  if (editing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); els.searchInput?.focus(); }
  else if (e.key === "r") { e.preventDefault(); refreshAll(); }
  else if (e.key === "i") { e.preventDefault(); els.inspectorToggle?.click(); }
});
window.addEventListener("unhandledrejection", (e) => showToast(e.reason?.message || "操作失败", true));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { state.queueEventSource?.abort?.(); state.queueEventSource = null; return; }
  connectQueueEvents();
  void refreshAll({ silent: true, background: true, refreshDetail: false });
});

/* ── init ────────────────────────────────────────────────────────────── */

const storedLowPerf = localStorage.getItem(PREF_LOW_PERF);
state.lowPerf = storedLowPerf === "1" || (storedLowPerf !== "0" && detectConstrainedDevice());
if (storedLowPerf == null && state.lowPerf) localStorage.setItem(PREF_LOW_PERF, "1");
state.inspectorCollapsed = localStorage.getItem(PREF_INSPECTOR) === "1";
state.density = normalizeDensity(localStorage.getItem(PREF_DENSITY));
if (state.lowPerf && localStorage.getItem(PREF_DENSITY) == null) state.density = "compact";
setDensity(state.density, { persist: false });
applyWorkspacePreferences();
schedulePolling();
connectQueueEvents();
refreshAll();
