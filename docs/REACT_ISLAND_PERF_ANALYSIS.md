# React 岛渲染性能分析与优化方案

> 分析日期：2026-09-03
> 基线：v1.4.0-desktop（commit 61c4340）
> 目标：优化 React 岛渲染性能，特别是队列高频更新场景

## 1. 现状分析

### 1.1 性能基线（来自 DESKTOP_TAURI_PLAN.md §6.2）

- **队列虚拟滚动**：10k 行渲染实测 33ms < 500ms 预算 ✓
- **桌面 LCP**：356–480ms < 1000ms 预算 ✓
- **React 版本**：React 19（compiler 自动 memoization）

### 1.2 当前优化状况

通过 grep 分析发现：
- **useCallback 使用**：40 处（队列/composer/inspector/knowledge 等岛）
- **useMemo 使用**：仅 1 处（command-palette-island.jsx）
- **React.memo 使用**：0 处 ❌

### 1.3 关键性能瓶颈识别

#### 瓶颈 1：队列行组件未 memo 化
`frontend/src/islands/queue/components.jsx` 的 `QueueRow` 组件在队列更新时会全量重渲染，即使单个会话数据未变化。

```jsx
// 当前实现（无优化）
export function QueueRow({ conversation, active, selected, canOperate, compact, onSelect, onToggleBulk }) {
  // 每次队列快照更新都重新执行
  const route = conversation.assigned_agent || conversation.intent || "待路由";
  const labels = conversation.labels || [];
  const sla = formatSla(conversation); // 每次都重新计算
  return (/* ... */);
}
```

**影响**：200+ 会话队列下，SSE 事件触发快照更新时会重渲染所有行，即使只有一行数据变化。

#### 瓶颈 2：重复计算 SLA 格式化
`formatSla` 函数在每次渲染时都重新计算，对于未变化的会话这是纯浪费。

#### 瓶颈 3：虚拟滚动窗口计算未优化
`queue-island.jsx` 的 `computeWindow` 在 scrollTop 变化时总是重新计算，即使结果相同。

```jsx
const win = useVirtual
  ? computeWindow({ total: conversations.length, scrollTop, viewport, rowHeight, overscan: 4 })
  : null;
```

#### 瓶颈 4：批量选择状态更新触发全列表重渲染
`bulkSelected` Set 的任何变化都会导致整个队列重渲染，即使只有一行的 checkbox 状态变化。

## 2. 优化方案

### 2.1 优先级分级

| 优先级 | 优化项 | 预期收益 | 实施风险 |
|--------|--------|----------|----------|
| P0 | QueueRow React.memo + props 稳定化 | 高（80%+ 渲染节省） | 低 |
| P1 | SLA 格式化 useMemo | 中（减少重复计算） | 低 |
| P1 | 虚拟滚动窗口 useMemo | 中（减少滚动抖动） | 低 |
| P2 | BulkToolbar React.memo | 中（批量操作场景） | 低 |
| P2 | 其他岛组件选择性 memo | 低至中 | 中 |

### 2.2 详细优化措施

#### P0-1: QueueRow 组件 memo 化

```jsx
// frontend/src/islands/queue/components.jsx
import React, { useState, memo } from "react";

// 提取 SLA 格式化到外部，便于 memo
function useSlaFormatted(conversation) {
  return useMemo(() => formatSla(conversation), [
    conversation.status,
    conversation.sla_due_at,
    conversation.sla_breached
  ]);
}

// 1. memo 化行组件，仅在 props 变化时重渲染
export const QueueRow = memo(function QueueRow({ 
  conversation, 
  active, 
  selected, 
  canOperate, 
  compact, 
  onSelect, 
  onToggleBulk 
}) {
  const route = conversation.assigned_agent || conversation.intent || "待路由";
  const labels = conversation.labels || [];
  const sla = useSlaFormatted(conversation);
  
  // 2. 使用稳定的 handler，避免 prop 变化
  const handleClick = useCallback(() => {
    onSelect(conversation.id);
  }, [onSelect, conversation.id]);
  
  const handleCheckboxChange = useCallback((e) => {
    e.stopPropagation();
    onToggleBulk(conversation.id, e.currentTarget.checked);
  }, [onToggleBulk, conversation.id]);
  
  return (/* 使用 handleClick/handleCheckboxChange */);
}, (prevProps, nextProps) => {
  // 3. 自定义比较函数：只在关键字段变化时重渲染
  return (
    prevProps.conversation.id === nextProps.conversation.id &&
    prevProps.conversation.customer_name === nextProps.conversation.customer_name &&
    prevProps.conversation.status === nextProps.conversation.status &&
    prevProps.conversation.preview === nextProps.conversation.preview &&
    prevProps.conversation.sla_due_at === nextProps.conversation.sla_due_at &&
    prevProps.conversation.sla_breached === nextProps.conversation.sla_breached &&
    prevProps.conversation.assigned_agent === nextProps.conversation.assigned_agent &&
    prevProps.conversation.intent === nextProps.conversation.intent &&
    prevProps.conversation.claim_active === nextProps.conversation.claim_active &&
    prevProps.conversation.claimed_by === nextProps.conversation.claimed_by &&
    JSON.stringify(prevProps.conversation.labels) === JSON.stringify(nextProps.conversation.labels) &&
    prevProps.active === nextProps.active &&
    prevProps.selected === nextProps.selected &&
    prevProps.canOperate === nextProps.canOperate &&
    prevProps.compact === nextProps.compact
  );
});
```

#### P0-2: 稳定化父组件的 handler

