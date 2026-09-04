# 1.4 / Phase 41 运维手册

> **适用版本**：1.4.0 及以后  
> **目标读者**：SRE、平台工程师、安全响应人员  
> **前置阅读**：[`RUNBOOK_M0.md`](RUNBOOK_M0.md)、[`DEPLOYMENT.md`](../DEPLOYMENT.md)、[`ROADMAP_2_X.md`](../ROADMAP_2_X.md) § 6

---

## 1. 1.4 版本概览

1.4 是 Secure Operations 版本，建立凭据生命周期、供应链签名、审计锚定和 AI 安全基线：

| ID | 主题 | 关键交付 | 运维影响 |
| --- | --- | --- | --- |
| **SEC-003** | 供应链加固 | 镜像/Action 摘要固定、SAST、secret scan、签名、provenance | CI/CD 流程变更，需要配置签名密钥和 SAST 工具 |
| **SEC-004** | 凭据生命周期 | API key 双活轮换、渠道 secret 双活、自动过期、吊销传播 | 需要更新密钥管理流程，支持双密钥并行验证 |
| **SEC-005** | 审计锚定 | 外部审计锚点（定期导出链头/manifest 到不可变存储） | 需要配置对象存储和定期导出任务 |
| **SEC-008** | 安全治理 | 威胁模型增量复审进入发布 gate | 发布流程增加安全复审步骤 |
| **AI-001** | AI 安全基线 | 对抗评测、工具授权不变量、提示注入防护 | 需要扩展 golden set，增加对抗样本测试 |
| **ARC-001** | 代码可维护性 | `app.js` 深度模块化（4,560 行 → <2,000 行目标） | 前端构建流程可能变化，需要验证兼容性 |

**升级窗口要求**：

- 单实例（SQLite）：滚动重启，< 30 秒停机
- 多实例（PostgreSQL + Redis）：数据库迁移 `0028 → 0030`（新增 `api_key_rotations`、`channel_secret_rotations`、`audit_anchors` 表），预估停机 < 3 分钟

---

## 2. 部署前检查清单

### 2.1 凭据轮换准备（SEC-004）

**API Key 双活轮换**：

- [ ] 已准备密钥生成工具（`scripts/rotate_api_keys.py`）
- [ ] 已配置 `API_KEY_ROTATION_WINDOW_HOURS`（默认 48 小时，旧密钥在此期间仍可用）
- [ ] 已通知下游客户端：轮换期间新旧 key 并行有效，需在 48 小时内切换
- [ ] 已配置审计告警：`api_key.rotated` 事件发生时通知安全团队
- [ ] 已准备应急吊销脚本（立即失效所有旧 key）

**渠道 Secret 双活轮换**：

- [ ] 已识别所有正式渠道账号（`CHANNEL_WEBHOOKS_FILE` 中的 `account_id`）
- [ ] 已在渠道供应商侧配置双 secret 支持（或准备短暂停机窗口）
- [ ] 已准备轮换脚本（`scripts/rotate_channel_secrets.py`）
- [ ] 已配置 `CHANNEL_SECRET_ROTATION_WINDOW_HOURS`（默认 24 小时）

### 2.2 供应链加固准备（SEC-003）

**CI/CD 配置变更**：

- [ ] 已配置 SAST 工具（推荐：Semgrep、Bandit）并集成到 CI pipeline
- [ ] 已配置 secret scan 工具（推荐：TruffleHog、GitGuardian）
- [ ] 已生成容器镜像签名密钥（使用 Cosign 或等价工具）
- [ ] 已在 CI 中固定基础镜像摘要（`FROM python:3.11@sha256:...`）
- [ ] 已固定 GitHub Actions 版本（`uses: actions/checkout@v4.1.1` 而非 `@v4`）
- [ ] 已配置 provenance 生成（SLSA Level 2+）

**本地验证**：

```bash
# 验证 SAST 配置
semgrep --config=auto app/ tests/

# 验证 secret scan
trufflehog filesystem . --only-verified

# 验证镜像签名（如果已构建）
cosign verify --key cosign.pub <image-reference>
```

### 2.3 审计锚定准备（SEC-005）

- [ ] 已配置外部对象存储（S3、Azure Blob 或 GCS）用于审计导出
- [ ] 已配置 `AUDIT_ANCHOR_EXPORT_BUCKET` 和访问凭据
- [ ] 已设置对象存储不可变策略（Object Lock / Immutable Storage）
- [ ] 已配置定期导出任务（推荐：每日凌晨 2:00 UTC）
- [ ] 已准备审计链校验脚本（`scripts/verify_audit_anchors.py`）

