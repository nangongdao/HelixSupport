# ADR-003: 持久任务队列抽象（TaskQueue）

- 状态：已接受（自 1.0.0 生效）
- 日期：2026-08-14（补记）

## 背景

异步 turn 执行需要跨进程/重启存活、多实例原子分发、租约恢复。直接内联 worker 逻辑会导致重试/恢复/幂等重复实现。

## 决策

`TaskQueue` Protocol（`app/queue.py`）定义 `enqueue/dequeue/complete/fail/recover/retry/stats`；`SQLiteTaskQueue`（默认，持久表 `turn_jobs` + 租约）与 `RedisTaskQueue`（多实例）实现同一契约。`ConversationOrchestrator` 只依赖 Protocol，worker（`TurnJobWorker`）通过队列消费。

## 后果

- 优点：后端可插拔；SQLite 单实例零依赖，Redis 多实例；测试可注入内存队列。
- 代价：契约必须覆盖两个后端的语义（如 Redis 锁 vs SQLite 事务租约）；新队列后端需通过 `test_multi_instance.py` 契约。

## 相关

[ADR-002](0002-sse-seq-streaming.md)
