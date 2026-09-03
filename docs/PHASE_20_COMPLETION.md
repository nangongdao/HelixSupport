# Phase 20 完成报告：连接器健壮性与真实接入

**完成日期**: 2026-09-03  
**基线版本**: 1.4.0  
**目标版本**: 1.1.0 (Phase 19-21)

## 执行摘要

Phase 20 "连接器健壮性与真实接入" 已全部完成，所有子任务均已实现并通过测试门禁。本阶段实现了连接器运行时防护（熔断/重试/降级）、HTTP 连接器参考实现、编排层集成、契约测试套件对外化以及出站 Webhook 投递系统。

## 完成的子任务

### 20.1 连接器运行时防护 ✅

**实现内容**:

**核心模块**: `app/connectors_runtime.py`
- **熔断器状态机** (`CircuitBreaker` 类):
  - 状态: `closed` → `open` (连续 N 次失败) → `half_open` (探测恢复) → `closed` (恢复成功)
  - 配置参数:
    - `failure_threshold`: 触发熔断的连续失败次数（默认 5）
    - `recovery_timeout_seconds`: 从 open 转入 half_open 的超时时间（默认 30s）
    - `half_open_max_calls`: 半开状态下允许的探测调用数（默认 1）
  - 隔离粒度: 按 `(tenant_id, connector_name)` 隔离状态，单租户故障不影响其他租户
  - 线程安全: 使用 `threading.Lock` 保护状态变更

- **重试逻辑**:
  - 仅针对 `TransientConnectorError`（超时、网络故障）重试
  - 指数退避: `base_backoff * (2 ** attempt)` + 抖动（jitter）
  - 最大重试次数: 默认 3 次
  - 所有连接器方法都是只读（幂等），安全重试

- **降级行为**:
  - 熔断打开时返回 `unavailable` 结果，不抛异常
  - `OrderConnector.lookup_order()` → `OrderLookup(ok=False, code="unavailable")`
  - `CRMConnector.resolve_customer()` → `CustomerLookup(ok=False, code="unavailable")`
  - `KnowledgeConnector.search()` → 空列表 `[]`（编排层回退到内置 FTS）

**包装器**:
- `ResilientOrderConnector`: 包装任意 `OrderConnector`
- `ResilientKnowledgeConnector`: 包装任意 `KnowledgeConnector`
- `ResilientCRMConnector`: 包装任意 `CRMConnector`
- 每个包装器持有独立的 `CircuitBreaker` 实例

**测试覆盖**: `tests/test_connectors_runtime.py` (19 例)
- 熔断器状态机转换 (closed → open → half_open → closed)
- 重试逻辑与指数退避验证
- 降级语义验证（unavailable 结果）
- 租户隔离验证

**测试结果**: ✅ 19 passed

---

### 20.2 Knowledge/CRM 连接器接入编排 ✅

**实现内容**:

**集成点**: `app/tools.py` 的 `ToolGateway` 类
- 构造器接受可选连接器依赖注入:
  ```python
  def __init__(
      self,
      database: Database,
      *,
      order_connector: OrderConnector | None = None,
      knowledge_connector: KnowledgeConnector | None = None,
      crm_connector: CRMConnector | None = None,
      ...
  )
  ```
- 默认使用 `Sandbox*Connector`（向后兼容，行为不变）
- 可注入 `ResilientOrderConnector(HttpOrderConnector(...))` 等包装后的真实连接器

**编排层使用**:
- `orchestrator.py` 创建 `ToolGateway` 时传入连接器实例
- 所有 Agent（`OrderAgent`, `KnowledgeAgent`）通过 `ToolGateway` 调用工具
- 连接器接口透明：Agent 代码无需修改即可切换实现

**降级路径**:
- 熔断打开时，`OrderAgent` 检测到 `code="unavailable"` 升级人工
- `KnowledgeAgent` 检测到空结果回退到内置 FTS 检索（`database.search_knowledge()`）
- 审计事件记录降级原因（`tool.degraded`）

