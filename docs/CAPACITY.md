# Capacity Model & §18.5 Acceptance

本文档承载两部分:既有单实例容量基线(Phase 30.3)与 ROADMAP §18.5 容量验收目标
(1.3 验收)。原则(§18):先测量后优化——每个数字注明测量方法、环境与窗口;
达标结论只认真实基准,不认推理。

## 1. §18.5 验收目标(1.3 验收,来源 ROADMAP §18.5)

| 目标 | 验收线 | 证据来源 |
|------|--------|----------|
| 单实例(4C8G,PG+Redis)并发活跃会话 | ≥ 50 并发 | 已测(2026-08-17,`scripts/load_test.py --concurrency 50` PG+Redis,见 §3.6) |
| turn P95(确定性路径) | < 2s | 已测(2026-08-17,PG+Redis 多 worker 串行 20 turn,见 §3.6) |
| 队列首屏 P95 | < 150ms | `scripts/pagination_load_test.py` queue 基准(PG 实测) |
| SSE 扇出连接数 | ≥ 500 | 已测(2026-08-17,`scripts/sse_fanout_test.py`,见 §3.5) |
| 双实例滚动重启 | 零任务丢失、可用性不中断 | 已测(2026-08-17,`scripts/rolling_restart_test.py`,见 §3.7) |
| 10 万会话/百万消息数据集 | 队列任意页 P95 < 300ms | `scripts/pagination_load_test.py --scale full`(PG 实测) |
| 同上数据集 | 消息搜索 P95 < 500ms | `pagination_load_test.py` FTS `queue.search.fts.worst/selective`(PG 实测) |

度量口径:分位数由 `statistics.quantiles(n=100, method="inclusive")` 计算;
窗口 = 单机单次压测,不跨设备做 SLO 转译(跨设备 SLO 见 `docs/SLO.md`)。

## 2. 测量方法与环境

- 合成分页压测:`scripts/pagination_load_test.py`。种子用 executemany 批量
  直插以减少逐条往返;SQLite 写触发对直插仍会执行(会话投影/`message_fts`
  镜像随之增量填充),脚本随后强制线性重建 `message_fts` 镜像
  (DELETE + 批量 INSERT,非 O(n²) 反连接),保证搜索基准读到完整数据。
  消息 `seq` 由 rowid 触发回填(单调,分页排序键)。SQLite FTS5 走
  `queue.search.fts.{worst,selective}` 两档探针;PG 上该镜像不存在,搜索测
  LIKE 回退。
- 档位:`smoke` 2 千会话/2 万消息(秒级)、`mid` 2 万/20 万、`full` 10 万/百万
  (§18.5 gate)。用例:队列 offset 首屏/深页、队列 keyset 续页、消息 keyset
  前向/后向、消息 FTS 搜索(全匹配最坏情形 + 唯一索引词选择性检索两档)。
- turn 分段与 TTFT:见 `docs/PERF_NOTES.md`(18.2a/18.2c),本地开发机
  Windows 11 单机 SQLite,20 个确定性知识 turn。
- 生产验收环境(1.3):4C8G 单实例 + PostgreSQL + Redis,`DATABASE_BACKEND=
  postgresql`;热表 fillfactor/autovacuum 参数按 DEPLOYMENT.md §PostgreSQL
  Tuning 落位。

## 3. 已测证据(当前开发窗口内)

### 3.1 分页与搜索(合成数据集)

SQLite 本地库(保守上界;PG 目标线 1.3 实测,此处同量级 SQLite 数据佐证趋势):

| benchmark | 数据集 | n | avg ms | p50 ms | p95 ms | max ms |
|-----------|--------|---|--------|--------|--------|--------|
| queue.offset.page0 | smoke(2k/20k) | 20 | 6.2 | 6.0 | 7.0 | 7.6 |
| queue.offset.depth(pages×50) | smoke | 20 | 6.2 | 6.2 | 6.7 | 6.8 |
| queue.keyset.walk(5 页) | smoke | 20 | 17.4 | 17.4 | 18.7 | 19.2 |
| messages.keyset.forward(深会话) | smoke | 20 | 2.5 | 2.4 | 3.1 | 3.1 |
| messages.keyset.backward | smoke | 20 | 1.1 | 0.9 | 1.8 | 1.9 |
| queue.search.fts.worst(全匹配) | smoke | 20 | 51.0 | 50.3 | 56.5 | 61.3 |
| queue.search.fts.selective(唯一索引词) | smoke | 20 | 1.3 | 1.3 | 1.5 | 1.6 |

