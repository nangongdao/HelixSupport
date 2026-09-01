/**
 * Helix Support — knowledge island reducer (§43.6 createState + reduce).
 *
 * The pure state machine behind the filters and the draft editor. Split out
 * of knowledge-island.jsx (400-line module limit); the island re-exports
 * both names so the component test's import surface is unchanged.
 */

import { EMPTY_DRAFT, draftFromArticle } from "./domain.js";

export function createKnowledgeState() {
  return {
    filters: { status: "all", language: "", query: "" },
    // editor === null keeps the aside hidden, which is what the single-column
    // :has(.knowledge-editor[hidden]) layout rule keys off.
    editor: null,
  };
}

export function reduceKnowledge(state, action) {
  switch (action.type) {
    case "SET_FILTER":
      return { ...state, filters: { ...state.filters, ...action.payload } };
    case "RESET_FILTERS":
      return { ...state, filters: { status: "all", language: "", query: "" } };
    case "OPEN_EDITOR":
      return {
        ...state,
        editor: {
          articleId: action.article ? action.article.id : null,
          values: action.article ? draftFromArticle(action.article) : { ...EMPTY_DRAFT },
          error: null,
          busy: false,
        },
      };
    case "CLOSE_EDITOR":
      return { ...state, editor: null };
    case "SET_FIELD":
      if (!state.editor) return state;
      return {
        ...state,
        editor: {
          ...state.editor,
          values: { ...state.editor.values, [action.name]: action.value },
        },
      };
    // Legacy resetKnowledgeEditor() also drops the editing id, turning the
    // open editor back into a blank new-draft form.
    case "RESET_EDITOR":
      if (!state.editor) return state;
      return { ...state, editor: { articleId: null, values: { ...EMPTY_DRAFT }, error: null, busy: false } };
    case "SET_EDITOR_ERROR":
      if (!state.editor) return state;
      return { ...state, editor: { ...state.editor, error: action.error, busy: false } };
    case "SET_EDITOR_BUSY":
      if (!state.editor) return state;
      return { ...state, editor: { ...state.editor, busy: action.busy } };
    default:
      return state;
  }
}
