# 租户管理员手册

面向租户管理员(admin 角色):配置、成员、配额、知识、webhook 与监控。

## 成员管理(Phase 22)

- `POST /api/admin/tenants` 开通租户(幂等,自动种子默认知识 + 配额)。
- 成员:邀请(`POST .../members`)、角色变更(`PATCH .../members/{actor}`)、停用(`POST .../members/{actor}/deactivate`)。
- 角色:admin(全权)、supervisor(主管)、operator(坐席)、viewer(只读)、auditor(只读审计)、channel(渠道)。

## 配额与用量(Phase 22.4)

- `GET/PUT /api/admin/tenants/{id}/quota` 读写会话配额/存储配额。
- `GET /api/admin/usage` 导出每日用量(会话/turn/消息)对账。
- 超会话配额:新建会话返回 429 + Retry-After;解决会话释放名额。

## 知识库(Phase 21.3)

- 创建条目进 `draft` 状态(检索不可见);审批 `POST /api/knowledge/{id}/review` 发布/退休。
- 负反馈消息可一键生成 draft(`knowledge-draft` 端点),供编辑后发布。
- 检索只命中 `published`;草稿需审批后上线。

## Webhook(Phase 20.5)

- `POST /api/webhooks` 注册端点(事件:conversation.created/escalated/resolved/sla_breached)。
- 投递 HMAC 签名(`X-Helix-Signature` over `timestamp.body`),至少一次 + `event_id` 去重。
- `GET /api/webhooks/deliveries` 查看投递状态;删除端点会死信其挂起投递。

## 安全(Phase 28)

- API key 吊销:`GET /api/me` 看 `credential_id`,`POST /api/admin/keys/{id}/revoke` 立即失效。
- 审计链:`scripts/verify_audit_chain.py` 校验;`GET /api/admin/diagnostics` 生成支持诊断包。

## 监控

- `GET /api/system/metrics`(`metrics:read`):队列/worker/数据库池/延迟。
- `GET /api/supervisor/quality`:质量桶(升级率/负反馈率/首响)。
- `GET /api/supervisor/knowledge-gaps`:负反馈无引用会话,知识回流起点。

## 发布与升级

- 版本见 `GET /api/me` 或 `APP_VERSION`;升级按 `docs/RELEASE_CHECKLIST.md` 走检查清单。
- 备份/恢复/灾备见 `docs/DISASTER_RECOVERY.md`。