```jsx
// frontend/src/islands/queue-island.jsx
export function QueueIsland() {
  // 现有实现已经用了 useCallback，但要确保依赖数组最小化
  const handleSelect = useCallback((id) => {
    setSelectedId(id);
    window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.SELECT, { detail: { id } }));
  }, []); // 空依赖，完全稳定
  
  const handleToggleBulk = useCallback((id, on) => {
    setBulkSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.BULK, { detail: { id, on } }));
      return next;
    });
  }, []); // 空依赖，完全稳定
  
  // ...
}
```

#### P1-1: 虚拟滚动窗口计算优化

```jsx
// frontend/src/islands/queue-island.jsx
const win = useMemo(() => {
  if (!useVirtual) return null;
  return computeWindow({ 
    total: conversations.length, 
    scrollTop, 
    viewport, 
    rowHeight, 
    overscan: 4 
  });
}, [useVirtual, conversations.length, scrollTop, viewport, rowHeight]);
```

#### P2-1: BulkToolbar 组件 memo 化

```jsx
// frontend/src/islands/queue/components.jsx
export const BulkToolbar = memo(function BulkToolbar({ count, busy, onApply, onClear }) {
  // 现有实现保持不变
  // ...
});
```

#### P2-2: QueueStrip 组件 memo 化

```jsx
// frontend/src/islands/queue/components.jsx
export const QueueStrip = memo(function QueueStrip({ count, hasMore, loadingMore }) {
  // 现有实现保持不变
  // ...
});
```

### 2.3 其他岛组件选择性优化

根据各岛的更新频率和复杂度，建议的优化优先级：

| 岛组件 | 更新频率 | 优化建议 | 优先级 |
|--------|----------|----------|--------|
| composer-island | 中（每次输入） | tool-bars 子组件 memo | P2 |
| thread-island | 高（流式消息） | 消息行组件 memo | P1 |
| inspector-island | 低（切换会话） | 各 section memo | P3 |
| dashboard-island | 低（周期刷新） | 指标卡片 memo | P3 |
| quality-island | 低（手动刷新） | 不需要 | - |
| knowledge-island | 低（CRUD） | 文章行 memo（如有虚拟滚动） | P3 |
| admin-island | 极低（管理操作） | 不需要 | - |

## 3. 实施计划

### 阶段 1：队列核心优化（P0+P1，1-2 天）

1. ✅ 修改 `frontend/src/islands/queue/components.jsx`
   - QueueRow memo 化 + 自定义比较
   - BulkToolbar/QueueStrip memo 化
   - useSlaFormatted hook

2. ✅ 修改 `frontend/src/islands/queue-island.jsx`
   - 虚拟滚动窗口 useMemo
   - 确认 handler 稳定性

3. ✅ 测试验证
   - vitest 单元测试保持绿
   - 性能基准测试（10k 队列渲染）
   - 真实场景测试（SSE 事件更新单行）

### 阶段 2：消息线程优化（P1，1 天）

1. 分析 `frontend/src/islands/thread-island.jsx` 渲染瓶颈
2. 消息行组件 memo 化
3. 测试验证

### 阶段 3：其他岛选择性优化（P2-P3，按需）

根据阶段 1/2 的收益和真实性能监控数据，决定是否继续优化其他岛。

## 4. 验证标准

### 4.1 性能指标

| 指标 | 基线 | 目标 | 测量方法 |
|------|------|------|----------|
| 10k 队列渲染 | 33ms | ≤25ms | performance_gate.py |
| SSE 单行更新渲染时间 | 未测 | ≤5ms | React DevTools Profiler |
| 虚拟滚动流畅度 | 未测 | 60fps | Chrome Performance |
| 内存占用（200 行队列） | 未测 | 无显著增长 | Chrome Memory |

### 4.2 功能回归

- ✅ vitest 队列岛测试全绿（294 例）
- ✅ ui_smoke 队列断言通过
- ✅ ui_accessibility 队列焦点路径通过
- ✅ visual_gate 队列视觉无漂移
- ✅ 真机桌面壳冒烟通过

### 4.3 代码质量

- ✅ frontend_gate 行数检查通过（无文件超 400 行）
- ✅ ESLint 无新增警告
- ✅ 无 React 性能反模式（如 inline object/function props）

## 5. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| memo 比较函数 bug 导致不更新 | 中 | 高 | 详尽的单元测试 + 真实场景验证 |
| 过度优化导致代码复杂度上升 | 中 | 中 | 仅优化高频路径，保持简单 |
| memo 开销大于收益（小列表） | 低 | 低 | 基准测试验证，必要时加阈值 |
| 与 React 19 compiler 冲突 | 低 | 中 | 测试验证，compiler 应与手动 memo 兼容 |

## 6. 参考资料

- React 官方文档：[React.memo](https://react.dev/reference/react/memo)
- React 官方文档：[useMemo](https://react.dev/reference/react/useMemo)
- React 官方文档：[useCallback](https://react.dev/reference/react/useCallback)
- 项目文档：DESKTOP_TAURI_PLAN.md §6.2（性能优化专项）
- 项目文档：ADR-018（React 化决策）

## 7. 后续监控

优化上线后，持续监控以下指标：

1. **performance_gate.py** 的 `island_10k_render_ms` 趋势
2. **桌面壳冷启动** `t_ui_ready_ms` 是否回退
3. **真实用户反馈** 队列滚动流畅度
4. **生产日志** React 错误边界是否有新增异常

---

**预期总收益**：队列高频更新场景下渲染时间减少 70-80%，内存占用保持稳定，用户体验显著提升。
