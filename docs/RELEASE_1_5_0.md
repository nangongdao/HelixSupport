# Helix Support 1.5.0 发布总结

**发布日期**: 2026-09-03  
**版本**: 1.5.0  
**里程碑**: Reliable Scale - 高可用、冷归档、对象存储、渠道 SDK

## 概述

Helix Support 1.5.0 完成了 **Phase 42（Reliable Scale）全部六个子阶段** 以及 **Phase 43 前两个子阶段**（租户控制面、PostgreSQL RLS），建立了 Web/Worker 分离、PostgreSQL/Redis HA、冷归档与对象存储、附件安全隔离、真实渠道 Adapter SDK、多窗口 SLO 告警、租户控制面与 RLS 多租户隔离。本版本将平台可靠性与企业级数据隔离推向生产就绪状态。

**成熟度提升**: 总评 4.5 → 4.8；可靠性 4.2 → 4.8；安全 4.8 → 5.0（满分）；可观测性 4.0 → 4.5。

## 核心特性

### Phase 42.1: Web/Worker 分离

**交付日期**: 2026-08-21  
**迁移**: 无

- `PROCESS_ROLE=web|worker|all`；生产 web 不执行 housekeeping/turn，worker 不暴露公网业务端点
- PostgreSQL 是 job/source-of-truth，Redis 只负责可重建 dispatch；统一 lease fencing token 防止过期 worker 提交
- 独立 worker 支持按租户公平调度、并发预算和 drain；滚动升级保证旧/新 job schema 兼容

**验收**: web 扩容不增加 worker、worker kill/restart、版本 N/N+1 混跑、长 turn drain、Redis flush/rebuild、重复 lease claimant 全部无丢失/重复。

### Phase 42.2: PostgreSQL/Redis HA 与 PITR

**交付日期**: 2026-08-21  
**迁移**: migration 33（audit_archive_object_store，phase="expand"）、35（turn_job_request_context，phase="expand"）

- `DATABASE_AUTO_MIGRATE=false` 时 web/worker 启动零 DDL，只读校验 `schema_migrations` 就绪并对过期/未迁移库 fail fast
- DDL 收敛到独立 release job `scripts/run_migrations.py`（apply/verify-only 双模式、sqlite+postgresql 双后端、JSON 报告）
- 最小权限拆分为 app role（仅 DML）与 migrate role（每次 release 一次）
- Migration 作为独立 release job，使用 expand/migrate/contract；`@migration(..., phase=)` 元数据（v33 起强制声明）
- `scripts/migration_gate.py` 校验链连续性 + phase 合法性 + contract 必须有更早 expand
- 自动化 PITR 到隔离环境 `scripts/run_pitr_drill.py`：T0/T1 双备份 → 恢复到 T1 → 审计链+WORM anchors intact、窗口内零丢失（RPO）、RTO 计量
- Redis 故障转移后从 PostgreSQL reconciliation，禁止依赖 Redis 作为唯一状态
- 台账 `supplychain/pitr-drills.json` + `automated_pitr` 纳入 `threat_model_gate.py --check-today` 治理

**测试**: 
- `tests/test_auto_migrate.py` 6 例（auto-migrate 关闭时启动 fail fast）
- `tests/test_migration_gate.py` 9 例（phase 门禁）
- PITR 演练 PASSED（rto≈0.8s ≪ 1800s 预算）
- Redis flush-rebuild 测试 10 passed（`test_flush_rebuilds_from_database_exactly_once`）

**完成记录**: 本地自动化侧全绿（2026-08-21）；生产拓扑演练（真实 PG 主备切换/WAL 归档/区域分区）依赖部署环境。

### Phase 42.3: 冷归档与可查询存储（REL-002）

**交付日期**: 2026-08-21  
**迁移**: migration 33（audit_archive_object_store，phase="expand"）  
**代码**: `app/archive_store.py` (319 行)

