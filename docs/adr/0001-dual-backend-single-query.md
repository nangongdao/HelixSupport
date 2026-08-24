# ADR-001: 单查询实现双后端（SQLite / PostgreSQL）

- 状态：已接受（自 1.0.0 生效）
- 日期：2026-08-14（补记，对应既有实现）

## 背景

部署需要同时支持单节点 SQLite（零依赖、本地可跑）与生产 PostgreSQL。若为两个后端各写一套查询，会复制 100+ 方法、SQL 漂移、行为不一致。

## 决策

`Database` 类持有全部领域查询（连接/事务在 `connect()`），SQL 使用 SQLite 方言；`app/postgres_db.py` 的 `PostgresDatabase(Database)` 继承全部方法，仅通过 `pg_dialect.py`/`pg_compat.py` 翻译占位符（`?` → `%s`）与少量 PG 差异。PG 子类不重写领域逻辑。

## 后果

- 优点：单一实现、行为一致、测试一次覆盖两端（PG 集成测试由 `HELIX_PG_INTEGRATION=1` 开关）。
- 代价：SQLite 是方言基线，PG 特有的能力（如 `ON CONFLICT` 语法差异）需在方言层兼容；复杂查询受两端交集约束。
- 2026-08-14 结构治理：`Database` 拆为 9 个域 mixin（`app/db/`），PG 子类继承关系不变。

## 相关

[ADR-002](0002-sse-seq-streaming.md)、[ADR-006](0006-backend-structure.md)