### 2.4 AI 安全基线准备（AI-001）

- [ ] 已扩展 golden set 到 ≥50 个测试用例（含对抗样本）
- [ ] 已添加提示注入测试用例（至少 10 个）
- [ ] 已添加工具越权测试用例（尝试访问其他租户数据）
- [ ] 已配置 AI 评测 gate（`scripts/golden_set_eval.py` 通过率 ≥95%）
- [ ] 已准备模型回滚脚本（恢复到上一个已知良好配置）

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
cd /path/to/helix-support
source .venv/bin/activate
alembic upgrade head

# 验证迁移版本
python -c "from app.database import Database; db = Database(':memory:'); print(db.get_migration_version())"
# 预期输出：0030
```

**步骤 3：更新配置**

编辑生产环境变量文件：

```dotenv
# SEC-004: 凭据轮换窗口
API_KEY_ROTATION_WINDOW_HOURS=48
CHANNEL_SECRET_ROTATION_WINDOW_HOURS=24

# SEC-005: 审计锚定
AUDIT_ANCHOR_EXPORT_BUCKET=s3://helix-audit-anchors-prod
AUDIT_ANCHOR_EXPORT_ENABLED=true
AUDIT_ANCHOR_EXPORT_SCHEDULE="0 2 * * *"  # 每日凌晨 2:00 UTC

# AI-001: AI 安全
ENABLE_AI_SAFETY_CHECKS=true
PROMPT_INJECTION_THRESHOLD=0.85
TOOL_AUTHORIZATION_STRICT=true
```

**步骤 4：滚动重启实例**

```bash
# 逐台重启（每台等待 /health/ready 返回 200 后再重启下一台）
systemctl restart helix-support-web-1
curl -f http://localhost:8000/health/ready || exit 1

systemctl restart helix-support-web-2
curl -f http://localhost:8000/health/ready || exit 1

systemctl restart helix-support-worker-1
```

**步骤 5：恢复流量**

```bash
# 将实例重新加入负载均衡器
# 监控错误率和延迟 5-10 分钟
```

**步骤 6：执行首次审计锚点导出**

```bash
# 手动触发首次导出（验证配置正确）
python scripts/export_audit_anchors.py --target s3://helix-audit-anchors-prod
# 验证导出成功
aws s3 ls s3://helix-audit-anchors-prod/
# 预期：audit-anchor-YYYY-MM-DD-HHMMSS.json
```

---

## 4. 凭据轮换操作

### 4.1 API Key 双活轮换

**轮换流程**：

```bash
# 步骤 1：生成新 key（旧 key 仍然有效）
python scripts/rotate_api_keys.py generate \
  --tenant-id tenant-1 \
  --key-id api-key-prod-01 \
  --output new-key.json

# 输出示例：
# {
#   "key_id": "api-key-prod-01",
#   "new_key": "hsk_new_abc123...",
#   "old_key_valid_until": "2026-09-05T10:00:00Z",
#   "rotation_window_hours": 48
# }

# 步骤 2：通知下游客户端切换到新 key
# （通过邮件、工单系统或自动化配置管理工具）

# 步骤 3：监控旧 key 使用情况
python scripts/rotate_api_keys.py monitor \
  --key-id api-key-prod-01
# 输出：旧 key 请求数、最后使用时间

# 步骤 4：48 小时后，旧 key 自动失效
# 或手动提前失效（如果所有客户端已切换）
python scripts/rotate_api_keys.py revoke \
  --key-id api-key-prod-01 \
  --old-key-only

# 步骤 5：验证旧 key 已失效
curl -X GET https://support.example.com/api/conversations \
  -H "X-API-Key: <旧key>"
# 预期：401 Unauthorized
```

**应急吊销**（密钥泄露）：

```bash
# 立即失效所有版本的 key（新旧都失效）
python scripts/rotate_api_keys.py revoke \
  --key-id api-key-prod-01 \
  --immediate

# 生成全新 key（使用新的 key_id）
python scripts/rotate_api_keys.py generate \
  --tenant-id tenant-1 \
  --key-id api-key-prod-02 \
  --output emergency-key.json