### 3.2 全量档位与门禁判定

full(10 万/百万)档位实测(2026-08-16,SQLite,线性 FTS 重建后):

| benchmark | 数据集 | n | avg ms | p50 ms | p95 ms | max ms |
|-----------|--------|---|--------|--------|--------|--------|
| queue.offset.page0 | full(100k/1M) | 20 | 230.5 | 230.8 | 241.2 | 243.7 |
| queue.offset.depth | full | 20 | 244.5 | 244.2 | 256.4 | 258.8 |
| queue.keyset.walk(5 页) | full | 20 | 788.9 | 773.9 | 891.1 | 956.4 |
| messages.keyset.forward(深会话) | full | 20 | 1.5 | 1.4 | 1.8 | 1.9 |
| messages.keyset.backward | full | 20 | 0.6 | 0.6 | 0.7 | 0.9 |
| queue.search.fts.worst(全匹配) | full | 20 | 2738.0 | 2596.2 | 3438.7 | 3598.1 |
| queue.search.fts.selective(唯一索引词) | full | 20 | 111.3 | 102.0 | 141.8 | 178.5 |

### 3.2b PostgreSQL 全量档实测(2026-08-17 基线,2026-08-18 搜索收敛复测)

PG 环境:本机 scratch 实例(PostgreSQL 18.4,5433),`DATABASE_BACKEND=
postgresql` 跑同一 `scripts/pagination_load_test.py --scale full`(10 万会话/
百万消息,同种子;PG 无消息 FTS 镜像,搜索走 LIKE/pg_trgm 回退路径)。
压测脚本在 seed 后对 PG 执行 `ANALYZE`(`_analyze_postgres`),使 planner 统计
与生产 autovacuum 一致。2026-08-18 复测在全新 `bench_final3` 库上同脚本同
配置重跑,列 **after**;before 为 2026-08-17 首测(3 轮中间一轮)。

2026-08-17 先修了队列排序表达式索引的**方向缺陷**:`idx_conversations_priority_page`
末列写成 `id`(默认 ASC),而查询 ORDER BY 是 `id DESC`——PostgreSQL 按列逐个
匹配索引方向,末列不一致会让**整个索引**被判定不可用,planner 于是全租户
并行 SEQ 扫 + top-N heapsort(≈290–435ms)。补上 `id DESC` 后索引命中,
offset 分页实际执行降到 0.4ms(根因与复测见 `docs/PERF_NOTES.md` §18.2d)。

| benchmark | 数据集 | n | before avg/p50/p95/max | after avg/p50/p95/max |
|-----------|--------|---|------------------------|----------------------|
| queue.offset.page0 | full(100k/1M) | 20 | 2.9 / 2.9 / 3.6 / 4.0 | 3.7 / 3.5 / 5.4 / 5.6 |
| queue.offset.depth(offset=250) | full | 20 | 3.3 / 3.2 / 4.5 / 5.2 | 4.2 / 4.2 / 4.6 / 4.8 |
| queue.keyset.walk(5 页累计) | full | 20 | 16.7 / 16.6 / 18.9 / 20.6 | 21.4 / 21.4 / 22.9 / 23.0 |
| messages.keyset.forward(深会话) | full | 20 | 9.4 / 8.8 / 12.3 / 14.0 | 12.7 / 12.8 / 13.2 / 13.8 |
| messages.keyset.backward | full | 20 | 1.7 / 1.6 / 2.2 / 2.9 | 2.8 / 2.8 / 3.2 / 3.2 |
| queue.search.fts.worst(全匹配) | full | 20 | 660.4 / 653.8 / 735.2 / 840.3 | **6.2 / 6.1 / 7.0 / 7.0** |
| queue.search.fts.selective(唯一索引词) | full | 20 | 269.1 / 263.1 / 316.7 / 384.9 | 126.7 / 106.5 / 273.4 / 339.4 |