**测试覆盖**: `tests/test_connector_degradation.py` (7 例)
- Order 连接器降级升级人工验证
- Knowledge 连接器降级回退 FTS 验证
- Golden Set 在降级路径下仍 100% 通过

**测试结果**: ✅ 7 passed

---

### 20.3 HTTP 连接器参考实现 ✅

**实现内容**:

**核心模块**: `app/connectors_http.py`
- **通用 HTTP 客户端**:
  - 基于配置 `HttpConnectorConfig`:
    - `base_url`: REST API 基础 URL
    - `timeout_seconds`: 请求超时时间（默认 10s）
    - `signature_secret`: HMAC-SHA256 签名密钥
    - `signature_header`: 签名头名称（默认 `X-Helix-Signature`）
    - `retryable_status_codes`: 可重试的 HTTP 状态码（429, 5xx）
  
- **HMAC 签名**:
  - 签名消息: `METHOD\nPATH\nCANONICAL_QUERY\nTIMESTAMP\nBODY`
  - 查询参数按键排序后拼接，确保签名稳定性
  - 时间戳防重放攻击（消费者验证时间窗口）

- **错误映射**:
  - 超时/网络错误 → `TransientConnectorError`（触发重试）
  - 404 → `not_found`（业务语义）
  - 4xx（非 404）→ `HttpConnectorError`（不可重试）
  - 5xx → `TransientConnectorError`（可重试）

- **实现的连接器**:
  - `HttpOrderConnector`: 实现 `OrderConnector` 协议
  - `HttpKnowledgeConnector`: 实现 `KnowledgeConnector` 协议
  - `HttpCRMConnector`: 实现 `CRMConnector` 协议

**可注入传输层**:
- `Transport` 类型别名允许测试注入模拟响应
- 生产环境使用 `_default_transport`（基于 `httpx.Client`）
- 测试可注入返回固定 `(status_code, json_body)` 的 lambda

**用途**:
- 作为对接真实 CRM/订单系统的**模板**
- 展示配置驱动、签名验证、超时控制的最佳实践
- 不是硬依赖：默认继续使用 Sandbox 连接器

**测试覆盖**: `tests/test_connectors_http.py` (16 例)
- HMAC 签名生成与验证
- 超时触发 `TransientConnectorError`
- 状态码映射（404 → not_found, 5xx → transient）
- 传输层注入测试（无需真实服务器）

**测试结果**: ✅ 16 passed

---

### 20.4 契约测试套件对外化 ✅

**实现内容**:

**核心模块**: `tests/test_connectors.py`
- **参数化 Conformance Mixin**:
  - `OrderConnectorConformanceMixin`: 验证 `OrderConnector` 协议
    - 测试方法:
      - `test_identity_required_without_customer_ref()`: 未绑定身份返回 `identity_required`
      - `test_verified_customer_succeeds()`: 已绑定客户查询成功
      - `test_other_customer_does_not_disclose()`: 跨客户查询不泄露（返回 `not_found`）
      - `test_unknown_order_not_found()`: 不存在的订单返回 `not_found`
    - 子类实现 `make_order_connector()` 返回 `(connector, known_customer_ref, known_order_id, known_order_status)`

  - `KnowledgeConnectorConformanceMixin`: 验证 `KnowledgeConnector` 协议
    - 测试方法:
      - `test_search_returns_results()`: 查询返回匹配结果
      - `test_search_limit_enforced()`: limit 参数生效
      - `test_search_score_descending()`: 结果按分数降序
      - `test_no_results_for_unmatched_query()`: 无匹配时返回空列表

  - `CRMConnectorConformanceMixin`: 验证 `CRMConnector` 协议
    - 测试方法:
      - `test_resolve_known_customer()`: 已知客户返回 `ok`
      - `test_resolve_unknown_customer()`: 未知客户返回 `not_found`

**已验证的实现**:
1. `SandboxOrderConnector` / `SandboxKnowledgeConnector` / `SandboxCRMConnector` (原有实现)
2. `HttpOrderConnector` / `HttpKnowledgeConnector` / `HttpCRMConnector` (Phase 20.3 新增)
3. `ResilientOrderConnector` / `ResilientKnowledgeConnector` / `ResilientCRMConnector` (Phase 20.1 包装器)

