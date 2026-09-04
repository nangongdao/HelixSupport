# Helix Support 1.1.0 发布总结

**发布日期**: 2026-09-03  
**版本**: 1.1.0  
**里程碑**: 智能质量与集成成熟

## 概述

Helix Support 1.1.0 完成了从"功能完整的单体产品"到"可被第三方集成的商用级智能客服平台"的关键升级。本版本实现了 Phase 19（智能质量与集成成熟）、Phase 20（连接器健壮性与真实接入）、Phase 21（Supervisor 质量看板与知识运营）以及 Phase 25（API 治理与开发者体验）的全部内容。

**成熟度跨越**: 总评 3.0 → 3.6；智能质量维度 2.5 → 3.8；集成能力维度 2.0 → 4.0（达到商用级）；可靠性维度 3.5 → 3.8。

## 核心特性

### 1. 智能质量管理 (Phase 19)

**提示词/模型版本注册表**:
- 任何模型、提示词变更都可登记、可对照、可回滚
- 支持 draft/active/canary/retired 状态流转
- 全部操作记录审计事件

**Canary 对照部署**:
- 流量稳定分桶（基于 SHA-256 哈希）
- 按 prompt_version 维度聚合指标
- 同一会话 ID 始终路由到相同版本

**Golden Set 扩展**:
- 从 6 例扩展至 **27 例**
- 覆盖：知识检索（11 例）、订单工具（6 例）、敏感升级（5 例）、提示注入防护（3 例）、多轮上下文（3 例）、边界场景（2 例）
- 100% 通过率作为门禁标准

**租户模型策略与预算**:
- 允许模型列表白名单
- 每日 turn 预算上限
- 超限自动降级为确定性路径

### 2. 连接器健壮性 (Phase 20)

**运行时防护**:
- 熔断器状态机（closed → open → half_open → closed）
- 指数退避重试（仅针对 TransientConnectorError）
- 租户隔离的熔断状态
- 降级语义（熔断打开时返回 unavailable 而非抛异常）

**HTTP 连接器参考实现**:
- 通用 REST 连接器模板
- HMAC-SHA256 签名（METHOD\nPATH\nCANONICAL_QUERY\nTIMESTAMP\nBODY）
- 超时控制与错误映射
- 可注入传输层便于测试

**出站 Webhook**:
- 6 种事件类型（conversation.created/escalated/resolved/sla_breached/sla_impending、report.generated）
- At-least-once 投递语义 + 幂等 ID 去重
- HMAC-SHA256 签名、指数退避重试、死信队列
- SSRF 防护（拒绝内网地址）

**契约测试套件对外化**:
- 参数化 Conformance Mixin（OrderConnectorConformanceMixin / KnowledgeConnectorConformanceMixin / CRMConnectorConformanceMixin）
- 任何实现继承 Mixin 即可验证合规性
- 已验证：Sandbox 连接器、HTTP 连接器、Resilient 包装器

### 3. 质量看板与知识运营 (Phase 21)

**质量统计聚合**:
- 按天×租户×意图×prompt_version 聚合
- 指标：turn 数、首次响应时长、升级率、负反馈率、平均延迟、估算 token 成本
- Keyset 游标分页查询（since/until 日期过滤、intent/prompt_version 筛选）

**Supervisor 前端视图**:
- 趋势图表（SVG 原生绘制，零第三方图表库依赖）
- 意图×版本矩阵热图
- 知识缺口列表（负反馈 + 无引用回答聚类）
- 双模式渲染：检查器面板内嵌模式与独立质量视图整页模式

**知识生命周期**:
- 四状态流转：draft → pending_review → published → retired
- 强制审批流程（API 层不可绕过）
- 检索只命中 published 状态
- 负反馈回流：从负评消息一键生成 draft 知识条目

### 4. API 治理与开发者体验 (Phase 25)

**RFC 9457 统一错误契约**:
- 标准化字段：type/title/status/detail/instance + request_id/code
- Content-Type: `application/problem+json`
- 错误类型体系：validation/authentication/permission/not-found/conflict/rate-limit/service-unavailable
- 错误目录文档（`docs/ERRORS.md`）

**OpenAPI 治理**:
- 快照门禁（`scripts/openapi_snapshot.py`）
- 破坏性变更检测：删除端点、删除字段、类型变更、删除/必填参数
- 全部 43+ 端点补充 summary/description，按 tag 分组

**API 版本与弃用策略**:
- 书面策略（`docs/API_POLICY.md`）
- 原则 1: 响应体只增不改
- 原则 2: 弃用需 `Deprecation`/`Sunset` 头 + 至少一个次版本过渡
- 原则 3: 语义化版本（semver.org）
- 原则 4: CHANGELOG 记录每次 API 变更

**Python 客户端 SDK**:
- 完整 API 封装（612 行）
- 类型化错误层次（HelixAuthenticationError/HelixPermissionError/HelixNotFoundError 等）
- 自动重试（指数退避，最多 3 次）
- 幂等键支持（`idempotency_key` 参数）
- SSE 流式解析（`stream_turn_job()` 迭代器）
- Webhook 验签辅助函数（`verify_webhook_signature()`）
- 28 个测试全部通过

**API 参考文档站**:
- 集成指南（`docs/api/guide.md`，166 行）
- 完整端点参考（`docs/api/reference.md`，6270 行）
- 从 OpenAPI 自动生成，与代码同步

## 测试覆盖

