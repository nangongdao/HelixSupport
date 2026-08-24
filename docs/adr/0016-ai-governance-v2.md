# ADR-016: AI Governance v2（provider 治理 / 工具能力令牌 / drift 自动停 canary）

- 状态：已接受（Phase 43.5，2.0 后续）
- 日期：2026-08-23

## 背景

`ROADMAP_2_X.md` §43.5 在 §43.1 租户控制面与 ADR-014 评测 Gate v1 的基础上提出
AI Governance v2：

- Eval registry 需保存 dataset/version/hash、模型/提示/工具策略、结果、审批与
  部署关联；线上反馈回流必须经过脱敏和人工审核。
- 工具调用需使用 capability token、参数 schema、预算、side-effect
  classification；高风险动作要求 human approval。
- 模型 provider 需记录数据保留/训练 opt-out/region；tenant policy 可禁用
  provider、模型、工具和数据出境。
- 线上 drift、拒答率、升级率、citation validity、tool denial 和成本需能触发
  自动停止 canary/回滚。

既有缺口：v41 migration 已建四张治理表但无服务层；`tool_governance.py`/
`tools.py` 的治理面未接线；provider 无元数据声明，控制面 `model_policy`
只有存储语义没有执行点；线上信号（升级率、负反馈、拒答）不会反作用于
canary 路由。

## 决策

### 1. Provider 元数据与 tenant 禁用面（`app/model_provider.py`）

- `ProviderMetadata(name, data_retention, training_opt_out, region)` 声明式
  注册表 `PROVIDER_METADATA`——部署整体替换条目接入真实 vendor，模块不做环境读取。
  `OpenAICompatibleProvider.metadata` 暴露所属声明。
- `check_model_policy(model_policy, model_ref, tenant_region)` 三 facet 判定：
  `disabled_models` 精确匹配 → `disabled_providers`（经
  `provider_for_model_ref` 从 `provider/model` / `provider:model` 引用推断，
  裸引用归属 settings 默认 provider）→ `allow_data_egress=False` 时比对
  provider 声明 region 与租户 pinned region（`resolve_region` 规范化）。
  空/缺失策略全放行（向后兼容默认）。
- 执行点在 turn policy 阶段（`app/turn_policy.py`）：19.4 allow-list 通过后，
  再从数据面 `effective_policy(tenant_id).model_policy` 取禁用面判定；被拒时
  `allow_model=False` 并在 `turn.model_denied` 审计 payload 附 `reason`。
  无控制面 / plane 不可达 / 无快照（`PolicyUnavailableError`）一律跳过该门，
  保持 pre-43.5 fail-open 行为——控制面降级不 brick 流量。

### 2. 工具治理执行（`app/tools.py` + `app/tool_governance.py`）

- 每个网关工具注册 `ToolPolicy(side_effect, parameter_schema,
  max_duration_ms)`（`DEFAULT_TOOL_POLICIES` 覆盖沙箱面）。side-effect 类：
  `readonly` / `mutating` / `high_risk`。
- `enforce_governance` 固定顺序 fail closed：未注册策略即拒 → 出示的
  capability token 验签/过期/tool+tenant 绑定/schema digest 钉扎 → 实参对
  schema 校验 → `high_risk` 还须 `AiGovernanceService.require_approved`
  （maker-checker），无 registry 或记录非 approved 一律拒。拒绝以
  `ToolGovernanceDenied(reason)` 抛出/返回 `policy_denied`，日志记
  `tool.denied tool=… reason=…` 供 drift 计数。
- token 由 `issue_capability_token`（HMAC-SHA256、短 TTL、单工具单租户、
  schema digest）签发；schema 编辑使所有在外 token 失效。

### 3. Eval registry 与线上反馈回流（`app/ai_governance.py`）

- v41 四表的服务层：dataset 版本单调递增 + `content_hash`（canonical JSON
  sha256）钉死条目集合，每次 load 重验 hash；eval run 关联 dataset/candidate/
  baseline/WORM report object id。
