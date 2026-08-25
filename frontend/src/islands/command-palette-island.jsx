/**
 * Helix Support — command palette React island (D3, §5.3)
 *
 * Upgrades the existing commands.js fuzzy-search palette to a React
 * component with Ctrl+K global shortcut, fuzzy scoring (~60 lines, no
 * library dependency), and action groups (navigation / conversation /
 * diagnostics). Uses Radix-style headless focus management (ARIA only,
 * no Radix import to keep the bundle small).
 *
 * See DESKTOP_TAURI_PLAN.md §5.3 + §D3.
 */

import React, { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { createRoot } from "react-dom/client";

/* ── fuzzy score (self-contained ~60 lines, no library) ─────────────── */

function fuzzyScore(query, target) {
  if (!query) return 1;
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  let score = 0;
  let qi = 0;
  let prevMatch = -1;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      score += prevMatch === ti - 1 ? 2 : 1; // consecutive bonus
      if (ti === 0 || t[ti - 1] === " " || t[ti - 1] === "/") score += 3; // word-start bonus
      qi++;
      prevMatch = ti;
    }
  }
  return qi === q.length ? score : 0;
}

/* ── command registry ────────────────────────────────────────────────── */

const COMMAND_GROUPS = [
  {
    label: "导航",
    commands: [
      { id: "nav:workspace", label: "工作台", hint: "切换到会话工作台" },
      { id: "nav:quality", label: "质量看板", hint: "查看质量仪表板" },
      { id: "nav:knowledge", label: "知识库", hint: "管理知识文章" },
      { id: "nav:admin", label: "管理", hint: "管理租户与成员" },
      { id: "nav:settings", label: "设置", hint: "桌面运行时信息" },
    ],
  },
  {
    label: "会话操作",
    commands: [
      { id: "conv:new", label: "新建会话", hint: "创建新的客户会话" },
      { id: "conv:refresh", label: "刷新队列", hint: "重新同步会话列表" },
      { id: "conv:convert-ticket", label: "转为工单", hint: "将当前会话转为工单" },
    ],
  },
  {
    label: "诊断命令",
    commands: [
      { id: "diag:logs", label: "服务器日志", hint: "查看 sidecar stdout（桌面版）" },
      { id: "diag:health", label: "健康检查", hint: "检查后端 /health/ready" },
    ],
  },
];

const ALL_COMMANDS = COMMAND_GROUPS.flatMap((g) =>
  g.commands.map((c) => ({ ...c, group: g.label })),
);

/* ── palette component ───────────────────────────────────────────────── */

function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef(null);

  // Global Ctrl+K toggle.
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Focus input when opened.
  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
      setQuery("");
      setActiveIndex(0);
    }
  }, [open]);

  const scored = useMemo(() => {
    const results = ALL_COMMANDS.map((cmd) => ({
      ...cmd,
      score: fuzzyScore(query, `${cmd.label} ${cmd.hint} ${cmd.group}`),
    })).filter((cmd) => cmd.score > 0);
    return results.sort((a, b) => b.score - a.score);
  }, [query]);

  const handleSelect = useCallback((cmd) => {
    // Dispatch a custom event so the legacy app.js or islands can react.
    window.dispatchEvent(new CustomEvent("helix-command", { detail: { id: cmd.id } }));
    setOpen(false);
  }, []);

  const handleKeyDown = useCallback((e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, scored.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (scored[activeIndex]) handleSelect(scored[activeIndex]);
    }
  }, [scored, activeIndex, handleSelect]);

  if (!open) return null;

  // Group results for display.
  const grouped = {};
  scored.forEach((cmd, idx) => {
    if (!grouped[cmd.group]) grouped[cmd.group] = [];
    grouped[cmd.group].push({ ...cmd, globalIndex: idx });
  });

  return (
    <div
      className="command-palette-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="命令面板"
      onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}
    >
      <div className="command-palette">
        <input
          ref={inputRef}
          type="search"
          className="command-input"
          placeholder="搜索命令…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setActiveIndex(0); }}
          onKeyDown={handleKeyDown}
          aria-label="搜索命令"
          aria-controls="command-results"
          aria-expanded="true"
        />
        <div id="command-results" className="command-results" role="listbox">
          {scored.length === 0 ? (
            <div className="command-empty">没有匹配的命令</div>
          ) : (
            Object.entries(grouped).map(([group, cmds]) => (
              <div key={group} className="command-group">
                <div className="command-group-label">{group}</div>
                {cmds.map((cmd) => (
                  <button
                    key={cmd.id}
                    type="button"
                    className={`command-item${cmd.globalIndex === activeIndex ? " is-active" : ""}`}
                    role="option"
                    aria-selected={cmd.globalIndex === activeIndex}
                    onClick={() => handleSelect(cmd)}
                    onMouseEnter={() => setActiveIndex(cmd.globalIndex)}
                  >
                    <span className="command-label">{cmd.label}</span>
                    <span className="command-hint">{cmd.hint}</span>
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function CommandPaletteIsland() {
  return <CommandPalette />;
}

export function mount(element) {
  const root = createRoot(element);
  root.render(<CommandPaletteIsland />);
}

export { fuzzyScore, ALL_COMMANDS, COMMAND_GROUPS };
export default { mount, CommandPaletteIsland, fuzzyScore };
