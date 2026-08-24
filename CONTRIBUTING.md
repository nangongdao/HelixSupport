# Contributing

## 工作流

1. 从 `main` 开分支；小步提交，提交信息说明"改了什么 + 为什么"。
2. 修改后运行全部门禁（见下），全绿才可合入。
3. **架构决策先写 ADR**：任何影响跨模块契约、部署形态、数据模型或向后兼容的决策，先写 `docs/adr/` 记录（模板 `docs/adr/0000-template.md`），合入时一并提交。
4. 合入 `main` 前更新 `CHANGELOG.md`（按 `docs/API_POLICY.md` 记录 API 变更）。

## 门禁（合并前必须全绿）

```bash
python -m ruff format app/ tests/ scripts/
python -m ruff check app/ tests/ scripts/
python -m pyright app/ tests/ scripts/
python -m pytest tests/ -q
python scripts/openapi_snapshot.py        # OpenAPI 契约快照比对
python scripts/frontend_gate.py           # 前端模块语法/行数/单测
pip check
```

- 破坏性 API 变更（删字段/端点/改类型）会触发 `openapi_snapshot.py` 红灯；需显式重新生成快照并在 changelog 说明。
- 前端改动需通过 `frontend_gate.py`（node --check + 模块 ≤400 行 + ≥30 单测）与无头浏览器冒烟。

## 结构约定（Phase 27）

- **数据库层**：领域方法放 `app/db/<domain>.py` 的 mixin（core/core_schema/tenancy/conversations/conversations_query/messages/jobs/knowledge/audit），`Database` 组合它们；新领域查询先找对应 mixin，不在 `database.py` 单文件里加。
- **API 层**：路由放 `app/routers/<domain>.py` 的 `build_router(deps)` 工厂（system/conversations/knowledge/admin/auth/quality/widget）；新增依赖在 `app/routers/common.py` 的 `RouteDeps` 扩展。
- **迁移**：schema 变更在 `app/migrations.py` 追加新版本迁移（`@migration` 装饰器），不修改已发布迁移。

## 测试约定

- 后端：pytest（`tests/`），新增功能带单测/集成测试；幂等、租户隔离、审计是硬要求。
- 前端：纯逻辑模块用 Node `node:test`（`tests/frontend/`）。
- golden set（`golden/set.json`）是路由/提示词/模型变更的回归门禁，不允许回退。
