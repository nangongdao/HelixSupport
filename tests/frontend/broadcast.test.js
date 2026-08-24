// Helix Support — broadcast leader-election/relay module unit tests (ROADMAP §18.4)
// Run: node --test tests/frontend/broadcast.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  PROTOCOL,
  makeFrame,
  isFrame,
  parseFrame,
  createLeaderElector,
} from "../../app/static/js/broadcast.js";

/** Manual clock usable as the elector's `now`. */
function createClock(initial = 1_000_000) {
  let value = initial;
  return {
    now: () => value,
    advance(ms) {
      value += ms;
      return value;
    },
  };
}

/**
 * Fake timer scheduler for ONE elector. Timers are keyed by absolute time
 * and fired by `fireDue` (which runs all timers due at the clock's current
 * time, oldest first, looping while newly-armed timers come due).
 */
function createScheduler(clock) {
  const timers = new Map(); // handle -> { at, fn }
  let nextHandle = 1;
  let frozen = false;
  return {
    setTimer(fn, ms) {
      const at = clock.now() + ms;
      const handle = nextHandle++;
      timers.set(handle, { at, fn });
      return handle;
    },
    clearTimer(handle) {
      timers.delete(handle);
    },
    freeze() {
      frozen = true; // simulate a crashed tab: drop all its timers
      timers.clear();
    },
    dueCount() {
      return timers.size;
    },
    isArmed() {
      return timers.size > 0;
    },
    fireDue() {
      while (true) {
        const due = [...timers.entries()]
          .filter(([, t]) => t.at <= clock.now())
          .sort((a, b) => a[1].at - b[1].at);
        if (!due.length) return;
        for (const [handle, t] of due) {
          if (frozen) return;
          timers.delete(handle);
          t.fn();
        }
      }
    },
  };
}

/**
 * Shared in-memory BroadcastChannel. Each elector connects with its clientId
 * so replies exclude the sender — mirroring the browser's BroadcastChannel.
 */
function createChannel() {
  const listeners = []; // [{id, fn}]
  return {
    connect(clientId) {
      const self = {
        postMessage(message) {
          for (const listener of listeners) {
            if (listener.id !== clientId) listener.fn(message);
          }
        },
        addListener(fn) {
          const entry = { id: clientId, fn };
          listeners.push(entry);
          return () => {
            const index = listeners.indexOf(entry);
            if (index >= 0) listeners.splice(index, 1);
          };
        },
      };
      self.drop = () => {
        // A closed tab loses all listeners — a real crash, not just silence.
        for (let index = listeners.length - 1; index >= 0; index -= 1) {
          if (listeners[index].id === clientId) listeners.splice(index, 1);
        }
        return self;
      };
      return self;
    },
    listenerCount() {
      return listeners.length;
    },
  };
}

function makeTab(bus, clock, clientId, hooks = {}) {
  const scheduler = createScheduler(clock);
  const adapter = bus.connect(clientId);
  const received = [];
  const elected = [];
  const steppedDown = [];
  const elector = createLeaderElector({
    channel: adapter,
    clientId,
    now: clock.now,
    setTimer: scheduler.setTimer,
    clearTimer: scheduler.clearTimer,
    onBecomeLeader: () => {
      elected.push(true);
      hooks.onBecomeLeader?.();
    },
    onSteppedDown: (reason) => {
      steppedDown.push(reason);
      hooks.onSteppedDown?.(reason);
    },
    onEvent: (payload) => {
      received.push(payload);
      hooks.onEvent?.(payload);
    },
  });
  return {
    elector,
    scheduler,
    received,
    elected,
    steppedDown,
    crash: () => {
      scheduler.freeze();
      adapter.drop();
    },
  };
}

test("PROTOCOL exposes stable channel name and timings", () => {
  assert.equal(PROTOCOL.channelName, "helix:queue-relay");
  assert.ok(PROTOCOL.leaseMs > PROTOCOL.heartbeatMs);
  assert.ok(PROTOCOL.probeWaitMs > 0);
});

test("makeFrame/parseFrame round-trip and reject foreign traffic", () => {
  const frame = makeFrame(PROTOCOL.kindEvent, { type: "queue" }, { from: "tab-a", seq: 7 });
  assert.equal(isFrame(frame), true);
  assert.deepEqual(parseFrame(frame), {
    kind: PROTOCOL.kindEvent,
    from: "tab-a",
    seq: 7,
    payload: { type: "queue" },
  });
  assert.equal(parseFrame({ type: "message" }), null);
  assert.equal(parseFrame(null), null);
  assert.equal(parseFrame("hello"), null);
  assert.equal(isFrame({ hx: 1, kind: 5 }), true);
});

test("a lone tab claims leadership after the probe wait", () => {
  const clock = createClock();
  const bus = createChannel();
  const tab = makeTab(bus, clock, "solo");
  tab.elector.start();
  assert.equal(tab.elector.isLeader(), false);
  clock.advance(PROTOCOL.probeWaitMs);
  tab.scheduler.fireDue();
  assert.equal(tab.elector.isLeader(), true);
  assert.deepEqual(tab.elected, [true]);
});

test("a late tab becomes a follower under an incumbent leader", () => {
  const clock = createClock();
  const bus = createChannel();
  const leader = makeTab(bus, clock, "A");
  leader.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  leader.scheduler.fireDue();

  const follower = makeTab(bus, clock, "B");
  follower.elector.start();
  // B's probe must be answered within the wait by A.
  clock.advance(PROTOCOL.probeWaitMs);
  follower.scheduler.fireDue();
  leader.scheduler.fireDue();

  assert.equal(leader.elector.isLeader(), true);
  assert.equal(follower.elector.isLeader(), false);
  assert.deepEqual(follower.elected, []);
});

