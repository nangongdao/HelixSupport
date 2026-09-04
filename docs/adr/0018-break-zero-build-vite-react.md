# ADR-018: 打破零构建原则引入 Vite + React 构建链

- 状态：已接受
- 日期：2026-08-25

## 背景

ADR-0017 确立了「零构建」前端原则：所有 ES 模块以源码形态直接由 Python 后端 serve，以 `?v=STATIC_ASSET_VERSION` 手工缓存破坏，无打包/无 transpile/无 CDN。该原则在 1.3.x 周期成功支撑了 22 个领域模块 + 3258 行 app.js 控制器的增量演进，并将 JS 预算控制在 277KB（原始）/ CSS 86KB。ADR-0017 同时显式遗留了「minification/打包与预算收紧另行决策」的开放决策点。

桌面化（DESKTOP_TAURI_PLAN.md §3）需要将核心域渐进迁移到 React 岛（island）模式。JSX 无法在零构建管线中编译；code-splitting（首屏只载 workspace 岛）与 minification 是 §6.1 冷启动 <3s 目标的必要手段；HMR 开发体验在 React 语境下是刚需。零构建原则与这些需求直接冲突。

## 决策

**采用 Vite + @vitejs/plugin-react 构建链**，源码迁至 `frontend/src/`，产物输出 `app/static/dist/`。

边界与约束：

1. **仅 operator console 引入构建链**。嵌入式 Web Chat（widget-app.js + widget-core.js）**不迁移**，保持零构建——25KB 预算与部署独立性是 widget 的卖点，引入构建链将破坏第三方嵌入场景。
2. **双轨期共存**：React 岛通过 `createRoot` 挂载到 index.html 中预留的 `<div>`，未迁移区域继续由 app.js + js/*.js 驱动。Vite 产物（content-hashed 文件名）自缓存破坏，不需要 `?v=`；零构建模块继续用 `?v=STATIC_ASSET_VERSION`。
3. **预算口径切换**（§6.3）：operator JS 从 ≤345KB 调整为 ≤700KB raw / ≤210KB gzip（React 19 + ReactDOM ≈ 140KB raw）；CSS 从 ≤105KB 调整为 ≤125KB。performance_gate 已适配，将 `dist/assets/*.js` 纳入计算（终端岛懒加载、不计入首屏预算）。
4. **门禁保留**：ui_smoke `#queuePane` 类名断言、axe ≥4.5:1、视觉基线、performance_gate 全部保留。reducer 纯函数（§43.6）零改写，新增 @testing-library/react 组件用例由 frontend_gate 的 vitest 段执行。
5. **widget 例外永久保留**：若未来 widget 也需构建，须另开 ADR。

备选方案：

- **Electron + 全量 React 重写**：一次性作废全部六道门禁与 3258 行 legacy，回归面不可控，体积/内存税永久支付。否决。
- **Preact + 无构建 JSX 替代（htm）**：避免构建链但丧失 code-splitting 与 HMR，且 §43.6 reducer 资产仍需手工桥接。收益不抵成本。

## 后果

- 优点：React 19 compiler 自动 memoization 对队列高频渲染收益直接；code-splitting 支撑冷启动 <3s；HMR 提升开发体验；§43.6 reducer 作为 `useReducer` 入参零改写迁移。
- 代价 / 风险：引入 Node 构建依赖（Vite/esbuild）；dist/ 产物体积纳入预算，需持续卡控；双轨期 bundle 膨胀由 performance_gate 硬卡。
- 迁移路径：D2 完成 Vite 地基 + 第一岛（quality）；D3 按叶→根序迁移 knowledge→ticket→queue→composer→inspector→session/shell，每岛一 PR 全门禁；D3 末强制 app.js <500 行验收。

## 相关

- [ADR-0017](0017-frontend-maintainability-performance.md) — 前端可维护性与性能预算（零构建原则的提出与遗留决策点）
- DESKTOP_TAURI_PLAN.md §3（React 化策略）、§6.3（包体预算）