**对外化**:
- 任何第三方实现只需继承 Mixin 并实现 `make_*_connector()` 即可验证合规性
- 测试套件本身无需修改，扩展性强

**测试覆盖**: `tests/test_connectors.py` (19 例)
- Sandbox 连接器合规性验证
- HTTP 连接器合规性验证
- 身份绑定、非泄露、未知资源语义全覆盖

**测试结果**: ✅ 19 passed

---

### 20.5 出站 Webhook ✅

**实现内容**:

**核心模块**: `app/webhooks.py`
- **事件类型** (6 种):
  - `conversation.created`: 会话创建
  - `conversation.escalated`: 升级人工
  - `conversation.resolved`: 会话解决
  - `conversation.sla_breached`: SLA 违约
  - `conversation.sla_impending`: SLA 即将违约
  - `report.generated`: 报表生成完成

- **投递语义**:
  - **At-least-once**: 重试机制保证至少投递一次
  - **幂等 ID**: 每个事件携带 `event_id`（UUID），消费者用于去重
  - **SLA breach 去重**: 同一会话的 breach 事件共享 `event_id`（`sla_breach:{conversation_id}`），避免重复通知

- **签名机制**:
  - HMAC-SHA256 签名: `timestamp.body`
  - 请求头: `X-Webhook-Signature`, `X-Webhook-Timestamp`
  - 消费者验证签名防止伪造和重放

- **重试策略**:
  - 指数退避: `base_backoff * (2 ** attempt)` + 随机抖动
  - 最大尝试次数: 默认 5 次
  - 可重试状态码: 429, 500, 502, 503, 504
  - 超过 `max_attempts` 后移入 `dead` 状态（死信队列）

- **投递状态**:
  - `pending`: 等待投递
  - `sending`: 正在投递（带租约，60s 后可重新认领）
  - `delivered`: 投递成功（2xx）
  - `failed`: 暂时失败，将重试
  - `dead`: 超出重试次数，需人工处理

- **API 端点** (`app/routers/admin.py`):
  - `POST /api/webhooks`: 注册 webhook 端点
  - `GET /api/webhooks`: 列举租户的 webhook 端点
  - `DELETE /api/webhooks/{endpoint_id}`: 删除 webhook 端点
  - `GET /api/webhooks/deliveries`: 查询投递历史（支持过滤：endpoint_id, status, event_type）

**数据模型** (迁移已存在):
- `webhook_endpoints` 表: `id`, `tenant_id`, `url`, `secret`, `subscribed_events_json`, `active`
- `webhook_deliveries` 表: `id`, `endpoint_id`, `event_id`, `event_type`, `payload_json`, `status`, `attempts`, `next_retry_at`, `delivered_at`, `error`

**安全防护** (`app/webhook_safety.py`):
- URL 验证: 拒绝内网地址（SSRF 防护）
- Host 解析器: 验证目标 IP 非内网（`192.168.x.x`, `10.x.x.x`, `127.x.x.x`, `169.254.x.x`）
- Secret 加密存储（使用 `EnvelopeCipherProtocol`）

**测试覆盖**: `tests/test_webhooks.py` (32 例)
- 端点 CRUD（创建/列表/删除）
- 事件发射与投递
- 幂等 ID 去重验证
- 重试逻辑与指数退避
- 签名生成与验证
- 投递历史查询
- 死信队列处理

**测试结果**: ✅ 32 passed

---

## 验收门槛检查

### ✅ 熔断状态机单测 (closed → open → half_open → closed)
- `tests/test_connectors_runtime.py` 包含完整状态转换测试
- 验证连续失败触发 open、超时转入 half_open、探测成功关闭
- 验证探测失败重新打开熔断器

### ✅ 故障注入下 Golden Set 相关用例走降级路径仍通过
- `tests/test_connector_degradation.py` 验证连接器故障时降级行为
- Order 连接器降级 → 升级人工（会话状态正确）
- Knowledge 连接器降级 → 回退 FTS 检索（仍能返回知识）
- Golden Set 门禁测试保持 100% 通过

