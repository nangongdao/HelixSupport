# Release Checklist (Phase 30.6)

每次发布(次版本或补丁)按此清单执行并留痕(在 PR 描述或发布说明中逐项打勾)。

## 前置

- [ ] `CHANGELOG.md` 新增版本条目,记录所有 Added/Changed/Fixed(含 API 变更,遵循 `docs/API_POLICY.md`)
- [ ] `docs/API_POLICY.md` 检查:无未走弃用流程的破坏性变更
- [ ] ADR:本版本无未记录的新架构决策(`docs/adr/`)

## 代码门禁(CI 全绿)

- [ ] `python -m ruff format --check app tests scripts`
- [ ] `python -m ruff check app tests scripts`
- [ ] `python -m pyright app tests scripts`
- [ ] `python -m pytest tests/`(全量)
- [ ] coverage ≥ 80%(`python -m coverage report --fail-under=80`)
- [ ] `python scripts/openapi_snapshot.py`(契约快照比对,破坏性变更需显式重生成)
- [ ] `python scripts/frontend_gate.py`(前端模块语法/行数/单测)
- [ ] `python -m pip check` + `python -m pip_audit .` +
  `python -m pip_audit -r requirements.lock`(依赖一致 + 项目/部署锁双重漏洞扫描)
- [ ] `python scripts/generate_sbom.py`(SBOM 产出)
- [ ] 无头浏览器冒烟(`tests/ui_smoke.py`)

## 迁移演练

- [ ] 迁移链校验:`tests/test_migrations.py`(连续性/重复/空洞)
- [ ] 从上一版本数据库快照跑迁移成功
- [ ] 迁移回滚路径验证(失败恢复)

## 运维与发布

- [ ] 版本号切换(`app/main.py` APP_VERSION + `pyproject.toml` + README)
- [ ] `docs/OPERATIONS.md`/`docs/DEGRADATION.md` 与本版本行为一致
- [ ] `GET /api/admin/diagnostics` 输出正常
- [ ] 备份/恢复演练通过(`tests/test_drills.py`)
- [ ] 发布负责人、on-call owner、SLA/保留/runbook 审批
- [ ] 打 tag(`vX.Y.Z`)并记录发布说明
