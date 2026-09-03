# M0 / Phase 40 运维手册

> **适用版本**：1.4.0-M0 及以后  
> **目标读者**：SRE、平台工程师、安全响应人员  
> **前置阅读**：[`DEPLOYMENT.md`](../DEPLOYMENT.md)、[`OPERATIONS.md`](OPERATIONS.md)、[`ROADMAP_2_X.md`](../ROADMAP_2_X.md) § 5

---

## 1. M0 关键变更概览

M0 是停止线安全修复版本，解决三个发布阻断风险：

| ID | 风险 | 修复内容 | 运维影响 |
| --- | --- | --- | --- |
| **SEC-001** | OIDC 身份验证不完整 | 完整的 Authorization Code + PKCE 流程、JWKS 签名验证、事务绑定、成员映射 | 启用 `ENABLE_SESSION_AUTH=true` 前必须配置测试 IdP 并完成负向测试 |
| **SEC-002** | DSR 权限过宽 | 新增 `privacy:admin` 权限，DSR 创建/执行与普通 `operator:act` 解耦 | 需要更新 API keys 配置，授予隐私管理员专用权限 |
| **REL-001** | Redis 静默降级 | `fail_closed` 模式：Redis 不可达时拒绝排队请求，不再静默回退 SQLite | 多实例部署必须配置 Redis HA；启动探针验证 queue backend |

**升级窗口要求**：

- 单实例（SQLite）：滚动重启，< 30 秒停机
- 多实例（PostgreSQL + Redis）：需要数据库迁移 `0027 → 0028`（新增 `auth_transactions` 表和 `tenant_members.privacy_admin` 列），预估停机 < 2 分钟

---

## 2. 部署前检查清单

### 2.1 身份与权限准备（SEC-001、SEC-002）

**如果启用 OIDC (`ENABLE_SESSION_AUTH=true`)**：

- [ ] 已配置 OIDC IdP 并获取 `OIDC_ISSUER_URL`、`OIDC_CLIENT_ID`、`OIDC_CLIENT_SECRET`
- [ ] 已在 IdP 注册回调 URL：`{BASE_URL}/auth/callback`（`BASE_URL` 必须是外部可达 origin）
- [ ] 已配置 `tenant_members` 表，将 IdP 用户映射到租户和角色
- [ ] 已在 staging 环境完成负向测试：state 重放、nonce 错配、过期 token、未知成员
- [ ] 已配置 JWKS 缓存 TTL（默认 3600 秒）和刷新策略

**隐私权限分离（SEC-002）**：

- [ ] 已识别需要执行 DSR 导出/删除的管理员账号
- [ ] 已在 `API_KEYS_FILE` 或 `tenant_members` 中授予 `privacy:admin` 权限
- [ ] 已从普通坐席账号移除 DSR 路由访问权限（网络层或 RBAC）
- [ ] 已配置审计事件监控，告警 `dsr.*` 操作

### 2.2 队列可靠性准备（REL-001）

**多实例部署（`DEPLOYMENT_PROFILE=multi`）**：

- [ ] 已配置 Redis HA（主从复制 + Sentinel，或托管 Redis 集群）
- [ ] 已设置 `QUEUE_FAILURE_MODE=fail_closed`（M0 后强制要求）
- [ ] 已配置 Redis 连接健康检查和告警（连接失败 → 立即停止扩容）
- [ ] 已准备 Redis 故障演练脚本（断连 → 观察 `/health/ready` 返回 `degraded` → intake 返回 `503`）
- [ ] 已配置 Prometheus 告警：`helix_queue_backend != "redis"` 且 `DEPLOYMENT_PROFILE=multi`

**单实例部署（SQLite）**：

- [ ] 确认 `QUEUE_BACKEND=sqlite`（默认值，无需 Redis）
- [ ] 无需额外配置，SQLite 队列继续工作

### 2.3 数据库迁移检查

- [ ] 当前数据库版本为 `0027`（运行 `SELECT version FROM alembic_version` 验证）
- [ ] 已备份生产数据库（PostgreSQL: `pg_dump`；SQLite: 拷贝 `support.db` 文件）
- [ ] 已在 staging 环境完成迁移 `0027 → 0028` 验证（预估时间 < 10 秒）
- [ ] 已准备回滚脚本（降级到 `0027`，删除 `auth_transactions` 表）

