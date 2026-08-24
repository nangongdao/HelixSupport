// Helix Support — command palette module unit tests (UI 升级 §17.1 Ctrl+K)
// Run: node --test tests/frontend/commands.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  VIEW_COMMANDS,
  ACTION_COMMANDS,
  buildStaticCommands,
  conversationCommand,
  filterCommands,
} from "../../app/static/js/commands.js";

test("static commands cover five views and six actions", () => {
  assert.equal(VIEW_COMMANDS.length, 5);
  assert.equal(ACTION_COMMANDS.length, 6);
  const all = buildStaticCommands();
  assert.equal(all.length, 11);
  assert.deepEqual(all.slice(0, 5).map((c) => c.run), [
    "view:workspace",
    "view:quality",
    "view:knowledge",
    "view:admin",
    "view:settings",
  ]);
});

test("every command has a unique id and a run key", () => {
  const ids = buildStaticCommands().map((c) => c.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const command of buildStaticCommands()) {
    assert.ok(command.run.startsWith("view:") || command.run.startsWith("action:"));
    assert.ok(command.label.length > 0);
  }
});

test("conversationCommand builds a jump command", () => {
  const command = conversationCommand({
    id: "conv_abc",
    customer_name: "林嘉",
    channel: "web",
  });
  assert.equal(command.run, "conversation:conv_abc");
  assert.equal(command.label, "林嘉");
  assert.ok(command.keywords.includes("conv_abc"));
});

test("filterCommands matches label, keywords, and group", () => {
  const all = buildStaticCommands();
  const byLabel = filterCommands(all, "质量");
  assert.equal(byLabel.length, 1);
  assert.equal(byLabel[0].id, "view.quality");
  const byKeyword = filterCommands(all, "refresh");
  assert.equal(byKeyword[0].id, "action.refresh");
  const byGroup = filterCommands(all, "视图");
  for (const view of VIEW_COMMANDS) {
    assert.ok(byGroup.some((match) => match.id === view.id), view.id);
  }
});

test("filterCommands returns everything for an empty query", () => {
  assert.equal(filterCommands(buildStaticCommands(), "").length, 11);
  assert.equal(filterCommands(buildStaticCommands(), "   ").length, 11);
});

test("filterCommands is case-insensitive and partial", () => {
  const all = [...buildStaticCommands(), conversationCommand({ id: "conv_xyz", customer_name: "Alice" })];
  const alice = filterCommands(all, "ALICE");
  assert.equal(alice.length, 1);
  assert.equal(alice[0].label, "Alice");
  assert.equal(filterCommands(all, "工")[0].id, "view.workspace");
});
