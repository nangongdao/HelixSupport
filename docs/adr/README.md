# Architecture Decision Records (ADR)

每个 ADR 记录一个已接受的架构决策：背景、决策、后果、相关 ADR。**新增架构决策必须先写 ADR**（Phase 27 起强制），模板见 `0000-template.md`。

| ADR | 标题 | 状态 |
|-----|------|------|
| [0001](0001-dual-backend-single-query.md) | 单查询实现双后端（SQLite/PostgreSQL） | 已接受（1.0.0） |
| [0002](0002-sse-seq-streaming.md) | SSE + 单调 seq 流式响应 | 已接受（1.0.0） |
| [0003](0003-queue-abstraction.md) | 持久任务队列抽象（TaskQueue） | 已接受（1.0.0） |
| [0004](0004-bff-stateless-session.md) | BFF 无状态会话（OIDC） | 已接受（1.0.0） |
| [0005](0005-connector-contracts.md) | 连接器契约与韧性封装 | 已接受（Phase 20） |
| [0006](0006-backend-structure.md) | 后端结构治理（mixin/router 拆分） | 已接受（Phase 27，1.2.0） |
| [0007](0007-signed-inbound-channels.md) | 签名入站消息渠道与持久幂等 | 已接受（Phase 38） |
| [0008](0008-m0-security-hardening.md) | M0 安全加固（OIDC 事务/JWKS、DSR 隐私工作流、队列 fail-closed） | 已接受（Phase 40） |
| [0009](0009-credential-lifecycle.md) | 统一凭据生命周期注册表（SEC-004） | 已接受（Phase 41） |
| [0010](0010-supply-chain.md) | 供应链与可复现发布 gate（SEC-003） | 已接受（Phase 41.2） |
| [0011](0011-audit-external-anchoring.md) | 审计证据外部锚定（KMS 签名 + WORM 锚点 + 同事务高危审计） | 已接受（Phase 41.3） |
| [0012](0012-dsr-maker-checker.md) | DSR maker-checker 状态机与加密导出 | 已接受（Phase 41.4） |
| [0013](0013-data-protection.md) | 数据保护与隐私运营（字段 registry / 统一 redaction / SLA 看板 / 删除证明 / tombstone） | 已接受（Phase 41.4） |
| [0014](0014-ai-safety-eval-gate.md) | AI 安全评测 Gate v1（对抗集 / 不可变报告 / 工具再授权 / 晋级阈值自动阻断） | 已接受（Phase 41.5） |
| [0015](0015-postgres-row-level-security.md) | PostgreSQL 行级租户隔离（RLS 纵深防御） | 已接受（Phase 43.2 contract a） |
| [0016](0016-ai-governance-v2.md) | AI Governance v2（provider 治理 / 工具能力令牌 / drift 自动停 canary） | 已接受（Phase 43.5） |
| [0017](0017-frontend-maintainability-performance.md) | 前端可维护性与性能预算（inspector 状态机 / 双层 performance gate / 稳定面视觉回归） | 已接受（Phase 43.6） |
| [0018](0018-break-zero-build-vite-react.md) | 打破零构建原则引入 Vite + React 构建链（桌面化 React 岛迁移） | 已接受（v1.4.0-desktop） |
