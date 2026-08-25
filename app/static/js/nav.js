/**
 * Helix Support — global navigation module (UI 升级 §17.1)
 *
 * Pure view registry for the left icon rail: the five top-level mount
 * points (workspace / quality / knowledge / admin / settings) and which of
 * them currently render a placeholder. No DOM access, unit-testable with
 * node:test.
 */

/** Every top-level view the nav rail can select. */
export const NAV_VIEWS = ["workspace", "quality", "knowledge", "admin", "settings"];

/** Views that render the "under construction" placeholder mount point. */
export const NAV_PLACEHOLDER_VIEWS = [];

/** True when `value` is a selectable nav view. */
export function isNavView(value) {
  return NAV_VIEWS.includes(value);
}

/** True when `value` maps to the placeholder mount point. */
export function isPlaceholderView(value) {
  return NAV_PLACEHOLDER_VIEWS.includes(value);
}

export default { NAV_VIEWS, NAV_PLACEHOLDER_VIEWS, isNavView, isPlaceholderView };
