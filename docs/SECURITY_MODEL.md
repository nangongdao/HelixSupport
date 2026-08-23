# Security Model

版本:1.3.0 安全基线(Phase 28 已完成)+ M0 停止线(Phase 40 已完成)+ 1.4 Security Operations 进行中(Phase 41.1–41.3、41.7 已完成;41.4/41.5/41.6 进行中)
已完成历史:`ROADMAP_1_X.md` 第 12 节;M0 证据见 [`IMPLEMENTATION_REPORT_PHASE_40.md`](IMPLEMENTATION_REPORT_PHASE_40.md)
后续整改:[`ROADMAP_2_X.md`](../ROADMAP_2_X.md) 风险登记册与 Phase 41-43
安全响应流程见 [`SECURITY.md`](../SECURITY.md)

## 信任边界

```
        ┌──────────┐    TLS    ┌──────────────────┐
客户 ──▶ │ Web Chat │ ───────▶ │                  │
        └──────────┘          │                  │
        ┌──────────┐    TLS    │   Helix Support  │──▶ SQLite/PostgreSQL
坐席 ──▶ │ 工作台    │ ───────▶ │   (API + BFF)    │──▶ Redis(可选队列)
        └──────────┘          │                  │──▶ 连接器(Order/CRM/Knowledge)
        ┌──────────┐          │                  │──▶ 模型供应商(可选)
管理员 ─▶ │ 管理 API  │ ───────▶ │                  │──▶ Webhook 消费者
        └──────────┘          └──────────────────┘
```

信任主体:客户(匿名/签名 token)、坐席(API key)、管理员(API key,tenant:manage)、
系统(编排器/worker)、连接器(外部 HTTP)、模型供应商、数据库、Webhook 消费者。

## STRIDE 分析

| 威胁类别 | 资产/入口 | 现有控制 | 阶段整改项 |
|---------|----------|---------|-----------|
| **Spoofing** | API key 认证 | `hmac.compare_digest` 常量时间比对、租户绑定、`X-Tenant-Id` 校验 | 28.2 key 吊销 + 会话密钥轮换 |
| **Spoofing** | Web Chat 客户 token | HMAC 签名 + 过期 + 时钟偏差;session fresh token 绑定 conversation_id | — |
| **Tampering** | 审计事件 | — | **28.3 哈希链 + 校验脚本** |
| **Tampering** | 会话 cookie | HMAC 签名、SameSite=lax | 28.2 kid 多密钥;28.4 Origin 校验 |
| **Repudiation** | 操作审计 | 审计事件全量记录(actor/request_id) | 28.3 链头可校验 |
| **Information disclosure** | 跨租户/客户数据 | 租户绑定、widget 会话绑定、内部备注服务端过滤、`_ensure_same_tenant`、order not_found 统一 | — |
| **Clickjacking** | 操作台 / Web Chat | 操作台 `DENY`/`frame-ancestors 'none'`;仅 `/widget` 使用精确 origin allowlist | — |
| **Information disclosure** | 错误体 | RFC 9457 Problem Details(Phase 25.1) | — |
| **Denial of service** | API 滥用 | 全局限流(每 key)+ 会话配额(429) | 28.4 auth 独立限流 |
| **Elevation of privilege** | RBAC | `require_permission` 单一入口 + 权限矩阵测试(Phase 22.3) | — |

## 控制矩阵(按信任主体)

| 控制 | 客户 | 坐席 | 管理员 | 连接器 |
|------|------|------|--------|--------|
| 认证 | 签名 token | API key | API key + tenant:manage | HMAC(出站验签) |
| 授权 | conversation:write(渠道) | operator:act | admin:manage | 契约只读 |
| 租户隔离 | token 绑定 | key 绑定 | key 绑定 + 跨租户守卫 | 请求带 tenant_id |
| 审计 | 消息/反馈 | 操作 | 管理操作 | 调用留痕 |

## 关键风险与缓解

1. **API key 泄露**:28.2 吊销端点立即失效 + 持久化;建议 90 天轮换。
2. **会话密钥泄露**:28.2 滚动轮换(多密钥验证 + 单密钥签发);轮换 runbook 见 `docs/OPERATIONS.md`。
3. **审计被篡改**:28.3 哈希链,`scripts/verify_audit_chain.py` 校验;导出归档带链头。
4. **CSRF**:BFF 变更端点(Phase 28.4)Origin 严格校验;cookie SameSite=lax;`allow_credentials=False`。
5. **供应链**:pip-audit 阻断门禁(CI);28.5 SBOM 随发布产出;41.2 供应链 gate 闭环 —— secret 扫描、license 策略、漏洞例外到期红灯、workflow pin 一致性、发布 manifest 内容校验,全部进入 CI(见 [`docs/adr/0010-supply-chain.md`](adr/0010-supply-chain.md))。
6. **凭据生命周期**:41.1 credential registry 统一管理 API/渠道/widget/session 密钥 —— pending→active→retiring→revoked 状态机、有界重叠窗口、staged activation、跨实例即时吊销;41.1b 轮换 API 与 key_id 版本化签名与审计脱敏(见 [`docs/adr/0009-credential-lifecycle.md`](adr/0009-credential-lifecycle.md))。

## M0 已闭环项(Phase 40)

- [x] **SEC-001** OIDC state/nonce/PKCE/JWKS/claims 完整验证与本地成员授权映射 —— Phase 40.1;36 个 OIDC 测试覆盖
      全部拒绝路径(issuer/audience/过期/重放),无租户/角色回退;证据见
      [`IMPLEMENTATION_REPORT_PHASE_40.md`](IMPLEMENTATION_REPORT_PHASE_40.md)。