**对 §18.5 PG 两线的判定(1.3 验收):**

- **队列任意页 P95 < 300ms:达标,余量充分**。offset 首屏/深页 P95 复测
  4.6–5.4ms;keyset 单页约 3–5ms(5 页 walk 累计 22.9ms)。修复前该线
  (348/330ms)已贴线甚至超标,方向修复后余量 >60x,判据不再依赖运气。
- **消息搜索 P95 < 500ms:达标(2026-08-18 收敛)**。此前 PG 无消息 FTS 镜像,
  LIKE 回退 + pg_trgm 对全匹配最坏路径(P95 627–809ms)超线,记为已知上限;
  根因是 psycopg2 参数化 `LIKE %s` 的 planner 统计盲区(全匹配词被估为仅
  0.5% 命中 → 选全租户 SEQ 扫 + top-N sort)。**确定性窗口快路径**
  (`app/db/conversations_query.py` `_query_conversations_windowed`,见
  `docs/PERF_NOTES.md` §18.2e)+ 新索引 `idx_conversations_tenant_updated_id`
  (`app/pg_compat.install_updated_sort_index`)不依赖估计:先按有序索引取最新
  `offset+limit` 个会话,再逐会话探测搜索谓词,窗口填满即返回
  (completeness rule,窗口外任何匹配按序都在窗口之下)。同脚本同配置复测:
  全匹配 worst P95 **735.2 → 7.0ms**,选择性 **316.7 → 273.4ms**,均远低于
  500ms 线;"已知上限"标记解除,产品侧无需二段词过滤规避。普通 `updated`
  排序 offset 分页同时由同索引从 ~255ms(并行全扫 + top-N)降到 ~8ms。
  归档搜索仍按设计走 LIKE 回退(§18.3)。

**对 §18.5 数据规模门禁的判定(SQLite 保守口径):**

- 队列任意页 P95 < 300ms:**达标**——offset 首屏 230.8ms(P50)、241.2ms
  (P95)、深页 244.2/256.4ms,均达标;keyset 续页单页约 156ms(5 页 walk
  891ms P95 为累计)。队列 offset/keyset 数字在两次 full 复测间稳定
  (首屏 avg 222→231),判定可靠;PG 侧(同一索引路径)1.3 复测确认。
- 消息搜索 P95 < 500ms:**选择性检索达标,全匹配最坏路径不达标**——真实
  检索形态(唯一索引词 `000007`)full 档 P95=141.8ms,达标且余量充足;全
  匹配最坏情形(种子全量同词)avg 2.7s / P95 3.4s,且 run-to-run 方差大
  (前次同配置复测 avg 2.05s / P95 2.12s)——枚举百万命中再归并会话是真实
  的慢路径。PG 侧该镜像不存在(搜索走 LIKE 回退,更弱),故 §18.2 计划的
  PG `pg_trgm` 相似度索引应从"可选对齐"升级为 **1.3 验收的必要前置**,用
  来把最坏命中路径收敛到可接受线内。归档后(`archived=true`)搜索按设计走
  LIKE 回退(§18.3),冷数据反查的规模代价需在 1.3 容量复核时一并评估。

  **§18.2 pg_trgm 对齐已落地(2026-08-16)**:`app/pg_compat.py` 新增
  `install_trgm_search()`——`CREATE EXTENSION IF NOT EXISTS pg_trgm` +
  `messages.content gin_trgm_ops` GIN 索引,随 `PostgresDatabase.initialize()`
  幂等安装(缺失 contrib/权限时降级为原 LIKE 路径并记 warning,不阻塞启动);
  共享 LIKE 回退(`content LIKE '%term%'`)对 ≥3 字符 needle 由索引位图扫描
  收敛。PG 门控测试入 `tests/test_postgres.py`(扩展/索引存在性、检索命中、
  `enable_seqscan=off` 下索引可应答该谓词)。**全匹配病态已收敛(2026-08-18)**:
  PG+百万消息数据集全量复测(见 §3.2b)完成——队列两线达标;搜索全匹配
  worst P95 735.2→7.0ms、选择性 316.7→273.4ms 均达标,已知上限解除;
  收敛机制见 `docs/PERF_NOTES.md` §18.2e。