- maker-checker 审批：同 subject 单开放请求；请求者不能审批自己
  （`SelfApprovalError`）；`require_approved` fail closed。
- 线上反馈两道门后才可入 dataset：ingest 时先 `redact_sensitive`（存的就是
  脱敏文档，下游无人可达原文），且保持 `pending_review` 直到人工复核；
  `promote_feedback_to_dataset` 只接受 accepted 行，混入 pending/rejected
  整批中止。

### 4. Drift 监控自动停 canary（`app/drift_monitor.py`）

- 信号全部来自既有面，turn 时零新写入：质量桶（升级率、负反馈率）+
  审计拒绝计数（`turn.model_denied`/`turn.budget_exceeded` 合并为模型拒答、
  `tool.denied` 为工具拒绝）。窗口、最小样本量、各阈值由 `DRIFT_*` 配置组
  控制（rate 类 (0,1]，计数类正整数，None 显式停用该信号）。
- 任一阈值越限：清空该租户所有 canary 回 draft
  （`PromptRegistry.clear_canary`，审计 `prompt_version.canary_cleared`）并
  记 `ai.drift_canary_stopped`（含信号明细与停止清单）；无 canary 也记事件，
  让稳定流量上的持续 drift 可见。active 版本绝不动——回滚到 retired 版本
  仍是显式的人工决策。
- 挂 turn-worker housekeeping 小时级 sweep；失败仅记日志；`DRIFT_ENABLED`
  默认关，既有部署零影响。ADR-014 评测 Gate 是晋级前的静态防线，drift
  monitor 是上线后的动态回收，两者共用"canary 是唯一受控放量通道"的语义。

## 后果

- 优点：
  - provider 治理三要素（保留期/opt-out/region）机器可读，tenant 可按
    provider/模型/出境三维禁用，拒绝原因进审计链可追溯。
  - 工具调用从"连接器信任 agent 参数"升级为"签名能力 + schema + 分级 +
    审批"的完整链路，高风险动作无人工记录不可能执行。
  - 反馈回流默认不可见、脱敏前置，dataset 内容 hash 可复现评测。
  - drift 响应全自动且有界：只收 canary、不动 active、不写 turn 时路径。
- 代价 / 风险：
  - 控制面不可用时 43.5 门静默跳过（fail-open）——这是刻意取舍：43.1 数据面
    已保证 LKG 过期才 fail closed，此处二次 fail closed 会把策略故障放大成
    全量停服。审计里看不到 reason 字段即为跳过态。
  - citation validity 与成本阈值本轮未接 drift 信号源（citation 断言在评测
    Gate 内、成本估算确定性路径恒 0）；live-model 成本遥测落地后在同一
    `collect_signals` 扩展，不新增机制。
  - capability secret 尚未在 main.py→orchestrator→ToolGateway 生产接线
    （gateway 构造缺省空 secret = 不强制 token）；接线随首个真实 mutating
    工具一起做，避免为只读沙箱面引入密钥分发负担。
- 迁移路径：
  - 全部为 additive：v41 表已 expand、新配置默认关闭、无端点变更
    （OpenAPI 快照不变）、`clear_canary`/`run_once` 均为新函数不触碰既有调用方。
  - 启用顺序：设 ≥32B `CONTROL_PLANE_SECRET` → 控制面下发
    `model_policy` → `DRIFT_ENABLED=1` + 按站点调 `DRIFT_*` 阈值。

## 相关

- `docs/adr/0014-ai-safety-eval-gate.md`（晋级 Gate 与 canary 语义）
- `docs/adr/0013-data-protection.md`（redaction 复用）
- `docs/adr/0015-postgres-row-level-security.md`（审计租户作用域）
- `ROADMAP_2_X.md` §43.5
- `app/model_provider.py`、`app/tools.py`、`app/tool_governance.py`、
  `app/ai_governance.py`、`app/drift_monitor.py`、`app/prompts.py`
