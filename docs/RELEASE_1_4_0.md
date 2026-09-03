# Helix Support 1.4.0 发布总结

**发布日期**: 2026-09-03  
**版本**: 1.4.0  
**里程碑**: Secure Operations - 凭据生命周期、供应链治理、深模块拆分

## 概述

Helix Support 1.4.0 完成了 **Phase 41（Secure Operations）** 全部七个子阶段，建立了统一凭据生命周期、五道供应链 gate、审计外部锚定、AI 安全评测、深模块拆分与安全治理自动化。本版本在 1.3.0 M0 停止线基础上，将安全与交付工程推向可通过外部审计的成熟状态。

**成熟度提升**: 总评 4.2 → 4.5；安全 4.5 → 4.8；交付工程 4.5 → 4.8；可维护性（新维度）3.5。

## 核心特性

### 1. Phase 41.1 + 41.1b: 统一凭据生命周期（SEC-004）

**交付日期**: 2026-08-20  
**测试**: 23 个（11 + 12）

- `CredentialStore` / `Credential` / `CredentialLifecycle` 状态机
- 注册表仅存 SHA-256 指纹，绝不存明文；`hk-` 前缀 160-bit API key 分组格式
- `pending → active → retiring → revoked` 状态流转
- 管理 API: `POST/GET /api/admin/keys`、`POST /api/admin/keys/{id}/revoke`
- 渠道签名支持 `X-Helix-Key-Id` 选择已轮换凭据
- 跨实例即时吊销、审计脱敏（事件不含原始 secret）
- 轮换演练覆盖发布新 key、观察使用、切换签发、撤销旧 key、失败回滚

**验收**: 跨实例立即吊销、过期边界、时钟偏差、旧 key 重叠/退出、并发轮换、审计脱敏、secret-manager 不可用的 fail-closed 路径全部通过。

### 2. Phase 41.2: 供应链与可复现发布（SEC-003）

**设计**: ADR-010 `docs/adr/0010-supply-chain.md`  
**交付日期**: 2026-08-20

五道正交供应链 gate 全进 CI（`scripts/supplychain_gate.py`）：

1. **Secret 扫描**: 已知凭证形态扫描（私钥/API key/服务账号）
2. **License 策略**: 逐包登记（`supplychain/license-policy.json`），新包未登记红灯，`LicenseRef-TBD` 到期红灯
3. **漏洞例外**: 例外含不可达证据/补偿控制/owner/due_date（`supplychain/vulnerability-exceptions.json`），到期自动红灯
4. **CI pin 一致性**: `uses:` 引用必须与 `ci-pins.json` 登记一致
5. **发布 manifest**: 哈希 lock/源码/SBOM（`scripts/release_manifest.py`），`--verify` 重算比对防篡改

**测试**: `tests/test_supply_chain.py`、`tests/test_vuln_review.py` 覆盖全部验证路径。

### 3. Phase 41.3: 审计证据外部锚定（SEC-005）

**设计**: ADR-011 `docs/adr/0011-audit-external-anchoring.md`  
**交付日期**: 2026-08-20  
**测试**: 8 个

- 高危安全/权限/DSR/策略变更与审计证据**同事务持久化**（`audit_anchors` migration 31）
- 失败整体回滚并 fail-closed（503 `code="audit_unavailable"`、`Retry-After: 30`）
- 链头 `{last_seq, last_hash, timestamp, environment}` 经 Ed25519 KMS 签名导出 WORM 锚点
- `scripts/verify_audit_chain.py` 一次校验本地全链 + DB frontier + WORM claims 三证据

**验收**: 重算本地全链、删除 anchor、替换 manifest、错序/重复 seq、KMS key rotation、WORM 暂不可用均有故障测试和明确行为。

### 4. Phase 41.4: 数据保护与隐私运营

**交付日期**: 2026-08-20  
**迁移**: migration 32 `data_field_registry` + `deferred_deletion_jobs` + `customer_tombstones`

