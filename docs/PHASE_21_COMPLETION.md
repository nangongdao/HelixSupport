# Phase 21 完成报告：Supervisor 质量看板与知识运营

**完成日期**: 2026-09-03  
**基线版本**: 1.4.0  
**目标版本**: 1.1.0 (Phase 19-21-25)

## 执行摘要

Phase 21 "Supervisor 质量看板与知识运营" 已全部完成，所有子任务均已实现并通过测试门禁。本阶段实现了质量统计聚合系统（按天×租户×意图×prompt_version 维度）、Supervisor 前端质量看板视图（趋势图表+知识缺口列表）以及知识生命周期管理（draft/pending_review/published/retired 状态与强制审批流程）。

## 完成的子任务

### 21.1 质量统计聚合 ✅

**实现内容**:

**数据模型**: 迁移 v09 创建 `quality_daily` 表
- 主键: `(tenant_id, date, intent, prompt_version)`
- 聚合字段:
  - `turn_count`: turn 总数
  - `escalation_count`: 升级人工次数
  - `negative_feedback_count`: 负反馈次数
  - `first_response_sum_seconds`: 首次响应时长总和
  - `first_response_samples`: 首次响应样本数
  - `latency_sum_ms`: 延迟总和（毫秒）
  - `estimated_tokens`: 估算 token 成本
- 索引: `idx_quality_daily_tenant_date` 加速租户按日期查询

**核心模块**: `app/quality.py`
- `QualityService` 类提供完整聚合与查询接口:
  - `record_turn()`: 增量记录 turn 指标，使用 `INSERT ... ON CONFLICT DO UPDATE` 实现增量 upsert
  - `record_negative_feedback()`: 记录负反馈，支持 delta 参数（正负评分翻转时 delta=-1 减量）
  - `apply_feedback_rating()`: 处理评分变更，自动计算 delta 并调用 `record_negative_feedback()`
  - `list_buckets()`: keyset 游标分页查询，支持 since/until 日期过滤、intent/prompt_version 筛选
- 辅助函数:
  - `normalize_intent()` / `normalize_prompt_version()`: 清洗并截断键值（80 字符上限）
  - `estimate_tokens()`: 简单 token 估算（字符数 / 4）
  - `encode_quality_cursor()` / `decode_quality_cursor()`: 游标编解码

**API 端点**: `app/quality_routes.py`
- `GET /api/supervisor/quality`: 返回聚合桶列表
  - 查询参数: `since`/`until`（日期过滤）、`intent`/`prompt_version`（维度筛选）、`cursor`（游标）、`limit`（1-200，默认 50）
  - 响应头: `X-Next-Cursor`（下一页游标）、`X-Has-More`（是否有更多数据）
  - RBAC: `metrics:read` 权限（viewer/supervisor/admin）
  - Schema: `QualityBucketOut` 包含聚合指标与计算比率（escalation_rate、negative_feedback_rate、avg_first_response_seconds、avg_latency_ms）

**集成点**:
- `app/turn_persist.py` 在 turn 完成时调用 `quality.record_turn()`
- `app/routers/conversations.py` 在反馈评分变更时调用 `quality.apply_feedback_rating()`

**测试覆盖**: `tests/test_phase21.py` 21.1 节（12 例）
- 增量聚合逻辑验证（多次调用累加）
- 负反馈 delta 正负翻转
- 游标分页（首页、翻页、穷尽）
- 日期过滤与维度筛选
- API 端点权限与错误处理

**测试结果**: ✅ 12 passed

---

### 21.2 Supervisor 前端视图 ✅

**实现内容**:

**核心模块**: `app/static/js/quality-panel.js`
- `loadQualityPanel()`: 加载质量数据并渲染
  - 权限检查: `metrics:read` 权限缺失时显示拒绝态
  - 节流机制: 10 秒内重复调用复用缓存数据
  - 并行获取: `Promise.all` 同时请求质量桶（limit=200）与知识缺口（limit=20）
