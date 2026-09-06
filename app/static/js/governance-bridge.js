/**
 * Helix Support — governance island bridges (2.7.0).
 *
 * The governance card's approve/reject and feedback review writes. The
 * island dispatches helix-admin-governance-* events; these handlers own
 * the api()/toast lifecycle and answer with helix-admin-saved (refetch).
 * Split from js/admin-actions.js by the 400-line module gate, mirroring
 * js/admin-report-bridge.js.
 */

let ctx = null;

/** Inject the legacy app.js singletons (api/showToast). */
export function configure(deps) {
  ctx = deps;
}

function dispatchAdminSaved(ok, domains) {
  window.dispatchEvent(new CustomEvent("helix-admin-saved", { detail: { ok, domains } }));
}

export async function governanceDecideFromIsland({ approvalId, approve } = {}) {
  if (!approvalId) return;
  let ok = false;
  try {
    await ctx.api(
      `/api/admin/governance/approvals/${encodeURIComponent(approvalId)}/decide`,
      { method: "POST", body: JSON.stringify({ approve: Boolean(approve) }) },
    );
    ok = true;
    ctx.showToast(approve ? "已批准" : "已拒绝");
  } catch (error) {
    ctx.showToast(`审批操作失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["governance-approvals"]);
  }
}

export async function governanceFeedbackReviewFromIsland({ feedbackId, accept } = {}) {
  if (!feedbackId) return;
  let ok = false;
  try {
    await ctx.api(
      `/api/admin/governance/feedback/${encodeURIComponent(feedbackId)}/review`,
      { method: "POST", body: JSON.stringify({ accept: Boolean(accept) }) },
    );
    ok = true;
    ctx.showToast(accept ? "反馈已接受" : "反馈已拒绝");
  } catch (error) {
    ctx.showToast(`反馈评审失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["governance-feedback"]);
  }
}

export default {
  governanceDecideFromIsland,
  governanceFeedbackReviewFromIsland,
};
