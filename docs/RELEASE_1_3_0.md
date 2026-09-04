# Helix Support 1.3.0 发布总结

**发布日期**: 2026-09-03  
**版本**: 1.3.0  
**里程碑**: 商用级可信：安全、可靠性、运维

## 概述

Helix Support 1.3.0 完成了从"企业级多租户平台"到"通过外部安全评审不需临时补救的商用成熟平台"的关键升级。本版本实现了 Phase 28（安全深化）、Phase 29（可靠性与过载工程）、Phase 30（可运维性与文档体系）的核心内容，通过 **M0 停止线（Phase 40）** 和 **Phase 41（Secure Operations）** 闭环了关键安全与可靠性风险。

**成熟度跨越**: 总评 3.7 → 4.2+；安全 3.5 → 4.5；可靠性 3.8 → 4.2；交付工程 4.0 → 4.5。

## 核心特性

### 1. M0 停止线（Phase 40）- 关键风险闭环

**40.1 SEC-001: OIDC Authorization Code Flow 加固**:
- 一次性 `auth_transactions` 防重放（migration 28）
- state/nonce/PKCE S256 verifier/redirect_uri/tenant_hint 绑定
- RS256-only + kid 轮换 JWKS 缓存
- iss/aud/exp/iat/nonce 强制校验
- identity 仅来自已验证 claims + tenant_members，无 demo/admin 回退
- 36 个负向测试覆盖全部拒绝路径

**40.2 SEC-002: 数据主体请求（DSR）maker-checker**:
- 申请人 ≠ 审批人 ≠ 执行人（migration 29）
- `idempotency_key` 唯一索引幂等重放
- 导出物 Fernet 加密、一次性下载 token ≤15 分钟
- 导出对象 ≤24 小时、无 `DSR_EXPORT_SECRET` 时 501 fail-closed
- 隐私权限（`privacy:request/approve/execute`）仅 ADMIN 持有
- 23 个测试覆盖角色分离与加密链路

**40.3 REL-001: 多实例队列 fail-closed**:
- Redis 不可用时 API 返回 503 + `Retry-After: 30`（`urn:helix:error:queue_unavailable`）
- 绝不静默降级为 SQLite，readiness 报告 degraded
- `deployment_profile=multi` 强制 PostgreSQL+Redis+fail-closed
- 四类 Redis 故障演练全部通过

**40.4 SEC-007: 安全报告最小闭环**:
- `SECURITY.md` 部署前配置检查清单
- 威胁模型见 `docs/SECURITY_MODEL.md`
- 负向测试进入 CI

### 2. Phase 41（Secure Operations）- 凭据与供应链

**41.1 + 41.1b: SEC-004 统一凭据生命周期**:
- `CredentialStore` / `Credential` / `CredentialLifecycle` 状态机
- 注册表仅存 SHA-256 指纹，绝不存明文
- `hk-` 前缀 160-bit API key 分组格式
- pending → active → retiring → revoked 状态流转
- 管理 API: `POST/GET /api/admin/keys`、`POST /api/admin/keys/{id}/revoke`
- 渠道签名支持 `X-Helix-Key-Id` 选择已轮换凭据
- 跨实例即时吊销、审计脱敏（事件不含原始 secret）
- 11 + 12 = 23 个测试覆盖轮换演练与并发竞争

**41.2: SEC-003 供应链与可复现发布**:
- `supplychain/` 配置目录 + 五个正交 gate 全进 CI:
  - **secret 扫描**: 已知凭证形态扫描（私钥/API key/服务账号）
  - **license 策略**: 逐包登记，新包未登记红灯，`LicenseRef-TBD` 到期红灯
  - **漏洞例外**: 例外含不可达证据/补偿控制/owner/due_date，到期自动红灯
  - **CI pin 一致性**: `uses:` 引用必须与 `ci-pins.json` 登记一致
  - **发布 manifest**: 哈希 lock/源码/SBOM，`--verify` 重算比对防篡改
- 设计见 `docs/adr/0010-supply-chain.md`

**41.3: SEC-005 审计证据外部锚定**:
- 高危安全/权限/DSR/策略变更与审计证据**同事务持久化**（`audit_anchors` migration 31）
- 失败整体回滚并 fail-closed（503 `code="audit_unavailable"`、`Retry-After: 30`）
- 链头 `{last_seq, last_hash, timestamp, environment}` 经 Ed25519 KMS 签名导出 WORM 锚点
- `scripts/verify_audit_chain.py` 一次校验本地全链 + DB frontier + WORM claims 三证据
- 8 个测试覆盖重算全链/删 anchor/替换 manifest/KMS 轮换
- 设计见 `docs/adr/0011-audit-external-anchoring.md`

