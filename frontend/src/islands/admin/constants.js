/**
 * Helix Support — admin island domain constants.
 *
 * Split out of admin-island.jsx (D3 long tail) when that file crossed the
 * project's 400-line module limit. Values are verbatim from app.js /
 * js/admin-report.js — the legacy console and the island must label and
 * name things identically or the desktop axe pass and the class/id based
 * checks in tests/ui_admin.py stop resolving.
 */

export const WEBHOOK_EVENTS = [
  ["conversation.created", "会话创建"],
  ["conversation.escalated", "升级人工"],
  ["conversation.resolved", "会话解决"],
  ["conversation.sla_breached", "SLA 违约"],
  ["conversation.sla_impending", "SLA 临近"],
  ["report.generated", "报表生成"],
];

export const ROLE_LABELS = {
  admin: "管理员",
  supervisor: "主管",
  operator: "客服",
  channel: "渠道",
  viewer: "只读",
  auditor: "审计员",
};

export const REPORT_TYPE_LABELS = { quality: "质量报表", usage: "使用量报表" };
export const SCHEDULE_LABELS = { daily: "每日", weekly: "每周" };
export const PRIORITY_LABELS = { normal: "普通", high: "高优" };

export const ADMIN_EVENTS = Object.freeze({
  IDENTITY: "helix-identity",
  REFRESH: "helix-admin-refresh",
  SAVED: "helix-admin-saved",
  SAVE_QUOTA: "helix-admin-save-quota",
  INVITE_MEMBER: "helix-admin-invite-member",
  MEMBER_ROLE: "helix-admin-member-role",
  MEMBER_DEACTIVATE: "helix-admin-member-deactivate",
  REGISTER_WEBHOOK: "helix-admin-register-webhook",
  DELETE_WEBHOOK: "helix-admin-delete-webhook",
  CREATE_SUBSCRIPTION: "helix-admin-create-subscription",
  TOGGLE_SUBSCRIPTION: "helix-admin-toggle-subscription",
  DELETE_SUBSCRIPTION: "helix-admin-delete-subscription",
  GENERATE_REPORT: "helix-admin-generate-report",
  REPORT_GENERATED: "helix-admin-report-generated",
  SAVE_SLA: "helix-admin-save-sla",
  CREATE_RULE: "helix-admin-create-rule",
  DELETE_RULE: "helix-admin-delete-rule",
});

/** React-suffixed ids: the yielded legacy cards keep the originals. */
export const CARD_IDS = Object.freeze({
  quotaReadout: "quotaReadoutReact",
  quotaForm: "quotaFormReact",
  quotaConversations: "quotaConversationsReact",
  quotaStorageMb: "quotaStorageMbReact",
  memberList: "memberListReact",
  memberForm: "memberFormReact",
  memberActorId: "memberActorIdReact",
  memberRole: "memberRoleReact",
  webhookList: "webhookListReact",
  webhookForm: "webhookFormReact",
  webhookUrl: "webhookUrlReact",
  webhookEvents: "webhookEventsReact",
  webhookSecret: "webhookSecretReact",
  reportSubscriptionList: "reportSubscriptionListReact",
  reportSubscriptionForm: "reportSubscriptionFormReact",
  reportType: "reportTypeReact",
  reportSchedule: "reportScheduleReact",
  reportWindowDays: "reportWindowDaysReact",
  reportWebhook: "reportWebhookReact",
  reportGenerateForm: "reportGenerateFormReact",
  reportGenerateType: "reportGenerateTypeReact",
  reportGenerateWindow: "reportGenerateWindowReact",
  reportPreview: "reportPreviewReact",
  csatReadout: "csatReadoutReact",
  csatTrend: "csatTrendReact",
  slaPolicyList: "slaPolicyListReact",
  slaPolicyForm: "slaPolicyFormReact",
  slaPriority: "slaPriorityReact",
  slaChannel: "slaChannelReact",
  slaFirstResponse: "slaFirstResponseReact",
  slaResolve: "slaResolveReact",
  routingRuleList: "routingRuleListReact",
  routingRuleForm: "routingRuleFormReact",
  ruleIntent: "ruleIntentReact",
  ruleLabel: "ruleLabelReact",
  ruleChannel: "ruleChannelReact",
  ruleGroup: "ruleGroupReact",
  rulePriority: "rulePriorityReact",
});
