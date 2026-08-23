# ADR-005: 连接器契约与韧性封装

- 状态：已接受（自 1.0.0 / Phase 20 生效）
- 日期：2026-08-14（补记）

## 背景

订单/知识/CRM 外部系统必须可替换、可故障注入，且外部故障不能破坏会话正确性。直接内联 HTTP 调用会让每个集成重复错误处理。

## 决策

- `app/connectors.py` 定义 `OrderConnector`/`KnowledgeConnector`/`CRMConnector` Protocol + 沙盒实现（数据来自应用自身存储，身份绑定、跨客户不泄露）。
- `app/connectors_runtime.py` 的韧性封装（熔断 + 重试 + 降级）包裹任意实现：瞬态错误重试、熔断打开返回 `unavailable`（Order/CRM 升级人工、Knowledge 回退内置 FTS）。
- `app/connectors_http.py` 提供 HMAC 签名参考实现；契约套件（`*ConformanceMixin`）对沙盒与 HTTP 实现同测。
- `ToolGateway` 作为最小权限入口注入编排器。

## 后果

- 优点：第三方凭契约接入；故障注入测试保证降级语义。
- 代价：协议字段是硬契约，扩展需改 Protocol + 全实现 + 契约测试。

## 相关

[ADR-001](0001-dual-backend-single-query.md)
