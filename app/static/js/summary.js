/**
 * Helix Support — summary banner model (D3 long tail slice 15).
 *
 * Derives the conversation summary banner content from the detail payload's
 * summaries list (context/disposition kinds, model vs projected source).
 * Pure and framework-free: js/renderSummaries paints the legacy banner from
 * it in a plain browser tab, and the summary island renders the same model
 * from the helix-summary-state event in the desktop shell — a single source
 * of truth for both render paths.
 */

export const SUMMARY_EVENT = "helix-summary-state";

/** Derive {visible, title, text} from a detail.summaries list. */
export function summaryModel(summaries) {
  const list = Array.isArray(summaries) ? summaries : [];
  if (!list.length) return { visible: false, title: "", text: "" };
  const context = list.find((entry) => entry.kind === "context");
  const disposition = list.find((entry) => entry.kind === "disposition");
  const entry = context || disposition;
  if (!entry) return { visible: false, title: "", text: "" };
  const title = entry.kind === "context" ? "前情摘要（接入参考）" : "处置记录草稿";
  const badge = entry.source === "model" ? "（AI 生成草稿）" : "（自动投影）";
  return { visible: true, title, text: `${entry.content}${badge}` };
}

export default { SUMMARY_EVENT, summaryModel };
