# ADR-011: 审计证据外部锚定（SEC-005）

- 状态：已接受（Phase 41.3，1.4 / Secure Operations）
- 日期：2026-08-20

## 背景

ROADMAP 41.3（SEC-005）的威胁模型是：**DB 管理员或任何能写出数据库的人可以
重算本地哈希链**。Phase 28.3 的链让“行内容被改”可被发现，但链头本身仍只存
在本地——拥有数据库写权限的攻击者可以按同一哈希算法重放所有事件、重算链头，
让校验器对整个篡改故事返回“intact”。审计证据必须有一部分不活在攻击者的
可写域里：

- 把链头 `{last_seq,last_hash,timestamp,environment}` 用 KMS 非对称密钥签名，
  导出到 WORM / object-lock 存储——攻击者没有 KMS 私钥，也改不了 WORM 上的对象。
- 安全/权限/凭据/DSR 这类高危变更必须与其审计证据同一事务持久化（同事务
  audit/outbox），审计失败时整笔 mutation 回滚并 fail closed，避免“变更发生了、
  证据丢了”。
- 普通对话 telemetry 走已有 best-effort 路径，失败时不允许沉默——必须产生
  可观测的 `audit_gap` 告警与修复任务。
- 恢复校验必须同时核对本地链、归档 manifest 与外部锚点三份证据。

## 决策

### 1. 同事务高危锚定（`app/audit_gap.py`、migration 31）

- 新增 `audit_anchors` 表（migration 31）：`anchor_id / seq / chain_hash /
  event_type / reason / created_at`。高危事件写审计行后立即在**同一
  `BEGIN IMMEDIATE` 事务**内记录链头 `(seq, chain_hash)`。
- `audit_high_risk()` 独占入口：append 事件 + 读 `_audit_chain_tail` +
  INSERT frontier 行一起提交或一起回滚。任何失败把整笔事务回滚并抛出
  `AuditUnavailableError` → 调用方 fail closed（HTTP 503
  `code="audit_unavailable"`、`Retry-After: 30`）。
- 高危事件族固定 12 类（`HIGH_RISK_EVENT_TYPES`：api key 发放/吊销、
  DSR 创建/审批/执行、成员邀请/角色变更/停用、retention 策略更新、
  SLA 策略设置、webhook 注册/删除）。
- 普通 telemetry 失败走 `record_audit_gap()`：落 `audit_gaps` 行 + 进程内
  计数器，经 `GET /api/admin/audit/gaps` 可观测。

### 2. KMS 签名 + WORM 锚点（`app/audit_anchor.py`、`app/worm_store.py`）

- 锚点文档 `{schema_version, kind, anchor_id, environment, last_seq,
  last_hash, timestamp, kid, public_key, signature, signature_scheme}`：
  自校验——内嵌 public key，恢复时不依赖按需查询 KMS。
- `kid` 是 public key 的 sha256 前缀。验证时强制“kid 与内嵌公钥一致”，并只
  接受出现在 `trusted_kids` 白名单中的 kid。**轮换语义**：KMS 换新 key 产出的
  新签名在信任名单更新前一律拒绝（“not in the trusted set”），更新后可正常
  验证——历史锚点跨轮换保持可验证。
- `DiskWormStore`：每对象 JSON + 内容哈希 journal，对象只写一次；读回时
  校验 journal 一致性，发现孤儿对象、哈希/元数据漂移、对象丢失立即抛
  `WormIntegrityError`。`_housekeeping_audit_anchor` 挂在 turn worker 的
  housekeeping 上周期性把当前链头导出为一份新 claim。

### 3. 统一校验器（`scripts/verify_audit_chain.py`）

- 一次运行校验三份证据：重算本地全链（热表 + 归档 manifest 合并）→ 核对
  `audit_anchors` 每个 frontier tip 的 `seq/hash` → 校验每个 WORM claim 的
  签名/kid/环境/与链的一致性。
- 故障模式全部显式：重算全链（`hash mismatch`）、删除 anchor（`missing DB
  anchor`）、替换 manifest（`!= recomputed`）、错序（`prev_hash mismatch`）、
  重复 seq（`duplicate audit sequence`）、KMS 轮换旧 kid（`not in the trusted
  set`）、WORM 不可用（`TAMPER` + 原因）。exit 0=intact / 1=TAMPER /
  2=DB 不存在。
- 仅配置 DB 而无高危事件时输出 note 而非误报；未指定 `--worm-dir` 时不强检
  WORM（显式告知未检查）。

## 后果

- 优点：链头证据迁移到攻击者写域之外；高危变更与证据原子持久化，审计丢失时
  变更不可见；三类故障（本地重算、锚点删除/替换、密钥轮换/存储不可用）均有
  可机器判定的行为与测试（`tests/test_audit_anchors.py` 8 例）。
- 代价：高危写入多一次链头读 + frontier INSERT（同事务内，SQLite 本地开销
  可忽略）；`trusted_kids` 需要在 KMS 轮换 runbook 中同步维护；WORM 存储与
  签名密钥是部署方新增的运维资产。
- 未闭环（Gate B 待办）：WORM 真实备份恢复演练（ROADMAP §6 Gate B）。

## 相关

`docs/adr/0008-m0-security-hardening.md`（既有审计与 fail-closed 语义）、
`docs/adr/0009-credential-lifecycle.md`（凭据发行/吊销即高危事件）、
`docs/DISASTER_RECOVERY.md`（§3 恢复后校验链与证据）、`docs/OPERATIONS.md`
（审计证据运维章节）、`ROADMAP_2_X.md` §41.3。