| 测试类别 | 测试数 | 结果 |
|---------|-------|------|
| Phase 19 智能质量 | 31 | ✅ passed |
| Phase 20 连接器健壮性 | 94 | ✅ passed |
| Phase 21 质量看板与知识 | 33 | ✅ passed |
| Phase 25 Python SDK | 28 | ✅ passed |
| Golden Set 门禁 | 1 (27 例) | ✅ passed |
| OpenAPI 快照门禁 | - | ✅ passed |
| 后端总覆盖率 | 88% | ✅ 13106 stmts, 1238 miss |
| 前端门禁 | 139 | ✅ passed |

**总计**: 723 个后端测试全部通过 + 139 个前端测试全部通过 + 全部门禁保持绿色

## 成熟度评分变化

| 维度 | 1.0.0 基线 | 1.1.0 | 提升 | 1.1.0 目标 |
|------|:--------:|:----:|:---:|:---------:|
| 核心功能 | 4.0 | 4.0 | - | 4.0 |
| **智能质量** | **2.5** | **3.8** | **+1.3** ✨ | 3.5+ |
| **集成能力** | **2.0** | **4.0** | **+2.0** ✨ | 3.5+ |
| 安全 | 3.5 | 3.5 | - | 3.5+ |
| **可靠性** | 3.5 | **3.8** | **+0.3** ✨ | 4.0+ |
| 可观测/运维 | 3.0 | 3.5 | +0.5 | 3.5+ |
| 前端工程 | 2.0 | 2.0 | - | 3.0+ |
| **交付工程** | 3.5 | **4.0** | **+0.5** ✨ | 4.0+ |
| **总评** | **3.0** | **3.6** | **+0.6** | **3.5** |

**关键提升**:
- ✨ 智能质量: 2.5 → 3.8（**超出目标 3.5+**）
- ✨ 集成能力: 2.0 → 4.0（**达到商用级成熟度**）
- ✨ 可靠性: 3.5 → 3.8（接近 1.2.0 目标）
- ✨ 交付工程: 3.5 → 4.0（**达到目标**）

## 完成报告

- `docs/PHASE_19_COMPLETION.md` - 智能质量与集成成熟（提示词/模型注册表、Canary 对照、Golden Set 27 例、租户模型策略）
- `docs/PHASE_20_COMPLETION.md` - 连接器健壮性与真实接入（熔断/重试/降级、HTTP 连接器、契约测试、出站 Webhook）
- `docs/PHASE_21_COMPLETION.md` - Supervisor 质量看板与知识运营（质量聚合、前端视图、知识生命周期）
- `docs/PHASE_25_COMPLETION.md` - API 治理与开发者体验（RFC 9457、OpenAPI 治理、Python SDK、API 文档站）

## 文档更新

- `CHANGELOG.md` - 新增 1.1.0 版本章节，记录全部 Phase 19-21-25 变更
- `README.md` - 更新版本号至 v1.1.0
- `docs/api/guide.md` - API 集成指南（认证、幂等、分页、流式、webhook 验签）
- `docs/api/reference.md` - 完整 API 参考（6270 行，从 OpenAPI 生成）
- `docs/API_POLICY.md` - API 版本与弃用策略
- `docs/ERRORS.md` - 错误目录（RFC 9457 错误类型与处置建议）

## 破坏性变更

**无破坏性变更**。本版本完全向后兼容 1.0.0。

- RFC 9457 错误响应保留旧 `detail` 字段（向后兼容）
- OpenAPI 快照门禁确保无破坏性变更
- 所有新功能为增量添加，现有 API 行为不变

## 升级指南

从 1.0.0 升级到 1.1.0 **无需任何操作**，现有客户端代码无需修改。

**可选优化**:
1. 升级错误处理代码以使用 RFC 9457 字段（`type`/`title`/`request_id`/`code`）
2. 使用 Python SDK 替代手写 HTTP 客户端（`pip install helix-client`）
3. 配置租户模型策略与预算（`PUT /api/admin/tenants/{tenant_id}/model-policy`）
4. 启用提示词版本注册表与 Canary 对照部署（`POST /api/prompts`）
5. 注册 Webhook 端点订阅事件（`POST /api/webhooks`）

## 下一步路线图

### Version 1.2.0 - 平台化：租户运营、渠道、前端工程（预计 6-8 周）

**Phase 22 - 租户自助开通与成员生命周期**:
- 22.1 租户开通 API（自动初始化策略/标签/配额）
- 22.2 成员管理（邀请、角色变更、停用）
- 22.3 细粒度权限（permission 映射表、auditor/supervisor 角色）
- 22.4 配额与计量（会话数/turn 数/存储、账单导出）

**Phase 23 - Web Chat 渠道与渠道幂等**:
- 23.1 可嵌入 Web Chat（签名 token、SSE 流式、脚本/独立页两种形态）
- 23.2 渠道抽象（渠道级幂等键、路由规则）

**Phase 26-27 - 前端工程化**:
- 26.1 模块边界与单元测试
- 26.2 i18n 框架（中英分离）
- 26.3 设计令牌与组件文档

### Version 1.3.0 - 商用级可信：安全、可靠性、运维、文档（预计 6-8 周）

**Phase 28-30 - 安全与可靠性深化**:
- 28.1 威胁模型与响应流程（SECURITY.md）
- 28.2 密钥轮换机制（API key、会话密钥）
- 28.3 审计防篡改（哈希链验证）
- 29.1 优雅关闭验证（in-flight turn/SSE）
- 29.2 队列背压与过载保护
- 29.3 依赖降级矩阵与混沌测试
- 30.1 SLO 定义与错误预算
- 30.2 告警规则与分症状 runbook
- 30.3 支持诊断包与健康检查分级

## 致谢

感谢所有参与 1.1.0 开发的贡献者。本版本共包含 4 个 Phase、186 个测试用例、4 份完成报告以及 6000+ 行 API 文档。

---

**Helix Support 1.1.0** - 可被第三方集成的商用级智能客服平台 🎉
