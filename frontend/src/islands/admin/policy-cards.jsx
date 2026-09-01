/**
 * Helix Support — admin island policy cards: SLA policies, routing rules.
 *
 * Split out of admin-island.jsx (400-line module limit). Both cards write
 * through the helix-admin-* bridge; the SLA card's 编辑 button only fills
 * the form (legacy sla-policy-fill parity), it does not PATCH.
 */

import React, { useState } from "react";

import { ADMIN_EVENTS, CARD_IDS } from "./constants.js";
import { routingRuleLabel, routingRuleMeta, slaPolicyLabel } from "./models.js";
import { AdminEmpty, useBridge, useClearOnSaved } from "./shared.jsx";

/* ── SLA policies card ───────────────────────────────────────────────── */

export function AdminSlaCard({ policies }) {
  const bridge = useBridge();
  const [priority, setPriority] = useState("");
  const [channel, setChannel] = useState("");
  const [firstResponse, setFirstResponse] = useState("15");
  const [resolve, setResolve] = useState("1440");
  const fillFrom = (policy) => {
    setPriority(policy.priority || "");
    setChannel(policy.channel || "");
    setFirstResponse(String(policy.first_response_minutes));
    setResolve(String(policy.resolve_minutes));
  };
  const submit = (event) => {
    event.preventDefault();
    bridge(ADMIN_EVENTS.SAVE_SLA, {
      priority: priority || null,
      channel: channel.trim() || null,
      firstResponseMinutes: Number(firstResponse || 0),
      resolveMinutes: Number(resolve || 0),
    });
  };
  return (
    <section className="admin-card" aria-label="SLA 策略">
      <h3>SLA 策略</h3>
      <ul id={CARD_IDS.slaPolicyList} className="admin-list">
        {!(policies || []).length && <AdminEmpty>暂无 SLA 策略（走全局默认）</AdminEmpty>}
        {(policies || []).map((policy) => (
          <li className="sla-rule-row" data-id={policy.id} key={policy.id}>
            <span className="sla-rule-main">
              <span className="sla-rule-title">{slaPolicyLabel(policy)}</span>
              <span className="sla-rule-meta">
                首响 {policy.first_response_minutes}min · 解决 {policy.resolve_minutes}min
              </span>
            </span>
            <span className="admin-member-actions">
              <button
                type="button"
                className="admin-ghost-button sla-policy-fill"
                data-id={policy.id}
                title="填入表单编辑"
                onClick={() => fillFrom(policy)}
              >
                编辑
              </button>
            </span>
          </li>
        ))}
      </ul>
      <form id={CARD_IDS.slaPolicyForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">适用级别
          <select id={CARD_IDS.slaPriority} value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">默认（全局）</option>
            <option value="normal">普通</option>
            <option value="high">高优</option>
          </select>
        </label>
        <label className="admin-field">渠道
          <input
            id={CARD_IDS.slaChannel}
            type="text"
            maxLength={40}
            placeholder="留空表示全部"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          />
        </label>
        <label className="admin-field">首响时限（分钟）
          <input
            id={CARD_IDS.slaFirstResponse}
            type="number"
            min="1"
            max="10080"
            value={firstResponse}
            onChange={(e) => setFirstResponse(e.target.value)}
          />
        </label>
        <label className="admin-field">解决时限（分钟）
          <input
            id={CARD_IDS.slaResolve}
            type="number"
            min="1"
            max="10080"
            value={resolve}
            onChange={(e) => setResolve(e.target.value)}
          />
        </label>
        <button className="button button-primary" type="submit">保存策略</button>
      </form>
    </section>
  );
}

/* ── routing rules card ──────────────────────────────────────────────── */

export function AdminRoutingCard({ rules, groups }) {
  const bridge = useBridge();
  const [intent, setIntent] = useState("");
  const [label, setLabel] = useState("");
  const [channel, setChannel] = useState("");
  const [groupId, setGroupId] = useState("");
  const [priority, setPriority] = useState("0");
  useClearOnSaved(["rules"], () => {
    setIntent("");
    setLabel("");
    setChannel("");
  });
  const submit = (event) => {
    event.preventDefault();
    const effectiveGroup = groupId || (groups && groups[0] ? groups[0].id : "");
    if (!effectiveGroup) return; // 桥会以 legacy 同款 toast 提示
    bridge(ADMIN_EVENTS.CREATE_RULE, {
      intent: intent.trim(),
      label: label.trim(),
      channel: channel.trim(),
      groupId: effectiveGroup,
      priority: Number(priority || 0),
    });
  };
  return (
    <section className="admin-card" aria-label="自动路由规则">
      <h3>自动路由规则</h3>
      <ul id={CARD_IDS.routingRuleList} className="admin-list">
        {!(rules || []).length && <AdminEmpty>暂无路由规则</AdminEmpty>}
        {(rules || []).map((rule) => (
          <li className="routing-rule-row" data-id={rule.id} key={rule.id}>
            <span className="routing-rule-main">
              <span className="routing-rule-title">{routingRuleLabel(rule)}</span>
              <span className="routing-rule-meta">{routingRuleMeta(rule, groups)}</span>
            </span>
            <span className="admin-member-actions">
              <button
                type="button"
                className="admin-ghost-button routing-rule-delete"
                data-id={rule.id}
                onClick={() => bridge(ADMIN_EVENTS.DELETE_RULE, { id: rule.id })}
              >
                删除
              </button>
            </span>
          </li>
        ))}
      </ul>
      <form id={CARD_IDS.routingRuleForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">意图
          <input
            id={CARD_IDS.ruleIntent}
            type="text"
            maxLength={80}
            placeholder="可选"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
          />
        </label>
        <label className="admin-field">标签
          <input
            id={CARD_IDS.ruleLabel}
            type="text"
            maxLength={80}
            placeholder="可选"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label className="admin-field">渠道
          <input
            id={CARD_IDS.ruleChannel}
            type="text"
            maxLength={40}
            placeholder="可选"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          />
        </label>
        <label className="admin-field">分配组
          <select id={CARD_IDS.ruleGroup} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
            {(groups || []).length
              ? groups.map((group) => (
                  <option value={group.id} key={group.id}>{group.name}</option>
                ))
              : <option value="">暂无坐席组（请先创建）</option>}
          </select>
        </label>
        <label className="admin-field">优先级
          <input
            id={CARD_IDS.rulePriority}
            type="number"
            min="0"
            max="1000"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
        </label>
        <button className="button button-primary" type="submit">添加规则</button>
      </form>
    </section>
  );
}