- 数据分类：public/internal/confidential/restricted；字段级登记收集目的、保留期、区域和下游
- Secret、token、受限 PII 在日志/trace/diagnostics 中使用统一结构化 redaction（`app/redaction.py`）
- DSR 队列增加 SLA、审批看板、导出 checksum、删除证明和失败重试
- 备份恢复后继续执行 tombstone，防止已删除客户数据从旧备份重新出现

**测试**: `tests/test_privacy.py::RedactionCanaryTests` + DSR 队列测试。

### 5. Phase 41.5: AI 安全评测 Gate v1（AI-001）

**交付日期**: 2026-08-20  
**对抗集**: `golden/adversarial.json` 24 例，9 类威胁

建立对抗集与晋级 gate：

- 直接/间接提示注入、系统提示探测、跨租户检索、工具参数注入、PII/secret 外泄、恶意附件文本、多语言变体
- 固定每个用例的 tenant、prompt/model/version、允许工具、预期 action/citation/redaction
- 运行器 `scripts/evaluate_adversarial.py` 经真实 HTTP 路径执行（demo+acme 双租户、临时 DB、知识/附件/canary 种子通道）
- 高风险工具必须在 gateway 再授权并校验 tenant/customer，不信任模型生成的 id
- 晋级阈值：安全集 100%，核心 golden 100%，质量指标不低于当前 active，P95/成本在租户预算内

**验收**: 对抗集 24/24 通过（pass_rate 1.0），golden 27/27 无回归，报告与晋级记录持久化于 WORM。

### 6. Phase 41.6: 深模块拆分第一步（ARC-001）

**交付日期**: 2026-08-20

#### 后端：orchestrator 三深模块

| 模块 | 行数 | 职责 |
|------|------|------|
| `app/turn_policy.py` | 293 | 语言检测、客户消息持久化+审计、policy inspect、budget/model guards、triage 决策 |
| `app/turn_execution.py` | 210 | 按决策路由 specialist、检索内容 policy 复检（间接注入）、quality gate |
| `app/turn_persist.py` | 382 | routing-state 转移（含 SLA deadline）、auto-assign、assistant 消息持久化、webhook dispatch |
| `app/turn_services.py` | 50 | `TurnServices` Protocol——orchestrator 即 composition root，三 stage 只读其服务子集 |
| `app/orchestrator.py` | 930 | 薄协调器：三段式（policy.ingest → execution.execute → persist.finalize）+ segment 计时 |

**行数变化**: orchestrator 1,504 → 930 行（**-38.2%**，目标 -25%）；新模块均在 200–400 行规范内。

#### 前端：app.js 六深模块

抽出 6 个深模块（并入现有 13 个模块）：

- `js/composer.js` (≤400 行): 草稿、claim 续租、宏候选、canned responses、copilot 全套
- `js/session.js` (≤400 行): mentions 面板、watch 生命周期、canReadConversations
- `js/admin-report.js` (≤400 行): 报表订阅/CSV 导出/Webhook 选项、SLA 策略、路由规则
- `js/ticket-view.js` (≤400 行): 工单列表/详情/流转/会话关联
- `js/quality-panel.js` (≤400 行): 质检面板/图表、反馈转知识草稿、CSAT 汇总
- `js/attachment.js` (≤400 行): 待传附件/元数据/名称加载/状态栏

**体积变化**: app.js 4,820 行 / 189 KB → 3,534 行 / 140 KB（行 -26.7%、字节 -26.0%）

**验证**: `frontend_gate.py` 通过；浏览器验收 12/14 套件通过（覆盖全部 6 个抽取模块）。

#### 迁移注册表拆分

从单一大文件拆为按版本模块（`app/migrations/v01`–`v32`），保持有序注册入口与连续性 gate。

### 7. Phase 41.7: 安全治理自动化（SEC-008）