### ✅ 无重复业务操作
- 所有连接器方法都是只读（幂等）
- 重试不会产生副作用
- Webhook 投递带幂等 ID，消费者可去重

### ✅ 参考实现通过契约套件
- `HttpOrderConnector` 通过 `OrderConnectorConformanceMixin` 全部测试
- `HttpKnowledgeConnector` 通过 `KnowledgeConnectorConformanceMixin` 全部测试
- `HttpCRMConnector` 通过 `CRMConnectorConformanceMixin` 全部测试

### ✅ Webhook 投递恰好一次语义（至少一次 + 消费端去重 ID）有测试
- `tests/test_webhooks.py` 验证 `event_id` 去重
- 同一 `(endpoint_id, event_id)` 只创建一次 delivery 记录
- 消费者通过 `event_id` 识别已处理事件

---

## 测试结果汇总

| 模块 | 测试文件 | 测试数 | 结果 |
|------|---------|-------|------|
| 连接器运行时防护 | `test_connectors_runtime.py` | 19 | ✅ passed |
| HTTP 连接器参考 | `test_connectors_http.py` | 16 | ✅ passed |
| 连接器降级 | `test_connector_degradation.py` | 7 | ✅ passed |
| 契约测试套件 | `test_connectors.py` | 19 | ✅ passed |
| Webhook 投递 | `test_webhooks.py` | 32 | ✅ passed |
| Golden Set 门禁 | `test_golden_set.py` | 1 (27 例) | ✅ passed |

**总计**: 94 个测试全部通过 ✅

---

## 架构改进

### 连接器分层架构

```
ToolGateway (编排层)
    ↓
ResilientOrderConnector (防护层: 熔断 + 重试)
    ↓
HttpOrderConnector (传输层: HTTP + 签名)
    ↓
真实 CRM/订单系统
```

**优势**:
- 每层职责单一，易于测试
- 防护层与传输层解耦，可独立替换
- Sandbox 连接器绕过网络层，测试速度快
- 真实连接器可通过同一套契约测试验证

### Webhook 架构

```
业务事件 (会话创建/升级/解决)
    ↓
WebhookService.emit_event() (持久化 delivery 记录)
    ↓
WebhookService.deliver_pending() (后台任务定期调度)
    ↓
HMAC 签名 + HTTP POST
    ↓
消费者端点 (验证签名 + 去重 + 处理)
```

**优势**:
- 异步投递，不阻塞主流程
- 死信队列捕获无法投递的事件
- 幂等 ID 防止重复处理
- 租户隔离，单租户故障不影响其他租户

---

## 下一步建议

### 立即开始: Phase 21 (Supervisor 质量看板与知识运营)
- 21.1 质量统计聚合（按 prompt_version / intent 维度）
- 21.2 Supervisor 前端视图
- 21.3 知识生命周期（draft/pending_review/published/retired）

### 收口里程碑: Phase 25 (API 治理与开发者体验 → 发布 1.1.0)
- 25.1 统一错误契约（RFC 9457 Problem Details）
- 25.2 OpenAPI 治理（快照门禁）
- 25.3 API 版本与弃用策略
- 25.4 Python 客户端 SDK
- 25.5 API 参考文档站
- 25.6 发布 1.1.0

---

## 成熟度评分变化

| 维度 | Phase 20 前 | Phase 20 后 | 目标 (1.1.0) |
|------|:----------:|:----------:|:------------:|
| 集成能力 | 2.2 | **3.5** | 3.5+ |
| 可靠性 | 3.5 | **3.8** | 4.0+ |
| 总评 | 3.2 | **3.4** | 3.5 |

**提升要点**:
- ✅ 连接器运行时防护完整实现（熔断/重试/降级）
- ✅ HTTP 连接器参考实现提供真实集成模板
- ✅ 契约测试套件对外化，任何实现可验证合规性
- ✅ 出站 Webhook 完整实现（HMAC 签名、重试、死信队列）
- ✅ Golden Set 在降级路径下保持 100% 通过

**结论**: Phase 20 圆满完成，达到 1.1.0 集成能力维度的目标成熟度。
