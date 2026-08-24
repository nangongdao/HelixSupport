/**
 * Helix Support — BroadcastChannel SSE leader election & relay (ROADMAP §18.4).
 *
 * Multiple tabs of the operator console would each open their own SSE stream
 * to `/api/events/queue`. To run a single connection, the tabs elect one
 * leader that owns the stream; followers use a BroadcastChannel. The leader
 * relays each queue event to the channel, and followers refresh through the
 * relayed payload instead of opening their own stream.
 *
 * The module is pure and DOM-free so it runs under `node --test`: the browser
 * adapter (app.js) injects the real BroadcastChannel, timers, and clock. All
 * protocol decisions happen here against the injected channel interface
 * `{ postMessage(msg), addListener(fn) -> unsubscribe }`.
 */

/** Shared, pure protocol constants the adapter also uses to name its channel. */
export const PROTOCOL = Object.freeze({
  channelName: "helix:queue-relay",
  messageKey: "hx", // envelope field that marks our frames
  kindLeader: "leader",
  kindProbe: "probe",
  kindRelinquish: "relinquish",
  kindEvent: "event",
  probeWaitMs: 120, // how long a newcomer waits for an incumbent to answer
  heartbeatMs: 10_000, // leader lease-refresh cadence
  leaseMs: 40_000, // followers drop a leader that goes quiet this long
});

/**
 * Build a protocol frame.
 * @param {string} kind
 * @param {*} [payload]
 * @param {object} [opts]
 * @param {string} [opts.from] sender client id
 * @param {number} [opts.seq] per-sender monotonic sequence
 * @returns {object}
 */
export function makeFrame(kind, payload, opts = {}) {
  const frame = { [PROTOCOL.messageKey]: 1, kind, from: opts.from, seq: opts.seq ?? 0 };
  if (payload !== undefined) frame.payload = payload;
  return frame;
}

/** True when the message is one of our frames. */
export function isFrame(message) {
  return Boolean(message) && typeof message === "object" && message[PROTOCOL.messageKey] === 1;
}

/** Decode a frame into { kind, from, seq, payload }, or null for foreign traffic. */
export function parseFrame(message) {
  if (!isFrame(message)) return null;
  return { kind: message.kind, from: message.from, seq: message.seq, payload: message.payload };
}

/**
 * One dedicated SSE connection shared across tabs.
 *
 * @param {object} deps
 * @param {{postMessage(msg): void, addListener(fn): () => void}} deps.channel
 * @param {string} deps.clientId unique per page view
 * @param {() => number} deps.now ms epoch clock (injectable)
 * @param {(fn: () => void, ms: number) => *} deps.setTimer
 * @param {(handle: *) => void} deps.clearTimer
 * @param {() => void} [deps.onBecomeLeader] fire when this tab wins leadership
 * @param {(reason: string) => void} [deps.onSteppedDown] fire on loss/abandon
 * @param {(payload: *) => void} [deps.onEvent] fire when a relayed event arrives
 * @returns {{start(): void, abandon(reason?: string): void, isLeader(): boolean, isRunning(): boolean}}
 */
