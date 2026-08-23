# ADR-009: 统一凭据生命周期注册表（SEC-004）

- 状态：已接受（Phase 41，1.4 / Secure Operations）
- 日期：2026-08-19

## 背景

系统现有四类密钥/凭据，各自独立管理、生命周期语义不一致：

- **API key**（`app/security.py` + `revoked_api_keys`）：无过期、无轮换窗口，
  吊销靠 Phase 28.2 静态弃用集合（进程内立即、跨实例靠重启 seed）。
- **正式渠道 secret**（`app/channel_webhooks.py`）：每账户一个静态 HMAC
  secret，签名既无 key_id 也无版本选择，轮换需改配置并重启。
- **widget signing key**（`app/widget_token.py` + `widget_secret`）：单一共享
  secret，无版本/过期，轮换即全量失效。
- **session key**（`app/session_auth.py` `SESSION_SECRETS_JSON`）：已有 kid
  滚动轮换雏形，但未纳入统一生命周期。

ROADMAP 41.1（SEC-004）要求将这些统一为逻辑 credential：`id/type/tenant/
status/not_before/expires_at/last_used_at/version`，秘密值保存在 secret
manager/KMS，数据库只保存标识、哈希或引用；支持 `pending → active →
retiring → revoked/expired`，至少两把 active key 的有界重叠窗口和 staged
activation；保持现有请求头兼容；管理 API 永不返回原 secret，创建时只显示
一次；渠道签名加入 key_id/版本选择，未知 key 与错误签名统一响应。

## 决策

引入**逻辑凭据注册表** `app/credentials.py` + `credential_registry` 表
（migration 30），作为所有凭据生命周期的单一裁决者。

- 每行 credential 记录：`credential_id`（稳定指针）、`type`、`tenant_id`、
  `status`、`key_ref`（秘密值的 SHA-256 指纹，绝不存明文）、`not_before`、
  `expires_at`、`last_used_at`、`version`、创建/撤销审计字段、`rotated_to`。
- 状态机（每个动作必须落在允许迁移上，违者 `InvalidCredentialTransition`）：
  - `pending → active`（staged activation；到 `not_before` 也可自动激活）
  - `active → retiring`（轮换发起，旧 key 在重叠窗口内继续验证）
  - `retiring → revoked`（窗口到期后退出，立即拒绝）
  - 任意非终态 → `expired`（`expires_at` 到达，单项迁移）
- 统一鉴权函数 `is_allowed`：`status == active` 且 `not_before ≤ now <
  expires_at`；`revoked`/`expired`/`retiring` 未通过窗口者一律拒绝。`retiring`
  在窗口内依然允许（有界重叠），保证旧 key 平滑退出。
- 秘密值来源保持现状：配置/文件/secret manager。注册表持有指纹与生命周期，
  不改变现有 `X-API-Key` / `X-Helix-Signature` / `X-Widget-Token` / session
  cookie 的请求头格式。
- 管理 API 只返回元数据；创建时原 secret 只显示一次；审计经
  `sanitize_for_audit` 统一脱敏。
- 时钟偏差：过期与 not_before 判定允许 ±5 秒偏斜（`max_skew_seconds`），
  与 widget token 与 OIDC 的 skew 语义一致。

### 切片边界（第一期纵向切片）

统一注册表 + 生命周期 + 管理 API 先完整接入 **API key**（现状单 key 集合），
提供 `key_id`/版本选择接口；正式渠道与 widget key 的签名格式升级在
41.1b 尾部与 41.2 阶段进行（它们复用注册表的统一吊销/过期语义）。

## 后果

- 优点：跨实例吊销即刻生效（注册表是持久裁决者）；轮换演练有 staged
  activation 与重叠窗口；四类凭据共享同一生命周期与审计语义；管理 API 不
  泄密。
- 代价 / 风险：注册表与配置的"真相源"二义性——需明确定义配置 seed 与
  运行时轮换合并规则（外键为 `key_ref` 指纹，冲突时拒绝启动并提示）；
  旧版未注册 key 与新版 key 的混跑窗口需要负向测试覆盖。
- 迁移路径：migration 30 建表上蓝图；启动时把配置中的 api key 注册为
  active（幂等 upsert）；无注册表的旧库先建表再 seed，行为与现状等价。

## 相关

- [ADR-008](0008-m0-security-hardening.md)：M0 安全加固（OIDC/DSR/队列 fail-closed）
- [ADR-004](0004-bff-stateless-session.md)：BFF 无状态会话（session key）
- [ADR-007](0007-signed-inbound-channels.md)：签名入站消息渠道（channel secret）
- [ADR-003](0003-queue-abstraction.md)：持久任务队列抽象