# ADR-008: M0 安全加固（OIDC 事务、DSR 隐私工作流、多实例队列 fail-closed）

- 状态：已接受（Phase 40，1.3.0 后续）
- 日期：2026-08-19

## 背景

Phase 40 集中关闭 M0 停止线安全风险（`ROADMAP_2_X.md` §5 Gate A）。三个独立风险需要
明确架构决策，分别涉及认证、隐私数据处理与消息可靠交付：

1. **SEC-001**：原 OIDC 回调流程把授权码、PKCE verifier 与会话创建分散在不同路径，
   state 无一次性绑定，存在授权码重放与非预期租户混淆风险。
2. **SEC-002**：DSR 导出与执行由同一角色完成，无审批痕迹、无幂等键，导出物可被反复
   下载；operator 也没有被明确排除在隐私权限之外。
3. **REL-001**：多实例部署（PostgreSQL + Redis）中队列不可用时旧工厂静默回退到
   SQLite，会产生重复分发与租户错配，违背停止线可靠性要求。

## 决策

- **OIDC 一次性事务（migration 28）**：新增 `auth_transactions`，每次发现授权的完整
  上下文（state、nonce、PKCE S256 verifier、`redirect_uri`、tenant_hint）在事务表中
  一次性写入；回调以原子 `consume` 消费，key 已消费即拒绝重放。RS256 为唯一允许的
  签名算法（拒绝 RSA-2048 以下与算法混淆），jwks_uri 缓存带 kid 轮换刷新；
  iss/aud/exp/iat/nonce 均强制校验。identity 只从已验证 claims + `tenant_members`
  解析，无 demo/admin 回退。
- **DSR maker-checker（migration 29）**：`data_subject_requests` 增加
  approver/approved_at/executor/executed_at/execution_summary_json/idempotency_key，
  申请人、审批人、执行人三点不重合；`(tenant_id, idempotency_key)` 唯一索引保证幂等
  重放。导出物 Fernet 加密（`DSR_EXPORT_SECRET` 派生），一次性下载 token ≤15 分钟、
  导出对象 ≤24 小时；未配置密钥时导出 501 fail-closed。`privacy:request /
  privacy:approve / privacy:execute` 权限仅 ADMIN 持有，普通 operator 无任何隐私权限。
- **多实例队列 fail-closed（REL-001）**：`create_task_queue` 在 `queue_failure_mode=
  fail_closed` 时返回 `RedisTaskQueue`（Redis 中断也让应用可启动、readiness 报告
  degraded）；所有队列操作抛 `QueueUnavailableError` → API 503 + `Retry-After: 30`，
  绝不静默降级 SQLite。`deployment_profile=multi` 强制
  PostgreSQL+Redis+fail-closed，防止拓扑覆盖；worker 在队列 not ready 时不起动。

## 后果

- 优点：授权码与 PKCE verifier 一次性绑定切割重放面；隐私操作全链路留痕且干系人
  分离，审计可按幂等键重放核验；队列故障有明确语义（503+Retry-After）而非隐性降级。
  三类风险各自有密集负向测试（OIDC 36、DSR 23、fail-closed 12）与四类 Redis 故障演练。
- 代价 / 风险：`auth_transactions` 增加回调路径一次数据库写；DSR 需维护导出物
  生命周期与 Fernet 密钥（`DSR_EXPORT_SECRET` 缺失即 501）；Redis 不可用期间异步
  intake 全部不可用，是停止线接受的取舍。
- 迁移路径：migration 27→28→29 递增，独立 SQLite 0→29 演练通过；既有会话 Cookie
  不受影响，OIDC 只在下次授权开始使用事务表。production 需要
  `DSR_EXPORT_SECRET` 与 `ENABLE_SESSION_AUTH` 配置（见 `docs/DEPLOYMENT.md`）。

## 相关

[ADR-004](0004-bff-stateless-session.md)、[ADR-003](0003-queue-abstraction.md)、
[ADR-007](0007-signed-inbound-channels.md)