**41.7: SEC-008 安全治理自动化**:
- 每发布提交 threat-model delta（新增入口/资产/信任边界、关闭/新增风险、控制与验证）
- `scripts/threat_model_gate.py` 校验缺失 delta/未命名 owner/placeholder/未来日期
- 季度桌面演练（报告接收/依赖漏洞/密钥泄露/跨租户告警）记录于 `supplychain/security-drills.json`
- `--check-today` 校验最近演练未过期（90 天）
- 14 个测试 + ADR [`docs/adr/0012-security-governance.md`](adr/0012-security-governance.md)

### 3. Phase 28-30 核心内容（已集成）

**Phase 28 - 安全深化** ✅:
- ✅ 28.1 威胁模型：`docs/SECURITY_MODEL.md`（STRIDE 分析 + 控制矩阵）
- ✅ 28.2 凭据轮换：41.1 统一凭据生命周期 + API key 双活轮换
- ✅ 28.3 审计防篡改：41.3 哈希链 + WORM 外部锚定
- ✅ 28.4 应用层加固：OIDC 完整验证 + DSR maker-checker + CSRF 防护
- ✅ 28.5 供应链：41.2 五道 gate（secret/license/漏洞/CI pin/manifest）

**Phase 29 - 可靠性与过载工程** ✅:
- ✅ 29.1 优雅关闭：SIGTERM 后停止接受新请求 → 等待 in-flight turn → SSE 重连提示
- ✅ 29.2 背压与过载保护：队列深度阈值、429 + `Retry-After`、租户并发限制
- ✅ 29.3 降级矩阵：`docs/DEGRADATION.md`（每依赖不可用时的自动行为）
- ✅ 29.4 混沌测试：`tests/test_chaos.py`（随机故障注入 + 不变量保持）

**Phase 30 - 可运维性与文档体系** ✅:
- ✅ 30.1 SLO 与告警：`docs/SLO.md`（可用性/延迟/队列/SSE 四指标 + 错误预算）
- ✅ 30.2 Runbook 与诊断：`docs/runbooks/`（按症状组织）+ `GET /api/admin/diagnostics`
- ✅ 30.3 容量与压测：`docs/CAPACITY.md`（单实例 turn/s、连接数、存储增速）
- ✅ 30.4 灾备与合规：RTO/RPO 声明、跨区备份流程、故障演练脚本化
- ✅ 30.5 用户文档：`docs/guides/operator-manual.md` + `tenant-admin-manual.md`
- ✅ 30.6 发布工程：`docs/RELEASE_CHECKLIST.md`（迁移演练门禁 + SemVer 纪律）

## 测试覆盖

| 测试类别 | 测试数 | 结果 |
|---------|-------|------|
| Phase 40 (M0 停止线) | 71 | ✅ passed |
| Phase 41.1-41.3 (SEC-004/003/005) | 50+ | ✅ passed |
| Redis 故障演练（四类场景） | 4 | ✅ ALL PASS |
| 后端总测试 | 757+ + 65 subtests | ✅ passed |
| 分支覆盖率 | 86% | ✅ ≥85% 通过 |
| Golden Set 门禁 | 27/27 | ✅ passed |
| OpenAPI 快照门禁 | - | ✅ passed |
| 前端测试 | 351 | ✅ passed |
| 供应链 gate（5 道） | 5 | ✅ passed |

**总计**: 757+ 个后端测试 + 351 个前端测试 + 全部门禁 + 五道供应链 gate 全绿

## 成熟度评分变化

| 维度 | 1.2.0 | 1.3.0 | 提升 | 1.3.0 目标 |
|------|:----:|:----:|:---:|:---------:|
| 核心功能 | 4.0 | 4.0 | - | 4.0 ✅ |
| 智能质量 | 3.8 | 3.8 | - | 3.5+ ✅ |
| 集成能力 | 4.0 | 4.0 | - | 3.5+ ✅ |
| **安全** | **3.5** | **4.5** | **+1.0** ✨ | **4.0+** ✅ |
| **可靠性** | **3.8** | **4.2** | **+0.4** ✨ | **4.0+** ✅ |
| 可观测/运维 | 3.5 | 4.0 | +0.5 | 3.5+ ✅ |
| 前端工程 | 3.0 | 3.0 | - | 3.0+ ✅ |
| **交付工程** | **4.0** | **4.5** | **+0.5** ✨ | **4.0+** ✅ |
| **总评** | **3.7** | **4.2** | **+0.5** | **4.0+** ✅ |

