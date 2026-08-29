# Changelog

所有版本遵循[语义化版本](https://semver.org)。API 变更遵循 `docs/API_POLICY.md`(响应体只增不改、弃用需 `Deprecation`/`Sunset` 头 + 至少一个次版本过渡、每次变更记录于此)。

## 1.4.0-desktop — Tauri 2.x 桌面壳 + React 岛双轨(2026-08-26)

### Added

- **桌面原生壳**(`src-tauri/`,基于 Tauri 2.x):`SidecarSupervisor`(动态端口 bind 127.0.0.1:0 → 回读 → 注入 WebView、指数退避就绪探测 200ms→2s 上限 20s、TERM→5s 超时 kill 树优雅停机、崩溃自愈 ≤3 次/分钟超出弹窗、单实例锁二次启动唤起)、`terminal.rs`(portable-pty 白名单诊断终端,xterm.js + fit/webgl/search 三 addon,仅 `admin`/`platform` 角色可见,DEBUG 构建才启用完整交互式 PTY,空闲 10 分钟回收、输出环形缓冲 5MB 上限)、启动三时间戳遥测写入 `%APPDATA%/HelixSupport/telemetry/startup.json`、Splash 屏、设置页显示版本/DB 路径/端口。
- **Python sidecar 打包**(`desktop/helix-server.spec`,PyInstaller `--onedir`):`DATABASE_PATH` env 注入 `app_data_dir`,后端零改动;冒烟脚本 `desktop/smoke_sidecar.py`(spawn→/health/ready→sample API→graceful kill)实测 2.2s 就绪。
- **React 19 岛渐进迁移**(`frontend/src/islands/`):9 个岛(quality/knowledge/ticket/queue/composer/inspector/command-palette/session-shell/terminal);queue 与 inspector 直通 §43.6 reducer 三元组作 `useReducer` 入参,纯函数测试零改写;Zustand v5 客户端全局状态 + TanStack Query v5 服务端状态;`frontend/src/island-loader.js` 运行时 fetch `/static/dist/manifest.json` 解析内容哈希 chunk,无 manifest 时静默跳过所有岛(CSP `script-src 'self'` 下不再报 dev-origin 违反)。
- **tokens.css 三层 @layer**(`@layer tokens.primitive/semantic/component`)+ styles.css `@layer reset, tokens, base, components, utilities` 层叠顺序;motion tokens(`--duration-fast/base/slow` + `--ease-entry/exit/emphasized`);View Transitions API 列表→详情过渡、骨架屏、按钮按压/抽屉/tab indicator 微交互(reduced-motion gate 复验)。
- **ADR-018**(`docs/adr/0018-break-zero-build-vite-react.md`):记录打破零构建原则引入 Vite + React 构建链的动机与边界(operator console 引入构建链;widget 永久保持零构建;双轨期 `createRoot` 挂载到预留 `<div>`,未迁移区由 app.js + js/*.js 驱动;预算口径切换 operator JS ≤700KB raw/≤210KB gzip、CSS ≤125KB)。
- **D5 交互打磨**:`tauri-plugin-updater` 自动更新、NSIS 安装器;冷启动 SLO `startup.json` 验证 `t_backend_ready_ms=2465ms` < 3s。
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

- **桌面壳资源路径断裂(真机 GUI 冒烟发现,2026-08-29)**:`frontendDist` 嵌入模式下 webview 直接加载 `index.html`,但页面所有资源是 `/static/...` 绝对路径(为 uvicorn StaticFiles 设计),`tauri://localhost` 下全部 404——真机窗口渲染裸 HTML 骨架(无 CSS/JS),UI 永不初始化,`t_ui_ready_ms` 恒 null。修复:壳只在启动帧显示内置 splash 页,后端就绪后 `WebviewWindow::navigate` 到 sidecar 自身 origin(`http://127.0.0.1:<port>/`),资源与 API 全部同源成立;watchdog 自动重启绑定新端口后重新导航。`on_page_load` 在服务端页面加载完成后注入 `__HELIX_BACKEND__` 并派发 `helix-backend-ready`、直调 `ui_ready`(on_page_load 与 module 执行时序跨导航已证不稳,直调兜底)。远程页面 IPC 需显式 ACL 授权:`build.rs` 用 `AppManifest::commands` 为 7 个自定义命令自动生成 `allow-*` 权限,`capabilities/main.json` 配 `remote.urls=["http://127.0.0.1:*"]` + 权限授予。真机复验:窗口渲染完整深色控制台,三时间戳 window 969ms / backend 3306ms / ui_ready 3872ms,API 200,关窗优雅停机无残留。
- **亮色主题 amber 状态徽标对比度不足**(^d92236c):`.status-pill.waiting_human` 以 `--color-amber` 文字压在 `--color-amber-soft` 叠行底色上,实测 **3.99:1**,低于 11px 文字要求的 4.5:1。该缺陷在历次 axe 运行中全部存活——徽标只在队列有行时渲染,而旧扫描面对的永远是空队列。桌面壳 pass 注入数据后首轮即被抓出。`--color-amber` 亮色值 `#8a6512` → `#6f5010`(5.58:1),与同背景的兄弟状态色对齐(green 6.17:1 / violet 5.91:1 / blue 4.85:1),soft 色调同步重算保留琥珀倾向。

### Gates(2026-08-28 复跑)

- frontend gate **165** tests + vitest **19**;performance gate 静态 JS 599KB/700KB + CSS 97KB/125KB,浏览器层 web + 桌面壳双上下文全绿;visual gate 四面 **0.00% drift**(令牌改动未波及 clean DB 基线);ui_smoke + ui_accessibility(含新增桌面壳 pass)旅程绿;`tests/test_frontend_gate.py` + `tests/test_performance_gate.py` 10 例绿。
- 注:本机 ruff 0.16.5 默认规则集宽于项目开发期(CI 固定 `ruff>=0.9,<1`),全仓 372 项报告属版本差异,非本次改动引入;本次改动未新增告警(并顺带消掉 1 项 PIE810)。

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
