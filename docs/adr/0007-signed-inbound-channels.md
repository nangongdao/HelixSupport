# ADR-007: 签名入站消息渠道与持久幂等

- 状态：已接受（Phase 38，1.3.0 后续）
- 日期：2026-08-19

## 背景

外部消息提供商需要把客户消息送入现有异步 turn worker。入站请求不能携带可信的
租户身份，也不能依赖操作台 API key；提供商重试、同一线程首投并发和 worker 重试
必须只产生一个会话与一个客户 turn。Web Chat 的会话内幂等键不足以解决首次投递时
尚无内部会话的问题。

## 决策

- 提供 provider-neutral 的 `POST /api/channels/{account_id}/webhook`。服务端账号配置
  将 `account_id` 固定绑定到一个租户、渠道和 HMAC 密钥，请求体不接受租户字段。
- 使用 `X-Helix-Timestamp` 与 `X-Helix-Signature: sha256=<hex>` 验证原始
  `<timestamp>.<body>`；常量时间比较并限制时间偏差，未知账号和验签失败返回同一
  未授权响应。
- `channel_threads` 以 `(tenant_id, account_id, external_thread_id)` 唯一映射内部
  会话；`channel_webhook_receipts` 以 `(tenant_id, account_id, message_id)` 抢占首次
  投递。外部消息 ID 同时写入 turn job 和最终客户消息，重放返回原 job/turn。
- 入口沿用会话配额、队列背压、状态重开、审计与 TaskQueue/worker；已解决线程只有在
  活跃会话配额允许时才能重开。同一线程更换客户身份、同一消息 ID 更换正文或线程均
  返回冲突。
- 生产密钥优先从只读、被版本控制忽略的 `CHANNEL_WEBHOOKS_FILE` 加载；内联 JSON
  仅用于本地开发。密钥和原始请求体不得写入日志。

## 后果

- 优点：第三方渠道只需实现稳定的签名事件契约；SQLite/PostgreSQL 与 SQLite/Redis
  队列组合共享同一幂等链路，进程重启后映射和收据仍然有效。
- 代价 / 风险：账号配置和密钥轮换需要部署重启；时间窗要求提供商时钟同步；数据库
  会保留外部线程与消息 ID 元数据，需随会话保留策略管理。
- 迁移路径：migration 27 增加线程映射、消息收据及 `turn_jobs.channel_message_id`；
  未配置渠道账号时公开端点保持关闭式未授权，不影响现有 API key 与 Web Chat 流程。

## 相关

[ADR-001](0001-dual-backend-single-query.md)、[ADR-003](0003-queue-abstraction.md)、
[ADR-005](0005-connector-contracts.md)
