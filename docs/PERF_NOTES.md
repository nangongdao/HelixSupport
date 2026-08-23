# Performance Notes (ROADMAP §18)

本文件记录 §18 性能优化专项的基准数字(before/after)与热路径审计结论。
原则:先测量后优化;所有数字注明测量方法、环境与窗口,禁止无凭据的"优化"。

## 基线环境

- 硬件:本地开发机(Windows 11, x86-64),SQLite 文件库(非 PostgreSQL)。
- 测量方法:`app/telemetry` 的 `TelemetryMetrics` 直方图(P50/P95 由
  `statistics.quantiles(n=100, inclusive)` 计算),通过 worker 的 `run_once`
  跑 20 个确定性知识 turn(内容 "配送一般多久能到？",知识库 1 篇文章,
  `stream_pacing_ms=10` 默认值)。
- 测量日期:2026-08-16。单机单次窗口,不作为跨设备 SLO 承诺;18.3 压测脚本
  将把同类基准纳入回归。

## 18.2a — turn 延迟分段剖析(P50/P95 基线)

`handle_customer_message` 记录五段直方图 `turn.segment_ms{segment=...}`:

| segment  | avg   | p50   | p95   | max   |
|----------|-------|-------|-------|-------|
| intake   | <1 ms | ~     | 15 ms | 15 ms |
| policy   | <1 ms | ~     | <1 ms | 15 ms |
| triage   | <1 ms | ~     | <1 ms | 15 ms |
| specialist| <1 ms | ~     | <1 ms | <1 ms |
| persist  | 672 ms| 671 ms| 720 ms| 735 ms|

**结论(真实瓶颈)**:persist 段占整段 turn 处理的 ~98%,几乎全部来自
`_record_quality_turn` 的质量聚合写入(发生在 assistant 消息落库、且 18.2c
stream 首 chunk 已写出**之后**)。它不影响 TTFT,但把 `handle_customer_message`
返回推迟到 ~672 ms。→ 优化方向见 **18.2d 热路径 SQL 审计**。

## 18.2c — 流式 TTFT(generate-as-you-write)

- **改造前**:worker 在 `handle_customer_message` 完整返回(含 persist 段的
  质量聚合)后才调用 `_write_stream_chunks` 分 token 写 chunk;SSE 端点 200 ms
  轮询读取。TTFT 被 persist 段(≈672 ms)直接拖累。
- **改造后**:`handle_customer_message` 接受可选 `chunk_sink`
  (grant-as-you-write)——assistant 消息落库后立即把回复按 token 写入 chunk,
  首 chunk 无 pacing 即时落库;worker 后置 fallback 路径对已流式 job 跳过。
  SSE 轮询收敛到 100 ms(`TURN_JOB_SSE_POLL_INTERVAL_MS`)。确定性路径保留
  每 token 10 ms pacing(`TURN_JOB_STREAM_PACING_MS`)用于 UI 平滑;真实模型
  流路径将逐 token 直写、无 pacing。
- **TTFT 基准**(`turn.ttft_ms`,dequeue → 首 chunk 落库):

  | n    | avg   | p50   | p95   | max   |
  |------|-------|-------|-------|-------|
  | 20   | 21.9  | 15.5  | **46.1** | 47  |

  **P95 = 46 ms < 500 ms 目标(本地模型路径验收门),远超达标。**
  TTFT 不再受 persist 段质量聚合影响——这是 18.2c "生成即写"消除的最大延迟项。

相关测试:`tests/test_ttft_streaming.py`(chunk 恰写一次、sink 标记后 fallback
跳过、TTFT 直方图记录)与 `tests/test_streaming.py`(SSE token/completed 次序、
幂等重放不重复)、`tests/test_generation_control.py`(cancel 契约)。

## 18.2d — 热路径 SQL 审计

审计目标:队列首屏、会话详情、消息分页三个高频读查询(以及 persist 段的
质量聚合写),SQLite 实测 `EXPLAIN QUERY PLAN`。**双端**:DDL 由
`PostgresConnection` 适配器在 PG 上以同一语法执行;PG 的 `EXPLAIN ANALYZE`
实测随 18.3 压测补充。

### 队列首屏(`list_conversations`,默认 sort=priority)

```
FROM conversations c WHERE c.tenant_id = ?
ORDER BY CASE c.priority WHEN 'high' THEN 0 ELSE 1 END, c.updated_at DESC, c.id DESC
LIMIT ? OFFSET ?

SEARCH c USING INDEX idx_conversations_tenant_updated (tenant_id=?)
USE TEMP B-TREE FOR ORDER BY
```