---

## 3. 部署步骤

### 3.1 升级流程（多实例 PostgreSQL + Redis）

**步骤 1：停止流量写入**

```bash
# 将所有实例从负载均衡器移除
# 或设置维护页（返回 503）
```

**步骤 2：数据库迁移**

```bash
# 在一台实例上执行迁移
cd /path/to/helix-support
source .venv/bin/activate
alembic upgrade head

# 验证迁移版本
python -c "from app.database import Database; db = Database(':memory:'); print(db.get_migration_version())"
# 预期输出：0028
```

**步骤 3：更新配置**

编辑生产环境变量文件（或 secret manager）：

```dotenv
# SEC-001: 如果启用 OIDC
ENABLE_SESSION_AUTH=true
OIDC_ISSUER_URL=https://idp.example.com/.well-known/openid-configuration
OIDC_CLIENT_ID=helix-support-prod
OIDC_CLIENT_SECRET=<从 secret manager 读取>
BASE_URL=https://support.example.com

# SEC-002: DSR 导出加密（生产必须）
DSR_EXPORT_SECRET=<长随机值，从 secret manager 读取>

# REL-001: 队列故障模式
QUEUE_FAILURE_MODE=fail_closed
REDIS_URL=redis://redis-ha:6379/0
```

**步骤 4：滚动重启实例**

```bash
# 逐台重启（每台等待 /health/ready 返回 200 后再重启下一台）
systemctl restart helix-support-web-1
curl -f http://localhost:8000/health/ready || exit 1

systemctl restart helix-support-web-2
curl -f http://localhost:8000/health/ready || exit 1

systemctl restart helix-support-worker-1
# Worker 实例无需等待外部健康检查，直接启动即可
```

**步骤 5：恢复流量**

```bash
# 将实例重新加入负载均衡器
# 监控错误率和延迟 5-10 分钟
```

### 3.2 升级流程（单实例 SQLite）

**步骤 1：停止服务**

```bash
# Windows
Stop-Process -Id <PID>

# Linux
systemctl stop helix-support
```

**步骤 2：备份数据库**

```bash
cp data/support.db data/support.db.backup-before-m0
```

**步骤 3：拉取新版本并安装**

```bash
git fetch origin
git checkout v1.4.0-M0
source .venv/bin/activate
pip install -e .
```

**步骤 4：执行迁移**

```bash
alembic upgrade head
```

**步骤 5：更新配置**

编辑 `.env` 文件：

```dotenv
# SEC-002: DSR 导出加密（如果使用 DSR）
DSR_EXPORT_SECRET=<长随机值>

# SEC-001: OIDC（如果需要启用会话认证）
ENABLE_SESSION_AUTH=false  # 单实例演示通常继续使用 AUTH_MODE=demo
```

**步骤 6：启动服务**

```bash
# Windows
powershell.exe -NoLogo -NoProfile -NonInteractive -File .\scripts\start_local.ps1

# Linux
systemctl start helix-support
```

**步骤 7：验证**

```bash
curl http://localhost:8000/health/ready
# 预期输出：{"status":"ready","version":"1.4.0-M0",...}
```

---

## 4. 验证与冒烟测试

### 4.1 OIDC 身份验证（SEC-001）

**正向测试**：

```bash
# 1. 访问登录页
curl -I https://support.example.com/auth/login
# 预期：302 重定向到 IdP 授权页面

# 2. 完成 IdP 登录后，验证 callback 设置了 session cookie
# （浏览器操作，检查 Set-Cookie: helix_session=...; HttpOnly; SameSite=lax）

# 3. 验证 session 信息
curl -b "helix_session=<cookie值>" https://support.example.com/auth/session
# 预期：{"authenticated":true,"tenant_id":"...","actor_id":"...","role":"..."}
```

**负向测试**（在 staging 环境）：