**关键提升**:
- ✨ 安全: 3.5 → 4.5（M0 风险闭环 + 凭据生命周期 + 供应链 gate + 审计锚定）
- ✨ 可靠性: 3.8 → 4.2（队列 fail-closed + 故障演练 + 降级矩阵）
- ✨ 交付工程: 4.0 → 4.5（五道供应链 gate + 发布 manifest + 迁移演练）
- ✨ 总评达到 4.2，**超越 4.0+ 目标**

## 完成报告

- `IMPLEMENTATION_REPORT_PHASE_40.md`（M0 停止线：SEC-001/002, REL-001, SEC-007，69 行）
- `IMPLEMENTATION_REPORT_PHASE_41.md`（Phase 41.1-41.4：凭据/供应链/审计锚定/数据保护，173 行）
- `IMPLEMENTATION_REPORT_PHASE_41_5.md`（Phase 41.5，98 行）
- `IMPLEMENTATION_REPORT_PHASE_41_6.md`（Phase 41.6，83 行）
- `IMPLEMENTATION_REPORT_PHASE_41_7.md`（Phase 41.7：安全治理自动化，76 行）

## 文档更新

- `CHANGELOG.md` - 新增 1.3.0 版本章节
- `README.md` - 更新版本号至 v1.3.0
- `docs/SECURITY_MODEL.md` - M0 + Phase 41 完整威胁模型与控制矩阵
- `docs/SLO.md` - SLO 定义、错误预算、告警规则
- `docs/RELEASE_CHECKLIST.md` - 发布检查清单
- `docs/CAPACITY.md` - 容量模型与压测基线
- `docs/DEGRADATION.md` - 依赖降级矩阵
- `docs/OPERATIONS.md` - 运维手册更新
- `docs/adr/` - 新增 ADR-008/009/010/011/012（M0 + 供应链 + 审计锚定 + 安全治理）
- `docs/runbooks/` - 按症状组织的故障处置手册
- `docs/guides/` - 坐席与租户管理员用户手册

## 破坏性变更

**无破坏性变更**。本版本完全向后兼容 1.2.0。

- 所有新功能为增量添加或可选启用
- `deployment_profile=multi` 强制 fail-closed 仅影响多实例部署
- DSR 未配置 `DSR_EXPORT_SECRET` 时返回 501（之前为 500）
- OIDC 完整验证强化了安全边界，但不影响 API key 认证路径

## 升级指南

从 1.2.0 升级到 1.3.0 **需要运行迁移**（migration 28-31），但 API 行为向后兼容。

**必须操作**:
1. 运行数据库迁移（28: auth_transactions, 29: DSR, 30: credential_registry, 31: audit_anchors）
2. 多实例部署必须配置 `deployment_profile=multi`（强制 fail-closed）
3. 配置 `DSR_EXPORT_SECRET`（32 字节以上，用于 Fernet 加密）

**建议操作**:
1. 使用新的凭据管理 API（`POST /api/admin/keys`）实现 API key 轮换
2. 配置 WORM 存储用于审计外部锚定（`app/worm_store.py`）
3. 定期运行 `scripts/verify_audit_chain.py` 校验审计链完整性
4. 配置 KMS Ed25519 密钥用于审计链头签名
5. 审查 `supplychain/` 目录下的策略配置（license/漏洞例外）

## 下一步路线图

### Version 1.4.0 - Secure Operations 续（预计 4-6 周）

**Phase 41 剩余项**:
- 41.4 数据保护与隐私运营（已部分完成）
- 41.5 AI 安全评估 gate
- 41.6 PostgreSQL Row-Level Security（多租户数据隔离）

### Version 2.0 及更远规划

详见 [`ROADMAP_2_X.md`](ROADMAP_2_X.md)（1.5 可靠扩展、2.0 企业控制面）。

## 致谢

感谢所有参与 1.3.0 开发的贡献者。本版本完成了 M0 停止线四项关键风险闭环、Phase 41 凭据与供应链治理、Phase 28-30 核心内容集成，共计 **757+ 后端测试、351 前端测试、5 道供应链 gate、499 行实施报告**。

---

**Helix Support 1.3.0** - 通过外部安全评审不需临时补救的商用成熟平台 🚀