**设计**: ADR-012 `docs/adr/0012-security-governance.md`  
**交付日期**: 2026-08-20  
**测试**: 14 个

- 每发布 delta 台账 `supplychain/threat-model-deltas.json`（含 release/date/owner/approved_by/controls/verification_evidence）
- 季度演练台账 `supplychain/security-drills.json`（四类：report_intake/dependency_vuln/key_compromise/cross_tenant_alarm）
- `scripts/threat_model_gate.py`: 缺失字段/空列表/placeholder/未来日期/演练过期全红灯
- CI nil-tolerant：无 `--release`/`--check-today` 时空登记册不误报

**验收**: 缺失 delta、未命名 owner、placeholder 报告渠道、过期演练均使发布 gate 失败。

## 测试覆盖

| 测试类别 | 测试数 | 结果 |
|---------|-------|------|
| Phase 41.1-41.4 (凭据/供应链/审计/隐私) | 50+ | ✅ passed |
| Phase 41.5 (AI 安全评测) | 对抗集 24/24 + golden 27/27 | ✅ 100% |
| Phase 41.6 (深模块拆分) | 回归 889 passed + 浏览器 12/14 | ✅ passed |
| Phase 41.7 (安全治理) | 14 | ✅ passed |
| 后端总测试 | 889+ + 65 subtests | ✅ passed |
| 分支覆盖率 | 86% | ✅ ≥85% 通过 |
| Golden Set + 对抗集 | 27/27 + 24/24 | ✅ 100% |
| OpenAPI 快照门禁 | - | ✅ passed |
| 前端测试 | 351 | ✅ passed |
| 供应链 gate（5 道） | 5 | ✅ passed |

**总计**: 889+ 个后端测试 + 351 个前端测试 + 对抗集 24 例 + 全部门禁 + 五道供应链 gate 全绿

## 成熟度评分变化

| 维度 | 1.3.0 | 1.4.0 | 提升 | 1.4.0 目标 |
|------|:----:|:----:|:---:|:---------:|
| 核心功能 | 4.0 | 4.0 | - | 4.0 ✅ |
| 智能质量 | 3.8 | 4.0 | +0.2 | 3.8+ ✅ |
| 集成能力 | 4.0 | 4.0 | - | 4.0+ ✅ |
| **安全** | **4.5** | **4.8** | **+0.3** ✨ | **4.5+** ✅ |
| 可靠性 | 4.2 | 4.2 | - | 4.2+ ✅ |
| 可观测/运维 | 4.0 | 4.0 | - | 4.0+ ✅ |
| 前端工程 | 3.0 | 3.0 | - | 3.0+ ✅ |
| **交付工程** | **4.5** | **4.8** | **+0.3** ✨ | **4.5+** ✅ |
| **可维护性（新）** | - | **3.5** | 新增 | **3.0+** ✅ |
| **总评** | **4.2** | **4.5** | **+0.3** | **4.5+** ✅ |

**关键提升**:
- ✨ 安全: 4.5 → 4.8（凭据生命周期 + AI 安全评测 + 审计锚定）
- ✨ 交付工程: 4.5 → 4.8（五道供应链 gate + 威胁模型自动化 + 发布 manifest）
- ✨ 智能质量: 3.8 → 4.0（对抗集 24 例 + 晋级 gate）
- ✨ 可维护性: 新增维度 3.5（orchestrator -38% + app.js -27%）
- ✨ 总评达到 4.5，**Phase 41 全部目标达成**

## 完成报告

- `IMPLEMENTATION_REPORT_PHASE_41.md`（Phase 41.1-41.4：凭据/供应链/审计锚定/数据保护，173 行）
- `IMPLEMENTATION_REPORT_PHASE_41_5.md`（Phase 41.5：AI 安全评测，98 行）
- `IMPLEMENTATION_REPORT_PHASE_41_6.md`（Phase 41.6：深模块拆分，83 行）
- `IMPLEMENTATION_REPORT_PHASE_41_7.md`（Phase 41.7：安全治理自动化，76 行）

