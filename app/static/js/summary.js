/**
 * Helix Support — summary banner model + legacy paint (D3 long tail slice 15,
 * app.js <500 campaign).
 *
 * Derives the conversation summary banner content from the detail payload's
 * summaries list (context/disposition kinds, model vs projected source).
 * summaryModel is pure and framework-free; renderSummaries paints the legacy
 * banner from it in a plain browser tab and publishes the same model on the
 * SUMMARY_EVENT in the desktop shell — a single source of truth for both
 * render paths.
 */

export const SUMMARY_EVENT = "helix-summary-state";

let els = null;

/** Inject the legacy app.js DOM elements for the legacy banner paint. */
export function configure(deps) {
  els = deps.els;
}

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

/** Paint the summary banner (legacy) or publish the model (island). */
export function renderSummaries(detail) {
  const model = summaryModel(detail.summaries);
  if (window.__HELIX_ISLAND_MODE__) {
    window.dispatchEvent(new CustomEvent(SUMMARY_EVENT, { detail: model }));
    return;
  }
  els.summaryBanner.hidden = !model.visible;
  if (!model.visible) return;
  els.summaryTitle.textContent = model.title;
  els.summaryText.textContent = model.text;
}

export default { SUMMARY_EVENT, summaryModel, configure, renderSummaries };