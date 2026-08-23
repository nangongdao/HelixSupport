# ADR-012: 安全治理自动化（SEC-008）

- 状态：已接受（Phase 41.7，1.4 / Secure Operations）
- 日期：2026-08-20

## 背景

ROADMAP 41.7（SEC-008）的威胁模型是：**威胁模型 delta 与安全复审从未进入
发布 gate**。风险登记册只做一次性人工复核（`ROADMAP_2_X.md` §3），每次发布
“人工复核安全模型”，但没有机械校验：delta 可以缺字段、owner 可以写占位符
（`security@helix.example`、`TBD`、`<请填写>`），季度桌面演练可以无限期过期，
报告渠道 placeholder 不会被发现。SEC-008 要求把这些治理动作自动化进发布 gate：

- 每个版本提交 threat-model delta：新增入口/资产/信任边界、已关闭/新增风险、
  控制和验证证据；安全负责人批准后才能发布。
- `SECURITY.md` 中的真实报告地址、值班 owner 和测试时间只从部署配置/私有运行
  记录确认；placeholder 在外部环境直接阻断 readiness 或 release gate。
- 季度执行报告接收、依赖漏洞、密钥泄露和跨租户告警桌面演练；记录确认、控制、
  修复、沟通和复盘耗时。
- 验收：缺失 delta、未命名 owner、placeholder 报告渠道、过期演练或超期例外
  均使发布 gate 失败。

## 决策

### 1. 每发布 threat-model delta（`supplychain/threat-model-deltas.json`）

- 每个 release 一条 delta 记录：`release`（版本）、`date`、`owner`（具名，
  禁止 placeholder）、`approved_by`（安全负责人具名批准）、`controls`
  （本次发布新增控制项，非空列表）、`verification_evidence`（验证证据，
  非空列表）。schema_version 固定 1。
- 新增入口/资产/信任边界与关闭/新增风险的描述并入 `controls`（控制对应
  风险）与 delta 自由文本，证据链留在 `verification_evidence`。

### 2. placeholder 阻断（`scripts/threat_model_gate.py`）

- `owner`/`approved_by`/演练 `owner` 通过正则与空白检查识别占位符：
  `security@helix.example`、`example.com`、`tbd`/`todo`/`待定`/`占位`、
  `<...>`、空串等，一律红灯；缺失必填字段（release/date/owner/approved_by/
  controls/verification_evidence 或 drill_type/started_at/owner/scenario/
  duration_minutes）、空 controls/evidence、非法 `drill_type` 同样红灯。
- 与既有 gate（`vuln_review.py`/`check_workflows.py`）同构：模块级
  `ReviewError` 映射 exit 2（配置不可读/坏 JSON），违规逐条打印映射 exit 1，
  干净返回 exit 0。
- **CI nil-tolerant**：不传 `--release`/`--check-today` 时，空登记册本身不是
  违规——placeholder 红灯与演练过期检查是发布时刻/受控环境的强制检查，
  避免每个 PR 因治理台账尚未填写而误报。`--release <version>` 校验该版本
  delta 已提交且日期不在未来；`--check-today` 校验最近演练未过期
  （默认 90 天）、未来日期与“无任何演练”均红灯。

### 3. 季度桌面演练台账（`supplychain/security-drills.json`）

- 四类演练固定枚举：`report_intake`（报告接收）、`dependency_vuln`
  （依赖漏洞）、`key_compromise`（密钥泄露）、`cross_tenant_alarm`
  （跨租户告警）。每条记录 `drill_type`/`started_at`/`owner`/`scenario`/
  `duration_minutes`，owner 具名，禁止占位。
- `--check-today --drill-max-days 90` 判定最近演练距今超过 90 天为过期。

### 4. 真实渠道断言（`SECURITY.md` / readiness）

- SEC-008 的第二条要求（报告地址、值班 owner、测试时间只从部署配置/私有
  运行记录确认）由 SECURITY.md 承载：placeholder 邮箱与部署前配置步骤已在
  1.4 开发中保留为文档形态，部署负责人完成真实渠道演练前（SEC-007 / M0
  闭环后外部可见）不得宣称生产就绪。本文档与 gate 只保证台账结构化、命名
  与日期合法，不代为验证外部私有渠道本身——那是部署配置与私密演练领域。

## 后果

- 优点：发布 gate 具备机器可判定的治理证据；placeholder 在任何进入
  release/readiness 的路径上无法静默通过；演练被周期强制。
- 代价：每次发布需要真实填写 delta（一个新文件条目）；季度演练需计时与
  具名 owner；CI 中 gate 默认 nil-tolerant，真正执行需显式传参与受控环境。
- 未闭环（Gate B 待办）：真实私有报告渠道的端到端演练、runbook 非作者执行
  仍由外部运行记录承担（ROADMAP §6 Gate B），gate 只校验台账形态。

## 相关

`docs/adr/0008-m0-security-hardening.md`（SEC-007 报告闭环语义）、
`docs/adr/0010-supply-chain.md`（gate 同构形态）、`SECURITY.md`（仓库根目录，
报告渠道与部署配置占位）、`ROADMAP_2_X.md` §41.7 与 Gate B。