```bash
# 1. 重放 state（应返回 400 或 403）
# 2. 使用错误 nonce（应返回 400）
# 3. 使用过期 token（应返回 400）
# 4. 使用未在 tenant_members 中映射的用户（应返回 403）
```

### 4.2 DSR 权限分离（SEC-002）

```bash
# 1. 使用普通坐席 API key 尝试创建 DSR 导出（应返回 403）
curl -X POST https://support.example.com/api/admin/dsr/export \
  -H "X-API-Key: <operator-key>" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"tenant-1","actor_id":"user-1"}'
# 预期：{"detail":"Forbidden: privacy:admin required"}

# 2. 使用 privacy:admin API key 创建导出（应返回 200）
curl -X POST https://support.example.com/api/admin/dsr/export \
  -H "X-API-Key: <privacy-admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"tenant-1","actor_id":"user-1"}'
# 预期：{"export_id":"...","download_url":"...","expires_at":"..."}

# 3. 验证审计事件
curl -H "X-API-Key: <supervisor-key>" \
  "https://support.example.com/api/supervisor/audit?event_type=dsr.export_created"
# 预期：包含 actor_id 为 privacy admin 的审计记录
```

### 4.3 队列故障模式（REL-001）

**多实例 + Redis fail-closed 验证**：

```bash
# 1. 正常状态：验证 /health/ready 返回 queue_backend=redis
curl https://support.example.com/health/ready
# 预期：{"status":"ready","queue_backend":"redis",...}

# 2. 模拟 Redis 故障（在测试环境）
docker stop redis-test

# 3. 验证健康检查降级
curl https://support.example.com/health/ready
# 预期：{"status":"degraded","queue_backend":"redis","queue_healthy":false,...}

# 4. 验证 intake 请求被拒绝
curl -X POST https://support.example.com/api/conversations \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"customer_name":"测试","query":"你好"}'
# 预期：503 Service Unavailable, {"detail":"Queue unavailable","retry_after":30}

# 5. 恢复 Redis
docker start redis-test

# 6. 验证服务恢复
curl https://support.example.com/health/ready
# 预期：{"status":"ready","queue_backend":"redis","queue_healthy":true,...}
```

---

## 5. 故障排查

### 5.1 OIDC 登录失败

**症状**：用户点击登录后无法完成认证，或收到 400/403 错误。

**排查步骤**：

1. 检查 `BASE_URL` 配置是否与外部可达 URL 一致：
   ```bash
   echo $BASE_URL
   # 必须是 https://support.example.com（不能是 http://localhost:8000）
   ```

2. 检查 IdP 回调 URL 配置：
   ```bash
   # 在 IdP 管理控制台验证注册的 redirect_uri
   # 必须是 {BASE_URL}/auth/callback
   ```

3. 检查 JWKS 验证日志：
   ```bash
   tail -f /var/log/helix-support/app.log | grep "JWKS"
   # 如果看到 "JWKS fetch failed" 或 "Invalid signature"，检查网络连接和证书
   ```

4. 验证 `tenant_members` 映射：
   ```sql
   SELECT * FROM tenant_members WHERE actor_id = '<IdP用户ID>';
   -- 如果为空，用户未映射到任何租户，需要添加记录
   ```

5. 检查 `auth_transactions` 表（负向测试）：
   ```sql
   SELECT state, consumed, expires_at FROM auth_transactions ORDER BY created_at DESC LIMIT 10;
   -- 验证 state 是否被正确消费（consumed=1）且未过期
   ```

### 5.2 DSR 权限错误

**症状**：管理员无法执行 DSR 导出/删除，返回 403 Forbidden。

**排查步骤**：

1. 验证 API key 权限：
   ```bash
   # 检查 API_KEYS_FILE 配置
   cat /path/to/api_keys.json | jq '.keys[] | select(.permissions[] | contains("privacy:admin"))'
   # 确保目标 key 包含 "privacy:admin" 权限
   ```

2. 验证 session 用户权限（OIDC 模式）：
   ```sql
   SELECT role, privacy_admin FROM tenant_members WHERE actor_id = '<用户ID>';
   -- privacy_admin 列应为 1（或 true）
   ```