**结论**:无全表扫描;在租户子集上做临时排序。`CASE` 表达式无法匹配任何
(tenant, priority, updated_at, id) 索引前缀,故 priority 排序必然 temp
B-tree。**18.5 全量实测**(100k 会话/百万消息,SQLite):offset 首屏
P95=241ms、深页 P95=256ms、keyset 单页约 156ms,均 < 300ms 门——SQLite
判定达标。

**PG 复测(1.3,2026-08-17)** 修正了此前的"暂不引入候选索引"结论。PG 上
planner 对 `ORDER BY CASE ...` 不匹配任何普通索引,每队列页全租户并行 SEQ
扫 + top-N sort,offset 首屏/深页 P95 实测 **348.2/329.7ms**(贴线)。新增
表达式索引兜底:

```
idx_conversations_priority_page(tenant_id,
  (CASE priority WHEN 'high' THEN 0 ELSE 1 END), updated_at DESC, id DESC)
```

关键在**末列方向**。PostgreSQL 按列逐个匹配索引方向;初版误写成 `id`
(默认 ASC)而 ORDER BY 是 `id DESC`,末列不一致使**整个索引**被判定不可用
作排序源,planner 仍走 Gather Merge(2 worker)+ top-N heapsort,EXPLAIN
ANALYZE 实测 404–435ms、read+hit 14847 buffers。补 `id DESC` 后索引命中
(Index Scan,0.3–0.4ms),offset 任意页 P95 **2.5–5.2ms**、keyset 单页
约 3–5ms——60x 余量。SQLite 对 DESC 索引同样支持该 DDL(初始化冒烟无回归,
SQLite full 数字不受影响)。完整 PG 表格见 `docs/CAPACITY.md` §3.2b。

**处置**:深翻页继续推荐 keyset cursor(已支持);offset 分页在 PG 上已由
该表达式索引兜底,首屏与深页同成本。

### 18.2e — PG 搜索全匹配收敛(确定性窗口快路径,2026-08-18)

§18.5 已知上限(**全匹配最坏 627–809ms 超线**)的根因是 planner 统计盲区:
psycopg2 参数化 `content LIKE %s` 使 `patternsel` 用 DEFAULT_MATCH_SEL(0.5%)
估算,全匹配词(命中全部百万消息)被估为仅 5 万行 → planner 选全租户并行
SEQ 扫 + top-N sort;而字面量 `'%message%'` 时 pg_trgm 统计可估 ~100% 走有序
早退。同字段绑参,常量统计不可见。此外共享队列 `ORDER BY updated_at DESC,
id DESC` 缺 `id` 末列,`idx_conversations_tenant_updated` 不可用作有序索引。

**收敛方案(不依赖 planner 估计)**:

1. **确定性窗口快路径**(`app/db/conversations_query.py`
   `_query_conversations_windowed`):对 `sort="updated"` 的搜索,先通过新索引
   `idx_conversations_tenant_updated_id (tenant_id, updated_at DESC, id DESC)`
   强制有序索引扫描取最新 `offset+limit` 个会话(含 keyset/过滤子句),再对
   窗口逐会话探测搜索谓词(`content LIKE` 经 pg_trgm GIN 位图)。页面若填满
   `limit` 行即返回——窗口外任何匹配按序都在窗口之下,故该页即全局该页
   (completeness rule);不足则回退聚合 CTE。
2. **窗口硬界** `_PG_SEARCH_WINDOW = 1024`:深 offset(`offset+limit > 1024`)
   直接走 CTE(窗口无论如何盖不住整页,无谓)。
3. **新索引 `idx_conversations_tenant_updated_id`**(`app/pg_compat.py`
   `install_updated_sort_index`,随 `PostgresDatabase.initialize()` 幂等安装):
   同时修复普通 `updated` 排序分页——此前缺 `id DESC` tiebreak,planner 拒用
   两列索引退回并行全扫 + top-N sort(~255ms warm)。

**实测(`scripts/pagination_load_test.py` 数据库适配层 n=20,scratch PG
5433/bench_final2 百万消息,2026-08-18,三轮;与 §3.2b 口径一致)**:

| benchmark | before(P95) | after avg/p50/p95/max |
|-----------|-------------|----------------------|
| queue.search.fts.worst(全匹配 `message`) | ~735ms(§3.2b 627–809) | 5.1–5.6 / 5.1–5.3 / **6.5–7.3** / 6.6–8.2 |
| queue.search.fts.selective(`selectiveneedle`,仅消息正文) | 旧探针无效 | 123.9–137.2 / 100.6–102.6 / **143.2–168.3** / 422.3–658.5 |
| updated 排序 offset 首屏 | ~255ms(并行全扫 + top-N) | 3.2–4.4 / 2.0–3.1 / **3.8–6.1** / 20.5–24.4 |

全匹配 P95 从 627–809ms 收敛到 6.5–7.3ms;修正后的消息专属选择性词为
143.2–168.3ms,均远低于 500ms 验收线;普通 updated 排序分页同时降到
3.8–6.1ms。旧 selective 词 `000007` 实际命中会话 ID、没有进入消息路径,
现由基准前置断言防回退。正确性由 `tests/test_search_window.py` 固定密集页、稀疏
回退、游标与租户边界,live PG 索引/路径测试见 `tests/test_postgres.py`。

### 会话详情(`get_conversation`)

```
SEARCH conversations USING INDEX sqlite_autoindex_conversations_1 (id=?)
```

**结论**:主键直取,最优,无改动。

### 消息分页(`list_messages` keyset,首屏与续页)

```
SEARCH messages USING INDEX idx_messages_page (tenant_id=? AND conversation_id=?)
USE TEMP B-TREE FOR RIGHT PART OF ORDER BY   ← 待消除(见下)
```

**结论**:`idx_messages_page = (tenant_id, conversation_id, created_at, id)`
末列是 UUID `id`,而分页按 `seq` 排序(seq=rowid/sequence,单调),导致
"RIGHT PART OF ORDER BY" 临时排序。**修复(M23)**:新增
`idx_messages_page_seq (tenant_id, conversation_id, created_at ASC, seq ASC)`;
修复后两页查询计划均为纯索引 SEARCH,无 temp sort。该索引同时服务前向和后向
keyset 分页,PG 同一 DDL。

### persist 段质量聚合(§18.2a 定位的 672 ms)

root cause:**chunk 逐条写入的 WAL 提交开销**(Windows 本地 SQLite,单次提交
约 20–30 ms 受 fsync 影响;每 turn 10–40 条 chunk)。TTFT(46 ms)不受影响——
首 chunk 在 persist 段开头即提交;延迟全部落在"流式完成"与 job 收口上。
生产 PG(fillfactor/checkpoint 参数见 18.3)将消除此 fsync 热点;本地可将
`TURN_JOB_STREAM_PACING_MS` 降为 0 只在需要打字机效果时启用。

相关测试:`tests/test_pagination_seq_index.py`(stub)与 `tests/test_migrations.py`(M23 幂等)。

## 18.4 — 前端渲染预算与资源(2026-08-17)

- **requestIdleCallback 渲染预算**:`app/static/app.js` 新增 `scheduleIdle(fn)`
  助手(有 `requestIdleCallback` 时用 idle 时隙 + 2s 兜底超时,否则 250ms
  setTimeout)。会话详情打开时的非关键后台工作(附件名回填、工单徽标详情、
  质量看板数据、首次提及徽标)改为 idle 调度——交互路径上的重活让出给
  首帧与点击响应。浏览器冒烟(ui_smoke / ui_thread_lazy / ui_virtual_queue /
  ui_sserelay)全绿。
- **modulepreload**:`index.html` 为模块入口 `main.js` 及其 10 个静态导入
  增加 `<link rel="modulepreload">`,模块图与样式表并行取回;预加载 URL 与
  ES module import specifier 共用当前 `v=1.3.7`,避免同一模块因 URL 不同被
  重复取回。
- **强缓存契约**:`app/assets.py` 单一维护资源版本与缓存策略。仅当 `/static/*`
  请求携带当前 `v=1.3.7` 时返回
  `Cache-Control: public, max-age=31536000, immutable`;未版本化或过期版本仍
  返回 `no-cache`,HTML shell 也保持 `no-cache` 以发现新资源清单。
  `scripts/frontend_gate.py` 扫描 HTML/CSS/JS 内的绝对资源与相对 module import,
  版本缺失或不一致即失败;后端契约测试同时固定三种缓存响应。
- **关键 CSS 内联:不做(设计约束)**。应用 CSP(`app/main.py`
  `style-src 'self'`,Phase 28.4)禁止内联 `<style>`,内联骨架会在控制台报
  CSP 违规并破坏冒烟门禁;因此该子项以"文档化不实施"收口,样式首屏依赖
  单张 `styles.css`(23KB,本地无跨域成本)。