test("heartbeats keep a follower loyal across the lease window", () => {
  const clock = createClock();
  const bus = createChannel();
  const leader = makeTab(bus, clock, "A");
  const follower = makeTab(bus, clock, "B");
  leader.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  leader.scheduler.fireDue();
  follower.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  follower.scheduler.fireDue();
  leader.scheduler.fireDue();
  assert.equal(follower.elector.isLeader(), false);

  // Advance past the initial lease; the leader's heartbeats (every
  // heartbeatMs) refresh it, so the follower must never hit the watchdog.
  clock.advance(PROTOCOL.leaseMs + PROTOCOL.heartbeatMs);
  leader.scheduler.fireDue();
  follower.scheduler.fireDue();
  assert.equal(leader.elector.isLeader(), true);
  assert.equal(follower.elector.isLeader(), false);
  assert.deepEqual(follower.steppedDown, []);
});

test("relinquish hands leadership over immediately", () => {
  const clock = createClock();
  const bus = createChannel();
  const leader = makeTab(bus, clock, "A");
  const follower = makeTab(bus, clock, "B");
  leader.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  leader.scheduler.fireDue();
  follower.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  follower.scheduler.fireDue();
  leader.scheduler.fireDue();
  assert.deepEqual(follower.elected, []);

  leader.elector.abandon("unload");
  // B re-probes and, with A gone, takes over after the probe wait — no
  // waiting on A's old lease.
  clock.advance(PROTOCOL.probeWaitMs);
  follower.scheduler.fireDue();
  assert.equal(follower.elector.isLeader(), true);
  assert.equal(leader.elector.isRunning(), false);
  assert.deepEqual(follower.elected, [true]);
  assert.deepEqual(follower.steppedDown, ["leader-relinquished"]);
});

test("simultaneous claims collapse to a single leader deterministically", () => {
  const clock = createClock();
  const bus = createChannel();
  const smaller = makeTab(bus, clock, "a-tab");
  const larger = makeTab(bus, clock, "z-tab");
  smaller.elector.start();
  larger.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  smaller.scheduler.fireDue();
  larger.scheduler.fireDue();

  // The synchronous channel delivers the first claim immediately, so the
  // later tab defers before it can lead — the election must converge to
  // exactly one leader (the lexicographically smaller id on a lease tie).
  const leaders = [smaller, larger].filter((tab) => tab.elector.isLeader());
  assert.equal(leaders.length, 1);
  assert.equal(leaders[0], smaller);
  assert.equal(larger.elector.isLeader(), false);
});

test("a crashed leader is re-elected after its lease expires", () => {
  const clock = createClock();
  const bus = createChannel();
  const leader = makeTab(bus, clock, "A");
  const follower = makeTab(bus, clock, "B");
  leader.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  leader.scheduler.fireDue();
  follower.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  follower.scheduler.fireDue();
  leader.scheduler.fireDue(); // B now follows A's initial announcement
  assert.equal(follower.elector.isLeader(), false);

  // Give both one heartbeat round so B's watchdog anchors to A's freshest
  // lease, then "crash" A: drop its timers AND its listener (no relinquish).
  clock.advance(PROTOCOL.heartbeatMs);
  leader.scheduler.fireDue();
  follower.scheduler.fireDue();
  leader.crash();

  // Advance past the freshest lease B heard; the watchdog fires and re-elects.
  clock.advance(PROTOCOL.leaseMs);
  follower.scheduler.fireDue();
  assert.ok(["leader-watchdog"].every((r) => follower.steppedDown.includes(r)));
  // Follower is now re-electing; complete the takeover after the probe wait.
  clock.advance(PROTOCOL.probeWaitMs);
  follower.scheduler.fireDue();
  assert.equal(follower.elector.isLeader(), true);
});

test("the leader relays queue events that followers apply in order", () => {
  const clock = createClock();
  const bus = createChannel();
  const leader = makeTab(bus, clock, "A");
  const follower = makeTab(bus, clock, "B");
  leader.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  leader.scheduler.fireDue();
  follower.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  follower.scheduler.fireDue();
  leader.scheduler.fireDue();

  leader.elector.relay({ type: "queue", cursor: 1 });
  leader.elector.relay({ type: "queue", cursor: 2 });
  assert.deepEqual(follower.received, [
    { type: "queue", cursor: 1 },
    { type: "queue", cursor: 2 },
  ]);
});

test("duplicate or replayed event frames are dropped by sequence", () => {
  const clock = createClock();
  const bus = createChannel();
  const leader = makeTab(bus, clock, "A");
  const follower = makeTab(bus, clock, "B");
  leader.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  leader.scheduler.fireDue();
  follower.elector.start();
  clock.advance(PROTOCOL.probeWaitMs);
  follower.scheduler.fireDue();
  leader.scheduler.fireDue();

  leader.elector.relay({ type: "queue", cursor: 10 });
  leader.elector.relay({ type: "queue", cursor: 11 });
  // Replay an older frame directly on the channel.
  bus.connect("A").postMessage(makeFrame(PROTOCOL.kindEvent, { type: "queue", cursor: 10 }, { from: "A", seq: 1 }));
  assert.deepEqual(follower.received, [
    { type: "queue", cursor: 10 },
    { type: "queue", cursor: 11 },
  ]);
});

test("foreign channel traffic never disturbs the election", () => {
  const clock = createClock();
  const bus = createChannel();
  const tab = makeTab(bus, clock, "A");
  tab.elector.start();
  bus.connect("other").postMessage({ app: "unrelated", index: 0 });
  bus.connect("other").postMessage("[not an object]");
  clock.advance(PROTOCOL.probeWaitMs);
  tab.scheduler.fireDue();
  assert.equal(tab.elector.isLeader(), true);
});