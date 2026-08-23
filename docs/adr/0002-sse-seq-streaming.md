# ADR-002: SSE + 单调 seq 流式响应

- 状态：已接受（自 1.0.0 生效）
- 日期：2026-08-14（补记）

## 背景

客服助手需要逐 token 流式回复，同时回答必须持久化、可恢复、进程重启不丢。WebSocket 状态复杂且需要长连接管理；纯轮询延迟高。

## 决策

- 回复正文按 token 分块写入 `turn_job_chunks`，每块带单调递增 `seq`（迁移 4 补列 + 触发器）。
- 客户端通过 `GET /api/turn-jobs/{job_id}/events`（SSE）消费：`event: token`（渐进输出）、`event: job`（状态快照）、`ping`（保活）、`timeout`（长轮询到期）。
- 断线重连用 `after_seq` 续传，`seq` 保证不重不漏；客户端断开时 `cancel_stream` 停止后续分块写入（回复已持久化）。
- 2023（Phase 23）widget 复用同一机制：`/api/widget/sessions/{id}/stream`。

## 后果

- 优点：持久 + 流式 + 可恢复，无 WebSocket 复杂度。
- 代价：SSE 单工（客户端不能通过同连接上行）；需要 worker 逐块写库（延迟受 `TURN_JOB_STREAM_PACING_MS` 控制）。

## 相关

[ADR-001](0001-dual-backend-single-query.md)、[ADR-003](0003-queue-abstraction.md)
