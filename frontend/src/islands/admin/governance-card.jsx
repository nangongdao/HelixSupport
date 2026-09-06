/**
 * Helix Support — admin island governance operations card (2.7.0).
 *
 * The human operations surface for the governance plane wired in 2.5/2.6:
 * pending tool-enablement approvals (maker-checker decide buttons), the
 * staged online-feedback review queue (accept/reject), and the eval
 * dataset registry readout. Writes bridge to legacy via
 * helix-admin-governance-decide / -feedback-review (api()/toast lifecycle
 * stays in app.js, exactly like every other admin card); the saved event
 * drives the island's react-query refetch.
 */

import React from "react";

import { ADMIN_EVENTS, CARD_IDS } from "./constants.js";
import {
  governanceApprovalItems,
  governanceDatasetRows,
  governanceEvalRunItems,
  governanceFeedbackItems,
} from "./models.js";
import { AdminEmpty, AdminReadout, useBridge } from "./shared.jsx";

export function AdminGovernanceCard({ approvals, feedback, datasets, evalRuns }) {
  const bridge = useBridge();
  const approvalItems = governanceApprovalItems(approvals);
  const feedbackItems = governanceFeedbackItems(feedback);
  const datasetRows = governanceDatasetRows(datasets);
  const evalRunItems = governanceEvalRunItems(evalRuns);

  const decide = (approvalId, approve) =>
    bridge(ADMIN_EVENTS.GOVERNANCE_DECIDE, { approvalId, approve });
  const review = (feedbackId, accept) =>
    bridge(ADMIN_EVENTS.GOVERNANCE_FEEDBACK_REVIEW, { feedbackId, accept });

  return (
    <section className="admin-card" aria-label="治理操作台">
      <h3>治理操作台</h3>

      <h4>待决审批（tool_enablement 等域，maker-checker）</h4>
      <ul id={CARD_IDS.governanceApprovalsList} className="admin-list">
        {!approvalItems.length && <AdminEmpty>暂无待决审批</AdminEmpty>}
        {approvalItems.map((item) => (
          <li className="governance-row" key={item.id} data-approval-id={item.id}>
            <span className="governance-row-main">
              <span className="governance-row-subject">{item.subject}</span>
              <span className="governance-row-meta">{item.meta}</span>
            </span>
            <span className="governance-row-actions">
              <button
                type="button"
                className="admin-ghost-button"
                onClick={() => decide(item.id, true)}
              >
                批准
              </button>
              <button
                type="button"
                className="admin-ghost-button"
                onClick={() => decide(item.id, false)}
              >
                拒绝
              </button>
            </span>
          </li>
        ))}
      </ul>

      <h4>线上反馈评审队列</h4>
      <ul id={CARD_IDS.governanceFeedbackList} className="admin-list">
        {!feedbackItems.length && <AdminEmpty>暂无待评审反馈</AdminEmpty>}
        {feedbackItems.map((item) => (
          <li className="governance-row" key={item.id} data-feedback-id={item.id}>
            <span className="governance-row-main">
              <span className="governance-row-subject">{item.subject}</span>
              <span className="governance-row-meta">{item.meta}</span>
            </span>
            <span className="governance-row-actions">
              <button
                type="button"
                className="admin-ghost-button"
                onClick={() => review(item.id, true)}
              >
                接受
              </button>
              <button
                type="button"
                className="admin-ghost-button"
                onClick={() => review(item.id, false)}
              >
                拒绝
              </button>
            </span>
          </li>
        ))}
      </ul>

      <h4>评测数据集</h4>
      <AdminReadout id={CARD_IDS.governanceDatasetsReadout} rows={datasetRows} />

      <h4>评测运行</h4>
      <ul id={CARD_IDS.governanceEvalRunsList} className="admin-list">
        {!evalRunItems.length && <AdminEmpty>暂无评测运行</AdminEmpty>}
        {evalRunItems.map((item) => (
          <li className="governance-row" key={item.id}>
            <span className="governance-row-main">
              <span className="governance-row-subject">{item.subject}</span>
              <span className="governance-row-meta">{item.meta}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
