# ADR-006: 后端结构治理（database/mixin 与 main/router 拆分）

- 状态：已接受（Phase 27，1.2.0）
- 日期：2026-08-14

## 背景

`database.py`（3712 行、104 方法）与 `main.py`（2268 行、74 路由）单文件膨胀到新人难以安全添加代码；新增功能被迫堆进两大文件。Phase 27 目标是"偿还结构债，使新人可以在正确位置添加代码"，且验收要求**拆分前后测试零修改全绿**（纯结构变更）。

## 决策

1. **数据库层**：`Database` 拆为 9 个域 mixin（`app/db/`）：`core`（连接/缓存/性能）、`core_schema`（建表/FTS）、`tenancy`（租户/配额/成员）、`conversations`、`conversations_query`、`messages`、`jobs`、`knowledge`、`audit`。`app/database.py` 变成薄组合类，`PostgresDatabase(Database)` 继承关系不变。模块级辅助（`utc_now` 等）移至 `app/db/_util.py` 并由各 mixin 导入。
2. **API 层**：`create_app` 内的路由闭包按域拆为 `app/routers/` 工厂（`build_router(deps: RouteDeps) -> APIRouter`）：`system`、`conversations`、`knowledge`、`admin`、`auth`。`RouteDeps` dataclass 持有闭包依赖（settings/database/orchestrator/turn_worker/services/queue/oidc 等），main.py 组装一次并注入各 router。已有 `quality_routes`/`widget_routes` 保持。
3. **迁移框架**：`app/migrations.py` 保持单一迁移入口（`@migration` 装饰器 + `run_migrations`），新增迁移按版本号追加；`_ensure_column`/`_create_seq_trigger_if_table_exists` 容忍部分表缺失（旧测试库）。

## 后果

- 优点：文件行数全部 ≤800（main 714、conversations 775、db mixin 最大 665）；新领域查询/路由有明确归属；mixin/工厂模式可组合复用。
- 代价：mixin 跨成员引用（`self.connect` 等）pyright 无法静态解析，需文件级 `reportAttributeAccessIssue=false`；RouteDeps 集中依赖需在新增依赖时扩展。
- 验证：拆分前后 416 测试零修改全绿；ruff/pyright 干净。

## 相关

[ADR-001](0001-dual-backend-single-query.md)、[ADR-002](0002-sse-seq-streaming.md)