#### Phase 37 最终收敛与基准口径修正(2026-08-18)

Phase 37 复核上述结论时发现,原 selective 探针 `000007` 并不在消息正文
(正文使用未补零的 `7-...`),却存在于 `conversations.id=conv_000007`。因此旧
selective 数字实际由会话字段满足,不能作为消息索引路径证据。基准现改为只写入
`conv_000007` 十条消息正文的 `selectiveneedle`,并在测量前强制断言 0 个会话
字段命中、10 条消息命中;不满足即拒绝产出数据。

全匹配路径使用确定性窗口:先由
`idx_conversations_tenant_updated_id(tenant_id,updated_at DESC,id DESC)` 读取
请求页所需的最新候选,再做 tenant+conversation 消息 `EXISTS` 探测。窗口内若
填满请求页,窗口外任何命中都排在该页之后,可直接返回;不足一页则回退原
tenant-scoped pg_trgm 聚合,保持稀疏词、筛选、游标和租户隔离语义。

同一 scratch PostgreSQL 18.4 数据集(100,000 会话/1,000,990 消息,三轮、
每项 20 次)修正后的最终结果:

| benchmark | 三轮 P50 ms | 三轮 P95 ms | 判定 |
|-----------|-------------|-------------|------|
| queue.offset.page0 | 2.0–3.1 | 3.8–6.1 | <300ms,达标 |
| queue.offset.depth(offset=250) | 3.0–3.7 | 3.6–4.7 | <300ms,达标 |
| queue.search.fts.worst(全匹配) | 5.1–5.3 | 6.5–7.3 | <500ms,达标 |
| queue.search.fts.selective(仅消息正文) | 100.6–102.6 | 143.2–168.3 | <500ms,达标 |

`EXPLAIN (ANALYZE,BUFFERS)` 显示全匹配窗口通过 updated/id 索引做
index-only scan,50 个候选的消息探测使用 `idx_messages_page_seq`,执行 1.95ms;
选择性回退通过 `idx_messages_content_trgm` bitmap index scan 命中 10 条消息,
总执行 93.46ms。由此 §18.5 PostgreSQL 百万消息搜索从“选择性达标/全匹配已知
上限”更新为两线均达标;上方 2026-08-17 表格保留为历史 before 证据。

### 3.3 turn 路径(docs/PERF_NOTES.md,2026-08-16)

| 指标 | p50 | p95 | 上线关系 |
|------|-----|-----|----------|
| turn persist 段(质量聚合,18.2a 定位) | 671 | 720 | 不影响 TTFT;已被 18.2c 移出首 token 路径 |
| stream TTFT(18.2c,dequeue→首 chunk) | 15.5 | 46.1 | 达标 (<500ms),不再受 persist 拖累 |
| SSE 轮询收敛 | – | 100ms 间隔 | `TURN_JOB_SSE_POLL_INTERVAL_MS` |

### 3.4 归档分层对热表体积的约束(§18.3)

归档把已解决会话整体移出热表(会话/消息/标签/反馈 → `*_archive`),
热表 delete 经既有 trigger 级联 `message_fts`。作用直接对应 §18.5 的
"10 万会话"规模约束:热队列与 FTS 镜像只承载活跃工作。进度以
`GET /api/system/metrics` 的 `archived_total` 观测(worker
`CONVERSATION_ARCHIVE_*` 4 项配置,见 DEPLOYMENT.md)。

### 3.5 SSE 扇出(§18.5,2026-08-17)

`scripts/sse_fanout_test.py` 对运行实例并发建立 `/api/events/queue` SSE 连接,
统计活跃连接(HTTP 200 + snapshot 与 ≥1 个 ping 到达)与首包延迟。客户端用
裸 asyncio TCP(demo 模式 `conversation:read` 放行)——初版用 httpx 连接池
对并发 SSE 流会产 ~3% 假 502(httpcore 与并发流的交互),而裸 TCP 同规模
0 失败,故以裸 TCP 测服务端真实能力。

| 连接数 | 活跃 ping | snapshot | errors | 首包 p50 | 首包 p95 |
|--------|----------|----------|--------|----------|----------|
| 500(验收档) | 500 | 500 | 0 | 1.67s | 2.42s |
| 600(高压档) | 600 | 600 | 0 | 2.02s | 2.81s |