- `loadQualityPanelForIsland()`: 岛模式对应版本
  - 相同获取逻辑，但结果通过 `helix-inspector-quality` 自定义事件发布到 React 岛
  - 岛模式下 legacy 容器隐藏，避免双重渲染
- `renderQualityPanel()`: 渲染检查器内质量面板（legacy 模式）
- `renderQualityViewIfVisible()`: 渲染独立质量视图（整页模式）

**图表模块**: `app/static/js/quality-charts.js`
- SVG 原生绘制趋势折线图与意图×版本矩阵热图
- 零第三方图表库依赖（保持零构建部署优势）
- 响应式尺寸适配

**全局导航集成**: `app/static/js/nav.js` + `app.js`
- 左侧图标栏"质量看板"挂载点
- `switchAppView('quality')` 切换到独立质量视图
- 复用 Phase 21.1 的聚合数据 API

**知识缺口列表**: `GET /api/supervisor/knowledge-gaps`
- 聚合负反馈消息与无引用回答
- 按频次降序返回待补充知识点（标题/示例查询/出现次数）

**岛模式桥接**:
- legacy `quality-panel.js` 获取数据并构建 HTML
- 通过 `window.dispatchEvent(new CustomEvent('helix-inspector-quality', {detail: {bucketsHtml, gapsHtml}}))` 发布
- React 岛 `inspector-island.jsx` 监听事件并注入 HTML 到 Shadow DOM

**测试覆盖**: 
- 后端集成: `tests/test_phase21.py` 21.2 节（4 例，验证 API 端点、权限、分页）
- 前端纯模块: `tests/frontend/quality-panel.test.js`（加载逻辑）和 `tests/frontend/quality-charts.test.js`（图表数据转换）

**测试结果**: ✅ 4 passed (后端) + 前端纯模块测试通过

---

### 21.3 知识生命周期 ✅

**实现内容**:

**数据模型**: 迁移 v09 扩展 `knowledge_articles` 表
- 新增列:
  - `status TEXT NOT NULL DEFAULT 'published'`: 状态（draft/pending_review/published/retired）
  - `reviewed_by TEXT`: 审批人 actor_id
  - `reviewed_at TEXT`: 审批时间戳
- 向后兼容: 现有文章默认 `status='published'`，检索条件兼容 `NULL` 状态

**检索过滤**: `app/db/knowledge.py`
- `search_knowledge()` 查询增加条件: `AND (k.status = 'published' OR k.status IS NULL)`
- 确保 draft/pending_review/retired 状态的知识条目不被检索命中
- `list_knowledge()` 的 `include_inactive` 参数需要 `knowledge:write` 权限才能查看全生命周期

**API 端点**: `app/routers/knowledge.py`
- `POST /api/knowledge/drafts`: 创建草稿
  - 权限: `knowledge:write`
  - 调用 `database.create_knowledge_draft()` 创建 `status='draft'` 的条目
  - 审计事件: `knowledge.draft_created`
  - Schema: `KnowledgeCreateRequest` → `KnowledgeArticleOut`

- `POST /api/knowledge/{article_id}/review`: 审批动作
  - 权限: `knowledge:write`
  - Payload: `KnowledgeReviewRequest` (`action`: "publish" / "retire", `notes?`)
  - 调用 `database.review_knowledge()` 执行状态转换:
    - `draft` / `pending_review` → `published` (action="publish")
    - `draft` / `pending_review` / `published` → `retired` (action="retire")
  - 非法转换（如 `retired` → `published`）抛 `InvalidTransitionError` 返回 409
  - 重复审批（已 `published` 再次 publish）返回 409
  - 审计事件: `knowledge.reviewed` (包含 action/status/notes)

