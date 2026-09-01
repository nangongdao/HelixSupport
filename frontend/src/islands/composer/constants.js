/**
 * Helix Support — composer island bridge events + element ids.
 *
 * Split out of composer-island.jsx (400-line module limit) so the tool-bar
 * components can read them without importing the island root back — the
 * island re-exports both names, so the component test and any other importer
 * are unchanged.
 */

export const COMPOSER_EVENTS = Object.freeze({
  STATE: "helix-composer-state",
  SUBMIT: "helix-composer-submit",
  TYPING: "helix-composer-typing",
  SYNC: "helix-composer-sync",
  COPILOT: "helix-composer-copilot",
  COPILOT_SUGGEST: "helix-composer-copilot-suggest",
  COPILOT_TONE: "helix-composer-copilot-tone",
  MACRO_USE: "helix-composer-macro-use",
  ATTACHMENT_UPLOAD: "helix-composer-attachment-upload",
  ATTACHMENT_REMOVE: "helix-composer-attachment-remove",
});

export const INPUT_IDS = Object.freeze({
  customerForm: "composerFormReact",
  operatorForm: "operatorFormReact",
  customerInput: "composerInputReact",
  operatorInput: "operatorInputReact",
  // The file input MUST carry a distinct id. `<label for>` binds to the first
  // element in tree order with that id, and the yielded legacy #attachmentFile
  // (hidden, not removed) comes first — so a bare "attachmentFile" here made
  // the island's own upload label activate the legacy input instead, leaving
  // handleFileChange and the helix-composer-attachment-upload bridge dead.
  attachmentFile: "attachmentFileReact",
});

/** Mirror of legacy renderMacroSuggest matching (cannedResponses from the
 * composer state snapshot; shows at most six options for a trailing /token). */
export function macroMatches(cannedResponses, query) {
  const needle = (query || "").toLowerCase();
  return (cannedResponses || [])
    .filter((item) => {
      const shortcut = (item.shortcut || "").toLowerCase();
      const title = (item.title || "").toLowerCase();
      return !needle || shortcut.includes(needle) || title.includes(needle);
    })
    .slice(0, 6);
}
