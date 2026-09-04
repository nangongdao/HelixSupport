/**
 * Helix Support — note composer & mention suggest (app.js <500 campaign slice 19).
 *
 * The internal-note composer's legacy DOM flow: @mention autocomplete over
 * the tenant roster (loadCollaborators + renderMentionSuggest + keyboard
 * navigation + caret-based replacement), and the note form submit (which
 * delegates the write to inspector.js submitNote). Extracted from the
 * legacy app.js; the island path renders its own composer from the
 * helix-inspector-state snapshot and bridges through inspector.js.
 */

let ctx = null;
let mentionIndex = -1;
const MENTION_PATTERN = /(?:^|\s)@([A-Za-z0-9._:@/-]*)$/;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export async function loadCollaborators() {
  if (!ctx.state.me) {
    ctx.state.collaborators = [];
    return;
  }
  try {
    const payload = await ctx.api("/api/collaborators");
    ctx.state.collaborators = Array.isArray(payload) ? payload : [];
    ctx.state.collaboratorsLoadedAt = Date.now();
    // roster 迟到达时若候选已打开,重渲染一次补上(修复与输入竞态的窗口)。
    if (window.__HELIX_ISLAND_MODE__) {
      // Island mode: the note composer renders mention candidates from the
      // inspector state snapshot — republish so a late roster lands.
      window.HelixModules?.inspector?.publishInspectorState?.();
    } else if (ctx.state.mentionOpen && ctx.els.mentionSuggest) {
      renderMentionSuggest(ctx.state.mentionToken);
    }
  } catch (error) {
    // roster is best-effort; the composer still accepts plain @actor text.
    // 不设 collaboratorsLoadedAt → 下个刷新周期会重试(HIGH-2 修复)。
    ctx.state.collaborators = [];
  }
}

export function hideMentionSuggest() {
  ctx.state.mentionOpen = false;
  ctx.state.mentionToken = "";
  mentionIndex = -1;
  if (ctx.els.mentionSuggest) {
    ctx.els.mentionSuggest.hidden = true;
    ctx.els.mentionSuggest.innerHTML = "";
  }
}

export function renderMentionSuggest(token) {
  if (!ctx.els.mentionSuggest || !ctx.canOperate()) {
    hideMentionSuggest();
    return;
  }
  const needle = (token || "").toLowerCase();
  const me = ctx.state.me ? ctx.state.me.actor_id : "";
  const matches = ctx.state.collaborators
    .filter(
      (c) =>
        c.actor_id !== me &&
        (!needle || (c.actor_id || "").toLowerCase().includes(needle)),
    )
    .slice(0, 8);
  if (!matches.length) {
    hideMentionSuggest();
    return;
  }
  ctx.state.mentionToken = token;
  ctx.state.mentionOpen = true;
  ctx.els.mentionSuggest.hidden = false;
  mentionIndex = -1;
  ctx.els.mentionSuggest.innerHTML = matches
    .map(
      (c, i) =>
        `<button class="macro-option" type="button" role="option" data-mention-actor="${ctx.escapeHtml(
          c.actor_id,
        )}" data-mention-index="${i}">` +
        `<strong>${ctx.escapeHtml(c.actor_id)}</strong>` +
        `<span>${ctx.escapeHtml(ctx.roleLabels[c.role] || c.role)}</span></button>`,
    )
    .join("");
}

export function setMentionActive(i) {
  if (!ctx.state.mentionOpen || !ctx.els.mentionSuggest) return;
  const options = [...ctx.els.mentionSuggest.querySelectorAll(".macro-option")];
  if (!options.length) return;
  mentionIndex = (i + options.length) % options.length;
  options.forEach((el, idx) => {
    el.classList.toggle("is-active", idx === mentionIndex);
    if (idx === mentionIndex) el.scrollIntoView({ block: "nearest" });
  });
}

export function applyMentionFromSuggest(actorId) {
  const ta = ctx.els.noteInput;
  if (!ta) return;
  // apply 时以当前 value + caret 重新推导 @token 段,不信任 input 时捕获的
  // 位置——用户可能已用鼠标移动光标(WARNING-3 修复)。
  const caret = ta.selectionStart ?? ta.value.length;
  const head = ta.value.slice(0, caret);
  const match = head.match(MENTION_PATTERN);
  if (!match) {
    hideMentionSuggest();
    return;
  }
  const start = caret - match[0].length + match[0].lastIndexOf("@");
  if (start < 0) {
    hideMentionSuggest();
    return;
  }
  ta.value = `${ta.value.slice(0, start)}@${actorId} ${ta.value.slice(caret)}`;
  const pos = start + actorId.length + 2;
  ta.setSelectionRange(pos, pos);
  hideMentionSuggest();
  ta.focus();
}

/** Bind the legacy note composer listeners (exactly once, at boot) — these
 * only reach their DOM in a plain browser tab; the island bridges the same
 * interactions through inspector.js. */
export function bindNotes() {
  if (!ctx?.els) return false;
  ctx.els.mentionSuggest.addEventListener("click", (event) => {
    const button = event.target.closest(".macro-option");
    if (button?.dataset.mentionActor) applyMentionFromSuggest(button.dataset.mentionActor);
  });
  ctx.els.noteInput.addEventListener("input", (event) => {
    // IME 组合期间(input 事件带中间拼音/片假名)不渲染也不收起;避免候选
    // 列表干扰选字(中文客服台第一优先,HIGH-1 修复)。
    if (event.isComposing) return;
    const ta = ctx.els.noteInput;
    const caret = ta.selectionStart ?? ta.value.length;
    const head = ta.value.slice(0, caret);
    const match = head.match(MENTION_PATTERN);
    if (match) {
      mentionIndex = -1;
      renderMentionSuggest(match[1]);
    } else {
      hideMentionSuggest();
    }
  });
  ctx.els.noteInput.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (!ctx.state.mentionOpen) return;
    if (event.key === "Escape") {
      event.preventDefault();
      hideMentionSuggest();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const options = ctx.els.mentionSuggest ? ctx.els.mentionSuggest.querySelectorAll(".macro-option") : [];
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setMentionActive(mentionIndex < 0 ? (delta > 0 ? 0 : options.length - 1) : mentionIndex + delta);
      return;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      const active = ctx.els.mentionSuggest ? ctx.els.mentionSuggest.querySelector(".macro-option.is-active") : null;
      if (active?.dataset.mentionActor) {
        event.preventDefault();
        applyMentionFromSuggest(active.dataset.mentionActor);
      }
    }
  });
  ctx.els.noteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!ctx.state.selectedId) return;
    const content = ctx.els.noteInput.value.trim();
    if (!content) return;
    ctx.setFormBusy(ctx.els.noteForm, true);
    const ok = await window.HelixModules?.inspector?.submitNote?.({ content });
    if (ok) {
      ctx.els.noteInput.value = "";
      hideMentionSuggest();
    }
    ctx.setFormBusy(ctx.els.noteForm, false);
  });
  return true;
}

export default {
  loadCollaborators,
  hideMentionSuggest,
  renderMentionSuggest,
  setMentionActive,
  applyMentionFromSuggest,
  bindNotes,
};
