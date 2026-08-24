# Degradation Matrix (Phase 29.3)

每个依赖不可用时的自动行为、用户可见影响、恢复动作。每格都有故障注入测试引用——该表是可靠性验收的对照依据。

## 矩阵

| 依赖 | 不可用时的自动行为 | 用户可见影响 | 恢复动作 | 故障注入测试 |
|------|------------------|-------------|---------|-------------|
| **PostgreSQL** | `/health/ready` 返回 503(DB ping 失败);API 请求返回 5xx | 服务不可用(非降级) | 修复 DB、恢复连接 | `test_request_controls_readiness_and_metrics`(readiness 503 路径);`BackupConsistencyTests` |
| **SQLite** | 连接池耗尽 → `database is locked` / 500;`/health/ready` 503 | 写入失败,读取可能降级 | 检查磁盘/锁;缩减实例 | `test_database_busy` 路径(`test_infrastructure.py`) |
| **Redis 队列** | 队列工厂自动回退 SQLite(启动时检测,带 warning) | 多实例分发降级为单实例 SQLite;功能不中断 | 修复 Redis、重启 | `RedisQueueIntegrationTests`;`test_infrastructure.py:589`(fallback 测试) |
| **模型供应商** | `ChainedModelProvider` 按序 failover;全失败 → 确定性路由(`rules_fallback`) | 语义路由降级为规则路由;回答质量可能下降但不中断 | 修复供应商、恢复 provider | `ChainedModelProviderTests`;`StreamCancellationTests` |
| **Order 连接器** | 熔断打开 → `unavailable` → 升级人工,不泄露状态 | 订单查询转人工 | 熔断冷却后半开探针自动恢复 | `OrderUnavailableTests`;`OrchestratorFaultInjectionTests` |
| **CRM 连接器** | `unavailable` → 升级人工,不查订单,留 `customers.resolve` 审计 | 未绑定客户订单转人工 | 同上 | `test_crm_unavailable_escalates_without_order_lookup` |
| **Knowledge 连接器** | 空结果/熔断 → 回退内置 FTS 检索 | 知识回答仍可(基于本地库),不误判为无资料升级 | 同上 | `KnowledgeFallbackTests` |
| **SSE 流** | 断线重连(`retry: 2000`);客户端断开 → `cancel_stream` 停止分块(回复已持久化) | 流式中断但回答完整;重连续传不重不漏 | 客户端重连 | `StreamWorkerTests`;`StreamCancellationTests` |
| **turn 队列背压** | 队列深度/租户并发超限 → 新 turn 返回 429 + Retry-After | 过载时新异步 turn 被拒,已入队排空 | 排空后自动接受 | `test_queue_depth_threshold_returns_429` |
| **审计链** | 写入失败仅影响该事件(哈希链其余完好) | 审计缺口(罕见) | 修复 DB;校验 `verify_audit_chain.py` | `AuditChainTests` |

## 不变量(所有故障下保持)

1. **无重复 turn**:入队按 `UNIQUE(tenant_id, conversation_id, idempotency_key)` 去重;同 key 重放返回原 job。
2. **无丢失任务**:turn_jobs 持久化 + 租约过期恢复(`recover_turn_jobs`);重启不丢 queued。
3. **审计连续**:哈希链在正常路径连续;篡改可检出。
4. **每会话串行**:同会话同时最多一个 processing job(claim 子查询)。

## 混沌验证

`tests/test_chaos.py` 在故障注入下断言上述不变量(杀 worker/断 Redis/模型超时/慢 DB)。滚动重启压测:`scripts/load_test.py` + 持续写入下重启实例,验证零任务丢失、零重复。
