# 故障排查手册

> **适用版本**：1.3.0+  
> **目标读者**：运维工程师、值班人员、平台工程师  
> **前置阅读**：[`OPERATIONS.md`](OPERATIONS.md)、[`SLO.md`](SLO.md)

本手册提供生产环境常见故障的诊断流程、根因分析方法和临时缓解措施。

---

## 目录

- [故障分类与优先级](#故障分类与优先级)
- [通用诊断流程](#通用诊断流程)
- [服务不可用](#服务不可用)
- [性能降级](#性能降级)
- [数据异常](#数据异常)
- [认证与权限](#认证与权限)
- [队列与后台任务](#队列与后台任务)
- [外部依赖故障](#外部依赖故障)
- [诊断工具](#诊断工具)

---

## 故障分类与优先级

| 级别 | 影响范围 | 响应 SLA | 示例 |
| --- | --- | --- | --- |
| **P0** | 全局服务中断、数据泄露 | 立即响应，15 分钟内开始缓解 | 所有请求 5xx、跨租户数据泄露、认证绕过 |
| **P1** | 单租户服务中断、关键功能失效 | 30 分钟内响应 | 特定租户无法创建会话、队列完全堵塞、数据库不可达 |
| **P2** | 性能降级、非关键功能失效 | 2 小时内响应 | 响应延迟 > 5s、知识检索失效但确定性路径可用 |
| **P3** | 个别用户影响、间歇性问题 | 1 个工作日内响应 | 个别会话卡住、偶发超时 |

---

## 通用诊断流程

### 第一步：确认影响范围

1. **检查健康端点**：
   ```bash
   curl -i https://support.example.com/health/ready
   # 预期：200 OK, {"status":"ready",...}
   # 降级：503, {"status":"degraded","degraded_reason":"..."}
   ```

2. **查看监控面板**（Grafana / Prometheus）：
   - 错误率：`rate(http_requests_total{code=~"5.."}[5m])`
   - 延迟：`histogram_quantile(0.95, http_request_duration_seconds)`
   - 队列积压：`helix_turn_jobs_queued`
   - 数据库连接：`helix_database_pool_in_use / helix_database_pool_size`

3. **确认租户范围**：
   - 全局故障：所有租户受影响
   - 租户隔离：特定 `tenant_id` 受影响（检查配额、模型策略、外部依赖）

### 第二步：收集诊断信息

```bash
# 1. 服务版本与配置
curl https://support.example.com/api/admin/diagnostics | jq
# 包含：version, deployment_profile, queue_backend, database_backend

# 2. 系统指标
curl -H "X-API-Key: <supervisor-key>" \
  https://support.example.com/api/system/metrics | jq

# 3. 最近日志（最近 5 分钟，ERROR 级别）
tail -n 1000 /var/log/helix-support/app.log | grep ERROR

# 4. 审计事件（最近 1 小时）
curl -H "X-API-Key: <supervisor-key>" \
  "https://support.example.com/api/supervisor/audit?since=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)&limit=100"
```

### 第三步：根因分析

使用 **5 Why 分析法**：

```
现象：客户端报告无法创建会话，返回 503
↓ Why?
服务器返回 "queue_unavailable"
↓ Why?
Redis 连接失败，队列 backend 降级
↓ Why?
Redis 主节点故障切换，新主节点未在应用配置中
↓ Why?
Redis Sentinel 配置未同步到应用服务器
↓ Why?
部署自动化未包含 Redis 配置更新步骤

根因：部署流程缺少 Redis 配置验证和同步
临时缓解：手动更新 REDIS_URL 并重启应用实例
长期修复：自动化 Redis 配置同步，增加启动探针验证
```

---

## 服务不可用

### 症状 1：所有请求返回 502 Bad Gateway

**可能原因**：

1. 应用进程崩溃或未启动
2. 反向代理配置错误
3. 应用监听端口不匹配

**诊断步骤**：

```bash
# 1. 检查应用进程
ps aux | grep uvicorn
# 或
systemctl status helix-support

# 2. 检查监听端口
netstat -tlnp | grep 8000
# 或
ss -tlnp | grep 8000

# 3. 检查应用日志
journalctl -u helix-support -n 100 --no-pager
# 或
tail -n 100 /var/log/helix-support/app.log

# 4. 尝试直接访问应用（绕过反向代理）
curl http://localhost:8000/health/live
```

**缓解措施**：

```bash
# 重启应用服务
systemctl restart helix-support

# 如果进程无法启动，检查配置文件
python -c "from app.config import Settings; Settings()"
# 输出配置验证错误
```

---

### 症状 2：健康检查返回 503 degraded

**可能原因**：

1. 数据库连接失败
2. Redis 队列不可达（多实例模式）
3. 磁盘空间不足

**诊断步骤**：

```bash
# 1. 查看降级原因
curl https://support.example.com/health/ready | jq '.degraded_reason'

# 2. 如果是数据库问题
# PostgreSQL
psql $DATABASE_URL -c "SELECT 1"

# SQLite
sqlite3 /path/to/support.db "SELECT 1"

# 3. 如果是 Redis 问题
redis-cli -u $REDIS_URL ping
# 预期：PONG

# 4. 检查磁盘空间
df -h
# 预期：使用率 < 90%
```

**缓解措施**：

```bash
# 数据库连接失败 → 检查凭据、网络、数据库服务状态
# Redis 失败 → 检查 Redis 服务、网络、凭据
# 磁盘满 → 清理日志、临时文件、旧归档
find /var/log/helix-support -name "*.log.*" -mtime +7 -delete
```

---

## 性能降级

### 症状 1：响应延迟突然升高（P95 > 5s）

**可能原因**：

1. 数据库慢查询
2. 外部模型 API 延迟
3. 队列积压导致背压
4. 内存不足触发 GC

**诊断步骤**：

```bash
# 1. 查看系统指标
curl -H "X-API-Key: <supervisor-key>" \
  https://support.example.com/api/system/metrics | jq '.latency'

# 2. 数据库慢查询日志
# PostgreSQL
psql $DATABASE_URL -c "
SELECT query, calls, mean_exec_time, max_exec_time 
FROM pg_stat_statements 
WHERE mean_exec_time > 1000 
ORDER BY mean_exec_time DESC 
LIMIT 10;"

# SQLite（需要启用 query profiling）
# 检查应用日志中的 database.* 指标

# 3. 检查队列积压
curl -H "X-API-Key: <supervisor-key>" \
  https://support.example.com/api/system/metrics | jq '.turn_jobs'

# 4. 检查模型 API 延迟
tail -f /var/log/helix-support/app.log | grep "model_api"
```

**缓解措施**：

```bash
# 1. 临时禁用模型调用（降级到确定性路径）
# 在配置中设置 ENABLE_LLM=false 并重启

# 2. 增加数据库连接池（谨慎，SQLite 不适用）
# 编辑 .env
DATABASE_POOL_SIZE=8  # 从默认 4 增加

# 3. 扩容 worker 并发（如果队列积压）
TURN_WORKER_CONCURRENCY=2  # 从默认 1 增加（多实例可更高）

# 4. 重启应用释放内存
systemctl restart helix-support
```

---

### 症状 2：队列堆积（queued jobs 持续增长）

**可能原因**：

1. Worker 进程未启动或卡死
2. 外部依赖（模型 API、工具服务）超时
3. Worker 并发不足

**诊断步骤**：

```bash
# 1. 检查 worker 状态
curl -H "X-API-Key: <supervisor-key>" \
  https://support.example.com/api/system/metrics | jq '.turn_worker'

# 输出示例：
# {
#   "active_workers": 1,
#   "queued": 245,
#   "processing": 1,
#   "completed_total": 12034,
#   "failed_total": 23,
#   "retried_total": 8
# }

# 2. 检查 worker 日志
tail -f /var/log/helix-support/app.log | grep "turn_worker"

# 3. 检查是否有卡住的 job
psql $DATABASE_URL -c "
SELECT job_id, status, attempts, created_at, updated_at 
FROM turn_jobs 
WHERE status = 'processing' 
  AND updated_at < NOW() - INTERVAL '10 minutes';"
```

**缓解措施**：

```bash
# 1. 重启 worker 进程
systemctl restart helix-support-worker

# 2. 手动重试失败的 job（如果外部依赖已恢复）
curl -X POST https://support.example.com/api/turn-jobs/{job_id}/retry \
  -H "X-API-Key: <supervisor-key>"

# 3. 清理僵尸 job（超时且无法恢复）
# 需要数据库访问权限
psql $DATABASE_URL -c "
UPDATE turn_jobs 
SET status = 'failed', 
    error_code = 'worker_timeout' 
WHERE status = 'processing' 
  AND updated_at < NOW() - INTERVAL '30 minutes';"
```

---

## 数据异常

### 症状 1：审计链验证失败（TAMPER 警告）

**可能原因**：

1. 数据库直接修改（绕过应用）
2. 数据迁移/恢复错误
3. 磁盘损坏

**诊断步骤**：

```bash
# 1. 运行审计链验证
python scripts/verify_audit_chain.py --database /path/to/support.db

# 输出示例：
# ✗ 审计链完整性检查失败
# ✗ 事件 #1234 哈希不匹配
#   预期：abc123...
#   实际：def456...
# ✗ 事件 #1235-#1240 缺失（缺口）

# 2. 检查最近的数据库操作
psql $DATABASE_URL -c "
SELECT event_type, actor_id, created_at, event_id 
FROM audit_events 
ORDER BY created_at DESC 
LIMIT 50;"

# 3. 检查数据库完整性
# PostgreSQL
psql $DATABASE_URL -c "SELECT pg_database.datname, pg_size_pretty(pg_database_size(pg_database.datname)) FROM pg_database;"

# SQLite
sqlite3 /path/to/support.db "PRAGMA integrity_check;"
```

**缓解措施**：

> **警告**：审计链损坏是严重的数据完整性问题，可能表明安全事件。

```bash
# 1. 立即隔离受影响的数据库
# 停止写入，保留现场用于调查

# 2. 从最近的备份恢复
# 验证备份的审计链完整性后再恢复到生产

# 3. 如果无法恢复，记录缺口
psql $DATABASE_URL -c "
INSERT INTO audit_events (event_type, event_id, actor_id, metadata)
VALUES (
  'audit.integrity_gap',
  gen_random_uuid()::text,
  'system',
  '{\"gap_start\": 1235, \"gap_end\": 1240, \"reason\": \"database_corruption\"}'::jsonb
);"

# 4. 通知安全团队进行调查
```

---

### 症状 2：数据库空间快速增长

**可能原因**：

1. 审计事件未归档
2. 消息附件未清理
3. 已删除数据未回收（PostgreSQL VACUUM）

**诊断步骤**：

```bash
# 1. 检查表空间占用
# PostgreSQL
psql $DATABASE_URL -c "
SELECT 
  schemaname, 
  tablename, 
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables 
WHERE schemaname = 'public' 
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC 
LIMIT 10;"

# SQLite
sqlite3 /path/to/support.db "
SELECT 
  name, 
  (SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=m.name) as row_count
FROM sqlite_master m 
WHERE type='table' 
ORDER BY row_count DESC;"

# 2. 检查审计归档配置
curl https://support.example.com/api/admin/diagnostics | jq '.config | {audit_retention_days, archive_enabled}'

# 3. 检查附件存储
du -sh /path/to/attachments/
```

**缓解措施**：

```bash
# 1. 手动触发归档（如果配置正确但未执行）
python scripts/archive_old_audit.py --dry-run
# 验证无误后执行
python scripts/archive_old_audit.py

# 2. 清理过期附件
python scripts/cleanup_attachments.py --older-than 90

# 3. PostgreSQL VACUUM
psql $DATABASE_URL -c "VACUUM ANALYZE;"

# 4. SQLite VACUUM（需要停机，需要额外磁盘空间）
systemctl stop helix-support
sqlite3 /path/to/support.db "VACUUM;"
systemctl start helix-support
```

---

## 认证与权限

### 症状 1：API key 认证失败（401 Unauthorized）

**可能原因**：

1. API key 过期或被吊销
2. key 格式错误或被截断
3. 租户配置不匹配

**诊断步骤**：

```bash
# 1. 验证 API key 格式
# 正确格式：hsk_<base64>（以 hsk_ 开头）
echo "<客户端提供的key>" | grep -E '^hsk_[A-Za-z0-9+/=]+$'

# 2. 查询 key 状态
python scripts/verify_api_key.py --key <key>
# 输出：key_id, tenant_id, status, valid_until, permissions

# 3. 检查审计日志
curl -H "X-API-Key: <supervisor-key>" \
  "https://support.example.com/api/supervisor/audit?event_type=auth.forbidden&limit=20"
```

**缓解措施**：

```bash
# 1. 如果 key 过期，生成新 key
python scripts/generate_api_key.py --tenant-id <tenant_id>

# 2. 如果 key 被误吊销，恢复 key
psql $DATABASE_URL -c "
UPDATE api_keys 
SET status = 'active' 
WHERE key_id = '<key_id>';"

# 3. 通知客户端使用新 key
```

---

### 症状 2：OIDC 登录重定向失败

**可能原因**：

1. `BASE_URL` 配置与实际访问 URL 不匹配
2. IdP 回调 URL 未注册
3. state/nonce 验证失败

**诊断步骤**：

```bash
# 1. 检查 BASE_URL 配置
curl https://support.example.com/api/admin/diagnostics | jq '.config.base_url'
# 必须与浏览器地址栏的 origin 一致

# 2. 检查 IdP 配置
curl https://support.example.com/api/admin/diagnostics | jq '.config | {oidc_issuer_url, oidc_client_id}'

# 3. 查看登录失败日志
tail -f /var/log/helix-support/app.log | grep "oidc"
```

**缓解措施**：

```bash
# 1. 修正 BASE_URL
# 编辑 .env
BASE_URL=https://support.example.com  # 去掉尾部斜杠

# 2. 在 IdP 管理控制台添加回调 URL
# {BASE_URL}/auth/callback

# 3. 重启应用
systemctl restart helix-support
```

---

## 队列与后台任务

### 症状 1：Webhook 未发送（webhook_deliveries 表积压）

**可能原因**：

1. Worker housekeeping 未运行
2. 目标端点持续返回错误
3. Webhook 配置错误

**诊断步骤**：

```bash
# 1. 检查待发送的 webhook
psql $DATABASE_URL -c "
SELECT 
  delivery_id, 
  endpoint_id, 
  status, 
  attempts, 
  last_attempt_at, 
  next_retry_at 
FROM webhook_deliveries 
WHERE status IN ('pending', 'sending') 
ORDER BY created_at 
LIMIT 20;"

# 2. 检查端点配置
curl -H "X-API-Key: <tenant-admin-key>" \
  https://support.example.com/api/webhooks

# 3. 查看发送失败日志
tail -f /var/log/helix-support/app.log | grep "webhook_delivery"
```

**缓解措施**：

```bash
# 1. 手动触发重试（如果目标端点已恢复）
python scripts/retry_webhooks.py --delivery-ids <id1,id2,id3>

# 2. 如果端点永久失效，删除端点（会 dead-letter 待发送的消息）
curl -X DELETE https://support.example.com/api/webhooks/<endpoint_id> \
  -H "X-API-Key: <tenant-admin-key>"

# 3. 查看 dead-letter 队列
psql $DATABASE_URL -c "
SELECT delivery_id, error_code, metadata 
FROM webhook_deliveries 
WHERE status = 'dead_letter' 
ORDER BY updated_at DESC 
LIMIT 10;"
```

---

### 症状 2：定时任务未执行（数据保留、审计归档）

**可能原因**：

1. Worker housekeeping 循环卡住
2. 定时任务配置错误
3. 磁盘空间不足

**诊断步骤**：

```bash
# 1. 检查 housekeeping 日志
tail -f /var/log/helix-support/app.log | grep "housekeeping"

# 2. 查看最近的归档记录
psql $DATABASE_URL -c "
SELECT archive_id, row_count, created_at 
FROM audit_archives 
ORDER BY created_at DESC 
LIMIT 5;"

# 3. 检查数据保留配置
curl https://support.example.com/api/admin/diagnostics | jq '.config | {audit_retention_days, message_retention_days}'
```

**缓解措施**：

```bash
# 1. 手动执行归档任务
python scripts/archive_old_audit.py

# 2. 手动执行数据保留清理
python scripts/cleanup_old_data.py --retention-days 90

# 3. 重启 worker 进程
systemctl restart helix-support-worker
```

---

## 外部依赖故障

### 症状 1：模型 API 持续超时或 5xx

**可能原因**：

1. 模型提供商服务中断
2. API key 配额耗尽
3. 网络连接问题

**诊断步骤**：

```bash
# 1. 直接测试模型 API
curl -X POST https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"test"}],"max_tokens":10}'

# 2. 检查配额使用情况
# （访问模型提供商控制台）

# 3. 查看应用日志中的错误
tail -f /var/log/helix-support/app.log | grep "model_api_error"
```

**缓解措施**：

```bash
# 1. 临时禁用模型调用（降级到确定性路径）
# 编辑 .env
ENABLE_LLM=false

# 2. 重启应用
systemctl restart helix-support

# 3. 确认确定性路径可用
curl -X POST https://support.example.com/api/conversations \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"customer_name":"测试","query":"配送时间"}'
# 应返回基于规则的回答，不调用模型
```

---

### 症状 2：Redis 连接失败（多实例模式）

**可能原因**：

1. Redis 服务停止
2. 网络分区
3. 凭据错误

**诊断步骤**：

```bash
# 1. 测试 Redis 连接
redis-cli -u $REDIS_URL ping
# 预期：PONG

# 2. 检查 Redis 服务状态
systemctl status redis
# 或
docker ps | grep redis

# 3. 检查 Redis 日志
tail -f /var/log/redis/redis-server.log

# 4. 验证应用配置
curl https://support.example.com/api/admin/diagnostics | jq '.config.redis_url'
```

**缓解措施**：

```bash
# 1. 重启 Redis 服务
systemctl restart redis

# 2. 如果 Redis 无法恢复，应用会进入 fail-closed 模式
# /health/ready 返回 503 degraded
# 所有 intake 请求返回 503，不会静默降级到 SQLite

# 3. 恢复 Redis 后，应用自动恢复
curl https://support.example.com/health/ready
# 预期：{"status":"ready","queue_backend":"redis","queue_healthy":true}
```

---

## 诊断工具

### 脚本工具

| 脚本 | 用途 | 示例 |
| --- | --- | --- |
| `scripts/verify_audit_chain.py` | 验证审计链完整性 | `python scripts/verify_audit_chain.py --database /path/to/support.db` |
| `scripts/verify_api_key.py` | 验证 API key 状态 | `python scripts/verify_api_key.py --key hsk_abc123...` |
| `scripts/export_diagnostics.py` | 导出完整诊断包 | `python scripts/export_diagnostics.py --output diag.tar.gz` |
| `scripts/redis_failure_drill.py` | Redis 故障演练 | `python scripts/redis_failure_drill.py` |
| `scripts/archive_old_audit.py` | 手动归档审计事件 | `python scripts/archive_old_audit.py --dry-run` |
| `scripts/cleanup_attachments.py` | 清理过期附件 | `python scripts/cleanup_attachments.py --older-than 90` |

### 诊断包内容

运行 `scripts/export_diagnostics.py` 会生成包含以下内容的压缩包：

- 系统配置快照（`/api/admin/diagnostics`）
- 系统指标快照（`/api/system/metrics`）
- 最近 1000 行应用日志
- 数据库表行数统计
- 队列状态快照
- 审计事件统计（最近 24 小时）
- 进程资源使用情况（CPU、内存、磁盘）

**使用方法**：

```bash
# 生成诊断包
python scripts/export_diagnostics.py --output /tmp/helix-diag-$(date +%Y%m%d-%H%M%S).tar.gz

# 上传到 issue 或发送给支持团队
```

### 日志分析

**常见日志模式**：

```bash
# 1. 查找错误和异常
grep -E "ERROR|CRITICAL" /var/log/helix-support/app.log

# 2. 查找特定租户的活动
grep "tenant_id=tenant-1" /var/log/helix-support/app.log

# 3. 查找慢请求（> 5 秒）
grep -E "duration=[5-9]\d{3}|duration=\d{5,}" /var/log/helix-support/app.log

# 4. 统计错误类型
grep ERROR /var/log/helix-support/app.log | \
  awk -F'error_code=' '{print $2}' | \
  awk '{print $1}' | \
  sort | uniq -c | sort -rn

# 5. 分析请求分布
grep "request_id=" /var/log/helix-support/app.log | \
  awk -F'method=' '{print $2}' | \
  awk '{print $1}' | \
  sort | uniq -c | sort -rn
```

---

## 升级支持

如果上述诊断步骤无法解决问题，准备以下信息并提交支持请求：

1. **诊断包**：`scripts/export_diagnostics.py` 输出
2. **问题描述**：
   - 开始时间
   - 影响范围（租户、功能、用户数）
   - 已尝试的缓解措施
3. **环境信息**：
   - 版本号（`/health/ready` 中的 `version`）
   - 部署档位（单实例 SQLite / 多实例 PostgreSQL+Redis）
   - 基础设施（云厂商、区域、实例规格）
4. **可复现步骤**（如果适用）
5. **审计事件样本**（如果涉及数据异常）

**支持渠道**：

- GitHub Issue：[https://github.com/nangongdao/agent1/issues](https://github.com/nangongdao/agent1/issues)
- 企业支持：参考 `SECURITY.md` 中的联系方式

---

## 变更历史

| 版本 | 日期 | 变更内容 | 作者 |
| --- | --- | --- | --- |
| 1.0 | 2026-09-03 | 初始版本，覆盖常见故障场景 | Claude Code |
