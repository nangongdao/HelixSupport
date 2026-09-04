/**
 * Helix Support — admin island report cards: subscriptions, export, CSAT.
 *
 * Split out of admin-island.jsx (400-line module limit). The export card
 * reads `submitter` off the native event on purpose — React's synthetic
 * event does not proxy it, and legacy (js/admin-report.js bindAdminReports)
 * dispatches generate-vs-export on exactly that, so reading the synthetic
 * one would leave the export branch unreachable in real browsers.
 */

import React, { useEffect, useState } from "react";

import { ADMIN_EVENTS, CARD_IDS } from "./constants.js";
import {
  activeWebhookOptions,
  csatModel,
  reportExportUrl,
  subscriptionRowModel,
} from "./models.js";
import { AdminEmpty, AdminReadout, useBridge } from "./shared.jsx";

/* ── report subscriptions card ───────────────────────────────────────── */

export function AdminSubscriptionsCard({ subscriptions, webhooks }) {
  const bridge = useBridge();
  const options = activeWebhookOptions(webhooks);
  const [reportType, setReportType] = useState("quality");
  const [schedule, setSchedule] = useState("daily");
  const [windowDays, setWindowDays] = useState("7");
  const [webhookId, setWebhookId] = useState("");
  const submit = (event) => {
    event.preventDefault();
    const endpointId = webhookId || (options[0] ? options[0].id : "");
    if (!endpointId) return; // 桥会以 legacy 同款 toast 提示
    bridge(ADMIN_EVENTS.CREATE_SUBSCRIPTION, {
      reportType,
      schedule,
      windowDays: Number(windowDays || 7),
      webhookEndpointId: endpointId,
    });
  };
  return (
    <section className="admin-card" aria-label="报表订阅">
      <h3>报表订阅</h3>
      <ul id={CARD_IDS.reportSubscriptionList} className="admin-list">
        {!(subscriptions || []).length && <AdminEmpty>暂无报表订阅</AdminEmpty>}
        {(subscriptions || []).map((sub) => {
          const row = subscriptionRowModel(sub);
          return (
            <li className="admin-report-sub" data-id={row.id} key={row.id}>
              <span className="admin-report-sub-main">
                <span className="admin-report-sub-title">{row.title}</span>
                <span className="admin-report-sub-meta">{row.meta}</span>
              </span>
              <span className="admin-member-actions">
                <span className={`status-pill${row.active ? "" : " is-ticket-closed"}`}>
                  {row.active ? "启用" : "停用"}
                </span>
                <button
                  type="button"
                  className="admin-ghost-button report-sub-toggle"
                  data-id={row.id}
                  onClick={() => bridge(ADMIN_EVENTS.TOGGLE_SUBSCRIPTION, { id: row.id, active: !row.active })}
                >
                  {row.active ? "停用" : "启用"}
                </button>
                <button
                  type="button"
                  className="admin-ghost-button report-sub-delete"
                  data-id={row.id}
                  onClick={() => bridge(ADMIN_EVENTS.DELETE_SUBSCRIPTION, { id: row.id })}
                >
                  删除
                </button>
              </span>
            </li>
          );
        })}
      </ul>
      <form id={CARD_IDS.reportSubscriptionForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">报表
          <select id={CARD_IDS.reportType} value={reportType} onChange={(e) => setReportType(e.target.value)}>
            <option value="quality">质量报表</option>
            <option value="usage">使用量报表</option>
          </select>
        </label>
        <label className="admin-field">周期
          <select id={CARD_IDS.reportSchedule} value={schedule} onChange={(e) => setSchedule(e.target.value)}>
            <option value="daily">每日</option>
            <option value="weekly">每周</option>
          </select>
        </label>
        <label className="admin-field">窗口（天）
          <input
            id={CARD_IDS.reportWindowDays}
            type="number"
            min="1"
            max="30"
            value={windowDays}
            onChange={(e) => setWindowDays(e.target.value)}
          />
        </label>
        <label className="admin-field">Webhook 端点
          <select
            id={CARD_IDS.reportWebhook}
            value={webhookId}
            onChange={(e) => setWebhookId(e.target.value)}
          >
            {options.length
              ? options.map((hook) => (
                  <option value={hook.id} key={hook.id}>{hook.url}</option>
                ))
              : <option value="">暂无可用 Webhook（请先注册）</option>}
          </select>
        </label>
        <button className="button button-primary" type="submit">创建订阅</button>
      </form>
    </section>
  );
}

/* ── report export card ──────────────────────────────────────────────── */

export function AdminReportExportCard() {
  const bridge = useBridge();
  const [reportType, setReportType] = useState("quality");
  const [windowDays, setWindowDays] = useState("7");
  const [preview, setPreview] = useState(null);
  const generate = (event) => {
    event.preventDefault();
    setPreview(null);
    bridge(ADMIN_EVENTS.GENERATE_REPORT, {
      reportType,
      windowDays: Number(windowDays || 7),
    });
  };
  // 下载由带鉴权 cookie 的导航触发(attachment disposition)——不需要桥。
  const exportCsv = (event) => {
    event.preventDefault();
    window.location.href = reportExportUrl(reportType, Number(windowDays || 7));
  };
  useEffect(() => {
    const onGenerated = (event) => {
      const { ok, text } = event.detail || {};
      if (ok) setPreview(text);
    };
    window.addEventListener(ADMIN_EVENTS.REPORT_GENERATED, onGenerated);
    return () => window.removeEventListener(ADMIN_EVENTS.REPORT_GENERATED, onGenerated);
  }, []);
  // React's synthetic event does not proxy `submitter` — legacy reads it
  // off the native event (js/admin-report.js bindAdminReports), so the
  // island must too, or the export branch is unreachable in real browsers.
  const submit = (event) => {
    if (event.nativeEvent.submitter?.value === "export") exportCsv(event);
    else generate(event);
  };
  return (
    <section className="admin-card" aria-label="报表导出">
      <h3>报表导出</h3>
      <form id={CARD_IDS.reportGenerateForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">报表
          <select
            id={CARD_IDS.reportGenerateType}
            value={reportType}
            onChange={(e) => setReportType(e.target.value)}
          >
            <option value="quality">质量报表</option>
            <option value="usage">使用量报表</option>
          </select>
        </label>
        <label className="admin-field">窗口（天）
          <input
            id={CARD_IDS.reportGenerateWindow}
            type="number"
            min="1"
            max="30"
            value={windowDays}
            onChange={(e) => setWindowDays(e.target.value)}
          />
        </label>
        <div className="admin-form-row">
          <button className="button button-secondary" type="submit" name="mode" value="generate">生成预览</button>
          <button className="button button-secondary" type="submit" name="mode" value="export">导出 CSV</button>
        </div>
        <pre id={CARD_IDS.reportPreview} className="report-preview" hidden={preview == null}>
          {preview ?? ""}
        </pre>
      </form>
    </section>
  );
}

/* ── CSAT card ───────────────────────────────────────────────────────── */

export function AdminCsatCard({ csat }) {
  const model = csatModel(csat || {});
  return (
    <section className="admin-card" aria-label="CSAT 评分汇总">
      <h3>CSAT 评分汇总</h3>
      <AdminReadout id={CARD_IDS.csatReadout} rows={model.rows} />
      <ul id={CARD_IDS.csatTrend} className="admin-list">
        {!model.trend.length && <AdminEmpty>暂无已回收的评分</AdminEmpty>}
        {model.trend.map((day) => (
          <li className="csat-day" key={day.date}>
            <span className="csat-day-date">{day.date}</span>
            <span className="csat-day-meta">{day.meta}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
