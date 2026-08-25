/**
 * Helix Support — xterm.js terminal island (D4)
 *
 * Built-in diagnostic terminal for tenant admins/platform operators.
 * Renders a bottom drawer (40% height, Ctrl+` toggle) with xterm.js +
 * fit/webgl/search addons. Production mode uses whitelist diagnostic
 * commands; DEBUG builds enable an interactive PTY via portable-pty.
 *
 * RBAC: only admin/platform roles see the terminal entry (frontend hides
 * it + Rust enforces capability). See DESKTOP_TAURI_PLAN.md §4.
 */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { createRoot } from "react-dom/client";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import { SearchAddon } from "@xterm/addon-search";
import "@xterm/xterm/css/xterm.css";

const TERMINAL_HEIGHT_PERCENT = 40;
const RING_BUFFER_MAX = 5 * 1024 * 1024; // 5MB output ring buffer (§4.1)

/* ── ANSI theme (follows tokens.css dark/light) ──────────────────────── */

const DARK_THEME = {
  background: "#0a0d12",
  foreground: "#e6edf3",
  cursor: "#7da3ff",
  selectionBackground: "rgba(59, 107, 240, 0.25)",
  black: "#0a0d12",
  red: "#e5736b",
  green: "#7fb981",
  yellow: "#e0a83a",
  blue: "#6cb6ff",
  magenta: "#b49af5",
  cyan: "#56d4d0",
  white: "#e6edf3",
  brightBlack: "#8b95a3",
  brightRed: "#ff8b83",
  brightGreen: "#9fd6a1",
  brightYellow: "#f5c460",
  brightBlue: "#8cc4ff",
  brightMagenta: "#ccb8f7",
  brightCyan: "#7fe0dc",
  brightWhite: "#ffffff",
};

const LIGHT_THEME = {
  ...DARK_THEME,
  background: "#f5f7fa",
  foreground: "#1a2027",
  cursor: "#2347c4",
  selectionBackground: "rgba(35, 71, 196, 0.15)",
  black: "#1a2027",
  white: "#1a2027",
  brightBlack: "#5d6675",
  brightWhite: "#0a0d12",
};

function TerminalIsland() {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("diagnostic"); // diagnostic | pty
  const [diagnostics, setDiagnostics] = useState([]);
  const [sessionId] = useState(() => `pty-${Date.now()}`);
  const containerRef = useRef(null);
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const ringBufferRef = useRef(0);

  // Toggle with Ctrl+`
  useEffect(() => {
    const handler = (e) => {
      if (e.ctrlKey && e.key === "`") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Load diagnostic command list from Rust. The caller's role is passed
  // for the Rust-side RBAC check (defense-in-depth, §4.1).
  useEffect(() => {
    if (!open) return;
    const loadDiagnostics = async () => {
      try {
        if (window.__TAURI_INTERNALS__?.invoke) {
          const role = window.__HELIX_ROLE__ || "guest";
          const cmds = await window.__TAURI_INTERNALS__.invoke("list_diagnostics", { role });
          setDiagnostics(cmds);
        }
      } catch (err) {
        console.warn("[terminal] failed to load diagnostics:", err);
      }
    };
    loadDiagnostics();
  }, [open]);

  // Initialize xterm.js terminal when opened.
  useEffect(() => {
    if (!open || !containerRef.current || termRef.current) return;

    const isDark = document.documentElement.getAttribute("data-theme") !== "light";
    const term = new Terminal({
      theme: isDark ? DARK_THEME : LIGHT_THEME,
      fontSize: 13,
      fontFamily: '"SFMono-Regular", "Cascadia Mono", Consolas, monospace',
      cursorBlink: true,
      scrollback: 10000,
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new SearchAddon());
    try {
      term.loadAddon(new WebglAddon());
    } catch {
      // WebGL not available; fall back to canvas renderer.
    }

    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;
    fitRef.current = fitAddon;

    term.writeln("\x1b[1;36mHelix Support 诊断终端\x1b[0m");
    term.writeln("\x1b[2mCtrl+` 切换 · 白名单命令模式\x1b[0m");
    term.writeln("");

    // Handle resize.
    const resizeObserver = new ResizeObserver(() => {
      if (fitRef.current) fitRef.current.fit();
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      term.dispose();
      termRef.current = null;
    };
  }, [open]);

  const runDiagnostic = useCallback(async (cmdId) => {
    const term = termRef.current;
    if (!term) return;
    term.writeln(`\x1b[1;33m> ${cmdId}\x1b[0m`);
    try {
      if (window.__TAURI_INTERNALS__?.invoke) {
        const role = window.__HELIX_ROLE__ || "guest";
        const result = await window.__TAURI_INTERNALS__.invoke("run_diagnostic", {
          commandId: cmdId,
          role,
        });
        if (result.stdout) term.write(result.stdout);
        if (result.stderr) term.writeln(`\x1b[31m${result.stderr}\x1b[0m`);
        ringBufferRef.current += result.stdout.length + result.stderr.length;
        // Enforce ring buffer cap.
        if (ringBufferRef.current > RING_BUFFER_MAX) {
          term.clear();
          ringBufferRef.current = 0;
        }
      }
    } catch (err) {
      term.writeln(`\x1b[31m错误：${String(err)}\x1b[0m`);
    }
    term.writeln("");
  }, []);

  if (!open) return null;

  return (
    <div className="terminal-drawer" style={{ height: `${TERMINAL_HEIGHT_PERCENT}%` }}>
      <div className="terminal-drawer-header">
        <span className="terminal-drawer-title">诊断终端</span>
        <select
          className="terminal-profile-select"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          aria-label="终端模式"
        >
          <option value="diagnostic">诊断命令</option>
          <option value="pty">PowerShell（DEBUG）</option>
        </select>
        <button
          className="icon-button"
          type="button"
          onClick={() => setOpen(false)}
          title="关闭终端"
          aria-label="关闭终端"
        >
          ✕
        </button>
      </div>
      {mode === "diagnostic" && (
        <div className="terminal-diagnostic-bar">
          {diagnostics.map((cmd) => (
            <button
              key={cmd.id}
              className="button button-secondary terminal-diag-btn"
              type="button"
              onClick={() => runDiagnostic(cmd.id)}
            >
              {cmd.label}
            </button>
          ))}
        </div>
      )}
      <div className="terminal-container" ref={containerRef} />
    </div>
  );
}

export function mount(element) {
  const root = createRoot(element);
  root.render(<TerminalIsland />);
}

export default { mount, TerminalIsland };
