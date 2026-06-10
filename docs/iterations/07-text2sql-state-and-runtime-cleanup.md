# Text2SQL Typed State 与运行时清理

## 背景

本轮优化从 Text2SQL 的 `_activate_domain_context()` 开始。原实现把 `active_domain`、`active_table`、`active_text_fields`、`database_backend` 等状态散写到 `run_ctx.cache`，导致工具之间的隐式依赖不清晰，也让测试夹具继续依赖裸 key。

目标是把“run 内隔离状态”沉淀为框架通用能力，同时让 Text2SQL 自己管理 domain/schema/backend 这类业务状态。

## 主要改动

- 框架新增 namespaced typed state：`RuntimeContext.get_typed_state(namespace, factory)`，并通过 `SubagentContext.typed_state(...)` 暴露给 subagent。
- child run 不继承父 run 的 `_typed_state`，保证 worker 状态按 run 隔离。
- Text2SQL 新增 `Text2SQLState` 和 `Text2SQLRunStateManager`，统一管理 backend、active domain、table、schema、columns、text fields 和 field descriptions。
- `extension.py` 不再直接读写 `run_ctx.cache["active_*"]` 或 `database_backend`，工具只通过 state manager 获取当前运行态。
- SQL generation 的 dialect 改为由调用方显式传入，不再从 cache 里读取 backend。

## 运行时收口

- Text2SQL runtime 移除 CSV backend 模式，只接受 SQLite：`TEXT2SQL_BACKEND=sqlite` + `TEXT2SQL_DATABASE_URL`。
- CSV 仍保留为离线准备输入：`agentweave_prepare/text2sql.py` 从 `data/examples/text2sql/<table>.csv` 生成 `.agentweave/text2sql.sqlite`。
- `TEXT2SQL_TABLES_JSON` 不再是 runtime 配置。

## 策略收口

- 移除 `TEXT2SQL_STRICT_SCHEMA_VALIDATION` 环境变量。
- schema 白名单校验改为读取 subagent manifest policy：`policies.db.require_schema_validation`，默认开启。
- `generate_readonly_sql` 和 `execute_sql` 使用同一个 policy 判断是否校验，避免生成阶段和执行阶段策略不一致。

## 兼容与测试

本轮接受内部不兼容，直接更新测试和工具夹具：

- Text2SQL 测试不再通过裸 `state={"database_backend": ...}` 或 `active_*` key 注入运行态。
- Text2SQL 工具测试改用临时 SQLite 文件，而不是 runtime CSV backend。
- 新增/更新 typed state 测试，覆盖同 namespace 复用、namespace 隔离和 child run 隔离。

已验证：

```bash
PYTHONPATH=. uv run pytest tests/test_context.py tests/test_text2sql_tools.py tests/test_db_backend.py tests/test_text2sql_domain_catalog.py tests/test_subagent_runner.py tests/test_subagent_boundaries.py tests/test_public_imports.py
```

## 后续关注

- Text2SQL 的 `domain_catalog.yaml` 是否需要进一步抽象为生产 schema registry provider。
- SQL generation 是否需要 structured output / no-thinking 路径，减少 SQL 提取和校验失败。
- 框架 typed state 是否需要提供调试视图；默认不应把 subagent-local state 暴露给 Orchestrator。
