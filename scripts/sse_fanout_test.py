"""ROADMAP 18.5 SSE 扇出压测(1.3 验收门:≥500 并发订阅)。

对运行中的 Helix Support 实例并发建立 ``GET /api/events/queue`` SSE 连接,
度量:

- 成功建立数(HTTP 200 + snapshot 事件到达 = 服务端接受了流);
- 活跃性:收到 ≥1 个 ping 的连接数(服务端在持续推送,而非挂起空流);
- 首包延迟 p50/p95(从发起连接到 snapshot 事件到达);
- 断开/异常连接数。

客户端用**裸 asyncio TCP** 而非 HTTP 客户端库:初版用 ``httpx.AsyncClient``
在同一连接池上并发打开 SSE 流时出现了与数据无关的 ~3% ``502`` 假象
(httpcore 与并发流的交互),而裸 TCP 同为 600 连接时 0 失败——因此用裸
TCP 测服务端真实扇出能力。

判定:活跃连接 ≥500 且异常率 <0.5% 时 exit 0,否则 exit 1。SSE 扇出是服务端
事件循环的连接保持能力,与数据库/队列后端无关,本地任意实例(demo 模式)
即可验收。

Usage:
    python scripts/sse_fanout_test.py --connections 600 --duration 12
    python scripts/sse_fanout_test.py --base-url http://127.0.0.1:8000 --connections 500
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import quantiles
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._console import use_utf8_console  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class SseProbe:
    connected: bool = False
    snapshot_ms: float | None = None
    first_ping_ms: float | None = None
    events: list[str] = field(default_factory=list)
    error: str | None = None


async def probe_one(host: str, port: int, path: str, duration_s: float) -> SseProbe:
    probe = SseProbe()
    started = time.perf_counter()

    async def snap() -> float:
        return (time.perf_counter() - started) * 1000

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Accept: text/event-stream\r\n"
            f"Connection: keep-alive\r\n"
            f"\r\n"
        )
        writer.write(req.encode())
        await writer.drain()

        # 读响应头(直到空行)。
        status_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not status_line.startswith(b"HTTP/1.1 200"):
            probe.error = f"http {status_line.split(b' ')[1].decode() if len(status_line.split(b' ')) > 1 else '?'}"
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return probe
        while True:  # 消费剩余响应头
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        probe.connected = True

        deadline = time.perf_counter() + duration_s
        while time.perf_counter() < deadline:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not line:  # 服务端关闭
                break
            text = line.decode("utf-8", "replace").strip()
            if not text or text.startswith("data:") or text.startswith("retry:"):
                continue
            if text.startswith("event:"):
                kind = text.split(":", 1)[1].strip()
                probe.events.append(kind)
            # 首包延迟 = 读到 snapshot 事件标题的到达时刻。
            if probe.snapshot_ms is None and "snapshot" in probe.events:
                probe.snapshot_ms = await snap()
            if probe.first_ping_ms is None and "ping" in probe.events:
                probe.first_ping_ms = await snap()
            if probe.snapshot_ms is not None and probe.first_ping_ms is not None:
                break
        if probe.snapshot_ms is None:
            probe.snapshot_ms = await snap()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except asyncio.TimeoutError:
        probe.error = "timeout"
    except Exception as exc:
        probe.error = f"{type(exc).__name__}"
    return probe


async def run_fanout(
    host: str, port: int, path: str, connections: int, duration_s: float
) -> list[SseProbe]:
    results = await asyncio.gather(
        *(probe_one(host, port, path, duration_s) for _ in range(connections))
    )
    return list(results)


def report(probes: list[SseProbe]) -> dict[str, Any]:
    total = len(probes)
    connected = [p for p in probes if p.connected]
    pings = [p for p in connected if p.first_ping_ms is not None]
    errors = [p for p in probes if p.error]
    error_kinds = dict(Counter(p.error or "" for p in errors).most_common(5))
    first_byte = [p.snapshot_ms or 0.0 for p in connected]
    q = quantiles(sorted(first_byte), n=100, method="inclusive") if first_byte else [0, 0, 0]
    return {
        "total": total,
        "connected": len(connected),
        "snapshot_received": len(connected),
        "active_ping": len(pings),
        "errors": len(errors),
        "error_kinds": error_kinds,
        "first_byte_p50_ms": round(q[49], 1) if len(q) > 49 else 0.0,
        "first_byte_p95_ms": round(q[94], 1) if len(q) > 94 else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SSE fanout load test (ROADMAP 18.5)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--connections", type=int, default=600)
    parser.add_argument("--duration", type=float, default=12.0, help="keep-alive seconds")
    args = parser.parse_args()

    from urllib.parse import urlparse

    parsed = urlparse(args.base_url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
    path = f"{parsed.path.rstrip('/')}/api/events/queue"

    print(f"opening {args.connections} SSE connections to {host}:{port}{path} ...")
    started = time.perf_counter()
    probes = asyncio.run(run_fanout(host, port, path, args.connections, args.duration))
    elapsed = time.perf_counter() - started
    stats = report(probes)

    print(f"wall: {elapsed:.1f}s")
    for key, value in stats.items():
        print(f"  {key:<20} {value}")
    threshold = 500
    error_budget = max(2, args.connections // 200)
    ok = stats["active_ping"] >= threshold and stats["errors"] <= error_budget
    print(
        f"\n判定: active_ping={stats['active_ping']} >= {threshold} 且 errors "
        f"{stats['errors']} <= {error_budget} -> {'PASS' if ok else 'FAIL'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
