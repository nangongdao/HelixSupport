/**
 * Helix Support — admin island cost dashboard card (2.3.0 analytics API).
 *
 * The ninth admin card: read-only visibility over inference cost
 * attribution — cumulative spend (costs/daily), today-vs-baseline anomaly
 * status (costs/anomaly, the same evaluation the 2.4.0 drift monitor uses
 * to stop canaries), and per-agent / per-prompt-version breakdowns.
 *
 * No legacy twin exists: every other card mirrors a yielded legacy section,
 * but cost analytics shipped API-only, so this card is island-native. All
 * four queries are read-only GETs under the ["admin"] query-key prefix, so
 * the island's existing helix-admin-refresh / helix-admin-saved lifecycle
 * covers them without new bridges.
 */

import React from "react";

import { CARD_IDS } from "./constants.js";
import {
  costAnomalyModel,
  costBreakdownItems,
  costSummaryRows,
} from "./models.js";
import { AdminEmpty, AdminReadout } from "./shared.jsx";

function BreakdownList({ id, items, emptyText }) {
  return (
    <ul id={id} className="admin-list">
      {!items.length && <AdminEmpty>{emptyText}</AdminEmpty>}
      {items.map((item) => (
        <li className="cost-row" key={item.id}>
          <span className="cost-row-label">{item.label}</span>
          <span className="cost-row-meta">{item.meta}</span>
        </li>
      ))}
    </ul>
  );
}

export function AdminCostCard({ daily, agents, prompts, anomaly }) {
  const summary = costSummaryRows(daily);
  const anomalyModel = costAnomalyModel(anomaly);
  const agentItems = costBreakdownItems(agents, "agent");
  const promptItems = costBreakdownItems(prompts, "prompt_version");
  return (
    <section className="admin-card" aria-label="成本仪表盘">
      <h3>
        成本仪表盘
        <span className={`cost-status ${anomalyModel.status === "成本异常" ? "is-anomaly" : ""}`}>
          {anomalyModel.status}
        </span>
      </h3>
      <AdminReadout id={CARD_IDS.costReadout} rows={summary} />
      <AdminReadout id={CARD_IDS.costAnomalyReadout} rows={anomalyModel.rows} />
      <h4>按功能拆分</h4>
      <BreakdownList
        id={CARD_IDS.costAgentList}
        items={agentItems}
        emptyText="暂无推理成本记录"
      />
      <h4>按提示版本拆分</h4>
      <BreakdownList
        id={CARD_IDS.costPromptList}
        items={promptItems}
        emptyText="暂无归因到提示版本的记录"
      />
    </section>
  );
}
