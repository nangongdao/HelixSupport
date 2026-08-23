/**
 * Helix Support — command palette module (UI 升级 §17.1 Ctrl+K)
 *
 * Pure command registry + filtering. Static commands cover view switching
 * (the five nav mount points) and workspace actions (new conversation,
 * refresh, theme, low-perf, inspector, save view); conversation-jump
 * commands are fetched dynamically and merged at open time. No DOM access,
 * unit-testable with node:test.
 */

/**
 * @typedef {{id: string, group: string, label: string, keywords: string[], run: string}} Command
 * `run` is a stable action key the app.js dispatcher maps to a real handler;
 * the module stays side-effect free.
 */

/** View-switch commands backed by the global nav rail. */
export const VIEW_COMMANDS = [
  { id: "view.workspace", group: "视图", label: "工作台", keywords: ["workspace", "队列", "会话"], run: "view:workspace" },
  { id: "view.quality", group: "视图", label: "质量看板", keywords: ["quality", "质量"], run: "view:quality" },
  { id: "view.knowledge", group: "视图", label: "知识库", keywords: ["knowledge", "知识"], run: "view:knowledge" },
  { id: "view.admin", group: "视图", label: "管理", keywords: ["admin", "管理"], run: "view:admin" },
  { id: "view.settings", group: "视图", label: "设置", keywords: ["settings", "设置"], run: "view:settings" },
];

/** Workspace actions; each maps to an existing UI handler. */
export const ACTION_COMMANDS = [
  { id: "action.new_conversation", group: "动作", label: "新建会话", keywords: ["新建", "会话", "create"], run: "action:new_conversation" },
  { id: "action.refresh", group: "动作", label: "刷新队列", keywords: ["刷新", "refresh"], run: "action:refresh" },
  { id: "action.toggle_theme", group: "动作", label: "切换深浅主题", keywords: ["主题", "theme", "暗色", "浅色"], run: "action:toggle_theme" },
  { id: "action.toggle_lowperf", group: "动作", label: "切换低配模式", keywords: ["低配", "性能", "lowperf"], run: "action:toggle_lowperf" },
  { id: "action.toggle_inspector", group: "动作", label: "折叠/展开检查器", keywords: ["检查器", "inspector"], run: "action:toggle_inspector" },
  { id: "action.save_view", group: "动作", label: "保存当前视图", keywords: ["保存", "视图", "save view"], run: "action:save_view" },
];

/** Build the static command list (views + actions). */
export function buildStaticCommands() {
  return [...VIEW_COMMANDS, ...ACTION_COMMANDS];
}

/**
 * Convert a conversation row into a jump command (dynamic).
 * @param {{id: string, customer_name: string, channel?: string}} conversation
 */
export function conversationCommand(conversation) {
  return {
    id: `conversation:${conversation.id}`,
    group: "会话",
    label: conversation.customer_name || conversation.id,
    keywords: [conversation.id, conversation.customer_name || "", conversation.channel || ""],
    run: `conversation:${conversation.id}`,
  };
}

/**
 * Filter commands by a query against label / keywords / group.
 * Empty query returns everything (ordered: views, actions, conversations).
 * @param {Command[]} commands
 * @param {string} query
 */
export function filterCommands(commands, query) {
  const needle = (query || "").trim().toLowerCase();
  if (!needle) return commands;
  return commands.filter((command) => {
    const label = command.label.toLowerCase();
    if (label.includes(needle)) return true;
    if ((command.group || "").toLowerCase().includes(needle)) return true;
    return (command.keywords || []).some((keyword) =>
      keyword.toLowerCase().includes(needle),
    );
  });
}

export default {
  VIEW_COMMANDS,
  ACTION_COMMANDS,
  buildStaticCommands,
  conversationCommand,
  filterCommands,
};
