# ADR-013: 数据保护与隐私运营（Phase 41.4 DATA）

- 状态：已接受（Phase 41.4，1.4 / Secure Operations）
- 日期：2026-08-20

## 背景

ROADMAP 41.4 要求把隐私运营从"DSR 能跑"升级为"可审计、可恢复、可证明"：

- 数据分类：public/internal/confidential/restricted，字段级登记收集目的、保留期、区域和下游。
- secret、token、受限 PII 在日志/trace/diagnostics 中使用统一结构化 redaction，并增加 canary 泄漏测试。
- DSR 队列增加 SLA、审批看板、导出 checksum、删除证明和失败重试；大规模删除不得占用同步 API 路径。
- 备份恢复后继续执行 tombstone，防止已删除客户数据从旧备份重新出现。

M0（Phase 40）已落地 DSR maker-checker 状态机与加密导出（`app/retention.py`、migration 29），但缺少：
1. 字段级分类的机器可读 registry；
2. 统一的红action 层（此前各模块自行处理）；
3. DSR 的 SLA 与审批看板；
4. 删除完成后的可验证证明（deletion proof）；
5. 备份恢复后的 tombstone 重放；
6. 大规模删除的异步化。

## 决策

### 1. 字段级数据分类 registry（migration 32）

新增 `data_field_registry` 表，字段级登记：

| 列 | 说明 |
|---|---|
| `field` | 字段名（主键） |
| `classification` | `public` / `internal` / `confidential` / `restricted` |
| `collection_purpose` | 收集目的 |
| `retention_days` | 默认保留期 |
| `region` | 数据区域 |
| `downstream` | 下游消费者 JSON 数组 |

`app/redaction.py` 的 `FIELD_REGISTRY` 提供代码级默认值（如 `customer_ref` = confidential，730 天），数据库表允许运营覆盖。

### 2. 统一结构化 redaction（`app/redaction.py`）

双通道红action：

- **Key 通道**：字段名匹配敏感词（`password`、`token`、`secret`、`key`、`authorization`、`cookie` 等）或分类为 confidential/restricted 时，值替换为 `[REDACTED]`。
- **Value 通道**：值匹配 secret 形态（JWT、Bearer、sk-*、ghp_、xox、AKIA、PEM、40+ hex、base64）时替换为 `[REDACTED]`。

提供 `make_canary()` / `scan_for_canary()` / `assert_no_canary_leaks()` 用于测试：生成 `canary-<32hex>` 哨兵值，断言其不会出现在红action 后的输出中。

### 3. DSR SLA 与审批看板

migration 32 为 `data_subject_requests` 增加：

- `sla_due_at`：创建时计算的 SLA 截止时间（默认 120 分钟，可配置 `dsr_sla_minutes`）。
- `sla_breached`：超时未完成时置 1（幂等，只翻一次）。
- `last_errored_at` / `error_detail`：失败原因留痕，支持重试。

`DataProtectionService.list_approved_pending` 返回审批看板行，`customer_ref` 经 SHA-256 单向指纹脱敏（前 16 hex），原始引用不出 API。

### 4. 删除证明（deletion proof）

- 创建 DSR 时生成 `execution_secret`（32 hex），仅存数据库，API 不返回。
- 删除执行后写 `customer_tombstones` 行，含 `secret_hash = sha256(execution_secret)`。
- 查询证明需持有原始 secret：`GET /api/privacy/deletion-proof?customer_ref=...&secret=...`，匹配则返回删除时间/执行者，不匹配 404。
- 使用 `hmac.compare_digest` 防时序攻击。

### 5. 失败重试

执行失败不再让请求卡在 `approved`：

- `mark_request_failed` 置 `failed`，留 `error_detail`。
- `POST /api/privacy/requests/{id}/retry` 重置回 `approved`，可重新执行。
- 重试幂等：从原 approved id 重跑，不新建请求。

### 6. 大规模删除异步化

- `DEFERRED_DELETION_THRESHOLD = 1000`：预估删除范围（conversations + messages）≥ 1000 时，不同步执行。
- 入队 `deferred_deletion_jobs`，返回 `{"status": "accepted", "deferred": true, "scope": N}`。
- housekeeping 周期任务 `drain_deferred_jobs` 批量处理（默认 50 条/批），完成后写 deletion proof 并审计。

### 7. Tombstone-after-restore

- 每次删除执行后写 `customer_tombstones`（tenant_id, customer_ref, request_id, deleted_at, deleted_by, secret_hash）。
- 应用启动时（`main.py` lifespan）调用 `enforce_tombstones_after_restore()`，重放所有 tombstone 删除。
- 恢复旧备份后，已删除客户数据不会复活。

## 后果

- 优点：
  - 字段级分类机器可读，支撑后续数据地图与保留策略自动化。
  - 统一红action 消除各模块自行处理的不一致；canary 测试提供回归保障。
  - DSR 全生命周期可观测（SLA、看板、证明、重试），满足隐私合规审计。
  - 大规模删除不阻塞 API，避免超时与资源耗尽。
  - 备份恢复后数据一致性有机制保障。
- 代价 / 风险：
  - `execution_secret` 需安全保管，丢失则无法查询删除证明（设计使然：证明需持有秘密）。
  - 红action 可能误伤合法内容（如长 hex 字符串），需通过 canary 测试与字段白名单平衡。
  -  deferred 删除依赖 housekeeping 运行，需监控队列积压。
- 迁移路径：
  - migration 32 幂等，新旧版本可共存。
  - 既有 DSR 请求无 `sla_due_at` / `execution_secret`，视为无 SLA / 不可证明，不影响功能。

## 相关

- `docs/adr/0008-m0-security-hardening.md`（SEC-002 DSR maker-checker）
- `docs/adr/0011-audit-external-anchoring.md`（审计锚定，删除证明的审计事件）
- `app/privacy.py`、`app/redaction.py`、`app/retention.py`
- `tests/test_privacy.py`、`tests/test_dsr.py`
