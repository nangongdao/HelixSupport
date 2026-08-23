# Disaster Recovery (Phase 30.4)

## RTO / RPO 声明

| 级别 | 目标 | 说明 |
|------|------|------|
| **RTO** | ≤ 30 分钟 | 从故障到恢复服务(单区域/单实例场景) |
| **RPO** | ≤ 15 分钟 | 可接受的最大数据丢失窗口(备份/复制周期) |

- 单实例 SQLite:恢复 = 恢复最近备份(`scripts/restore.py`,校验 + 原子替换),RTO 取决于备份可用性。
- 多实例 PostgreSQL:恢复 = 数据库恢复 + 实例拉起,RPO 取决于 PITR 备份频率。

## 备份流程

1. **SQLite**:`python scripts/backup.py --out backups/$(date +%F).db.gz`(在线备份 API,一致性快照 + SHA-256 清单)。
2. **PostgreSQL**:部署方配置加密自动备份 + PITR(weekly full + daily incremental + WAL 归档)。
3. **跨区**:备份产物复制到独立区域/对象存储(离站),保留 ≥ 30 天。

## 恢复流程

1. 运行 `python scripts/restore.py --in <backup>.db.gz`(校验清单 + `PRAGMA integrity_check` + 原子替换)。
2. 启动实例,验证 `/health/ready` 200。
3. 运行 `python scripts/verify_audit_chain.py --db <restored.db> --worm-dir <worm_dir> --trusted-kids <kid[,kid...]>` 校验审计链与三证据:
   - 重算本地全链(热表 + `audit_archives` 冷档合并),任一历史行被重算即报 `TAMPER`;
   - `audit_anchors` 每个 frontier tip 的 `seq/hash` 与重算链一致,链头不得超前于证据;
   - 每个 WORM claim 的 Ed25519 签名、`kid` 白名单、环境与链一致性(校验器对 WORM
     缺失路径"创建于首次导出、未配置即可跳过"给出明确 note,不会误报)。
   exit 0=intact / 1=TAMPER / 2=DB 不存在。**恢复后未通过校验的证据不能作为合规证据**
   (Phase 41.3 / SEC-005,部署约定见 `docs/SECURITY_MODEL.md` §Phase 41.3 与
   `docs/adr/0011-audit-external-anchoring.md`)。
4. 抽样验证最近会话/消息可读。

## 故障演练

- `tests/test_drills.py`:备份一致性(写入负载下)、恢复往返、篡改备份拒绝;PG 演练由 `HELIX_PG_INTEGRATION=1` 门控。
- 建议季度演练:注入数据库损坏 → 按 runbook 恢复 → 记录实际 RTO/RPO。

## 合规证据包

- **审计归档导出**:`GET /api/audit-events`(`metrics:read`)用于热数据分页导出;
  retention 会先把过期行写入可校验的 `audit_archives` 冷归档,再物理清理热表。
  通过 `GET /api/audit-archives/{archive_id}` 取回完整 manifest,并运行
  `scripts/verify_audit_chain.py` 合并冷/热行校验链完整性后写入受控备份存储;
  校验器同时拒绝 manifest 摘要/边界不一致与重复事件 id/seq。数据主体删除不
  改写该证据链,审计数据仅由独立保留策略到期归档。
- **外部证据核对(Phase 41.3 / SEC-005)**:除本地链外,证据包还必须包含两样外部证据——
  `audit_anchors` 表的高危 frontier tips(migration 31,与高危变更同事务写入)与 WORM /
  object-lock 存储上的 Ed25519 签名链头 claim(`--worm-dir` 指定)。恢复后用
  `verify_audit_chain.py` 一次核对三份证据;WORM 对象只写一次,**备份恢复中必须整体
  回拷到对象存储,不要原地重写已存在的 claim id**(`DiskWormStore` journal 会把对象丢失
  判为 tamper,见 `docs/adr/0011-audit-external-anchoring.md`)。KMS 密钥轮换后,
  归档保留历史 claim 与 `trusted_kids` 白名单即可跨轮换验证。
- **数据区域配置**:部署方通过环境/网络配置指定数据驻留区域(应用无内置区域配置,由部署拓扑保证)。
- **安全评审清单**:`docs/SECURITY_MODEL.md`(威胁模型 + 未闭环项)、`SECURITY.md`(报告渠道/SLA)。
- **SBOM**:`artifacts/sbom.json` 随发布产出,供应链可追溯。

## 相关

`docs/OPERATIONS.md`(日常运维 + 备份/恢复章节)、`scripts/backup.py`、`scripts/restore.py`、`tests/test_drills.py`、`docs/SLO.md`。