export function createLeaderElector(deps) {
  const {
    channel,
    clientId,
    now,
    setTimer,
    clearTimer,
    onBecomeLeader = () => {},
    onSteppedDown = () => {},
    onEvent = () => {},
  } = deps;

  let mode = "stopped"; // "leader" | "follower" | "discovery"
  let announceTimer = null;
  let discoverTimer = null;
  let watchdogTimer = null;
  let leaderFrom = null;
  let leaderUntil = 0;
  let seq = 0;
  const lastEventSeq = new Map(); // sender id -> last handled seq
  let unsubscribe = null;

  function post(kind, payload, withSeq = false) {
    channel.postMessage(makeFrame(kind, payload, { from: clientId, seq: withSeq ? ++seq : 0 }));
  }

  function stopTimer(timerRef) {
    if (timerRef) clearTimer(timerRef);
    return null;
  }

  function clearAllTimers() {
    announceTimer = stopTimer(announceTimer);
    discoverTimer = stopTimer(discoverTimer);
    watchdogTimer = stopTimer(watchdogTimer);
  }

  function becomeLeader() {
    mode = "leader";
    leaderFrom = clientId;
    leaderUntil = now() + PROTOCOL.leaseMs;
    announceTimer = stopTimer(announceTimer);
    discoverTimer = stopTimer(discoverTimer);
    watchdogTimer = stopTimer(watchdogTimer);
    post(PROTOCOL.kindLeader, { until: leaderUntil }, true);
    announceTimer = setTimer(() => {
      leaderUntil = now() + PROTOCOL.leaseMs;
      post(PROTOCOL.kindLeader, { until: leaderUntil }, true);
    }, PROTOCOL.heartbeatMs);
    onBecomeLeader();
  }

  function probe() {
    mode = "discovery";
    announceTimer = stopTimer(announceTimer);
    discoverTimer = stopTimer(discoverTimer);
    watchdogTimer = stopTimer(watchdogTimer);
    // Arm the wait first so any incumbent that answers our probe synchronously
    // (follow()) clears the discovery timer instead of racing it.
    discoverTimer = setTimer(() => {
      discoverTimer = null;
      if (mode !== "discovery") return;
      // No incumbent answered while we waited — take over. If another tab
      // claimed at the same instant, the deterministic tiebreak collapses
      // the pair to one leader; an unresponsive incumbent whose lease has
      // not expired loses to our strictly fresher claim.
      becomeLeader();
    }, PROTOCOL.probeWaitMs);
    post(PROTOCOL.kindProbe, undefined, false);
  }

  function follow(incumbent, until) {
    mode = "follower";
    leaderFrom = incumbent;
    leaderUntil = Math.max(leaderUntil, until);
    // A usurped leader must stop announcing, or followers see two leaders.
    announceTimer = stopTimer(announceTimer);
    discoverTimer = stopTimer(discoverTimer);
    watchdogTimer = stopTimer(watchdogTimer);
    // Re-arm the watchdog relative to the freshest lease heard.
    const remaining = Math.max(1, leaderUntil - now());
    watchdogTimer = setTimer(() => {
      watchdogTimer = null;
      if (mode !== "follower") return;
      if (now() >= leaderUntil) {
        onSteppedDown("leader-watchdog");
        probe();
      }
    }, remaining);
  }

  function handleMessage(raw) {
    const frame = parseFrame(raw);
    if (!frame || (frame.from === clientId && mode === "leader")) {
      // Own announcements are never echoed by the channel to the sender, so
      // this path is for defence-in-depth only (fragmentary adapters).
      return;
    }
    if (frame.kind === PROTOCOL.kindEvent) {
      const last = lastEventSeq.get(frame.from) ?? -1;
      if (frame.seq > last) {
        lastEventSeq.set(frame.from, frame.seq);
        onEvent(frame.payload);
      }
      return;
    }
    if (frame.kind === PROTOCOL.kindLeader) {
      const until = frame.payload?.until == null ? now() + PROTOCOL.leaseMs : frame.payload.until;
      if (mode === "leader") {
        // Deterministic tiebreak so two simultaneous claims collapse to one
        // leader: the freshest lease wins; equal leases defer to the smaller
        // client id. Announcements only grow, so this converges and stays.
        const weWin = until < leaderUntil || (until === leaderUntil && clientId < frame.from);
        if (!weWin) {
          onSteppedDown("usurped");
          follow(frame.from, until);
        }
      } else {
        follow(frame.from, until);
      }
      return;
    }
    if (mode === "leader") {
      // An incumbent answers newcomer probes by refreshing its announcement.
      if (frame.kind === PROTOCOL.kindProbe) {
        post(PROTOCOL.kindLeader, { until: leaderUntil }, true);
      }
      return;
    }
    // Follower or discovery mode.
    if (frame.kind === PROTOCOL.kindRelinquish && frame.from === leaderFrom) {
      onSteppedDown("leader-relinquished");
      probe();
      return;
    }
    if (frame.kind === PROTOCOL.kindProbe && frame.from === leaderFrom && mode === "follower") {
      // Our leader is being probed; re-send the lease we heard to reassure.
      post(PROTOCOL.kindLeader, { until: leaderUntil }, false);
    }
  }

  return {
    start() {
      if (mode !== "stopped") return;
      mode = "discovery";
      unsubscribe = channel.addListener(handleMessage);
      probe();
    },
    abandon(reason = "abandon") {
      if (mode === "stopped") return;
      const wasLeader = mode === "leader";
      const previous = mode;
      // Go deaf and stopped BEFORE announcing, so a follower that re-probes
      // in response to our relinquish does not get pulled back by our answer.
      if (unsubscribe) {
        unsubscribe();
        unsubscribe = null;
      }
      mode = "stopped";
      clearAllTimers();
      if (wasLeader) post(PROTOCOL.kindRelinquish, { until: leaderUntil }, true);
      if (wasLeader || previous === "follower" || previous === "discovery") {
        onSteppedDown(reason);
      }
    },
    /** Relay one queue event as the leader to every follower. */
    relay(payload) {
      post(PROTOCOL.kindEvent, payload, true);
    },
    isLeader() {
      return mode === "leader";
    },
    isRunning() {
      return mode !== "stopped";
    },
  };
}

export default {
  PROTOCOL,
  makeFrame,
  isFrame,
  parseFrame,
  createLeaderElector,
};