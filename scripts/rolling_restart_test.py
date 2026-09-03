"""ROADMAP 29.1 滚动重启压测(1.3 验收门:双实例滚动重启零任务丢失)。

对共享 PostgreSQL + Redis 的双实例持续写入 turn job,在写入中段由**外部**
kill 主实例(脚本只负责观察与断言;kill 动作由操作者在另一终端执行,例如停掉
主实例的 uvicorn)。写入端探测到主实例 health 不可达后自动切换备用实例继续
写入(DSN 切换).最终连 PG(ground truth)断言:

- **零丢失**:本次运行前缀标记的 job 全部到达终态(completed/failed),无 job
  停留在孤儿状态(状态机停留在非终态且已超出租约恢复窗);
- **零失败/零重复**:failed == 0;`(job_id, seq)` 在 turn_job_chunks 无重复
  (同一 turn 未被重放产出两份 chunk;语义守卫,DB 幂等本应阻止);
- **幂等唯一**:每 (tenant_id, conversation_id, idempotency_key) 仅一行;
- **审计链连续**:全链 `verify_chain` 无断裂(含滚动重启窗口写入的行)。

判定:全部通过 exit 0,否则 exit 1。租约恢复时间由 `TURN_JOB_LEASE_SECONDS`
决定(压测环境建议 30),脚本 `lease_grace` 覆盖到恢复完成。

Usage:
    python scripts/rolling_restart_test.py --base-url http://127.0.0.1:8000 \\
      --fallback-url http://127.0.0.1:8001 --write-seconds 20 --concurrency 20
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.jobs import split_stream_tokens
from scripts._console import use_utf8_console
from scripts.verify_audit_chain import verify_chain

API_KEY = "helix-demo-key"
TENANT = "demo"


@dataclass
class ProbeState:
    run_prefix: str
    start_iso: str
    stop: threading.Event = field(default_factory=threading.Event)
    primary_down_at: float | None = None
    failover_count: int = 0
    enqueued_ok: int = 0
    enqueue_fail: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime())


def _enqueue_loop(
    state: ProbeState,
    base_url: str,
    fallback_url: str,
    write_seconds: float,
    client: httpx.Client,
) -> None:
    """Create conversations and enqueue turn jobs, failing over when primary drops."""
    deadline = time.monotonic() + write_seconds
    current = base_url
    issue = 0
    while not state.stop.is_set() and time.monotonic() < deadline:
        try:
            r = client.post(
                f"{current}/api/conversations",
                json={"customer_name": f"rst-{issue}"},
                timeout=15,
            )
            if r.status_code == 201:
                cid = r.json().get("id")
            else:
                raise httpx.TransportError(f"create {r.status_code}")
            r = client.post(
                f"{current}/api/conversations/{cid}/turn-jobs",
                json={"content": "rolling restart probe turn"},
                headers={"Idempotency-Key": f"{state.run_prefix}-{uuid4().hex[:12]}"},
                timeout=15,
            )
            if r.status_code in (200, 202):
                with state.lock:
                    state.enqueued_ok += 1
            else:
                with state.lock:
                    state.enqueue_fail += 1
        except (httpx.TransportError, KeyError, TypeError, ValueError):
            # Primary unreachable (killed mid-write): fail over to the fallback.
            if current == base_url:
                current = fallback_url
                with state.lock:
                    state.failover_count += 1
                    if state.primary_down_at is None:
                        state.primary_down_at = time.monotonic()
            else:
                # Fallback also down — stop writing.
                state.stop.set()
                return
        issue += 1


def _primary_is_up(base_url: str, client: httpx.Client) -> bool:
    try:
        return client.get(f"{base_url}/health", timeout=3).status_code == 200
    except httpx.TransportError:
        return False


def _pg_verify(dsn: str, run_prefix: str, after_iso: str) -> dict[str, Any]:
    """Assert row-level invariants in Postgres (the systems of record)."""
    # psycopg's overloads default to tuple rows unless the row factory type is
    # carried through a generic annotation. This diagnostic intentionally uses
    # mapping rows throughout; make that dynamic boundary explicit once.
    conn = psycopg.connect(dsn, row_factory=cast(Any, dict_row))
    cur: Any = conn.cursor()

    def statuses() -> dict[str, int]:
        cur.execute(
            "SELECT status, count(*) AS n FROM turn_jobs "
            "WHERE idempotency_key LIKE %s AND created_at >= %s GROUP BY status",
            (f"{run_prefix}-%", after_iso),
        )
        return {row["status"]: int(row["n"]) for row in cur.fetchall()}

    by_status = statuses()
    total = sum(by_status.values())
    not_finished = total - by_status.get("completed", 0) - by_status.get("failed", 0)
    # After the lease-recovery window, any job still off a terminal state is a
    # lost task: recovery re-queues abandoned jobs and a fresh worker must have
    # finished them within lease_grace.
    lost = not_finished

    # Replay guard: a completed turn's chunks must equal exactly the
    # tokenization of its assistant reply.  A replayed turn (or an abandoned
    # partial run followed by a full re-run) would carry the reply twice, so
    # chunk_count > token_count flags it.  DB idempotency is what prevents
    # this; the count check is the semantic guard.  Chunk ``seq`` is globally
    # monotonic across runs, so a ``(job_id, seq)`` collision check cannot
    # detect a re-run — the count comparison can.
    cur.execute(
        "SELECT j.id AS job_id, j.response_json, "
        "  (SELECT count(*) FROM turn_job_chunks c WHERE c.job_id = j.id) AS chunk_count "
        "FROM turn_jobs j "
        "WHERE j.idempotency_key LIKE %s AND j.created_at >= %s AND j.status = 'completed'",
        (f"{run_prefix}-%", after_iso),
    )
    replayed: list[dict[str, Any]] = []
    for row in cur.fetchall():
        try:
            content = ((json.loads(row["response_json"]) or {}).get("assistant_message") or {}).get(
                "content"
            ) or ""
        except (TypeError, ValueError):
            content = ""
        expected = len(split_stream_tokens(content))
        if int(row["chunk_count"]) > expected:
            replayed.append(
                {"job_id": row["job_id"], "chunks": row["chunk_count"], "expected": expected}
            )

    cur.execute(
        "SELECT tenant_id, conversation_id, idempotency_key, count(*) AS n "
        "FROM turn_jobs "
        "WHERE idempotency_key LIKE %s AND created_at >= %s "
        "GROUP BY tenant_id, conversation_id, idempotency_key HAVING count(*) > 1",
        (f"{run_prefix}-%", after_iso),
    )
    dup_keys = cur.fetchall()

    # Verify the full audit hash chain — the rollover must not break it.
    cur.execute(
        "SELECT id, tenant_id, conversation_id, request_id, actor, event_type, "
        "payload_json, created_at, prev_hash, event_hash "
        "FROM audit_events ORDER BY seq ASC"
    )
    audit_rows = cur.fetchall()
    chain_breaks = verify_chain(audit_rows)
    conn.close()

    return {
        "total": total,
        "by_status": by_status,
        "not_finished": not_finished,
        "lost": lost,
        "dup_chunks": len(replayed),
        "replayed": replayed,
        "dup_keys": len(dup_keys),
        "audit_rows": len(audit_rows),
        "chain_breaks": chain_breaks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="29.1 rolling-restart load test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--fallback-url", default="http://127.0.0.1:8001")
    parser.add_argument("--write-seconds", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument(
        "--lease-grace",
        type=float,
        default=180.0,
        help="max seconds to wait for lease recovery after primary down",
    )
    parser.add_argument(
        "--database-url", default="postgresql://postgres:helix@127.0.0.1:5433/helix_load"
    )
    args = parser.parse_args()

    run_prefix = f"rst-{int(time.time())}"
    state = ProbeState(run_prefix=run_prefix, start_iso=_utc_now_iso())
    start_epoch = time.monotonic()
    print(
        f"run={run_prefix} write_window={args.write_seconds}s "
        f"concurrency={args.concurrency} base={args.base_url} fallback={args.fallback_url}\n"
        f"KILL the primary ({args.base_url}) mid-write, e.g. ~{max(3, args.write_seconds // 2)}s in."
    )

    def health_watch() -> None:
        with httpx.Client(base_url=args.base_url) as c:
            while not state.stop.is_set():
                if not _primary_is_up(args.base_url, c):
                    with state.lock:
                        if state.primary_down_at is None:
                            state.primary_down_at = time.monotonic()
                            print(
                                f"[{time.monotonic() - start_epoch:6.1f}s] "
                                f"primary DOWN detected -> failover to {args.fallback_url}"
                            )
                time.sleep(0.5)

    writers: list[threading.Thread] = []
    for index in range(args.concurrency):
        client = httpx.Client(
            base_url=args.base_url,
            timeout=20,
            headers={
                "X-API-Key": API_KEY,
                "X-Tenant-Id": TENANT,
            },
        )
        t = threading.Thread(
            target=_enqueue_loop,
            args=(state, args.base_url, args.fallback_url, args.write_seconds, client),
            name=f"writer-{index}",
            daemon=True,
        )
        t.start()
        writers.append(t)

    watcher = threading.Thread(target=health_watch, daemon=True)
    watcher.start()

    # Wait for the write window to pass and a primary failure to be observed.
    time.sleep(args.write_seconds + 1.0)
    if state.primary_down_at is None:
        print("WARNING: primary never went down during the window (kill it mid-write).")
    state.stop.set()
    for t in writers:
        t.join(timeout=10)
    watcher.join(timeout=2)

    # Verification window: allow lease recovery to re-queue abandoned jobs.
    wait_until = time.monotonic() + args.lease_grace
    ver: dict[str, Any] | None = None
    while time.monotonic() < wait_until:
        ver = _pg_verify(args.database_url, run_prefix, state.start_iso)
        if ver["not_finished"] == 0:
            break
        time.sleep(3.0)

    assert ver is not None
    elapsed = time.monotonic() - start_epoch
    print(
        f"\nrun={run_prefix} wall={elapsed:.1f}s "
        f"enqueued_ok={state.enqueued_ok} fail={state.enqueue_fail} "
        f"failovers={state.failover_count}"
    )
    print(f"pg total={ver['total']} status={ver['by_status']}")
    print(
        f"not_finished={ver['not_finished']} lost={ver['lost']} "
        f"dup_chunks={ver['dup_chunks']} dup_keys={ver['dup_keys']} "
        f"audit_rows={ver['audit_rows']} chain_breaks={len(ver['chain_breaks'])}"
    )
    if ver["replayed"]:
        print(f"replayed chunk sets: {ver['replayed']}")

    ok = (
        ver["total"] > 0
        and ver["not_finished"] == 0
        and ver["lost"] == 0
        and (ver["by_status"].get("failed", 0) == 0)
        and ver["dup_chunks"] == 0
        and ver["dup_keys"] == 0
        and ver["chain_breaks"] == []
    )
    print(
        f"\n判定: {'PASS' if ok else 'FAIL'}"
        f"(零丢失 {ver['lost'] == 0} / 零失败 {ver['by_status'].get('failed', 0) == 0} "
        f"/ 无重复 {ver['dup_chunks'] == 0 and ver['dup_keys'] == 0} "
        f"/ 审计连续 {ver['chain_breaks'] == []})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
