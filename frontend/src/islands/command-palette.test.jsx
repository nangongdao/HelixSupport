/**
 * Helix Support — command palette fuzzy scorer tests (D2, §3.5)
 *
 * The fuzzyScore function is a pure scoring routine; vitest covers it
 * without a DOM. The frontend gate runs this suite (§3.5 vitest segment).
 */

import { describe, it, expect } from "vitest";
import { fuzzyScore, ALL_COMMANDS } from "../islands/command-palette-island.jsx";

describe("fuzzyScore", () => {
  it("returns a positive score for an exact prefix match", () => {
    expect(fuzzyScore("工作", "工作台")).toBeGreaterThan(0);
  });

  it("returns 1 for an empty query (matches everything)", () => {
    expect(fuzzyScore("", "anything")).toBe(1);
  });

  it("returns 0 when the query has no matching characters", () => {
    expect(fuzzyScore("zzz", "工作台")).toBe(0);
  });

  it("awards a higher score for consecutive matches", () => {
    const loose = fuzzyScore("ws", "workspace");
    const tight = fuzzyScore("wo", "workspace");
    expect(tight).toBeGreaterThan(loose);
  });

  it("scores word-start matches higher than mid-word", () => {
    const midWord = fuzzyScore("s", "workspace");
    const wordStart = fuzzyScore("w", "workspace");
    expect(wordStart).toBeGreaterThan(midWord);
  });
});

describe("ALL_COMMANDS registry", () => {
  it("contains navigation, conversation, and diagnostic groups", () => {
    const groups = new Set(ALL_COMMANDS.map((c) => c.group));
    expect(groups.has("导航")).toBe(true);
    expect(groups.has("会话操作")).toBe(true);
    expect(groups.has("诊断命令")).toBe(true);
  });

  it("every command has a unique id", () => {
    const ids = ALL_COMMANDS.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
