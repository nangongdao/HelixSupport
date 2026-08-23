# ADR-004: BFF 无状态会话（OIDC / BFF）

- 状态：已接受（自 1.0.0 生效）
- 日期：2026-08-14（补记）

## 背景

客服工作台需要 OIDC 登录，但服务端会话表会引入状态同步、过期清理、多实例粘性等问题。API 本身用 API Key 认证，浏览器端需要独立会话。

## 决策

浏览器 → BFF（本服务）用签名的无状态 Cookie：`base64url(payload).signature`，载荷含 `session_id/tenant_id/actor_id/role/expires_at/iat`。`POST /auth/refresh` 用滑动续期重签 Cookie（受 `SESSION_MAX_LIFETIME_MINUTES` 绝对上限约束）。BFF 不存会话状态，重启不影响会话。API 端点仍走 `X-API-Key`；会话 Cookie 只服务 `/auth/*` 与工作台页面。

## 后果

- 优点：无服务端状态、多实例天然、轮换密钥重签即可吊销全部。
- 代价：Cookie 载荷可见（不可含敏感数据）；吊销即时性受 Cookie 过期限制；密钥泄露需全局重签。

## 相关

[ADR-005](0005-connector-contracts.md)
