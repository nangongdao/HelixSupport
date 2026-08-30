/**
 * Helix Support — palette command dispatcher (app.js <500 campaign slice 22).
 *
 * Consumes the command-palette island's helix-command events and routes each
 * action key to its handler: nav view switches, the conversation-dialog and
 * terminal bridges, refresh, ticket conversion, and the backend health
 * check. Extracted from the legacy app.js; the handlers arrive through
 * configure because switchAppView/refreshAll are app.js-scoped.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export async function checkBackendHealth() {
  try {
    const res = await fetch("/health/ready");
    const body = await res.json().catch(() => ({}));
    const ready = res.ok && body.status === "ready";
    ctx.showToast(ready ? "健康检查：后端就绪" : `健康检查：后端异常（${body.status || res.status}）`, !ready);
  } catch (error) {
    ctx.showToast(`健康检查失败：${error.message || error}`, true);
  }
}

/** Bind the helix-command consumer (exactly once, at boot). */
export function bindCommandDispatch() {
  if (typeof window === "undefined") return false;
  window.addEventListener("helix-command", (event) => {
    const { id } = event.detail || {};
    switch (id) {
      case "nav:workspace":
      case "nav:quality":
      case "nav:knowledge":
      case "nav:admin":
      case "nav:settings":
        ctx.switchAppView(id.slice("nav:".length));
        break;
      case "conv:new":
        // Island mode: route through the conversation dialog island's bridge.
        window.dispatchEvent(new CustomEvent("helix-conversation-new"));
        break;
      case "conv:refresh":
        void ctx.refreshAll();
        break;
      case "conv:convert-ticket":
        void window.HelixModules?.ticketView?.convertToTicket?.();
        break;
      case "diag:logs":
        window.dispatchEvent(new CustomEvent("helix-terminal-toggle"));
        break;
      case "diag:health":
        void checkBackendHealth();
        break;
      default:
        break;
    }
  });
  return true;
}


/** Execute a palette command: close the palette, then route view switches,
 * conversation jumps and the workspace actions. Extracted from app.js; the
 * palette close and the app.js-scoped handlers arrive through configure. */
export function runCommand(command) {
  if (!command) return;
  ctx.actions.closeCommandPalette();
  const { run } = command;
  if (run.startsWith("view:")) {
    ctx.switchAppView(run.slice("view:".length));
    return;
  }
  if (run.startsWith("conversation:")) {
    ctx.switchAppView("workspace");
    void ctx.actions.loadDetail(run.slice("conversation:".length));
    return;
  }
  const actions = {
    "action:new_conversation": () => ctx.els.newConversation?.click(),
    "action:refresh": () => ctx.refreshAll(),
    "action:toggle_theme": () => document.getElementById("themeToggle")?.click(),
    "action:toggle_lowperf": () => ctx.els.lowPerfToggle?.click(),
    "action:toggle_inspector": () => ctx.els.inspectorToggle?.click(),
    "action:save_view": () => ctx.els.saveView?.click(),
  };
  const action = actions[run];
  if (action) action();
}

export default { checkBackendHealth, bindCommandDispatch, runCommand };