3. 检查审计日志：
   ```bash
   curl -H "X-API-Key: <supervisor-key>" \
     "https://support.example.com/api/supervisor/audit?event_type=auth.forbidden&since=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)"
   # 查看被拒绝请求的 actor_id 和 required_permission
   ```

### 5.3 Redis 队列降级

**症状**：`/health/ready` 返回 `degraded`，intake 请求返回 503。

**排查步骤**：

1. 验证 Redis 连接：
   ```bash
   redis-cli -u $REDIS_URL ping
   # 预期：PONG
   ```

2. 检查 Redis 主从状态（如果使用 Sentinel）：
   ```bash
   redis-cli -h sentinel-host -p 26379 SENTINEL master helix-queue
   # 验证 master 状态和 flags
   ```

3. 检查应用日志：
   ```bash
   tail -f /var/log/helix-support/app.log | grep "queue"
   # 查找 "Redis connection failed" 或 "Queue backend unavailable"
   ```

4. 验证 `QUEUE_FAILURE_MODE` 配置：
   ```bash
   curl https://support.example.com/api/admin/diagnostics | jq '.config.queue_failure_mode'
   # 预期：fail_closed
   ```

5. 如果 Redis 短期无法恢复，临时解决方案（**仅限紧急情况**）：
   ```bash
   # 方案 A：停止所有实例，等待 Redis 恢复
   # 方案 B：如果必须继续服务，临时降级到单实例 + SQLite（需要停止其他实例并切换配置）
   ```

### 5.4 数据库迁移失败

**症状**：`alembic upgrade head` 执行失败，或数据库版本不正确。

**排查步骤**：

1. 检查当前迁移版本：
   ```sql
   SELECT version FROM alembic_version;
   -- 应为 0028（M0 版本）
   ```

2. 检查迁移日志：
   ```bash
   alembic history
   # 验证 0027 → 0028 迁移脚本存在
   ```

3. 手动执行迁移 SQL（如果自动迁移失败）：
   ```sql
   -- 0028 迁移内容（仅供参考，实际以迁移脚本为准）
   CREATE TABLE IF NOT EXISTS auth_transactions (
     state TEXT PRIMARY KEY,
     nonce TEXT NOT NULL,
     pkce_verifier TEXT NOT NULL,
     redirect_uri TEXT,
     tenant_hint TEXT,
     consumed INTEGER DEFAULT 0,
     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
     expires_at TIMESTAMP NOT NULL
   );
   
   ALTER TABLE tenant_members ADD COLUMN privacy_admin INTEGER DEFAULT 0;
   
   UPDATE alembic_version SET version = '0028';
   ```

4. 如果迁移成功但应用启动失败，检查表结构：
   ```sql
   PRAGMA table_info(auth_transactions);
   PRAGMA table_info(tenant_members);
   -- 验证新增的列存在
   ```

---

## 6. 回滚指南

### 6.1 回滚条件

- M0 升级后发现 P0/P1 级别的回归问题
- OIDC 登录完全失效且无法在 2 小时内修复
- Redis fail-closed 导致所有 intake 请求失败且无法恢复 Redis

### 6.2 回滚步骤

**步骤 1：停止流量**

```bash
# 将所有实例从负载均衡器移除
```

**步骤 2：回滚代码**

```bash
git checkout v1.3.9  # 回到 M0 之前的稳定版本
source .venv/bin/activate
pip install -e .
```

**步骤 3：回滚数据库（可选）**

> **警告**：只有在 M0 数据库迁移导致兼容性问题时才需要回滚数据库。如果只是代码回滚，数据库可以保持 `0028` 版本（向后兼容）。

```sql
-- 删除新增的表（如果需要）
DROP TABLE IF EXISTS auth_transactions;

-- 删除新增的列（SQLite 不支持 DROP COLUMN，需要重建表）
-- 对于 PostgreSQL：
ALTER TABLE tenant_members DROP COLUMN IF EXISTS privacy_admin;

-- 更新迁移版本
UPDATE alembic_version SET version = '0027';
```

**步骤 4：恢复配置**

