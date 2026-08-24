# ADR-015: PostgreSQL 行级租户隔离（RLS 纵深防御）

- 状态：已接受（Phase 43.2 contract (a)，2.0 后续）
- 日期：2026-08-22

## 背景

`ROADMAP_2_X.md` §43.2 要求在应用层租户过滤之外启用 PostgreSQL Row-Level
Security（RLS）。当前所有租户隔离都依赖应用代码在每个查询上写 `WHERE tenant_id`
谓词——单点防线。一条遗漏谓词的查询、一个被复用却残留上下文的池化连接、或一次
手写运维 SQL，都会直接变成跨租户读写。Gate D 明确要求"故意删除应用 WHERE 仍无法
跨租户、连接池 context 不串租户、管理员/break-glass 受审计"。

约束：

1. 项目保持"单查询实现双后端"（ADR-001）：SQLite 没有 RLS，隔离机制必须只在
   PostgreSQL 后端生效且不产生第二份查询实现。
2. 队列认领、housekeeping 扫描、schema 初始化/迁移是合法的跨租户系统工作，
   不能被租户策略挡住。
3. 审计写入发生在大量无请求作用域的路径（worker 收尾、启动扫描、登录失败）。

## 决策

- **单一策略、事务级 GUC**（`app/rls.py`）：18 张核心客户数据表统一挂
  `helix_tenant_isolation` 策略，USING/WITH CHECK 均为
  `tenant_id = current_setting('app.tenant_id', true)`。GUC 由
  `PostgresDatabase.connect()` 从 ambient `app.context.tenant_scope_context`
  绑定，`set_config(..., true)` 事务结束即消亡——连接池复用的下一个事务从
  fail-closed 起步。无 GUC 时 `current_setting(..., true)` 返回 NULL → 零行匹配、
  写入违反 WITH CHECK；错误租户同样零匹配。策略表达式是唯一边界，WHERE 子句被忽略。
- **作用域模型**（`app/context.py`）：`tenant_scope(tenant)` 用于业务路径；
  `maintenance_scope(reason)` 用于跨租户系统工作，刻意不绑 GUC——因此要求 RLS
  不覆盖的角色（表 owner / BYPASSRLS），每次进入记 INFO 日志可审计。
  API 认证依赖在验证凭据后 `bind_tenant_scope()`（只来自凭据，绝不来自请求参数）；
  worker 认领/提交走 maintenance scope、turn 执行收窄到 job 自己的 tenant；
  审计写入经 `_audit_scope()` 自绑定事件本身的 tenant。
- **fail loud 而非 fail empty**：RLS 强制开启时，无任何作用域的数据库访问抛
  `TenantContextError`——静默看到空结果会掩盖 bug 而不是暴露它。
- **开关与角色**（`app/config.py` / `app/postgres_db.py`）：
  `DATABASE_RLS_ENABLED=1` 只允许 PostgreSQL 后端；默认关闭保证既有部署不变。
  安装（DDL）必须以 migration release job / owner 角色执行
  （`scripts/run_migrations.py` 或 `install_row_level_security()`），最小权限
  app 角色（无 BYPASSRLS）只拿 DML。owner 是否也受 RLS 约束由
  `FORCE ROW LEVEL SECURITY` 可选决定。
- **保护面**：v01 baseline + attachments(v22) + archive 冷层(v24) + channel
  threads(v27) 中所有 `tenant_id TEXT NOT NULL` 的客户数据表共 18 张。配置/
  registry 类表（tenants、sla_policies、prompt_versions、csat_surveys、webhook_*、
  quality_daily）混有全局行或无 token 路径，暂不纳入，与其访问路径改造一起进后续迭代。
- **验证**：`verify_rls()` 读 pg_class/pg_policy 报告每张表的 ENABLE/policy 状态；
  `_is_tenant_policy()` 对 `pg_get_expr` 的规范化输出做语义 token 匹配（源文本
  比较会因 `::text` cast 失败）。`scripts/run_rls_drill.py` 在 scratch 集群上
  以双角色证明全部验收契约并写台账。

## 后果

- 优点：即使应用层 WHERE 谓词丢失，PostgreSQL 也在行级拒绝越界读写；事务级 GUC
  使连接池天然不串租户；maintenance scope 有显式审计痕迹；负向契约
  （no-context/wrong-tenant/pool-reuse/where-less DELETE/cross-tenant UPDATE）
  由 drill 在真实 PostgreSQL 上持续证明。
- 代价 / 风险：每事务多一次 `set_config` 往返；RLS 对全表扫有计划器开销
  （核心路径本就带 tenant 索引，影响有限）；18 表之外的表仍靠应用层过滤——
  保护面扩张必须与访问路径改造同步；SQLite 后端不受保护（本地档位按设计）。
- 迁移路径：默认关闭；生产启用顺序为 release job 装 policy → 部署最小权限
  app role → `DATABASE_RLS_ENABLED=1` 滚动重启。回滚只需把 env 关掉
  （policy 留存无害），不需要 schema 变更。

## 相关

[ADR-001](0001-dual-backend-single-query.md)、[ADR-003](0003-queue-abstraction.md)、
[ADR-013](0013-data-protection.md)