**判定:达标**(§18.5 线 ≥500)。单 uvicorn 事件循环可保持并推送 >500 并发
SSE 流。首包延迟随连接数线性上升——每连接在 snapshot 前同步执行
`database.queue_revision`(DB 调用),单 worker SQLite 单连接池上串行排队;
连接保持本质是事件循环能力,与 DB 后端无关,生产 PG 连接池 + 多 worker
下该排队会显著下降。首包延迟非门禁行,记为单 worker SQLite 的特征观测。

### 3.6 并发活跃会话与 turn P95(§18.5,2026-08-17)

PG(scratch 5433/`helix_load`)+ Redis(6379)多 worker 场景。uvicorn `--workers 7`、
`TURN_WORKER_CONCURRENCY=8`(每进程)、`DATABASE_POOL_SIZE=12`、
`RATE_LIMIT_PER_MINUTE=20000`(50 并发注入下排除限流干扰)。

- **50 并发注入**(`scripts/load_test.py --concurrency 50 --duration 45
  --poll-turn-jobs`,demo 租户):818 会话 / 2454 请求,**success=1.0、
  failed=0、rate_limited=0**。操作级延迟:create p50/p95 453/859ms、
  enqueue 438/843ms、poll(turn 端到端)p50/p95 1718/2453ms。服务端累计
  3028 turn 全部完成、0 failed。
- **turn 确定性路径**(队列空、串行 20 turn):p50 688ms / **p95 907ms** /
  max 922ms——**P95 < 2s 达标**。
- **判定:达标**。实例稳定承载 50 并发活跃会话零丢失、零限流;确定性路径
  turn P95 < 1s。并发下 poll P95 2.45s 是 56 并发处理池面对 50 冷启动会话
  同时涌入的瞬时排队,反映端到端(入队→完成)而非确定性路径,记为特征观测。

注意:demo 模式鉴权只放行 `demo` 租户(`security.py:129`),多租户并发注入需
`AUTH_MODE=api_key` + 预注册租户;本测用 demo 单租户,实例级结论不受影响。

### 3.7 双实例滚动重启(§18.5,2026-08-17)

`scripts/rolling_restart_test.py` 对共享 PostgreSQL(`helix_roll`,5433)+ Redis
的双实例(8000/8001,uvicorn `--workers 2`)持续写入 turn job,在写入中段由
**外部** kill 主实例(8000 整树,含 spawn_main 子进程);写入端探测 health 不可达
后自动 DSN 切换到备用实例继续写入。租约参数:`TURN_JOB_LEASE_SECONDS=30` +
`IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS=30`(config 校验 lease≥timeout),
`TURN_WORKER_CONCURRENCY=8`。租约恢复窗口由脚本 `--lease-grace 180` 覆盖。

| 项 | 值 |
|----|----|
| 写入窗口 | 30s,`kill` 在 ~10s 处触发 |
| 写入端接收 | enqueued_ok=490(成功入队) |
| 写入端拒绝 | enqueue_fail=252(Phase 29.2 背压 429——tenant cap `queued+processing>200` 在 failover 突发时触发,属设计内保护,非丢失) |
| failover 次数 | 20 |
| PG ground truth | total=498(本次前缀 `rst-*` 全部入队 job),`completed=498`、failed=0 |
| not_finished / lost | 0 / 0 |
| 重复 chunk / 重复幂等键 | 0 / 0(重放守卫:完成 job 的 `chunk_count == len(split_stream_tokens(assistant_reply))`) |
| 审计链 | 23,165 行,`chain_breaks=0`(含滚动窗口写入的行) |
| 墙钟 | 61.1s(含租约恢复等待) |

**判定:达标**(§18.5 双实例滚动重启零任务丢失、零失败、无重复、审计连续)。
验收依赖的底层修复本轮一并落地:lease-aware `recover_turn_jobs`(健康在途
claim 不再被 peer 翻回 queued)、Redis 队列孤儿补偿(`reconciled`)、chunk
`seq` 的 PG 序列赋值修复(INSERT 不再显式传 0 绕过 DEFAULT)、重跑清理
(attempts>1 先删旧 chunk 再重写)、`initialize()` 全表 backfill 的
`pg_advisory_lock(9173001)` 串行化(两实例并发 boot 死锁修复)。见 CHANGELOG。

