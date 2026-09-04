# Runbook: 1.4 Release(非作者执行)

> 目标读者:未参与 1.4(Secure Operations / Phase 41)实现的发布工程师。
> 前置:已通过 M0 Gate A;CI 全绿;staging 可写;具备受控部署凭据。
> 发布负责人通读本 runbook 后按顺序执行;每步留痕(PR/issue 打勾)。

## 0. 前置

- [ ] 工作树与 tag 一致:`git describe --tags`(或核对 `APP_VERSION`)。
- [ ] `CHANGELOG.md` 已新增 1.4.0 条目;`docs/API_POLICY.md` 无未走弃用的破坏变更。
- [ ] `pip freeze`/`requirements.lock` 已更新,`requirements.lock` 有 pin。
- [ ] 检查发布工具版本一致(静态读取,不导入应用、无启动副作用):
  ```bash
  python - <<'PY'
  import ast
  from pathlib import Path
  tree = ast.parse(Path("app/main.py").read_text(encoding="utf-8"))
  for node in tree.body:
      if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "APP_VERSION" for t in node.targets):
          print(node.value.value)
          break
  PY
  ```

## 1. CI 门禁复核(对应 RELEASE_CHECKLIST.md)

```bash
python -m pytest tests/                 # 全量通过
python -m ruff format --check app tests scripts
python -m ruff check app tests scripts
python -m pyright app tests scripts
python -m coverage report --fail-under=80
python -m pip check && python -m pip_audit -r requirements.lock
python scripts/openapi_snapshot.py      # 契约快照比对
python scripts/frontend_gate.py
python scripts/generate_sbom.py         # 产出 artifacts/sbom.json
python scripts/release_manifest.py --build --out artifacts/release-manifest.json
python scripts/threat_model_gate.py --release 1.4.0   # delta 缺失/占位 owner → exit 1
```

## 2. 供应链证据

1. **SBOM**:`artifacts/sbom.json` 已生成(Step 1),与 `requirements.lock` 的 pin 一致。
2. **Release manifest**:`artifacts/release-manifest.json` 已 build,内容含
   base-image 与 CI 依赖 pin;用 `--verify` 复核:
   ```bash
   python scripts/release_manifest.py --verify --out artifacts/release-manifest.json
   ```
3. **漏洞扫描**:`vuln_review.py` 在 CI `supply-chain` job 中运行;确认无超期
   Critical/High 例外(`supplychain/vulnerability-exceptions.json` 无到期未续)。
4. **secret scan**:`scripts/scan_secrets.py` 通过(仓库无凭据痕迹)。

## 3. AI 安全基线(Phase 41.5)

1. 运行对抗集与 golden(仅 CI 执行亦可):
   ```bash
   python scripts/evaluate_adversarial.py --worm-dir <worm> --active-metrics <metrics>
   python scripts/evaluate.py --golden <golden.json> [--worm-dir <worm>]
   ```
2. 检查报告:24/24 对抗、golden 集 100%,`EvalReportStore` 报告已写入 WORM/不可变存储。
3. 模型/prompt 晋级需满足 `decide_promotion` 五条件(证据在报告中可追溯)。

## 4. 演练与关键安全操作

1. **凭据轮换演练**(有自动化脚本,须至少执行一次真实运行):
   ```bash
   python scripts/run_rotation_drill.py          # → supplychain/rotation-drills.json 记 PASSED
   python scripts/run_restore_drill.py           # → supplychain/restore-drills.json 记 PASSED
   ```
2. **restore 验证**:恢复后用
   `python scripts/verify_audit_chain.py --db <restored> --worm-dir <worm> --trusted-kids <kid>`。
3. **threat-model 巡礼**:
   ```bash
   python scripts/threat_model_gate.py --check-today --drill-max-days 90
   ```
   要求 `security-drills.json` 最近 90 天内有一次 `report_intake`/`dependency_vuln`/
   `key_compromise`/`cross_tenant_alarm` 演练;没有则需先完成一次真实演练并记录。
4. **安全报告渠道**(SEC-007):部署方私有渠道端到端演练(发送测试报告→分级→关闭)。

## 5. 部署(单实例 → canary → 全量)

1. **备份**:`python scripts/backup.py --database data/support.db --output backups/`
   (SQLite);PG 部署用自动备份 + PITR。
2. **灰度**:staging 单租户 canary;`ENABLE_SESSION_AUTH` 保持关闭(或仅在已验证
   IdP 的 profile 开启)。
3. **发布**:部署新版本;`GET /health/ready` 200;`GET /api/admin/diagnostics` 正常。
4. **Redis**:multi profile 启动后验证队列 backend 为 PostgreSQL+Redis(非 SQLite)。

## 6. 发布后监控

- 30 分钟内观察:`/health/ready`、`/api/system/metrics`、错误率/延迟(burn-rate 告警)。
- 校验审计链完整:`verify_audit_chain.py` 对生产 DB exit 0。
- 观察 24h:无未豁免 Critical/High;无 `turn.budget_exceeded` 异常增长;无 DSR 误触达。

## 7. 回滚

- 回滚 = 恢复上一版本镜像 + 恢复备份(仅允许 SQLite 单实例或多实例 PG 的旧版本组合;
  **禁止 multi profile + SQLite 混跑**)。
- 恢复失败时回到 runbook §4-2 做 `restore.py --verify-only` 先验备份。

## 8. 完成判定

- [ ] §1-6 全部通过,失败项有 issue + owner + 补偿控制。
- [ ] `supplychain/threat-model-deltas.json` 已包含 1.4.0 的 delta(release/date/owner/
  approved_by/controls/verification_evidence 全字段)。
- [ ] `RotationDrill` / `RestoreDrill` 台账最近一次 `passed=true`。
- [ ] 说明:Gate B 项 2(digest/cosign/admission)与项 7(补丁发布演练)依赖真实
  registry 与签名 key,本 runbook 在真实环境补齐后勾选。