/**
 * Helix Support — palette command dispatcher (app.js <500 campaign slice 22).
 *
 * Consumes the command-palette island's helix-command events and routes each
 * action key to its handler: nav view switches, the conversation-dialog and
 * terminal bridges, refresh, ticket conversion, and the backend health
 * check. Extracted from the legacy app.js; the handlers arrive through
 * configure because switchAppView/refreshAll are app.js-scoped.
 */

import {
  buildStaticCommands,
  conversationCommand,
  filterCommands,
} from "./commands.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

// Legacy command palette: the shell island owns Ctrl+K and yields this dialog.

const commandItems = [];
let commandIndex = 0;
let conversationCommands = [];
let conversationCommandsLoadedAt = 0;

export async function loadConversationCommands({ force = false } = {}) {
  if (!force && conversationCommandsLoadedAt && Date.now() - conversationCommandsLoadedAt < 60000) {
    return conversationCommands;
  }
  try {
    const rows = await ctx.api("/api/conversations?limit=50");
    conversationCommands.length = 0;
    conversationCommands.push(...(Array.isArray(rows) ? rows : []).map(conversationCommand));
    conversationCommandsLoadedAt = Date.now();
  } catch {
    conversationCommands.length = 0;
  }
  return conversationCommands;
}

export function renderCommandResults() {
  if (!ctx.els.commandResults) return;
  const query = ctx.els.commandInput ? ctx.els.commandInput.value : "";
  const filtered = filterCommands([...buildStaticCommands(), ...conversationCommands], query);
  commandItems.length = 0;
  commandItems.push(...filtered);
  commandIndex = Math.min(commandIndex, Math.max(0, filtered.length - 1));
  if (!filtered.length) {
    ctx.els.commandResults.innerHTML = '<div class="command-empty">没有匹配的命令或会话</div>';
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
    html += `<div class="command-group-label">${ctx.escapeHtml(group)}</div>`;
    for (const command of commands) {
      const selected = flatIndex === commandIndex;
      html += `<button type="button" class="command-item${selected ? " is-selected" : ""}" role="option" aria-selected="${selected}" data-command-index="${flatIndex}">${ctx.escapeHtml(command.label)}</button>`;
      flatIndex += 1;
    }
  }
  ctx.els.commandResults.innerHTML = html;
  ctx.els.commandResults
    .querySelectorAll(".command-item")
    .forEach((item) => item.addEventListener("click", () => runCommand(commandItems[Number(item.dataset.commandIndex)])));
}

export async function openCommandPalette() {
  if (!ctx.els.commandPalette) return;
  // Always re-arm the conversation jump list: loadConversationCommands has a
  // 60s TTL of its own, so the palette shows fresh conversations instead of
  // freezing on the first fetch for the whole session (audit D1).
  await loadConversationCommands();
  if (ctx.els.commandInput) ctx.els.commandInput.value = "";
  commandIndex = 0;
  renderCommandResults();
  if (typeof ctx.els.commandPalette.showModal === "function") {
    ctx.els.commandPalette.showModal();
  } else {
    ctx.els.commandPalette.setAttribute("open", "");
  }
  if (ctx.els.commandInput) ctx.els.commandInput.focus();
}

export function closeCommandPalette() {
  if (!ctx.els.commandPalette) return;
  if (typeof ctx.els.commandPalette.close === "function") {
    ctx.els.commandPalette.close();
  } else {
    ctx.els.commandPalette.removeAttribute("open");
  }
}

/** Ctrl+K opens (island mode yields), Escape/arrows/Enter navigate. */
export function bindCommandPalette() {
  if (typeof window === "undefined" || !ctx?.els) return false;
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      if (window.__HELIX_ISLAND_MODE__) return; // island owns the palette
      event.preventDefault();
      void openCommandPalette();
      return;
    }
    if (!ctx.els.commandPalette?.open) return;
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
  if (ctx.els.commandInput) {
    ctx.els.commandInput.addEventListener("input", () => {
      commandIndex = 0;
      renderCommandResults();
    });
  }
  return true;
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
  closeCommandPalette();
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

export default { checkBackendHealth, bindCommandDispatch, runCommand, loadConversationCommands, renderCommandResults, openCommandPalette, closeCommandPalette, bindCommandPalette };