# 通过安全渠道发送给客户端
```

### 4.2 渠道 Secret 双活轮换

**轮换流程**：

```bash
# 步骤 1：生成新 secret（旧 secret 仍然有效）
python scripts/rotate_channel_secrets.py generate \
  --account-id wechat-tenant1 \
  --output new-secret.json

# 输出示例：
# {
#   "account_id": "wechat-tenant1",
#   "new_secret": "chsk_new_xyz789...",
#   "old_secret_valid_until": "2026-09-04T10:00:00Z",
#   "rotation_window_hours": 24
# }

# 步骤 2：在渠道供应商侧添加新 secret
# （微信公众号管理后台 → 配置 → 添加备用密钥）

# 步骤 3：验证新 secret 工作正常
curl -X POST https://support.example.com/api/channels/wechat-tenant1/webhook \
  -H "X-Channel-Signature: <使用新secret签名>" \
  -d '{"message_id":"test","text":"测试消息"}'
# 预期：200 OK

# 步骤 4：24 小时后，在渠道供应商侧删除旧 secret
# 同时旧 secret 在 Helix 侧自动失效

# 步骤 5：验证旧 secret 已失效
curl -X POST https://support.example.com/api/channels/wechat-tenant1/webhook \
  -H "X-Channel-Signature: <使用旧secret签名>" \
  -d '{"message_id":"test2","text":"测试消息"}'
# 预期：401 Unauthorized 或 403 Forbidden
```

---

## 5. 审计锚点验证与恢复

### 5.1 验证审计链完整性

```bash
# 验证本地数据库审计链
python scripts/verify_audit_chain.py --database /path/to/support.db

# 输出示例：
# ✓ 审计链完整，共 15,234 个事件
# ✓ 所有哈希验证通过
# ✓ 无缺口或篡改迹象

# 验证导出的审计锚点
python scripts/verify_audit_anchors.py \
  --bucket s3://helix-audit-anchors-prod \
  --since 2026-08-01
# 输出：每日锚点列表及其验证状态
```

### 5.2 从审计锚点恢复

**场景**：生产数据库审计表被意外删除或损坏。

```bash
# 步骤 1：下载所有审计锚点
aws s3 sync s3://helix-audit-anchors-prod/ ./audit-anchors-backup/

# 步骤 2：验证锚点完整性
python scripts/verify_audit_anchors.py \
  --local-path ./audit-anchors-backup/

# 步骤 3：重建审计事件表（从锚点 + 应用日志）
python scripts/rebuild_audit_from_anchors.py \
  --anchors ./audit-anchors-backup/ \
  --logs /var/log/helix-support/ \
  --output restored-audit.db

# 步骤 4：对比恢复的数据与当前数据库
python scripts/audit_diff.py \
  --source restored-audit.db \
  --target /path/to/support.db

# 步骤 5：如果确认恢复数据正确，替换生产数据库审计表
# （需要停机维护窗口）
```

---

## 6. AI 安全评测与回滚

### 6.1 Golden Set 评测

**部署前评测**（在 staging 环境）：

```bash
# 运行完整 golden set 评测
python scripts/golden_set_eval.py \
  --env staging \
  --model gpt-4o \
  --output eval-report.json

# 输出示例：
# Golden Set Evaluation Report
# ============================
# Total cases: 52
# Passed: 51 (98.08%)
# Failed: 1 (1.92%)
# 
# Failed cases:
# - prompt_injection_006: 模型泄露了系统提示词
# 
# Recommendation: BLOCK deployment (pass rate < 95%)
```

**处理评测失败**：

1. 如果失败是已知限制（已记录在豁免列表）：
   ```bash
   # 添加豁免（需要安全负责人批准）
   python scripts/golden_set_eval.py add-exemption \
     --case-id prompt_injection_006 \
     --reason "已知限制，已在提示词中添加防护，但极端情况下仍可能触发" \
     --approved-by security-lead@example.com
   ```

2. 如果失败是新回归：
   - 阻断部署
   - 调查根因（模型版本变化？提示词改动？工具配置错误？）
   - 修复后重新评测

### 6.2 模型回滚

**场景**：生产环境发现模型行为异常（幻觉、拒绝回答、工具调用错误）。

```bash
# 步骤 1：立即回滚到上一个已知良好配置
python scripts/rollback_model.py \
  --target-version 1.3.9 \
  --reason "生产环境发现模型幻觉率异常升高"

