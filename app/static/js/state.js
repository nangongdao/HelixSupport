/**
 * Helix Support — state module (Phase 26.1)
 *
 * Pure state-reduction helpers for the queue/detail model. The legacy app.js
 * keeps its own `state` object; these reducers are the single source for
 * tests and the migration target.
 */

/**
 * Merge an updated conversation into a list, preserving order.
 * @param {Array<object>} conversations
 * @param {object} updated
 * @returns {Array<object>} new array with the conversation replaced in place
 */
export function upsertConversation(conversations, updated) {
  if (!Array.isArray(conversations)) return [updated];
  const index = conversations.findIndex((c) => c && c.id === updated.id);
  if (index === -1) return [updated, ...conversations];
  const next = conversations.slice();
  next[index] = updated;
  return next;
}

/**
 * Compute a stable signature of a conversation list so the UI can skip
 * re-rendering when nothing changed.
 * @param {Array<object>} conversations
 * @returns {string} joined signature
 */
export function queueSignature(conversations) {
  if (!Array.isArray(conversations)) return "";
  return conversations
    .map((c) => `${c.id}:${c.status}:${c.updated_at}:${c.version ?? 0}`)
    .join("|");
}

/**
 * Reduce a list of conversations by a filter predicate.
 * @param {Array<object>} conversations
 * @param {function} [filter]
 * @returns {Array<object>}
 */
export function filterConversations(conversations, filter) {
  if (!filter) return conversations;
  return conversations.filter(filter);
}

/**
 * Count conversations by status.
 * @param {Array<object>} conversations
 * @returns {{open: number, waiting_human: number, human_active: number, resolved: number}}
 */
export function countByStatus(conversations) {
  const counts = { open: 0, waiting_human: 0, human_active: 0, resolved: 0 };
  for (const c of conversations || []) {
    if (c && c.status in counts) counts[c.status] += 1;
  }
  return counts;
}

/**
 * Return the latest assistant message in a message list.
 * @param {Array<object>} messages
 * @returns {object|null}
 */
export function latestAssistant(messages) {
  if (!Array.isArray(messages)) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i] && messages[i].role === "assistant") return messages[i];
  }
  return null;
}

/**
 * Normalize conversation labels: trim, drop empties, dedupe, cap length.
 * @param {Array<string>} labels
 * @param {number} [max]
 * @returns {Array<string>}
 */
export function normalizeLabels(labels, max = 20) {
  if (!Array.isArray(labels)) return [];
  const seen = new Set();
  const out = [];
  for (const raw of labels) {
    const label = String(raw).trim();
    if (!label || seen.has(label)) continue;
    seen.add(label);
    out.push(label);
    if (out.length >= max) break;
  }
  return out;
}

export default {
  upsertConversation,
  queueSignature,
  filterConversations,
  countByStatus,
  latestAssistant,
  normalizeLabels,
};
