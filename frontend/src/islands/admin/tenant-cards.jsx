/**
 * Helix Support — admin island tenant cards: quota, members, webhooks.
 *
 * Split out of admin-island.jsx (400-line module limit). Every write
 * bridges back to legacy via helix-admin-* events so api()/showToast()/
 * window.confirm() and the reload lifecycle stay in app.js; the legacy
 * class contract (.admin-card/.admin-member/.admin-webhook) is preserved
 * for the desktop axe pass.
 */

import React, { useState } from "react";

import { ADMIN_EVENTS, CARD_IDS, ROLE_LABELS, WEBHOOK_EVENTS } from "./constants.js";
import { memberRowModel, quotaReadoutRows } from "./models.js";
import { AdminEmpty, AdminReadout, useBridge, useClearOnSaved } from "./shared.jsx";

/* ── quota card ──────────────────────────────────────────────────────── */

export function AdminQuotaCard({ quota }) {
  const bridge = useBridge();
  const [conversations, setConversations] = useState("");
  const [storageMb, setStorageMb] = useState("");
  useClearOnSaved(["quota"], () => {
    setConversations("");
    setStorageMb("");
  });
  const submit = (event) => {
    event.preventDefault();
    const body = {};
    if (conversations !== "") body.conversation_quota = Number(conversations);
    if (storageMb !== "") body.storage_quota_bytes = Number(storageMb) * 1024 * 1024;
    if (!Object.keys(body).length) return;
    bridge(ADMIN_EVENTS.SAVE_QUOTA, body);
  };
  return (
    <section className="admin-card" aria-label="租户配额">
      <h3>租户配额</h3>
      <AdminReadout id={CARD_IDS.quotaReadout} rows={quotaReadoutRows(quota || {})} />
      <form id={CARD_IDS.quotaForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">会话配额
          <input
            id={CARD_IDS.quotaConversations}
            type="number"
            min="1"
            max="10000000"
            placeholder="不修改留空"
            value={conversations}
            onChange={(e) => setConversations(e.target.value)}
          />
        </label>
        <label className="admin-field">存储配额（MB）
          <input
            id={CARD_IDS.quotaStorageMb}
            type="number"
            min="1"
            placeholder="不修改留空"
            value={storageMb}
            onChange={(e) => setStorageMb(e.target.value)}
          />
        </label>
        <button className="button button-primary" type="submit">保存配额</button>
      </form>
    </section>
  );
}

/* ── members card ────────────────────────────────────────────────────── */

export function AdminMembersCard({ members, selfActor }) {
  const bridge = useBridge();
  const [actorId, setActorId] = useState("");
  const [role, setRole] = useState("operator");
  useClearOnSaved(["members"], () => setActorId(""));
  const rows = (members || []).map((member) => memberRowModel(member, selfActor));
  const submit = (event) => {
    event.preventDefault();
    const trimmed = actorId.trim();
    if (!trimmed) return;
    bridge(ADMIN_EVENTS.INVITE_MEMBER, { actorId: trimmed, role });
  };
  return (
    <section className="admin-card" aria-label="成员">
      <h3>成员</h3>
      <ul id={CARD_IDS.memberList} className="admin-list">
        {!rows.length && <AdminEmpty>暂无成员</AdminEmpty>}
        {rows.map((row) => (
          <li className="admin-member" key={row.actorId}>
            <span className="admin-member-actor">
              {row.actorId}
              {row.isSelf ? "（你）" : ""}
            </span>
            <span className="admin-member-role">{row.roleLabel}</span>
            <span className="admin-member-actions">
              <select
                className="admin-ghost-button member-role-select"
                data-actor={row.actorId}
                aria-label="变更角色"
                disabled={row.isSelf}
                value={row.role}
                onChange={(e) =>
                  bridge(ADMIN_EVENTS.MEMBER_ROLE, { actorId: row.actorId, role: e.target.value })
                }
              >
                {Object.entries(ROLE_LABELS).map(([value, label]) => (
                  <option value={value} key={value}>{label}</option>
                ))}
              </select>
              {row.deactivated ? (
                <span className="admin-member-role">已停用</span>
              ) : (
                <button
                  type="button"
                  className="admin-ghost-button member-deactivate"
                  data-actor={row.actorId}
                  disabled={row.isSelf}
                  onClick={() => bridge(ADMIN_EVENTS.MEMBER_DEACTIVATE, { actorId: row.actorId })}
                >
                  停用
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
      <form id={CARD_IDS.memberForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">坐席标识
          <input
            id={CARD_IDS.memberActorId}
            type="text"
            maxLength={80}
            pattern="[A-Za-z0-9._:@\-]{2,}"
            title="2–80 位，仅字母、数字、. _ : @ -"
            placeholder="例如 operator-2"
            required
            value={actorId}
            onChange={(e) => setActorId(e.target.value)}
          />
        </label>
        <label className="admin-field">角色
          <select
            id={CARD_IDS.memberRole}
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            <option value="operator">客服</option>
            <option value="supervisor">主管</option>
            <option value="admin">管理员</option>
            <option value="viewer">只读</option>
            <option value="auditor">审计员</option>
            <option value="channel">渠道</option>
          </select>
        </label>
        <button className="button button-primary" type="submit">邀请成员</button>
      </form>
    </section>
  );
}

/* ── webhooks card ───────────────────────────────────────────────────── */

export function AdminWebhooksCard({ webhooks }) {
  const bridge = useBridge();
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [events, setEvents] = useState([]);
  useClearOnSaved(["webhooks"], () => {
    setUrl("");
    setSecret("");
    setEvents([]);
  });
  const toggleEvent = (event, checked) => {
    setEvents((prev) =>
      checked ? [...prev, event] : prev.filter((item) => item !== event),
    );
  };
  const submit = (event) => {
    event.preventDefault();
    const trimmedUrl = url.trim();
    const trimmedSecret = secret.trim();
    if (!trimmedUrl || !trimmedSecret || !events.length) return;
    bridge(ADMIN_EVENTS.REGISTER_WEBHOOK, {
      url: trimmedUrl,
      secret: trimmedSecret,
      events: [...events],
    });
  };
  return (
    <section className="admin-card" aria-label="Webhook">
      <h3>Webhook</h3>
      <ul id={CARD_IDS.webhookList} className="admin-list">
        {!(webhooks || []).length && <AdminEmpty>暂无 Webhook</AdminEmpty>}
        {(webhooks || []).map((hook) => (
          <li className="admin-webhook" key={hook.id}>
            <span className="admin-webhook-url" title={hook.url}>{hook.url}</span>
            <span className="admin-webhook-events">{hook.events.join(" · ")}</span>
            <button
              type="button"
              className="admin-ghost-button webhook-delete"
              data-id={hook.id}
              onClick={() => bridge(ADMIN_EVENTS.DELETE_WEBHOOK, { id: hook.id })}
            >
              删除
            </button>
          </li>
        ))}
      </ul>
      <form id={CARD_IDS.webhookForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">URL
          <input
            id={CARD_IDS.webhookUrl}
            type="url"
            maxLength={500}
            autoComplete="url"
            placeholder="https://example.com/hook"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>
        <fieldset className="admin-events">
          <legend>事件</legend>
          <div id={CARD_IDS.webhookEvents} className="admin-events-grid">
            {WEBHOOK_EVENTS.map(([event, label]) => (
              <label key={event}>
                <input
                  type="checkbox"
                  value={event}
                  checked={events.includes(event)}
                  onChange={(e) => toggleEvent(event, e.target.checked)}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="admin-field">签名密钥
          <input
            id={CARD_IDS.webhookSecret}
            type="password"
            minLength={8}
            maxLength={200}
            autoComplete="new-password"
            spellCheck={false}
            placeholder="至少 8 位"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
        </label>
        <button className="button button-primary" type="submit">注册 Webhook</button>
      </form>
    </section>
  );
}