# 输出示例：
# ✓ 模型配置已回滚到 1.3.9
# ✓ Prompt 版本已回滚到 stable-2024-08
# ✓ 工具配置已回滚
# ✓ 重启 worker 进程以应用新配置

# 步骤 2：验证回滚后行为正常
python scripts/golden_set_eval.py --env production --quick

# 步骤 3：创建 P1 issue 调查根因
# 步骤 4：修复后在 staging 验证，再重新发布
```

---

## 7. 故障排查

### 7.1 API Key 轮换失效

**症状**：客户端报告新 key 无法使用，返回 401。

**排查步骤**：

1. 验证新 key 是否已正确写入数据库：
   ```sql
   SELECT key_id, key_hash, status, valid_from, valid_until 
   FROM api_keys 
   WHERE key_id = 'api-key-prod-01' 
   ORDER BY created_at DESC LIMIT 2;
   -- 应看到两行：旧 key（status=rotating）和新 key（status=active）
   ```

2. 验证客户端使用的是新 key（不是旧 key）：
   ```bash
   # 检查请求日志
   tail -f /var/log/helix-support/access.log | grep "X-API-Key"
   # 确认客户端发送的 key 前缀为 hsk_new_
   ```

3. 验证时钟同步：
   ```bash
   # 轮换依赖服务器时钟，时钟偏移可能导致 valid_from 未生效
   date -u
   # 确保与 UTC 时间一致
   ```

4. 手动验证 key：
   ```bash
   python scripts/verify_api_key.py --key <新key>
   # 输出：key 状态、关联租户、权限列表
   ```

### 7.2 审计锚点导出失败

**症状**：定期任务未生成新的审计锚点文件。

**排查步骤**：

1. 检查定期任务日志：
   ```bash
   journalctl -u helix-audit-anchor-export.timer -n 50
   # 或
   tail -f /var/log/helix-support/audit-anchor-export.log
   ```

2. 验证对象存储连接：
   ```bash
   aws s3 ls s3://helix-audit-anchors-prod/
   # 如果失败，检查 IAM 角色/凭据配置
   ```

3. 手动执行导出：
   ```bash
   python scripts/export_audit_anchors.py \
     --target s3://helix-audit-anchors-prod \
     --verbose
   # 查看详细错误信息
   ```

4. 常见错误：
   - **权限不足**：IAM 角色缺少 `s3:PutObject` 权限
   - **网络隔离**：应用服务器无法访问 S3 endpoint
   - **磁盘空间不足**：本地临时目录空间耗尽

### 7.3 AI 评测失败

**症状**：Golden set 评测通过率突然下降。

**排查步骤**：

1. 检查模型 API 可用性：
   ```bash
   curl -X POST https://api.openai.com/v1/chat/completions \
     -H "Authorization: Bearer $OPENAI_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"model":"gpt-4o","messages":[{"role":"user","content":"test"}]}'
   # 验证 API 是否可达
   ```

2. 对比失败用例：
   ```bash
   # 查看具体失败的测试用例
   cat eval-report.json | jq '.failed_cases'
   # 对比上一次成功的评测结果
   diff eval-report-previous.json eval-report.json
   ```

3. 检查模型版本变化：
   ```bash
   # 如果使用 gpt-4o，OpenAI 可能静默升级了模型
   # 查看响应头中的模型版本
   curl -i https://api.openai.com/v1/chat/completions \
     -H "Authorization: Bearer $OPENAI_API_KEY" \
     -d '{"model":"gpt-4o","messages":[{"role":"user","content":"test"}]}' \
     | grep -i "openai-model"
   ```

4. 检查提示词版本：
   ```sql
   SELECT version, canary_ratio, status 
   FROM prompt_versions 
   WHERE status = 'active' 
   ORDER BY created_at DESC LIMIT 5;
   -- 验证是否意外切换了 canary 提示词
   ```

---

## 8. 回滚指南

### 8.1 回滚条件

- 1.4 升级后发现 P0/P1 级别的回归问题
- 凭据轮换导致大规模客户端认证失败且无法在 2 小时内修复
- AI 评测通过率 < 85% 且影响生产流量

### 8.2 回滚步骤

**步骤 1：停止流量**

```bash
# 将所有实例从负载均衡器移除
```

**步骤 2：回滚代码**

```bash
git checkout v1.4.0-M0  # 回到 1.4 之前的稳定版本
source .venv/bin/activate
pip install -e .
```

**步骤 3：回滚数据库（可选）**

> **警告**：只有在 1.4 数据库迁移导致兼容性问题时才需要回滚数据库。凭据轮换表可以保留（向后兼容）。

```sql
-- 删除新增的表（如果需要）
DROP TABLE IF EXISTS api_key_rotations;
DROP TABLE IF EXISTS channel_secret_rotations;
DROP TABLE IF EXISTS audit_anchors;