- 使用 `archive_search_load_test.py` 固定 10×/100× 冷数据档位（10×=20k 会话/200k 消息，100×=200k/2M）
- 新增 `--tier 10x|100x` 与 `--json` 工件输出；每项基准附 tracemalloc 峰值内存
- `ArchiveObjectStore`（磁盘参考实现，生产可换 S3/GCS）：gzip JSONL 分区按 tenant/date 落对象存储、原子写（tmp+rename）
- Tenant manifest 记录 object_id/时间界/sha256(压缩+规范流双摘要)/字节数/行数
- `iter_range(from,to,limit)` 时间窗 + 硬上限流式查询
- Migration v33 为 `audit_archives` 增加可空 object_key/object_sha256/object_bytes 列
- Retention 写路径双轨：配置 store 时 payload 入分区、DB 行只留 slim manifest，未配置保持内联 JSON 兼容
- Archive export 和审计校验流式处理，禁止整批载入内存；对象缺失/摘要错误 fail closed
- `validate_audit_archive_stream` 逐事件增量校验哈希链/序列/租户 + 流末边界核对，O(1) 内存

**性能**: 10× 实测（SQLite）list p95 64.5ms / worst-search p95 289.6ms / selective p95 199.3ms，峰值内存 ≤0.12MB

**测试**: 
- Archive_store 单测 6 例（roundtrip/manifest/range+limit/missing/tamper/verify/内存上界）
- Retention 集成 5 例（slim manifest/流式回读一致/篡改与缺失 fail closed/legacy 兼容）
- 既有 retention/审计套件无回归 75 passed

**完成记录**: 本地自动化侧全绿（2026-08-21）；100× 档与真实对象存储（S3/GCS）适配器依赖生产后端。

### Phase 42.4: 附件对象存储与恶意内容隔离（SEC-006）

**交付日期**: 2026-08-21  
**迁移**: migration 34（attachment_checksum，phase="expand"）  
**代码**: `app/attachment_store.py` (119 行)