- [x] **SEC-002** 数据主体导出/删除专用权限(`privacy:request` / `privacy:approve` / `privacy:execute`)、
      maker-checker 审批与幂等执行 —— Phase 40.2;创建者不能审批自己的请求,审批与执行分离,
      全链路写入审计链。
- [x] **REL-001** 多实例队列 fail-closed —— Phase 40.3;Redis 不可用时不再静默回退本地 SQLite,
      API 返回 503 + `Retry-After: 30`(`urn:helix:error:queue_unavailable`),worker 拒绝启动并按退避重试。
- [x] **SEC-007** 安全报告闭环 —— Phase 40.4;占位符邮箱与部署前配置步骤见 `SECURITY.md`,
      部署负责人完成真实渠道演练前视为未通过闭环。

## Phase 41 已闭环项(1.4 开发中)

- [x] **41.1 SEC-004 统一凭据生命周期** —— `app/credentials.py` + migration 30 `credential_registry`;
      每把密钥只存指纹(`key_ref`),`is_allowed` 强制 not_before/±5s 时钟偏差/expires_at/retiring 重叠窗口;
      证据:`tests/test_credentials.py`(11 tests)+ `tests/test_phase41.py` 管理 API 一次发放/跨实例吊销/审计脱敏/并发 revoke race 收敛。
- [x] **41.1b 轮换 API / key_id 版本化 / 审计脱敏** —— `POST/GET /api/admin/keys`、`{id}/revoke`,secret 只显示一次;
      channel webhook 签名可选 `X-Helix-Key-Id` 选择已轮换凭据,未知/错类型/跨租户/已吊销统一 401 fail-closed;
      issuance/revoke 审计事件只含 credential_id,永不出现原始 secret。
- [x] **41.2 SEC-003 供应链与可复现发布 gate** —— `scripts/scan_secrets.py`(已知凭证形态扫描,忽略 build/测试夹具/npm integrity)、
      `scripts/license_gate.py`(逐包登记制 + LicenseRef-TBD 临时批准到期红灯)、`scripts/vuln_review.py`(例外 schema + 到期红灯 + pip-audit 覆盖)、
      `scripts/check_workflows.py`(actions 引用 register 一致,SHA 固定留给受控更新机器人)、`scripts/release_manifest.py`(从源码树重建并核对 digest);
      全部接入 CI `supply-chain` job。设计见 [`docs/adr/0010-supply-chain.md`](adr/0010-supply-chain.md)。
- [x] **41.3 SEC-005 审计证据外部锚定** —— 高危安全/权限/DSR/策略变更与审计证据同事务持久化
      (`app/db/audit.py::audit_high_risk` + migration 31 `audit_anchors`),失败整体回滚并 fail-closed
      (503 `code="audit_unavailable"`、`Retry-After: 30`);链头 `{last_seq,last_hash,timestamp,environment}`
      经 Ed25519 KMS 签名导出 WORM 锚点(`app/worm_store.py::DiskWormStore`),kid 白名单轮换语义;
      `scripts/verify_audit_chain.py` 一次校验本地全链 + DB frontier anchors + WORM claims 三证据。
      证据:ADR [`docs/adr/0011-audit-external-anchoring.md`](adr/0011-audit-external-anchoring.md) +
      `tests/test_audit_anchors.py`(8 例,覆盖重算全链/删 anchor/替换 manifest/错序/重复 seq/KMS 轮换/WORM 不可用)。
- [x] **41.7 SEC-008 安全治理自动化** —— 每发布提交 threat-model delta(新增入口/资产/信任边界、
      关闭/新增风险、控制与验证证据,named owner/审批人),`scripts/threat_model_gate.py` 校验缺失 delta、
      未命名/placeholder owner、未来日期、空 control/evidence;季度桌面演练(报告接收/依赖漏洞/密钥泄露/
      跨租户告警)记录于 `supplychain/security-drills.json`,`--check-today` 校验最近演练未过期(90 天);
      CI `supply-chain` job 接入 nil-tolerant gate。证据:`tests/test_threat_model_gate.py`(14 例)+
      ADR [`docs/adr/0012-security-governance.md`](adr/0012-security-governance.md)。

## 未闭环项(整改中)

- [ ] 28.5 基础镜像 digest 回填与镜像签名(cosign):`supplychain/base-image-pin.json` 的 `digest` 待真实 registry 受控回填;
      provenance/attestation 与 staging admission(拒绝未签名镜像)进入 Gate B 退出标准。
- [ ] 41.4 数据保护与隐私运营、41.5 AI 安全评测 AI-001、41.6 深模块拆分 ARC-001 —— 见 `ROADMAP_2_X.md` §6。
- [ ] Gate B 概览(41.2/41.3/41.7 已完成台账与门禁形态):镜像 digest 回填 + cosign 签名与 staging admission;
      API/channel/session key 轮换演练;审计 anchor 真实备份恢复演练;AI 安全集 100% 与可追溯 promotion report;
      真实私有报告渠道端到端演练(SEC-007 闭环);安全补丁发布演练;M0+1.4 runbook 非作者执行——均依赖
      受控部署环境/私密运行记录,不在公开源码仓断言。

上述项目的严重度、临时控制、负责人、依赖和验收标准以
[`ROADMAP_2_X.md`](../ROADMAP_2_X.md) 为准；未完成前不得把对应能力描述为
生产就绪。
