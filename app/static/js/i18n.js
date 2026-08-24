/**
 * Helix Support — i18n module (Phase 26.3)
 *
 * Central language packs for UI copy. zh-CN is complete; en is a
 * translation skeleton (keys exist, many still carry the zh-CN text as
 * placeholder). The active locale is resolved from `Accept-Language` /
 * a stored user preference, falling back to zh-CN.
 *
 * Pure module: no DOM access, unit-testable with node:test.
 */

export const LOCALES = {
  "zh-CN": "zh-CN",
  en: "en",
};

/** zh-CN language pack (complete). */
const ZH_CN = {
  // queue / status
  "status.open": "自动处理中",
  "status.waiting_human": "等待人工",
  "status.human_active": "人工处理中",
  "status.resolved": "已解决",
  "status.unknown": "未知",
  "role.admin": "管理员",
  "role.supervisor": "主管",
  "role.operator": "客服",
  "role.channel": "渠道",
  "role.viewer": "只读",
  "role.auditor": "审计员",
  // common actions
  "action.new": "新建",
  "action.save": "保存",
  "action.cancel": "取消",
  "action.close": "关闭",
  "action.delete": "删除",
  "action.retry": "重试",
  "action.search": "搜索",
  "action.refresh": "刷新",
  // feedback
  "feedback.recorded": "反馈已记录",
  "feedback.helpful": "有帮助",
  "feedback.unhelpful": "无帮助",
  // conversation
  "conv.customer": "客户",
  "conv.operator": "客服",
  "conv.internal_note": "内部备注",
  "conv.internal_note_added": "内部备注已添加",
  "conv.no_messages": "尚无消息",
  "conv.created": "会话已创建",
  "conv.claimed": "会话已认领",
  "conv.reopened": "会话已重开",
  "conv.resolved": "会话已解决",
  "conv.assigned_self": "会话已转派给自己",
  "conv.need_reopen": "会话已解决，请先重开",
  "conv.priority_high": "已提升为高优先级",
  "conv.priority_normal": "已调整为普通优先级",
  "conv.entered_human_queue": "客户消息已进入人工队列",
  "conv.human_connected": "人工客服",
  "conv.human_active": "会话已接入",
  // queue
  "queue.closed": "关闭会话队列",
  "queue.open": "打开会话队列",
  "queue.all_labels": "全部标签",
  "queue.more": "加载更多",
  "queue.empty": "暂无会话",
  "queue.sla_breached": "SLA 超时",
  "queue.pending_response": "待响应",
  "queue.waiting": "待人工",
  // inspector
  "inspector.expand": "展开检查器",
  "inspector.collapse": "折叠检查器",
  // low-perf
  "lowperf.enable": "开启低配模式",
  "lowperf.disable": "关闭低配模式",
  "lowperf.enabled": "已开启低配模式",
  "lowperf.disabled": "已关闭低配模式",
  // labels / views
  "labels.save": "保存标签",
  "views.save_name": "保存视图名称",
  // dialog
  "dialog.new_conversation": "新建会话",
  "dialog.customer_name": "客户名称",
  "dialog.customer_ref": "客户身份标识 可选",
  "dialog.create": "创建会话",
  // misc
  "misc.vip_refund_risk": "VIP, 退款风险",
  "misc.supervisor": "主管",
  "misc.internal_knowledge": "内部知识",
  "misc.tool_execution": "工具执行",
  "misc.risk_labels": "风险标签",
  "misc.quality_gate": "质量门",
  "misc.route_mode": "路由模式",
  "misc.assigned": "分配",
  "misc.customer_ref": "客户标识",
  "misc.intent": "意图",
  "misc.labels": "标签",
  "misc.claimed": "认领",
  "misc.answer_feedback": "回答反馈",
  "misc.completed": "已完成",
  "misc.recorded": "已记录",
  "misc.pending_route": "待路由",
  "misc.pending_identify": "待识别",
  "misc.pending_review": "待审核",
  // languages (backlog: 多语言客服)
  "lang.zh": "中文",
  "lang.en": "English",
  "lang.ja": "日本語",
  "lang.ko": "한국어",
  "lang.ru": "Русский",
  "lang.ar": "العربية",
  "lang.hi": "हिन्दी",
  "lang.he": "עברית",
  "lang.th": "ไทย",
  "lang.el": "Ελληνικά",
  "lang.es": "Español",
  "lang.fr": "Français",
  "lang.de": "Deutsch",
  "lang.pt": "Português",
  // copilot (backlog: AI 辅助坐席)
  "copilot.suggest": "智能建议",
  "copilot.rewrite": "语气改写…",
  "copilot.tone.friendly": "亲切",
  "copilot.tone.concise": "简洁",
  "copilot.tone.professional": "专业",
  "copilot.generating": "生成中…",
  "copilot.rewriting": "改写中…",
  "copilot.rewritten": "已改写",
  "copilot.kept": "原样保留（模型不可用）",
  "copilot.empty_draft": "先输入草稿再改写",
  "copilot.failed": "生成失败",
  "copilot.no_suggestions": "暂无建议",
  // tickets (backlog: 工单化)
  "ticket.convert": "转工单",
  "ticket.subject_prompt": "转工单主题（长周期问题描述）",
  "ticket.convert_failed": "转工单失败",
  "ticket.status.open": "待处理",
  "ticket.status.in_progress": "处理中",
  "ticket.status.closed": "已关闭",
  // attachments (backlog: 语音/富媒体消息)
  "attachment.upload": "附件",
  "attachment.upload_failed": "附件上传失败",
  "attachment.remove": "移除",
  // global nav (UI 升级 §17.1)
  "nav.workspace": "工作台",
  "nav.quality": "质量看板",
  "nav.knowledge": "知识库",
  "nav.admin": "管理",
  "nav.settings": "设置",
  // command palette (UI 升级 §17.1 Ctrl+K)
  "command.placeholder": "输入命令或会话… Ctrl+K",
  "command.empty": "没有匹配的命令或会话",
  "command.group.view": "视图",
  "command.group.action": "动作",
  "command.group.conversation": "会话",
  // density (UI 升级 §17.2)
  "density.comfortable": "舒适",
  "density.compact": "紧凑",
  "density.dense": "密集",
  "density.toggle": "密度:{level}（点击切换）",
  // admin page (UI 升级 §17.3)
  "admin.denied": "当前角色无权限查看管理页。",
  "admin.quota": "租户配额",
  "admin.members": "成员",
  "admin.webhooks": "Webhook",
  "admin.refresh_failed": "管理数据加载失败",
  "admin.quota_saved": "配额已更新",
  "admin.quota_save_failed": "配额保存失败",
  "admin.member_invited": "成员已邀请",
  "admin.member_invite_failed": "邀请失败",
  "admin.member_role_failed": "角色变更失败",
  "admin.member_deactivate_failed": "停用失败",
  "admin.webhook_registered": "Webhook 已注册",
  "admin.webhook_register_failed": "Webhook 注册失败",
  "admin.webhook_delete_failed": "删除失败",
};