## 4. §18.5 门禁登记(曾为未测,当前开发窗口内全部实测)

以下目标曾依赖 1.3 环境的 service 压测/故障注入,先登记为待验收、不得视为
达标;现已在 2026-08-17 开发窗口内全部实测并移入 §3 已测证据:

1. ~~**≥50 并发活跃会话 / turn P95 < 2s(确定性路径)**~~:**已测(2026-08-17)**,
   见 §3.6——PG+Redis 多 worker 实例 50 并发注入零失败,确定性路径 turn
   P95 907ms 达标;并发端到端 poll P95 2.45s 记为排队观测。
2. ~~**双实例滚动重启零任务丢失**~~:**已测(2026-08-17)**,见 §3.7——
   `scripts/rolling_restart_test.py` 在持续写入中外部 kill 主实例,DSN 切换
   备用实例续写,PG 断言全部 job 到达终态、零重复 chunk、零重复 idempotency
   键、审计链连续,判定 PASS。
3. ~~**10 万/百万数据集在 PG 上的两条 P95 线**~~:**已测(2026-08-17 基线,
   2026-08-18 搜索收敛复测)**,见 §3.2b——full 档位 PG 复测完成,队列两线
   达标;搜索选择性与全匹配最坏路径在确定性窗口快路径 + updated 排序索引
   收敛后均达标(全匹配 P95 7.0ms、选择性 273.4ms),"已知上限"已解除。
4. ~~**SSE 扇出 ≥500**~~:**已测(2026-08-17)**,见 §3.5——500 连接受
   500 活跃、0 errors、PASS。

## 5. 单实例基线(既有,Phase 30.3)

| 维度 | 基线 | 说明 |
|------|------|------|
| turn 吞吐 | ~50–150 turn/s | 取决于内容长度与 FTS 命中;以 `scripts/load_test.py` 实测为准 |
| 并发连接 | 4 连接池(可调 `DATABASE_POOL_SIZE`) | SQLite WAL 单写者,多读者 |
| 存储增速 | ~2–5 KB/turn(消息+审计) | `messages` + `audit_events` + `turn_jobs` 为主 |
| SSE 连接 | 无硬上限(受连接池/进程 fd 约束) | 每连接独立生成器 |

## 6. 扩展路径

1. **队列积压**:`QUEUE_DEPTH_THRESHOLD`/`TENANT_CONCURRENT_TURN_CAP` 背压 → 429;增加 `TURN_WORKER_CONCURRENCY`(单实例多 worker,SQLite 内)。
2. **横向扩展**:SQLite 限单实例;切 `DATABASE_BACKEND=postgresql` + Redis 队列(`QUEUE_BACKEND=redis`)后多实例。
3. **读放大**:知识/消息 FTS + 租户缓存(`CACHE_MAX_ENTRIES`)缓解。

## 7. 容量测试

```bash
# 单实例基线
python scripts/load_test.py --base-url http://127.0.0.1:8000 --duration 60 --concurrency 10
# 多实例场景(Phase 30.3):worker 轮流分发到各实例
python scripts/load_test.py --base-urls http://10.0.0.1:8000,http://10.0.0.2:8000 \
  --duration 120 --concurrency 20
```

报告输出:success/rate_limited/failed、p50/p95 延迟、吞吐 RPS、每操作分解、`tail_risk_95_50_ms`;失败率 >2% 时 exit 1。

## 8. 观测

- `GET /api/system/metrics`:`turn_jobs`(queued/processing/oldest_queued_age_seconds)、`turn_worker`(active/processed)、`database`(pool/cache)、`archived_total`(§18.3)。
- SLO 指标见 `docs/SLO.md` 与 `ops/prometheus/alerts.yml`。

## 相关

`docs/SLO.md`、`docs/DEGRADATION.md`、`docs/OPERATIONS.md`(队列搜索/分页
处置)、`docs/PERF_NOTES.md`、`scripts/pagination_load_test.py`、
`scripts/load_test.py`。
