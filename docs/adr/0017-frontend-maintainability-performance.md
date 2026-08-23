# ADR-017: 前端可维护性与性能预算（领域模块迁移 / 浏览器 performance gate / 视觉回归）

- 状态：已接受（Phase 43.6，2.0 后续）
- 日期：2026-08-23

## 背景

`ROADMAP_2_X.md` §43.6 要求：

- 完成 legacy controller 到领域模块迁移；建立 UI component contract 和状态机，
  不强制换框架。
- 首屏 JS/CSS、LCP/INP/CLS、长任务、内存和 10k 队列渲染设预算；真实浏览器
  performance gate 进入 CI/nightly。
- 视觉回归只对稳定组件/关键状态做截图差异，保留 axe、键盘、移动和
  reduced-motion gate。

既有缺口：`app/static/app.js` 在 §41.6 第一步拆分后仍有约 3,533 行（notes.md
基线为 4,560 行），inspector 渲染/标签与优先级变更流仍内联在 legacy
controller 中；无任何性能预算执行点；视觉层只有 axe/键盘/reduced-motion
验收（tests/ui_accessibility.py），没有像素回归。

## 决策

### 1. 领域模块迁移第二步 + inspector 状态机（`app/static/js/inspector.js`）

- 沿用 §41.6 ARC-001 的既定模式：深模块持逻辑，app.js 保留同名同签名的薄
  包装器经 `window.HelixModules?.inspector?.[fn](...arguments)` 委托，
  `configure()` 注入 singletons（state/els/api/loadDetail/refreshAll/
  loadQualityPanel/canOperate/setFormBusy/showToast/escapeHtml/formatTime/
  latestAssistant/renderLabelChips）。行为零变化，不换框架。
- 迁移面：renderOverview/renderEvidence/renderAudit 三 tab 渲染器 +
  updateLabels/updatePriority 变更流 + safeCitationUrl（href 白名单：
  仅 `/` 相对与 https://，其余降级 `#`）+ resetInspectorRenderFlags /
  ensureInspectorTab / renderInspector / switchInspectorTab。
- **component contract 与状态机**：模块导出纯函数三元组——
  `INSPECTER_TABS` 冻结数组（overview/evidence/audit/quality 四挂载点）、
  `createInspectorState()` 初始状态、`reduceInspector(state, action)` 唯一
  转移入口（select-tab / mark-rendered / invalidate / set-collapsed；未知
  action 与幂等转移返回同一引用以便调用方零成本判空转）。DOM 层在迁移期
  继续驱动 legacy `state.inspectorRendered` 标志保证行为对等，reducer 是
  同语义的镜像源，后续 app.js state 入模块后成为唯一权威。
- main.js import 图新增 inspector 节点并暴露于 HelixModules；
  index.html 增加 modulepreload（保持 §18.4 的预取并行度）。

### 2. 性能预算双层 gate（`scripts/performance_gate.py`）

- **静态字节预算**（默认 pytest 档，无浏览器）：首屏载荷 =
  app.js + js/*.js（operator JS）、styles.css + css/tokens.css（operator
  CSS）、widget-app.js + js/widget-core.js（widget JS）。预算按 1.3.x 实测
  基线（277 KB / 85 KB / 20 KB）留 ~25% 余量钉在 345 KB / 105 KB /
  25 KB——正常特性迭代不触发，失控依赖或未压缩 vendor blob 即失败。
- **真实浏览器预算**（Playwright Chromium，与 tests/ui_smoke.py 同 harness）：
  LCP ≤ 2500 ms（PerformanceObserver buffered）、CLS ≤ 0.10、5 s 窗口长任务
  数 ≤ 50（longtask buffered）、10k 合成会话注入后单次 windowed render
  （renderQueue 全量调用计时，VIRTUAL_THRESHOLD=200 以上路径）≤ 2000 ms、
  20 个刷新周期的 JS heap 波动 ≤ 15 MB。
- 关键工程事实：operator 控制台的队列 SSE 流（/api/events/queue，45 s
  hold）使 `networkidle` 永不触发——测量用 domcontentloaded +
  `#conversationList[aria-busy='false']` + 固定 settle 窗口替代。
- CI 分层：静态层进默认 pytest（tests/test_performance_gate.py）；浏览器层
  挂 ci.yml 新增 schedule 触发（cron 0 3 * * *）的 nightly 步骤——wall-clock
  预算对 runner 敏感，不进 per-PR 门。基线 JSON 写 artifacts/
  performance-baseline.json（--update 重写）。
- INP 不直接断言：Chromium 的 web-vitals INP 归因需要事件流回放，长任务数
  + LCP 已覆盖同一退化模式（主线程阻塞），列为后续增强而非缺口。

### 3. 视觉回归只覆盖稳定面（`scripts/visual_gate.py`）

- 四个稳定表面入基线（tests/baselines/*.png）：workspace-dark /
  workspace-light（主题 token 集）/ knowledge-view / mobile-queue drawer。
  会话数据、时间戳、SLA 倒计时等动态区域在截图前统一 mask（textContent='0'
  + color:transparent），基线因此对数据churn免疫。
- 比较参数：像素通道容差 ±12（主题过渡抗锯齿）、整图差分比上限 0.5%；
  viewport 尺寸变化视为布局级变更→重建基线并放行（显式提示）。
  bootstrap 语义：无基线时首跑写入并放行；有意 UI 变更删基线重引导。
  drift 时把当前截图落 artifacts/visual-drift-<name>.png 供人审。
- 纯 Python/Pillow 逐像素比较（无 pixelmatch 等 node 依赖引入，保持零构建）；
  自检证明 4.09% 差分被正确拒绝。
- 保留项不动：axe（critical/serious 零违规）、桌面焦点序、knowledge 键盘
  路径、移动 focus trap、reduced-motion 断言全部留在 tests/ui_accessibility.py。

## 后果

- 正向：app.js 3,533 → 约 3,386 行且 inspector 面有独立受测契约；首屏载荷
  有硬上界并在每次 pytest 执行；渲染/内存回归可 nightly 捕获；UI 改动有
  客观像素证据链。
- 取舍：字节/时间预算是防回归护栏而非优化目标——未做 minification/打包，
  零构建原则优先；夜间档意味着浏览器层回归最长滞后一天被发现。
- 维护责任：改 inspector 结构需同步 reducer 语义；有意视觉变更必须显式
  重建基线（diff 报告给出命令路径）；预算收紧随 minification 引入另行决策。

## 相关

- ADR-0006（后端结构治理）、ADR-0014（评测 Gate v1 的 WORM 报告先例）、
  ADR-0016（AI Governance v2，同期 43.x 收尾）。