- `POST /api/conversations/{conversation_id}/messages/{message_id}/knowledge-draft`: 负反馈回流
  - 权限: `knowledge:write`
  - 从负评消息生成草稿知识条目:
    - `title`: "Draft from feedback: {intent}"
    - `content`: 助手消息内容
    - `tags`: [intent]
    - `category`: "feedback_draft"
    - `source_url`: "conversation:{conversation_id}"
  - 审计事件: `knowledge.draft_from_feedback`
  - 用例: Supervisor 发现负反馈消息 → 一键生成 draft → 编辑完善 → 审批发布 → 补充知识库缺口

**状态转换实现**: `app/db/knowledge.py`
- `create_knowledge_draft()`: 插入 `status='draft'` 行
- `review_knowledge()`: 
  - 校验当前状态是否允许目标动作
  - 更新 `status`、`reviewed_by`、`reviewed_at`
  - 非法转换或重复审批抛异常

**RBAC 强制**:
- `GET /api/knowledge?include_inactive=true`: 需要 `knowledge:write` 权限，否则 403
- 创建/编辑/审批端点: 统一需要 `knowledge:write` 权限
- 只读角色（operator/viewer/auditor）只能通过 `GET /api/knowledge`（无 `include_inactive`）查看已发布条目

**重复检测**:
- FTS 相似度阈值提示功能未在本阶段实现
- 列入后续优化（可通过 `search_knowledge()` 的 FTS 分数实现相似度检测）

**前端集成**: `app/static/js/knowledge.js`
- `normalizeKnowledgeArticle()`: 状态字段归一化（缺省回退 'published'）
- `reviewActionsFor(status)`: 根据当前状态返回可用审批动作
  - `draft`/`pending_review` → ["publish", "retire"]
  - `published` → ["retire"]
  - `retired` → []
- `filterKnowledgeArticles()`: 支持按 status 筛选（all/published/draft/pending_review/retired）
- `summarizeKnowledgeArticles()`: 计算五种状态的条目计数

**测试覆盖**: `tests/test_phase21.py` 21.3 节（17 例）
- 草稿创建与 RBAC（非 `knowledge:write` 403）
- `include_inactive` 权限检查（非 writer 403）
- 审批状态转换（draft → publish、published → retire）
- 非法转换（retired → publish）返回 409
- 重复审批（published 再次 publish）返回 409
- 负反馈回流端到端（message → draft → publish）
- 检索只命中 published 状态验证

**前端测试**: `tests/frontend/knowledge.test.js`（8 例）
- 状态归一化（缺省/非法值回退）
- 标签分词（中英文逗号/空白分词去重）
- 表单投影（payload 生成）
- 组合筛选（query + status + language）
- 五计数（summarizeKnowledgeArticles）
- 审核动作（reviewActionsFor 根据状态返回动作）

**测试结果**: ✅ 17 passed (后端) + 8 passed (前端纯模块)

---

## 验收门槛检查

### ✅ 模型/提示变更必须通过 golden set 回归门禁才能 activate
- Phase 19 已落地提示词版本注册表与 Canary 对照部署
- `tests/test_golden_set.py` 作为门禁测试，所有版本激活前必须通过 27 例 100%

### ✅ Supervisor 能按版本/意图下钻定位质量下降来源
- `QualityService.list_buckets()` 支持 `intent` 和 `prompt_version` 维度筛选
- 前端质量面板提供趋势图表与意图×版本矩阵热图
- Supervisor 可通过 `GET /api/supervisor/quality?intent=order_query&prompt_version=v2.3` 精确下钻

### ✅ 知识审批在 API 层强制不可绕过
- 检索条件 `AND (k.status = 'published' OR k.status IS NULL)` 确保 draft 不可被检索
- `review_knowledge()` 状态转换强制校验，非法转换抛异常返回 409
- `include_inactive=true` 查询需要 `knowledge:write` 权限，403 拒绝非 writer

---

## 测试结果汇总