/** en skeleton — keys exist, text is a translation placeholder. */
const EN = Object.fromEntries(
  Object.keys(ZH_CN).map((key) => [key, key]),
);

const PACKS = {
  "zh-CN": ZH_CN,
  en: EN,
};

const STORAGE_KEY = "helix-locale";

/**
 * Resolve the active locale from a stored preference or Accept-Language.
 * @param {string|null} acceptLanguage e.g. "en-US,en;q=0.9"
 * @param {string|null} storedPreference
 * @returns {string} one of LOCALES
 */
export function resolveLocale(acceptLanguage, storedPreference = null) {
  if (storedPreference && storedPreference in LOCALES) {
    return storedPreference;
  }
  const header = acceptLanguage || "";
  if (/^en\b|^en-|(^|,)\s*en(\b|;)/i.test(header)) {
    return "en";
  }
  return "zh-CN";
}

/**
 * Translate a key in the active pack, substituting ``{name}`` placeholders
 * from ``params`` (e.g. ``t("density.toggle", { level: "紧凑" })``).
 * @param {string} key
 * @param {object} [params] placeholder values (string keeps legacy locale arg)
 * @param {string} [locale] defaults to zh-CN
 * @returns {string} the translated string (or the key when missing)
 */
export function t(key, params = {}, locale = "zh-CN") {
  if (typeof params === "string") {
    // Legacy call form: t(key, locale).
    locale = params;
    params = {};
  }
  const pack = PACKS[locale] || ZH_CN;
  const template = pack[key] ?? key;
  return Object.entries(params).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    template,
  );
}

/**
 * Language-pack integrity check: every locale must define every key that
 * zh-CN defines (the source of truth).
 * @returns {string[]} keys missing from any non-source pack
 */
export function missingKeys() {
  const source = ZH_CN;
  const missing = [];
  for (const [name, pack] of Object.entries(PACKS)) {
    if (name === "zh-CN") continue;
    for (const key of Object.keys(source)) {
      if (!(key in pack)) missing.push(`${name}:${key}`);
    }
  }
  return missing;
}

export function packFor(locale) {
  return PACKS[locale] || ZH_CN;
}

export const STORAGE = STORAGE_KEY;

export default { LOCALES, resolveLocale, t, missingKeys, packFor, STORAGE };
