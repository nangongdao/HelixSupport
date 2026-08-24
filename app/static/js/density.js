/**
 * Helix Support — density module (UI 升级 §17.2 三档密度)
 *
 * Pure level registry for the queue/message density: comfortable (default),
 * compact, and dense. The legacy binary toggle (is-compact) is upgraded to a
 * three-state cycle persisted under "helix-queue-density"; low-perf mode
 * forces compact through `effectiveDensity` without corrupting the user's
 * stored choice. No DOM access, unit-testable with node:test.
 */

/** Density levels in cycle order. */
export const DENSITY_LEVELS = ["comfortable", "compact", "dense"];

/** Default level when nothing is stored. */
export const DEFAULT_DENSITY = "comfortable";

/**
 * Coerce any stored value into a valid level.
 * @param {string|null|undefined} value
 * @returns {string} one of DENSITY_LEVELS
 */
export function normalizeDensity(value) {
  return DENSITY_LEVELS.includes(value) ? value : DEFAULT_DENSITY;
}

/** The next level in the cycle (comfortable -> compact -> dense -> …). */
export function nextDensity(level) {
  const index = DENSITY_LEVELS.indexOf(normalizeDensity(level));
  return DENSITY_LEVELS[(index + 1) % DENSITY_LEVELS.length];
}

/**
 * The level actually applied to the document. Low-perf mode always renders
 * compact (and never dense), but the user's stored level is preserved.
 * @param {string} level the user's chosen level
 * @param {boolean} lowPerf
 */
export function effectiveDensity(level, lowPerf) {
  return lowPerf ? "compact" : normalizeDensity(level);
}

/** True when the queue/messages should render the compact layout. */
export function isCompactDensity(level, lowPerf) {
  const effective = effectiveDensity(level, lowPerf);
  return effective === "compact" || effective === "dense";
}

export default {
  DENSITY_LEVELS,
  DEFAULT_DENSITY,
  normalizeDensity,
  nextDensity,
  effectiveDensity,
  isCompactDensity,
};