| 模块 | 测试文件 | 测试数 | 结果 |
|------|---------|-------|------|
| Phase 21 完整集成 | `tests/test_phase21.py` | 33 | ✅ passed |
| 前端质量面板 | `tests/frontend/quality-panel.test.js` | 包含在前端门禁 | ✅ passed |
| 前端质量图表 | `tests/frontend/quality-charts.test.js` | 包含在前端门禁 | ✅ passed |
| 前端知识模块 | `tests/frontend/knowledge.test.js` | 8 | ✅ passed |

**Phase 21 子任务分解**:
- 21.1 质量统计聚合: 12 例（增量聚合、游标分页、API 端点）
- 21.2 前端视图集成: 4 例（API 权限、数据加载）
- 21.3 知识生命周期: 17 例（状态转换、RBAC、负反馈回流）

**总计**: 33 个后端测试 + 8 个前端纯模块测试全部通过 ✅

---

## 架构改进

### 质量聚合架构

```
Turn 完成 (turn_persist.py)
    ↓
QualityService.record_turn()
    ↓
INSERT ... ON CONFLICT DO UPDATE (增量 upsert)
    ↓
quality_daily 表 (按天×租户×意图×版本聚合)
    ↓
Supervisor 查询: GET /api/supervisor/quality
    ↓
前端质量面板渲染（趋势图表 + 知识缺口）
```

**优势**:
- 增量聚合，避免全表扫描 `messages.metadata_json`
- 主键唯一约束确保单桶单日单版本只有一行
- 游标分页支持大规模租户按日期降序查询
- 负反馈支持正负翻转（delta 机制）

### 知识生命周期架构

```
创建草稿 (POST /api/knowledge/drafts)
    ↓
status='draft' (不可被检索)
    ↓
审批 (POST /api/knowledge/{id}/review)
    ↓
状态转换: draft → published
    ↓
检索可见 (search_knowledge 只命中 published)
```

**优势**:
- 强制审批流程，API 层不可绕过
- 状态转换在 DB 层校验，非法转换抛异常
- 负反馈回流（消息 → draft）提供知识库补充闭环
- 向后兼容（现有文章默认 published）

---

## 下一步建议

### 立即开始: Phase 25 (API 治理与开发者体验 → 发布 1.1.0)
- 25.1 统一错误契约（RFC 9457 Problem Details）
- 25.2 OpenAPI 治理（快照门禁）
- 25.3 API 版本与弃用策略
- 25.4 Python 客户端 SDK
- 25.5 API 参考文档站
- 25.6 发布 1.1.0

### 后续优化（Phase 21 遗留）
- 知识重复检测：基于 FTS 相似度阈值提示（计算两个知识条目的 BM25 分数重叠度）
- 质量看板趋势预测：基于历史数据的简单移动平均预测质量下降
- 知识缺口自动补充：负反馈聚类 → 自动生成 draft 模板（而非一键手动生成）

---

## 成熟度评分变化

| 维度 | Phase 21 前 | Phase 21 后 | 目标 (1.1.0) |
|------|:----------:|:----------:|:------------:|
| 智能质量 | 3.8 | **4.0** | 3.5+ |
| 可观测/运维 | 3.0 | **3.5** | 3.5+ |
| 前端工程 | 2.0 | 2.0 | 3.0+ |
| 总评 | 3.4 | **3.5** | 3.5 |

**提升要点**:
- ✅ 智能质量: 质量聚合与 Supervisor 看板完成闭环（3.8 → 4.0，超出 1.1.0 目标）
- ✅ 可观测/运维: Supervisor 可按版本/意图下钻定位质量下降（3.0 → 3.5，达到 1.1.0 目标）
- ✅ 知识运营: 生命周期管理与强制审批流程落地

**结论**: Phase 21 圆满完成，**总评 3.5 达到 1.1.0 目标成熟度**。接下来完成 Phase 25（API 治理）即可发布 1.1.0 版本。