-- 更新迁移版本
UPDATE alembic_version SET version = '0028';
```

**步骤 4：恢复配置**

```bash
# 禁用新功能
AUDIT_ANCHOR_EXPORT_ENABLED=false
ENABLE_AI_SAFETY_CHECKS=false
```

**步骤 5：重启服务**

```bash
systemctl restart helix-support-web-*
systemctl restart helix-support-worker-*
```

**步骤 6：验证**

```bash
curl https://support.example.com/health/ready
# 预期：{"status":"ready","version":"1.4.0-M0",...}
```

---

## 9. 监控与告警

### 9.1 关键指标

| 指标 | 阈值 | 告警优先级 |
| --- | --- | --- |
| `helix_api_key_rotation_failures` > 0 | 立即 | P1 |
| `helix_audit_anchor_export_failures` > 2/day | 24 小时内 | P2 |
| `helix_golden_set_pass_rate` < 0.95 | 立即 | P0 |
| `helix_prompt_injection_detected` > 5/hour | 1 小时内 | P2 |
| `helix_tool_authorization_violations` > 0 | 立即 | P1 |

### 9.2 Prometheus 告警规则示例

```yaml
groups:
  - name: helix_1_4
    interval: 30s
    rules:
      - alert: HelixGoldenSetFailure
        expr: helix_golden_set_pass_rate < 0.95
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Helix Golden Set 评测通过率 < 95%"
          description: "当前通过率 {{ $value }}，可能存在模型回归"

      - alert: HelixAuditAnchorExportFailed
        expr: increase(helix_audit_anchor_export_failures_total[24h]) > 2
        labels:
          severity: warning
        annotations:
          summary: "Helix 审计锚点导出失败超过 2 次/天"
          description: "最近 24 小时失败 {{ $value }} 次"

      - alert: HelixToolAuthorizationViolation
        expr: increase(helix_tool_authorization_violations_total[5m]) > 0
        labels:
          severity: critical
        annotations:
          summary: "检测到工具授权违规"
          description: "可能存在提示注入或工具越权攻击"
```

---

## 10. 非作者执行验证

**验收标准**：

- [ ] 执行者未参与 1.4 实现代码编写
- [ ] 仅使用本 runbook 和生产环境配置文件
- [ ] 在 staging 环境完成完整升级流程（含数据库迁移）
- [ ] 完成至少一次 API key 轮换操作（§ 4.1）
- [ ] 完成至少一次审计锚点导出和验证（§ 5）
- [ ] 完成至少一次 Golden Set 评测（§ 6.1）
- [ ] 完成回滚演练（§ 8）
- [ ] 记录执行时间、遇到的问题和 runbook 改进建议

**执行记录模板**：

```markdown
## 1.4 非作者执行记录

- **执行者**：[姓名]
- **执行日期**：[YYYY-MM-DD]
- **环境**：[staging / production]
- **升级耗时**：[分钟]
- **凭据轮换耗时**：[分钟]
- **遇到的问题**：
  1. [问题描述]
  2. [问题描述]
- **Runbook 改进建议**：
  1. [建议]
  2. [建议]
- **验收结果**：[通过 / 未通过]
```

---

## 11. 参考文档

- [`RUNBOOK_M0.md`](RUNBOOK_M0.md)：M0 运维手册
- [`ROADMAP_2_X.md`](../ROADMAP_2_X.md) § 6：1.4 需求和验收标准
- [`DEPLOYMENT.md`](../DEPLOYMENT.md)：基础部署配置
- [`OPERATIONS.md`](OPERATIONS.md)：日常运维手册
- [`docs/SECURITY_MODEL.md`](SECURITY_MODEL.md)：安全模型与权限

---

## 12. 变更历史

| 版本 | 日期 | 变更内容 | 作者 |
| --- | --- | --- | --- |
| 1.0 | 2026-09-03 | 初始版本，覆盖 SEC-003/004/005/008、AI-001、ARC-001 | Claude Code |
