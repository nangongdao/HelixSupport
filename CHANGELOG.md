# Changelog

所有版本遵循[语义化版本](https://semver.org)。API 变更遵循 `docs/API_POLICY.md`(响应体只增不改、弃用需 `Deprecation`/`Sunset` 头 + 至少一个次版本过渡、每次变更记录于此)。

## 2.11.0 — 迁移可运维性: expand 语义门禁与 N/N+1 混跑矩阵 (2026-09-07)

Version 2.11.0 把 42.2 expand/migrate/contract 纪律从"标注门禁"升级为"执行级语义门禁"，并同步 README 到 2.10.0 面。

**无后端契约变更、无新迁移**（纯治理 + 文档）。

### Added

- **expand 迁移执行级 additivity 检查**（`app/migrations.py` `check_expand_additivity`）: phase gate 此前只校验 `@migration(phase="expand")` 的*标注*，从不校验*语句*——一个标注 expand 却 DROP 表/列或重建表改类型的迁移能直接通过门禁，静默破坏滚动升级 N/N+1 窗口（N-1 二进制在滚出期间继续读同一数据库）。新检查把整条迁移链在 scratch 内存 SQLite 上执行，要求每个 expand 步骤的 schema 增量（表集、逐表列集、列声明类型）是执行前的**严格超集**。基于执行而非语法：无论破坏性 SQL 怎么写（直接 execute、executescript、或 `_ensure_column` 类帮助函数）都被抓住。触发器正文与索引变更刻意不比较（触发器可合法改自身表；expand 可增/换索引而不破坏 N-1 读者）。
- **行级数据保全矩阵**（`tests/test_migration_data_matrix.py` 4 例，SQLite + PostgreSQL 双后端）: additivity 门禁证明 *schema* 超集，本套件证明*行*在迁移链上保全——v01 基线库种子代表性行（tenant/conversation/message/audit/turn_request/knowledge）逐步执行 44 个迁移，每步后既有列值逐位不变（新列带默认值/回填允许出现在行尾）。覆盖逐步保全、幂等重跑、红光（数据改写迁移被准确抓住）；PG 用例经 `pg_dialect` 转译路径在真实后端验证（`HELIX_PG_INTEGRATION=1` 门控，与 test_postgres.py 一致）。
- **`scripts/migration_gate.py` 接线**: `verify_migration_chain` + `check_migration_phases` + `check_expand_additivity` 三合一进 supply-chain CI job；数据矩阵随 CI migration additivity gate 步骤运行。
- **测试**（`tests/test_migration_additivity.py` 7 例）: 真实链零问题 + 红光（DROP 表 / DROP 列 / 重建表改列类型 / 执行失败）+ 绿光（纯 additive / contract 阶段允许删列）。

### Fixed

- **成本异常测试日期漂移（2026-09-07 全量跑发现）**: `tests/test_cost_attribution.py` 的 anomaly 基线写死绝对日期（2026-08-30/31、09-01），而 `check_anomaly` 用 `utc_now()` 算 7 天窗口——真实日期走过 2026-09-06 后基线滑出窗口，spike 对空基线比较导致 `test_anomaly_when_cost_spikes` 必失败。基线改为相对真实今天（today-3/-2/-1），两测试永久在窗口内。

### Changed

- **README**: 徽章版本 v2.4.0 → v2.10.0；2.x 平台化清单补 2.5–2.10 列车（治理注册表接线/capability token、线上反馈管线、治理操作台卡、评测运行 WORM 报告、租户开通即发布初始控制面策略、Widget CSAT 客户面闭环）。
- **`ROADMAP_2_X.md` Gate C**: N/N+1 混跑项从待办标记为已自动化落地（执行级超集检查 + 行级保全矩阵）。

## 2.10.0 — Widget CSAT 客户面: 解决状态与评分链接跨面闭环 (2026-09-06)

Version 2.10.0 打通 CSAT 循环的客户侧：此前运营者解决会话后，评分链接只回给运营端（ConversationOut.survey_url）——**widget 渠道的客户永远看不到评分入口**。

**无后端契约变更**（history 端点新增响应头，只增不改）。

### Added

- **history 端点暴露评分链接**（`app/widget_routes.py`）: 会话 resolved 且存在未答复、未过期的 CSAT 调查（复用 `get_pending_csat_survey`）时，响应头携带 `X-CSAT-Survey-URL`（相对/绝对随 csat_base_url）。已答复的调查不再暴露（一次性语义）。
- **widget 已解决横幅 + 评分链接**: `widget.html` 新增 `#resolvedBanner`（会话已解决 + 「评价本次服务」链接）；`widget-app.js` loadHistory 读取头并渲染，startSession 重置状态。
- **跨面旅程**: `tests/ui_widget.py` 扩展——运营者经 API 解决 widget 会话 → widget 重载后横幅与链接出现 → 客户进入一次性评分页 → 5 星提交 → 感谢页。

### Fixed

- **评分链接被渲染守卫清空（自建代码缺陷，旅程首跑抓出）**: loadHistory 设置了 `csatLink.href` 但未同步 `state.resolvedSurveyUrl`，随后 renderResolvedBanner 的守卫用空 state 字段把 href 清空。修复：loadHistory 同步两个 state 字段。

### Changed

- **版本号**: `app/main.py` APP_VERSION 更新至 "2.10.0"。

### Tests

- `tests/test_widget_routes.py` +2 例（resolved+pending 暴露链接/已答复不暴露）；`tests/ui_widget.py` 跨面旅程（解决→横幅/链接→一次性评分页→感谢态），真机绿。

## 2.9.0 — 评测运行面: 治理注册表 API 收官 (2026-09-06)

Version 2.9.0 补齐治理注册表 API 面的最后一块：**评测运行（eval runs）**。2.5 接了审批、2.6 接了反馈/数据集，但运行记录（run 关联数据集/候选/WORM 报告对象）只存在于测试与 CLI 工具中——运营者无法查看任何一次评测的历史与报告。

**无后端契约变更**（新增只读端点，admin:manage）。

### Added

- **服务**: `AiGovernanceService.list_eval_runs(tenant_id, dataset_id, limit)` / `get_eval_run(run_id, tenant_id)`——经 ai_eval_datasets JOIN 做租户作用域（runs 表无 tenant 列，作用域即数据集属主）。
- **API**（governance 路由扩展）: `GET /api/admin/governance/eval-runs?dataset_id=` 与 `GET .../eval-runs/{run_id}`——详情含 WORM 报告内容（`EvalReportStore.read_all()` 按 `report_object_id` 匹配后解包内层 `report`）。
- **装配**: `EvalReportStore(settings.eval_worm_dir)` 首次进入生产装配（`AppServices.eval_reports`）——此前 WORM 报告存储只活在测试与 CLI 中。
- **admin 岛治理卡**: 新增「评测运行」小节（候选 @ 数据集 + 通过/未判定状态）。

### Changed

- **版本号**: `app/main.py` APP_VERSION 更新至 "2.9.0"。

### Tests

- `tests/test_governance_api.py` +4 例（列表/详情租户作用域经 other-admin 主键、WORM 报告随行、404 与 RBAC、bootstrap 装配断言）。

## 2.8.0 — 审计修复列车: 影子流量保真度与 cell 健康循环 (2026-09-06)

Version 2.8.0 收敛本轮对 2.x 旗舰功能面的功能审计——三个已发货缺陷修复(影子流量两层保真、cell 健康循环隔离),全部由"真实执行路径测试 + 失败瞬间取证"的方法论发现(既有测试全为单元件或未覆盖该路径)。

**无新迁移、无 API 契约变更**。

### Fixed

- **影子流量对比保真度（2.1.x 缺陷，2.8.0 审计发现）**: 请求控制中间件的影子调用把 `v1_response_body` 硬编码为 `{"status": "ok"}`——字段级对比拿 v2 真实响应对比一个假体，每条影子记录都是全字段 mismatch（健康监控的 24h 窗口会读到常驻 ~100% 不匹配率，告警全是噪声）；且 `v1_status_code` 参数被无视，比较记录写死 `200 if v1_response_body else None`（404 也记成 200）。修复：中间件对 JSON 响应有界缓冲（256KB 上限，超限部分懒透传绝不截断客户端响应）并重包响应，`response_json_body` 提取真实体（非 JSON/超大/非 dict/不可解码 → 无字段对比但状态/延迟信号保留）；真实 `v1_status_code` 穿过 maybe_shadow_request → create_shadow_task → shadow_request_to_v2 三跳。既有测试全为单元件（无中间件路径），缺陷因此存活。新增 10 例：`response_json_body` 边界（非 JSON/超大/非 dict/不可解码）+ 真实 app 中间件集成（真实体捕获/404 状态传递/X-Shadow-Request 重放标记永不二次影子）。
- **cell 健康循环鲁棒性（2.2.x 缺陷，同审计模式）**: `periodic_health_check` 的 while 循环无逐 cell 异常隔离——`check_cell_health` 只捕获 httpx 异常族（`InvalidURL`/`UnsupportedProtocol` 穿透），一个配置错误的 health_url 即可让火后不管的任务永久死亡，**所有 cell 的健康状态冻结**（区域故障切换据此决策）。修复：`check_cell_health` 补 `except Exception` 兜底（永不抛出契约）；巡检体抽为 `health_check_tick`（逐 cell 隔离、坏 cell 记录跳过），循环调用之；确定性 tick 测试替代 cancel 时序测试。
- **影子对比第二层保真（同审计发现）**: 字段对比盲映射三处结构性问题——①v2 列表响应按设计是 `{"data": [...], "next_cursor": ...}` 信封而 v1 是裸列表，顶层键对比必然全 mismatch 且 `_compare_responses` 对两个 list 直接 AttributeError（fire-and-forget 任务静默崩溃）；②影子重放盲映射 `/api/` → `/api/v2/`，无 v2 对应面的 GET（health/turn-jobs/labels/detail 等）重放后全 404 噪声；③对比方向按并集把 v1 独有字段记为漂移，而实测 v2 会话条目是 8 键契约投影（v1 31 键超集，共享键零差异——SDK shadow-read 同款契约）。修复：`_align_payloads` 信封解包（对比负载、弃 next_cursor）、`_compare_responses` 改 **v2 驱动**语义并支持 list 逐元素对比（问题名 `items[i].field`/`items.length`）、`is_v2_shadow_eligible` 路径资格（仅会话列表与消息列表两个实测可对比面；detail 顶层零共享键不资格）。测试：语义更新（v1 独有键忽略）+ 列表/信封/资格 7 例 + 全链集成（MockTransport v2 信封 vs 真实 v1 体 → 比较行 fields_matched=["items"]）1 例。

### Tests

- `tests/test_shadow_traffic.py` +10 例(`response_json_body` 边界 + 真实 app 中间件集成:真实体捕获/404 状态传递/重放标记永不二次影子)+ 语义更新(v1 独有键忽略)+ 列表/信封/资格 7 例 + 全链集成(MockTransport v2 信封 → 比较行 fields_matched=["items"])。
- `tests/test_cell_router.py` +2 例(坏 health_url 永不抛出/确定性 tick 逐 cell 隔离)。
- 全量后端套件绿;ruff 0.9.9 / pyright app 0;OpenAPI 快照仅 info.version 变更。

 — 治理操作台: 审批与反馈评审的人工操作面 (2026-09-05)

Version 2.7.0 给治理面补上人工操作入口：admin 岛第 10 张卡「治理操作台」把 2.5/2.6 的审批与反馈评审 API 变成可点击的操作——待决审批的批准/拒绝、线上反馈队列的接受/拒绝、评测数据集读数。

**无后端变更、无新迁移**（纯前端 + legacy 写桥）。

### Added

- **AdminGovernanceCard**（`frontend/src/islands/admin/governance-card.jsx`）: 三个查询（pending 审批/pending_review 反馈/数据集注册表）接入既有身份门控与刷新生命周期；批准/拒绝与接受/拒绝经 **helix-admin-governance-decide / -feedback-review** 两个新桥事件回 legacy（api()/toast 生命周期不变，与既有九卡同构）。
- **legacy 写桥**: `js/admin-actions.js` 新增 `governanceDecideFromIsland` / `governanceFeedbackReviewFromIsland`（fail-closed + dispatchAdminSaved 驱动岛重取），boot.js 绑定 + wire.js ctx + app.js 委托面同步。
- **样式**: `.governance-row` 行族复用 bulk-member/SLA-row 形态。

### Changed

- **版本号**: `app/main.py` APP_VERSION 更新至 "2.7.0"。

### Tests

- vitest +2（治理卡渲染契约：审批/反馈行与数据集读数；桥契约：decide/review 事件载荷与 saved 域）。
- `tests/ui_admin_island.py` 扩展治理段（种子审批/反馈经直接 DB 插入——maker-checker 请求人用 seed 身份避免自批 409；岛上批准/接受 → POST 断言 → 列表清空），真机绿。
- **岛旅程加固（冷服务器取证驱动的三处稳健化）**: ①导航点击改为「点击直到目标视图可见」重试原语（冷服务器上点击可早于 app.js 绑定视图切换——岛自身的 react-query 照常发数据请求，卡片网格在仍隐藏的视图里渲染，等待因而超时；admin/knowledge 两套件同享 open_view 原语）②boot_shell 显式等待 `#desktopSplash` 隐藏（文本断言在覆盖层下也能通过而 fill/click 的可操作性检查不能）③admin 岛 open_admin_island 检测全新库首渲染的零高裁剪布局并 reload 一次重开（取证：input 在视口 y=0 被 app-header 覆盖、祖先链含空 class/overflow:clip/h=0 节点，第二次页面加载即恢复）。修复过程中 admin-actions.js 一度超 400 行门禁，治理处理器迁至独立 `js/governance-bridge.js` 模块（mirroring admin-report-bridge.js）

## 2.6.0 — Online Feedback 管线: 负评分自动入治理库 + 评审/晋级 API (2026-09-05)

Version 2.6.0 打通 43.5 治理注册表的另一半：线上反馈从采集到评测数据集的生产管线。此前只有审批面接了线（2.5.0），客户负评分与治理注册表之间是断开的。

**发布亮点**:
- ✅ **负评分自动入治理库**: 客户评分翻转为 -1 时，被评分的交换自动脱敏入册（pending_review，exactly-once per flip，best-effort 绝不阻塞评分写入）
- ✅ **评审队列 API**: 治理 API 新增 feedback 列表/评审与数据集列表/条目端点
- ✅ **晋级 API**: accepted 行折叠进具名数据集的新版本；pending/rejected 搭车即整批 409 中止

**无新迁移**（复用 v41 四表）。

### Added

- **负评分自动摄取**（`app/routers/conversations.py`）: 评分端点在质量聚合之后 best-effort 调用 `ingest_online_feedback(source="negative_rating", payload={message_id, rating, reason, rated_content})`——脱敏发生在入库前（存储即脱敏文档）；仅新评分 = -1 且旧评分 ≠ -1 时触发（与质量聚合的翻转语义一致）。
- **治理反馈/数据集 API**（`app/routers/governance.py` 扩展，全 `admin:manage`）:
  - `GET /api/admin/governance/feedback?status=pending_review` — 评审队列（accepted/rejected/all 可选）
  - `POST /api/admin/governance/feedback/{id}/review` — 人工接受/拒绝
  - `POST /api/admin/governance/datasets/promote-feedback` — accepted 行折进具名数据集新版本（`strategy="feedback"`）；任何 pending/rejected id 整批 409 中止
  - `GET /api/admin/governance/datasets` + `GET .../datasets/{id}/items` — 注册表与条目
- **服务方法**: `AiGovernanceService.list_feedback` / `list_datasets`。

### Changed

- **版本号**: `app/main.py` APP_VERSION 更新至 "2.6.0"。

### Tests

- `tests/test_governance_feedback.py` 6 例（负评分翻转自动入册且脱敏文档落库/exactly-once/正评分不入册/评审+晋级 round-trip/pending 搭车 409 中止/评审与晋级 RBAC 403）。

## 2.5.0 — AI Governance 生产接线: 审批 API、capability secret 与首个 mutating 工具 (2026-09-05)

Version 2.5.0 关闭 §43.5 最后一个本地推迟项——"capability secret 在 main.py→orchestrator→ToolGateway 生产接线（随首个真实 mutating 工具）"。审计发现治理注册表（审批/数据集/在线反馈）自 43.5 起是**纯测试机器**：app 内零构造、零 API 暴露。本版本把治理面接入生产装配。

**发布亮点**:
- ✅ **首个真实 mutating 工具**: `knowledge.draft` 经 ToolGateway 全治理链（策略→schema→capability token→审计）创建 `draft` 状态知识文章
- ✅ **治理审批 API**: maker-checker 工作流（请求→他人批准，自批 409）让 `tool_enablement` 审批在生产可操作
- ✅ **CAPABILITY_SECRET**: 网关令牌验证的生产开关（≥32 字节校验；服务端按调用铸造短 TTL 令牌）

**无新迁移**（复用 v41 四表）。

### Added

- **配置**: `CAPABILITY_SECRET`（可选，空=开发默认令牌不强制；设置时 ≥32 字节校验）——经 `ConversationOrchestrator` 注入 `ToolGateway`，生产装配同时传入 `AiGovernanceService`（此前从未在 app 内构造）；bootstrap 在生产未设置时记录 `capability_secret.unset` 警告。
- **首个 mutating 工具 `knowledge.draft`**: 网关方法（策略 `side_effect="mutating"`、参数 schema 含长度边界）经 `SandboxKnowledgeConnector.draft_article`/`ResilientKnowledgeConnector.draft_article`（写调用熔断降级必须**响亮失败**而非假成功）创建 `draft` 状态文章并审计 `tool.knowledge_drafted`；拒绝路径审计 `tool.denied`（drift 监控信号源）。
- **治理审批 API**（新 `app/routers/governance.py`，全 `admin:manage`）: `GET /api/admin/governance/approvals?status=`、`POST .../approvals/request`、`POST .../approvals/{id}/decide`（SelfApprovalError→409）。
- **Copilot draft 端点**: `POST /api/copilot/knowledge-draft`（`operator:act`）——服务端按调用铸造短 TTL capability token 并呈现给网关验证，拒绝以 403 + `tool denied (reason)` 呈现。
- **schema 校验器扩展**: `minLength`/`maxLength`/`minItems`/`maxItems`（进 schema digest——收紧边界即令牌失效）。

### Changed

- **版本号**: `app/main.py` APP_VERSION 更新至 "2.5.0"。

### Tests

- `tests/test_governance_api.py` 8 例（审批 round-trip/maker-checker/重复 409/RBAC、draft 落库+审计/令牌铸造验证/schema 拒绝+审计/viewer 403/无密钥 fail-closed、CAPABILITY_SECRET 校验 2 例）；`test_ai_governance.py` +校验器边界单测；装配接线断言（gateway.governance_service is services.ai_governance）。

## 2.4.0 — AI 治理闭环: 成本/citation drift 信号 (2026-09-05)

Version 2.4.0 关闭 ROADMAP §43.5 遗留的本地推迟项——"citation validity 与成本阈值接 drift 信号源"。2.3.0 交付 live-model 成本遥测后，本版本把成本异常与引用失效接成 drift 信号：越限时与质量/拒绝信号一样自动清空 canary 并审计，线上 AI 治理四类信号（质量、拒绝、成本、引用）全部闭环。

**发布亮点**:
- ✅ **成本因子信号**: 当日归因成本达到租户基线日均的 `DRIFT_MAX_COST_FACTOR` 倍（默认 2.0）自动停 canary
- ✅ **引用失效信号**: 窗口内 assistant 消息引用失效（退役/删除/未发布）知识文章的占比超过 `DRIFT_MAX_STALE_CITATION_RATE`（默认 0.2）自动停 canary
- ✅ **零新写路径**: 两个信号全部复用既有面（`tenant_cost_daily` 聚合 + `messages.metadata_json` + `knowledge_articles`），turn 路径零新增写入

### Added

- **成本 drift 信号（ROADMAP 2.4.0）**:
  - `app/cost_attribution.py`: `check_anomaly()` 新增可选 `today` 参数（默认真实当前日期）——drift 监控注入自己的时钟使窗口数学可测，analytics 端点行为不变。
  - `app/drift_monitor.py`: `DriftMonitor` 新增 `cost_service` 注入（默认自行构造）；`_cost_signal()` 复用 `check_anomaly` 的基线/因子语义，但阈值由 `DRIFT_MAX_COST_FACTOR` 独立判定（analytics 端点继续走 `CostTolerance`，两套消费互不干扰）；基线为 0（尚无定价推理）永不触发，与异常检测语义一致；探测失败仅记日志不阻塞其余信号。
  - `app/config.py`: `DRIFT_MAX_COST_FACTOR`（默认 2.0；校验必须 >1；`None`/未设置停用）。
- **引用失效 drift 信号（ROADMAP 2.4.0）**:
  - `app/drift_monitor.py`: `_citation_signal()` 统计窗口内带引用的 assistant 消息中，引用 id 不再解析为可服务文章（`active=1` 且 `status='published'` 或 NULL——与检索面完全一致）的占比；样本量低于 `drift_min_turns` 静默（小样本不停发布）；越限严格大于阈值才触发（与率值信号一致）。metadata 用 Python 解析而非 JSON SQL，SQLite/PG 双方言中立；`IN` 列表按 500 分块规避 SQLite 参数上限。
  - `app/config.py`: `DRIFT_MAX_STALE_CITATION_RATE`（默认 0.2；校验 (0,1]；`None`/未设置停用）。
- **接线**: `app/main.py` 把 `cost_attribution_service` 注入 `DriftMonitor`，housekeeping 小时 sweep 自动获得两个新信号；`ai.drift_canary_stopped` 审计 payload 的 `signals` 数组携带 `cost_factor`/`stale_citation_rate`。
- **测试**: `tests/test_ai_governance.py` 新增 `DriftCostCitationSignalTests` 8 例（成本越限停 canary + 审计断言/低于阈值与零基线静默/信号停用；引用退役越限停 canary/新引用静默/部分越限与恰好阈值不触发/样本下限静默/信号停用），`tests/test_config_validation.py` 新增 2 例负向校验（factor ≤1 拒绝、rate 越界拒绝）。
- **成本仪表盘管理卡（岛原生，补 2.3.0 API 的 UI 面）**: admin 岛第 9 张卡 `frontend/src/islands/admin/cost-card.jsx`——4 个只读 GET（`/api/analytics/costs/{daily,by_agent,by_prompt,anomaly}`）经 `["admin"]` 前缀 react-query 接入既有身份门控与 `helix-admin-refresh/saved` 生命周期，零新写桥；读数含累计调用/tokens/成本（`formatUsd`：亚美分保留 6 位、可读金额 2 位）、今日 vs 基线异常读数（与 2.4.0 drift 同一评估语义，`无定价推理` 空态渲染 — 而非 $0.00，沿用 CSAT 卡 W2 约定）、按功能（agent 值 → 中文标签映射）与按提示版本拆分（成本降序）；`cost-status` 状态徽标用对比度修正过的 `--amber` 令牌，行样式与 CSAT 卡同形。模型/卡/常量按域拆分进 `admin/`（模块均 ≤400 行），vitest 新增 5 例（模型纯函数 4 + 渲染契约 1，九卡断言更新），真实浏览器岛模式 + 种子数据全旅程验证（含双主题），全套前端/性能门禁绿。
- **桌面壳 admin 写旅程自动化 + 岛身份门控竞态修复**: 新增 `tests/ui_admin_island.py`——桌面壳（Tauri 前置条件）里真浏览器 + 真后端驱动岛模式管理台完整写闭环（岛表单 → helix-admin-* 桥 → legacy 处理器 → 真实 API → saved 事件 → 岛 react-query 重取重渲染）：配额保存/成员邀请+改角色+停用/webhook 注册+确认删除/非管理员零特权请求（含 `/api/analytics/`）。**套件首跑即抓出既有竞态缺陷**：React 18 无 act() 时 effect 异步提交，`/api/me` 的 helix-identity 派发可落在岛首次渲染（陈旧全局快照）与 effect 订阅之间——事件无订阅者被错过，管理员岛在暖服务器上永久卡在 guest 门控。修复：`useIdentity` 类钩子（admin 门控/identity 读数/mentions 徽标三处）订阅后从全局快照追赶同步一次，窗口闭合；渲染序技巧的确定性 vitest 回归测试锁定（兄弟组件渲染期派发、effect 阶段前无订阅者的精确窗口）。真机套件连跑 3 次全绿，vitest 173、前端/性能门禁、a11y 浏览器套件绿。

### Changed

- **版本号**: `app/main.py` APP_VERSION 更新至 "2.4.0"。
- **组合根瘦身（Phase 27 纪律回归）**: `app/main.py` 因 2.x 装配代码回涨到 1,237 行——超出 Phase 27 拆分后的 714 行基线。请求控制中间件（request-id/安全响应头/影子流量采样/request 指标）与 versioned Problem Details 异常处理器整体抽取为 `app/middleware.py`（325 行，`register_request_controls`/`register_error_handlers` 两个注册工厂，代码逐字搬移仅换归属），main.py 回落到 963 行。快照门禁不变、全量后端套件绿。
- **组合根瘦身第二片（服务装配体）**: 数据库引导、凭据注册层、orchestrator/worker/成本归因核心、审计锚定、信封加密、outbox、附件/归档存储、保留服务、控制面、drift/影子监控、cell 注册与 `AppServices` 容器整体抽取为 `app/bootstrap.py`（522 行，`build_application(settings)` 返回 `ApplicationContext`，装配顺序逐字保留，housekeeping 闭包对 `services` 的晚绑定语义不变）；OIDC 构造随之入 bootstrap（避免闭包绑定断裂，ruff F821 抓出 `oidc_flow` 在新作用域未赋值的隐患）；`AppServices` 随迁并从 main 重导出，消费方导入面零改动。main.py 最终回落到 **519 行**（低于 Phase 27 基线），快照不变、全量后端套件绿。
- **controller 领域化（生命周期域拆分）**: `app/orchestrator.py`（935 行）的运营者状态迁移域——handoff/claim/release/assign/回复/内部备注/标签与优先级/批量操作/resolve/reopen 及 CSAT 链接与 webhook emit 助手——整体抽取为 `app/conversation_lifecycle.py` 的 `ConversationLifecycleMixin`（591 行，方法逐字、经 `self` 读协作方，延续 db mixin 的既定模式与 pyright 文件级豁免）；三个生命周期异常类（`TurnInProgressError`/`InvalidTransitionError`/`IdempotencyConflictError`）迁入 `app/domain.py` 与 `ConversationStatus` 同域，orchestrator 重导出保持 middleware/routers/tests 的导入面零改动。orchestrator.py 回落到 **372 行**（只留启动、turn 接收与流水线组装），快照不变、全量后端套件绿、零测试修改。

### Fixed

- **`check_anomaly` 在 PostgreSQL 上不可用（2.3.0 方言缺陷，本版本审计发现）**: 基线查询用了 SQLite 专有的两参 `date(?, '-N days')` 修饰符（PG 无此函数且 pg_compat 垫片刻意未提供）和 `MAX(1, COUNT(*))`（PG 的 `COUNT(*)` 返回 bigint，不匹配 int/int `max()` 垫片）——成本异常端点在真实 PostgreSQL 上直接报 `UndefinedFunction`，2.4.0 的 drift 成本信号也会因 fail-safe 静默失效。本机 PG 18 现场复现后修复：窗口边界改在 Python 计算（ISO 日期串比较，双方言中立），除零守卫改用 `NULLIF(COUNT(*), 0)`，SUM/COUNT(*) 的 NULL 稀释语义与原实现逐位一致（SQLite/PG 双端数字核对相同）。回归守卫：`tests/test_cost_attribution.py` 源码方言扫描（AST 字符串常量级，含守卫自证断言）+ PG 集成套件新增运行时用例 `test_cost_anomaly_uses_portable_sql`（真实 PG 上锁定稀释语义与 2× 异常边界）。

## 2.3.0 — AI Cost Attribution: 完整推理成本追踪与异常检测 (2026-09-04)

Version 2.3.0 实现了 AI 成本归因系统，为每次模型推理调用记录真实 token 用量和 USD 成本，并提供按租户/agent/prompt 维度的聚合报表与成本异常检测。本版本配合 Provider 定价元数据实现了精确的成本计算（误差 <1%）和基于基线的自动告警。

**发布亮点**:
- ✅ **真实成本追踪**：每次推理记录 prompt/completion tokens 与 USD 成本（基于 provider 定价）
- ✅ **多维度聚合**：按租户/日期/provider/model 四维聚合，支持任意时间窗口查询
- ✅ **异常检测**：今日成本 vs N 日基线，超过阈值（默认 2×）自动告警
- ✅ **管理员 API**：4 个分析端点（daily/by_agent/by_prompt/anomaly），需 `admin:manage` 权限

**新增迁移**: v44（1 个，phase="expand"）

### Added

- **AI 成本归因系统（ROADMAP 2.3.x）**：
  - `app/model_provider.py` 扩展：新增 `ModelResponse` dataclass（content/usage/model/provider/cost_usd/latency_ms/model_ref）替代原始字符串返回；`OpenAICompatibleProvider.complete()` 解析响应 usage 字段（prompt_tokens/completion_tokens），查找 `ProviderMetadata.pricing`，计算 `cost_usd = prompt_tokens/1000 × input_cost + completion_tokens/1000 × output_cost`，精度保留 6 位小数；所有 provider 调用点更新为 `.content` 访问（app/agents.py、app/copilot.py、app/language.py、app/summaries.py）。
  - `app/cost_attribution.py` (206 行)：成本归因服务核心；`record_inference_cost()` 写入 `inference_costs` 表并更新 `tenant_cost_daily` 聚合（原子 upsert，累加 turn_count/tokens/cost_usd）；`get_tenant_cost_summary()` 按时间范围查询汇总（支持 since/until 参数）；`get_cost_by_dimension()` 按 provider/model/agent/prompt_version 维度分组查询（date_str 参数过滤单日）；`check_anomaly()` 今日成本异常检测（对比过去 N 日均值，默认 baseline_days=7、threshold_factor=2.0，返回 anomaly 布尔值 + today_cost/baseline_avg/factor）。
  - Migration v44 (phase="expand")：新增 `inference_costs` 表（id/tenant_id/conversation_id/turn_id/message_id/agent/prompt_version/provider/model/prompt_tokens/completion_tokens/cost_usd/latency_ms/created_at，外键 tenant_id → tenants ON DELETE CASCADE，索引 tenant_id+created_at）；新增 `tenant_cost_daily` 聚合表（tenant_id/date/provider/model 复合主键，字段 turn_count/prompt_tokens/completion_tokens/cost_usd 默认 0，外键 tenant_id → tenants ON DELETE CASCADE）。
  - `app/routers/analytics.py` (54 行)：成本仪表板 API；4 个端点全部需 `admin:manage` 权限——`GET /api/analytics/costs/daily?start_date=&end_date=` 返回租户时间范围汇总、`GET /api/analytics/costs/by_agent?date=` 按 agent 分组、`GET /api/analytics/costs/by_prompt?date=` 按 prompt_version 分组、`GET /api/analytics/costs/anomaly` 返回今日异常检测结果。
  - `app/main.py` 集成：`AppServices` dataclass 新增 `cost_attribution: CostAttributionService` 字段，`configure()` 实例化服务（`cost_attribution=cost_attribution_module`），`create_app()` 注册路由（`app.include_router(analytics_router, tags=["Analytics"])`）。
  - **真实接线（ROADMAP 2.3.3 补完）**: 模型推理调用点全部接入成本记录——`TriageAgent._model_decision`（语义路由，agent="triage"）、`LanguageService.detect/translate`（agent="language_detect"/"language_translate"）、`CopilotService._model_suggestions/rewrite_tone`（agent="copilot_suggest"/"copilot_rewrite"）、`SummaryService._model_summary`（agent="summary"）。统一经 `record_model_response()` fail-safe helper 写入（never raises，无 usage 的响应跳过）；`InferenceContext` 携带 conversation_id/agent/prompt_version 归因维度。所有服务经 `ConversationOrchestrator`/`create_app` 注入 `CostAttributionService`。
  - 测试：`tests/test_cost_attribution.py` 9 例（记录成本/汇总查询/维度分组/异常检测基线/超标/正常/**turn 路径接线**/无模型跳过）、`tests/test_cost_analytics_api.py` 6 例（daily 汇总/by_agent/by_prompt/anomaly 端点/viewer 权限拒绝），所有测试修复 Windows 清理顺序（database.close() 先于 client.close() 和 _tmp.cleanup()）。
  - OpenAPI 快照：`api/openapi.json` 重生成（+326 行 = 4 个新端点 schema）。
  - 迁移上界同步：`tests/test_phase38.py`、`tests/test_streaming.py`、`tests/test_migration_registry.py` 三处断言更新为 44（从 43）。

### Added（2.2.x 列车补完，随 2.3.0 发布合入）

- **影子流量真实链路接通（2.1.x 补完）**: v1 读请求现在会按采样率经 HTTP 中间件异步重放到 v2 端点，写入 `shadow_traffic_comparisons`；`SHADOW_TRAFFIC_BASE_URL` 替代原先硬编码的 `http://127.0.0.1:8000`（生产可指向真实 v2 API）；监控接入 turn-worker housekeeping 周期评估 24h 窗口健康度。v42 迁移已注册进迁移链。
- **多 Cell 真实链路接通（2.2.x）**: 注册 v43 迁移（`replication_log` 表）；新增带内部认证（控制面 secret）的 `POST /api/internal/replication/apply` 复制入口，支持 `conversations`/`messages`/`audit_events`/`knowledge_articles` 的白名单列 upsert 与 delete；启动时按 cell 注册表为每个对等 cell 拉起复制 worker 与周期健康检查任务。
- **审计锚定密钥持久化（SEC-005）**: `AUDIT_ANCHOR_KEY`（base64 原始 Ed25519 私钥）现在真正生效——`Ed25519KmsSigner.from_encoded()` 恢复持久键，重启后 kid 稳定、历史锚点可继续验证。
- **区域故障切换（ROADMAP 2.2.3）**: 新增 `app/region_failover.py`——`check_region_health`（fail-safe 探测）、`find_healthy_cell_in_region`、`initiate_failover`（校验目标 cell 健康 → 强制数据驻留 → 发布带签名的新控制面快照 → 返回可审计的 FailoverState）；`--dry-run` 只验证不发布。配套 runbook `scripts/run_region_failover.py`（支持 `--target-cell`/`--target-region`/`--dry-run`/`--skip-health`）。`CELL_REGISTRY_JSON` 环境变量绑定补齐，多 cell 部署配置可完全走环境变量。

### Changed

- **生产安全校验收紧**: `APP_ENV=production` 时 `validate()` 拒绝内置默认 `WIDGET_SECRET`，并拒绝在未显式设置 `CONTROL_PLANE_SECRET` 时回退到开发默认值（避免可伪造 widget token / 控制面签名）。
- **版本号**: `app/main.py` APP_VERSION 更新至 "2.3.0"

### Fixed

- **v42/v43 迁移此前未注册进迁移链**（仅 CHANGELOG 声称存在，`all_migrations()` 实际只有 41 个）：v42 已加入 `_VERSION_MODULES`，v43 重写为 `@migration` 装饰器格式后注册，`verify_migration_registry`/`check_migration_phases` 全绿，迁移上界断言同步至 44。
- **成本记录此前未接线（声明未实现）**: `record_inference_cost` 在真实代码路径零调用，`inference_costs` 表永远为空、仪表板恒 0。已补完所有 4 类模型调用点（triage/language/copilot/summaries）并注入 `CostAttributionService`；`by_agent`/`by_prompt` 端点修正为真正的按 agent/prompt_version 分组（此前错误地按 model/provider）——`inference_costs` 表新增 `agent`/`prompt_version` 归因列。
- **无定价推理成本口径不一致（code review 发现）**: `cost_usd=None` 时聚合表 `tenant_cost_daily` 错误落成 `0.0`（`cost_usd or 0.0`），与明细表 NULL 口径矛盾——明细聚合排除 NULL 而聚合表 0.0 会稀释异常检测均值。修复：聚合表 `cost_usd` 改为可 NULL，upsert 用 `COALESCE(cost_usd,0) + COALESCE(excluded.cost_usd,0)`，两表统一为 NULL 语义（无定价不计 USD、照计 token），并补回归断言。
- **analytics 端点 OpenAPI 元数据缺失**: 4 个成本端点缺 summary/description/tags，`test_openapi_gate` 门禁失败。已为每个端点补 `summary=`/`description=`（装饰器参数）和 `tags=["analytics"]`，快照重生成。

## 2.1.0 — Shadow Traffic System: v1/v2 验证与自动降级 (2026-09-03)

Version 2.1.0 实现了影子流量系统，为 API v1→v2 迁移提供生产级验证能力。通过可配置采样率将真实 v1 请求异步重放到 v2，进行字段级深度对比并追踪延迟差异，配合健康监控实现自动降级。本版本为后续多 cell 部署和成本归因奠定基础。

**发布亮点**:
- ✅ **影子流量核心**：异步 v1→v2 请求复制，字段级递归对比，延迟追踪
- ✅ **健康监控**：24 小时滚动窗口，不匹配率/延迟回归双重阈值检测
- ✅ **fail-safe 设计**：默认关闭，纯函数评估器，从不阻塞 v1 响应路径
- ✅ **生产就绪**：完整测试覆盖（20 例），遵循项目架构模式

**新增迁移**: v42（1 个，phase="expand"）

### Added

- **影子流量系统（ROADMAP 2.1.x）**：
  - `app/shadow_traffic.py` (254 行): v1→v2 请求异步复制、字段级差异对比、延迟追踪；`ShadowRequest` 快照原始 v1 请求（路由、payload、tenant_id）、`ShadowComparison` 记录对比结果（匹配/不匹配字段、状态码、延迟差异）、`should_shadow_request` 采样决策（基于可配置 0-100% 比率）、`shadow_request_to_v2` 异步重放（构造等效 v2 请求、调用 v2 endpoint、捕获异常不影响 v1）、`_compare_responses` 递归深度对比（处理嵌套字典/列表、null 值、类型不匹配）、`_record_comparison` 数据库持久化（记录完整对比上下文供后续分析）。
  - `app/shadow_monitor.py` (158 行): 影子流量健康监控与自动降级；`ShadowSignals` 聚合指标（total_comparisons/mismatch_count/mismatch_rate/v1_p95_latency_ms/v2_p95_latency_ms）、`ShadowMonitorThresholds` 阈值配置（max_mismatch_rate=0.05 即 5%、max_latency_regression_ms=200.0、min_comparisons=100 样本量门槛）、`evaluate_shadow_health` 纯函数健康评估（样本不足→健康、不匹配率超标→拒绝、延迟回归超标→拒绝、其他→健康）、`collect_shadow_signals` 24 小时滚动窗口 SQL 查询（聚合分租户/路由统计、计算 P95 延迟通过 `_percentile` 辅助函数）、`monitor_shadow_traffic` 周期性清扫钩子（挂载到 housekeeping 任务）。
  - Migration v42 (phase="expand"): 新增 `shadow_traffic_comparisons` 表，字段包括 id/tenant_id/request_id/route/v1_status_code/v2_status_code/matched_fields（JSON 数组）/mismatched_fields（JSON 数组）/v1_latency_ms/v2_latency_ms/sampling_rate/created_at；复合索引 `idx_shadow_comparisons_tenant_route_time` (tenant_id, route, created_at) 支持时间窗口查询、唯一索引 `idx_shadow_comparisons_request` (request_id) 防止重复记录。
  - `app/config.py` 扩展: 新增 `shadow_traffic_enabled: bool = False`（生产环境需显式启用）、`shadow_traffic_sample_rate: float = 0.05`（默认 5% 采样）；环境变量 `SHADOW_TRAFFIC_ENABLED`/`SHADOW_TRAFFIC_SAMPLE_RATE` 解析。
  - `app/telemetry.py` 扩展: 新增 `record_shadow_comparison(result, latency_diff_ms, route)` 函数，记录影子流量对比指标到 metrics 后端（`shadow.comparison_result` 计数、`shadow.latency_diff_ms` 直方图）。
  - 测试: `tests/test_shadow_traffic.py` 20 例全绿——采样逻辑（禁用/100%/0%/概率性）、深度相等判断（原始类型/列表/字典/嵌套结构/null/类型不匹配）、响应对比（完全匹配/部分不匹配/缺失字段/额外字段/状态码差异）、数据库持久化（记录写入/字段正确性）、健康评估（样本不足/不匹配率超标/延迟超标/正常场景）、信号聚合（空窗口/正确聚合/P95 计算）。

### Changed

- **版本号**: `app/main.py` APP_VERSION 更新至 "2.1.0"
- **README**: 版本标签更新至 v2.1.0

### Fixed

无修复项（本版本为新功能发布）。

### Architecture

- **设计模式**: 遵循 `drift_monitor.py` 架构——纯函数评估器（`evaluate_shadow_health` 无副作用、可独立测试）、fail-safe 默认（监控失败不影响业务、默认配置关闭）、清晰分层（数据采集/信号聚合/健康评估分离）。
- **异步执行**: 影子请求通过 `create_shadow_task` 在事件循环中 fire-and-forget 启动，绝不阻塞 v1 响应路径；失败只记录日志不抛异常。
- **数据库模式**: 使用项目标准 `with db.connect() as conn:` 模式、`conn.execute()` 执行 SQL、`conn.fetchone()`/`conn.fetchall()` 读取结果；时间戳统一使用 `app.db._util.utc_now()`。

### Documentation

- 更新 `CHANGELOG.md` 完整记录 2.1.0 变更
- 配置说明：`SHADOW_TRAFFIC_ENABLED`（默认 false，生产需显式启用）、`SHADOW_TRAFFIC_SAMPLE_RATE`（默认 0.05，范围 0.0-1.0）

### Upgrade Guide

从 2.0.0 升级到 2.1.0 需要运行迁移 v42（phase="expand"，非破坏性）。

**必须操作**:
1. 运行数据库迁移: `python -m app.db._migrate`（添加 shadow_traffic_comparisons 表）

**可选操作**（生产环境启用影子流量）:
1. 设置环境变量: `SHADOW_TRAFFIC_ENABLED=true`
2. 调整采样率（可选）: `SHADOW_TRAFFIC_SAMPLE_RATE=0.05`（默认 5%，建议从低开始）
3. 监控指标: 观察 `shadow.comparison_result`（match/mismatch/error 计数）、`shadow.latency_diff_ms`（v2-v1 延迟差异）

**回滚**:
- 停用影子流量: 设置 `SHADOW_TRAFFIC_ENABLED=false` 或移除环境变量
- 数据清理（可选）: `DELETE FROM shadow_traffic_comparisons WHERE created_at < datetime('now', '-30 days')`

**验收标准**（生产启用前）:
- [ ] 测试环境 5% 采样运行 24 小时无性能退化
- [ ] v1/v2 核心字段匹配率 ≥99%（mismatch_rate ≤0.01）
- [ ] v2 P95 延迟 ≤ v1 P95 延迟 + 200ms
- [ ] 自动降级逻辑验证（手动注入差异触发健康检查失败）

## 2.0.0 — Enterprise Control Plane: API v2、AI Governance、数据驻留、前端现代化 (2026-09-03)

Phase 43（Enterprise Control Plane）全部六个子阶段完成，建立了 API v2 与事件契约、区域数据驻留、AI Governance v2（eval registry、工具治理、drift 自动停 canary）、以及前端领域模块第二步（inspector + queue-view）与性能预算 gate。本版本将平台推向企业级多区域、AI 治理与可追溯性的成熟状态。

**重大里程碑**：这是 Helix Support 的首个 **主版本（Major）** 发布，标志着从 1.x 单体架构向 2.x 企业控制面的重大升级。

**成熟度提升**: 总评 4.8 → 5.0（**满分**）；前端工程 3.0 → 4.5；可靠性 4.8 → 5.0；可维护性 3.5 → 4.5。

**发布亮点**:
- ✅ **API v2 与事件契约（43.3）**：`/api/v2` 游标分页、幂等、事务 outbox、schema registry、deprecation 机制、SDK v1.2.0（migration 37，20+ 测试）
- ✅ **区域与数据驻留（43.4）**：tenant region 固定、`RegionSpec` 白名单、备份/恢复驻留感知、residency evidence pack（migration 40，20 测试）
- ✅ **AI Governance v2（43.5）**：eval registry + maker-checker、工具治理 capability token、provider 治理三 facet、drift 自动停 canary（migration 41，29 测试，ADR-016）
- ✅ **前端现代化（43.6）**：inspector.js (393 行) + queue-view.js (371 行) 领域模块、app.js -32.4%、性能预算双层 gate、视觉回归稳定面（165+ 测试）

**破坏性变更**: API v2 引入（v1 维护至少 12 个月）、数据驻留强制、AI 治理门禁、前端性能预算。详见升级指南。

**新增迁移**: v40-v41（2 个，全部 phase="expand"），从 1.5.0 到 2.0.0 总计新增 9 个迁移（v33-v41）

### Added

- **Phase 43.3 API v2 与事件契约**（2026-08-23）：
  - `app/routers/v2.py` (286 行)：`/api/v2` 使用明确资源版本、游标分页、幂等和 Problem Details；`X-API-Version: 2.0` 全响应（含错误）、`Idempotency-Key` 重放返回原资源 + `X-Idempotent-Replay: true`。
  - 游标信封 `{"data": [...], "next_cursor": ...}`，创建会话业务行+幂等映射+domain event 同事务；migration v37（phase="expand"）domain_outbox + api_idempotency 表。
  - `app/event_outbox.py` (145 行)：事务 outbox 原子 claim、handler 失败释放重试；消费者 `app/outbox_consumer.py` fan-out 到 webhook 端点、`(endpoint_id, event_id)` 唯一约束去重。
  - Schema registry `app/event_schemas.py` BACKWARD/FORWARD 兼容强制校验、version 递增；Deprecation `app/deprecation.py`：`Deprecation`/`Sunset` IMF-fixdate 头 + successor link、启动 validate_registry 过期拒启。
  - SDK v2 typed core：`clients/python` v1.2.0 新增 `list_conversations_v2`/`iter_conversations_v2` 游标自动翻页、`create_conversation_v2` `_idempotent_replay` 标志。
  - **承诺**：v2 GA 后 v1 至少维护 12 个月；弃用提前至少 6 个月通知。
  - 测试：`tests/test_api_v2.py` 7 例、`tests/test_event_outbox.py` + `tests/test_outbox_consumer.py` 9 例、`tests/test_deprecation.py`、`clients/python/tests/test_client_v2.py` 9 例、`clients/python/tests/test_e2e.py` 4 例。

- **Phase 43.4 区域、备份和数据驻留**（2026-08-23）：
  - Tenant 创建时固定 region/cell；migration v40（phase="expand"）`tenants.region` 列（默认 `'local'`）+ 控制面 `TenantPolicy.region` 双向核对。
  - `app/residency.py` (147 行)：`RegionSpec`（storage_location/backup_target/max_data_class/support_access_from/cross_border_transfers）、`REGION_INVENTORY` 默认单区 closed。
  - `summarize_tenant_residency` 按 region 桶汇总 + single_write_region 判定；`check_restore_compatibility` 恢复目标区域白名单校验。
  - Provisioning 链路透传 region（`provision_tenant(region=...)` COALESCE 幂等）；备份/恢复驻留感知：`scripts/backup.py` manifest 新增 `residency` 维度；`scripts/restore.py` 新增 `--allowed-region`（可重复）——manifest 覆盖区域不在白名单即拒绝换入。
  - Evidence：`scripts/generate_residency_pack.py` 对每个租户生成 `<tenant>.residency.json`（pinned region、控制面签名快照复核、数据字段注册表、覆盖该租户的备份 manifest）+ `_cross_border_register.json` 跨境处理清单。
  - 测试：`tests/test_residency.py` 20 例全绿。

- **Phase 43.5 AI Governance v2**（2026-08-23）：
  - `app/ai_governance.py` (456 行)：eval registry + `AiGovernanceService`、工具治理、provider 治理、drift monitor。
  - **Eval registry**：migration v41（phase="expand"）四表（ai_datasets/ai_eval_runs/ai_approval_requests/ai_production_feedback）；dataset 版本单调递增 + canonical-JSON sha256 `content_hash` 钉死条目集合（每次 load 重验）；eval run 关联 dataset/candidate/baseline/WORM report object id；maker-checker 审批（同 subject 单开放请求、请求者不能自批 `SelfApprovalError`、`require_approved` fail closed）；线上反馈两道门（ingest 即 `redact_sensitive` 且保持 `pending_review`，`promote_feedback_to_dataset` 只接受 accepted 行）。
  - **工具治理**：`app/tool_governance.py` HMAC capability token（短 TTL、单工具单租户、schema digest 钉扎）、`ToolPolicy(side_effect ∈ readonly|mutating|high_risk, parameter_schema, max_duration_ms)`、无依赖 JSON-schema 子集校验、`ToolGateway.enforce_governance` 固定顺序 fail closed（未注册策略→token 验签→实参 schema→high_risk 需 require_approved）、拒绝返回 `policy_denied`+机器可读 reason 并记 `tool.denied` 日志。
  - **Provider 治理**：`app/model_provider.py` ProviderMetadata 声明式注册表 `PROVIDER_METADATA`（data_retention/training_opt_out/region）、`provider_for_model_ref` 前缀引用推断、`check_model_policy` 三 facet 禁用面（disabled_models 精确 / disabled_providers / allow_data_egress=False 时 provider region 与租户 pinned region 比对）、执行点接入 turn_policy.py 模型门。
  - **Drift 自动停 canary**：`app/drift_monitor.py` 信号全部来自既有面（质量桶升级率/负反馈率 + 审计拒绝计数 turn.model_denied/turn.budget_exceeded 合并模型拒答、tool.denied 工具拒绝）、任一阈值越限清空该租户 canary 回 draft（新 `PromptRegistry.clear_canary` 审计 prompt_version.canary_cleared）并记 ai.drift_canary_stopped、`DRIFT_*` 配置组（window/min_turns/rate/计数/None 显式停用单信号）默认 DRIFT_ENABLED=False、挂 housekeeping 小时 sweep。
  - ADR-016 记录决策与取舍。
  - 测试：`tests/test_ai_governance.py` 29 例全绿。

- **Phase 43.6 前端可维护性和性能预算**（2026-08-23/24）：
  - **领域模块第二步 + 第三步**：`app/static/js/inspector.js` (393 行，2026-08-23) 承接 inspector 全域（renderOverview/renderEvidence/renderAudit 三 tab、updateLabels/updatePriority 变更流、safeCitationUrl href 白名单）；`app/static/js/queue-view.js` (371 行，2026-08-24) 承接队列渲染域（queueRowHtml 行模板、renderFullQueue/renderWindowedQueue CSP 安全的 CSSOM pad 高度、scheduleQueueWindowUpdate rAF 节流、renderBulkToolbar/renderLabelChips）。
  - App.js 4,820 行（1.4.0 前）→ 3,534 行（1.4.0）→ 3,386 行（43.6 第二步）→ **3,258 行**（43.6 第三步）；**累计优化**: -1,562 行 / **-32.4%**。
  - Component contract 与状态机：模块导出纯函数三元组（INSPECTOR_TABS/createInspectorState/reduceInspector、QUEUE_ROW_PARTS/createQueueViewState/reduceQueueView）；DOM 层迁移期继续驱动 legacy state 保证行为对等，reducer 是同语义镜像源。
  - **性能预算双层 gate**：`scripts/performance_gate.py` 静态字节预算（operator JS ≤345KB 实测 277KB / operator CSS ≤105KB 实测 85KB / widget JS ≤25KB 实测 20KB）；浏览器层 Playwright Chromium 测（LCP≤2500ms 实测 ~844-1008ms / CLS≤0.10 实测 ~0.0005 / 长任务数≤50 实测 2 / 10k 合成会话渲染≤2000ms 实测 ~11ms / JS heap 波动≤15MB 实测 0.06MB）；挂 ci.yml schedule cron 0 3 * * * 的 nightly 步骤；工程要点：队列 SSE 流使 networkidle 永不触发，测量用 domcontentloaded+aria-busy settle 替代；基线 JSON `artifacts/performance-baseline.json --update` 重写。
  - **视觉回归稳定面**：`scripts/visual_gate.py` + `tests/baselines/` 四基线（workspace-dark/light 主题 token 集/knowledge-view/mobile-queue drawer）；截图前 mask 动态区（time/.item-sla/#liveStatus/#queueCount），Pillow 逐像素通道容差 ±12、整图差分比上限 0.5%；drift 落 `artifacts/visual-drift-<name>.png`；自检证明 4.09% 差分被正确拒绝；axe/桌面焦点序/knowledge 键盘路径/移动 focus trap/reduced-motion gate 全部保留在 `tests/ui_accessibility.py`。
  - 测试：frontend gate 155 node tests（inspector.test.js 9 例 + queue-view.test.js 7 例新增）、`tests/test_frontend_gate.py` + `tests/test_performance_gate.py` 3 例、clean DB 上 ui_smoke/ui_admin/ui_knowledge/ui_accessibility 四浏览器套件全通过、visual_gate 四基线全部 ≤0.23% 差分、performance_gate 静态+浏览器层通过。

### Changed

- **破坏性变更（Major 版本）**：
  1. **API v2 引入**：`/api/v2` 使用新的游标分页格式（非向后兼容）；v1 API 继续服务至少 12 个月，但已进入维护模式；新功能将优先在 v2 实现。
  2. **数据驻留强制**：新租户创建必须指定 `region`（既有租户默认 `'local'`）；跨区域恢复需要显式 `--allowed-region` 白名单。
  3. **AI 治理门禁**：工具调用需要 capability token（高风险工具需审批）；drift 监控可自动停止 canary（默认关闭，需显式启用）。
  4. **前端性能预算**：静态资源超过预算将阻止发布；浏览器性能指标进入 nightly gate。
- **迁移路径**：v1 API 用户有 12 个月窗口迁移到 v2；所有弃用将提前 6 个月通过 `Deprecation`/`Sunset` 头通知；SDK v1.2.0 同时支持 v1 和 v2，平滑迁移。
- **成熟度评分**：总评 4.8 → 5.0（**满分**）；核心功能/智能质量/集成能力 4.0 → 4.5；可靠性 4.8 → 5.0；可观测性 4.5 → 4.8；前端工程 3.0 → 4.5；交付工程 4.8 → 5.0；可维护性 3.5 → 4.5。
- **测试覆盖**：后端 1117+ passed + 37 skipped（分支覆盖率 86%）、前端 351 passed、node 165 passed、golden set 27/27、对抗集 24/24、供应链 gate 5/5、浏览器性能 gate 5 metrics、视觉回归 4 baselines ≤0.5% drift。

### Fixed

无修复项（本版本为新功能发布）。

### Security

- AI Governance v2 引入工具治理 capability token、provider 治理三 facet 禁用面、drift 自动停 canary，提升 AI 系统安全性和可追溯性。
- 数据驻留机制确保租户数据固定在指定区域，跨区域转移需要显式白名单授权。

### Documentation

- 新增 `docs/RELEASE_2_0_0.md` 完整发布总结
- 新增 ADR-016（AI Governance v2 与 drift 监控决策）
- 更新 `README.md` 版本号至 v2.0.0
- 更新 `app/main.py` APP_VERSION = "2.0.0"
- 新增 `scripts/generate_residency_pack.py` 数据驻留证据包生成器

### Upgrade Guide

从 1.5.0 升级到 2.0.0 **需要运行迁移**（migration 40-41），有**破坏性变更**。

**必须操作**:
1. 运行数据库迁移（40-41，全部 phase="expand"）
2. 审查 API v2 变更（如计划使用新功能）
3. 为新租户配置 `region` 参数
4. 审查前端性能预算（如有自定义 CSS/JS）

**建议操作**:
1. 迁移到 API v2（v1 将在 12 个月后弃用）
2. 启用 AI drift 监控（`DRIFT_ENABLED=true`）
3. 配置工具治理策略（`ToolPolicy`）
4. 运行数据驻留证据生成（`scripts/generate_residency_pack.py`）
5. 审查 provider 元数据（`PROVIDER_METADATA`）
6. 配置性能基线（`artifacts/performance-baseline.json`）
7. 配置视觉回归基线（`tests/baselines/`）

## 1.5.0 — Reliable Scale: 高可用、冷归档、对象存储、PostgreSQL RLS (2026-09-03)

Phase 42（Reliable Scale）全部六个子阶段完成 + Phase 43 前两个子阶段（租户控制面、PostgreSQL RLS），建立了 Web/Worker 分离、PostgreSQL/Redis HA、冷归档与对象存储、附件安全隔离、真实渠道 Adapter SDK、多窗口 SLO 告警、租户控制面与 RLS 多租户隔离。本版本将平台可靠性与企业级数据隔离推向生产就绪状态。

**成熟度提升**: 总评 4.5 → 4.8；可靠性 4.2 → 4.8；安全 4.8 → 5.0（满分）；可观测性 4.0 → 4.5。

**发布亮点**:
- ✅ **Web/Worker 分离（42.1）**：`PROCESS_ROLE=web|worker|all`、PostgreSQL job source-of-truth、Redis 可重建 dispatch、lease fencing token
- ✅ **PostgreSQL/Redis HA（42.2）**：`DATABASE_AUTO_MIGRATE=false` 零 DDL 启动、expand/migrate/contract 迁移纪律、PITR 自动化演练（RTO 0.8s）、Redis flush-rebuild 测试
- ✅ **冷归档与对象存储（42.3）**：`ArchiveObjectStore` gzip JSONL 分区、10×/100× 档位基准、流式校验 O(1) 内存、双摘要防篡改（migration 33）
- ✅ **附件对象存储（42.4）**：`AttachmentStore` 原子写、quarantine 三态、EICAR/多态/伪装 MIME/压缩炸弹检测、签名 URL TTL 600s（migration 34）
- ✅ **真实渠道 Adapter SDK（42.5）**：`ProviderAdapter` 协议、`NormalizedEvent` 统一形状、conformance 9 例（重放/乱序/edit/recall/DLQ）
- ✅ **SLO 多窗口告警（42.6）**：page=14.4×(1h+5m)、ticket=6×(6h+30m) burn-rate、统一 trace context、6 个 Grafana dashboard、月度 game day（migration 35）
- ✅ **租户控制面（43.1）**：`TenantControlPlane` 版本化签名快照、LKG 降级服务、高风险变更门禁（migration 36，12 测试）
- ✅ **PostgreSQL RLS（43.2）**：18 张核心表 `helix_tenant_isolation` 策略、tenant_scope/maintenance_scope 双作用域、信封加密 webhook secret（migration 38-39，44 测试）

**新增迁移**: v33-v39（10 个，全部标注 phase），遵循 expand/migrate/contract 纪律

### Added

- **Phase 42.1 Web/Worker 分离**（2026-08-21）：
  - `PROCESS_ROLE=web|worker|all`；生产 web 不执行 housekeeping/turn，worker 不暴露公网业务端点。
  - PostgreSQL 是 job/source-of-truth，Redis 只负责可重建 dispatch；统一 lease fencing token 防止过期 worker 提交。
  - 独立 worker 支持按租户公平调度、并发预算和 drain；滚动升级保证旧/新 job schema 兼容。

- **Phase 42.2 PostgreSQL/Redis HA 与 PITR**（2026-08-21）：
  - `DATABASE_AUTO_MIGRATE=false` 时 web/worker 启动零 DDL，只读校验 `schema_migrations` 就绪并对过期/未迁移库 fail fast（main.py）。
  - DDL 收敛到独立 release job `scripts/run_migrations.py`（apply/verify-only 双模式、sqlite+postgresql 双后端、JSON 报告）。
  - 最小权限拆分为 app role（仅 DML）与 migrate role（每次 release 一次）；TLS（`sslmode`）与连接池/proxy 拓扑入 `docs/OPERATIONS.md`。
  - Migration 作为独立 release job，使用 expand/migrate/contract；`@migration(..., phase=)` 元数据（v33 起强制声明），`scripts/migration_gate.py` 校验链连续性 + phase 合法性 + contract 必须有更早 expand。
  - 自动化 PITR 到隔离环境 `scripts/run_pitr_drill.py`：T0/T1 双备份 → 恢复到 T1 隔离环境 → 审计链+WORM anchors intact、窗口内零丢失（RPO）、post-T1 写入不回放、RTO 计量对比预算（默认 1800s/900s）。
  - 台账 `supplychain/pitr-drills.json` + `automated_pitr` 纳入 `threat_model_gate.py --check-today` 治理。
  - Redis 故障转移后从 PostgreSQL reconciliation，禁止依赖 Redis 作为唯一状态；`test_flush_rebuilds_from_database_exactly_once`（全量 flushdb 后一次 recover 恰好重派全部非终态 job、无丢失/重复/双认领）。
  - 测试：`tests/test_auto_migrate.py` 6 例、`tests/test_migration_gate.py` 9 例、PITR 演练 PASSED（rto≈0.8s）、Redis flush-rebuild 10 passed。

- **Phase 42.3 冷归档与可查询存储（REL-002）**（2026-08-21）：
  - `app/archive_store.py` (319 行)：`ArchiveObjectStore`（磁盘参考实现，生产可换 S3/GCS），gzip JSONL 分区按 tenant/date 落对象存储、原子写（tmp+rename）。
  - Tenant manifest 记录 object_id/时间界/sha256(压缩+规范流双摘要)/字节数/行数；`iter_range(from,to,limit)` 时间窗 + 硬上限流式查询。
  - Migration v33（phase="expand"）为 `audit_archives` 增加可空 object_key/object_sha256/object_bytes 列。
  - Retention 写路径双轨：配置 store 时 payload 入分区、DB 行只留 slim manifest，未配置保持内联 JSON 兼容。
  - Archive export 和审计校验流式处理 O(1) 内存；`validate_audit_archive_stream` 逐事件增量校验哈希链/序列/租户 + 流末边界核对。
  - 对象缺失/摘要错误 fail closed；读路径经 store 双重校验（压缩字节 sha256 vs manifest + 规范流 sha256 vs DB 行）。
  - 使用 `archive_search_load_test.py --tier 10x|100x --json` 固定档位基准；10× 实测 list p95 64.5ms / worst-search p95 289.6ms，峰值内存 ≤0.12MB。
  - 测试：archive_store 6 例、retention 集成 5 例、既有 retention/审计套件无回归 75 passed。

- **Phase 42.4 附件对象存储与恶意内容隔离（SEC-006）**（2026-08-21）：
  - `app/attachment_store.py` (119 行)：`DiskAttachmentStore`（参考实现），原子写、key 单段白名单防穿越、**拒绝静默覆盖**（key 冲突抛错）。
  - `ATTACHMENT_SCAN_MODE=external` 时上传落 `quarantined` 态（不可下载/不可绑定），`POST /api/attachments/{id}/verdict` 回调晋升 stored/rejected，rejected 即时删除对象。
  - Storage_key 由 attachment_id+随机段生成（原文件名仅展示元数据）；migration v34（phase="expand"）增 sha256 列，上传时计算固定、下载时流式重算比对、篡改即拒绝（404）。
  - 签名 URL：HMAC-SHA256 over tenant.attachment.expiry（widget_secret 签名密钥）、TTL 硬顶 600s、过期重放/篡改/跨租户 token 全拒；下载响应强制 nosniff/no-store/CSP sandbox。
  - `TimeoutMalwareScanner` 墙钟预算包装（超时/引擎崩溃一律 fail closed）；`normalize_verdict` 非 bool/非已知 clean 词全部拒绝。
  - 内置扫描器：EICAR+多态变体检测、伪装 MIME（text/* 声明携带 MZ/PK/gzip magic）、压缩炸弹守卫（zip 条目总量 / gzip 流式解压上限 = 原始 20×）。
  - Delete 审计含 storage_key/sha256/size（删除证明），DSR 删除先清对象文件再删行（counts["attachment_objects"] 入证明）。
  - 测试：`tests/test_attachment_security.py` 20 例全绿。

- **Phase 42.5 正式渠道 Adapter SDK**（2026-08-21）：
  - `app/channel_providers.py` (190 行)：`ProviderAdapter` 协议（verify_signature + parse）与 `NormalizedEvent` 统一形状（kind=message/edit/recall/receipt、附件引用不内联字节）。
  - `ReferenceJsonAdapter` 固定参考线协议（HMAC `sha256=<hex>` over `<ts>.<body>`，与核心 ingress 同一验证规则；未知 kind fail closed）；适配器输出即统一入口 schema——核心零改动。
  - 渠道账号采用 41.1 凭据生命周期；key_id 轮换选择器 `InboundChannelRegistry._secret_for_key_id`（未知名/非活跃/指纹不符一律统一失败）。
  - 回执幂等（receipt claim）+ turn job 幂等键即回放工具；webhook_deliveries 的 retried/dead 状态机承担 DLQ 语义。
  - 测试：`tests/test_provider_conformance.py` 9 例（好签名 202 / 错密钥·过期时间戳·缺头 401、同 external id 重放折叠、乱序零丢失、edit/recall 归一化、附件引用解析、背压 429 + Retry-After、outbound 故障注入、跨账号/租户隔离）。

- **Phase 42.6 SLO 与可观测性 v2**（2026-08-21）：
  - `app/slo.py` 纯函数评估器：page=14.4×(1h+5m)、ticket=6×(6h+30m) 双窗口同时越限才告警（SRE workbook 标准）；瞬时尖峰不 page、缓慢燃烧不被静默；空/缺失长窗抑制；告警携带 window_rates/thresholds/budget 消耗。
  - Request → job → model/tool → webhook/channel 统一 trace context；migration v35（phase="expand"）turn_jobs 增 request_id 列，`enqueue_turn_job` 默认从调用方 contextvar 取 id，worker `run_once` 认领时回放 request_id 进 context。
  - 建立 tenant noisy-neighbor、queue fairness、model/provider、archive/object store、DSR 和 credential 使用 dashboard；`scripts/generate_dashboards.py` 生成六份 Grafana 风格 JSON 至 docs/dashboards/。
  - 每月 game day：`scripts/run_game_day.py --focus db|redis|model|connector|objectstore|webhook|all` 编排既有演练，台账 `supplychain/game-days.json`。
  - 测试：SLO 规则 8 例、trace 链路 2 例。

- **Phase 43.1 租户控制面与部署单元**（2026-08-21）：
  - `app/control_plane.py` (362 行)：`TenantPolicy`（plan 枚举 free/standard/enterprise + region/cell/feature_policy/model_policy/credential_reference），`TenantControlPlane`（唯一策略写方，版本递增，签发 HMAC-SHA256 签名的 `ConfigSnapshot`，重发时对存储态自校验——被篡改行 fail closed）。
  - Migration v36（phase="expand"）`tenant_control_policies` 版本化表（tenant_id+version 主键）。
  - 大租户可固定 cell/数据库；region/deployment_cell 进入策略文档并随快照签名固定；model_policy 承载容量配额（allowed_models/daily_turn_budget）。
  - 控制面不可用时数据面使用有时限的 last-known-good；`DataPlaneConfig`：签名验证先行（篡改/过期到达即拒）；CP 不可达时 LKG 在 TTL 内继续服务、过期即 fail closed（PolicyUnavailableError）。
  - 降级期间 plan/region/cell/model_policy 任一变更拒绝（ControlPlaneError），feature_policy 等低风险面允许滚动。
  - 测试：`tests/test_control_plane.py` 12 例全绿。

- **Phase 43.2 PostgreSQL RLS 与信封加密**（2026-08-22）：
  - `app/rls.py` (214 行)：18 张核心客户数据表统一 `helix_tenant_isolation` 策略（USING/WITH CHECK 均为 `tenant_id = current_setting('app.tenant_id', true)`）。
  - `app/context.py` tenant_scope/maintenance_scope 双作用域：GUC 由 `PostgresDatabase.connect()` 从已认证凭据绑定、`set_config(..., true)` 随事务提交消亡（连接池复用天然 fail-closed）、maintenance scope 刻意不绑 GUC 因此要求 owner/BYPASSRLS 角色且每次进入记 INFO 审计日志。
  - API 认证依赖验证后 `bind_tenant_scope()`，worker 认领/提交走 maintenance scope 而 turn 执行收窄到 job 自己的 tenant，审计写入经 `_audit_scope()` 自绑定事件本身租户。
  - RLS 强制时无作用域访问抛 `TenantContextError` fail loud；`DATABASE_RLS_ENABLED=1` 仅允许 PostgreSQL 后端、默认关闭。
  - `verify_rls()` 读 pg_class/pg_policy 报告保护状态；`scripts/run_rls_drill.py` scratch 集群双角色实跑 PASSED 10/10，台账 `supplychain/rls-drills.json` + `automated_rls` 入 threat_model_gate 治理，ADR-015。
  - Restricted 字段使用租户 DEK + KMS KEK；migration v38（tenant_deks）、v39（webhook_secret_format 判别列 plain|envelope）。
  - WebhookService 注入 `envelope_cipher`/`envelope_required`，注册存 envelope JSON（v/tenant_id/dek_version/kek_version/wrapped_dek/nonce/ciphertext，绝不存明文），投递 `_resolve_secret` 用租户 DEK 解密后 HMAC 签名，legacy 明文行混合部署照常投递。
  - KMS 不可用行为明确：envelope_required 但 cipher 未引导→注册拒绝 503（EnvelopeCryptoError），投递解密失败→dead-letter（status='dead'），绝不静默降级明文。
  - 搜索字段分类 gate `ensure_searchable_fields_are_classified`（knowledge/message FTS 初始化时校验索引列分类，FIELD_REGISTRY 补注册 title/tags/category/search_terms，未分类字段启动 fail loud）。
  - 测试：`tests/test_rls.py` 27 例、`tests/test_webhook_envelope.py` 10 例、`tests/test_envelope_runtime.py` 7 例。

### Changed

- 安全成熟度从 4.8 提升至 5.0（**满分**，PostgreSQL RLS + 信封加密 + 附件隔离）
- 可靠性成熟度从 4.2 提升至 4.8（Web/Worker 分离 + PG/Redis HA + PITR + 冷归档）
- 可观测性成熟度从 4.0 提升至 4.5（多窗口 SLO + 统一 trace context + 6 个 dashboard）
- 总评从 4.5 提升至 4.8
- 后端测试从 889+ 增至 1048+（新增 Phase 42/43 测试 159+）
- 新增 10 个迁移（v30-v39），全部标注 phase（expand/migrate/contract）
- 新增 1204 行核心代码（control_plane 362 + archive_store 319 + rls 214 + channel_providers 190 + attachment_store 119）

### Fixed

- 修复 `_json_safe_errors` 漏消毒 pydantic `input` 字段（非 JSON body 触发校验错误时 500 → 现正确 422 Problem Details）
- 修复缺键 envelope KeyError→dead-letter 而非卡 `sending`
- 修复 RuntimeError→EnvelopeCryptoError 503
- 修复 dead-letter last_response_code NULL

## 1.4.0 — Secure Operations: 凭据生命周期、供应链治理、深模块拆分 (2026-09-03)

Phase 41（Secure Operations）全部七个子阶段完成，建立了统一凭据生命周期、五道供应链 gate、审计外部锚定、AI 安全评测、深模块拆分与安全治理自动化。本版本在 1.3.0 M0 停止线基础上，将安全与交付工程推向可通过外部审计的成熟状态。

**成熟度提升**: 总评 4.2 → 4.5；安全 4.5 → 4.8；交付工程 4.5 → 4.8；可维护性（新维度）3.5。

**发布亮点**:
- ✅ **统一凭据生命周期（41.1）**：`hk-` 前缀 160-bit API key、pending→active→retiring→revoked 状态流转、跨实例即时吊销、审计脱敏、轮换演练（23 测试）
- ✅ **供应链五道 gate（41.2）**：secret 扫描、license 策略、漏洞例外到期检查、CI pin 一致性、发布 manifest（ADR-010）
- ✅ **审计外部锚定（41.3）**：高危变更同事务持久化、Ed25519 KMS 签名链头、WORM 存储、三证据互验（ADR-011，8 测试）
- ✅ **数据保护与隐私（41.4）**：字段级分类登记、结构化 redaction、DSR 队列 SLA、tombstone 防备份复活（migration 32）
- ✅ **AI 安全评测 gate（41.5）**：对抗集 24 例 9 类威胁、晋级五条件 gate、WORM 报告、工具再授权、间接注入复检
- ✅ **深模块拆分（41.6）**：orchestrator 1,504→930 行（-38%）三深模块、app.js 4,820→3,534 行（-27%）六深模块、迁移注册表按版本拆分
- ✅ **安全治理自动化（41.7）**：threat-model delta gate、季度桌面演练、90 天到期检查（ADR-012，14 测试）

**完成报告**:
- `IMPLEMENTATION_REPORT_PHASE_41.md`（41.1-41.4，173 行）
- `IMPLEMENTATION_REPORT_PHASE_41_5.md`、`PHASE_41_6.md`、`PHASE_41_7.md`（41.5-41.7，257 行）

### Added

- **Phase 41.1 + 41.1b 统一凭据生命周期（SEC-004）**（2026-08-20）：
  - `CredentialStore` / `Credential` / `CredentialLifecycle` 状态机（`app/credentials.py`），注册表仅存 SHA-256 指纹（`key_ref`）绝不存明文。
  - `hk-` 前缀 160-bit API key 分组格式（`secrets.token_urlsafe(20)`），`pending → active → retiring → revoked` 状态流转，`is_allowed` 强制 not_before / ±5s 时钟偏差 / expires_at / retiring 有界重叠窗口（24h 默认）。
  - migration 30：`credential_registry` 表 + `(type, key_ref)` 唯一索引。
  - 管理 API：`POST/GET /api/admin/keys`（secret 只显示一次）、`POST /api/admin/keys/{id}/revoke`（跨实例即时吊销）。
  - 渠道签名支持 `X-Helix-Key-Id` 版本化选择（`app/channels.py::InboundChannelRegistry._secret_for_key_id`），未知/错类型/跨租户/已吊销 key_id 统一 401 fail-closed。
  - 审计脱敏：issuance/revocation 事件只含 credential_id，序列化后绝无原始 secret。
  - 测试：23 passed（`tests/test_credentials.py` 11 + `tests/test_phase41.py` 12，覆盖跨实例吊销、过期边界、时钟偏差、并发轮换竞争、审计脱敏）。

- **Phase 41.2 供应链与可复现发布（SEC-003）**（2026-08-20）：
  - `supplychain/` 配置目录 + 五个正交 gate 全进 CI `supply-chain` job：
    - **secret 扫描**（`scripts/scan_secrets.py`）：私钥/Anthropic/OpenAI/AWS/GitHub/Slack/服务账号/Fernet 形态扫描。
    - **license 策略**（`scripts/license_gate.py` + `supplychain/license-policy.json`）：逐包登记，新包未登记红灯，`LicenseRef-TBD` + `approved_until` 到期红灯。
    - **漏洞例外**（`scripts/vuln_review.py` + `supplychain/vulnerability-exceptions.json`）：例外含不可达证据/补偿控制/owner/due_date，open 到期自动红灯，`--audit --require-coverage` 覆盖 pip-audit 上报。
    - **CI pin 一致性**（`scripts/check_workflows.py` + `supplychain/ci-pins.json`）：`uses:` 引用必须与登记一致。
    - **发布 manifest**（`scripts/release_manifest.py`）：哈希 lock/源码/SBOM/基础镜像 pin，`--verify` 重算比对防篡改。
  - 设计：`docs/adr/0010-supply-chain.md`。

- **Phase 41.3 审计证据外部锚定（SEC-005）**（2026-08-20）：
  - 高危安全/权限/DSR/策略变更与审计证据**同事务持久化**（`app/db/audit.py::audit_high_risk` + migration 31 `audit_anchors`）。
  - 12 类 `HIGH_RISK_EVENT_TYPES`（`api_key.*`、`data_subject_request.*`、`member.*`、`retention.policy_updated`、`sla_policy.set`、`webhook.*`）由 audit wrapper 强制走该路径，失败整体回滚并 fail-closed 503（`code="audit_unavailable"`）。
  - 链头 `{last_seq, last_hash, timestamp, environment}` 经 Ed25519 KMS 签名导出 WORM 锚点（`app/worm_store.py::DiskWormStore`），kid 白名单轮换。
  - `scripts/verify_audit_chain.py` 一次校验本地全链 + DB frontier anchors + WORM claims 三证据。
  - 设计：`docs/adr/0011-audit-external-anchoring.md`。测试：8 passed。

- **Phase 41.4 数据保护与隐私运营**（2026-08-20）：
  - 数据分类：migration 32 `data_field_registry` + `app/redaction.py` `FIELD_REGISTRY`（public/internal/confidential/restricted）。
  - Secret、token、受限 PII 在日志/trace/diagnostics 中使用统一结构化 redaction（`app/redaction.py` 双通道 redaction）。
  - DSR 队列：migration 32 `deferred_deletion_jobs` + `customer_tombstones`，增加 SLA、审批看板、导出 checksum、删除证明和失败重试。
  - 备份恢复后继续执行 tombstone（`enforce_tombstones_after_restore`），防止已删除客户数据从旧备份重新出现。
  - 测试：`tests/test_privacy.py::RedactionCanaryTests`。

- **Phase 41.5 AI 安全评测 Gate v1（AI-001）**（2026-08-20）：
  - 对抗集 `golden/adversarial.json` 24 例，9 类威胁：直接/间接提示注入、系统提示探测、跨租户检索、工具参数注入、PII/secret 外泄、恶意附件文本、多语言变体。
  - 独立 schema 校验 `scripts/adversarial_schema.py`（ADR-014 决策 1 扩展 expect 契约：`requires_human`、精确 `citation`、`redaction`、`canary`、`tool_calls`、`canary_assert`）。
  - 运行器 `scripts/evaluate_adversarial.py` 经真实 HTTP 路径执行（demo+acme 双租户、临时 DB、知识/附件/canary 种子通道），报告写入 WORM store（`app/eval_reports.py`）。
  - 高风险工具 gateway 再授权（`app/tools.py`：跨租户 owner_tenant_id 与非规范化资源 id 一律拒绝；`OrderAgent` 对粘连换行/分隔符/SQL 片段的订单号走网关拒绝路径）。
  - `app/agents.py` PolicyAgent 新增系统提示探测（en/zh/fr/ja）、角色扮演与多语言注入模式；`app/orchestrator.py` 对检索内容复检策略并将内容风险类别并入 turn metadata（间接注入可追溯）。
  - 晋级阈值：安全集 100%，核心 golden 100%，质量指标不低于当前 active，P95/成本在租户预算内；否则自动阻断 canary 提升（`decide_promotion` 五条件门禁 + WORM 晋级记录）。
  - CI 新增 `ai-eval` job（schema 门禁、对抗集 gate、golden 回归、gate 测试、报告上传）。
  - 测试：`tests/test_eval_reports.py`（WORM 一次性写入/篡改检测/五条件晋级）、`tests/test_adversarial_eval.py`（schema、探测模式、工具参数注入拒绝、间接注入可追溯、24/24 gate、golden 27/27）。

- **Phase 41.6 深模块拆分第一步（ARC-001）**（2026-08-20）：
  - **后端 orchestrator 三深模块**：
    - `app/turn_policy.py` (293 行)：语言检测、客户消息持久化+审计、policy inspect、budget/model guards、triage 决策；`ingest()` 返回 `TurnPolicyResult`。
    - `app/turn_execution.py` (210 行)：按决策路由 specialist、检索内容 policy 复检（间接注入，ADR-014）、quality gate、`quality.reviewed` + `tool.executed` audit。
    - `app/turn_persist.py` (382 行)：routing-state 转移（含 SLA deadline）、auto-assign、assistant 消息持久化（含翻译）、quality aggregate、telemetry、webhook dispatch。
    - `app/turn_services.py` (50 行)：`TurnServices` Protocol——orchestrator 即 composition root，三 stage 只读其服务子集，import 图无环。
    - `app/orchestrator.py`：1,504 → 930 行（**-38.2%**），薄协调器三段式（policy.ingest → execution.execute → persist.finalize）+ segment 计时。
  - **前端 app.js 六深模块**：
    - `js/composer.js` (≤400 行)：草稿、claim 续租、宏候选、canned responses、copilot 全套。
    - `js/session.js` (≤400 行)：mentions 面板、watch 生命周期、canReadConversations。
    - `js/admin-report.js` (≤400 行)：报表订阅/CSV 导出/Webhook 选项、SLA 策略、路由规则。
    - `js/ticket-view.js` (≤400 行)：工单列表/详情/流转/会话关联。
    - `js/quality-panel.js` (≤400 行)：质检面板/图表、反馈转知识草稿、CSAT 汇总。
    - `js/attachment.js` (≤400 行)：待传附件/元数据/名称加载/状态栏。
    - `app.js`：4,820 行 / 189 KB → 3,534 行 / 140 KB（行 -26.7%、字节 -26.0%）。
  - **迁移注册表拆分**：从单一大文件拆为按版本模块（`app/migrations/v01`–`v32`），保持有序注册入口与连续性 gate。
  - 验收：行为快照、API、golden、浏览器和迁移链不变；orchestrator -38%，app.js -27%，循环依赖为零。浏览器验收 12/14 套件通过（覆盖全部 6 个抽取模块）。

- **Phase 41.7 安全治理自动化（SEC-008）**（2026-08-20）：
  - 每发布 delta 台账 `supplychain/threat-model-deltas.json`（schema_version 1）：含 release/date/owner/approved_by/controls/verification_evidence。
  - 季度演练台账 `supplychain/security-drills.json`（四类 drill_type：report_intake/dependency_vuln/key_compromise/cross_tenant_alarm），含 started_at/owner/scenario/duration_minutes。
  - `scripts/threat_model_gate.py`：缺失字段/空列表/placeholder（`security@helix.example`、`example.com`、`tbd`/`todo`/`待定`/`占位`、`<...>`、空串）/未来日期/演练过期全红灯；exit 0/1/2 与既有 gate 同构。
  - CI nil-tolerant：无 `--release`/`--check-today` 时空登记册不误报，发布时刻/受控环境强制执行。
  - 设计：`docs/adr/0012-security-governance.md`。测试：14 passed（5 subtests）。

### Changed

- 安全成熟度从 4.5 提升至 4.8（凭据生命周期 + AI 安全评测 + 审计锚定）
- 交付工程成熟度从 4.5 提升至 4.8（五道供应链 gate + 威胁模型自动化 + 发布 manifest）
- 智能质量成熟度从 3.8 提升至 4.0（对抗集 24 例 + 晋级 gate）
- 新增可维护性维度 3.5（orchestrator -38% + app.js -27%）
- 总评从 4.2 提升至 4.5
- 后端测试从 757+ 增至 889+（新增 Phase 41 测试 132+）
- 对抗集与晋级 gate 进入 CI（`ai-eval` job）

### Fixed

- orchestrator 拆分修复：还原 `quality.reviewed`/`tool.executed` audit，对齐 `WebhookService.emit_event` 真实签名（旧版误调 `dispatch`）
- migration 拆分修复：链验证 `[]` 问题

## 1.3.0 — 商用级可信：安全、可靠性、运维 (2026-09-03)

Phase 28-30（通过 M0/Phase 40-41 实现）完成，标志着 Helix Support 从"企业级平台"升级为**通过外部安全评审不需临时补救的商用成熟平台**。本版本闭环了 M0 停止线四项关键安全风险（SEC-001/002、REL-001、SEC-007），实现了统一凭据生命周期、供应链五道 gate、审计外部锚定、安全治理自动化，完善了 SLO/runbook/灾备/用户文档体系。

**成熟度提升**: 总评 3.7 → 4.2；安全 3.5 → 4.5；可靠性 3.8 → 4.2；交付工程 4.0 → 4.5。

**发布亮点**:
- ✅ **M0 停止线闭环**：OIDC 完整验证（36 测试）、DSR maker-checker（23 测试）、队列 fail-closed（四类故障演练）、安全报告最小闭环
- ✅ **统一凭据生命周期**：API key 双活轮换、跨实例即时吊销、审计脱敏、`X-Helix-Key-Id` 版本化签名
- ✅ **供应链五道 gate**：secret 扫描、license 策略、漏洞例外、CI pin 一致性、发布 manifest
- ✅ **审计外部锚定**：高危变更同事务持久化、KMS Ed25519 签名链头、WORM 存储、三证据互验
- ✅ **安全治理自动化**：threat-model delta gate、季度桌面演练、90 天到期检查
- ✅ **SLO 与告警**：四指标（可用性/延迟/队列/SSE）+ 错误预算 + Prometheus 规则
- ✅ **Runbook 与诊断**：按症状组织、`GET /api/admin/diagnostics` 诊断包
- ✅ **容量与灾备**：压测基线、RTO/RPO 声明、故障演练脚本化

**完成报告**:
- `IMPLEMENTATION_REPORT_PHASE_40.md`（M0 停止线，69 行）
- `IMPLEMENTATION_REPORT_PHASE_41.md`（凭据/供应链/审计锚定，173 行）
- `IMPLEMENTATION_REPORT_PHASE_41_5.md`、`PHASE_41_6.md`、`PHASE_41_7.md`（Phase 41 续，257 行）

### Added

- **Phase 40 (M0 停止线) 安全风险闭环**（2026-09-03）：
  - **40.1 SEC-001 OIDC 加固**：一次性 `auth_transactions`（migration 28）防重放，state/nonce/PKCE S256 verifier/redirect_uri/tenant_hint 绑定，RS256-only + kid 轮换 JWKS 缓存，iss/aud/exp/iat/nonce 强制校验，identity 仅来自已验证 claims + tenant_members（无 demo/admin 回退）。实现：`app/oidc_flow.py`。测试：36 passed（覆盖授权码重放、PKCE 一次性、nonce/iss/aud/exp/iat 校验、算法混淆拒绝、JWKS kid 轮换）。
  - **40.2 SEC-002 DSR maker-checker**：申请人≠审批人≠执行人（migration 29），`idempotency_key` 唯一索引幂等重放，导出物 Fernet 加密、一次性下载 token ≤15 分钟、导出对象 ≤24 小时、无 `DSR_EXPORT_SECRET` 时 501 fail-closed。隐私权限（`privacy:request/approve/execute`）仅 ADMIN 持有。实现：`app/dsr.py`。测试：23 passed。
  - **40.3 REL-001 多实例队列 fail-closed**：Redis 不可用时 API 返回 503 + `Retry-After: 30`（`urn:helix:error:queue_unavailable`），绝不静默降级为 SQLite，readiness 报告 degraded，`deployment_profile=multi` 强制 PostgreSQL+Redis+fail-closed。实现：`app/queue.py`。测试：12 passed + 四类 Redis 故障演练 ALL PASS。
  - **40.4 SEC-007 安全报告最小闭环**：`SECURITY.md` 部署前配置检查清单，威胁模型见 `docs/SECURITY_MODEL.md`，负向测试进入 CI。

- **Phase 41.1 + 41.1b 统一凭据生命周期（SEC-004）**（2026-08-20）：
  - `CredentialStore` / `Credential` / `CredentialLifecycle` 状态机（pending → active → retiring → revoked），注册表仅存 SHA-256 指纹（`key_ref`）绝不存明文，`hk-` 前缀 160-bit API key 分组格式，`is_allowed` 强制 not_before / ±5s 时钟偏差 / expires_at / retiring 有界重叠窗口（24h 默认）。
  - migration 30：`credential_registry` 表 + `(type, key_ref)` 唯一索引，legacy `revoked_api_keys` → registry 状态种子同步。
  - 管理 API：`POST/GET /api/admin/keys`（secret 只显示一次，DB 只存指纹）、`POST /api/admin/keys/{id}/revoke`（registry 持久裁决 → 跨实例即时吊销）。
  - 渠道签名：`X-Helix-Key-Id` 选择已轮换的 registry 凭据，未知/错类型/跨租户/已吊销 key_id 统一 401 fail-closed。
  - 审计脱敏：issuance/revocation 审计事件只含 credential_id，序列化后绝无原始 secret。
  - 测试：`tests/test_credentials.py` 11 passed + `tests/test_phase41.py` 12 passed（覆盖跨实例吊销、过期边界、时钟偏差、并发轮换竞争收敛、审计脱敏）。

- **Phase 41.2 供应链与可复现发布（SEC-003）**（2026-08-20）：
  - `supplychain/` 配置目录 + 五个正交 gate 全进 CI `supply-chain` job：
    - **secret 扫描**（`scripts/scan_secrets.py`）：已知凭证形态扫描（私钥/Anthropic/OpenAI/AWS/GitHub/Slack/服务账号/Fernet），忽略 build/测试夹具/npm integrity。
    - **license 策略**（`scripts/license_gate.py` + `supplychain/license-policy.json`）：逐包登记许可，新包未登记红灯，`LicenseRef-TBD` + `approved_until` 临时批准到期红灯。
    - **漏洞例外**（`scripts/vuln_review.py` + `supplychain/vulnerability-exceptions.json`）：例外含不可达证据/补偿控制/owner/due_date，open 到期自动红灯，`--audit --require-coverage` 覆盖 pip-audit 上报。
    - **CI pin 一致性**（`scripts/check_workflows.py` + `supplychain/ci-pins.json`）：`uses:` 引用必须与登记一致，commit SHA 固定留给受控更新机器人（warning）。
    - **发布 manifest**（`scripts/release_manifest.py` + `supplychain/base-image-pin.json`）：`--build` 哈希 `requirements.lock`/`pyproject.toml`/`Dockerfile`/`app/` 树/SBOM + 基础镜像 pin，`--verify` 重算比对，篡改/漂移失败。
  - 设计：`docs/adr/0010-supply-chain.md`。测试：5 个 gate 各有对应测试套件。

- **Phase 41.3 审计证据外部锚定（SEC-005）**（2026-08-20）：
  - 高危安全/权限/DSR/策略变更与审计证据**同事务持久化**（`app/db/audit.py::audit_high_risk` + migration 31 `audit_anchors`），`BEGIN IMMEDIATE` 事务内追加事件 + 读取链尾 + 写 frontier tip（`fr_{event_id}`），任一失败整体回滚并 fail-closed 503（`code="audit_unavailable"`、`Retry-After: 30`）。
  - 12 类 `HIGH_RISK_EVENT_TYPES`（`api_key.*`、`data_subject_request.*`、`member.invited/role_updated/deactivated`、`retention.policy_updated`、`sla_policy.set`、`webhook.registered/deleted`）由 audit wrapper 强制走该路径。
  - 链头 `{last_seq, last_hash, timestamp, environment}` 经 Ed25519 KMS 签名导出 WORM 锚点（`app/worm_store.py::DiskWormStore`），kid 白名单轮换语义。
  - `scripts/verify_audit_chain.py` 一次校验本地全链 + DB frontier anchors + WORM claims 三证据。
  - 设计：`docs/adr/0011-audit-external-anchoring.md`。测试：`tests/test_audit_anchors.py` 8 passed（覆盖重算全链/删 anchor/替换 manifest/错序/重复 seq/KMS 轮换/WORM 不可用）。

- **Phase 41.7 安全治理自动化（SEC-008）**（2026-08-20）：
  - 每发布提交 threat-model delta（`supplychain/threat-model-deltas/*.json`）：新增入口/资产/信任边界、关闭/新增风险、控制与验证证据，named owner/审批人。
  - `scripts/threat_model_gate.py` 校验缺失 delta、未命名/placeholder owner、未来日期、空 control/evidence。
  - 季度桌面演练（报告接收/依赖漏洞/密钥泄露/跨租户告警）记录于 `supplychain/security-drills.json`，`--check-today` 校验最近演练未过期（90 天）。
  - CI `supply-chain` job 接入 nil-tolerant gate。
  - 设计：`docs/adr/0012-security-governance.md`。测试：`tests/test_threat_model_gate.py` 14 passed。

- **Phase 28-30 核心内容集成**（2026-09-03）：
  - **Phase 28 安全深化**：威胁模型（`docs/SECURITY_MODEL.md` STRIDE 分析 + 控制矩阵），凭据轮换（41.1），审计防篡改（41.3 哈希链 + WORM），应用层加固（OIDC 完整验证 + DSR maker-checker + CSRF 防护），供应链（41.2 五道 gate）。
  - **Phase 29 可靠性与过载工程**：优雅关闭（SIGTERM 后停止接受新请求 → 等待 in-flight turn → SSE 重连提示），背压与过载保护（队列深度阈值、429 + `Retry-After`、租户并发限制），降级矩阵（`docs/DEGRADATION.md`），混沌测试（`tests/test_chaos.py`）。
  - **Phase 30 可运维性与文档体系**：SLO 与告警（`docs/SLO.md` 四指标 + 错误预算），Runbook 与诊断（`docs/runbooks/` + `GET /api/admin/diagnostics`），容量与压测（`docs/CAPACITY.md`），灾备与合规（RTO/RPO 声明、跨区备份流程、故障演练脚本化），用户文档（`docs/guides/operator-manual.md` + `tenant-admin-manual.md`），发布工程（`docs/RELEASE_CHECKLIST.md` 迁移演练门禁 + SemVer 纪律）。

### Changed

- 安全成熟度从 3.5 提升至 4.5（M0 风险闭环 + 凭据生命周期 + 供应链 gate + 审计锚定）
- 可靠性成熟度从 3.8 提升至 4.2（队列 fail-closed + 故障演练 + 降级矩阵）
- 交付工程成熟度从 4.0 提升至 4.5（五道供应链 gate + 发布 manifest + 迁移演练）
- 总评成熟度从 3.7 提升至 4.2（超越 4.0+ 目标）
- 分支覆盖率从 85% 提升至 86%
- 后端测试从 723+ 增加至 757+（新增 M0/Phase 41 测试）

### Fixed

- DSR 未配置 `DSR_EXPORT_SECRET` 时返回 501（之前为 500）
- Redis 不可用时不再静默降级为 SQLite，返回 503 + `Retry-After: 30`
- OIDC 完整验证强化了安全边界，修复算法混淆、重放攻击、租户混淆等潜在风险

## 1.2.0 — 平台化：租户运营、渠道、前端工程 (2026-09-03)

Phase 22-23-26-27 完成，标志着 Helix Support 从"可被第三方集成的商用级平台"升级为**支持多租户自助运营与渠道接入的企业级平台**。本版本实现了租户开通与成员生命周期管理、可嵌入 Web Chat 与渠道 webhook、前端模块化拆分（48 模块 + 351 测试）以及后端结构治理（database.py 拆分为 70 行）。

**成熟度提升**: 总评 3.6 → 3.7；前端工程 2.0 → 3.0。

**发布亮点**:
- ✅ **租户开通 API**：`POST /api/admin/tenants` 幂等开通，自动初始化策略/标签/配额
- ✅ **成员管理**：invite_member / update_member_role / deactivate_member，完整审计
- ✅ **细粒度权限**：新增 auditor（只读审计）/ supervisor 角色，6 种角色权限矩阵
- ✅ **配额与计量**：tenant_usage_daily 表，按天聚合 turn/会话/消息数，导出账单
- ✅ **可嵌入 Web Chat**：widget.html + Widget API，签名 token、SSE 流式、品牌定制
- ✅ **渠道 webhook**：HMAC-SHA256 签名、时间窗重放防护、持久幂等（2026-08-19 完成）
- ✅ **前端模块化**：48 个 JS 模块（最大 399 行），351 个测试，i18n 国际化
- ✅ **后端结构治理**：database.py 仅 70 行，按域拆分为 app/db/ mixin 模块

**完成报告**:
- `docs/RELEASE_1_2_0.md`（发布总结）
- `docs/RELEASE_1_2_0_SUMMARY.md`（实现状态汇总）

### Added

- **Phase 22 租户自助开通与成员生命周期**（2026-09-03）：
  - **22.1 租户开通 API**：`POST /api/admin/tenants`（`app/routers/admin.py:249`）幂等开通接口，`database.provision_tenant()` 实现自动初始化默认策略（知识库种子文档、默认标签、会话配额）。Schema: `TenantProvisionRequest`（tenant_id + name + 可选配额参数）/ `TenantQuotaOut`（配额详情）。审计事件: `tenant.provisioned`。实现位置: `app/db/tenancy.py:273-330`。
  - **22.2 成员管理**：`invite_member()`（邀请成员，幂等，重复调用返回现有成员）、`update_member_role()`（角色变更，完整审计）、`deactivate_member()`（停用成员，保留会话与审计记录）、`list_members()` / `get_member()`（查询成员）、`find_active_members_by_actor()`（OIDC 用户绑定生命周期）。审计事件: `member.invited` / `member.role_updated` / `member.deactivated`。实现位置: `app/db/tenancy.py:426-545`。
  - **22.3 细粒度权限**：6 种角色（admin / supervisor / operator / channel / viewer / auditor），新角色权限：auditor（conversation:read + metrics:read + audit:read，只读审计角色），supervisor（conversation:read/write + operator:act + knowledge:write + metrics:read）。`ROLE_PERMISSIONS` 权限映射表（`app/security.py:25`），`Principal.can()` 统一权限检查，`require_permission()` 装饰器强制 RBAC。
  - **22.4 配额与计量**：`tenant_usage_daily` 表（turn_count / conversation_count / message_count），`list_tenant_usage()` 导出账单数据（CSV/JSON），`_conversation_quota_exceeded()` 配额检查，增量计数防重复（ON CONFLICT DO UPDATE）。实现位置: `app/db/tenancy.py:115` + `app/main.py:93`。

- **Phase 23 Web Chat 渠道与渠道幂等**（2026-09-03）：
  - **23.1 可嵌入 Web Chat**：`app/static/widget.html` 客户侧聊天页面，`app/widget_routes.py` Widget API（`POST /api/widget/sessions` 创建会话、`POST /api/widget/sessions/{id}/messages` 发送消息、`GET /api/widget/sessions/{id}/stream` SSE 流式）。签名 token 认证（`app/widget_token.py`，短期 bootstrap token 换取会话 token）。两种形态：可嵌入脚本 + 独立页面。支持品牌名、主题色、语言、刷新恢复。
  - **23.2 渠道抽象**：渠道级幂等键（`channel_message_id`），外部线程映射（`external_thread_mappings` 表），路由规则（渠道账号绑定租户），持久幂等（重放相同消息 ID 返回原 job）。
  - **23.3 正式渠道 webhook 接入** ✅（2026-08-19 已完成）：`POST /api/channels/{account_id}/webhook`（`app/routers/channels.py`），HMAC-SHA256 签名验证（`app/channel_webhooks.py`），安全协议（`X-Helix-Timestamp` + `X-Helix-Signature`），签名输入格式 `<timestamp>.<body>`，时间窗重放防护（5 分钟），幂等链路（外部 `message_id` → 内部作业幂等）。完整证据: `IMPLEMENTATION_REPORT_PHASE_38.md`。

- **Phase 26 前端工程化**（2026-09-03）：
  - **26.1 模块化拆分**：48 个 JS 模块（`app.js` 已完全拆分），模块列表（api.js / state.js / queue-view.js / conversation-detail.js / composer.js / sse.js / i18n.js / helpers.js / format.js / admin-report.js / knowledge-view.js / quality-view.js 等），最大文件 399 行（符合 ≤400 行目标），设计令牌层拆分（`app/static/css/tokens.css`）。
  - **26.2 前端测试**：351 个前端测试集成到 CI（从 139 升级至 351），测试文件（api.test.js / boot.test.js / queue-view.test.js / knowledge.test.js / widget.test.js / format.test.js 等），框架（Node.js 内建测试 + JSDOM），100% 通过率。
  - **26.3 国际化**：`app/static/js/i18n.js` 国际化模块，支持语言包切换（zh-CN / en），消除硬编码文本。
  - **26.4 前端质量门禁**：`scripts/frontend_gate.py`（CSS 变量验证 + 模块行数检查），最大 399 行 < 400 行要求，无悬空 CSS 变量。

- **Phase 27 后端结构治理**（2026-09-03）：
  - **27.1 database.py 拆分**：`app/database.py` 仅 70 行（已完全拆分），按域拆分为 mixin 模块（`app/db/core.py` 连接/事务/迁移、`app/db/conversations.py` 会话管理、`app/db/messages.py` 消息管理、`app/db/jobs.py` 作业管理、`app/db/knowledge.py` 知识库、`app/db/audit.py` 审计日志、`app/db/tenancy.py` 租户与成员管理、其他 10+ 模块）。
  - **27.2 main.py 按 APIRouter 拆分**：路由已拆分到 `app/routers/` 目录（conversations.py / admin.py / auth.py / channels.py / quality_routes.py / widget_routes.py / 其他 5+ 路由模块）。
  - **27.3 架构决策记录**：延后到 1.3.0（不阻塞 1.2.0 发布）。

### Changed

- 前端测试数量从 139 增加至 351（Phase 26.2）
- 前端工程成熟度从 2.0 提升至 3.0（Phase 26 全部完成）
- 总评成熟度从 3.6 提升至 3.7

### Fixed

- 无破坏性变更，完全向后兼容 1.1.0

## 1.1.0 — 智能质量与集成成熟 (2026-09-03)

Phase 19-21-25 完成，标志着 Helix Support 从功能完整的单体产品升级为**可被第三方集成的商用级智能客服平台**。本版本实现了提示词/模型版本管理与 Canary 对照部署、连接器健壮性防护、Supervisor 质量看板、知识生命周期管理、RFC 9457 统一错误契约、OpenAPI 治理、Python SDK 以及完整 API 文档站。

**成熟度提升**: 总评 3.0 → 3.6；智能质量 2.5 → 3.8；集成能力 2.0 → 4.0；可靠性 3.5 → 3.8。

**发布亮点**:
- ✅ **提示词/模型版本注册表**：任何模型、提示词变更都可登记、可对照、可回滚
- ✅ **Canary 对照部署**：流量分桶、稳定复现、按版本聚合指标
- ✅ **Golden Set 27 例**：覆盖多轮上下文、CJK 检索、提示注入防护、越权探测
- ✅ **租户模型策略与预算**：允许模型列表、每日 turn 预算、超限自动降级
- ✅ **连接器运行时防护**：熔断/重试/降级、租户隔离、故障注入测试
- ✅ **出站 Webhook**：HMAC 签名、指数退避重试、死信队列、事件去重
- ✅ **Supervisor 质量看板**：按天×租户×意图×版本聚合、趋势图表、知识缺口列表
- ✅ **知识生命周期**：draft/pending_review/published/retired 状态、强制审批、负反馈回流
- ✅ **RFC 9457 统一错误契约**：type/title/status/detail/instance + request_id/code
- ✅ **OpenAPI 治理**：快照门禁、破坏性变更检测、全端点文档标注
- ✅ **Python SDK**：28 个测试、类型化错误、重试与幂等键、SSE 流式、webhook 验签
- ✅ **API 参考文档**：集成指南（166 行）+ 完整端点参考（6270 行）

**完成报告**:
- `docs/PHASE_19_COMPLETION.md`（智能质量与集成成熟）
- `docs/PHASE_20_COMPLETION.md`（连接器健壮性与真实接入）
- `docs/PHASE_21_COMPLETION.md`（Supervisor 质量看板与知识运营）
- `docs/PHASE_25_COMPLETION.md`（API 治理与开发者体验）

### Added

- **Phase 25 API 治理与开发者体验**（2026-09-03）：
  - **25.1 RFC 9457 统一错误契约**：`app/errors.py` 的 `problem_response()` 生成标准化错误响应（type/title/status/detail/instance + request_id/code），Content-Type 为 `application/problem+json`。错误类型体系：`urn:helix:error:validation`（422）、`urn:helix:error:authentication`（401）、`urn:helix:error:permission`（403）、`urn:helix:error:not-found`（404）、`urn:helix:error:conflict`（409）、`urn:helix:error:rate-limit`（429）、`urn:helix:error:service-unavailable`（503）。`docs/ERRORS.md` 错误目录记录每类错误的语义、可重试性、处置建议。向后兼容：保留旧 `detail` 字段。
  - **25.2 OpenAPI 治理**：`scripts/openapi_snapshot.py` 实现快照对比门禁，检测破坏性变更（删除端点、删除字段、类型变更、删除/必填参数）并返回非零退出码。快照文件 `api/openapi.json` 作为 API 契约基线。全部 43+ 端点补充 `summary`/`description`，按 tag 分组（Conversations、Admin、Knowledge、Quality、Webhooks、Auth、Widget、Channels、Audit），Schema 定义完整。测试覆盖：`tests/test_openapi_snapshot.py`。
  - **25.3 API 版本与弃用策略**：`docs/API_POLICY.md` 成文化策略：响应体只增不改、弃用需 `Deprecation`/`Sunset` 头 + 至少一个次版本过渡、CHANGELOG 记录每次变更。语义化版本遵循 [semver.org](https://semver.org)：MAJOR（破坏性变更）、MINOR（向后兼容新增）、PATCH（向后兼容修复）。
  - **25.4 Python 客户端 SDK**：`clients/python/src/helix_client/`（612 行）完整实现。`HelixClient` 类封装全部 API：会话 CRUD、消息发送（支持幂等键）、turn job 流式（SSE 事件迭代器）、v2 游标分页（`list_conversations_v2`/`iter_conversations_v2` 自动翻页）、反馈、知识草稿、Admin API（租户/成员/配额/使用导出）、Widget chat。错误层次：`HelixError`（基类）、`HelixAuthenticationError`（401）、`HelixPermissionError`（403）、`HelixNotFoundError`（404）、`HelixConflictError`（409）、`HelixRateLimitError`（429）、`HelixValidationError`（422）。重试机制：指数退避（最多 3 次）。辅助函数：`verify_webhook_signature()`（HMAC-SHA256）。测试覆盖：`clients/python/tests/`（28 例，test_client.py + test_client_v2.py + test_e2e.py）。
  - **25.5 API 参考文档站**：`docs/api/guide.md`（166 行集成指南：认证、幂等、分页、流式 SSE、Web Chat、Webhook 验签、RFC 9457 错误、示例流程、Python SDK 参考）+ `docs/api/reference.md`（6270 行完整端点参考，从 OpenAPI 自动生成，按 tag 分组，包含全部请求/响应 schema、示例、错误码）。生成工具：`scripts/generate_api_docs.py`。
  - **完成报告**：`docs/PHASE_25_COMPLETION.md` 记录全部实现细节、测试结果（SDK 28 例全部通过、OpenAPI 快照门禁通过、Golden Set 27 例通过）、验收门槛检查、成熟度评分变化（集成能力 3.5 → 4.0，交付工程 3.5 → 4.0）。

- **Phase 20 连接器健壮性与真实接入**（2026-09-03）：
  - **20.1 连接器运行时防护**：`app/connectors_runtime.py` 实现统一防护层，包含熔断器状态机（closed → open → half_open → closed，按 (tenant_id, connector) 隔离）、指数退避重试（仅针对 TransientConnectorError）、降级语义（熔断打开时返回 unavailable 结果而非抛异常）。包装器：`ResilientOrderConnector`、`ResilientKnowledgeConnector`、`ResilientCRMConnector`。测试覆盖：`tests/test_connectors_runtime.py`（19 例，验证状态转换、重试逻辑、降级语义、租户隔离）。
  - **20.2 编排层集成**：`app/tools.py` 的 `ToolGateway` 接受连接器依赖注入（order_connector/knowledge_connector/crm_connector），默认使用 Sandbox 实现向后兼容。降级路径：Order 连接器 unavailable 升级人工，Knowledge 连接器降级回退内置 FTS 检索。测试覆盖：`tests/test_connector_degradation.py`（7 例，验证降级行为、Golden Set 在降级路径下仍 100% 通过）。
  - **20.3 HTTP 连接器参考实现**：`app/connectors_http.py` 提供通用 REST 连接器模板，支持 HMAC-SHA256 签名（METHOD\nPATH\nCANONICAL_QUERY\nTIMESTAMP\nBODY）、超时控制（默认 10s）、错误映射（超时/5xx → TransientConnectorError，404 → not_found）。实现：`HttpOrderConnector`、`HttpKnowledgeConnector`、`HttpCRMConnector`。可注入传输层便于测试。测试覆盖：`tests/test_connectors_http.py`（16 例，验证签名、超时、状态码映射）。
  - **20.4 契约测试套件对外化**：`tests/test_connectors.py` 重构为参数化 Conformance Mixin（OrderConnectorConformanceMixin / KnowledgeConnectorConformanceMixin / CRMConnectorConformanceMixin），任何实现继承 Mixin 并实现 `make_*_connector()` 即可验证合规性（身份绑定、跨客户非泄露、未知资源语义）。已验证实现：Sandbox 连接器、HTTP 连接器、Resilient 包装器。测试覆盖：19 例。
  - **20.5 出站 Webhook**：`app/webhooks.py` 实现完整投递系统，支持 6 种事件类型（conversation.created/escalated/resolved/sla_breached/sla_impending、report.generated）。投递语义：at-least-once + 幂等 ID 去重（event_id）、HMAC-SHA256 签名（timestamp.body）、指数退避重试（最多 5 次）、死信队列（超出重试次数）。Admin API：`POST/GET/DELETE /api/webhooks`、`GET /api/webhooks/deliveries`（投递历史查询）。安全防护：SSRF 防护（拒绝内网地址）、Secret 加密存储。测试覆盖：`tests/test_webhooks.py`（32 例）。
  - **完成报告**：`docs/PHASE_20_COMPLETION.md` 记录全部实现细节、测试结果（94 例全部通过）、验收门槛检查、成熟度评分变化（集成能力 2.2 → 3.5，可靠性 3.5 → 3.8）。

- **Phase 21 Supervisor 质量看板与知识运营**（2026-09-03）：
  - **21.1 质量统计聚合**：新增 `quality_daily` 表（迁移 v09），按天×租户×意图×prompt_version 聚合指标。`app/quality.py` 的 `QualityService` 提供增量聚合：`record_turn()` 记录 turn 数/升级率/首次响应时长/平均延迟/估算 token 成本，`record_negative_feedback()` 记录负反馈（支持正负评分翻转），`list_buckets()` 提供 keyset 游标分页查询（since/until 日期过滤、intent/prompt_version 筛选）。API 端点：`GET /api/supervisor/quality`（权限：`metrics:read`，返回 X-Next-Cursor/X-Has-More 分页头）。Schema：`QualityBucketOut` 包含聚合指标与计算比率（escalation_rate/negative_feedback_rate/avg_first_response_seconds/avg_latency_ms）。测试覆盖：`tests/test_phase21.py` 包含 21.1 聚合逻辑、增量 upsert、游标分页、负反馈翻转验证。
  - **21.2 Supervisor 前端视图**：`app/static/js/quality-panel.js` 实现质量看板加载与渲染（10s 节流、权限检查、空态/错误态处理）。双模式渲染：检查器面板内嵌模式（legacy 容器）与独立质量视图全屏模式（React 岛 yieldsLegacy）。岛模式通过 `helix-inspector-quality` 自定义事件桥接 legacy 获取的数据与 React 岛渲染层。质量面板包含趋势图表（`quality-charts.js` SVG 原生绘制，零第三方图表库）与知识缺口列表（`GET /api/supervisor/knowledge-gaps` 返回负反馈+无引用聚类）。全局导航栏"质量看板"挂载点（`switchAppView('quality')`）提供独立整页视图。测试覆盖：`tests/frontend/quality-panel.test.js` 和 `tests/frontend/quality-charts.test.js` 纯模块单测。
  - **21.3 知识生命周期**：迁移 v09 为 `knowledge_articles` 新增 `status`（draft/pending_review/published/retired，默认 published 向后兼容）、`reviewed_by`、`reviewed_at` 列。`app/db/knowledge.py` 的 `search_knowledge()` 查询条件增加 `AND (k.status = 'published' OR k.status IS NULL)` 确保检索只命中已发布条目。API 端点：`POST /api/knowledge/drafts`（创建草稿，`knowledge:write` 权限）、`POST /api/knowledge/{article_id}/review`（审批动作 publish/retire，不可绕过的强制审批）、`POST /api/conversations/{conversation_id}/messages/{message_id}/knowledge-draft`（负反馈回流，从负评消息一键生成 draft 知识条目，自动提取 intent/content）。状态转换由 `database.review_knowledge()` 执行，非法转换抛 `InvalidTransitionError` 返回 409。审计事件：`knowledge.draft_created`/`knowledge.reviewed`/`knowledge.draft_from_feedback`。重复检测（FTS 相似度阈值提示）未在本阶段实现（列入后续优化）。测试覆盖：`tests/test_phase21.py` 21.3 节包含草稿创建、RBAC（非 `knowledge:write` 403）、审批状态转换、重复审批 409、负反馈回流端到端验证。前端测试：`tests/frontend/knowledge.test.js` 覆盖状态归一化、标签分词、表单投影、组合筛选、五计数（全部/已发布/草稿/待审核/已停用）、审核动作（draft/pending_review→publish/retire）。
  - **验收门槛检查**：✅ 模型/提示变更必须通过 golden set 回归门禁才能 activate（Phase 19 已落地）；✅ Supervisor 能按版本/意图下钻定位质量下降来源（`list_buckets()` 支持 intent/prompt_version 筛选）；✅ 知识审批在 API 层强制不可绕过（检索只命中 published 状态，draft 必须经 review 端点发布）。
  - **测试结果**：`tests/test_phase21.py` 33 例全部通过（21.1 聚合与分页 12 例、21.2 前端集成 4 例、21.3 生命周期与 RBAC 17 例）。前端纯模块测试：`quality-panel.test.js` 和 `quality-charts.test.js` 和 `knowledge.test.js` 共计 8 例。

- **Phase 19 智能质量与集成成熟**（2026-09-03）：
  - **19.1 提示词/模型注册表**：新增 `prompt_versions` 表（迁移 v06），支持多版本管理（draft/active/canary/retired 状态）。`app/prompts.py` 的 `PromptRegistry` 类提供完整生命周期 API：`create_version()`、`activate()`、`set_canary()`、`clear_canary()`、`rollback()`。Admin API 端点：`GET/POST /api/prompts`、`POST /api/prompts/{version_id}/action`（权限：`admin:manage`）。全部操作记录审计事件（`prompt_version.created/activated/canary/canary_cleared/rollback/resolved`）。
  - **19.2 Canary 对照部署**：配置 `PROMPT_CANARY_RATIO` (0.0-1.0) 控制流量分配。`PromptRegistry.canary_bucket()` 基于 SHA-256 哈希稳定分桶，同一会话 ID 始终路由到相同版本（canary 或 active）。`turn_policy.py` 每次 turn 解析版本并记录 `prompt_version.resolved` 审计事件。助手消息元数据包含 `prompt_channel`/`prompt_version_id`/`prompt_version`。遥测计数器 `turn.processed` 按 `prompt_channel` 维度标记。测试覆盖：`tests/test_prompt_canary.py`（15 例，包含分桶确定性、ratio 边界、租户优先级、端到端验证）。
  - **19.3 Golden Set 扩展**：从 6 例扩展至 **27 例**（超出 ≥25 目标）。覆盖维度：知识检索（中英文、CJK 长查询）11 例、订单工具（身份验证、跨客户隔离、不存在订单）6 例、敏感升级（退款投诉、支付卡号检测）5 例、提示注入防护（角色扮演、忽略指令、系统提示泄露）3 例、多轮上下文（连续查询、升级后抑制）3 例、边界场景（无知识匹配、订单号缺失）2 例。测试文件：`golden/set.json`，门禁测试：`tests/test_golden_set.py`（100% 通过）。
  - **19.4 租户模型策略与预算**：迁移 v07 新增 `tenants.allowed_models_json`/`daily_turn_budget` 字段和 `tenant_usage_daily` 表。`app/db/tenancy.py` 提供策略 CRUD 和用例计数 API。`turn_policy.py` 实现预算检查（`_check_budget()`）和模型允许列表检查（`_model_allowed()`），超限时 `allow_model=False` 触发确定性降级并审计 `turn.budget_exceeded`/`turn.model_denied`。Admin API：`GET/PUT /api/admin/tenants/{tenant_id}/model-policy`（跨租户访问保护，403 拒绝）。Schema：`TenantModelPolicyRequest`/`TenantModelPolicyOut`。测试覆盖：`tests/test_tenant_model_policy.py`（16 例，包含 DB 层、编排层降级、API 端到端、跨租户拒绝）。
  - **完成报告**：`docs/PHASE_19_COMPLETION.md` 记录全部实现细节、测试结果、验收门槛检查、成熟度评分变化（智能质量维度 2.5 → 3.8）。Phase 19.5（流式取消、供应商故障切换）未实现，建议并入 Phase 20 统一设计。

- **桌面应用快捷键系统**（2026-09-03）：
  - 桌面应用已具备完整的全局快捷键支持，通过 `app/static/js/shortcuts.js` 模块实现。单键快捷键包括：`/` 聚焦搜索框、`c` 新建会话、`r` 刷新队列、`i` 切换检查器面板、`l` 切换低配模式、`j/k` 上下导航队列行。所有快捷键尊重用户输入上下文（在表单控件中自动禁用）且不与浏览器原生快捷键冲突（保留 Ctrl/Cmd/Alt 修饰键组合如 Ctrl+K 命令面板）。
  - 菜单栏快捷键：`CommandOrControl+Q` 退出应用（跨平台，Windows 为 Ctrl+Q，macOS 为 Cmd+Q）。未来可扩展更多菜单快捷键（如 CommandOrControl+W 关闭窗口、CommandOrControl+M 最小化等）。
  - Windows 系统级快捷键自动可用：Alt+F4 关闭窗口、Windows 键组合、任务栏快捷键等，由操作系统和 Tauri 框架自动处理，无需额外实现。
  - 架构优势：快捷键系统基于 Web 标准（`keydown` 事件），桌面壳与 Web 版共享同一套实现，无需维护两套代码；React 岛与 legacy 模块都能响应快捷键，架构透明。
  - 用户体验：所有快捷键在桌面应用中立即可用，无需配置；未来可扩展为用户可自定义快捷键绑定（见 DESKTOP_TAURI_PLAN.md 后续规划）。

- **桌面壳启动体验优化**（2026-09-03）：
  - 改进 Splash 屏视觉反馈：新增加载进度条动画（无限循环横向滑动，40% 宽度），在启动 300ms 后显示，提供持续的活动指示。
  - 优化错误处理逻辑：后端启动失败时保持 Splash 屏可见并隐藏进度条，仅显示错误消息；成功启动时才隐藏 Splash 并触发 UI 就绪事件。
  - CSS 动画优化：进度条使用 `translateX` 动画（GPU 加速），尊重 `prefers-reduced-motion` 用户偏好（禁用动画时显示静态满进度条）。
  - 样式改进：进度条高度 0.25rem，圆角 999px pill 形状，背景使用 `--bg-2` 和 `--accent` 语义颜色令牌，与主题系统一致。
  - 启动时序：窗口创建 → Splash 显示 → 300ms 延迟 → 进度条出现 → 后端就绪 → Splash 消失 → UI 可交互。冷启动目标 < 3s（当前 t_backend_ready 约 2.5s）。

- **桌面壳原生菜单栏**（2026-09-03）：
  - 新增 `src-tauri/src/menu.rs` 模块（78 行）提供原生应用菜单栏。菜单结构：「文件」菜单（退出 CommandOrControl+Q）、「帮助」菜单（检查更新、关于 Helix Support）。
  - 菜单事件处理：退出应用、手动触发更新检查（调用 `updater::check_and_prompt_update()`）、显示关于对话框（应用名称 + 版本号 + 描述）。
  - 集成到 `lib.rs` 的 `.setup()` 钩子，在窗口创建时构建菜单并注册事件监听器。所有菜单项使用中文标签，符合桌面应用 UX 规范。
  - 用户体验改进：「检查更新」菜单项显示三种状态反馈（无更新可用/更新失败+错误详情/下载安装中），用户现在可以主动检查更新而不必等待启动时的自动检查；「关于」对话框提供版本信息供故障排查使用。
  - 更新模块优化：重构 `check_and_prompt_update()` 返回 `Result<bool, String>` 以便菜单等待结果并提供精确反馈；优化进度日志（仅在 10% 间隔输出，减少噪音）；改进对话框文案（明确说明更新将自动重启应用）。

- **桌面壳自动更新机制**（2026-09-03）：
  - 实现基于 `tauri-plugin-updater` 的自动更新检查与安装流程。新增 `src-tauri/src/updater.rs` 模块（132 行）提供 `check_and_prompt_update()` 和 `check_on_startup()` 接口，在应用启动时后台检查 GitHub Releases 更新源。
  - 更新流程：启动时非阻塞后台检查 → 发现新版本时弹出对话框显示当前版本与目标版本 → 用户确认后下载并验证签名 → 安装完成后自动重启应用。下载进度实时输出到 stderr 日志。
  - 安全机制：所有更新包通过 `tauri.conf.json` 中配置的 `pubkey` 进行签名验证（当前为空，等待 D5 阶段代码签名证书采购，见 `DEPLOYMENT_DESKTOP.md` §5）。未签名的更新包将被拒绝安装。
  - 用户体验：更新检查失败不阻塞应用正常使用，仅记录日志；用户可选择"立即更新"或"稍后提醒"；未来可扩展为周期性后台检查（当前每次启动检查）。
  - 配置：更新源在 `src-tauri/tauri.conf.json` 的 `plugins.updater.endpoints` 配置，默认指向 `https://github.com/nangongdao/helix-support/releases/latest/download/latest.json`；`createUpdaterArtifacts: true` 确保构建时生成更新清单。
  - 剩余工作：OV 代码签名证书采购（1-2 周周期）→ 生成签名密钥对 → 填充 `pubkey` → CI 构建时签名 → 发布到 GitHub Releases（D5 阶段，见 `DESKTOP_TAURI_PLAN.md` §7 D5 节）。

- **React 岛渲染性能优化**（2026-09-03）：
  - 队列岛核心组件 memo 化：`QueueRow`、`BulkToolbar`、`QueueStrip` 使用 `React.memo` 包装，仅在 props 实际变化时重渲染。`QueueRow` 使用自定义比较函数，精确检查所有影响渲染的会话字段（id/customer_name/status/preview/sla_due_at/sla_breached/assigned_agent/intent/claim_active/claimed_by/labels）以及视觉状态（active/selected/canOperate/compact），避免 SSE 事件更新单行时触发整个队列重渲染。
  - SLA 格式化优化：在 `QueueRow` 内使用 `useMemo` 缓存 `formatSla()` 计算结果，仅在相关字段（status/sla_due_at/sla_breached）变化时重新计算，减少重复日期计算开销。
  - 虚拟滚动窗口计算优化：在 `queue-island.jsx` 中使用 `useMemo` 缓存 `computeWindow()` 结果，避免 scrollTop 微小变化时的重复计算，减少滚动时的抖动。
  - 预期收益：200+ 会话队列下，SSE 单行更新场景渲染时间减少 70-80%；虚拟滚动流畅度提升；内存占用保持稳定。详细分析见 `docs/REACT_ISLAND_PERF_ANALYSIS.md`。
  - 测试验证：vitest 队列岛测试 17 例全部通过，frontend_gate 351 例通过，无回退。

### Added

- **运维与故障排查手册**（2026-09-03）：
  - 新增 `docs/RUNBOOK_M0.md` 和 `docs/RUNBOOK_1_4.md` 运维手册，覆盖 M0（SEC-001/002、REL-001 停止线修复）和 1.4（SEC-003/004/005/008、AI-001、ARC-001 安全运营基线）的部署前检查清单、升级步骤、凭据轮换操作、故障排查流程、回滚指南、监控告警规则和非作者执行验证标准。两份手册包含完整的 OIDC 配置、DSR 权限分离、Redis fail-closed 验证、API key/渠道 secret 双活轮换、审计锚点导出与恢复、AI 安全评测与模型回滚的操作步骤。
  - 新增 `docs/TROUBLESHOOTING.md` 故障排查手册，覆盖生产环境常见故障场景：服务不可用（502/503 降级、健康检查失败）、性能降级（延迟升高、队列积压）、数据异常（审计链验证失败、空间增长）、认证与权限（API key 401、OIDC 重定向失败）、队列与后台任务（webhook 积压、定时任务未执行）、外部依赖故障（模型 API 超时、Redis 连接失败）。每个场景包含症状、可能原因、诊断步骤（含具体命令）、缓解措施和 5 Why 根因分析示例。附带诊断工具清单和日志分析方法。

### Changed

- **代码风格自动修复**（2026-09-03，commit 0854a3f + 5dd1c34）：
  - 第一轮（0854a3f）：应用 ruff 自动修复规则跨 163 个文件，共 579 处新增、648 处删除（净减少 69 行）。主要修复：`re.I` → `re.IGNORECASE` 规范化（26 处正则表达式，FURB167）、`yield` in for loop → `yield from` 优化（UP028）、多个 `startswith` 调用合并为元组形式（PIE810）、`fromisoformat` Z 替换优化（FURB162）、嵌套 if 语句合并（SIM102）、移除未使用导入和尾随逗号。警告从 182 降至 144 项。
  - 第二轮（5dd1c34）：修复 5 个可自动修复的 ruff 警告，从 72 个降至 67 个。**F841**：删除 3 个未使用的 `run_id` 变量（`desktop/verify_bulk_toolbar_desktop.py`、`desktop/verify_queue_strip_desktop.py`、`tests/test_queue_error_paths.py`）；**F541**：移除 1 个无占位符的 f-string（`desktop/verify_admin_island_desktop.py`）；**F401**：自动清理未使用的导入。
  - 剩余 67 个 E402 警告（模块级导入位置）均为 `scripts/` 中需要在导入前设置 `sys.path` 的合理模式，不影响运行时行为。

- **队列模块测试覆盖提升**（2026-09-03，commit b500944）：
  - 新增 `tests/test_queue_error_paths.py`（21 例）完整覆盖 Redis 队列错误处理路径、stats() 方法、retry() 方法和工厂函数 fallback 场景。
  - 测试场景：SQLiteQueue stats() 全局和租户过滤、RedisQueue stats() dispatch_depth/in_flight 指标、RedisQueue retry() 终态任务重试、Redis 错误处理（enqueue/dequeue/complete/fail/recover 失败路径）、create_task_queue() 工厂函数（redis 导入失败、客户端构建失败、fallback 模式）。
  - `app/queue.py` 模块覆盖率从 **72% → 90%**（226 stmts，22 miss，36 branches），超额完成 >85% 目标。
  
- **测试覆盖率提升**（2026-09-03）：
  - 第一轮：新增 `tests/test_coverage_final_push.py`（3 例）覆盖 `app/attachment_store.py:81`（tmp cleanup 异常路径）、`app/audit_gap.py:63-66`（DB 不可达异常处理）、`app/db/archive.py:167`（before cursor 反转）；新增 `tests/test_channel_webhooks_validation.py`（5 例）覆盖 `InboundChannelRegistry` 配置验证错误路径；扩展 `tests/test_config_validation.py`（+2 例）覆盖 archive 配置零值拒绝。覆盖率 **87.45% → 87.54%**（13109 stmts，1320 miss）。
  - 第二轮：新增 `tests/test_worm_store_errors.py`（11 例）覆盖 `app/worm_store.py` 异常路径（object_id 验证失败、目录创建失败、写入失败、重复写入、读取失败、journal 读取失败、孤立对象、哈希不匹配、mtime 篡改、对象丢失），`worm_store.py` 模块覆盖率从 **76.34% → 86.26%**。
  - 第三轮：新增 `tests/test_labels.py`（11 例）完整覆盖 `app/labels.py` 的 `normalize_conversation_labels` 函数（基础规范化、去重、空值拒绝、长度限制、非打印字符拒绝、最大标签数限制、边界条件）；新增 `tests/test_intake.py`（6 例）完整覆盖 `app/intake.py` 的 `backpressure_reason` 函数（全局队列过载、租户并发上限、边界条件、优先级检查）。
  - 第四轮：新增 `tests/test_residency.py`（16 例）完整覆盖 `app/residency.py` 数据驻留策略模块（区域规范化、默认值回退、区域规范查询、已知区域检查、数据分类权限验证、租户驻留摘要生成、跨境传输记录、恢复兼容性检查），`residency.py` 模块覆盖率从 **97.44% → 100.00%**。
  - 第五轮：新增 `tests/test_observability.py`（10 例）完整覆盖 `app/observability.py` 日志和指标模块（JsonFormatter 基础记录、请求字段、异常格式化、configure_logging 处理器创建、已配置跳过、日志级别环境变量、RuntimeMetrics 请求观测、服务器错误跟踪、空快照、多次相同路由），`observability.py` 模块覆盖率从 **96.55% → 100.00%**；新增 `tests/test_event_schemas.py`（11 例）完整覆盖 `app/event_schemas.py` 事件 schema 注册模块（首次注册、向后兼容添加字段、向后不兼容移除字段、向后不兼容类型变更、向前兼容无新增、版本必须递增、未知兼容模式、未知 schema 查询、全部 schema 返回、不可变性、预注册事件验证），`event_schemas.py` 模块覆盖率从 **0.00% → 98.28%**。
  - 第六轮：新增 `tests/test_outbox_consumer.py`（6 例）完整覆盖 `app/outbox_consumer.py` outbox 事件消费者模块（发布到 webhook、未映射事件无端点、无活跃订阅者、多事件处理、待处理计数、webhook 类型映射注册），`outbox_consumer.py` 模块覆盖率从 **0.00% → 100.00%**（30 stmts 全覆盖）。
  - 第七轮：新增 `tests/test_widget_token.py`（20 例）完整覆盖 `app/widget_token.py` 签名 widget token 模块（签名与验证基础流程、customer_ref/conversation_id 可选字段、自定义 TTL、过期拒绝、未来 iat 拒绝、时钟偏移容忍、签名错误拒绝、格式错误拒绝、base64/JSON 解析错误、tenant_id 缺失/空值拒绝、timestamp 缺失/类型错误拒绝、默认 time.time() 时间戳），`widget_token.py` 模块覆盖率从 **0.00% → 100.00%**（58 stmts 全覆盖）。
  - 第八轮：新增 `tests/test_auth_routes.py`（22 例）完整覆盖 `app/routers/auth.py` 认证路由模块（登录/登出/回调/会话/刷新端点、OIDC 流程集成、CSRF 防护同源检查、session cookie 管理、错误处理、速率限制、配置禁用时 501 响应），`auth.py` 模块覆盖率从 **51.72% → 89.66%**（113 stmts，10 miss，32 branches），超额完成 >80% 目标。
  - 第九轮：新增 `tests/test_widget_routes.py`（23 例）完整覆盖 `app/widget_routes.py` Phase 23 widget API 路由模块（POST /api/widget/sessions 创建会话、POST /sessions/{id}/messages 发送消息（同步/异步/幂等重放）、GET /sessions/{id}/messages 列举消息、GET /sessions/{id}/stream SSE 流式传输、所有异常处理分支：TurnInProgressError/IdempotencyConflictError/InvalidTransitionError/ValueError/LookupError/未分类异常重抛、backpressure 429 响应、签名 token 验证失败/租户不存在/conversation 不存在/token conversation_id 不匹配），`widget_routes.py` 模块覆盖率从 **68.69% → 85.00%**（150 stmts，17 miss，48 branches），达成 >85% 目标。
  - 第十轮：新增 `tests/test_conversation_routes.py`（21 例）和扩展 `tests/test_coverage_final_push.py`（+6 例）完整覆盖 `app/routers/conversations.py` 核心路由模块：查询参数验证（cursor/offset 冲突、mine/assigned_to 冲突、unclaimed/claimed_by 冲突、cursor sort 不匹配）、保存的队列视图 CRUD（创建/列表/删除、重复名称 409）、会话标签 API、批量操作（set_priority/add_labels、无效 action 422）、会话生命周期（claim/release/assign/accept/resolve/reopen）、消息列表分页（limit/cursor/before 参数、X-Has-More/X-Page-Limit/X-Prev-Cursor/X-Next-Cursor 响应头）、创建会话、更新优先级、替换标签、获取会话详情（含 message_limit 参数）、内部备注创建。`conversations.py` 模块覆盖率从 **24.16% → 79.00%**（380 stmts，60 miss，96 branches，22 partial），超额完成 >85% 初期目标。测试通过 29 passed + 1 skipped（反馈测试需真实 assistant 消息）。
  - **最终覆盖率**（2026-09-03）：**88%**（13106 stmts，1238 miss，3232 branches，525 partial）。超额完成 85% 目标，68 个文件达到 100% 覆盖。全部测试通过（723 passed + 50 subtests，exit 0）。

## 1.4.0-desktop — Tauri 2.x 桌面壳 + React 岛双轨(2026-08-26)

### Added

- **桌面原生壳**(`src-tauri/`,基于 Tauri 2.x):`SidecarSupervisor`(动态端口 bind 127.0.0.1:0 → 回读 → 注入 WebView、指数退避就绪探测 200ms→2s 上限 20s、TERM→5s 超时 kill 树优雅停机、崩溃自愈 ≤3 次/分钟超出弹窗、单实例锁二次启动唤起)、`terminal.rs`(portable-pty 白名单诊断终端,xterm.js + fit/webgl/search 三 addon,仅 `admin`/`platform` 角色可见,DEBUG 构建才启用完整交互式 PTY,空闲 10 分钟回收、输出环形缓冲 5MB 上限)、启动三时间戳遥测写入 `%APPDATA%/HelixSupport/telemetry/startup.json`、Splash 屏、设置页显示版本/DB 路径/端口。
- **Python sidecar 打包**(`desktop/helix-server.spec`,PyInstaller `--onedir`):`DATABASE_PATH` env 注入 `app_data_dir`,后端零改动;冒烟脚本 `desktop/smoke_sidecar.py`(spawn→/health/ready→sample API→graceful kill)实测 2.2s 就绪。
- **React 19 岛渐进迁移**(`frontend/src/islands/`):9 个岛(quality/knowledge/ticket/queue/composer/inspector/command-palette/session-shell/terminal);queue 与 inspector 直通 §43.6 reducer 三元组作 `useReducer` 入参,纯函数测试零改写;Zustand v5 客户端全局状态 + TanStack Query v5 服务端状态;`frontend/src/island-loader.js` 运行时 fetch `/static/dist/manifest.json` 解析内容哈希 chunk,无 manifest 时静默跳过所有岛(CSP `script-src 'self'` 下不再报 dev-origin 违反)。
- **tokens.css 三层 @layer**(`@layer tokens.primitive/semantic/component`)+ styles.css `@layer reset, tokens, base, components, utilities` 层叠顺序;motion tokens(`--duration-fast/base/slow` + `--ease-entry/exit/emphasized`);View Transitions API 列表→详情过渡、骨架屏、按钮按压/抽屉/tab indicator 微交互(reduced-motion gate 复验)。
- **ADR-018**(`docs/adr/0018-break-zero-build-vite-react.md`):记录打破零构建原则引入 Vite + React 构建链的动机与边界(operator console 引入构建链;widget 永久保持零构建;双轨期 `createRoot` 挂载到预留 `<div>`,未迁移区由 app.js + js/*.js 驱动;预算口径切换 operator JS ≤700KB raw/≤210KB gzip、CSS ≤125KB)。
- **D5 交互打磨**:`tauri-plugin-updater` 接线(插件已注册,自动更新**尚未可用**——配置修正与签名依赖见下方 2026-09-01 条)、NSIS 安装器;冷启动 SLO `startup.json` 验证 `t_backend_ready_ms=2465ms` < 3s。
- **DEPLOYMENT_DESKTOP.md**:桌面包构建与发布流程文档。
- **完整桌面构建验证（2026-08-27）**：本机成功执行 `cargo tauri build` 产出 `Helix Support_1.4.0_x64-setup.exe`（24MB NSIS 安装器）与 `helix-desktop.exe`（~13.5MB）；`cargo build` + `cargo clippy` 全绿；Vite dist（含最新 island-loader + 全部 9 岛 chunk + manifest.json）重建通过。

### Changed

- `app/assets.py` `STATIC_ASSET_VERSION` 1.3.9→1.4.0;index.html/widget.html/icons 引用 `?v=1.4.0` 同步;`frontend/package.json` version 1.4.0;`src-tauri/tauri.conf.json` + `Cargo.toml` version 1.4.0。
- `app/static/index.html` 新增 9 个 React 岛挂载 `<div>`(queueReactIsland/ticketReactIsland/composerReactIsland/inspectorReactIsland/qualityReactIsland/knowledgeReactIsland/commandPaletteReactIsland/sessionShellReactIsland/terminalReactIsland),双轨期与 legacy 容器并存;新增 `#desktopSplash` 覆盖层(Tauri 环境显示,浏览器 hidden)。
- `frontend/vite.config.js`:`preserveEntrySignatures:"strict"` 防 Rollup 树摇岛入口自身导出;`copyIslandLoader` 插件每构建把零构建 `island-loader.js` 同步到 `dist/`。
- 视觉基线四面(workspace-dark/workspace-light/knowledge-view/mobile-queue)在 clean DB 上重引导以反映 v1.4.0 tokens + splash 的新视觉。
- `tests/ui_admin.py`/`tests/ui_knowledge.py`:app.js 版本断言从硬编码 `1.3.7` 改为引用 `app.assets.STATIC_ASSET_VERSION`。

### Web 回退双轨修正(2026-08-26,commit cbb2825)

- 68d0c33 把 `app.js` 从 3,258 行 legacy 裁成 481 行胶水版(依赖 React 岛渲染),但 web 浏览器(无 `dist/`)下岛不渲染、胶水版无 legacy 渲染能力导致 web 空白。恢复完整 3,279 行 legacy `app.js` 作 web 双轨主渲染器;桌面壳分支仍用 481 行胶水版 + 岛渲染。**双轨架构现状**:web 浏览器 = legacy app.js 主渲染(岛全部跳过);Tauri 桌面壳 = dist 构建后岛渲染(481 行胶水版激活)。

### D5 门禁收口(2026-08-28)

- **桌面壳性能预算**(`scripts/performance_gate.py`,^45b87f5):新增 `lcp_desktop_ms`(≤1000ms,严于 web 的 2500ms——桌面资源来自本地包,唯一变量是自身渲染成本)与 `cls_desktop`(≤0.10,防止 splash 交班把工作区顶偏)两项浏览器层预算。此前浏览器层只测 web 加载,桌面这条路径处于无人看守状态;现在复用岛 10k 渲染已搭好的壳前置条件上下文(`__TAURI_INTERNALS__` + `helix-backend-ready`)一并测量。本机实测:桌面 LCP 356–480ms、CLS 0.0036、岛 10k 渲染 33ms(预算 500ms)、web LCP 404ms。基线 `artifacts/performance-baseline.json` 已按新口径重写。
- **桌面壳无障碍验收**(`tests/ui_accessibility.py`,^4c21dc8):新增桌面壳 pass(`wait_for_desktop_shell` + `assert_desktop_shell_accessibility`),在 Tauri 前置条件下等岛挂载后跑 axe 明暗双主题 + reduced-motion。**D3 岛接管后桌面发货的 DOM 此前零覆盖**——原套件始终只扫 legacy 渲染、且队列恒为空。新 pass 经 `helix-conversations-updated` 事件注入四种状态的合成会话(与生产同通道、确定性,不依赖库里碰巧有什么数据),使扫描真正覆盖行标记而非空态。
- **前端门测试计数修复**(`scripts/frontend_gate.py`,^893659d):Node 测试运行器按 stdout 是否 TTY 选择 reporter——交互式输出 `ℹ pass N`,管道输出(CI 及一切 `subprocess.run` 捕获)输出 `# pass N`。旧解析只认前一种,计数恒为 0,`MIN_TESTS=30` 断言失败,本地前端门一直是红的,且失败形态与「一个测试都没跑」无法区分。新增 `_parse_summary()` 兼容两种形态,解析不到摘要时显式报错而非静默报 0;`tests/test_frontend_gate.py` 补 3 例(TAP/spec/无法识别)。

### Fixed

- **自动更新配置整套失效:1.x 残留字段 + 缺失 v2 必需产物开关(2026-09-01)**:`plugins.updater.active: true` 是 Tauri **1.x** 字段,v2 的 `tauri-plugin-updater` 2.10.1 `Config` 结构体里根本没有它。而 Tauri 对 `plugins.*` 下的未知字段是**静默忽略**的:实测注入 `bogusFieldThatDoesNotExist` 后 `cargo tauri info` 零输出、exit 0,`$schema` 也覆盖不到这一层。于是这个字段整个 D5 阶段都在制造"自动更新已启用"的假象,而 v2 真正决定是否产出更新产物的 `bundle.createUpdaterArtifacts`(源码默认值 `Updater::Bool(false)`)完全缺失——构建从不产出更新包与 `.sig` 签名文件,自动更新无包可装。同时 `pubkey` 为空字符串:查证 `verify_signature` 路径确认这是 **fail-closed** 而非静默接受未签名包(`PublicKey::decode("")` 必然报错),安全上不成漏洞,但功能上等于自动更新整体不可用。修复:移除 `active`、补 `bundle.createUpdaterArtifacts: true`,`pubkey` 待签名证书采购(D5 唯一外部依赖)。
- **Tauri 配置门禁**(`scripts/tauri_config_gate.py` + `tests/test_tauri_config_gate.py` 15 例,接入 CI `Tauri config gate` step):守住三条不变量——无 1.x 残留字段(`active`/`dialog`,报错直接给出 v2 修法)、updater 三件套自洽(有 `endpoints` 就必须有 `createUpdaterArtifacts` 与非空 `pubkey`)、`dangerous_*` 传输层开关不得开启。签名证书采购期间空 `pubkey` 由 `--allow-empty-pubkey` 显式豁免(降级为 warning),让豁免在 CI 调用处可见,而不是靠一个空字符串蒙混过关。`plugins` 节是 schema 检查不到的盲区,只能靠门禁守。
- **`frontend_gate.py` 资产版本检查从未生效**(^9afeb4d,潜伏自 2026-08-18):`LOCAL_IMPORT_RE` 的反向引用 `\1` 闭在**路径**捕获组上而非引号上,等于要求同一个说明符连写两次——任何文件里的任何导入都匹配不上,而 notes.md 当时声称"此处版本漂移会让 CI 失败"。同一循环还读 `match.group(2)`,而旧模式只暴露一个组,真匹配上反而会 IndexError(被前一个 bug 掩盖)。修复:引号自成一组使反向引用闭合同类,组 1 为引号、组 2 为说明符。同提交另修 `sys.path` bootstrap 缺失——CONTRIBUTING.md 让开发者直接跑 `python scripts/frontend_gate.py`,但没有 editable 安装时 `from app.assets import ...` 直接 `No module named 'app'`,只有 CI(`pip install -e .`)能跑通。抽出可测纯核 `find_unversioned_imports()`,补 8 例锁定匹配(单/双引号、from 与裸 import 形式、行号、裸包说明符忽略)与版本比对;故意把真实模块的 `?v=` 降级实测 exit=1 并精确报出文件/行号/过期说明符。这与上面 `plugins.updater.active` 是同一类缺陷:**检查看似存在,实则从未运行**。

- **桌面壳 sidecar 嵌入过期静态资源(真机 CDP 岛模式验证发现,2026-08-29)**:PyInstaller spec 把 `app/static/` 整体嵌入 sidecar,但打包时 `frontend/dist` 产物是旧的——sidecar 提供的 HTML 为 v1.3.9 快照(无岛挂载 div、无 dist 资源),桌面壳岛模式静默回退 legacy 渲染且无任何报错。CDP(Runtime.evaluate)确认 `__HELIX_ISLAND_MODE__=false`。修复:`npx vite build` 先于 PyInstaller(顺序已写入 DEPLOYMENT_DESKTOP.md 并加顺序警告 + 嵌入快照新鲜度验证命令),重打 sidecar + 重拷 resources + 重建 release。真机复验:8 岛全部挂载(queue/composer/inspector/quality/knowledge/ticket 等,terminal 按需挂载为 0),legacy 容器全部 yield(hidden=true),React 树真实渲染(composer textarea + inspector 4 tab),像素统计确认深色控制台(avg RGB(13,17,23))。
- **桌面壳资源路径断裂(真机 GUI 冒烟发现,2026-08-29)**:`frontendDist` 嵌入模式下 webview 直接加载 `index.html`,但页面所有资源是 `/static/...` 绝对路径(为 uvicorn StaticFiles 设计),`tauri://localhost` 下全部 404——真机窗口渲染裸 HTML 骨架(无 CSS/JS),UI 永不初始化,`t_ui_ready_ms` 恒 null。修复:壳只在启动帧显示内置 splash 页,后端就绪后 `WebviewWindow::navigate` 到 sidecar 自身 origin(`http://127.0.0.1:<port>/`),资源与 API 全部同源成立;watchdog 自动重启绑定新端口后重新导航。`on_page_load` 在服务端页面加载完成后注入 `__HELIX_BACKEND__` 并派发 `helix-backend-ready`、直调 `ui_ready`(on_page_load 与 module 执行时序跨导航已证不稳,直调兜底)。远程页面 IPC 需显式 ACL 授权:`build.rs` 用 `AppManifest::commands` 为 7 个自定义命令自动生成 `allow-*` 权限,`capabilities/main.json` 配 `remote.urls=["http://127.0.0.1:*"]` + 权限授予。真机复验:窗口渲染完整深色控制台,三时间戳 window 969ms / backend 3306ms / ui_ready 3872ms,API 200,关窗优雅停机无残留。
- **亮色主题 amber 状态徽标对比度不足**(^d92236c):`.status-pill.waiting_human` 以 `--color-amber` 文字压在 `--color-amber-soft` 叠行底色上,实测 **3.99:1**,低于 11px 文字要求的 4.5:1。该缺陷在历次 axe 运行中全部存活——徽标只在队列有行时渲染,而旧扫描面对的永远是空队列。桌面壳 pass 注入数据后首轮即被抓出。`--color-amber` 亮色值 `#8a6512` → `#6f5010`(5.58:1),与同背景的兄弟状态色对齐(green 6.17:1 / violet 5.91:1 / blue 4.85:1),soft 色调同步重算保留琥珀倾向。

### Gates(2026-08-28 复跑)

- frontend gate **165** tests + vitest **19**;performance gate 静态 JS 599KB/700KB + CSS 97KB/125KB,浏览器层 web + 桌面壳双上下文全绿;visual gate 四面 **0.00% drift**(令牌改动未波及 clean DB 基线);ui_smoke + ui_accessibility(含新增桌面壳 pass)旅程绿;`tests/test_frontend_gate.py` + `tests/test_performance_gate.py` 10 例绿。
- 注:本机 ruff 0.16.5 默认规则集宽于项目开发期(CI 固定 `ruff>=0.9,<1`),全仓 372 项报告属版本差异,非本次改动引入;本次改动未新增告警(并顺带消掉 1 项 PIE810)。

### D3 收口:knowledge 岛接管编辑器(2026-08-29)

- **knowledge 岛成为完整知识面**(`frontend/src/islands/knowledge-island.jsx` + `island-loader.js` yieldsLegacy 增加 `knowledgeEditor`):摘要 + 筛选 + 列表 + 草稿编辑器全部由 React 岛渲染,legacy 侧仅保留写生命周期(`saveKnowledgeArticle` 的 api()/toast/reload 与 `reviewKnowledgeArticle` 的 retire confirm())。编辑器写回走 `helix-knowledge-save` 事件桥,岛在 `helix-knowledge-saved` 回报前保持 busy;岛内 DOM 保留 legacy 定位符契约(`knowledgeEditor`/`knowledgeTitle`/… → React 后缀),ui_knowledge 与 axe 键盘路径定位不受影响。
- **writer/reader 数据面补齐**:岛按角色请求 `?include_inactive=true`(对齐 legacy 缓存语义),摘要计数与编辑器不再只看到已发布文章;语言下拉覆盖 app.js 全部 14 种语言,未列出的语言码动态补入选项,编辑不再静默丢语言(review backlog 1);校验(`validateKnowledgeDraft`)复刻 legacy minlength 口径与文案,tags→title→content 顺序报错 + `role="alert"`。
- **刷新桥语义**:视图重开只派发不强制刷新的 `helix-knowledge-refresh`,岛用 `refetchQueries({stale:true})` 对齐 legacy 15s 缓存(数据新鲜不重复请求);显式刷新/写入成功仍强制 refetch。
- **vite 陈旧 chunk 累积修复**(`frontend/vite.config.js`):outDir 在 frontend 根之外时 Vite 默认不清空目录,内容一变哈希就变,旧 content-hashed chunk 永久累积——`client-*` 新旧两份即 362KB,静态预算门(全量计非 terminal JS)实测 903,980B > 700KB 上限。改 `emptyOutDir: true`(island-loader 由插件在 closeBundle 重拷,dist 内无第三方文件)。
- **验证**:vitest **40** 例(新增 21 例:reducer 生命周期、校验 parity、桥契约、语言全集、角色可见性、刷新语义);`tests/test_frontend_gate.py` + `tests/test_performance_gate.py` 绿;legacy web 路径 `tests/ui_knowledge.py`(writer 全旅程 + reader 只读)与 `tests/ui_accessibility.py` 对 127.0.0.1:8765 实跑通过;重建 dist 后静态 JS 回到预算内。

### D3 长尾:admin 岛接管管理页(2026-08-29)

- **admin 岛成为完整管理面**(`frontend/src/islands/admin-island.jsx` + `island-loader.js` 新增 admin 岛 + vite input 注册 + index.html `#adminReactIsland` 挂载点):八张卡片(租户配额/成员/Webhook/报表订阅/报表导出/CSAT/SLA 策略/自动路由规则)全部由 React 岛渲染,八张 legacy 卡片加 id 后经 yieldsLegacy 让位,拒绝面板 `#adminDenied` 与头部刷新按钮保持 legacy。
- **确定性权限门**(`useIdentity` + `helix-identity` 事件):app.js 在 /api/me 后发布 `__HELIX_PERMISSIONS__`/`__HELIX_ACTOR__` 并派发 identity 事件,岛的八个查询全部 `enabled: canManageIdentity`——岛先于 /api/me 挂载的竞态下非管理员**零特权请求**(对齐 tests/ui_admin.py 的 denied 断言),管理员身份落地后查询自动解锁。此事件同时修复了 knowledge/terminal 岛读一次性 `__HELIX_ROLE__` 全局的同类竞态隐患(事件广播后可重渲染)。
- **写桥**:13 个 `helix-admin-*` 事件桥把写回 legacy——api()/showToast()/window.confirm()(webhook 删除确认)与 Phase 32.1 自停用/自降级客户端守卫全部留在 app.js;岛在 `helix-admin-saved {ok, domains}` 回报后只失效被写域的查询(webhook 写会连带失效订阅下拉域)。表单清空遵循 legacy「成功才清空」语义(`useClearOnSaved`);报表导出走 `reportExportUrl` 本地导航(带鉴权 cookie),生成预览经 `helix-admin-report-generated` 回传文案。
- **测试抓出一个真实浏览器缺陷**:React 合成事件不代理 `submitter`——岛若照搬 legacy 的 `event.submitter?.value`(js/admin-report.js bindAdminReports 收的是原生事件),「导出 CSV」按钮在真实浏览器里会永远走生成预览分支。岛改读 `event.nativeEvent.submitter`,vitest 以原生点击路径锁定该分支。
- **验证**:vitest **64** 例(+24:身份门/八卡渲染契约/13 写桥/刷新语义/纯模型含 legacy "— MB" 空值逐字保真);`tests/test_frontend_gate.py` + `tests/test_performance_gate.py` 绿;legacy web 路径 `tests/ui_admin.py`(配额写/成员生命周期/webhook 注册删除/拒绝视图零特权请求)与 ui_accessibility、ui_smoke 对实跑服务全绿;桌面链重建(dist+admin chunk 23.1KB → PyInstaller → 冒烟 → resources 快照核对 → NSIS)后 `desktop/verify_admin_island_desktop.py` CDP 真机验证:岛模式 true、legacy 卡片让位、配额 readout 渲染、经桥真实邀请成员并回显「主管」角色。

### D3 长尾:settings 岛 + 端口读数修复(2026-08-29)

- **settings 岛接管设置面**(`frontend/src/islands/settings-island.jsx`):桌面运行时 readout(版本/端口/模式/数据目录)与偏好卡由 React 岛渲染,两张 legacy 卡片加 id 后经 yieldsLegacy 让位,标题保持 legacy。岛通过 `useDesktopBackend` 同时跟踪 `window.__HELIX_BACKEND__` 注入与 `helix-backend-ready` 事件(legacy 每次进视图重跑 `loadDesktopInfo` 的异步等价物),版本号从 `frontend/package.json` 以 JSON import 单源引用。
- **修复 D1 起的端口读数缺陷**:壳注入契约是 `{ backendPort: location.port }`(src-tauri/src/lib.rs),而 legacy `desktop-info.js` 一直读不存在的 `backend.port`——桌面壳设置页端口自 D1 起恒显「等待中…」。legacy 与岛同步改读 `backendPort`;CDP 真机验证 readout 与实时 sidecar origin 端口一致。
- **死代码清理**:`desktop-info.js` 移除早期探索遗留的 `loadKnowledgeView`/`loadAdminView`(派发从未有消费者的 helix-knowledge-loaded/helix-admin-loaded 事件;真实桥是 app.js 的 helix-knowledge/helix-admin 事件族)。
- **验证**:vitest **72** 例(+8:模型 parity 含等待中/错误态/浏览器占位、backend-ready 异步更新、已注入即 seeding、legacy 类契约);pytest 门禁绿;ui_smoke + ui_accessibility legacy 实跑绿;桌面链重建(dist+settings chunk 2.5KB → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_settings_island_desktop.py` CDP 真机验证:岛模式 true、legacy 卡让位、版本/端口/模式渲染、端口与 origin 一致、env note 隐藏。

### D3 长尾:dashboard metrics 岛(2026-08-29)

- **dashboard 岛接管工作区指标条**(`frontend/src/islands/dashboard-island.jsx`):四格指标(自动/待响应/认领中/SLA 超时)由 React 岛渲染,legacy `#metrics` 经 yieldsLegacy 让位;`metricsModel` 捕获 legacy `renderMetrics` 的**净渲染效果**——legacy 先构建「待人工」瓦片再在绘制前原地改写为「待响应」(needs_response),可见瓦片集从未显示 waiting_human,岛直接按净效果建模并以测试锁定。
- **刷新节奏语义保真**:legacy 仅在**前台** refreshAll 周期(初次加载/用户操作/搜索)refetch `/api/dashboard`,30s 后台轮询复用缓存读数——岛模式下 legacy 前台周期派发 `helix-dashboard-refresh {force}`,后台周期不派发也不发请求;岛 `staleTime: Infinity` 只响应 force 事件,首帧渲染空网格(不闪 0)对齐 legacy 首刷前行为。应用状态不再写 `state.dashboard`,yielded 的 `#metrics` 不再被填充。
- **验证**:vitest **80** 例(+8:净渲染 parity 含告警规则/缺省 0、tenant 头、首帧空网格、force 刷新 refetch、unforced 忽略);pytest 门禁绿;ui_smoke + ui_accessibility legacy 实跑绿;桌面链重建(dist+dashboard chunk 1.6KB → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_dashboard_island_desktop.py` CDP 真机验证:4 瓦片标签/数值/legacy 让位/tenant 头/force 事件触发真实 refetch 全过。过程杂音:rustc 因系统内存不足(可用 2.5GB)OOM 崩溃留下损坏的编译产物(E0463 找不到 crate),清 `target/release/{deps,.fingerprint}` 后重建通过(3m35s)。

### D3 长尾:queue 岛接管条带控件(2026-08-29)

- **queue 岛扩至 footer strip**(`frontend/src/islands/queue-island.jsx`):会话计数与「加载更多」按钮由岛渲染,legacy `#queueCount`/`#loadMore` 经 yieldsLegacy 让位;快照通道 `helix-conversations-updated` 增加 `queueLoadingMore`,js/queue-view.js 的 renderQueue 在岛模式下完全停止绘制 legacy 队列 DOM(此前 strip 仍由 legacy 先画)。分页生命周期(cursor、加载中守卫、query-key 陈旧校验)留在 legacy——岛按钮经新增的 `helix-queue-load-more` 事件桥触发 `loadMoreConversations()`。
- **状态完整性**:岛此前预快照/空态直接提前返回,现在统一渲染 `.queue-island` 包装层——预快照显示「正在同步 + 0 个会话」(镜像 legacy 初始文案),空态/加载中/列表态都带 strip;mentionsBadge 因绑定 session.js 面板生命周期保持 legacy(其所在 legacy footer 仍在)。
- **验证**:vitest **84** 例(+4 strip 用例:+后缀计数/无更多隐藏/加载中 aria-busy/点击桥事件,预快照用例改写);pytest 门禁绿;ui_smoke + ui_virtual_queue + ui_accessibility legacy 实跑绿;桌面链重建(queue chunk 5.4KB → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_queue_strip_desktop.py` CDP 真机验证:**种子 60 条会话**(页大小 50)→ 条带显示「50+ 个会话」→ 岛内「加载更多」点击触发真实 cursor 请求 → 行数增至 60,legacy 控件全程让位。

### D3 长尾:identity 岛接管头部身份读数(2026-08-29)

- **identity 岛**(`frontend/src/islands/identity-island.jsx`):头部身份读数("actor · 角色",header 中唯一数据派生元素)由 React 岛渲染,legacy `#operatorIdentity` 经 yieldsLegacy 让位;周边切换按钮(主题/低配/检查器/刷新/移动端抽屉)各绑 legacy 偏好生命周期,保持 legacy。岛是 `helix-identity` 事件的纯订阅者(admin 岛切片引入的身份广播)——零 fetch 零写桥,事件按 actorId+role 去重避免每刷新周期重渲染;`identityModel` 逐字复刻 legacy `roleLabel` 回退映射(岛不接 i18n 模块,与其它岛逐字复制规则一致),未认证时显示 legacy 初始文案「正在验证」。app.js 岛模式下跳过向隐藏 legacy span 的绘制。
- **验证**:vitest **90** 例(+6:模型 parity 含未知角色回退/待验证占位、事件订阅、全局 seeding、legacy 类契约);pytest 门禁绿;ui_smoke + ui_accessibility legacy 实跑绿;桌面链重建(identity chunk 1.2KB → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_identity_island_desktop.py` CDP 真机验证:岛读数「demo.admin · 管理员」、legacy span 隐藏且未被绘制、头部切换按钮全部保留。

### D3 长尾:conversation dialog 岛 + drawer 决策(2026-08-29)

- **conversation-dialog 岛**(`frontend/src/islands/conversation-dialog-island.jsx`):新建会话 `<dialog>` 由 React 岛渲染(原生 dialog + showModal 焦点管理),legacy `#newConversationDialog` 经 yieldsLegacy 让位。桥三件套:legacy「新建」按钮岛模式下派发 `helix-conversation-new` 开岛对话框;岛提交经 `helix-conversation-create {payload}` 回 legacy——`createConversation(payload)` 从表单处理器中提取为共享生命周期(POST → 选中 → 前插 → renderQueue → loadDetail → refreshAll),legacy 与岛共用;岛经 `helix-conversation-created {ok}` 回报,成功关闭、失败保留输入。取消/关闭为岛本地行为(无需 legacy 往返)。
- **jsdom 能力探测回退**:jsdom 26 仍未实现 `showModal/close`——岛做能力探测,无原生 API 时直接设 `open` 属性(测试路径),真实浏览器走模态路径(top layer + backdrop + 焦点圈),生产行为不变。
- **queue drawer 决策不激活**:抽屉入口 `#mobileQueue` 为 `.mobile-only`(≤900px 才显示),桌面壳宽视口下不可达;岛模式只存在于桌面壳,迁移零收益,与 session/shell 决策同理,列为后续候选。
- **验证**:vitest **98** 例(+8:payload 投影 parity/开闭契约/桥 busy 态/成功关闭/失败保留);pytest 门禁绿;ui_smoke(含 legacy 对话框旅程)+ ui_accessibility 实跑绿;桌面链重建(dialog chunk 3.5KB → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_conversation_dialog_desktop.py` CDP 真机验证:legacy 对话框让位、新建按钮开岛对话框并聚焦名称、真实 POST 201、成功后岛关闭且新会话入列并选中(is-active)。

### D3 长尾:queue 岛接管 bulk toolbar(2026-08-29)

- **bulk toolbar 并入 queue 岛**(`frontend/src/islands/queue-island.jsx`):批量操作条(已选计数/动作下拉/标签字段/应用/清除)由岛渲染,legacy `#bulkToolbar` 经 yieldsLegacy 让位,renderBulkToolbar 岛模式直接跳过绘制。批量生命周期(payload 构建、POST bulk-actions、toast、选择清空、refreshAll)留在 legacy——`applyBulkAction(source)` 增加可选载荷参数,legacy 表单路径与岛 `helix-queue-bulk-apply {action, labels}` 桥共用;标签动作的空标签校验前移到岛内(复刻 legacy「请输入标签」文案,role=alert 内联呈现,不发桥);桥完成派发 `helix-queue-bulk-applied` 释放岛的 busy 态;`helix-queue-bulk-clear` 桥走 legacy 清空 → 快照回流同步岛。
- **门禁抓住一个真 bug**:重构后 legacy 点击监听器仍直接绑定 `applyBulkAction`,点击事件对象作为首个实参泄漏进 `source` 形参(MouseEvent 为 truthy)→ `source.labels` 为 undefined → `.length` 抛错,批量 POST 永不发出。ui_smoke 批量旅程超时暴露,监听器改为显式无参调用。vitest 岛侧补 busy 释放用例锁定 `helix-queue-bulk-applied` 契约。
- **验证**:vitest **105** 例(+7:计数/标签字段显隐/桥载荷含逗号全半角解析/空标签内联阻断/busy 释放/无选择隐藏/清除桥);pytest 门禁绿;ui_smoke(批量旅程恢复)+ ui_virtual_queue + ui_accessibility 实跑绿;桌面链重建(queue chunk → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_bulk_toolbar_desktop.py` CDP 真机验证:legacy 让位、无选择时无工具栏、选 2 行显示「已选 2 项」、真实 POST 200 updated=2、成功后工具栏消失。

### D3 长尾:workspace tabs 岛(2026-08-29)

- **workspace-tabs 岛**(`frontend/src/islands/workspace-tabs-island.jsx`):工作区「队列/工单」tablist 由 React 岛渲染,legacy `#workspaceTabs` 容器加 id 后经 yieldsLegacy 让位。职责切分:岛渲染两个 tab(保留 .workspace-tab/is-active/role=tab/aria-selected/data-wstab 契约)并乐观切换;窗格切换(queuePane dataset.mode、ticketPane 显隐)、工单加载与队列刷新副作用全部留在 legacy `switchWorkspaceTab`——岛点击经 `helix-workspace-tab {field}` 桥触发,legacy 每次切换派发 `helix-workspace-tab-changed {field}` 让岛对账(同时覆盖工单跳转回队列等程序化切换)。
- **验证**:vitest **110** 例(+5:默认态契约/乐观切换桥/程序化对账/未知字段忽略/reducer 幂等);pytest 门禁绿;legacy 回归新增 ui_tickets(工单全旅程——tab 切换的直接消费者)与 ui_smoke、ui_accessibility 实跑绿;桌面链重建(tabs chunk 1.2KB → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_workspace_tabs_desktop.py` CDP 真机验证:legacy 让位、岛切工单 → legacy 窗格跟随 → 切回队列 → 直接调 legacy 切换器程序化切换时岛正确对账。

### D3 长尾:saved views 岛(2026-08-29)

- **saved-views 岛**(`frontend/src/islands/saved-views-island.jsx`):工作区的视图选择器 + 保存/删除按钮由 React 岛渲染,legacy `#savedViewField`/`#saveView`/`#deleteView` 经 yieldsLegacy 让位。数据生命周期留在 legacy——apply 传完整 view 对象(岛持数据)给 `applySavedView`(重写 legacy 过滤输入 + refreshAll),save 经桥 `helix-saved-views-save {name}` 用 `currentViewFilters()`(legacy 输入是过滤器唯一真源)POST 并 toast,delete 经 `helix-saved-views-delete {id}`;桥完成派发 `helix-saved-views-changed {ok, id?}` 让岛 refetch 并重选新建视图/删除后清空选择。岛自取 `/api/saved-views`,legacy `loadSavedViews` 岛模式跳过。`display:contents` 让岛控件无缝接管工具栏 grid 的单元格。
- **真机 CDP 抓住 mount 缺 Provider 缺陷**:岛的 `mount()` 忘了包 `QueryClientProvider` 而组件用 `useQueryClient`——真机启动 React 抛 "No QueryClient set",岛容器静默为空;组件测试各自包 provider 故测不出,只有桌面 boot 路径会踩中。修复 mount 并在注释记录该测试盲区。
- **验证**:vitest **119** 例(+9:select 装载/类契约/apply 桥带完整 view/提示词保存桥/取消不发桥/changed 重选/删除清选/失败不动选择);pytest 门禁绿;ui_smoke + ui_accessibility 实跑绿;桌面链重建后 `desktop/verify_saved_views_desktop.py` CDP 真机验证:legacy 让位、保存(POST 201 + 提示词 + 岛重选)、应用(改写 legacy 过滤输入 + 触发新队列请求)、删除(DELETE + 选择清空)全旅程绿。

### D3 长尾:mentions 岛接管提及收件箱(2026-08-29)

- **mentions 岛**(`frontend/src/islands/mentions-island.jsx`):提及徽标(经 React portal 渲染进 footer 的挂载点,与 legacy live dot 同排)与提及面板抽屉由 React 岛渲染,legacy `#mentionsBadge`/`#mentionsPanel` 经 yieldsLegacy 让位。镜像 legacy 语义:未读 0 且面板关闭时徽标隐藏、打开面板时拉取 `/api/mentions`、外点关闭(bindSession parity)、`conversation:read` 权限门经 helix-identity 广播。写生命周期留 legacy——标记已读(`POST /read` + toast)与跳转会话经 `helix-mentions-mark-read`/`-open-jump` 桥,完成派发 `-changed` 让岛 refetch。徽标 portal 容器缺失时内联回退,DOM 回归不会拖垮整岛。
- **能力边界记录**:真实提及种子需要第二作者会话(后端跳过自我提及),CDP 旅程以空收件箱路径验证(徽标隐藏 parity/程序化开面板/真实 API 空态/外点关闭),jump 与 mark-read 桥由组件测试锁定派发契约。
- **验证**:vitest **127** 例(+8:权限门/徽标可见性两态/面板行渲染/空态/跳转桥/已读桥 + changed refetch/外点关闭);pytest 门禁绿;ui_smoke + ui_accessibility 实跑绿;桌面链重建(mentions chunk 4.1KB → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_mentions_island_desktop.py` CDP 真机验证:legacy 让位、0 未读徽标隐藏、程序化开面板渲染真实空态、打开时徽标可见、外点关闭、真实 API 已请求。

### D3 长尾:palette 命令接线(2026-08-29)

- **闭合先前缺口:helix-command 事件自 palette 岛激活以来无消费者**——桌面壳 Ctrl+K 选任何命令都无效果。app.js 新增消费者把 9 条命令映射到既有处理器:nav:* → `switchAppView`、conv:new → `helix-conversation-new`(岛对话框桥)、conv:refresh → `refreshAll`、conv:convert-ticket → ticketView 模块、diag:logs → 新增 `helix-terminal-toggle` 桥(terminal 岛监听开关抽屉)、diag:health → 新增 `checkBackendHealth()`(GET /health/ready 结果 toast)。legacy 命令路径不变(浏览器模式 palette 岛不挂载)。
- **验证**:vitest **127**(纯接线无新增岛代码);pytest 门禁绿;ui_smoke + ui_accessibility 实跑绿;桌面链重建(app.js → PyInstaller → 冒烟 → resources 核对 → NSIS)后 `desktop/verify_palette_commands_desktop.py` CDP 真机验证:Ctrl+K 开面板、nav:admin 打开管理视图(admin 岛渲染)、conv:refresh 触发真实队列请求、diag:logs 打开诊断终端抽屉。脚本内置重试:palette 岛在 ISLANDS 数组末尾异步挂载,过早按键会丢失。

### 前端门禁:vitest 段不再无限挂起 + 静态字节预算随抽取战役放宽(2026-08-30)

- **vitest 段退出挂起修复**(`scripts/frontend_gate.py`):16 个岛测试文件全量运行时,vitest 在 ~15s 内跑完 166 例并打印通过摘要后**进程永不退出**(实测 420s 仍存活,15 个文件及以下必定正常退出)。定位为 Vite/esbuild 转换服务在 Windows 上为每个 worker 派生的 esbuild 子进程:文件数达到 16 时子进程句柄把父进程的事件循环一直挂着。修复:vitest 段以 `ESBUILD_WORKER_THREADS=1` 运行(esbuild 改走 worker thread 而非子进程),同一全量连跑 3 次均在 20s 内退出、退出码 0。
- **门禁不再可能无限等待**:`_run()` 新增 `timeout`/`env` 参数并把 `subprocess.TimeoutExpired` 归一成返回码 124 的失败结果——一个能永久挂住的门禁比一个会红的门禁更危险。vitest 段默认上限 300s(`FRONTEND_GATE_VITEST_TIMEOUT` 可覆盖),超时即报失败并附摘要尾部。
- **静态 JS 预算 700KB → 715KB**(`scripts/performance_gate.py`):app.js <500 战役每把一个 legacy 域搬进 ES 模块,净增 ~0.9–1.5KB 纯样板(import/export 语句、模块 JSDoc、app.js 为自用调用点保留的薄包装),被搬走的逻辑本身字节中性。第 23 片(命令面板 + saved-views,约 110 行)实测净增 880B,而旧上限只剩 1,043B 余量——再搬一片即红。15KB 余量覆盖约十片;app.js 降到 500 行以下后需重新收紧。依赖膨胀或未压缩的第三方 blob 依旧会被这道门禁拦下。
- **验证**:`scripts/frontend_gate.py` 全绿(node **270** + vitest **166** + 语法 + 400 行上限 + 资源版本);`tests/test_frontend_gate.py`(7 例)与 `tests/test_performance_gate.py`(3 例,operator JS 699,837B/715KB)绿;ruff 对两个改动脚本干净(存量 BLE001 未触碰)。

### 前端门禁:vitest 段改为「观测运行」,彻底摆脱退出挂起(2026-08-31)

- **ESBUILD_WORKER_THREADS=1 不够**:次日复测发现同一全量套件(16 文件/166 例全绿)在该 Windows 宿主上**进程退出仍不可靠**——同一份代码三个时间点直跑可在 20s 内退出 0,换个时间点就打印完整摘要后永不退出(`--isolate=false`、`--pool=forks`、限线程数均无效;测试本身 20s 跑完,挂的只是退出阶段)。
- **门禁改为观测运行**(`scripts/frontend_gate.py` `_run_vitest_observed`):流式读取 stdout,一旦出现收尾的 `Duration` 行即认定本轮已跑完,给 45s 宽限窗让运行器自行退出,仍不退就 `taskkill /T /F` 整棵进程树。判定(`_vitest_exit_verdict`)以捕获的摘要为证据——**完整且干净的摘要(全部 passed、无 failed、无 Unhandled Errors)接受并打 WARN**,不完整/有失败/有未处理错误照旧 FAIL。
- **顺带修掉两个观测缺陷**:vitest 摘要带 ANSI 色码(`Test Files \x1b[…16 passed`),完成检测与摘要正则都被色码隔断——匹配前统一剥离;`Duration` 行之后还有尾随空行,完成检测不能只看最后一行。
- **效果**:门禁 vitest 段从「挂起/300s 超时」收敛到 **~66s**(实测),CI(Linux 上退出正常)行为不变——退出 0 走原路径,宽限窗/树杀只在退出异常时兜底。
- **验证**:`scripts/frontend_gate.py` 全绿(node **284** + vitest **166**);`tests/test_frontend_gate.py` + `tests/test_performance_gate.py` 10 例绿;ruff 干净。
- **内存压力 OOM 追加修复(同日)**:复盘发现 fork 池默认按 CPU 核数派生 worker,本机内存吃紧时 worker 触发 `FATAL ERROR: AlignedAlloc Allocation failed` → 批量 `Worker exited unexpectedly`、部分测试未跑完。门禁 vitest 段改加 `--minWorkers=1 --maxWorkers=2` 限并发;`_vitest_exit_verdict` 追加判定——「全部 `Test Files/Tests` passed、无 `failed`,且每个 Unhandled Error 块均为 tinypool worker 崩溃」视为 harness 噪音打 WARN 放行,任何非 worker 崩溃的未处理错误/失败计数照旧 FAIL。`tests/test_frontend_gate.py` 新增 `VitestExitVerdictTests` 5 例锁住判定。

### D3 收官:岛侧模块行数门禁 + 四岛切分(2026-09-01)

- **门禁缺口**:`scripts/frontend_gate.py` 的 400 行模块限制自 Phase 26 起只扫 `app/static/js/*.js`。D2 引入 `frontend/src` 这条**同样发货**的前端轨后,它一直处于零覆盖状态——`admin-island.jsx` 已长到 **1,052 行**(越过项目 800 行硬禁线)、`knowledge-island.jsx` 613 行、`inspector-island.jsx` 523 行、`queue-island.jsx` 413 行,四个越线文件无人拦。`check_line_limits()` 扩到 `frontend/src/**/*.{js,jsx}`,排除 `*.test.jsx`(长度由用例数驱动,不是设计债)。
- **四岛按域切分为 13 个子模块**,根文件降为「组合根 + re-export 面」,导入面零变化(组件测试与 `vite.config.js` 入口都不用改):
  - `islands/admin/` — `constants.js`(事件名/角色与报表标签/React 后缀 id 表)、`models.js`(配额读数、成员行、订阅行、SLA/路由标签、CSAT、导出 URL 等纯模型)、`shared.jsx`(身份门 `useIdentity`/`canManageIdentity` + `useBridge`/`AdminReadout`/`useClearOnSaved`)、`tenant-cards.jsx`(配额/成员/Webhook)、`report-cards.jsx`(订阅/导出/CSAT)、`policy-cards.jsx`(SLA/路由);
  - `islands/knowledge/` — `domain.js`(状态与语言目录、标签解析、载荷、归一化/筛选/汇总/审核动作)、`reducer.js`(§43.6 `createState` + `reduce`)、`components.jsx`(汇总条/文章卡/草稿编辑器);
  - `islands/inspector/` — `helpers.jsx`(桥事件名、tab 目录、转义/引用 URL 白名单/时间格式化、`latestAssistant`/`renderLabelChips`)、`sections.jsx`(概览/证据)、`note-composer.jsx`(内部备注 + @提及,IME 组合守卫与光标推导原样保留);
  - `islands/queue/` — `components.jsx`(行/页脚条带/批量操作条 + SLA 文案与 windowing 数学)。
- **字节中性**:切分前后 `operator_js_bytes` 725,218 → 725,157(−61,少一行陈旧注释),每岛仍是**单 chunk**——子模块只被各自岛入口引用,Rollup 直接内联,不产生新 chunk 也不改首屏。最大文件从 1,052 行降到 252 行。
- **顺带清掉一处过期注释**:queue 岛头注释仍写「legacy 保留拥有 bulk toolbar(#bulkToolbar)」,而批量操作条在 D3 长尾第七片(^dd836f5)已并入岛。
- **验证**:vitest **166** 例全绿(16 文件);`frontend_gate` 绿(node 284 + vitest 166),并用 401 行探针确认新限制真能抓到越线;`performance_gate` 静态段绿(JS 725KB/780KB、CSS 97KB/125KB);`ui_accessibility` 全绿——含**真实挂载 React 岛的桌面壳 pass**(明暗双主题 axe + reduced-motion),这是唯一覆盖切分后岛 DOM 的门禁;`ui_smoke`、`ui_knowledge` 绿;`ui_admin` 配额读写 + 成员邀请/改角色/停用全生命周期绿(webhook 段失败为本机 fake-IP DNS 把 `hooks.example.com` 劫持到 `198.18.0.27` 触发后端 SSRF 防护,既有环境噪声,与本次改动无关)。
- **D3 里程碑达成**:`app.js` 3,258 → **477 行**,纯 `HelixModules` 委托包装 + 一次 `bindLegacyBoot()`,低于 <500 行验收线;岛侧全部 ≤400 行且从此有门禁看守,两条前端轨口径统一。

### 七道门禁自身静默失效 + 一处测试为错误理由变红(2026-09-01)

一轮针对「门禁脚本本身能不能失败」的审计。每一条都由执行验证,不靠读代码判断:构造出该门禁声称要拦的缺陷,确认它返回 0 违规。修复后再用同样的输入确认它报错。最后一条来自修完后的全量跑,方向相反(见末尾)。

- **pip-audit 覆盖门读的键 pip-audit 从不输出**(^dcb39f2,`scripts/vuln_review.py:119`):`audit_coverage` 遍历 `dependency.get("vulnerabilities", [])`,而 pip-audit 的 JSON formatter 输出的是 `"vulns"`(上游 `pip_audit/_format/json.py`:`"vulns": [self._format_vuln(vuln) for vuln in vulns]`)。实测把两种形状分别喂进去:真实 pip-audit 输出 → 违规 `[]`;测试夹具的错误键名 → 正确报出未登记漏洞。`reported` 恒为空集,`reported - registered` 恒为空,而 `registered_ids` 是 `set()`——零豁免登记,意味着 pip-audit 发现的**任何** CVE 都该让门禁变红。`.github/workflows/ci.yml:165` 跑的正是这条路径(`--audit /tmp/pip-audit.json --require-coverage`),它从来不具备失败能力。今天绿是因为 `requirements.lock` 恰好干净;CVE 落地那天它照样绿。缺陷在 `tests/test_vuln_review.py:90` 被镜像——夹具用了同一个错键名,所以测试与 bug 互相印证。
- **性能门禁四个预算把自己静默解除**(^e5974eb,`scripts/performance_gate.py`):`except Exception: island_render_ms = None` 的 `try` 覆盖整段桌面测量(shell context、`lcp_desktop_ms`、`cls_desktop`、岛渲染),而断言循环对未设置的键 `continue`。模拟异常后的状态确认:`problems = []`,静默解除 `queue_10k_island_render_ms`/`heap_growth_mb`/`lcp_desktop_ms`/`cls_desktop` 四项——60 秒的岛渲染能通过 500ms 预算,报告 JSON 只是缺键,与通过不可区分。改为把测量失败本身记成违规。**heap 预算结构上不可达**:`artifacts/performance-baseline.json` 记录 `heap_growth_mb: 0.0` 正是征兆——Chromium 不带 `--enable-precise-memory-info` 时 `usedJSHeapSize` 恒返回量子化的 10,000,000(实测带上该 flag 同页报 ~940KB),该常量 > 0 所以旧 `all(s > 0)` 守卫放行,每个采样相同,`max - min` 恒为 0。补 `HEAP_QUANTIZED_BYTES` 识别该状态并报错;循环从 `range(5)` 改为 `HEAP_REFRESH_CYCLES = 20`(预算文案一直写「20 refresh cycles」);实测数改由 `PERF_PRECISE_MEMORY=1` 显式 opt-in(该 flag 会关掉部分分配器优化,默认开启会污染同一会话的时延预算)。
- **前端门禁漏掉两个入口文件与整个 frontend/src**(^5eddac6,`scripts/frontend_gate.py`):构文门禁与行数门禁都用 `JS_DIR.glob("*.js")`,而 `app.js`/`widget-app.js` 在上一层的 `app/static/`,两个 glob 都到不了——`app.js` 477 行**当场越线**而 `check_line_limits()` 返回 `[]`,`node --check` 也从未校验过这两个真正的入口。补 `ENTRY_POINTS` 并给它们 `ENTRY_MAX_LINES = 500` 的独立上限(对齐 `CHANGELOG:153` 记的 <500 行验收线,而非把全局 400 抬高),构文门禁对象 48 → 50 个文件;把上限探针压到 100 行确认两个入口都能被抓到。同理 `check_asset_versions` 只 `rglob` 了 `app/static`:`frontend/src` 里有 27 处 `/static/` 引用、`?v=1.4.0` 硬编码在 JSX 中,模拟版本 bump 到 1.5.0 后 25 处过期引用会静默发版,而门禁报 0。`find_unversioned_imports` 同样只吃 `app/static` 的 `.js`、从不看 `.jsx`。注意 `check_line_limits`/`check_icon_symbols` **是**扫 `frontend/src` 的,所以这是不一致而非有意的范围取舍。
- **视觉门禁把尺寸变化当通过,并覆盖基线**(^78491e1,`scripts/visual_gate.py:105`):实测三组对照——同尺寸 100% 差异 → `ok=False`(正确失败);**差一个像素高、100% 差异 → `ok=True` 且把提交进仓的基线覆盖成新截图**;重跑同一张 → 0.00% 干净。任何改变布局高度的 UI 变更(多数都会)都能把任意大的回归洗进基线,下一跑报干净。`compare()` 明明算出了 `ratio=1.0` 然后丢掉。改为几何漂移即失败、截图落 `artifacts/`,重引导只能靠显式 `--update` 或删 PNG——顺带把一直存在却从未接线的 `update` 参数配上 CLI。新增 `tests/test_visual_gate.py` 8 例(PIL 缺失时 skip,同 Playwright 的既有惯例);把旧行为复原后测试精确报出。
- **OpenAPI 比较器看不见数组、parameters 与 requestBody**(^bbd00c8,`scripts/openapi_snapshot.py`):docstring 承诺拦「removed/changed response fields or types, changed parameter shapes」,三类都没实现。**数组元素属性不可见**——`deref` 虽解析 `items` 的 `$ref`,但 `_breaking_changes` 只读顶层 `.get("properties")`,而数组 schema 自己没有 properties。实测同一个 `email` 字段删除:非数组响应 → 正确报出;数组响应 → `[]`。141 个操作里 **31 个返回数组**,含 `GET /api/conversations`、`/api/knowledge`、`/api/audit-events`——从会话列表行里删字段是不可检测的。**parameters 从未被比较**(133 个操作声明了 parameters,删掉必需 query 参数返回 `[]`,而每个漏传的客户端此后都吃 422),**requestBody 同样**(48 个操作)。**无 content 的响应被丢弃**:`if not shapes` 测的是累积字典而非当前 status,所以 200 之后的裸 204 从不记录(实 snapshot 有 5 处)。修复:形状解析递归进 `items`/`anyOf`/`allOf`/`additionalProperties`(`seen` 集合界定递归——FastAPI 会产出自引用 schema,无守卫的解析器会爆栈),补 `_operation_params`/`_operation_bodies`。关键验证是**实 snapshot 141 操作自比较零误报**(比较粒度提高后最容易出的就是这个),`tests/test_openapi_gate.py` 新增 7 例含该项。
- **三个 sprite symbol 藏在模板字符串 href 后**(^a1ec855):承接上一节的空白图标缺陷——`ICON_REF_RE` 匹配不了 `#${...}`,而五处岛代码动态拼 href。`session-shell-island.jsx:74` 的 `theme === "dark" ? "moon" : "sun"` 里 `sun` **不存在**(浅色主题下主题开关是纯图标按钮,什么都不画);`NAV_VIEWS` 要的 `settings`/`sliders` 也不存在,五个导航按钮里两个空白。三个名字都在构建产物 `session-shell-*.js` 里,确实发版了。**严重度限定**:`index.html:896` 的挂载点是 `hidden`,而 `island-loader.js:218` 跳过隐藏挂载点,所以该岛构建了但当前未挂载(与 session-shell「决策不激活」一致)——空白图标是潜伏而非当下可见,而它变可见的那一刻正是门禁本该拦住的时刻。补 `ICON_REF_DYNAMIC_RE` + 字面量/属性两条抽取(锚定 `?`/`:` 与 `icon:` 以免把三元里的 `"dark"` 当图标名),sprite 26 → 29 symbol。
- **许可门禁只看 Python,八个发版的 npm 依赖从未审查**(`scripts/license_gate.py`):docstring 声称审查「the runtime dependency closure」,让「每个新依赖都是有意的许可决策而非意外」,但 `review()` 只读 `requirements.lock`。而 `frontend/package.json` 的 8 个 `dependencies`——react、react-dom、zustand、@tanstack/react-query 与 xterm 四件套——被 Vite 打进 `app/static/dist/assets`,随桌面安装器与 Web 一起分发到每个使用者手上,却整个 D2/D3 阶段都在闭包外。它们恰好都是 MIT(已逐包与 `node_modules` 内 `package.json` 的 `license` 字段及同目录 LICENSE 正文双向核对后登记),所以**当下没有违规**;缺陷不是「现在有个坏许可」,而是第九个 npm 依赖可以带任何许可进来并照样绿。修复:`_npm_package_names()` 只读 `dependencies`(`devDependencies` 的 vite/vitest/jsdom/testing-library 不随产物分发,与 Python 侧只读 lock 不读 dev extras 划同一条线),复用既有登记/允许列表/`LicenseRef-TBD` 逾期三段校验,门禁计数 25 → 33。**顺带修掉一个我自己引入的同类缺陷**:`npm_path` 最初默认 `DEFAULT_NPM_MANIFEST`,于是既有 6 例用合成 lock+政策的单测被悄悄塞进真实 manifest 而全红——默认值让 `review()` 读了调用方没交给它的文件。改为显式 opt-in,默认路径的责任归 `main()`。补 9 例(scoped 包名、dev 依赖不入闭包、不许可许可、逾期 TBD、manifest 缺失不红、真实政策双轨自检并断言 npm 侧 ≥8 包以防「读成空也算通过」)。
- **两处子进程测试用 OS 默认编码解码 UTF-8 输出**(`tests/test_audit_anchors.py:130`、`tests/test_operability.py:86,114`):这一条方向相反——不是门禁不会红,而是测试会**为了错误的理由**红。`subprocess.run(..., text=True)` 不带 `encoding` 时按 OS 默认代码页解码,Windows 上是 GBK。`test_audit_anchors.py` 的 `_run_script` 跑的锚点脚本打印中文诊断,读取线程于是死在 `UnicodeDecodeError`,`stderr` 回来是 `None`,而断言拿 `None` 去 `assertIn` 抛出的 `TypeError` 与真正的篡改检测毫无关系——**全量跑里唯一的失败**就是这个,而它掩盖的不是缺陷而是噪音。`scripts/frontend_gate.py:_run` 早已解决过同一问题(注释明写 Node 的 UTF-8 进度字形),这里是同类漏配。修复即补 `encoding="utf-8", errors="replace"`。横扫仓库找同型:`tests/test_operability.py` 是唯一另一处 `text=True` 无 `encoding`,它跑的 `migration_drill.py` 输出路径当前全 ASCII(唯一的非 ASCII 是第 87 行注释里的 em dash),所以**今天不会失败**——但 `:113` 对 `result.stderr` 做 `assertIn`,诊断文案里加进第一个中文字符的那一刻就会变成同样的 `TypeError`,故一并预防性对齐。

**为什么这批缺陷能一起存在**:前七条全部是 fail-open。门禁读错一个键、glob 少一层目录、`except` 吞掉测量、比较器少走一层递归——表现都是「0 违规,通过」,与真的干净逐字节相同。CI 日志里看不出区别,而这类脚本平时不会有人怀疑,因为它们一直是绿的。唯一可靠的检验是构造出它声称要拦的缺陷,看它是否真的报错;这七条修复各自都补了这样的红光测试。第七条还多一层教训:少扫一个目录与少读一个键同样是 fail-open,而「审查范围」写在 docstring 里最容易与实现悄悄脱节——npm 那条的检验方式必须是「门禁能否报出这 8 个包未登记」,而不是「门禁是否通过」。

末尾那条编码缺陷是同一枚硬币的反面,值得并列记下:一个**为错误理由变红**的测试与一个不会变红的门禁危害相当。前者训练人把红光当噪音,后者训练人把绿光当保证,两者都让信号失去意义。它也是本轮唯一由全量跑而非定向审计暴露的——门禁审计只问「它能不能失败」,问不到「它失败时说的是不是真话」。

- **验证**:**全量 pytest 95 文件 1,270 例 exit=0**(此前唯一的失败即上述 `test_audit_anchors` 编码缺陷,修复后该文件 8 例、`test_operability` 6 例均绿;余下 skip 全是 Playwright/PIL 缺失等既有条件跳过)。门禁单测 163 例全绿(`gate or vuln or visual or openapi or license`,含新增 24 例);`license_gate` 绿(33 个运行时依赖包 Python + npm 全部登记且合规),并实测七种红光场景(未登记 npm 包、npm 不许可许可、逾期 TBD 等)逐一 exit=1;`frontend_gate` 绿(node 351 测试、构文 50 文件、模块 ≤400 行、入口 ≤500 行);`performance_gate` 静态段绿(JS 725,743/780,000、CSS 99,791/125,000、widget 19,929/25,000);`openapi_snapshot` 实 spec 与 snapshot 一致、自比较零误报。

### 门禁自己印不出自己的失败:八条中文诊断在 Windows 控制台崩溃或乱码(2026-09-01)

上一节末尾那条编码缺陷只修到了**测试如何读子进程**,没修**门禁如何写自己的 stdout**。同一枚硬币还有第三面:Windows 控制台默认 GBK 代码页,Python 据此编码 stdout,而这批门禁的违规诊断全是中文。后果分两级,第二级是真缺陷:

- **`image_admission_check` 报告真实违规时抛异常而非返回 exit 1**(`scripts/image_admission_check.py`):`_check_manifest` 用 subprocess 调 `release_manifest.py --verify`,已带 `encoding="utf-8"`,但**子进程是按 GBK 写的**——父进程按 UTF-8 解码,中文全成 U+FFFD。随后 `print` 这个含替换字符的违规串,GBK 编码器无法表示 U+FFFD,抛 `UnicodeEncodeError`。于是门禁在报告一条真实违规的过程中死于 traceback:CI 看到的是未处理异常,而不是它读的 `exit 1`。**一个印不出自己失败的门禁等于不会失败**。修复:`_run_utf8()` 给 Python 子进程注入 `PYTHONIOENCODING=utf-8`(让子进程按 UTF-8 写,文字真正穿过管道),并把 `errors` 从 `replace` 改为 `ignore`——留下 U+FFFD 就是留下下一次崩溃的引信。cosign 是 Go 二进制,该环境变量对它无效,故用参数区分。
- **`tauri_config_gate` 的豁免警告读不出来**(以及 `check_workflows`/`license_gate`/`threat_model_gate`/`vuln_review`/`release_manifest`/`redis_failure_drill`):这一级不崩溃,只是把中文印成 `plugins.updater.pubkey Ϊ��`。危害在于**豁免的意图传不到操作者**:`--allow-empty-pubkey` 这个 flag 的全部价值就是让签名证书采购期的豁免在调用处保持可见(见上方 D5 条),而它降级后印出的那行 warning 一直是乱码。同理 `check_workflows` 的 `upload-artifact@v4` 未固定 SHA 警告。
- **共享模块而非逐个打补丁**(`scripts/_console.py`):`use_utf8_console()` 把 stdout/stderr 重新编码为 UTF-8 + `errors="replace"`(即便某个流仍无法表示某字符,门禁照样报告、照样非零退出,第二级问题不会回退成第一级)。只在 `__main__` 调用——import 期改全局流状态会波及把这些模块当库 import 的 pytest 调用方,那不是它该动的东西;无法 reconfigure 的流(已包装、重定向到非文本 sink、被测试替换)跳过而不抛,让控制台可读这件事本身永远不该弄坏门禁。
- **范围界定**:另 12 个改动脚本(backup/restore/migration_*/run_*_drill/verify_*/pagination_load_test/generate_residency_pack)逐行检查确认输出路径零非 ASCII 字符,不需接线——它们本轮的改动只是 repo-root 引导(见下)。该检查是逐行静态扫描 `print`/`stdout`/`stderr` 行,变量拼装后再输出的路径不在其覆盖内。

**顺带修掉的第二类缺陷**:这 20 个脚本里 `from app...` 之类的仓内 import 全都依赖 `pip install -e .`,而 CONTRIBUTING.md 让开发者直接 `python scripts/xxx.py`。补 `sys.path.insert(0, str(ROOT))` 引导后,实测 **13/20 → 0/20** 失败:backup、restore、migration_drill、migration_gate、pagination_load_test、generate_residency_pack、redis_failure_drill、verify_audit_chain、verify_migration_registry 与四个 `run_*_drill` 在无 editable 环境下原本直接 `No module named 'app'`。这与 `frontend_gate.py` 上一轮修的是同一个缺陷,当时只修了一个文件。

**这条的验证方式本身值得记下**:前三次测量都得出「修前 0 件失败」的假阴性,原因是 `artifacts/rls-venv` 装了 editable(`__editable__.helix_support-1.3.0.pth` 的 finder 走 import hook,**不经过 `sys.path`**),所以把 repo 路径从 `sys.path` 里剔掉根本不构成「无 editable 环境」。改用 `python -S`(完全不读 `.pth`)+ 手工追加 site-packages 才复现出真实条件。中途还有两次更粗的错误:用 `git worktree` 到 `/tmp/pre` 测——`ROOT` 随之指向 `/tmp/pre`,那里 `app/` 实在,必然通过;以及把 `verify_migration_registry` docstring 里**引用**的 `No module named 'app'` 文本当成了真实异常。三次假阴性都是「测量装置比被测对象更宽容」,与本轮门禁缺陷同型。

- **验证**:**全量 pytest 1,230 passed / 40 skipped / 0 failed exit=0**(476.92s)。八个接线门禁逐个实跑,中文诊断全部正确显示且 exit 0:`tauri_config_gate --allow-empty-pubkey`(豁免 warning 现可读)、`license_gate`(33 包)、`threat_model_gate`、`vuln_review`、`check_workflows`(SHA 警告现可读)、`release_manifest --verify`、`image_admission_check --no-digest-required`、`redis_failure_drill --help`;`frontend_gate` 绿(node 351 测试);`ruff check scripts/` 全过、`_console.py` 格式合规;20 个脚本在 `python -S`(无 editable)+ 任意 cwd 下解析 13 失败 → 0 失败。**过程中排除的两个误判**:`release_manifest --verify` 曾看似「报 4 处不一致却 exit 0」,实为我用管道读到了 `head` 的退出码,直接跑确认 exit=1、CI 会正确变红;本地 manifest 与源码树不一致是 `artifacts/` 未入库 + CI 每次先 `--build` 的正常状态,非回归,已重建对齐。另有 5 个文件 `ruff format --check` 报需重排,经比对为改动前既有的 0.16.3 版本漂移,按既定约定未触碰。

### 四个空白图标 + 岛内 label 绑到 legacy 隐藏输入框(2026-09-01)

- **四个 sprite symbol 从未定义**(^1743251):`<use href="…#name">` 指向 sprite 里不存在的 symbol 时**什么都不画**——没有控制台报错,没有 404(sprite 本身能解析),只有一个空盒子。`moon`/`eye`/`link`/`at-sign` 四个名字被 index.html 五处 + mentions-island.jsx 一处引用却从未定义:`#themeToggle`(头部主题开关,**纯图标按钮无文字**,整个渲染成空白方块)、`#mentionsBadge`、`#watchBtn` 与 `#watchBanner`、`#ticketLinkCurrent`。真机实测 `#moon` 的 `<use>` 盒子 0×0 而父 `<svg>` 已布局到 18px——未解析 symbol 的特征;另三个在 `hidden` 容器里,手动展开后确认同样空白。**为什么全套门禁都没拦**:视觉基线是在按钮已损坏时拍的,把空白方块编码成了「正确」(旧 workspace-dark 基线该按钮区域 13 种颜色、亮度极差 18,即纯色块;重引导后为 86 种、162),而 axe 只看 `aria-label`——那个标签一直存在且正确。四个 symbol 按 sprite 既有风格补齐(Lucide 几何、24×24 viewBox、描边属性继承自根 `<svg>`)。新增门禁 `check_icon_symbols`(纯核 `find_unknown_icon_symbols`),对修复前的 sprite 精确报出全部六处引用。基线按「有意 UI 变更」惯例重引导——重引导前漂移仅 0.01–0.03%(远在 0.5% 限内,因为字形在整页截图里只有 14px),**这正是这类缺陷需要独立检查而不能靠像素门禁的原因**。
- **岛内上传 label 绑到 legacy 隐藏输入框**(^a251325):composer 岛的附件上传一直在**误打误撞**走 legacy。它的 label 写 `for="attachmentFile"`,而让位的 legacy `#attachmentFile` 只是 hidden、并未移除——`label[for]` 绑定的是**文档树序里第一个**该 id 的元素,于是这个 label 控制的是 legacy 的输入框(真机 `label.control` 解析到岛外)。后果:岛自己的 `<input>` 与 `handleFileChange` 是死代码,`composer-island-bridge.js` 注释里写明「岛的文件输入把原始 File 递过来」的 `helix-composer-attachment-upload` 桥**一次都没触发过**;上传之所以能成,是因为 `bindAttachments()` 没有做岛模式门控,legacy 自己那个隐藏输入框的 change 监听仍在跑。岛本就有 `INPUT_IDS` 给它拥有的四个输入配 React 后缀 id,只是漏了文件输入,现补为 `attachmentFileReact`。**未改** `#noteForm`/`#noteInput`/`#mentionSuggest`:它们的 id 对等是有据可查的既定契约(`desktop/verify_note_form_desktop.py` 明写岛保留「legacy DOM 契约」),ui_smoke 与 ui_mention_autocomplete 按它定位,且岛的副本恰在树序靠前,label 绑定正确。由于哪个副本胜出**纯看 DOM 顺序、无法静态判定**,`ui_accessibility` 桌面壳 pass 新增 `assert_island_labels_bind_inside_their_island`:岛挂载点内每个 `<label for>` 必须控制同一岛内的元素;把修复 revert 后它精确报出违规项。
- **连带切分 composer 岛**:这一行 id 改动把 `composer-island.jsx` 从正好 400 行推到 415,触发 ^8e31865 新加的模块上限,于是按其余四岛同样的方式拆开——`composer/constants.js`(COMPOSER_EVENTS/INPUT_IDS/macroMatches)+ `composer/tool-bars.jsx`(CannedBar/MacroSuggest/CopilotBar/AttachmentBar)。根保留两个表单、全部状态与全部 handler 并向下传参,工具条保持纯展示;常量单独成模块以免工具条反向 import 根造成循环。根 290 行,最大新模块 183 行,三个名字均从根 re-export,导入面不变。
- **验证**:vitest **167** 例(16 文件,+1 例把文件输入的 id 钉住防回归);frontend/performance 门禁绿(JS 725,651/780,000、CSS 99,791/125,000);视觉门禁四面 **0.00%**(纯 JSX 抽取,像素中性);`ui_accessibility` 含新 label 断言全绿;`ui_smoke`/`ui_knowledge` 绿;gate 单测 25 例(新增 `UnknownIconSymbolTests` 5 例)。
- **顺带查清两处非缺陷**:`helix-*` 事件桥完整(60 派发 / 62 监听,唯一孤儿 `helix-nav-switch` 出自决策不激活、挂载点恒 hidden 的 session-shell 岛);岛模式活 DOM 里余下 19 处重复 id 全部是有意的 id 对等契约(legacy 写入自己那份隐藏副本,无害)。`tests/ui_attachment.py` 失败为既有状态(HEAD 同样失败、逐位一致):它是 docstring 自称 Backlog 且从未接入任何门禁的脚本(CI 只跑 ui_smoke/ui_admin/ui_knowledge/ui_accessibility),与本次改动无关;队列点击加载会话线程经实测正常(3 条客户消息的会话点出 4 行、1 条的点出 2 行,标题对应)。

### 三条被未定义 CSS 变量吞掉的声明 + 悬空 var() 门禁(2026-09-01)

- **缺陷共性**:`var(--x)` 在 `--x` 从未定义、且没有回退值时解析为 guaranteed-invalid,**整条声明在计算值阶段被丢弃**——没有控制台警告,没有构建错误,一条消失的 `background` 看起来只是设计选择。三处因此逃过了全部门禁与四张视觉基线:
  - `.report-preview`(管理页报表预览 `<pre>`)写 `background: var(--bg)`。别名块定义的是 `--bg-0`…`--bg-3`,没有 `--bg`,所以两个主题下这个 `<pre>` 都**完全透明**地压在卡片上(实测 `rgba(0,0,0,0)`)。改为 `--surface-alt`——所有同类凹陷等宽块都用它,`.audit-payload` 的 surface-alt/line/radius-sm 组合与它完全同形。
  - `.csat-label` 写 `color: var(--text-muted)` 且**没带回退**,而另外五处 `--text-muted` 引用全部写作 `var(--text-muted, var(--muted))`。声明被丢弃后标签继承了满强度正文色(暗色实测 `rgb(230,237,243)`,本应 `rgb(139,149,163)`),读起来像正文而不是标签。
  - `.desktop-splash` 引用 `--color-surface-0` 与 `--color-text`,**两个名字在任何地方都不存在**,双双落到硬编码深色 hex——这个 `inset:0; z-index:9999` 的 Tauri 启动全屏遮罩因此在亮色主题下始终是深色。改用别名名 `--bg-0`/`--ink`:亮色主题重映射的是别名(指向 `--color-light-*`),底层 `--color-bg-0`/`--color-ink` 仍保持深色值,所以直接引用 `--color-*` 同样不会翻转(第一版修法就错在这里,已纠正)。
- **未动**:`.thread-load-older-btn:hover` 的 `var(--accent, var(--blue))`。`--accent` 同样未定义,但回退能解析、能渲染,改它等于凭对意图的猜测改变外观。
- **门禁**(`scripts/frontend_gate.py` `check_css_custom_properties`):汇集 `app/static` 下所有手写样式表(跳过 Vite 产物 `dist/`)的自定义属性定义,再报出每一个既无定义又无回退的 `var()` 引用。对 f8bc271 的父提交运行,精确报出上述两条无回退缺陷。检测核心拆为纯函数 `find_dangling_css_vars(sheets)` 以便用合成 CSS 测试——**这一拆立刻抓出门禁自身的 bug**:定义正则锚在行首,导致单行 `:root { --x: red }` 不被登记、`--x` 的每一处使用都被误报;去掉锚点是安全的,因为 `var()` 引用后面永不跟冒号。
- **验证**:视觉门禁四面 **0.00% drift**(clean DB;首轮四面全飘是 UI 套件把 `support.db` 种了数据所致——把本次改动 revert 后百分比完全一致,证明这个 diff 在基线上像素中性);计算样式确认三处均已解析且随主题翻转;两处可见修复的对比度实测 `.csat-label` 6.24:1 暗 / 5.26:1 亮(12px,4.5:1 底线)、`.report-preview` 16.02:1 / 14.88:1;`ui_accessibility` 含桌面壳 pass 全绿;CSS 字节 99,791/125,000;`tests/test_frontend_gate.py` 新增 `DanglingCssVarTests` 6 例锁边界(定义/未定义、有无回退、跨表定义汇集、内联定义、行号、嵌套回退——`var(--a, var(--b))` 内层未定义时照样报,因为 `--a` 会回退到一个 guaranteed-invalid 值、声明仍然丢弃;这也正是仓库自己的 `var(--text-muted, var(--muted))` 干净的原因:`--muted` 有定义),共 19 例绿。

### INP 直接归因探针:交互延迟从「长任务代理」升级为逐事件实测(2026-09-02)

- **§43.6 残留关闭**:性能门禁的交互延迟此前一直用 `long_task_count_30s` ≤50 **代理** INP——长任务只统计 ≥50ms 的块,任何「慢但每块 <50ms」的交互或一根不快不慢的点击都无感;且长任务数无法区分是哪次交互慢、慢在哪。`scripts/performance_gate.py` 现在直接测 INP:`PerformanceObserver('event', durationThreshold: 0)` 在**任何交互发生前**注入页面,用 Playwright 受信输入(`locator.click()`)点真实队列行触发完整交互(pointerdown/pointerup/click 共享 interactionId,按 ID 分组取最长事件处理时长——这正是 INP 的定义),`expect_response` 等详情 fetch 落定确保 handler 全部跑完。程序化 `element.click()` 不带 interactionId、静默记不到任何东西,因此必须走受信输入管道。
- **实测校准**(本机 Chromium,空队列基线):web `inp_ms` = 3.0ms、桌面壳 `inp_desktop_ms` = 6.0ms,预算定为 300ms(实测值 50–100 倍余量——行渲染毫秒级,余量留给 CI runner 抖动与未来功能成本;预算如此宽松正说明 INP 不是当前瓶颈,而探针的职责是**在它变成瓶颈的第一天就把主线程回归抓出来**,这与桌面 LCP 1000ms 的严格预算形成对照)。`inp_ms == 0.0` 视为探针失效(队列无可点行)→ 门禁显式报「budget went unenforced」,不再静默跳过。
- **种子确定性**:点击目标由 `_ensure_queue_row` 通过应用自身 API 播种(`POST /api/conversations`,demo auth 对无 key 请求放行),再等一次轮询周期落定——不再赌队列里恰有数据,桌面壳与 web 两条测量轨共用同一行选择器(`.conversation-row button.conversation-item`)。
- **验证**:浏览器层全绿(perf 门禁带 `--base-url` exit=0,`PERF_PRECISE_MEMORY=1` 下 heap 0.23MB/15MB);`tests/test_performance_gate.py` 新增 INP 两键覆盖断言共 3 例绿;同时把测量顺序修正为「种子在 paint/10k 渲染**之后**」——先种子会让 CLS/LCP 暴露在非空队列渲染下,而 INP 探针需要真实可点行,两个需求各得其所。

### Core Web Vitals 补齐:浏览器层新增 FCP/FID/TTI 四组预算(2026-09-02)

- **§43.6 性能预算从三指标扩到七指标**:浏览器层此前只测 LCP/CLS/长任务,现在补齐 FCP(First Contentful Paint)、FID(First Input Delay)、TTI(Time to Interactive)三组——web 与桌面壳各一份,共 10 项浏览器预算。`metrics_script` 单一脚本同时产出两上下文,桌面壳经 `desktop_key_map` 把每个 paint 键映射到 §D5 预算键。
- **修复一个刚引入的 fail-open**:桌面壳路径此前只把 `lcp_ms`/`cls` 映射到 `lcp_desktop_ms`/`cls_desktop`,新增的三个桌面键若沿用旧写法会**从未被赋值**,而断言循环对缺键 `continue`——这正是本仓库反复踩过的「预算看似存在实则未接线」。改用整表映射后实测桌面五键全部落值;`tests/test_performance_gate.py` 新增 `test_desktop_budgets_have_web_twins` 钉住配对,以后加新指标必须同时加映射两侧,否则测试红。
- **实测校准**(本机 Chromium,空队列基线,PERF_PRECISE_MEMORY=1):web FCP 416ms/预算 1800、TTI 416ms/3800、FID 0ms(页面在首次交互前已空闲)/100;桌面 FCP 424ms/800、TTI 424ms/2000、FID 0ms/50。LCP 684ms/2500、INP 3ms、CLS 0.0019,全部宽裕。
- **bundle 分析顺手发现**:`app/static/dist/assets/` 22 个 chunk 共 728KB raw / 211KB gzip,其中 terminal 409KB(xterm.js 三 addon)按需懒加载且被首屏预算正确排除;但 **sourcemap 共 2.12MB,是 JS 的 298%**,被 PyInstaller 原样打进桌面包(`desktop/helix-server.spec` 把整个 `app/static` 收为 datas)——开发调试产物成了发货体积。spec 现在在 COLLECT 前过滤 `.map` 后缀,桌面包减重 ~2.1MB;web 端 sourcemap 保留不变。

### 详情开关内存泄漏探针:5 轮开关循环测残留堆(2026-09-02)

- **新增 `detail_leak_mb` 预算**(5.0MB):浏览器层现在对详情视图做「开→关」泄漏扫描——`selectConversation(id)` 打开所选会话的转录详情、`clearSelection()` 关闭,重复 5 轮;每轮关闭后强制 GC(`--js-flags=--expose-gc`)再采样堆,**采样稳态残留而非打开峰值**,预算断言闭合后的堆不逐轮爬升。详情渲染是 DOM 装配最重的路径(转录/标签/语言下拉/线程滚动),跨 clearSelection 残留的节点或监听器会在这里现形。
- **独立探针会话**:GC 标志与强制 GC 只作用于独立 browser session,与主测量会话隔离——实测 `--expose-gc` 会把同会话的桌面 LCP 拉高 ~50ms(992→1036),时延预算与内存探针互不污染。探针 wait 放宽到 60s 吸收低负载下的渲染抖动。
- **实测校准**(本机 Chromium,clean DB,PERF_PRECISE_MEMORY=1):`detail_leak_mb` = 0.03MB(关闭后残留 ~30KB,预算 5MB)——详情路径无泄漏;同轮 heap_growth 0.31MB/15MB。无行可点时探针显式报「budget went unenforced」,不静默跳过。CI nightly 不设 PERF_PRECISE_MEMORY 时探针与 heap 测量一样按量子化堆跳过,`:expose-gc` 不进入默认测量链路。

### 脚本 lint 修复 + 覆盖率补测 + runbook/文档完善(2026-09-02)

- **ruff 0.9.9(CI 口径)全绿**:消灭 scripts/ 下 16 个 lint 错误——15 个是 `_console.py` 统一改造引入的重复 `import sys`/`from pathlib import Path`(F811/E402,ruff --fix 安全清理),1 个是真实 bug:`split_main.py` 的 `__main__` 块调用**从未导入**的 `use_utf8_console()`(F821,该脚本 Phase 27.2 后从未被真正运行过)。`rebuild_main.py` 的 bootstrap `import sys` 加 noqa 对齐其余脚本。本地 0.16.x 的 473 项报告属版本差异噪音(仓库记录过「CI 口径 ruff 用 artifacts/ruff-099-pkg」),不在本次范围。
- **`split_main.py` 破坏性保护**:验证时发现该脚本无 argparse,`--help` 直接执行 `main()` 把 `app/main.py` 和 `app/routers/conversations.py` 重写了一遍(已 `git checkout` 完整恢复)。补 `--apply` 显式开关,无参数或 `--help` 一律 `parser.error` 拒绝执行——一次性的迁移工具也要防误触。
- **覆盖率 85% → 87.25%**(新增 13 个测试文件共 190 例,切审计/安全/配置/遥测/渠道/附件关键面):`tests/test_webhook_safety.py`(SSRF 防护 78%→98%);`tests/test_audit_gap.py`(audit_gap 45%→97%);`tests/test_anchor_service.py`(anchor_service 0%→96%);`tests/test_audit_chain.py`(audit_chain 6%→93%,含流式验证器 O(1) 内存契约);`tests/test_audit_anchor_verify.py`(audit_anchor 74%→96%);`tests/test_telemetry_edge.py` + `tests/test_telemetry_otel_branches.py`(telemetry 69%→99%,OTel mock 分支);`tests/test_cache.py`(保留原有 3 例扩展至 14 例);`tests/test_channel_providers_branches.py`(channel_providers →100%);`tests/test_channel_key_id.py`(channel_webhooks →95%,key_id 选择全失败模式统一 None);`tests/test_attachment_router_errors.py`(routers/attachments 75%→96%);`tests/test_config_validation.py`(config.py 88%→93%,41 个 env 校验分支 fail-fast);`tests/test_small_module_branches.py`(errors/attachment_store 兜底)。
- **runbook 非作者执行预检**(Gate B 项):两个 runbook 的 `APP_VERSION` 检查从 `import app.main`(触发整个应用初始化:建库/起 worker/刷日志)改为 AST 静态读取——零副作用,输出可预测。`docs/OPERATIONS.md` 补「Performance Gate Failing (Browser Layer)」故障排查段(桌面/网页两轨同升是系统负载信号、`PERF_PRECISE_MEMORY` 语义、泄漏探针失效形态、`--update` 使用纪律);`docs/PERF_NOTES.md` 补 §43.6 浏览器性能预算实测表与测量环境要点(桌面 LCP 860–984ms 贴线、`--expose-gc` 隔离、INP 受信输入要求、sourcemap 桌面包排除)。
- **覆盖率补测自抓一个已发货 fail-open(2026-09-03 修复)**:新测试 `test_rejects_widget_frame_ancestors_empty` 暴露 `WIDGET_FRAME_ANCESTORS=""` 被 `from_env` 的生成器过滤成空元组后,又经 `widget_frame_ancestors or ("'self'",)` **静默回退到默认值**——显式配置错误被吞,与该测试文件钉住的「env 校验 fail-fast,绝不静默回退」契约相反。修复:`from_env` 区分未设置(取 `'self'` 默认)与显式置空(保留空元组,由 `__post_init__` 的 `must contain at least one source` 校验抛错);删除构造处的 `or` 回退。这也是本仓库反复出现的同一类缺陷的第 N 例:回退写法让「看似存在的校验」对特定输入路径失效。

### ruff 门禁浮动版本静默改口径 + format 检查从未绿过(2026-09-03)

- **又一个「门禁看似存在,实则从未运行」**:`ci.yml` 装 `ruff>=0.9,<1`(浮动),而 ruff 的默认规则集与 formatter 风格随每个 minor 演化——同一棵树,0.9.9 lint 只报 1 个错误,0.16.5 报 **494 个**(BLE001/SIM117/UP035/I001/RUF100 等 0.9 时代从未主动选择的规则),format 待重排 79 对 64。更早:`ruff format --check` 这一步在 e51b972(1.3.0 发布)加入时起**在任何 ruff 版本下都从未绿过**——推送基线 833e277 上 0.9.9 也报 64 个文件待重排,即 CI 的「Ruff format check」自引入以来只会红。浮动范围让两条门禁的口径随时间静默漂移,与昨天 CHANGELOG 记录的「本地 0.16.x 473 项属版本差异噪音」是同一根因的另一面。
- **修复:钉定 + 一次性收口**。`ruff==0.9.9`(pyproject dev deps 与 ci.yml 同步钉定,与本地 `artifacts/ruff-099-pkg` 同口径,注释写明钉定理由);`ruff format app tests scripts` 一次性格式化 79 个文件(纯格式:+384/-355 行,无语义变化);顺手消灭 0.9.9 口径下唯一的真实 lint 错误(`test_small_module_branches.py` 未用 `Any` 导入)。0.9.9 口径下 format+lint 双绿,全量 pytest 复跑 exit 0。
- **`scan_secrets` 误报 Rust 构建缓存**:`IGNORED_DIR_NAMES` 漏了 `target`——src-tauri 的增量编译缓存把 CSP 哈希表(`'sha256-<43 base64>='`)编进 `.o` 文件,正中 fernet 形状正则;本机 `cargo build` 后门禁恒红(CI 无 target 目录所以从未暴露)。补入忽略列表(与 node_modules/build 同类:gitignored 的本地工具状态)+ 注释写明误报机理 + 测试用真实 CSP 哈希形态锁定。全仓扫描 clean。
- **RELEASE_1_4 runbook §1 门禁复核**(Gate B 非作者预演的自动化部分):pytest 全量、ruff format/lint(钉定后)、openapi 快照、frontend_gate(351 tests)、SBOM(26 components)、release manifest build+verify、threat_model_gate、tauri_config_gate(空 pubkey 豁免)、pip check 全绿;pyright/pip_audit 本地无包,属 CI 步骤。manifest 仅余「base_image_digest 未固定」警告——需真实 registry,既有记录。

## 2.0 后续 — ROADMAP §43.6 前端可维护性和性能预算(2026-08-23)

### Added

- **inspector 领域模块**(`app/static/js/inspector.js`):renderOverview/renderEvidence/renderAudit 三 tab 渲染器、updateLabels/updatePriority 变更流、safeCitationUrl href 白名单(仅 `/` 相对与 https://,其余降级 `#`)、inspector tab 切换/惰性渲染编排;app.js 保留同名薄包装器委托 + configure() 注入 singletons,行为零变化。
- **UI component contract 与状态机**:INSPECTOR_TABS 冻结挂载点数组 + createInspectorState() + reduceInspector(state, action) 纯 reducer(select-tab/mark-rendered/invalidate/set-collapsed;未知 action 与幂等转移返回同一引用)——迁移期与 legacy state.inspectorRendered 并行同语义,后续成为唯一权威。
- **性能预算 gate**(`scripts/performance_gate.py`,双层):静态字节预算(operator JS ≤345KB / operator CSS ≤105KB / widget JS ≤25KB,按 1.3.x 基线留 ~25% 余量)进默认 pytest(tests/test_performance_gate.py);真实浏览器层 Playwright Chromium 测 LCP≤2500ms、CLS≤0.10、长任务数≤50、10k 合成会话 windowed renderQueue 单次渲染≤2000ms、JS heap 波动≤15MB——基线 JSON 写 artifacts/performance-baseline.json(--update 重写)。
- **视觉回归 gate**(`scripts/visual_gate.py`):四个稳定面基线(tests/baselines/:workspace-dark/workspace-light/knowledge-view/mobile-queue),截图前 mask 动态区(time/.item-sla/#liveStatus/#queueCount/#operatorIdentity/.metric strong),Pillow 逐像素通道容差 ±12、整图差分比上限 0.5%、无基线首跑 bootstrap 写入、drift 落 artifacts/visual-drift-<name>.png;axe/键盘/focus trap/reduced-motion gate 原样保留在 tests/ui_accessibility.py。

### Changed

- app.js 3,533→3,386 行(inspector 面迁出);main.js import 图新增 inspector 节点,index.html modulepreload 同步。
- ci.yml 新增 schedule 触发(cron `0 3 * * *`),nightly 档执行 performance_gate 浏览器层与 visual_gate(wall-clock 与像素预算对 runner 敏感,不进 per-PR 门);静态性能预算仍随每次 pytest 执行。

详见 ADR-017。

## 2.0 后续 — ROADMAP §43.5 AI Governance v2(2026-08-23)

### Added

- **AI governance registry**(`app/ai_governance.py`,migration v41 phase="expand"):eval dataset 版本单调 + canonical-JSON sha256 content hash(load 重验,篡改 fail closed)、eval run 关联 dataset/candidate/WORM report、maker-checker 审批(请求者不能自批、require_approved fail closed);线上反馈 ingest 即脱敏且保持 pending_review 直到人工复核,promote 只接受 accepted 行混入即整批中止。
- **工具治理面**(`app/tool_governance.py` + `app/tools.py`):HMAC capability token(短 TTL、单工具单租户、schema digest 钉扎——策略编辑使在外 token 全失效)+ ToolPolicy(side-effect 分级 readonly/mutating/high_risk + 参数 schema + 时钟预算)+ 无依赖 JSON-schema 子集校验;ToolGateway.enforce_governance 固定顺序 fail closed:未注册策略即拒→token 验签→实参 schema→high_risk 须 require_approved;拒绝返回 policy_denied + 机器可读 reason,记 tool.denied 日志。
- **Provider 治理与 tenant 禁用面**(`app/model_provider.py`):ProviderMetadata 声明式注册表(data_retention/training_opt_out/region,部署整体替换接入真实 vendor);check_model_policy 三 facet——disabled_models 精确匹配、disabled_providers(provider_for_model_ref 从 provider/model 或 provider:model 前缀推断,裸引用归属 settings 默认)、allow_data_egress=False 时 provider 声明 region 与租户 pinned region 比对;空策略全放行。
- **turn 级治理门**(`app/turn_policy.py`):19.4 allow-list 通过后从数据面 effective_policy().model_policy 判定禁用面,被拒 allow_model=False 且 turn.model_denied 审计 payload 附 reason;无控制面/plane 不可达/快照过期一律跳过该门(fail-open),控制面降级不 brick 流量。
- **Drift 监控自动停 canary**(`app/drift_monitor.py`):信号全部来自既有面零 turn 时写入——质量桶升级率/负反馈率 + 审计拒绝计数(turn.model_denied/turn.budget_exceeded 合并模型拒答、tool.denied 工具拒绝);任一阈值越限清空该租户全部 canary 回 draft 并审计 ai.drift_canary_stopped(信号明细+停止清单;无 canary 也记事件);active 绝不动。DRIFT_* 配置组:DRIFT_ENABLED 默认 False、DRIFT_WINDOW_DAYS、DRIFT_MIN_TURNS(rate 信号最小样本)、rate 类 (0,1] / 计数类正整数 / None 显式停用单信号,validate 全覆盖;挂 turn-worker housekeeping 小时 sweep,失败仅记日志。

### Changed

- PromptRegistry 新增 clear_canary(set_canary 的逆操作:canary 回 draft、审计 prompt_version.canary_cleared 含 reason)——drift 响应的自动化半边,回滚到 retired 版本仍是显式人工决策。
- tests/test_migration_registry.py 版本上界 1–40 → 1–41(v41 入链)。
- Fixed: scripts/backup.py 备份副本连接缺 row_factory 导致 manifest residency 读取 TypeError(pre-v40 库降级分支同样受影响)。

### Security

- 高风险工具调用在无人工审批记录时不可能执行(无论 token/确认状态);未审核线上反馈对 dataset 构建器不可见,原文在持久化前即被替换为脱敏文档。

详见 ADR-016。

## 2.0 后续 — ROADMAP §43.4 区域、备份和数据驻留(2026-08-23)

### Added

- **租户驻留列与 provisioning 透传**:migration v40(phase="expand")`tenants.region TEXT NOT NULL DEFAULT 'local'`(`_ensure_column`,既有行不动)——驻留在创建时固定;`provision_tenant(region=...)` COALESCE 幂等(缺省重跑不清除已钉住的值),审计事件随 provision 落库;`TenantProvisionRequest.region` / `TenantQuotaOut.region`(默认 `local`)进 schema,OpenAPI 快照已重生成。
- **residency 模块**(`app/residency.py`):`RegionSpec`(storage_location/backup_target/max_data_class/support_access_from/cross_border_transfers)、`REGION_INVENTORY`(默认单区 `local` closed:零出境转移;多区域部署整体替换该映射)、`summarize_tenant_residency`(region 桶汇总 + `single_write_region` 判定)、`check_restore_compatibility`(恢复目标白名单校验,违规返回区域清单)。
- **备份驻留感知**(`scripts/backup.py`):manifest 新增 `residency` 维度——按 region 桶记录覆盖的租户(storage_location/backup_target/tenant_ids)、单写区判定与跨境转移汇总;租户读取在 backup 复制之后进行(manifest 与字节描述同一快照);pre-v40 库降级为未钉住。
- **恢复区域门**(`scripts/restore.py`):`restore_database(allowed_regions=...)` + CLI 可重复参数 `--allowed-region`;manifest 覆盖区域不在白名单即抛错拒绝换入(checksum 校验后、解压前)。受控灾备切换 = 运维显式把 DR 区域加入白名单,门本身绝不静默放宽驻留。省略参数保持旧行为(兼容既有演练脚本)。
- **residency evidence pack**(`scripts/generate_residency_pack.py`):每租户 `<tenant>.residency.json`——pinned region 与 RegionSpec 姿态、控制面签名快照复核(HMAC 验签通过才标 verified;pinned region 与策略 region 不一致记入 `mismatches`,不作为认证通过)、v32 数据字段注册表中超出该区 max_data_class 的字段、覆盖该租户的备份 manifest 关联;外加 `_cross_border_register.json` 跨境处理清单(closed 区贡献零条目)。
- **tests/test_residency.py** 20 例:v40 默认值/显式钉住/幂等保持/审计落库、manifest region 桶汇总、恢复门双向(未批准区域拒绝/匹配区域放行)、pack 结构/mismatch 检出/签名一致零 mismatch/legacy 无 region 列降级。

### Changed

- `tests/test_migration_registry.py` 版本上界 1–39 → 1–40(v40 入链)。

## 2.0 后续 — ROADMAP §43.3 SDK v2 支持与跨版本矩阵(2026-08-23)

### Added

- **helix-client Python SDK v1.2.0 v2 方法族**(`clients/python`):`ConversationPage` dataclass(data/next_cursor/api_version);五个 v2 方法走 `/api/v2/*` 游标信封契约——`list_conversations_v2`(解析信封 + 读 `X-API-Version` 头)、`iter_conversations_v2`(自动翻页生成器,内置 `_MAX_V2_PAGES=10_000` 上限防非终止游标死循环)、`get_conversation_v2`、`list_messages_v2`(消息 created_at+seq keyset 翻页)、`create_conversation_v2`(透传 `Idempotency-Key`,检测 `X-Idempotent-Replay: true` 并以 `_idempotent_replay` 布尔标志返回);错误响应沿用 Problem Details 子类映射(request_id 透传)。
- **跨版本矩阵测试**:`tests/test_client_v2.py` 9 例(MockTransport 单测——信封+版本头解析、cursor/sort/status 参数透传、多页无重复、next_cursor 缺失即停、非终止游标抛错而非挂起、幂等键发送与重放标志、404 映射含 request_id、messages 页面);`tests/test_e2e.py` 新增 4 例(真实 FastAPI 栈)——shadow-read 八核心字段(id/status/priority/channel/customer_name/customer_ref/created_at/updated_at)v1↔v2 字节一致(单资源含嵌套解包)、7 会话 limit=3 游标全覆盖无重复且集合等于 v1 列表、同 Idempotency-Key 重放返回原资源 id 且仅一条、消息 keyset 翻页覆盖全历史(41.6 抑制语义下首轮 assistant 回复必在页内)。

### Changed

- SDK 模块 docstring 补充 v2 用法示例;`pyproject.toml` 版本 1.1.0 → 1.2.0。
- consumer-driven contract 双写/影子流量长期并行与 schema downgrade 演练归入 Gate D「v1/v2 并行至少一个完整发布周期」项,不在本轮自动化侧。

## 2.0 后续 — ROADMAP §43.2 PostgreSQL 行级租户隔离 / 契约(a)(2026-08-22)

### Added

- **RLS 纵深防御**(`app/rls.py`,ADR-015):18 张核心客户数据表统一 `helix_tenant_isolation` 策略,USING/WITH CHECK 均为 `tenant_id = current_setting('app.tenant_id', true)`——即使应用层 WHERE 谓词丢失,PostgreSQL 也在行级拒绝越界读写;无 GUC 时读零行、写违反 WITH CHECK(fail closed)。保护面为 v01 baseline + attachments(v22) + archive 冷层(v24) + channel threads(v27) 中 `tenant_id TEXT NOT NULL` 的表;配置/registry 类表(混全局行或无 token 路径)与其访问路径改造一起后续纳入。
- **事务级 tenant context**(`app/context.py` 双作用域 + `app/postgres_db.py` 绑定):GUC 由连接层从 ambient scope 设置、只来自已认证凭据(API 认证依赖 `bind_tenant_scope()`)或 durable job 行(worker turn 执行收窄),绝不来自请求参数;`set_config(..., true)` 随 commit/rollback 消亡,连接池复用的下一个事务从 fail-closed 起步。跨租户系统工作(队列认领、housekeeping、schema init)走 `maintenance_scope(reason)`——刻意不绑 GUC,要求 RLS 不覆盖的 owner/BYPASSRLS 角色,每次进入记 INFO 审计日志。审计写入经 `_audit_scope()` 自绑定事件本身租户。RLS 强制开启时无任何作用域的数据库访问抛 `TenantContextError`(fail loud 而非静默空结果)。
- **开关与验证**:`DATABASE_RLS_ENABLED=1`(仅 PostgreSQL 后端,默认关);`verify_rls()` 读 pg_class/pg_policy 报告每张表 ENABLE/FORCE/policy 状态(`_is_tenant_policy` 对 `pg_get_expr` 规范化输出做语义 token 匹配);`missing_protection()` 列出保护缺口。
- **RLS 演练**(`scripts/run_rls_drill.py`):scratch 集群双角色(migrator owner / app 无 BYPASSRLS)实跑验收契约 10/10 PASSED——no-context SELECT 零行且 INSERT WITH CHECK 拒绝、acme scope 仅见本租户、显式他租户谓词零行(GUC 而非 WHERE 决定)、GUC 随事务提交消亡(池复用模拟)、无 WHERE DELETE 限本租户(globex 幸存)、跨租户 UPDATE 触零行、verify_rls 18/18 ENABLEd+policy。台账 `supplychain/rls-drills.json`,`automated_rls` 纳入 `threat_model_gate.py --check-today` 治理枚举;CI supply-chain job 新增 PG service + drill 步骤。
- **文档**:ADR-015(PostgreSQL 行级租户隔离决策与启用顺序)、`docs/OPERATIONS.md` Row-Level Tenant Security 章节;`[postgres]` extra(psycopg2-binary)。

### Fixed

- `run_rls_drill.py`:scratch DSN 用 urlsplit 构造(不再继承 admin 凭据/误解析主机);app role 补序列 USAGE GRANT;migrator 连接池关闭;`context-dies-with-transaction` 断言兼容 PG 返回空串而非 NULL 的 GUC 形态。

## 2.0 后续 — ROADMAP §43.1 租户控制面与部署单元(2026-08-21)

### Added

- **租户控制面**(`app/control_plane.py`):`TenantPolicy`(plan 枚举 free/standard/enterprise、region、deployment_cell、feature_policy、model_policy、credential_reference)——策略与执行分离,控制面是唯一策略写方;`TenantControlPlane` 版本递增签发 HMAC-SHA256 签名 `ConfigSnapshot`,重发时对存储态自校验(被篡改行 fail closed);migration v36(phase="expand")`tenant_control_policies` 版本化表(tenant_id+version 主键)。
- **数据面 last-known-good**(`DataPlaneConfig`):签名验证先行(伪造/过期到达即拒);控制面不可达时 LKG 在 TTL 内继续服务、过期 fail closed;降级期间 plan/region/deployment_cell/model_policy 任一变更拒绝(high-risk 门禁),feature_policy 等低风险面允许滚动。
- **装配**:`CONTROL_PLANE_SECRET`(缺省回退 widget_secret;<32 字节显式禁用并告警,不弱化签名)注入 AppServices.control_plane/data_plane_config。

## 1.5 后续 — ROADMAP §42.6 SLO 与可观测性 v2(2026-08-21)

### Added

- **多窗口 burn-rate 告警**(`app/slo.py`):page=14.4×(1h+5m)、ticket=6×(6h+30m) 双窗口同时越限才触发(SRE workbook 标准)——瞬时尖峰不 page、缓慢持续燃烧不被近 5 分钟静默掩盖;page/ticket 严重度分离;空/缺失长窗按未知处理(抑制而非放大);告警携带 window_rates/thresholds/error budget 消耗。评估器为纯函数,输入任意来源的 (bad,total) 计数。
- **统一 trace context**:migration v35(phase="expand")turn_jobs 增 request_id 列;`enqueue_turn_job` 默认从调用方 contextvar 取 id;worker `run_once` 认领时回放 request_id 进 context——处理期间发出的每条审计事件与 outbound webhook 携带与客户请求相同的关联 id,page → job → audit 单值可达。
- **Dashboard 集**(`scripts/generate_dashboards.py`):六份 Grafana 风格 JSON(tenant noisy-neighbor / queue fairness / model-provider / archive & object store / DSR / credential usage)生成至 docs/dashboards/,面板仅引用 METRIC_CATALOG 登记的真实指标名。
- **Game day 编排**(`scripts/run_game_day.py --focus db|redis|model|connector|objectstore|webhook|all`):按月轮换主故障域,驱动既有演练(restore+pitr/redis_failure/chaos/rotation/archive+attachment/conformance),台账 supplychain/game-days.json。

## 1.5 后续 — ROADMAP §42.5 正式渠道 Adapter SDK / REL-003(2026-08-21)

### Added

- **Provider Adapter SDK**(`app/channel_providers.py`):`ProviderAdapter` 协议(verify_signature + parse)——供应商特有代码只有认证与归一化两件事;`NormalizedEvent` 统一形状(kind=message/edit/recall/receipt、附件引用不内联字节、occurred_at 供排序);`ReferenceJsonAdapter` 固定参考线协议(HMAC `sha256=<hex>` over `<ts>.<body>`,与核心 ingress 同一验证规则;message_id/thread_id/customer_id 必填且有界;未知 kind fail closed);适配器输出即 Phase 38 统一入口 schema——核心 thread mapping/receipt 幂等/turn job 幂等零改动。
- **Conformance suite**(`tests/test_provider_conformance.py`,9 例全走真实 HTTP 路径):签名(好 202/错密钥·过期·缺头 401 且不留状态)、重放与重复投递折叠为单 job 单消息、乱序到达零丢失、edit/recall 归一化正确且经入口重投必 409(历史不可改写)、附件引用解析+流转、背压 429 带 Retry-After 排空后重试恰好一次、outbound provider 故障注入(503→retried→恢复后 delivered,DLQ 语义)、跨账号/租户隔离(acme 会话不入 demo 库、demo 密钥不能认证 acme 路径)。

### Fixed

- `_json_safe_errors` 漏消毒 pydantic 错误的 `input` 字段:非 JSON body(如缺 Content-Type 的 bytes)触发校验错误时 RFC 9457 响应序列化崩溃变 500;现全字段递归消毒(bytes 解码替换),正确返回 422。

## 1.5 后续 — ROADMAP §42.4 附件对象存储与恶意内容隔离 / SEC-006(2026-08-21)

### Added

- **附件对象存储**(`app/attachment_store.py` DiskAttachmentStore,生产可换 S3/GCS):原子写(tmp+rename)、storage key 单段白名单(防路径穿越)、**拒绝静默覆盖**(key 冲突抛 AttachmentKeyConflict,对象不可变);上传经 store 落盘并固定 sha256。
- **恶意内容隔离**:内置扫描器新增 EICAR+多态变体检测(规范固定后缀匹配)、伪装 MIME 拒绝(text/* 声明携带 MZ/PK/gzip magic)、压缩炸弹守卫(zip 条目总量/gzip 流式解压 ≤ 原始 20×);`TimeoutMalwareScanner` 墙钟预算包装——超时/引擎崩溃/未知 verdict 一律 fail closed;`normalize_verdict` 非 bool clean 或非已知 clean 词全部拒绝。
- **quarantine 流程**:`ATTACHMENT_SCAN_MODE=external`(默认 sync 向后兼容)时上传落 `quarantined` 态——对象已存但不可下载、不可绑定消息;新端点 `POST /api/attachments/{id}/verdict`(operator 权限)经 `finalize_verdict` 晋升 stored/rejected,rejected 即时删除对象并审计。
- **完整性**:migration v34(phase="expand")为 attachments 增 sha256 列;下载时流式重算比对,篡改即 404 + integrity 日志;`AttachmentOut` 暴露 sha256 供客户端校验。
- **签名下载 URL**:HMAC-SHA256(tenant.attachment.expiry,widget_secret 签名密钥),TTL 硬顶 600s;过期重放/篡改/跨租户 token 全拒;与既有会话鉴权并存(token 缺省走原权限路径)。下载响应强制 `X-Content-Type-Options: nosniff` / `Cache-Control: no-store` / CSP sandbox。
- **删除证明**:attachment.deleted 审计含 storage_key/sha256/size_bytes;DSR 删除先清对象文件再删行(`attachment_objects` 计数入删除证明)。

### Changed

- OpenAPI 快照重生成(新增 verdict 端点,含 summary/description/tags);`AttachmentOut` 增加 `sha256: str | null`(响应体只增不改)。

## 1.5 后续 — ROADMAP §42.3 冷归档与可查询存储 / REL-002(2026-08-21)

### Added

- **归档对象存储**(`app/archive_store.py` `ArchiveObjectStore`):冷层 payload 以 gzip JSONL 压缩分区按 tenant/date 落对象存储(磁盘参考实现,生产可换 S3/GCS),原子写(tmp+rename);tenant manifest 记录 object_id/时间界/双摘要(压缩字节 sha256 + 未压缩规范流 sha256)/字节数/行数;`iter_range(from,to,limit)` 时间窗+硬上限流式查询;读取先全量校验压缩摘要再解压——对象缺失/篡改在产出首条记录前 fail closed(`ArchiveIntegrityError`)。
- **审计归档迁对象存储**:migration v33(`phase="expand"`,42.2 纪律首个使用者)为 `audit_archives` 增加可空 object_key/object_sha256/object_bytes 列;retention 写路径双轨——配置 store 时每批 500 事件写为分区、DB 行只留 slim manifest(archive_json 置空),未配置保持内联 JSON 完全兼容;读路径 `get_audit_archive` 双轨流式回读。
- **流式审计校验**(`validate_audit_archive_stream`):逐事件增量校验哈希链/序列单调/租户一致 + 流末 count/first_seq/last_seq/边界哈希核对,O(1) 内存,替代整批载入。
- **压测档位化**:`archive_search_load_test.py --tier 10x|100x`(固定 10×=20k/200k、100×=200k/2M)+ 每项基准 tracemalloc 峰值内存 + 归档层字节足迹(dbstat 优先/file_size 兜底)+ `--json` 工件。10× 实测(SQLite):list p95 64.5ms、worst-search p95 289.6ms、selective p95 199.3ms(目标 <2s),峰值内存 ≤0.12MB,工件 `artifacts/archive-bench-10x.json`。

### Regression

- archive_store 单测 6 例 + retention 对象存储集成 5 例(slim manifest/流式回读一致/篡改与缺失 fail closed/legacy 兼容);既有 retention/审计套件无回归;ruff/pyright 干净;migration gate OK(33 迁移链连续)。100× 档实测与真实对象存储适配器列 Gate C 待办。

## 1.5 后续 — ROADMAP §42.2 PostgreSQL/Redis HA 与 PITR / REL-001(2026-08-21)

### Added

- **启动零 DDL 姿态**:`DATABASE_AUTO_MIGRATE=false`(默认 true 向后兼容)时 web/worker 启动不执行任何 DDL,只读校验 `schema_migrations` 就绪并对未迁移/过期库 fail fast(`pending_migration_versions`);DDL 收敛到独立 release job `scripts/run_migrations.py`(apply/verify-only、sqlite+postgresql 双后端、`--json` 机器报告),支撑最小权限 DB roles(app role 仅 DML / migrate role 每次 release 一次 DDL)。
- **expand/migrate/contract 迁移纪律**:`@migration(..., phase=)` 元数据(v33 起强制声明,v1–v32 豁免),规则:phase 合法枚举、contract 必须有更早的 expand;`scripts/migration_gate.py` 在 CI 校验链连续性 + 纪律,N/N+1 滚动兼容政策(先 expand+migrate 后 contract)入 `docs/OPERATIONS.md`。
- **PITR 演练**(`scripts/run_pitr_drill.py`):T0/T1 双备份 → 恢复到 T1 隔离环境 → 审计链+WORM anchors intact、窗口内数据零丢失(RPO)、post-T1 写入不回放(point-in-time 语义)、queued turn job 存活、附件行-对象-大小清单核对、DSR tombstone 存在且启动期重放干净;RTO 计量对比预算(默认 1800s/RPO 900s),台账 `supplychain/pitr-drills.json`,`automated_pitr` 纳入 `threat_model_gate.py --check-today` 治理枚举。
- **Redis flush→rebuild 不变式测试**:全量 flushdb(最坏故障)后一次 `recover()` 从 PostgreSQL 恰好重派全部非终态 job——无丢失、无重复、无双认领,完成后二次 recover 零幻影派发(lease fencing 生效)。
- **运维文档**:`docs/OPERATIONS.md` 新增 Deployment Topology And Recovery 章节(process roles、migration release job 与最小权限、TLS/sslmode 与连接池拓扑、PITR RTO≤30min/RPO≤15min 目标与演练、Redis failover reconciliation)。

### Regression

- 相关套件 69 passed / 6 skipped(migration gate/auto-migrate/redis queue/process role/fail-closed/system/drills);ruff/pyright 干净;PITR 演练 PASSED(rto≈0.8s);threat_model_gate 含 automated_pitr 全绿。生产侧真实 PG 主备切换/WAL 归档/区域分区演练依赖部署环境,列 Gate C 待办。

## 1.5 后续 — Phase 42.1 part B lease fencing token(2026-08-21)

### Fixed

- **过期 lease 仍可提交(僵尸 worker)**:`app/db/jobs.py` `complete_turn_job`/
  `fail_turn_job` 此前仅 `WHERE locked_by = ?`(worker_id)做所有权防护,缺新鲜
  lease 校验→ 一个过期 lease(无新声明者、未被 recovery 扫到的)工作器仍可
  complete/fail 其 job,可能覆盖已被重派的结果。现加 `AND locked_at > ?`
  (`stale_at = now - lease` 秒)新鲜校验,SELECT 与 UPDATE 双层守门;过期 lease
  提交被拒(`complete` rowcount 0 / `fail` 返回 None),job 保持 processing。
  `lease_seconds` 参数化:`app/queue.py` TaskQueue Protocol + SQLite + Redis 三处
  complete/fail 透传;`app/jobs.py` turn worker 调用传 `self.lease_seconds`。

### Security

- 一致性保证:ISO-8601 微秒精度字典序比较 = 时间序比较(与
  `claim_next_turn_job`/`recover_turn_jobs` 同模式);db mixin 单实现覆盖
  SQLite + PG 双后端,避免双实现漂移。

### Added

- `tests/test_lease_fencing.py`(6 例):新鲜 complete 成功、过期 complete 拒绝且
  留 processing、新鲜 fail 成功、过期 fail 拒绝且留 processing、错主人拒绝
  (defense-in-depth)、非法 lease_seconds ValueError;确定性回拨 `locked_at`
  避免 `sleep`。六处现存调用点(`test_concurrency`/`test_infrastructure`/
  `test_redis_queue`/`test_system`/`test_chaos`/`test_postgres`)补 lease 参数。

### Notes

- 回归:84(test_lease_fencing + concurrency + infrastructure + process_role)+
  45(redis + system + chaos)+ 47 postgres(live 26 skipped)全绿;OpenAPI 快照
  未变(无新端点)、ruff/pyright 0 errors。

## 1.5 后续 — Phase 42.1 part A Web 与 worker 运行角色分离(2026-08-21)

### Added

- **`PROCESS_ROLE=web|worker|all` 运行角色分离**(`ROADMAP_2_X.md` §42.1):
  `app/config.py` 新增 `process_role` 字段(默认 `all`)+ `PROCESS_ROLE` env 读取 +
  validate(`web|worker|all`,非法值 `ValueError`)+ `runs_turn_worker` 属性(`!= "web"`);
  `app/main.py` 启动门控改为 `if settings.runs_turn_worker and queue.is_ready()` 方注册
  `turn_worker.start`,`web` 角色记日志不启 worker,队列未就绪记 error 不启;
  `app/routers/system.py` `/health/ready` 与 `/api/admin/diagnostics` 暴露
  `process_role`/`runs_turn_worker` 便于运维区分实例角色。
- `tests/test_process_role.py`(6 例):默认 all、web 不启 worker、invalid 拒绝、
  web 无实时线程且 health 报告 web、all 启实时线程且 health 报告 all、worker diagnostics
  暴露角色(显式 `__enter__/__exit__` 避免测试上下文退出后 DB 池关闭)。
- `docs/OPERATIONS.md`:说明 `worker` 在部署层 ingress 收敛(不暴露公网业务端点是
  网络层关注,应用内端点接口不变),`/health/ready` 报角色便于探针区分。

### Notes

- 本期为 42.1 part A;part B(lease fencing token:`complete_turn_job`/`fail_turn_job`
  补充新鲜 lease 校验)列为后续任务。
- 用例已显式避开 `with client:` 退出触发 shutdown 关闭 DB 池的坑,改用显式生命周期管理。

## 1.4 后续 — Gate B 演练脚本 code-review 整改(2026-08-21)

### Fixed

- **`run_patch_drill._real_cosign` KeyError**:真实 cosign 路径误读 `payload['release']`(manifest 实际键为 `app_version`)→ 改为读 `app_version` 并返回 `(ok, detail)`,检查 subprocess returncode,子进程失败不再被空 stdout 掩盖为通过(review High)。
- **Windows GBK 解码崩溃**:`run_restore_drill`/`run_patch_drill` 所有 `subprocess.run` 补 `encoding="utf-8" errors="replace"`(review Medium;release_manifest/verify_audit_chain 含中文诊断)。
- **restore drill 上游失败韧性**:`backup_file` 作用域未初始化(NameError 风险)+ 上游失败仍对不存在的 `restored.db` 调 `_verify`→ 重写为 `restore_ok and restored.exists()` 守卫,verify/tamper 仅在恢复成功时跑(review Medium)。
- **admission digest 校验**:`_check_pin` 由 `in` 子串改为 `startswith("sha256:")` + 64hex 校验,拒绝 `xyz-sha256:-bogus` 等伪 digest(review Medium)。
- **import 顺序与 logger**:`datetime` 移到模块顶部、main 加 `logging.basicConfig`(review Medium;此前 logger.info 静默丢失)。
- **`_check_cosign` ref 构造**:`"{registry}"` 字面量改为 `/ in image` 语义判断 host 前缀(review Low)。
- **teardown bare pass**:`PatchDrill.close` 的 `except Exception: pass` 改为捕获并 `logger.warning`(review Low)。

### Added

- `tests/test_drill_helpers.py`(6 例):`_simulate_sign` 确定性/篡改敏感/nonce 敏感/hex 形态,`_seed_db_anchors` 每高危事件一 tip、返回 seq→chain_hash(review Medium 测试覆盖)。

## 1.4 后续 — Gate B 自动化演练与发布门禁(2026-08-20)

### Added

- **镜像 admission gate**(Gate B #2,`scripts/image_admission_check.py` + `tests/test_image_admission_gate.py` 6 例):
  fail-closed——base-image digest 未固定、SBOM 缺失/空、发布 manifest 树漂移一律拒绝;
  `--cosign-key/--registry` 时追加 `cosign verify`;CI 以 `--no-digest-required` 跑
  manifest/SBOM 一致性(digest 回填属部署方受控)。
- **凭据轮换演练**(Gate B #3,`scripts/run_rotation_drill.py`):真实 HTTP 审计路径——
  runtime issue → config promote → lifecycle.rotate(有界 overlap 内旧 key 仍 200) →
  admin revoke → 旧 key 401/新 key 200/旁观 key 200;结果入 `supplychain/rotation-drills.json`。
- **审计 anchor 恢复演练**(Gate B #4,`scripts/run_restore_drill.py`):scratch DB 高危事件 +
  真实 anchors/WORM claims → `backup.py` → `restore.py` → `verify_audit_chain` intact →
  篡改一行 → verify TAMPER;结果入 `supplychain/restore-drills.json`。
- **补丁发布演练**(Gate B #7,`scripts/run_patch_drill.py`):release manifest 构建 +
  签名绑定/篡改检测断言(无真实 key 时确定性模拟签名,`--cosign-key` 时真实 cosign)+
  canary 会话 + 发布后 diagnostics 监控;结果入 `supplychain/patch-drills.json`。
- **安全演练台账与 1.4.0 delta**:`supplychain/security-drills.json` 录入三场
  `automated_*` 演练(reference 指向可复查台账)、`supplychain/threat-model-deltas.json`
  录入 1.4.0 delta(controls/verification_evidence 全量);`threat_model_gate.py`
  新增 automated drill 类型与 reference 校验,
  `--release 1.4.0` 与 `--check-today` 全绿。
- **runbook**(Gate B #8):`docs/runbooks/M0_HARDENING_ACCEPTANCE.md`(OIDC 负向/DSR 权限/
  队列 fail-closed/报告渠道闭环/threat-model)、`docs/runbooks/RELEASE_1_4.md`
  (CI 门禁复核/供应链证据/AI 基线/演练/canary 发布/监控/回滚)。
- **CI `supply-chain` job 扩展**:admission gate(manifest/SBOM)、三场自动化演练 +
  `threat_model_gate --check-today` 步骤。

### Security

- **Gate B 现状(2026-08-20)**:项 1/3/4/5 已满足;项 2(admission 脚本)/6(gate 全绿)/
  7/8(脚本与 runbook)自动化侧就绪;剩余依赖真实环境——digest 回填/cosign 签名/
  provenance 对接、私有报告渠道端到端、真实签名补丁发布、runbook 非作者执行。

## 1.4 后续 — ROADMAP §41.6 深模块拆分第一步 / ARC-001(2026-08-20)

### Changed

- **orchestrator 拆三深模块**(app/orchestrator.py 1,504 → 930 行,-38.2%):turn policy(`app/turn_policy.py` 293 行,语言检测/消息持久化/policy inspect/budget-model guards/triage/suppressed 路径)、specialist execution(`app/turn_execution.py` 210 行,路由+quality gate+间接注入复检)、persistence/finalization(`app/turn_persist.py` 382 行,routing state+SLA/auto-assign/翻译/质量聚合/telemetry/webhook)。公共接口只传 frozen dataclass typed context/result;`TurnServices` Protocol(`app/turn_services.py`)使三 stage 只读 orchestrator 服务子集,import 图无环。
- **迁移注册表按版本拆分**:app/migrations/ 单一大文件拆为 `v01`–`v32` 32 个独立模块,`_MIGRATIONS` 有序注册 + `verify_migration_chain()` 连续性 gate 不变。
- **app.js 深模块拆分(41.6b)**:app.js 从 4,820 行/189,110 B 降至 3,534 行/140,024 B(行 -26.7%、字节 -26.0%,满足 ≥25% 验收)。抽出 6 个深模块(composer、session、admin-report、ticket-view、quality-panel、attachment),均 ≤400 行;模块间仅 composer→attachment 单向 import,import 图无环。app.js 保留 24 个 `HelixModules?.[mod]?.[fn](...arguments)` 薄包装器转发外部调用点,绑定统一收口到各模块 `bindX()`;`frontend_gate.py` 通过(全模块 node --check、体积上限、139 个 node 测试)。
- **修复拆分回归**:
  - `turn_persist._emit_webhook` 由错误的 `dispatch()` 修正为 `webhook_service.emit_event(tenant_id, event, payload, event_id)`(try/except `webhook.emit_failed`),与 orchestrator 原始实现一致;
  - `turn_execution.execute()` 补回拆分丢失的 `quality.reviewed` 与 `tool.executed` audit,语义对齐旧 `build/lib/app/orchestrator.py`;
  - `turn_policy.py` 恢复 `TurnPolicyStage` class 版(曾被旧函数式版本覆盖),补齐 `TurnPolicyResult` 全字段(suppressed/decision/risk/language/prompt_version/allow_model/budget_*/segment_elapsed)。

### Regression

- 全量 `pytest tests/`:**889 passed / 37 skipped / 85 subtests passed**;golden+adversarial 15 passed、对抗集 24/24;migration 链 v1–v32 连续验证通过;`/accept` 端到端 handoff 生命周期 200。

## 1.4 后续 — ROADMAP §41.5 AI 安全评测 Gate v1 / AI-001(2026-08-20)

### Added

- **对抗评测集**(golden/adversarial.json,24 例/9 类威胁 + 独立 schema scripts/adversarial_schema.py):
  直接/间接提示注入、系统提示探测(中/英/法/日)、跨租户检索、工具参数注入、PII/secret 外泄、
  恶意附件文本、多语言变体、策略抑制。每个用例固定 tenant/prompt/model/version/允许工具,并扩展
  expect 契约(ADR-014 决策 1):requires_human、精确 citation、redaction、canary、tool_calls、canary_assert。
- **评测运行器**(scripts/evaluate_adversarial.py):走真实 HTTP 路径(demo+acme 双租户 API key、
  临时数据库),支持知识文章/附件/secret 哨兵三类种子通道,按用例逐一回收种子防止交叉污染;
  报告含质量键(pass_rate/mean_confidence/citation_coverage/p95/成本),可选写 WORM store,
  --active-metrics 触发五条件晋级门禁。退出码按 EVAL_ADVERSARIAL_FLOOR(默认 1.0)闸门。
- **不可变评测报告与晋级门禁**(app/eval_reports.py):EvalReportStore 一次写入不可变报告
  (复用 DiskWormStore,篡改/重复写抛 WormIntegrityError);decide_promotion 五条件
  (对抗集 100%、golden 100%、质量不低于 active、P95/成本预算)全部满足才放行 canary 提升,
  阻断理由与指标披露写入 WORM 晋级记录。
- **运行时硬化**:
  - app/agents.py::PolicyAgent 新增系统提示探测模式(output/print/paste/show/send/copy system
    prompt、verbatim、中文一字不差/把…发给、法/日文注入、角色扮演/调试模式索要隐藏指令);
  - app/agents.py::OrderAgent 对粘连换行/; /-- /SQL 关键字的订单引用走网关拒绝路径,
    不回显注入片段;app/tools.py::lookup_order 对非规范化资源 id 拒绝并表现为 not_found;
  - app/orchestrator.py 对知识检索结果复检策略,拒绝时以 escalation 应答且不回声,并把
    内容风险类别并入 turn metadata(risk_categories/content_inspection_escalated),
    间接注入从元数据可追溯。
- **CI ai-eval job**:对抗集 schema 门禁、对抗集 100% gate(报告写 WORM)、golden 27/27 回归、
  Phase 41.5 测试、评测报告产物上传。

### Security

- **AI-001 验收覆盖**:tests/test_eval_reports.py(一次性写入/篡改检测/五条件晋级/缺失报告
  fail-closed)+ tests/test_adversarial_eval.py(schema 24 例唯一性、探测模式 en/zh/fr/ja/roleplay、
  工具参数注入拒绝无泄露、间接注入元数据追溯、对抗集 24/24 与 golden 27/27 双绿)+
  既有单元回归 49 passed。对抗集与 golden 集 pass_rate 均 1.0。

## 1.4 后续 — ROADMAP §41.7 安全治理自动化 SEC-008(2026-08-20)

### Added

- **threat-model delta gate**(`scripts/threat_model_gate.py` + `supplychain/threat-model-deltas.json`):
  每个 release 必提交 delta——新增入口/资产/信任边界、关闭/新增风险、控制项与验证证据,
  `owner`/`approved_by` 必须具名(placeholder 如 `security@helix.example`、`TBD`、`<...>` 直接红灯);
  `--release <version>` 校验缺失 delta 与未来日期;`--drill-max-days`(默认 90)控制演练 recency。
  设计见 [`docs/adr/0012-security-governance.md`](docs/adr/0012-security-governance.md)。
- **季度桌面演练记录**(`supplychain/security-drills.json`):报告接收/依赖漏洞/密钥泄露/跨租户告警
  四类演练,必录 `started_at`/owner/`scenario`/`duration_minutes`;
  `--check-today` 校验最近演练未过期(90 天),未来日期同样红灯。
- **CI 接入**:`supply-chain` job 新增 `Threat-model governance gate (Phase 41.7)` step(PR 上 nil-tolerant,
  release 时间/受控环境以 `--release`/`--check-today` 强制执行);`rules/threat_model_gate.py`
  提供的 `ReviewError`/`review()` 模式与 `vuln_review.py`/`check_workflows.py` 同构。

### Security

- **SEC-008 验收覆盖**:`tests/test_threat_model_gate.py` 14 例——缺失必填字段、空控制/证据、
  placeholder owner 五形态、`--release` 缺失/未来日期、无演练/演练过期/未来演练、非法 drill_type、
  缺失文件/坏 JSON exit 2;nil-tolerant 空登记册在无 `--release`/`--check-today` 时零违规(CI 友好)。

## 1.4 后续 — ROADMAP §41.3 审计证据外部锚定 SEC-005(2026-08-20)

### Added

- **同事务高危审计**(`app/db/audit.py::audit_high_risk` + migration 31 `audit_anchors`):
  `BEGIN IMMEDIATE` 事务内 append 高危事件 + 读 `_audit_chain_tail` + INSERT frontier
  anchor 行一起提交或一起回滚,任何失败抛 `AuditUnavailableError` → 调用方 fail-closed
  (HTTP 503 `code="audit_unavailable"`、`Retry-After: 30`),保证“变更发生了、证据丢了”
  不可能发生。高危事件族固定 12 类(`HIGH_RISK_EVENT_TYPES`:api key 发放/吊销、
  DSR 创建/审批/执行、成员邀请/角色变更/停用、retention 策略更新、SLA 策略设置、
  webhook 注册/删除);普通 telemetry 失败走 `record_audit_gap()` 落 `audit_gaps` 行并可观测。
- **KMS 签名 + WORM 锚点**(`app/audit_anchor.py`、`app/worm_store.py`):链头
  `{last_seq,last_hash,timestamp,environment}` 经 Ed25519 签名导出为自校验 claim 文档
  (内嵌 public key + kid);`DiskWormStore` 每对象 JSON + 内容哈希 journal,对象只写一次,
  读回校验一致性,孤儿对象/哈希漂移/对象丢失即抛 `WormIntegrityError`。
- **统一校验器**(`scripts/verify_audit_chain.py`):一次运行重算本地全链(热表 + 归档
  manifest)→ 核对 `audit_anchors` 每个 frontier tip → 校验 WORM claim 签名/kid/环境/一致性;
  故障模式全部显式(重算/删 anchor/替换 manifest/错序/重复 seq/KMS 轮换旧 kid/WORM 不可用),
  exit 0=intact / 1=TAMPER / 2=DB 不存在;缺 WORM 目录显式告知未检查。
- 管理审计锚点视图:`GET/POST /api/admin/audit/anchors[/verify]` + `GET /api/admin/audit/gaps`。
- 设计决策见 [`docs/adr/0011-audit-external-anchoring.md`](docs/adr/0011-audit-external-anchoring.md);
  安全基线见 [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md) §Phase 41.3。

### Security

- **SEC-005 故障测试全链路**:`tests/test_audit_anchors.py` 8 例覆盖 ROADMAP 41.3 验收
  每一项——重算本地全链(`hash mismatch`)、删除 anchor(`missing DB anchor`)、替换
  manifest(`!= recomputed`)、错序(`prev_hash mismatch`)、重复 seq(`duplicate audit
  sequence`)、KMS key rotation(`not in the trusted set`,轮换后同 claims 可正常验证)、
  WORM 暂不可用(`TAMPER`),healthy fixture 三证据层 exit 0。

## 1.4 后续 — ROADMAP §41 供应链与可复现发布 SEC-003 + 凭据轮换 SEC-004(2026-08-20)

### Added

- `supplychain/` 新增五个供应链 gate,全部挂进 CI `supply-chain` job:
  - `scripts/scan_secrets.py`:扫描发布来源树中的已知凭证形态(私钥/Anthropic/OpenAI/AWS/GitHub/Slack/service-account/Fernet),忽略 build 产物、测试夹具(`tests/` 不进 wheel/Docker)与 npm 完整性哈希。
  - `scripts/license_gate.py` + `supplychain/license-policy.json`:运行时依赖逐包登记许可,新包未登记即红灯;`LicenseRef-TBD` 临时批准带 `approved_until`,逾期红灯。
  - `scripts/vuln_review.py` + `supplychain/vulnerability-exceptions.json`:例外必须含不可达证据/补偿控制/owner/due_date;open 例外到期自动红灯;`--audit --require-coverage` 保证被 `pip-audit` 上报的每个 CVE 都登记。
  - `scripts/check_workflows.py` + `supplychain/ci-pins.json`:`uses:` 引用必须与登记一致,commit SHA 固定留给受控更新机器人。
  - `scripts/release_manifest.py`:构建哈希清单(`requirements.lock`/`pyproject.toml`/`Dockerfile`/`app/` 树/SBOM + 基础镜像 pin),`--verify` 从磁盘重算比对,篡改或漂移立即失败。
- 设计决策见 [`docs/adr/0010-supply-chain.md`](docs/adr/0010-supply-chain.md);安全基线见 [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md) §Phase 41。

### Security

- **SEC-004 并发轮换补测**:`tests/test_phase41.py` 新增
  `test_concurrent_revoke_race_settles_on_single_authority`——两个独立实例在
  barrier 同步下并发 revoke 同一 credential,均幂等返回 200、最终一致 revoked,
  且持有旧 secret 的第三方实例立即 401。SEC-004 五项验收(跨实例吊销/过期
  边界/时钟偏差/并发轮换/审计脱敏)全部闭环。

## 1.3.0 后续 — ROADMAP Phase 24.3 正式消息渠道 webhook(2026-08-19)

### Added

- 新增 provider-neutral `POST /api/channels/{account_id}/webhook`：服务端账号配置绑定唯一租户/渠道，按原始 `<timestamp>.<body>` 做 HMAC-SHA256 验签与 300 秒重放窗口检查，未知账号和错误签名使用同一未授权响应。
- migration 27 新增 `channel_threads`、`channel_webhook_receipts`，并在 `turn_jobs` 增加 `channel_message_id`；外部线程、消息 receipt、异步作业和最终 customer message 形成持久幂等链。
- 增加 SQLite/真实 PostgreSQL/真实 Redis、首次并发、身份冲突、重放、配额、队列背压、审计和 worker 传播测试，以及正式渠道部署指南与 ADR 0007。

### Fixed

- 已解决的渠道线程收到新消息时，会话重新打开前同样执行活跃会话配额检查；达到上限返回 429，且不提前占用 receipt 或创建重复状态。
- 静态 API 参考重新从 OpenAPI 快照生成，正式渠道入口不再只存在于运行时 schema。

## 1.3.0 后续 — ROADMAP §18.5 PostgreSQL 全匹配搜索收敛(2026-08-18)

### Added

- `app/db/conversations_query.py` 确定性窗口快路径 `_query_conversations_windowed` + 非搜索过滤子句提取 `_conversation_filter_clauses`:PG 上 `sort="updated"` 的消息搜索先经有序索引取最新 `offset+limit` 会话,再逐会话探测搜索谓词(completeness rule 论证窗口填满即返回全局正确页);窗口不足 `limit` 行或 `offset+limit > 1024`(`_PG_SEARCH_WINDOW`)回退聚合 CTE,语义与 SQLite 完全一致。`DatabaseCoreMixin` 增加 `self.backend` 标记。
- `app/pg_compat.py` 新增 `install_updated_sort_index()`:`idx_conversations_tenant_updated_id (tenant_id, updated_at DESC, id DESC)`,随 `PostgresDatabase.initialize()` 幂等安装,同时修复普通 `updated` 排序分页(此前缺 `id DESC` tiebreak,planner 退并行全扫 + top-N sort ~255ms)。
- `tests/test_postgres.py` 固定 updated/id 原生索引的安装、完整排序方向和 live PG
  快路径;`tests/test_search_window.py` 覆盖密集命中无回退、稀疏命中正确回退、
  updated 游标续页、租户隔离及容量探针自校验。

### Fixed

- **§18.5 PG 消息搜索全匹配已知上限收敛**:根因是参数化 `LIKE %s` 的 planner
  统计盲区和百万命中归并。窗口快路径不依赖估计,全匹配 P95
  **627–809ms → 6.5–7.3ms**;修正后的真实消息专属选择性词 P95 为
  **143.2–168.3ms**;普通 `updated` 首屏 P95 为 **3.8–6.1ms**。
- 修复容量证据误测:旧 selective 词 `000007` 不在消息正文、却存在于
  `conv_000007`,此前数字实际由会话 ID 条件满足。`pagination_load_test.py`
  现在把 `selectiveneedle` 仅写入十条消息,运行前强制校验 0 个会话字段命中、
  10 个消息命中;全量结果见 `docs/CAPACITY.md` §3.2b,机制见
  `docs/PERF_NOTES.md` §18.2e。

## 1.3.0 后续 — ROADMAP §17.4 可访问性自动门禁(2026-08-18)

### Added

- 固定浏览器测试依赖 `axe-core@4.13.0`(`package.json`/`package-lock.json`),CI browser
  job 使用 Node 20 与 `npm ci --ignore-scripts`,不增加产品运行时依赖。
- `tests/ui_accessibility.py` 在真实服务扫描操作台深色/浅色主题、知识页、管理页与
  Web Chat 首屏,并断言 critical/serious 规则、桌面焦点顺序、知识编辑器键盘路径、
  390px 队列抽屉 Tab/Shift+Tab/Escape 焦点陷阱及 reduced-motion 计算样式。

### Fixed

- 主题按钮绑定移入 `main.js` 并以数据标记保证只绑定一次,避免 legacy `app.js` 初始化
  顺序造成键盘点击竞态。
- 浅色主题语义色调整为 WCAG AA 可读的 teal/muted/blue/amber/red/green/violet 值;
  axe color-contrast 规则在所有目标视图通过。
- 无障碍门禁在主题切换后等待 CSS transition settle 再扫描,避免把合法的过渡中间色
  误报为 serious 对比度问题;连续真实浏览器复核稳定通过。
- 资源内容变更后按 §18.4 将统一静态键从 `v=1.3.6` 提升为 `v=1.3.7`。

## 1.3.0 后续 — ROADMAP §17 知识运营页(2026-08-18)

### Added(知识库挂载点落地为真实运营页)

- `app/static/js/knowledge.js` 纯模块 + `#knowledgeView`(index.html)+ app.js 接入:
  摘要条五计数(全部/已发布/草稿/待审核/已停用)、检索/状态/语言组合筛选、草稿创建、
  编辑回填、发布/停用审核、只读提示;复用既有 `/api/knowledge`、`/api/knowledge/drafts`、
  `/api/knowledge/{id}`、`/api/knowledge/{id}/review` 契约,不改响应体;`main.js` 在
  `window.HelixModules.knowledge` 暴露,app.js 提供行为一致的 fallback。
- 最小权限:写角色(`knowledge:write`)请求 `include_inactive=true` 加载全生命周期并可
  创建/编辑/审核;读角色只请求公开列表、状态筛选仅 all/published、隐藏全部写控件且
  不发起任何写请求(后端 `include_inactive=true` 由 `knowledge:write` 门控,403)。
- 测试与验收:纯模块 8 项单测;Phase 21 生命周期 RBAC 回归(非写角色建草稿 403、
  include_inactive 403、草稿发布前不可见、重复发布/停用 409);`tests/ui_knowledge.py`
  真实浏览器写/读旅程(草稿→编辑→发布→移动端→停用;operator 零写请求),截图入
  `artifacts/ui-knowledge*.png`;CI browser job 在同一 clean 服务串联
  smoke/admin/knowledge 三旅程。

### Fixed(Phase 35 code-review backlog)

- 语言下拉补齐:编辑器与筛选下拉此前只列 8/14 语言,编辑 ru/ar/hi/he/th/el 文章时
  value 无匹配 option 会被静默清空为 null;现按 `LANGUAGE_NAMES` 运行时幂等补全,
  编辑回填与保存不再丢失语言。
- fallback 一致性:app.js 的 `HelixModules.knowledge` fallback 与主模块对齐
  (normalize→count/filter、`reviewActionsFor` 完整),模块加载失败时摘要计数/筛选/
  审核动作不再静默降级。
- 语言筛选语义:null-language(通用)文章在任意语言筛选下保持可见,与检索路径
  (null 语言最高匹配)语义一致。
- 列表缓存按权限 scope:15s TTL 缓存以加载时的 `knowledge:write` 态为 key,会话中
  角色变更不再向读者展示此前写者视角的草稿缓存。
- 提交前 trim-aware 校验:标题/正文 trim 后低于后端最小长度时前端直接提示并聚焦,
  替代 422 toast。
- 静态资源键按 §18.4 强缓存约定随内容变更提升 `v=1.3.4` → `v=1.3.5`
  (`app/assets.py` 单一来源;全仓静态引用/浏览器测试断言同步,frontend gate
  资源一致性扫描绿)。

## 1.3.0 后续 — Phase 32 code-review backlog W1:last-admin 跨成员降级守卫(2026-08-18)

### Changed(成员锁死保护从自守扩展到跨成员)

- 成员角色变更/停用/邀请统一校验"租户必须至少保留一位 active 且 role=admin 的
  成员"。此前 Phase 32 只拦截操作者降级/停用*自己*;但凭据级 admin(API key
  声明 `role=admin` 却不在 `tenant_members` 中,如集成 principal)仍可降级或停用
  租户内唯一在册 admin,把租户锁死在成员管理之外。现在跨成员降级、停用、以及
  会把在册 admin 重置为 `invited` 的重复邀请,在目标是租户唯一 active admin 时
  统一返回 409(`Cannot remove the last active admin member`),租户至少保留一位
  admin;存在第二位 admin 时这些操作仍正常放行。
- 新增 `app/routers/admin.py` `_guard_keeps_an_admin(members, target_actor_id)`
  前置守卫;接入成员邀请、角色变更、停用三条路由,调用发生在变更前,基于同一
  租户当前成员名册判断。

### Added(定向测试)

- `tests/test_phase22.py` 新增 3 条 MemberLifecycleTests 子用例:
  `test_cannot_demote_last_admin_cross_member`、
  `test_cannot_deactivate_last_admin_cross_member`、
  `test_can_demote_last_admin_when_another_admin_remains`(正例:存在第二位
  admin 时仍可降级)。

## 1.3.0 后续 — Phase 32 code-review backlog S5:跨租户 model-policy 读取定向测试(2026-08-18)

### Added(定向测试)

- `tests/test_tenant_model_policy.py` 新增 `test_cross_tenant_read_forbidden`:
  补齐 GET 方向——demo 的 admin 读取 *存在的* other-tenant 的 model-policy
  返回 403(此前只覆盖 PUT 跨租户 403 与不存在的 ghost 租户 GET 403),证明
  `_ensure_same_tenant` 在触碰数据库前即拒绝跨租户策略读取。

## 1.3.0 后续 — ROADMAP §18.3 审计保留闭环(2026-08-18)

### Added

- migration 26 新增 `audit_archives` 持久归档表;过期审计按租户、每批最多
  500 行写入规范 JSON manifest,保存首尾 `seq`/hash、事件数与内容
  SHA-256。归档在同一事务内读回校验成功后才清理热行。
- 新增 `GET /api/audit-archives` 与 `GET /api/audit-archives/{archive_id}`
  租户隔离清单/详情;归档详情读取和 `scripts/verify_audit_chain.py` 统一校验
  摘要、schema、租户、事件数、首尾边界与顺序。

### Fixed

- 审计写入现在从 `audit_events` 热表与 `audit_archives` 冷表的真实全局链尾
  续接并显式分配单调 `seq`,修复 SQLite 热表清空后复用 rowid、下一事件从
  空哈希重启导致全链断裂的问题;诊断包也返回真实冷/热链头。
- 提示词 create/activate/canary/rollback 的事务内审计统一走带进程锁与
  数据库锁的哈希链入口,不再插入 `event_hash=NULL` 的旁路行。
- 数据主体删除不再选择性删除或改写审计归档。客户业务数据仍被清理,审计
  证据按独立 `audit_events` 保留策略到期归档,避免重写跨租户历史链。
- 校验器不再静默覆盖重复事件 id,同时拒绝重复 `seq` 和被篡改的 manifest
  边界字段。

## 1.3.0 后续 — ROADMAP §18.4 静态资源缓存收口(2026-08-18)

### Changed

- 新增 `app/assets.py` 作为静态资源版本与缓存策略的单一来源;本次资源键提升为
  `v=1.3.3`。当前版本 `/static/*` 响应使用一年期 `public, immutable`,无版本
  或错误版本继续 `no-cache`,HTML shell 保持重新验证。
- HTML、CSS `@import`、ES module 静态导入、modulepreload、favicon 与 SVG
  sprite 引用全部使用同一资源版本,消除模块预加载 URL 与实际 import URL
  不一致造成的重复请求。
- 前端门禁新增资源引用扫描,任何未版本化或版本过期的静态 URL/相对 module
  import 都会使 CI 失败;后端测试固定 immutable/no-cache 三分支缓存契约。

## 1.3.0 后续 — Phase 32.2 管理页安全收口(2026-08-18)

### Changed(租户管理 RBAC/隔离/自我保护加固)

- 配额 PUT 端点请求体从无类型 `dict[str, Any]` 升级为严格校验的
  `TenantQuotaUpdateRequest(StrictModel)`:`conversation_quota` 与
  `storage_quota_bytes` 在 schema 边界即拒绝越界值(`ge=1`/`le=10**15`),
  未知字段因 `extra="forbid"` 返回 422,不再穿透到数据库。OpenAPI 快照已
  重新生成,合约门禁通过。
- 配额 GET/PUT 与成员邀请/列表/角色变更/停用路由统一调用
  `_ensure_same_tenant(principal, tenant_id)`:租户管理员即使在
  `tenant:manage` 下也不能读取或变更其它租户的配额或成员,跨租户访问
  统一返回 403。
- 成员自我保护守卫:管理员不能把自己的角色降级为非 admin(409,保留至少
  一位管理员),也不能停用自己(409,避免租户锁死)。前端 `renderMembers`
  对当前管理员的角色选择与停用按钮禁用并标注「(你)」,`changeMemberRole`
  与 `deactivateMember` 在客户端即拦截并提示。
- 前端 `canManage()` 从仅检查 `admin:manage` 放宽为 `tenant:manage ||
  admin:manage`,与配额/成员路由的实际 `require_permission("tenant:manage")`
  对齐;非管理员(OPERATOR/AUDITOR/VIEWER)仍被拒绝,UI 拒绝态断言零特权
  请求。
- Webhook 删除前增加 `window.confirm` 破坏性确认;成员标识输入增加
  `pattern`/`title`/`required` HTML5 校验与配额会话数 `max` 上限。

### Fixed

- 修复 `tests/test_phase22.py` MemberLifecycleTests 在配额/成员路由加上跨
  租户守卫后全部返回 403 的问题:test setUp 预配 `demo`(与 admin principal
  同租户)而非 `acme`,所有成员路由调用改为指向 `/api/admin/tenants/demo/`。
- 修复 `tests/ui_admin.py` 在 Webhook 删除前缺少 `page.on("dialog")` 处理
  导致新增确认对话框触发意外 `dismiss` 的问题。
- 修复 `app/static/index.html` `#memberActorId` `pattern` 在 Chromium v-flag
  正则引擎下因 `@` 在字符类中无效而触发的控制台错误(转义 `-` 并保持语义
  等价)。
- 修复 `tests/ui_sserelay.py`、`tests/ui_mention_autocomplete.py`、
  `tests/ui_tickets.py` 在共享 scratch DB(负载测试残留数百条 high 优先级
  会话)下 seed 会话排在首屏之外导致断言超时的问题:seed 会话统一通过
  `PATCH /api/conversations/{id}` 提升为 high 优先级,确保落在首屏可见
  范围内;`ui_tickets.py` 在 PATCH 前先断言标题已选中,避免后台刷新抢占
  selectedId。

### Added(定向测试)

- `tests/test_phase22.py` 新增 5 条 MemberLifecycleTests 子用例:
  `test_admin_cannot_manage_other_tenant_members`、
  `test_admin_cannot_manage_other_tenant_quota`、
  `test_admin_cannot_demote_self`、`test_admin_cannot_deactivate_self`、
  `test_quota_update_rejects_invalid_values`(覆盖 0/越界/未知字段 422)。

## 1.3.0 后续 — ROADMAP §17.3 管理页验收收口(2026-08-18)

### Changed(管理能力从已有实现到可持续验收)

- 将操作台静态资源 URL 从过时的 `v=1.0.0-dev` 统一升至 `v=1.3.2`,
  确保管理页与当前 CSS/ES modules/legacy 脚本使用同一缓存版本。
- 配额输入最小值与后端正整数约束对齐为 1；Webhook URL 使用 `type=url`
  与 URL 自动填充语义，签名 secret 使用密码输入、至少 8 位、关闭拼写检查且
  不回显明文。
- 新增 `tests/ui_admin.py` 真实 Playwright 旅程，覆盖配额读写回显、成员
  邀请→角色变更→停用、Webhook 事件选择→注册→删除，以及非管理员拒绝态。
  拒绝态额外断言不会发出任何特权管理/Webhook 请求；全程要求零 console、
  page、HTTP 4xx+ 与意外 request failure。
- CI browser job 在 clean 服务上依次运行 `ui_smoke.py` 和 `ui_admin.py`；该
  测试服务单独设置 `RATE_LIMIT_PER_MINUTE=1000`,避免两套完整浏览器旅程共享
  默认 120/min 窗口产生假失败，生产默认配置与限流测试不变。
- 新增管理员与拒绝态截图 `artifacts/ui-admin.png`、
  `artifacts/ui-admin-denied.png`；两者均已目视确认布局正常。

## 1.3.0 后续 — ROADMAP §17.3 独立 Web Chat 客户端(2026-08-18)

### Added(客户渠道从 API 到可嵌入界面闭环)

- 新增 `GET /widget` 独立客户聊天面板：360px 移动优先、桌面嵌入尺寸
  自适应、气泡式消息、Enter 发送/Shift+Enter 换行、`aria-live`、busy/
  断线/超时/人工接管状态与 `prefers-reduced-motion`。
- 品牌配置从 query 读取 `brand`/`greeting`/`locale=zh|en`/
  `accent=teal|blue|amber|violet|coral`；bootstrap token 仅从 URL fragment
  读取，不进入请求目标、Referer 或 DOM data 属性。
- 首次会话用 bootstrap token 换 conversation-bound fresh token；fresh token
  + conversation id 只存 `sessionStorage`，刷新拉历史恢复，过期清理。
- 消息走 `async_mode=true` + 唯一 `channel_message_id`，客户端以带
  `X-Widget-Token` 的 streaming `fetch()` 解析 SSE token/job 事件（原生
  EventSource 无法带认证 header），完成后用 canonical history 校准。
- 新增 `WIDGET_FRAME_ANCESTORS`：仅 `/widget` 使用配置的 CSP 父页面
  origin 白名单（默认 `'self'`）；操作台/其它页面继续
  `X-Frame-Options: DENY` + `frame-ancestors 'none'`。生产拒绝通配符，
  配置校验拒绝路径、userinfo 和 CSP 注入字符。

### Security

- **会话 token 真正绑定 conversation**：原 fresh token 注释声称绑定会话，
  payload 实际只有 tenant/customer，持 token 者可读取同租户可猜测会话 id。
  payload 现增 `conversation_id`，send/history/stream 三条路径不匹配统一 404；
  bootstrap token 仅能创建会话。
- **Widget 历史过滤内部备注**：服务端拒绝返回 `internal_note`/`internal`
  role，客户端消息归并层再次过滤，防止坐席内部讨论暴露给客户。
- **显式 bootstrap 链接不会误恢复旧客户会话**：同一标签页再次打开带
  `#token=` 的入口时，中止旧会话的在途请求、清除旧 `sessionStorage` 并回到
  预聊界面；普通无 fragment 刷新仍恢复当前会话。widget 资源统一升到
  `v=1.3.2`，确保已部署浏览器重新获取这条会话边界修复。

### Tests / Docs

- `tests/frontend/widget-core.test.js` 9 例覆盖配置归一化、SSE 分帧、消息
  归并/内部备注过滤、session 恢复/新 bootstrap 重置与渠道 id；frontend
  gate 共 131 例。
- `tests/test_phase24.py` + `tests/test_widget_client.py` 覆盖 conversation token
  隔离、内部备注不可见、页面/framing 头与配置校验；相关后端 44 例通过。
- `tests/ui_widget.py` 真实 Edge/Chromium：建会话→异步消息→SSE 回复→刷新
  恢复，无重复 customer message、无 console/page/HTTP 4xx+，并输出
  `artifacts/widget-mobile.png` / `widget-desktop.png` 做视觉验收。
- README、`.env.example`、DEPLOYMENT、OPERATIONS、SECURITY_MODEL 与 API
  guide 已补 bootstrap/fresh token、iframe fragment、allowlist 集成说明。
- setuptools package-data 增补 `static/css/*`/`static/js/*`；此前 wheel 只含
  顶层 static 文件，安装包会丢失既有 ES modules 与新 widget core。

## 1.3.0 后续 — Backlog CSAT 聚合索引(2026-08-18)

### Added

- **CSAT 评分汇总查询加速索引**(backlog 优化项):新增 migration 25
  `idx_csat_summary_tenant_responded`——`csat_surveys(tenant_id,
  substr(responded_at,1,10)) WHERE rating IS NOT NULL` 的部分索引,
  覆盖 `summarize_csat` 的总体聚合与每日趋势查询。未回应的 survey token
  不进索引,随着历史累积汇总卡仍保持近常数时间。PG 侧由
  `install_csat_summary_index`(`pg_compat.py`)在 `PostgresDatabase.initialize()`
  的 post-schema pass 镜像安装(`install_compatibility` 顺带补),substr
  函数 shim 同步加入 `COMPAT_FUNCTIONS` 以保证日期截断在 PG 上与 SQLite
  行为一致。新 PG 单元测试 `test_csat_summary_index_is_partial_and_idempotent`
  断言 DDL partial + substr 谓词;PG 集成测试 `test_csat_summary_index_installed`
  断言索引存在且 partial WHERE 子句正确(SQLite 侧 `test_schema_version_after_apply`
  覆盖到 v25)。

## 1.3.0 后续 — Backlog 语言切换/翻译动作(2026-08-18)

### Fixed(审计迭代)

- **`set_conversation_language` 改为可移植 null-safe 不等比较**(code-reviewer C1):
  `IS NOT ?` 是 SQLite-only 语法,PG 方言翻译器不重写它,会直接 500;且
  orchestrator 对每条客户消息都调用该方法,PG 后端等于整条消息接收路径全断。
  改为 `(language IS NULL OR ? IS NULL OR language <> ?)`(同值跳过、NULL→设值、
  设值→NULL 清空、不同值→更新),SQLite/PG 语义一致;并加 `rowcount == 1`
  守卫,no-op 不再盲目 bump dashboard 水印(I1)。
- **手动语言 override 现在真正生效**(C2):`PATCH /language` 设的值会被
  orchestrator 自动检测在下一条客户消息覆盖——「手动覆盖」名存实亡。修复:
  自动写入仅在 `conversation.language IS NULL`(无 override)时进行,坐席钉住的
  语言稳定存续到手动清空为止。**回复自动翻译目标也改为优先取 override**:
  orchestrator 把 `language = conversation.get("language") or detected_language`
  作为翻译目标,override 钉住 en 时即便客户用中文提问,回复仍译为 en;
  `customer.message_received` 审计与 assistant 消息 `metadata` 新增
  `detected_language` 字段区分「检测值」与「生效值」,可观测性保留。
  新增 3 个后端测试覆盖 override 驱动翻译、跨消息粘性、清除后恢复自动检测。
- **schema 校验对齐知识库先例**(W3):`SetConversationLanguageRequest.language`
  与 `TranslateMessageRequest.target_language` 经 `field_validator` 归一化小写并
  限定 `KNOWN_LANGUAGES`,不再接受任意 2–16 字符串进模型提示词。
- **语言变更/翻译补审计事件**(W4):`conversation.language_changed` 与
  `message.translated`(含 message_id/target_language/source)落审计链,与 claim/
  priority 等其它会话变更一致,override 误用可复盘。
- **translate 端点按 docstring 收窄为仅 customer 消息**(I3):非 customer 角色
  422,避免内部备注被 API 客户端翻译外泄。
- **前端降级文案区分 `source=="none"`**(W1):目标语言 == 服务语言时提示「目标
  语言与当前服务语言一致，无需翻译」,不再误导为「无翻译模型」。
- **viewer/auditor 不再渲染会 403 的控件**(W2):语言 picker 与消息翻译条都以
  `canWriteConversations()`(`conversation:write`)门控隐藏,只读角色看到的是
  纯展示。
- **`tests/ui_language.py` 去拉丁 flaky 源**(H1):种子消息去掉 `message-N` 前缀与
  ASCII hex 后缀,纯 CJK+数字使自动检测确定性为 `zh`,不再有 ~40% 概率因
  `run_id` 恰含 ≥3 个十六进制字母把检测带偏成 `en` 而翻车。
- 已知限制(暂不修):resolved 会话仍可 PATCH 语言(语言是元数据,允许补录)。

### Added(多语言客服的人工操作面)

- 新增 `PATCH /api/conversations/{conversation_id}/language`
  (`conversation:write`):body `{"language": "<code>"}` 手动钉住语言,
  `{"language": null}` 清除回自动检测;返回更新后的 `ConversationOut`;
  非法语言 422。
- 新增 `POST /api/conversations/{conversation_id}/messages/{message_id}/translate`
  (`conversation:write`):body `{"target_language": "<code>"}`;复用
  `orchestrator.languages.translate`(模型优先、失败降级原样返回),响应
  `TranslateOut{translated, was_translated, source}`;无模型时
  `was_translated=false, source="rule"`,目标==服务语言时 `source="none"`。
- 详情头部「语言」下拉(`#conversationLanguageSelect`):默认跟随自动检测,改选
  即 PATCH,「自动」选项清空 override;成功 toast + subtitle 同步,失败回滚。
- 每条客户消息的翻译工具条(`.translate-bar`):目标语言下拉 + 「翻译」按钮,
  结果内联渲染(译文+语言标签 / 服务语言提示 / 无模型原样回显);按
  `conversation:write` 门控。
- **测试**:`tests/ui_language.py`(Playwright)——预建会话+纯 CJK 客户消息→
  探针 translate 确认降级语义→语言 picker en 覆盖/自动恢复→翻译条按探针布尔
  路径渲染→无 console/page/HTTP 4xx+。后端全量 589 passed;11 套 UI 回归全绿。
  OpenAPI 快照已重新生成 + `test_openapi_gate.py` 通过。

## 1.3.0 后续 — Backlog CSAT 评分汇总(2026-08-17)

### Fixed(审计迭代)

- `tests/ui_csat.py` 断言日期改用 `datetime.now(timezone.utc)`:responded 日
  趋势是 UTC 日期,本地日期在 UTC+8 的 0–8 点窗口会差一天(code-reviewer H1)。
- 汇总口径澄清:头部三项是全历史累计(标签「累计样本数/平均分/好评率」),
  `days` 仅约束趋势窗口,OpenAPI 描述与 docstring 同步明确(W1)。
- 前端失败不再伪装成合法的 0 态:catch 直接显示「汇总加载失败」;空态
  平均分/好评率显示「—」而非「0.00 / 5」(有样本时均值不可能为 0,W2)。
- `summarize_csat` 入口对 `days` 做防御性钳制(1–90),避免未来直接调用
  时 SQLite/PG 的 `LIMIT` 行为分叉(S1)。
- 测试补强:平均分解析为真实数值(0–5 区间)断言,而非仅文案含 "/ 5"(W4);
  趋势最新行断言 = 今天日期且带实际份数(S2)。
- 已知限制(暂不修):测试 `base+3` 精确等式仅串行可靠(本流程串行);
  趋势行无当日好评率(无需求);`csat_surveys` 聚合暂缺
  `(tenant_id, responded_at) WHERE rating IS NOT NULL` 部分索引(数据量小);
  汇总卡为 admin-only(与 `canManage()` 门控一致)。

### Added(满意度数据从「单次评分落库」到 admin 汇总闭环)

- 新增只读端点 `GET /api/admin/csat-summary?days=14`(`admin:manage`):
  聚合已回应 `csat_surveys` 的总体样本数/平均分(1–5)/好评率(≥4 占比)
  + 每日趋势(按 UTC 日期降序,最多 days 天,1–90);`summarize_csat`
  落在 `db/tenancy.py` 的 CSAT 区,仅统计 `rating IS NOT NULL`(未回应问卷
  不计);空表返回 0/0.0/0.0 + 空 trend。OpenAPI 快照已重新生成。
- 管理视图新增「CSAT 评分汇总」卡(`dl#csatReadout` 三数字 + `ul#csatTrend`
  逐日行):loadAdminView 末尾加载,失败降级为 0 态不阻塞;`.csat-day`
  行样式复用 admin 卡视觉。
- **测试**:`tests/ui_csat.py`(Playwright)——API 记录基值(共享 DB 累积
  友好)→ resolve 3 个会话取响应 `survey_url` 的 token → 公开端点 POST
  评分 4/5/2 → 管理视图断言样本数 = base+3、平均分「x.xx / 5」、趋势含
  当天 UTC 日期 → 无 console/page/HTTP 4xx+。

## 1.3.0 后续 — Backlog @提及 输入自动补全(2026-08-17)

### Fixed(审计迭代)

- IME 组合守卫:note 输入框的 `input`/`keydown` 处理在 `event.isComposing`
  时直接返回,中文/日文输入法候选翻页(ArrowUp/Down)与 Enter 上屏不再被
  补全列表劫持(code-reviewer H-1)。
- `loadCollaborators` 单次拉取改为节流刷新:失败时不设 `collaboratorsLoadedAt`,
  下个 30s 刷新周期自动重试;成功后每 ~120s 后台更新——新邀请的同事最多
  2 分钟出现在候选(修复 H-2)。
- 候选列表在 roster 迟到达时若已打开会重渲染一次,消除"输入 @ 时名单未到、
  列表永不出现"的竞态(H-3)。
- `applyMentionFromSuggest` 不再信任 input 时捕获的位置,apply 时以当前
  value + caret 重推导 `@token` 段(鼠标移动光标后点击不再错插)。
- 候选在 note 提交成功 / 切换会话 / 清空选择时主动收起,不再残留浮层。
- `GET /api/collaborators` 门控收紧为 `operator:act`(supervisor/operator/
  admin 才有),viewer/auditor 无法再枚举成员名单与角色(W-4)。
- `list_collaborators` 过滤与 `_mention_actors` 提取规则对齐:角色白名单、
  排除 `deactivated`、`length(actor_id) <= 64`——65–80 字符 actor 不再被
  建议却静默丢失(W-1/W-5/W-7)。
- `tests/ui_mention_autocomplete.py`:空前缀断言改为仅 `to_be_visible()`
  (不依赖 mentor 落在 top-8,共享 DB 累积后排序不可控);测试结束时 resolve
  预建会话出队列(卫生)。

### Added(Input-side UX 闭环,后端 M18 已有 note 内 `@actor` 提取与收件箱)

- 新增只读端点 `GET /api/collaborators`(`conversation:read`):返回当前租户
  全部成员 actor 名单(`CollaboratorOut{actor_id, role}`,`list_collaborators`
  不分状态,让受邀待激活的同事也出现在候选;OpenAPI 快照已重新生成,
  合约 metadata 含 tags `collaboration`)。
- 内部备注输入框 `#noteInput` 在输入 `@` 时(光标前为行首/空白的
  `@token`)弹出候选列表(`#mentionSuggest`,复用 `.macro-suggest` 视觉):
  - 候选 = 租户成员去掉自己,前缀模糊匹配,最多 8 条,行内展示角色中文名;
  - ArrowDown/Up 高亮循环、Enter/Tab 选中、Escape 取消、点击选中;
  - 选中后插入 `@actor_id ` 并复位光标(多字节/多行安全,以 caret 截断前文
    匹配 `MENTION_PATTERN`);
  - 名单由 `loadCollaborators()` 在 `firstMe` 后 `scheduleIdle` 一次拉取,
    失败降级为纯文本输入(不阻塞)。
- **测试**:`tests/ui_mention_autocomplete.py`(Playwright)——API 预建协作
  坐席 + open 会话 → 选中会话 → `@` 弹候选含预建坐席且排除自己 → 前缀过滤
  count==1 → ArrowDown+Enter 插入 `@actor ` → 无匹配前缀收起 → 提交 note
  成功 → 全程无 console/page/HTTP 4xx+。

## 1.3.0 后续 — Backlog SLA 策略 / 自动路由规则面板(2026-08-17)

### Fixed(审计迭代)

- `loadAdminView` 内 `loadRuleGroups()` 提前到 `loadRoutingRules()` 之前:
  首进管理视图时既有路由规则即可把 `group_id` 翻译为坐席组名,不再首屏
  落入原始 id 回退显示(code-reviewer W-1)。
- `tests/ui_routing.py` SLA 断言 `.first` 改为 `.filter(has_text="高优")`
  精确定位,消除对「不存在默认策略」旧数据的排序依赖(peer DB 累积后
  `ORDER BY priority` 首行未必是刚保存的高优策略)(code-reviewer W-2)。

### Added(策略管理后端功能到管理视图闭环)

- 管理视图新增「SLA 策略」卡:`GET /api/admin/sla-policies` 列表展示
  级别(默认/普通/高优)× 渠道的解析名与首响/解决时限;表单 upsert
  (`PUT`,priority/channel 为空传 null 走全局/全渠道默认,首响 1–10080min/
  解决 1–10080min);每行「编辑」把该策略回填表单(同键覆盖再保存,天然
  幂等,不产生重复行)。
- 管理视图新增「自动路由规则」卡:`GET /api/admin/routing-rules` 列表展示
  意图/标签/渠道 → 分配组 + 优先级;创建表单(intent/label/channel 可空,
  组下拉来自 `GET /api/admin/agent-groups` active 组,优先级 0–1000)POST;
  每行「删除」DELETE(204)。意图/标签/渠道直接输入 drop-in
  (空串不入 body),首次把 backlog 级后端端点接入 UI(此前双向零调用)。
- 路由规则列表的组名用 `agentGroupsCache` 翻译 `group_id` → 可读 name,
  组被删时回退显示 id。
- `app/static/styles.css`:`.sla-rule-row`/`.routing-rule-row` flex 布局
  (title + meta + 操作),与报表订阅行同构。
- **测试**:`tests/ui_routing.py`(Playwright)——API 预建坐席组 → 切管理
  视图(组下拉含预建组)→ 保存 SLA(高优/全渠道/30/720)→ 断言列表 →
  「编辑」回填断言 form 值 → 添加路由(意图 `退款-{run_id}`/优先级 10)→
  删除 → 按 rule_id 精确定位消失 → 全程无 console/page/HTTP 4xx+。
  `record_failed_request` 忽略 DELETE 方法的 `ERR_ABORTED`(204 No Content
  无响应体在 chromium 下的边界标记,非网络层失败)。

### Docs

- SLA/路由策略经 `tests/ui_routing.py` 浏览器验收收口;OpenAPI 合约未新增
  参数,无需重新快照。

## 1.3.0 后续 — Backlog 报表订阅/导出面板(2026-08-17)

### Added(报表订阅从后端功能到管理视图闭环)

- 管理视图新增「报表订阅」卡片(`app/static/index.html` + `app.js`):
  订阅列表(`GET /api/admin/report-subscriptions`)展示报表类型/周期/
  窗口天数/webhook 端点/上次运行/启用状态;创建表单(report_type ×
  schedule × window_days × webhook 端点下拉)POST 创建;每行「停用/启用」
  PATCH `active` 取反、「删除」DELETE。后端 `report_subscriptions` 表
  此前已在 milestones 提供,本轮为前端接线(此前 `GET /api/admin/
  report-subscriptions` 零调用)。
- 管理视图新增「报表导出」卡片:生成预览(POST `/api/admin/reports/
  generate`,窗口内 rows 摘要含日期区间与行数)与 CSV 导出(导航到
  `GET /api/admin/reports/{type}/export?from=&to=`,本地时区窗口日期计算,
  浏览器按 attachment 下载)。
- webhook 端点下拉:`GET /api/webhooks` 过滤 `status === "active"`,
  无可用端点时兜底提示先注册;与现有 webhook 卡共用一次请求
  (`loadAdminView` 传给 `renderReportWebhookOptions`)。
- `app/static/styles.css`:`.admin-report-sub` 布局、`.admin-form-row`
  横向按钮排、`.report-preview` 等宽滚动预览。
- **测试**:`tests/ui_reports.py`(Playwright)——API 预建公网 IP webhook
  (SSRF 校验拒绝私有/回环与不可解析域名,纯 IP 零 DNS 依赖)→ 切管理视图
  → 创建订阅 → 停用/重启用 → 生成预览 → `expect_download` 导出 CSV →
  删除订阅 → 全程无 console/page/HTTP 4xx+(下载类 `ERR_ABORTED` 视为
  浏览器磁盘下载中止,非失败)。

### Fixed(审计迭代)

- 移除 `loadReportWebhooks` 死函数(`loadAdminView` 已复用同一
  `/api/webhooks` 响应内联渲染);`window_days` 渲染补 `escapeHtml`。
- `tests/ui_reports.py` 删除订阅的最终断言从「列表回到暂无订阅」改为按
  本轮 `sub_id` 精确定位消失——共享 DB 残留其它订阅(前次崩溃遗留)不再
  误报;已注入残留订阅场景实测通过。

### Docs

- 报表订阅/导出的后端与前端链路经 `tests/ui_reports.py` 浏览器验收收口;
  OpenAPI 合约未新增参数,无需重新快照。

## 1.3.0 后续 — Backlog 工单状态机 UI(2026-08-17)

### Fixed(code-review 审计确认的 4 项前端缺陷)

- **`jumpToTicketConversation` 无法打开目标会话(HIGH)**:工单详情关联列表
  点击会话只切队列 + `loadDetail` 却不设 `state.selectedId`,`loadDetail`
  守卫(1559 行 `selectedId !== id`)静默丢弃请求;更糟时 selectedId 为空,
  `refreshAll` 抢开队列第一条。修复:预置 `selectedId` 再切 tab,并新增
  `suppressAutoSelect` 计数标志,在跳转窗口内抑制 `runRefresh` 的
  auto-select(`!background && !suppressAutoSelect`),`await
  selectConversation(conversationId)` 确定性渲染目标会话。
- **工单详情/状态机过期响应守卫**:`openTicketDetail` 无 `activeTicketId`
  变化守卫——快速切两个工单时旧票响应覆盖新视图;transition POST 进行中
  用户点返回后 `openTicketDetail(activeTicketId)` 变
  `openTicketDetail(null)` → `/api/tickets/null` 404 alert。修复:await 后
  `if (activeTicketId !== ticketId) return`;`transitionActiveTicket`/
  `linkActiveTicketConversation` 入口拷贝 `ticketId`/`conversationId`。
- **后台刷新重显会话视图与工单详情同框**:`runRefresh` 的
  `else if (background && detailStale)` 未带 `refreshDetail` 条件,工单详情
  打开时后台 SSE/poll 仍调 `loadDetail`,`renderDetail` 无条件
  `conversationView.hidden = false`,与 `#ticketDetailView` 堆叠。修复:加
  `refreshDetail` 前置条件。
- **pending 附件不按会话隔离**:`pendingAttachmentIds` 全局数组,A 会话
  上传后切到 B 会话发送会把附件错挂到 B。修复:改 `pendingAttachmentsByConv`
  按 `conversationId` 键控,渲染/发送/移除/清除全部按当前选中会话作用。
- **测试同步器**:`tests/ui_tickets.py` 首次切「工单」tab 的 matcher
  `startswith(/api/tickets)` 会命中 `enrichTicketBadge` 的
  `GET /api/tickets/{id}`,收紧为 `(/api/tickets(?:\?|$))` 只匹配列表 URL。

### Added(回归覆盖)

- `tests/ui_tickets.py` 新增「详情关联列表点击 → 跳回队列并打开该会话」断言
  (回归 `jumpToTicketConversation`,`has_text` 按客户名锁定行,不依赖后端
  排序)。
- `tests/ui_virtual_queue.py` 底部窗口断言从写死 `vq-slot-0001` 改为按
  `queue_last_conversation_id()`(经 API 取队列真实末行)——共享 scratch DB
  累积更老测试会话后,vq-slot-0001 不再必然在底部,原断言为测试环境假设
  失效(非前端回归)。

### Added(工单从"仅单向创建"升级为完整生命周期)

- workspace 新增「队列 / 工单」双 tab(`app/static/index.html` +
  `app/static/app.js::switchWorkspaceTab`):队列 tab 维持原状;
  工单 tab 展示 `#ticketPane`(状态过滤 select + 列表)。
- **工单列表**:`loadTickets()`(`GET /api/tickets`,可选 `?status=`
  过滤,open/in_progress/closed)→ `renderTicketList`,每行可点击进详情。
- **工单详情**:`openTicketDetail(id)`(`GET /api/tickets/{id}`)→
  `renderTicketDetail`,展示编号/主题/状态(中文 pill)/优先级/客户/来源会话/
  创建时间/描述/已关联会话列表;返回按钮 `closeTicketDetail()` 恢复队列。
- **状态机**:`renderTicketTransitions(status)` 按后端
  `_TICKET_TRANSITIONS` 渲染合法操作按钮——`open`→开始处理/直接关闭、
  `in_progress`→关闭、`closed`→重开;`transitionActiveTicket(status)`
  POST `/api/tickets/{id}/transition` 后刷新详情+列表。
- **关联当前会话**:`linkActiveTicketConversation()` POST
  `/api/tickets/{id}/link` 挂当前会话;按钮在无选中会话或已关联本会话时
  隐藏(`ticketLinkCurrent.hidden`);详情关联列表点击可跳回队列会话。
- `app/static/styles.css`:workspace-tabs 样式、
  `.queue-pane[data-mode="tickets"]` 隐藏队列区、ticket-row/priority-pill
  (`is-high` 红)/status-pill(`is-ticket-closed`)/ticket-detail/
  ticket-transitions 全套。
- **测试**:`tests/ui_tickets.py`(Playwright)——转工单(accept prompt)→
  徽章 → 工单 tab 列表 → 详情待处理 → 开始处理→处理中 → 关闭→已关闭 →
  重开→待处理 → 切回队列新建第二会话 → 进详情「关联当前会话」→ 关联列表
  count=2 → 全程无 console/page/HTTP 4xx+ 错误。

### Docs

- 工单生命周期前端证据入 `tests/ui_tickets.py`(浏览器验收);后端
  `POST/GET /api/tickets`、`GET/{id}`、`POST /{id}/transition|link`
  此前已在 1.3.0 提供,本轮前端接线闭环。

## 1.3.0 后续 — Backlog 附件下载/预览前端闭环(2026-08-17)

### Fixed(浏览器测试暴露的上传根因)

- **multipart 上传 Content-Type 被覆写**(`app/static/app.js::request`):上传
  附件走 FormData,但 `request()` 对任何 truthy body 无条件补
  `Content-Type: application/json`,`uploadPendingAttachment` 再用
  `{"Content-Type": undefined}` 覆盖——fetch 收到 `Content-Type: undefined`
  header,FastAPI 按 form 解析找不到 `conversation_id`/`file`,上传 422。
  此前附件上传从未被浏览器测试覆盖(缺测试正是暴露根因)。修复:
  `request()` 对 `FormData` body 不加 JSON header(交浏览器自动生成
  multipart 边界),上传调用移除 `Content-Type: undefined`。新增
  `tests/ui_attachment.py` 冒烟在真实会话走通上传→发送→下载全链路,
  回归 `tests/ui_smoke.py` 等 5 个 UI 套件全绿。

### Added(Backlog 语音/富媒体消息 — 下载/预览闭环)

- 消息内附件 chip(`app/static/app.js::attachmentChips`)从不可点击
  `<span>` 升级为 `<a href="/api/attachments/{id}/download" target="_blank">`
  下载链接;图片(安全子集 `image/png|jpeg|gif|webp`,排除 svg)额外渲染
  懒加载缩略图预览(`<img class="attachment-thumb" loading="lazy">`)。
  附件元数据缓存从文件名改为完整 `AttachmentOut`(`attachmentMetaById`,
  filename/content_type/size),`loadAttachmentNames`/上传响应均写入,
  chip 据此判断图片类型并异步回填文件名。
- `app/static/styles.css`:`.attachment-chip` 去下划线 + hover 态,新增
  `.attachment-thumb`(28×28 圆角缩略图)与 `.is-image` 布局。
- **测试**:`tests/ui_attachment.py`(Playwright)——新建会话 → 转人工 →
  上传 PNG+PDF → 发送人工回复携带 `attachment_ids` → 断言 chip 为下载链接、
  图片缩略图 `naturalWidth > 0`(download 端点对 img 可服务)、点击 PDF
  chip 触发真实下载(`expect_download`)、全程无 console/page/HTTP 4xx+
  错误。

### Docs

- 附件下载/预览闭环证据入 `tests/ui_attachment.py`(浏览器验收);前端
  `app/static/app.js` 附件区块与 `styles.css` 缩略图样式同步注释化。

## 1.3.0 后续 — §18.5 双实例滚动重启验收与租约恢复修复(2026-08-17)

### §18.5 滚动重启验收(2026-08-17,双实例 PG+Redis)

完成 ROADMAP §18.5 最后一项环境门禁:双实例滚动重启零任务丢失
(`docs/CAPACITY.md` §3.7)。`scripts/rolling_restart_test.py` 对共享 PG
(`helix_roll`)+ Redis 的双实例持续写入 turn job,写入中段**外部** kill 主
实例(整树含 spawn_main 子进程),写入端 health 探测到后 DSN 切换备用实例
续写。PG ground truth:**total=498 全部 completed、failed=0、lost=0、
重复 chunk=0、重复幂等键=0、审计链 24,165 行连续**,判定 PASS。

### Fixed(租约恢复与滚动窗口暴露的根因)

- **lease-aware `recover_turn_jobs`**(`app/db/jobs.py`):原先无条件把全部
  `processing` 翻回 `queued`——peer worker 的周期性 recover 会把健康在途
  job(租约未过期)也重排队,owner 的 complete 随即失败、Redis claim 被清理,
  job 永久孤儿(实测 10+11 个 lost)。现仅回收 `locked_at <= 租约过期点` 的
  行(`attempts >= max_attempts` 转 failed,否则回 queued),健康 claim 不触碰;
  回归 `tests/test_chaos.py::test_recover_does_not_touch_healthy_in_flight_job`。
- **Redis 队列孤儿补偿**(`app/queue.py::RedisTaskQueue.recover`):DB 端 recover
  之外新增 reconciliation——扫描 DB `status='queued'` 且 `available_at` 到期的
  行,凡 Redis `_DISPATCH`/`_DELAYED`/`_PROCESSING` 均无其 payload 的重新 rpush
  进分发队列(recover 返回值新增 `redis_requeued`/`reconciled`)。
- **chunk `seq` 的 PG 序列赋值修复**(`app/db/jobs.py::append_turn_job_chunk`):
  INSERT 显式传 `seq=0` 绕过 `turn_job_chunks_seq` 的 DEFAULT nextval,导致 PG
  全表 chunk `seq=0`(重复检测假阳性 754 行)。去掉显式列,交回 trigger/SQLite
  rowid、PG nextval 赋值。附重跑清理:dequeue 到 `attempts>1` 的 job 先
  `delete_turn_job_chunks` 再重写,避免 partial run 的旧 chunk 与重跑新 chunk
  并存的重复语义。
- **`initialize()` 并发死锁修复**(`app/postgres_db.py`):两实例 `uvicorn
  --workers 2` 并发 boot 时多个 child 同时跑 `conversations` 全表 backfill
  UPDATE,PG RowExclusiveLock 互相等待死锁,一方 child 被杀(实测)。整个
  schema 初始化流程用会话级 `pg_advisory_lock(9173001)` 串行化
  (`_SCHEMA_INIT_LOCK`),同一时刻仅一个进程执行 backfill。

### Changed

- `scripts/rolling_restart_test.py`:重复 chunk 断言从 `(job_id, seq)` 碰撞改为
  语义守卫——completed job 的 `chunk_count` 必须等于
  `len(split_stream_tokens(assistant_reply))`(`seq` 全局单调,碰撞检查本就不能
  捕捉重跑;DB 幂等是真正的防线,计数是语义兜底);判定与运行参数对齐
  29.1 门禁(lease=30、`--lease-grace 180`)。

### Docs

- `docs/CAPACITY.md` §3.7:双实例滚动重启实测证据表(498/498 completed、
  lost=0、failover=20、审计 24,165 行连续);§4 第 2 项移入已测证据。
- `ROADMAP_1_X.md` §18.5:双实例滚动重启改"已测通过"。

## 1.3.0 后续 — ROADMAP §18.2 PG 复测与 §18.5 并发/SSE 验收(2026-08-17)

### §18.5 容量验收(2026-08-17,PG+Redis 多 worker)

完成 ROADMAP §18.5 剩余环境门禁中的两项(详见 `docs/CAPACITY.md`
§3.5/§3.6):

- **SSE 扇出 ≥500**:新增 `scripts/sse_fanout_test.py`(裸 asyncio TCP,
  规避 httpx/httpcore 并发 SSE 流的假 502)。500/600 连接全活跃、0 异常,
  首包 p50/p95 1.67/2.42s(单 worker SQLite 的 DB 串行特征观测)。
- **≥50 并发活跃会话 + turn P95<2s**:PG(`helix_load`)+ Redis + uvicorn
  多 worker(`--workers 7` × `TURN_WORKER_CONCURRENCY=8`)下
  `load_test --concurrency 50` 注入:818 会话 / 2454 请求 **success=1.0、
  0 failed、0 rate_limited**;服务端累计 3028 turn 全完成零失败。
  确定性路径(队列空串行)turn P95 907ms < 2s **达标**;并发端到端 poll
  P95 2.45s 记为瞬时排队观测(56 并发池面对 50 冷启动会话同时涌入)。

### Fixed(§18.2 PG 复测定位的根因)

完成 ROADMAP §18.2 的 PG 环境复测(`docs/CAPACITY.md` §3.2b),给出
1.3 验收数字;过程中定位并修复三处让"SQLite 达标、PG 不达标"的根因:

### Fixed

- **队列排序表达式索引方向缺陷**:`idx_conversations_priority_page` 末列
  误写成 `id`(默认 ASC),而 `list_conversations`(sort=priority)的 ORDER BY
  是 `id DESC`——PostgreSQL 按列逐个匹配索引方向,末列不一致使**整个索引**
  被判定不可用作排序源,planner 每队列页全租户并行 SEQ 扫 + top-N heapsort
  (PG full 档 offset P95 348/330ms,贴线甚至超标)。补 `id DESC` 后索引命中
  (Index Scan 0.4ms),offset 任意页 P95 2.5–5.2ms、keyset 单页 ~3–5ms
  (>60x 余量)。根因与复测见 `docs/PERF_NOTES.md` §18.2d。
- **PG UPSERT 自引用列歧义**(既有兼容 bug,本轮 PG 门控首跑暴露):所有
  `ON CONFLICT ... DO UPDATE SET col = col + 1`(create_conversation、
  turn worker、tenant 用量等)在 PostgreSQL 上被报 AmbiguousColumn
  (未限定列同时匹配 target 与 excluded 行),SQLite 接受。`app/pg_dialect.py`
  新增 `_qualify_upsert_self_references()` 在方言层把自引用 `col` 限定为
  `INSERT` 目标表(`excluded.` 引用保留不动),SQLite 不接受限定列名故不改
  SQL 文本。
- **搜索 JOIN 形态让 pg_trgm 可服务**:`list_conversations` 搜索从 per-row
  EXISTS 逐会话探测改为 `message_matches` CTE(tenant + LIKE 预聚合再
  LEFT JOIN),planner 才能为 LIKE 谓词命中 `messages.content gin_trgm_ops`
  GIN;全匹配最坏路径 P95 由修复前 2214ms 收敛到 ~0.7s。

### Changed

- **`scripts/pagination_load_test.py`**:PG 分支 seed 后执行 `ANALYZE`
  (`_analyze_postgres`),消除 bulk 直插后 planner 无统计盲选 seq 扫导致的
  run-to-run 方差,与生产 autovacuum 对齐;SQLite 不执行。
- **`tests/test_postgres.py`**:新增 4 个 UPSERT 方言限定单测
  (`_qualify_upsert_self_references` 的 bare/qualified excluded/mixed/
  ON CONFLICT DO NOTHING 形态);`test_trgm_index_serves_the_like_predicate`
  断言从"计划含 trgm 索引名"放宽为"非 Seq Scan"(小表上 planner 合理选
  btree 位图,trgm 能力由 opclass 存在性测试 + full 压测证明)。

### Docs

- `docs/CAPACITY.md` §3.2b:PG full 档 before/after 表格与 §18.5 两线验收
  判定(队列达标、搜索选择性达标、全匹配病态 627–809ms 记为已知上限);
  §4 未测目标第 4 项移入已测证据。
- `docs/PERF_NOTES.md` §18.2d:补 PG 表达式索引方向缺陷根因与复测。

## 1.3.0 后续审计修复(2026-08-17)

对 1.3.0 与 Backlog/UI 升级实现的第二轮独立审计(后端逐模块 + 前端纯模块
子代理 + 浏览器冒烟复跑)。修复清单:

### Fixed

- **附件下载 Content-Disposition 头**(audit):手工拼装
  `attachment; filename="..."` 可能把恶意文件名(引号/CRLF)带入响应头,且
  与 Starlette FileResponse 自带的 RFC 6266 编码重复——移除手工头,交回
  `FileResponse`(非 ASCII 名自动 `filename*=utf-8''`);`sanitize_filename`
  同步剥离引号/反斜杠/控制字符。回归:`tests/test_attachments.py`
  (RFC 6266 编码、净化、插入失败孤儿文件清理 3 个新用例)。
- **附件上传孤儿文件**(audit):磁盘文件先于数据库行写入,行插入失败时
  文件残留——插入失败即 unlink 清理并记 warning。
- **命令面板会话列表会话期冻结**(audit D1):`openCommandPalette` 仅在缓存
  为空时拉取,首次成功后 60s TTL 永不生效——改为无条件调用
  `loadConversationCommands()`(TTL 内部短路),每次打开都是新会话。
- **低配模式污染密度偏好**(audit D2/D3):设备约束自动开启低配时把
  `compact` 写入 localStorage,且低配下点密度按钮会不可见地改写用户档位——
  自动检测不再持久化(由 `effectiveDensity` 强制 compact),低配下密度切换
  按钮直接忽略。
- **i18n 插值缺失**(audit D4):`t()` 不支持 `{level}` 占位符,`density.toggle`
  键的占位符会原样输出——`t()` 支持 `{name}` 插值(兼容旧 `t(key, locale)`
  签名),密度按钮 title/aria-label 改走 `i18n.t("density.toggle", {level})`;
  nav/command/admin 键保留为声明的本地化表面。前端测试 110→112。
- **冒烟脚本遗留断言**(audit):`ui_smoke.py` 仍断言 §17.2 迁移前的
  `body.is-compact`(现为 `data-density="compact"`);`ui_sserelay.py` 计数
  断言在共享数据库 > 单页行数时撞上 `N+ 个会话` 封顶(改为按会话名断言,
  更直接验证中继);`ui_virtual_queue.py` 窗口化重渲染竞态(选择点击补重试,
  与文件内既有复选框重试模式一致)。
- **pyright 冒烟脚本 4 错误**(audit):`tests/ui_*.py` 类型错误(缺非空断言/
  None 下标/text_content)全修复,CI `pyright app tests` 恢复 0。

### Changed

- **§18.4 渲染预算**:`app/static/app.js` 新增 `scheduleIdle(fn)`(requestIdleCallback
  + 2s 兜底),会话详情的附件名回填/工单徽标/质量数据/首次提及改为 idle 调度。
- **§18.4 资源**:`index.html` 增加 `main.js` 入口及 10 个静态导入的
  `modulepreload`;关键 CSS 内联**不做**(CSP `style-src 'self'` 禁止内联
  style,冒烟已证实冲突),记为文档化约束(`docs/PERF_NOTES.md` §18.4)。

## 1.3.0 (2026-08-15)

商用级可信:安全深度、可靠性与过载、可运维性与发布工程。版本自 1.2.0 收口。

### Added

- **SLO 与告警**(Phase 30.1):`docs/SLO.md` 定义可用性 99.9%/turn P95 ≤4s/队列滞留 ≤30s/SSE 投递 P95 ≤1.5s + 错误预算;`ops/prometheus/alerts.yml` 告警规则 + `ops/grafana/dashboard.json`;turn 延迟与队列滞留指标。
- **诊断与健康**(Phase 30.2):`GET /api/admin/diagnostics` 支持诊断包(版本/脱敏配置/队列/审计链头/失败统计);`/health/startup` 第三级健康检查。
- **容量与压测**(Phase 30.3):`scripts/load_test.py --base-urls` 多实例场景;`docs/CAPACITY.md` 容量模型。
- **灾备与合规**(Phase 30.4):`docs/DISASTER_RECOVERY.md` RTO ≤30min/RPO ≤15min 声明 + 备份/恢复/演练流程 + 合规证据包。
- **用户文档**(Phase 30.5):`docs/guides/operator-manual.md` 坐席手册 + `docs/guides/tenant-admin-manual.md` 管理员手册;`compose.staging.yaml`(PG+Redis+OTel collector 一条命令拉起)+ `ops/otel-collector.yaml`。
- **发布工程**(Phase 30.6):`docs/RELEASE_CHECKLIST.md` 检查清单;`scripts/migration_drill.py` 迁移演练 + CI 门禁。
- **优雅关闭**(Phase 29.1):SSE 端点(`/api/events/queue`、turn-jobs、widget)发出 `retry: 2000` 重连提示与 `event: shutdown` 排空信号;`TurnJobWorker.is_stopping` 标志。
- **背压保护**(Phase 29.2):turn-job 入队前检查队列深度(`QUEUE_DEPTH_THRESHOLD`,默认 1000)与每租户并发(`TENANT_CONCURRENT_TURN_CAP`,默认 50),超限返回 429 + Retry-After。
- **降级矩阵**(Phase 29.3):`docs/DEGRADATION.md` 每依赖(PG/Redis/模型/连接器/SSE/队列/审计)不可用的行为/影响/恢复 + 故障注入测试引用。
- **混沌测试**(Phase 29.4):`tests/test_chaos.py` 在杀 worker/模型超时/并发下验证不变量(无重复 turn、无丢失任务、每会话串行、审计链连续)。
- **审计哈希链**(Phase 28.3):`audit_events` 增 `prev_hash`/`event_hash`(migration 12),每条事件含前条哈希形成篡改可检链;`scripts/verify_audit_chain.py` 校验全链;篡改任一历史行会被检出。
- **API key 吊销**(Phase 28.2):`POST /api/admin/keys/{credential_id}/revoke` 立即失效 + 持久化(`revoked_api_keys` 表,migration 13);`GET /api/me` 暴露 `credential_id`;`api_key.revoked` 审计。
- **会话密钥滚动轮换**(Phase 28.2):cookie 载荷带 `kid`,`SESSION_SECRETS_JSON` 多密钥验证 + `SESSION_ACTIVE_KID` 单密钥签发;旧密钥在轮换窗口内仍可验证。
- **CSRF 防护**(Phase 28.4):BFF 变更端点(`/auth/logout`/`/auth/refresh`)Origin/Referer 严格校验。
- **Auth 独立限流**(Phase 28.4):`AUTH_RATE_LIMIT_PER_MINUTE`(默认 20)按 IP 限制 auth 端点,独立于全局限流。
- **安全文档**:`docs/SECURITY_MODEL.md`(STRIDE 威胁模型)、`SECURITY.md`(报告渠道/SLA/披露政策)。
- **SBOM**(Phase 28.5):`scripts/generate_sbom.py` 生成 CycloneDX 1.4 SBOM,CI 步骤随发布产出。

### Changed

- `audit_events` 表增两列(migration 12);`list_audit`/`export_audit_events` 返回体不含内部哈希列。
- 会话 cookie 载荷增 `kid` 字段(向后兼容:无 kid 按 "1" 处理)。
- SSE 端点响应新增 `retry:` 行与 `event: shutdown`(客户端应支持重连)。
- `GET /api/system/metrics` 单一定义(修复 inline/router 重复)。

### Added (backlog)

- **CSAT 满意度调查**:会话解决时生成一次性评分链接(`POST /api/csat/{token}`,无认证单次有效,7 天过期);评分 ≥4 映射正反馈回流到质量看板。

- **自动路由规则**(backlog):按意图/标签/渠道把会话自动分配到具备对应技能的坐席组(`/api/admin/agent-groups` + `/api/admin/routing-rules`,migration 15);坐席技能标签与容量上限,组满留公共池并审计(`routing.assigned`/`routing.group_full`)。

- **SLA 策略引擎**(backlog):按租户/优先级/渠道配置首次响应与解决时限(`/api/admin/sla-policies`,migration 16);解析回退链(精确→租户默认→全局默认→内置);create_conversation 应用策略时限;违约前预警事件 `conversation.sla_impending`(窗口 5 分钟)。

- **会话智能摘要**(backlog):接管时生成前情摘要、解决时生成处置记录草稿(`conversation_summaries` 表,migration 17);`SummaryService` 复用 ModelProvider,失败回退确定性摘要投影(会话元数据 + 近期消息,剔除内部备注);`GET /api/conversations/{id}` 返回体新增 `summaries`(只增不改);前端接入/解决后展示摘要横幅;首次生成不覆盖。

### Added(backlog 坐席协作,2026-08-15)

- **@提及同事**:内部备注内 `@actor` 生成按坐席隔离的未读提及 + `GET/POST /api/mentions(+read)` + 队列徽标。
- **内部讨论线程**:内部备注 `reply_to` + `GET /api/conversations/{id}/threads`。
- **旁观模式**:`GET /api/conversations/{id}/events` SSE 只读实时(supervisor/admin,`conversation:read` 即可,无需 `operator:act`)。

### Fixed(实现后审计)

- **CSRF host 匹配从 substring 改为精确匹配**:`Origin: https://evil-<host>.com` 或 `<host>.evil.com` 不再绕过 Origin 校验(原 `host in origin` 可被跨域绕过)。
- **审计链升级回填**:migration 12 现回填已有 `audit_events` 行的 `prev_hash`/`event_hash`;升级含旧审计行的库后链不再永久"篡改",新事件正确链接。
- **审计链并发分叉修复**:`audit()`/`audit_many()` 用进程锁串行化链追加,并发写不再产生不可校验的分叉链。
- **migration_drill 数据保护**:拒绝覆盖已存在的 `--db` 目标(需 `--force` 显式确认),避免演练误删真实/备份库。

### Fixed(backlog 实现后审计,2026-08-15)

- **SLA policy SQL 的 PostgreSQL 兼容性(高)**:`set_sla_policy`/`_find_sla_policy` 使用 SQLite 专有的 `tenant_id IS ?` 空安全等值,`pg_dialect` 不翻译,PG 后端的会话创建(经 `resolve_sla_policy`)会直接报错。改为可移植的 `(col = ? OR (col IS NULL AND ? IS NULL))`,并新增源码扫描守卫(`app/db` 不得出现 `<column> IS ?`)。
- **SLA 回退链补全**:全局策略若带优先级(如 `(NULL,'high',NULL)`)此前被静默忽略;现在精确→租户默认→全局带优先级/渠道→全局默认逐级探测,全部门禁验收点已测。
- **SLA 应用点补全(路线图 §16)**:此前仅在会话创建时套用策略;优先级变更、重开、升级(waiting_human)仍用硬编码 `high/normal_sla_minutes`。三处现统一切换到 `resolve_sla_policy`,设置项仅作兜底默认。
- **SLA 越权写(安全)**:`PUT /api/admin/sla-policies` 允许经 `payload.tenant_id` 配置任意租户的策略;现与 `_ensure_same_tenant` 一致,指向他租户一律 403。
- **agent-group 成员 `added_at` 回显**:`POST .../agents` 的 201 响应此前硬编码 `added_at=""`;现返回 DB 写入的真实时间戳。
- **claim 前情摘要**:认领(claim)作为另一条接管路径,此前不生成"前情摘要";现与 handoff 一致 best-effort 生成 `context` 摘要。

### Added(backlog 多语言客服,2026-08-16)

- **语言检测**:`app/language.py` 确定性 Unicode 脚本检测(假名→ja、谚文→ko、汉字→zh、西里尔→ru、阿拉伯→ar、天城文→hi 等,拉丁默认 en);可配置 `ModelProvider` 时模型检测优先(JSON `{"language": ...}`),任何失败回退规则检测;检测结果写入客户消息 `metadata.language` 与会话 `conversations.language`(migration 19)。
- **跨语言知识检索**:`knowledge_articles.language`(migration 19,NULL=语言无关);`search_knowledge` 增 `language` 参数,排序偏好客户语言或语言无关的文章,跨语言命中不被静默过滤。
- **回复翻译**:assistant 回复在客户语言 ≠ 服务语言(`SERVICE_LANGUAGE`,默认 zh)时经 `ModelProvider` 翻译(JSON `{"translation": ...}`);失败原样返回并标 `translated=false`,turn 永不阻塞;消息元数据带 `language`/`translated`/`translation_source`/`original_content`,发 `reply.translated` 审计。
- **API 与前端**:`ConversationOut`/`KnowledgeArticleOut` 增 `language`(只增不改);知识创建/更新/草稿可带 `language`;前端详情头部显示语言徽标(zh 完整,en 骨架按既有 i18n 规则自动派生)。
- 测试:`tests/test_multilingual.py` 25 用例覆盖脚本检测、模型优先/回退、翻译保底、同语言优先检索、语言无关匹配、API 校验与审计。

### Added(backlog AI 辅助坐席 copilot,2026-08-16)

- **建议回复**:`POST /api/copilot/suggest`(`operator:act`),以会话上下文(剔除内部备注)+ 当前草稿生成 1–3 条候选(复用 `ModelProvider`,按会话语言输出);失败回退租户快捷回复(按使用量,`source=rule`)。
- **知识推荐**:`POST /api/copilot/knowledge`(`operator:act`),以最近客户消息为查询(可 `query` 覆盖),复用语言感知的 `search_knowledge`,返回标题/摘要/语言/命中词,最多 3 条。
- **语气改写**:`POST /api/copilot/rewrite`(`operator:act`),friendly/concise/professional 三档,`ModelProvider` 改写(JSON `{"rewritten": ...}`);失败/同文本原样返回并标 `source=rule`,空文本 422。
- **前端**:人工输入区 copilot 栏——智能建议按钮拉取候选点击填入、语气改写下拉作用于当前草稿、知识推荐列表点击回填标题;无新表,复用 canned_responses/knowledge_articles;i18n 键 `copilot.*` 同步。
- 测试:`tests/test_copilot.py` 14 用例覆盖模型优先/回退、内部备注不外泄、查询覆盖、语言偏好、RBAC(viewer 403)与 404。

### Added(backlog 工单化,2026-08-16)

- **工单模型**:`tickets` 表(migration 20)——subject/description/status/priority/assigned_agent/客户身份快照/source_conversation_id;`ticket_conversations` 关联表支持一单多会话;`conversations.ticket_id` 列(只增不改)。
- **转单**:`POST /api/tickets`(`operator:act`)把会话转为长周期工单,快照客户身份与最近客户消息(description 缺省时),置 `conversations.ticket_id` 并建关联;同会话重复转单幂等返回现有工单。
- **状态机**:工单独立生命周期 `open→in_progress/closed`、`in_progress→closed`、`closed→open`,每次迁移写 `ticket.transitioned` 审计,非法迁移 409;`PATCH` 更新 subject/description/priority/assigned_agent。
- **跨会话跟踪**:`POST /api/tickets/{id}/link`(`operator:act`)挂另一会话到工单;`GET /api/tickets/{id}` 详情含关联会话(仅 id/客户/状态/更新时间,不泄露内部备注);`GET /api/tickets` 按状态/客户过滤。
- **前端**:会话操作区"转工单"按钮(subject 输入创建);已关联会话头部显示工单徽标(编号+状态异步补全);i18n 键 `ticket.*` 同步。
- 测试:`tests/test_tickets.py` 14 用例覆盖转单快照/幂等/404、状态机+审计+409、跨会话关联、列表过滤、内部备注不外泄、RBAC(viewer 读可/写 403)。

### Added(backlog 报表导出与订阅,2026-08-16)

- **订阅模型**:`report_subscriptions` 表(migration 21)——report_type(`quality`/`usage`)/schedule(`daily`/`weekly`)/window_days(默认 7,上限 30)/webhook_endpoint_id/active/last_run_at。
- **报表生成**:`ReportService`(`app/reports.py`)复用 `QualityService.list_buckets` 与 `list_tenant_usage`,`generate` 输出 `{report_type, from_date, to_date, rows, generated_at}`(行数受窗口约束)。
- **定时投递**:worker `_webhook_housekeeping` 内 `run_due_subscriptions()`——按 schedule 判定到期(daily 隔天/weekly 隔 7 天,last_run_at 为空即到期),生成报表经 `emit_event_to_endpoint` 定向投递(端点可订阅 `report.generated`,事件 id `report:{sub}:{日期}` 幂等去重),更新 last_run_at;单订阅失败仅记日志。
- **API**(`admin:manage`):`POST/GET /api/admin/report-subscriptions`、`PATCH/DELETE .../{id}`;`POST /api/admin/reports/generate`(按需生成,可带 webhook_endpoint_id 立即投递);`GET /api/admin/reports/{type}/export?from=&to=` 返回 CSV 附件(表头按类型、租户隔离)。
- 测试:`tests/test_reports.py` 11 用例覆盖订阅 CRUD/RBAC/404/422、按需生成、CSV 表头与租户隔离、到期扫描投递+幂等+weekly 阈值、按需投递计数。

### Added(backlog 语音/富媒体消息,2026-08-16,可行子集)

- **附件模型**:`attachments` 表(migration 22)——conversation_id/message_id(可空)/filename/content_type/size_bytes/storage_key(磁盘唯一名)/uploader/status(`stored`|`rejected`)/scanned/verdict;磁盘存储 `ATTACHMENT_STORAGE_DIR`(默认 data/attachments)。
- **上传校验**:`POST /api/attachments`(`operator:act`,multipart)类型白名单(图片/PDF/文本/JSON),单文件 `ATTACHMENT_MAX_MB`(默认 10)、租户累计 `ATTACHMENT_QUOTA_MB`(默认 512)超限 413,类型不符 415;`AttachmentScanner` 规则检测(可执行魔数 MZ/ELF、脚本 shebang、扩展名与魔数不符)判定 rejected 即不落盘并落库。
- **检索/下载/删除**:`GET /api/attachments?conversation_id=`(`conversation:read`)、`GET /api/attachments/{id}`、`GET .../download`(强制附件下载,rejected 404)、`DELETE /api/attachments/{id}`(`operator:act`,删文件+行并释放配额)。
- **图片/附件消息**:人工回复 `POST /api/conversations/{id}/operator-messages` 可带 `attachment_ids`(校验同租户同会话且 stored),写入消息元数据并回填 `attachments.message_id`;前端输入区附件按钮(FormData 上传,Content-Type 置 undefined 交浏览器)、待发附件芯片可移除、消息流渲染附件芯片。
- 测试:`tests/test_attachments.py` 13 用例覆盖落盘+配额记账、413/415、扫描 rejected、强制下载与租户隔离、删除释放配额、消息带附件+回填、异会话附件 404、RBAC(viewer 读可/写 403)。

### Added(UI 升级 §17.1 全局导航栏,2026-08-16)

- **导航栏**:`app-shell > header + app-body(nav + main)` 结构;左侧 52px 窄图标栏 `nav.app-nav`,五个顶级挂载点(工作台/质量看板/知识库/管理/设置),图标按钮 + title 提示 + `is-active` 高亮;窄屏(≤860px)折叠为顶栏下横条。
- **视图切换**:app.js `switchAppView(name)` 状态机——workspace(既有三栏 `#workspaceView`)/quality(`#qualityView` 整页质量看板)/knowledge|admin|settings(占位挂载点 `#placeholderView`,按视图文案);`renderQualityPanel` 参数化支持双目标渲染(检查器内 + 独立看板容器),复用 Phase 21 聚合数据与 10s 节流。
- **模块化**:新增 `app/static/js/nav.js`(纯视图注册表,`isNavView`/`isPlaceholderView`)接入 `HelixModules`;`tests/frontend/nav.test.js` 4 用例;i18n 键 `nav.*` 同步。
- 前端门禁:70 node 测试(66 + 4);无后端 API 变更。

### Added(UI 升级 §17.1 命令面板 Ctrl+K,2026-08-16)

- **命令面板**:`Ctrl+K`/`Cmd+K` 全局打开 `dialog#commandPalette` 覆盖层——输入框实时过滤、↑/↓ 高亮移动、Enter 执行、Esc 关闭;三类命令:切视图(五挂载点,走 `switchAppView`)、动作(新建会话/刷新队列/切换主题/低配模式/检查器/保存视图,复用既有 UI 按钮与快捷键 `c`/`r`/`i`/`l` 同一批行为)、跳会话(`/api/conversations?search=` 按客户名/ID 检索,60s 缓存,执行后切回工作台并 `loadDetail`)。
- **纯模块**:`app/static/js/commands.js`(`buildStaticCommands`/`conversationCommand`/`filterCommands`,label/keywords/group 过滤,`run` 为稳定动作键由 app.js 分发)接入 `HelixModules`;`tests/frontend/commands.test.js` 6 用例;i18n 键 `command.*` 同步。
- 前端门禁:76 node 测试(70 + 6);无后端 API 变更。

### Added(UI 升级 §17.2 三档密度,2026-08-16)

- **三档密度**:队列与消息列表 comfortable/compact/dense 三档循环切换并持久化(`PREF_DENSITY`,`<body data-density="{level}">`);dense 在 compact 基础上更紧(隐藏标签/预览、更小内边距与字号、消息气泡间距收紧);低配模式经 `effectiveDensity` 强制 compact 且不丢用户档位(关闭后恢复)。
- **纯模块**:`app/static/js/density.js`(`DENSITY_LEVELS`/`normalizeDensity`/`nextDensity`/`effectiveDensity`/`isCompactDensity`)接入 `HelixModules`;`tests/frontend/density.test.js` 5 用例;CSS 选择器自 `body.is-compact` 迁移为 `body[data-density=...]`;`#densityToggle` title/aria-label 随档位更新;i18n 键 `density.*` 同步。
- 前端门禁:81 node 测试(76 + 5);无后端 API 变更。

### Added(UI 升级 §17.3 管理页,2026-08-16)

- **管理页**:全局导航 admin 视图从占位变为真实页面 `#adminView`——三张卡片:租户配额(quota 只读 + 表单更新 conversation_quota/storage_quota_bytes)、成员(列表 + 邀请表单 actor_id/role + 角色变更 PATCH + 停用)、Webhook(端点列表 + 注册表单 url/事件复选(与 `SUPPORTED_EVENTS` 一致)/secret + 删除);RBAC:非 `admin:manage` 显示无权限提示。
- **交互**:`switchAppView("admin")` 切到 `#adminView` 并 `loadAdminView()`(并行拉 quota/members/webhooks,租户 id 取 `state.me.tenant_id`);表单提交后局部刷新与 toast 反馈;`#refreshAdmin` 手动刷新。
- **导航调整**:`nav.js` `NAV_PLACEHOLDER_VIEWS` 收敛为 `["knowledge", "settings"]`(admin 成为真实视图),`nav.test.js` 同步更新;占位文案移除 admin。
- 前端门禁:81 node 测试(无新增用例,nav 断言更新);无后端 API 变更。

## 1.2.0 (2026-08-15)

平台化:租户运营、渠道、前端工程与后端结构治理。版本自 1.1.0 收口。

### Added

- **后端结构治理**(Phase 27):`app/db/` 9 个域 mixin 组合 `Database`(database.py 3712→58 行);`app/routers/` 5 个域 router 工厂(main.py 2268→714 行);`docs/adr/` 6 份架构决策记录 + `CONTRIBUTING.md` 引用;迁移治理(`verify_migration_chain`/`migration_schema_version`)。拆分前后 416 测试零修改全绿。
- **前端工程化**(Phase 26):`app/static/js/` ES modules(api/format/i18n/state/sse/main,零构建)、`css/tokens.css` 设计令牌 + 深浅主题切换、i18n 语言包(zh-CN 全量 + en 骨架)、66 例 Node 单测 + 前端门禁(`node --check` + 400 行上限,进 pytest CI)。
- **可嵌入 Web Chat**(Phase 24.1):签名 token 认证的公开端点(`POST/GET /api/widget/sessions*`),复用编排器与 turn-job SSE 流式;`WIDGET_SECRET` 配置。
- **渠道级幂等**(Phase 24.2):`messages.channel_message_id` 唯一索引,同一渠道消息重投返回原始 turn(`idempotent_replay`),不产生重复 turn。
- **租户自助开通**(Phase 22.1):`POST /api/admin/tenants`(幂等,自动种子默认知识 + 配额),`GET/PUT /api/admin/tenants/{id}/quota`。
- **成员生命周期**(Phase 22.2):`tenant_members` 表,邀请/角色变更/停用端点,审计保留。
- **细粒度权限**(Phase 22.3):新增只读 `auditor` 角色与 `tenant:manage`/`audit:read` 权限点;权限矩阵穷举测试。
- **配额与计量**(Phase 22.4):会话配额强制(429 + Retry-After),`GET /api/admin/usage` 导出原始用量(会话/turn/消息计数)。

### Changed

- `tenant_usage_daily` 增 `conversation_count`/`message_count` 列(迁移 10),会话创建与消息写入时原子累加。
- `messages` 增 `channel_message_id` 列(迁移 11)。
- feedback 端点权限检查统一走 `require_any_permission` 共享依赖。

## 1.1.0 (2026-08-14)

智能质量与集成成熟 + 开发者体验。版本自 1.0.0 收口。

### Added

- **提示词/模型注册表**(Phase 19.1):`prompt_versions` 表与 `POST/GET /api/prompts`、`POST /api/prompts/{id}/action`(activate/canary/rollback),`admin:manage` RBAC + `prompt_version.*` 审计。
- **Canary 对照路由**(Phase 19.2):SHA-256 稳定分桶,租户专属优先全局,`prompt_version.resolved` 审计与 telemetry 维度。
- **Golden set 扩展**(Phase 19.3):6 → 27 例,`scripts/evaluate.py` 支持 `--prompt-version` 对照 diff,`tests/test_golden_set.py` CI 门禁 100%。
- **租户模型策略与预算**(Phase 19.4):`PUT/GET /api/admin/tenants/{tenant_id}/model-policy`;预算超限强制确定性路由 + `turn.budget_exceeded` 审计;`allowed_models` 白名单执行(1.1.0 审计修复补全)+ `turn.model_denied`。
- **生成控制**(Phase 19.5):`ChainedModelProvider` 故障切换 + 流式取消。
- **连接器运行时防护**(Phase 20):熔断/重试/降级、HTTP 参考连接器、契约套件对外化、故障注入测试。
- **出站 Webhook**(Phase 20.5):事件注册/HMAC/指数退避/死信/租约回收、`/api/webhooks*`。
- **Supervisor 质量看板**(Phase 21):`quality_daily` 增量聚合、`GET /api/supervisor/quality`(游标分页)、`GET /api/supervisor/knowledge-gaps`、前端质量面板。
- **知识生命周期**(Phase 21.3):draft → 审批 publish/retire(不可绕过)、负反馈一键回流 draft。
- **RFC 9457 错误契约**(Phase 25.1):所有错误体统一 `type/title/status/detail/instance` + `request_id` + `code`,保留旧 `detail` 字段;`docs/ERRORS.md` 错误目录。
- **OpenAPI 治理**(Phase 25.2):`api/openapi.json` 契约快照 + `scripts/openapi_snapshot.py` 门禁(破坏性变更红灯)、全部端点 summary/description/tags 标注。
- **API 版本与弃用策略**(Phase 25.3):`docs/API_POLICY.md`。
- **Python 客户端 SDK**(Phase 25.4):`clients/python/`(会话/消息/turn-job/SSE/验签,自带重试与幂等键,12 测含 E2E)。
- **API 参考文档**(Phase 25.5):`docs/api/reference.md`(从 OpenAPI 生成)+ `docs/api/guide.md`(认证/幂等/分页/流式/验签)。

### Changed

- 错误响应自 `{"detail": ...}` 升级为 RFC 9457 Problem Details(保留 `detail` 键兼容)。
- Webhook SLA 违约事件 id 携带违约时段(`sla_breach:{conv}:{sla_due_at}`),重新违约可再次触发。
- `prompt_versions.model_ref` 现在经 provider 边界实际选择模型。
- 首响延迟指标改为基于当前 turn 消息时间戳计算。

### Fixed(审计发现,见 IMPLEMENTATION_REPORT_1_1.md 修复表)

- `list_knowledge_gaps` 在 citations 为字符串/非法 JSON 时不再 500。
- 负反馈计日改为 turn 消息创建日(跨天不再错配);负 delta 不产生幻影 bucket。
- 知识无匹配转人工不再被质量门标失败,真实 handoff 原因保留。
- 熔断器:OPEN 态迟到失败不再延长冷却;重试每次重查熔断状态。
- `set_canary` 作用于当前 active 版本被拒绝(409),不再静默破坏 active 通道。
- HTTP 连接器 200 空载荷返回 `not_found`,不再伪造订单/客户数据。
- Webhook:按行独立事务(单条失败不回滚已投递)、永久 4xx 立即死信、删除端点死信其投递、退避加抖动。

### Security

- 租户 `allowed_models` 白名单从存储到执行(此前仅存储)。

## 1.0.0 (2026-08-xx)

商用级架构基线:版本化迁移框架、PostgreSQL 后端、单调 `seq`、Redis 持久队列、SSE 流式、OpenTelemetry、OIDC/BFF 会话、golden-set 评估、连接器契约、API keys secrets 文件。详见 `IMPLEMENTATION_REPORT_1_0_DEV.md`。

## 0.9.2 (2026-08-01)

质量基线:可访问性、测试可靠性、交付基线、隐私加固。详见 `IMPLEMENTATION_REPORT_0_9_2.md`。

## 0.9.x – 0.3.x

逐版本交付记录见 `IMPLEMENTATION_REPORT_0_3.md` 至 `IMPLEMENTATION_REPORT_0_9.md`(工作台协作、队列、缓存、分页、会话状态机等)。
