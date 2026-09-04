/**
 * Helix Support — conversation dialog island (D3 long tail).
 *
 * Owns the new-conversation dialog in the desktop shell. Legacy app.js
 * keeps the whole create lifecycle (POST /api/conversations, selection,
 * detail load, queue refresh); the island renders the <dialog> form and
 * bridges:
 *   helix-conversation-new     {}              ← legacy 新建 button opens it
 *   helix-conversation-create  {payload}       → legacy createConversation
 *   helix-conversation-created {ok}            ← legacy reports the outcome
 *
 * The payload contract mirrors legacy submitConversation: trimmed
 * customer_name (required), channel, and customer_ref only when non-empty.
 * showModal()/close() are imperative (native <dialog>), so the island owns
 * open/close state locally — closing needs no legacy round-trip.
 *
 * Element ids get a React suffix; the yielded legacy dialog keeps the
 * originals for the browser dual-track (tests/ui_smoke.py).
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

export const DIALOG_EVENTS = Object.freeze({
  NEW: "helix-conversation-new",
  CREATE: "helix-conversation-create",
  CREATED: "helix-conversation-created",
});

/** React-suffixed ids: the yielded legacy dialog keeps the originals. */
export const DIALOG_IDS = Object.freeze({
  form: "newConversationFormReact",
  customerName: "newCustomerNameReact",
  customerRef: "newCustomerRefReact",
  channel: "newChannelReact",
});

/**
 * Pure payload projection — parity with legacy: trimmed fields, customer_ref
 * included only when non-empty, customer_name required (empty → null, the
 * caller skips the submit).
 * @param {{customerName: string, customerRef: string, channel: string}} values
 * @returns {Object|null} API payload or null when customer_name is blank
 */
export function conversationPayload(values) {
  const customerName = String(values.customerName || "").trim();
  if (!customerName) return null;
  const payload = {
    customer_name: customerName,
    channel: values.channel || "web",
  };
  const customerRef = String(values.customerRef || "").trim();
  if (customerRef) payload.customer_ref = customerRef;
  return payload;
}

export function ConversationDialogIsland() {
  const dialogRef = useRef(null);
  const nameRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [values, setValues] = useState({ customerName: "", customerRef: "", channel: "web" });

  const setValue = (name) => (event) =>
    setValues((prev) => ({ ...prev, [name]: event.target.value }));

  const openDialog = () => {
    setValues({ customerName: "", customerRef: "", channel: "web" });
    setBusy(false);
    setOpen(true);
    // showModal() must run after the dialog element is mounted/patched.
    window.setTimeout(() => {
      const el = dialogRef.current;
      // jsdom (26) still lacks the dialog API; real browsers take the
      // modal path (top layer + backdrop + focus containment).
      if (typeof el?.showModal === "function") el.showModal();
      else if (el) el.open = true;
      nameRef.current?.focus();
    }, 0);
  };

  const closeDialog = () => {
    const el = dialogRef.current;
    if (typeof el?.close === "function") el.close();
    else if (el) el.open = false;
    setOpen(false);
  };

  useEffect(() => {
    const onNew = () => openDialog();
    const onCreated = (event) => {
      const { ok } = event.detail || {};
      if (ok) {
        closeDialog();
        return;
      }
      setBusy(false); // 桥已 toast,表单保留输入可重试
    };
    window.addEventListener(DIALOG_EVENTS.NEW, onNew);
    window.addEventListener(DIALOG_EVENTS.CREATED, onCreated);
    return () => {
      window.removeEventListener(DIALOG_EVENTS.NEW, onNew);
      window.removeEventListener(DIALOG_EVENTS.CREATED, onCreated);
    };
  }, []);

  const submit = (event) => {
    event.preventDefault();
    if (busy) return;
    const payload = conversationPayload(values);
    if (!payload) return;
    setBusy(true);
    window.dispatchEvent(new CustomEvent(DIALOG_EVENTS.CREATE, { detail: { payload } }));
  };

  return (
    <dialog ref={dialogRef} className="dialog" onClose={() => setOpen(false)}>
      <form id={DIALOG_IDS.form} method="dialog" onSubmit={submit} aria-busy={busy}>
        <div className="dialog-heading">
          <div>
            <span className="section-kicker">NEW CONTACT</span>
            <h2>新建会话</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            title="关闭"
            aria-label="关闭"
            onClick={closeDialog}
          >
            <svg className="icon"><use href="/static/icons.svg?v=1.4.0#x" /></svg>
          </button>
        </div>
        <label className="field-label" htmlFor={DIALOG_IDS.customerName}>客户名称</label>
        <input
          id={DIALOG_IDS.customerName}
          ref={nameRef}
          className="text-input"
          maxLength={80}
          required
          placeholder="例如：林嘉"
          value={values.customerName}
          onChange={setValue("customerName")}
        />
        <label className="field-label" htmlFor={DIALOG_IDS.customerRef}>
          客户身份标识 <span>可选</span>
        </label>
        <input
          id={DIALOG_IDS.customerRef}
          className="text-input"
          maxLength={80}
          placeholder="例如：CUST-1001"
          value={values.customerRef}
          onChange={setValue("customerRef")}
        />
        <label className="field-label" htmlFor={DIALOG_IDS.channel}>渠道</label>
        <select
          id={DIALOG_IDS.channel}
          className="text-input"
          value={values.channel}
          onChange={setValue("channel")}
        >
          <option value="web">Web</option>
          <option value="api">API</option>
          <option value="messaging">Messaging</option>
        </select>
        <div className="dialog-actions">
          <button className="button button-secondary" type="button" onClick={closeDialog}>
            取消
          </button>
          <button className="button button-primary" type="submit">
            <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#plus" /></svg>
            <span>创建会话</span>
          </button>
        </div>
      </form>
    </dialog>
  );
}

/**
 * Mount the conversation dialog island into a host <div>. Called by the
 * island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const root = createRoot(element);
  root.render(<ConversationDialogIsland />);
}

export default { mount, ConversationDialogIsland };
