/**
 * Helix Support — composer domain React island (D3)
 *
 * Migrates the message composer to React. Renders the customer-message and
 * operator-reply forms with the same DOM structure/class names so the
 * composer's existing event handlers (via custom events) keep working.
 *
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useState, useCallback } from "react";
import { createRoot } from "react-dom/client";

function ComposerIsland() {
  const [customerMessage, setCustomerMessage] = useState("");
  const [operatorReply, setOperatorReply] = useState("");
  const [resolved, setResolved] = useState(false);

  const sendCustomer = useCallback((e) => {
    e.preventDefault();
    if (!customerMessage.trim()) return;
    window.dispatchEvent(
      new CustomEvent("helix-composer-customer", { detail: { message: customerMessage } }),
    );
    setCustomerMessage("");
  }, [customerMessage]);

  const sendOperator = useCallback((e) => {
    e.preventDefault();
    if (!operatorReply.trim()) return;
    window.dispatchEvent(
      new CustomEvent("helix-composer-operator", { detail: { message: operatorReply } }),
    );
    setOperatorReply("");
  }, [operatorReply]);

  return (
    <div className="composer-island">
      <div className="composer-notice" hidden={!resolved}>
        会话已解决，请先重开后再发送。
      </div>
      <form className="composer customer-composer" onSubmit={sendCustomer}>
        <textarea
          rows={2}
          maxLength={4000}
          placeholder="输入一条模拟客户消息…"
          required
          value={customerMessage}
          onChange={(e) => setCustomerMessage(e.target.value)}
        />
        <button type="submit" className="button button-secondary">
          模拟客户消息
        </button>
      </form>
      <form className="composer operator-composer" onSubmit={sendOperator}>
        <div className="composer-stack">
          <textarea
            rows={3}
            maxLength={8000}
            placeholder="输入回复内容…"
            value={operatorReply}
            onChange={(e) => setOperatorReply(e.target.value)}
          />
          <div className="composer-actions">
            <button type="submit" className="button button-primary">
              发送回复
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

export function mount(element) {
  const root = createRoot(element);
  root.render(<ComposerIsland />);
}

export default { mount, ComposerIsland };