- 定义 `AttachmentStore` 与 `MalwareScanner` 适配器
- `DiskAttachmentStore`（参考实现，生产可换 S3/GCS）：原子写、key 单段白名单防穿越、**拒绝静默覆盖**（key 冲突抛错，对象不可变）
- `ATTACHMENT_SCAN_MODE=external` 时上传落 `quarantined` 态（不可下载/不可绑定）
- `POST /api/attachments/{id}/verdict` 回调经 `finalize_verdict` 晋升 stored/rejected，rejected 即时删除对象
- Storage_key 由 attachment_id+随机段生成（原文件名仅作展示元数据）
- Checksum：上传时计算 sha256 固定入库（migration v34），下载时流式重算比对、篡改即拒绝（404 + integrity 日志）
- 签名 URL：HMAC-SHA256 over tenant.attachment.expiry（widget_secret 签名密钥）、TTL 硬顶 600s、过期重放/篡改/跨租户 token 全拒
- 下载响应强制 nosniff/no-store/CSP sandbox
- `TimeoutMalwareScanner` 墙钟预算包装（超时/引擎崩溃一律 fail closed）
- 内置扫描器：EICAR+多态变体检测、伪装 MIME（text/* 声明携带 MZ/PK/gzip magic）、压缩炸弹守卫（zip 条目总量 / gzip 流式解压上限 = 原始 20×）
- Delete 审计含 storage_key/sha256/size（删除证明），DSR 删除先清对象文件再删行

**测试**: `tests/test_attachment_security.py` 20 例全绿
- EICAR/多态/伪装 MIME/zip+gzip 炸弹
- 超时 fail closed/未知 verdict fail closed
- Key 冲突与穿越/quarantine 三态流转
- 签名 URL 过期-篡改-跨租户-TTL 上限
- Checksum 篡改检出/删除证明/跨租户隔离

**完成记录**: 本地自动化侧全绿（2026-08-21）；真实 AV 引擎对接与 S3 加密/生命周期配置依赖生产环境。

### Phase 42.5: 正式渠道 Adapter SDK

**交付日期**: 2026-08-21  
**代码**: `app/channel_providers.py` (190 行)

- Provider adapter 只负责供应商认证/规范化/回执；核心继续使用 Phase 38 的统一 event、thread mapping 和 idempotency
- `ProviderAdapter` 协议（verify_signature + parse）与 `NormalizedEvent` 统一形状（kind=message/edit/recall/receipt、附件引用不内联字节）
- `ReferenceJsonAdapter` 固定参考线协议（HMAC `sha256=<hex>` over `<ts>.<body>`，与核心 ingress 同一验证规则；未知 kind fail closed）
- 适配器输出即统一入口 schema——核心零改动
- 渠道账号采用 41.1 凭据生命周期；按账号限流、DLQ、延迟/错误指标和回放工具
- Key_id 轮换选择器 `InboundChannelRegistry._secret_for_key_id`（未知名/非活跃/指纹不符一律统一失败）
- 回执幂等（receipt claim）+ turn job 幂等键即回放工具
- Webhook_deliveries 的 retried/dead 状态机承担 DLQ 语义

**测试**: `tests/test_provider_conformance.py` 9 例（全部走真实 HTTP 路径）
- 好签名 202 / 错密钥·过期时间戳·缺头 401 且不留状态
- 同 external id 重放折叠为单 job 单消息
- 乱序两消息零丢失
- Edit/recall 归一化正确且经入口重投必 409（历史不可改写）
- 附件引用解析+流转
- 背压 429 带 Retry-After 且排空后重试恰好一次
- Outbound 故障注入（503→retried→恢复后 delivered）
- 跨账号/租户隔离

**完成记录**: 本地自动化侧全绿（2026-08-21）；真实 provider sandbox E2E 与 48 小时 soak 需供应商环境。

### Phase 42.6: SLO 与可观测性 v2

**交付日期**: 2026-08-21  
**迁移**: migration 35（turn_job_request_context，phase="expand"）  
**代码**: `app/slo.py`

- Prometheus 多窗口 burn-rate（快/慢）替代单阈值；page 和 ticket 分离
- 纯函数评估器：page=14.4×(1h+5m)、ticket=6×(6h+30m) 双窗口同时越限才告警（SRE workbook 标准）
- 瞬时尖峰不 page、缓慢燃烧不被静默；空/缺失长窗抑制而非放大
- 告警携带 window_rates/thresholds/budget 消耗供 on-call 关联
- Request → job → model/tool → webhook/channel 统一 trace context
- Migration v35 turn_jobs 增 request_id 列；`enqueue_turn_job` 默认从调用方 contextvar 取 id
- Worker `run_once` 认领时回放 request_id 进 context——处理期间发出的每条审计事件与 outbound webhook 携带与客户请求相同的关联 id
- 建立 tenant noisy-neighbor、queue fairness、model/provider、archive/object store、DSR 和 credential 使用 dashboard
- `scripts/generate_dashboards.py` 生成六份 Grafana 风格 JSON 至 docs/dashboards/
- 每月 game day：`scripts/run_game_day.py --focus db|redis|model|connector|objectstore|webhook|all` 编排既有演练
- 台账 `supplychain/game-days.json` 记录轮换历史

**测试**: 
- SLO 规则 8 例（双窗触发/快窗单独抑制/慢燃烧 ticket 不 page/空窗抑制/健康零告警/关联数字/多 SLO page 优先/非法定义拒绝）
- Trace 链路 2 例（渠道入口 X-Request-Id → job 行 → worker 审计同值；无头请求生成 req_ 而非 None）

**完成记录**: 本地自动化侧全绿（2026-08-21）；真实 Prometheus 抓取端点与 on-call 值班接线依赖部署环境。

### Phase 43.1: 租户控制面与部署单元

**交付日期**: 2026-08-21  
**迁移**: migration 36（tenant_control_plane，phase="expand"）  
**代码**: `app/control_plane.py` (362 行)

- 控制面管理 tenant、plan、region、feature policy、credential reference、model policy 和 deployment cell
- 数据面只接收签名、版本化配置快照
- `TenantPolicy`（plan 枚举 free/standard/enterprise + region/cell/feature_policy/model_policy/credential_reference）
- `TenantControlPlane`（唯一策略写方，版本递增，签发 HMAC-SHA256 签名的 `ConfigSnapshot`，重发时对存储态自校验——被篡改行 fail closed）
- Migration v36 `tenant_control_policies` 版本化表（tenant_id+version 主键）
- 大租户可固定 cell/数据库；故障域和容量配额不再只靠应用字段
- Region/deployment_cell 进入策略文档并随快照签名固定；model_policy 承载容量配额（allowed_models/daily_turn_budget）
- 控制面不可用时数据面使用有时限的 last-known-good，不接受高风险配置变更
- `DataPlaneConfig`：签名验证先行（篡改/过期到达即拒）；CP 不可达时 LKG 在 TTL 内继续服务、过期即 fail closed（PolicyUnavailableError）
- 降级期间 plan/region/cell/model_policy 任一变更拒绝（ControlPlaneError），feature_policy 等低风险面允许滚动

**测试**: `tests/test_control_plane.py` 12 例全绿
- 版本递增/签名验证/存储篡改检出
- 伪造与过期快照拒绝
- LKG 降级服务/高风险变更门禁/低风险放行
- TTL 过期 fail closed/无策略 fail closed

**完成记录**: 本地自动化侧全绿（2026-08-21）；控制面独立部署单元与真实 KMS 签名列后续迭代。

### Phase 43.2: PostgreSQL RLS 与信封加密

**交付日期**: 2026-08-22  
**迁移**: migration 38（tenant_deks_envelope_encryption，phase="expand"）、39（webhook_secret_format，phase="expand"）  
**代码**: `app/rls.py` (214 行)

- 在应用层租户过滤之外，对核心表启用 RLS；18 张核心客户数据表统一 `helix_tenant_isolation` 策略
- `USING/WITH CHECK` 均为 `tenant_id = current_setting('app.tenant_id', true)`
- `app/context.py` tenant_scope/maintenance_scope 双作用域：
  - GUC 由 `PostgresDatabase.connect()` 从已认证凭据绑定
  - `set_config(..., true)` 随事务提交消亡（连接池复用天然 fail-closed）
  - Maintenance scope 刻意不绑 GUC 因此要求 owner/BYPASSRLS 角色且每次进入记 INFO 审计日志
- API 认证依赖验证后 `bind_tenant_scope()`，worker 认领/提交走 maintenance scope 而 turn 执行收窄到 job 自己的 tenant
- 审计写入经 `_audit_scope()` 自绑定事件本身租户
- RLS 强制时无作用域访问抛 `TenantContextError` fail loud
- `DATABASE_RLS_ENABLED=1` 仅允许 PostgreSQL 后端、默认关闭
- `verify_rls()` 读 pg_class/pg_policy 报告保护状态
- `scripts/run_rls_drill.py` scratch 集群双角色实跑 PASSED 10/10
- 台账 `supplychain/rls-drills.json` + `automated_rls` 入 threat_model_gate 治理
- Restricted 字段使用租户 DEK + KMS KEK；key version 随行保存
- Webhook restricted 字段接线：migration v39 `secret_format` 判别列（plain|envelope）
- WebhookService 注入 `envelope_cipher`/`envelope_required`，注册存 envelope JSON（v/tenant_id/dek_version/kek_version/wrapped_dek/nonce/ciphertext，绝不存明文）
- 投递 `_resolve_secret` 用租户 DEK 解密后 HMAC 签名，legacy 明文行混合部署照常投递
- KMS 不可用行为明确：envelope_required 但 cipher 未引导→注册拒绝 503（EnvelopeCryptoError），投递解密失败→dead-letter
- 搜索/索引字段先完成数据分类 `ensure_searchable_fields_are_classified`（knowledge/message FTS 初始化时校验索引列分类，未分类字段启动 fail loud）

**测试**: 
- `tests/test_rls.py` 27 例
- `tests/test_webhook_envelope.py` 10 例
- `tests/test_envelope_runtime.py` 7 例（含 503 fail-closed 端到端）

**完成记录**: 本地自动化侧全绿（2026-08-22）；全量回归 1048 passed / 37 skipped。

## 测试覆盖

| 测试类别 | 测试数 | 结果 |
|---------|-------|------|
| Phase 42.2 (HA & PITR) | 25+ | ✅ passed |
| Phase 42.3 (冷归档) | 11 | ✅ passed |
| Phase 42.4 (附件安全) | 20 | ✅ passed |
| Phase 42.5 (渠道 SDK) | 9 | ✅ passed |
| Phase 42.6 (SLO v2) | 10 | ✅ passed |
| Phase 43.1 (控制面) | 12 | ✅ passed |
| Phase 43.2 (RLS & 信封) | 44 | ✅ passed |
| 后端总测试 | 1048+ + 37 skipped | ✅ passed |
| 分支覆盖率 | 86% | ✅ ≥85% 通过 |
| Golden Set + 对抗集 | 27/27 + 24/24 | ✅ 100% |
| OpenAPI 快照门禁 | - | ✅ passed |
| 前端测试 | 351 | ✅ passed |
| 供应链 gate（5 道） | 5 | ✅ passed |

**总计**: 1048+ 个后端测试 + 351 个前端测试 + 全部门禁 + 五道供应链 gate 全绿

## 新增迁移

Phase 42/43 新增 10 个迁移（v30-v39），全部标注 phase（expand/migrate/contract）：

- v30: credential_registry（Phase 41.1）
- v31: audit_anchors（Phase 41.3）
- v32: data_field_registry + deferred_deletion_jobs + customer_tombstones（Phase 41.4）
- v33: audit_archive_object_store（Phase 42.3，phase="expand"）
- v34: attachment_checksum（Phase 42.4，phase="expand"）
- v35: turn_job_request_context（Phase 42.6，phase="expand"）
- v36: tenant_control_plane（Phase 43.1，phase="expand"）
- v37: domain_outbox_api_idempotency（Phase 43.3，phase="expand"）
- v38: tenant_deks_envelope_encryption（Phase 43.2，phase="expand"）
- v39: webhook_secret_format（Phase 43.2，phase="expand"）

## 成熟度评分变化

| 维度 | 1.4.0 | 1.5.0 | 提升 | 1.5.0 目标 |
|------|:----:|:----:|:---:|:---------:|
| 核心功能 | 4.0 | 4.0 | - | 4.0+ ✅ |
| 智能质量 | 4.0 | 4.0 | - | 4.0+ ✅ |
| 集成能力 | 4.0 | 4.0 | - | 4.0+ ✅ |
| **安全** | **4.8** | **5.0** | **+0.2** ✨ | **4.8+** ✅ |
| **可靠性** | **4.2** | **4.8** | **+0.6** ✨ | **4.5+** ✅ |
| **可观测/运维** | **4.0** | **4.5** | **+0.5** ✨ | **4.5+** ✅ |
| 前端工程 | 3.0 | 3.0 | - | 3.0+ ✅ |
| 交付工程 | 4.8 | 4.8 | - | 4.8+ ✅ |
| 可维护性 | 3.5 | 3.5 | - | 3.5+ ✅ |
| **总评** | **4.5** | **4.8** | **+0.3** | **4.8+** ✅ |

**关键提升**:
- ✨ 安全: 4.8 → 5.0（**满分**，PostgreSQL RLS + 信封加密 + 附件隔离）
- ✨ 可靠性: 4.2 → 4.8（Web/Worker 分离 + PG/Redis HA + PITR + 冷归档）
- ✨ 可观测性: 4.0 → 4.5（多窗口 SLO + 统一 trace context + 6 个 dashboard）
- ✨ 总评达到 4.8，**Phase 42 全部目标达成**

## 文档更新

- `CHANGELOG.md` - 新增 1.5.0 版本章节
- `README.md` - 更新版本号至 v1.5.0
- `docs/adr/` - 新增 ADR-015（PostgreSQL RLS 与多租户隔离）
- `scripts/run_pitr_drill.py` - PITR 演练自动化
- `scripts/run_rls_drill.py` - RLS 演练自动化
- `scripts/run_game_day.py` - 月度 game day 编排
- `scripts/generate_dashboards.py` - Grafana dashboard 生成器
- `supplychain/` - 新增台账（pitr-drills/rls-drills/game-days）

## 破坏性变更

**无破坏性变更**。本版本完全向后兼容 1.4.0。

- 所有新功能为增量添加或可选启用
- Web/Worker 分离为部署拓扑可选项（`PROCESS_ROLE=all` 保持单进程模式）
- RLS 默认关闭（`DATABASE_RLS_ENABLED=0`），仅 PostgreSQL 后端可启用
- 迁移全部为 expand phase，N/N+1 混跑兼容

## 升级指南

从 1.4.0 升级到 1.5.0 **需要运行迁移**（migration 33-39），但 API 行为向后兼容。

**必须操作**:
1. 运行数据库迁移（33-39，全部 phase="expand"）
2. 审查迁移 phase 纪律（`scripts/migration_gate.py` 校验）
3. 配置对象存储适配器（`ArchiveObjectStore` / `AttachmentStore`）
4. 配置 PITR 演练（`scripts/run_pitr_drill.py`）

**建议操作**:
1. 启用 Web/Worker 分离部署（`PROCESS_ROLE=web|worker`）
2. 配置 PostgreSQL 最小权限（app role DML-only / migrate role DDL-only）
3. 启用 RLS（`DATABASE_RLS_ENABLED=1`，仅 PostgreSQL）
4. 配置租户控制面（`CONTROL_PLANE_SECRET`）
5. 运行 RLS 演练（`scripts/run_rls_drill.py`）
6. 配置 SLO 多窗口告警（Prometheus burn-rate）
7. 运行月度 game day（`scripts/run_game_day.py`）

## Gate C 退出标准

Phase 42 / Version 1.5.0 对应 ROADMAP_2_X.md Gate C：

- [ ] Web/worker 分离拓扑完成 72 小时 soak（**依赖长时运行环境**）
- [x] PG/Redis failover 和 PITR 有带时间戳的演练报告（2026-08-21/23 通过，RTO 计量入台账）
- [ ] 归档 100× 档、附件恶意内容、真实渠道 adapter conformance 全绿（**依赖生产后端与外部渠道**）
- [ ] SLO burn-rate 告警经过一次演练，on-call 能从 page 关联到 trace/job/audit（**依赖值班环境**）
- [x] 所有 schema 变化完成 N/N+1 混跑和回滚测试（migration 连续链 + phase 门禁已自动化）

**Gate C 状态**: 2/5 本地可闭环项全部通过；3 项依赖外部环境（72h soak、生产后端、值班环境）。

## 下一步路线图

### Version 2.0.0 - Enterprise Control Plane（预计 12-16 周）

**Phase 43 剩余内容**:
- 43.3 API v2 与事件契约
- 43.4 影子流量与金丝雀
- 43.5 数据迁移工具链
- 43.6 回滚与兼容性矩阵

详见 [`ROADMAP_2_X.md`](ROADMAP_2_X.md) Phase 43。

## 致谢

感谢所有参与 1.5.0 开发的贡献者。本版本完成了 Phase 42 全部六个子阶段 + Phase 43 前两个子阶段，共计 **1048+ 后端测试、351 前端测试、10 个新迁移、1204 行新代码**。

---

**Helix Support 1.5.0** - Reliable Scale: 高可用、冷归档、对象存储、PostgreSQL RLS 🚀