```bash
# 禁用 OIDC
ENABLE_SESSION_AUTH=false

# 恢复 fail-open 队列模式（如果需要）
# 注意：这会恢复静默降级行为，不推荐长期使用
QUEUE_FAILURE_MODE=fail_open
```

**步骤 5：重启服务**

```bash
systemctl restart helix-support-web-*
systemctl restart helix-support-worker-*
```

**步骤 6：验证**

```bash
curl https://support.example.com/health/ready
# 预期：{"status":"ready","version":"1.3.9",...}
```

**步骤 7：通知与后续**

- 在内部工单系统记录回滚原因和时间
- 通知用户服务已恢复但部分功能（OIDC、DSR 权限分离）暂时不可用
- 创建 P0 issue 跟踪回滚根因，在修复后重新部署 M0

---

## 7. 监控与告警

### 7.1 关键指标

| 指标 | 阈值 | 告警优先级 |
| --- | --- | --- |
| `helix_health_ready` != 1 | > 1 分钟 | P1 |
| `helix_queue_backend` != "redis"（多实例部署） | 立即 | P0 |
| `helix_auth_login_failures` > 10/min | 持续 5 分钟 | P2 |
| `helix_dsr_export_failures` > 3/hour | 持续 1 小时 | P2 |
| `helix_intake_503_rate` > 5% | 持续 3 分钟 | P1 |

### 7.2 Prometheus 告警规则示例

```yaml
groups:
  - name: helix_m0
    interval: 30s
    rules:
      - alert: HelixQueueBackendMismatch
        expr: helix_queue_backend{deployment_profile="multi"} != 1
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Helix 多实例部署未使用 Redis 队列"
          description: "实例 {{ $labels.instance }} 的 queue_backend 不是 redis，可能静默降级到 SQLite"

      - alert: HelixOIDCLoginFailures
        expr: rate(helix_auth_login_failures_total[5m]) > 0.5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Helix OIDC 登录失败率过高"
          description: "最近 5 分钟登录失败率 {{ $value }}/min"

      - alert: HelixIntakeDegraded
        expr: rate(helix_intake_503_total[3m]) / rate(helix_intake_requests_total[3m]) > 0.05
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "Helix intake 503 错误率 > 5%"
          description: "队列可能不可用，intake 请求被拒绝"
```

---

## 8. 非作者执行验证

**目标**：证明 M0 runbook 可以被没有参与实现的 SRE 独立执行。

**验收标准**：

- [ ] 执行者未参与 M0 实现代码编写
- [ ] 仅使用本 runbook 和生产环境配置文件
- [ ] 在 staging 环境完成完整升级流程（含数据库迁移）
- [ ] 完成所有冒烟测试（§ 4）
- [ ] 完成至少一个故障场景排查（§ 5）
- [ ] 完成回滚演练（§ 6）
- [ ] 记录执行时间、遇到的问题和 runbook 改进建议

**执行记录模板**：

```markdown
## M0 非作者执行记录

- **执行者**：[姓名]
- **执行日期**：[YYYY-MM-DD]
- **环境**：[staging / production]
- **升级耗时**：[分钟]
- **遇到的问题**：
  1. [问题描述]
  2. [问题描述]
- **Runbook 改进建议**：
  1. [建议]
  2. [建议]
- **验收结果**：[通过 / 未通过]
```

---

## 9. 参考文档

- [`ROADMAP_2_X.md`](../ROADMAP_2_X.md) § 5：M0 需求和验收标准
- [`DEPLOYMENT.md`](../DEPLOYMENT.md)：基础部署配置
- [`OPERATIONS.md`](OPERATIONS.md)：日常运维手册
- [`docs/SECURITY_MODEL.md`](SECURITY_MODEL.md)：安全模型与权限
- [`docs/SLO.md`](SLO.md)：服务等级目标
- [`docs/DISASTER_RECOVERY.md`](DISASTER_RECOVERY.md)：灾备与恢复

---

## 10. 变更历史

| 版本 | 日期 | 变更内容 | 作者 |
| --- | --- | --- | --- |
| 1.0 | 2026-09-03 | 初始版本，覆盖 SEC-001/002、REL-001 | Claude Code |