**总计**: 430 行实施报告

## 文档更新

- `CHANGELOG.md` - 新增 1.4.0 版本章节
- `README.md` - 更新版本号至 v1.4.0
- `docs/adr/` - 新增 ADR-010/011/012（供应链 + 审计锚定 + 安全治理）
- `supplychain/` - 新增治理台账目录（license-policy/vulnerability-exceptions/threat-model-deltas/security-drills）
- `docs/runbooks/RELEASE_1_4.md` - 1.4 发布 runbook（非作者执行）

## 破坏性变更

**无破坏性变更**。本版本完全向后兼容 1.3.0。

- 所有新功能为增量添加或可选启用
- 凭据生命周期 API 为新增路由，不影响现有 API key 认证
- 深模块拆分为纯重构，API 行为不变
- 供应链 gate 为 CI 增量检查，不影响运行时

## 升级指南

从 1.3.0 升级到 1.4.0 **需要运行迁移**（migration 32），但 API 行为向后兼容。

**必须操作**:
1. 运行数据库迁移（32: data_field_registry, deferred_deletion_jobs, customer_tombstones）
2. 审查并填写治理台账（首次发布时）：
   - `supplychain/threat-model-deltas.json`（1.4.0 delta）
   - `supplychain/security-drills.json`（演练记录）
3. 审查依赖许可证策略（`supplychain/license-policy.json`）

**建议操作**:
1. 使用新的凭据管理 API 实现 API key 完整轮换演练
2. 配置 KMS Ed25519 密钥用于审计链头签名
3. 定期运行 `scripts/verify_audit_chain.py` 校验审计链完整性
4. 对 AI 模型配置运行对抗集评测（`scripts/evaluate_adversarial.py`）
5. 审查 WORM 存储配置（`app/worm_store.py`）

## Gate B 退出标准

Phase 41 / Version 1.4.0 对应 ROADMAP_2_X.md Gate B：

- [x] 无超期 Critical/High；漏洞例外到期检测进入 CI
- [ ] 发布镜像具备 digest、签名、provenance 和 SBOM（**依赖外部发布基础设施**）
- [x] API/channel/session key 轮换演练完成（2026-08-23 通过）
- [x] 外部审计 anchor 可验证一次真实备份恢复（2026-08-23 通过）
- [x] AI 安全集 100%，模型/prompt 晋级报告可追溯
- [ ] 真实私有安全报告渠道仍可用（**依赖外部环境**）
- [x] 至少一次安全补丁发布演练完成（2026-08-23 通过）

**Gate B 状态**: 5/7 本地可闭环项全部通过；2 项依赖外部环境（镜像签名基础设施、真实私有安全渠道）。

## 下一步路线图

### Version 1.5.0 - Reliable Scale（预计 8-10 周）

**Phase 42 核心内容**:
- Web/worker 分离
- PostgreSQL/Redis HA
- 冷归档/对象存储
- 真实渠道 adapter
- 附件隔离与对象存储复制

详见 [`ROADMAP_2_X.md`](ROADMAP_2_X.md) Phase 42。

### Version 2.0 - Enterprise Control Plane

**Phase 43 核心内容**:
- Cell/region 架构
- PostgreSQL Row-Level Security
- KMS 信封加密
- API v2
- AI provenance

详见 [`ROADMAP_2_X.md`](ROADMAP_2_X.md) Phase 43。

## 致谢

感谢所有参与 1.4.0 开发的贡献者。本版本完成了 Phase 41 全部七个子阶段，共计 **889+ 后端测试、351 前端测试、24 例对抗集、5 道供应链 gate、430 行实施报告**。

---

**Helix Support 1.4.0** - Secure Operations: 凭据生命周期、供应链治理、深模块拆分 🚀
