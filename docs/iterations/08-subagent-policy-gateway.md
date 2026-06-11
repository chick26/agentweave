# Subagent Policy Gateway 与授权边界

## 背景

第 7 轮已经把 Text2SQL 的 run-scoped typed state、SQLite runtime 和 schema validation policy 收口。本轮继续推进 subagent 治理，把 manifest 中的 `capabilities` / `policies` 从描述字段提升为 runtime 可校验、可审计、可执行的契约入口。

目标链路：

```text
capability -> tool -> policy -> audit -> artifact access
```

## 主要改动

- `SubagentExtensionAPI.tool(...)` 新增 `capability`、`policy_path` 和 `audit_name`。
- extension 加载时校验工具声明的 capability 必须存在于 manifest，policy path 必须能在 manifest `policies` 中解析。
- 工具事件自动注入 `subagent`、`tool`、`capability`、`policy_path`、`policy_snapshot`、`audit_name` 和 `scoped`；subagent-local tool 必须显式声明 capability，未声明时 extension 加载失败。
- Text2SQL / RAG 的内置工具已显式绑定 capability 和 policy。
- Text2SQL `execute_sql` 统一从 `policies.db` 读取 `max_rows`、`sample_rows`、`timeout_seconds` 和 `require_schema_validation`。
- SQLite backend 新增 per-query timeout，超时会以标准工具错误返回。
- `BotRegistry` 新增生产环境默认授权保护：`AGENTWEAVE_ENV=production` 时默认不再自动生成 default-all bot，除非设置 `AGENTWEAVE_ALLOW_GENERATED_DEFAULT_BOT=1`。
- ArtifactStore artifact 写入时记录 `run_id`、`session_id` 和 `bot_id`，读取分页和 CSV 导出支持按 scope 校验。

## 取舍

- 本轮不做 chain / parallel / background / resume，也不恢复 per-subagent model override。
- Policy Gateway 先提供通用注册、校验和审计，不把 SQL/RAG 业务规则写进 runner。
- ArtifactStore 先绑定 `run_id/session_id/bot_id`；当前项目还没有 user/tenant 身份模型，后续接入后再扩展 scope。
- ArtifactStore 读取分页、metadata、CSV 和 raw rows 时必须提供 `run_id`、`session_id` 或 `bot_id` 至少一种 scope，裸 `result_id` 读取会被拒绝。

## 已验证

```bash
PYTHONPATH=. uv run pytest tests/test_subagent_runner.py tests/test_text2sql_tools.py tests/test_db_backend.py tests/test_bot_registry.py tests/test_artifact_store.py tests/test_server_service.py tests/test_subagent_boundaries.py tests/test_public_imports.py
```

结果：81 passed。

## 后续关注

- 第 9 轮可继续推进 chain / parallel 编排，并复用本轮的 capability/policy audit metadata。
- ArtifactStore 后续接入真实用户体系时，需要把 scope 扩展到 `user_id` / `tenant_id`。
- 外部数据库或内部系统 gateway 接入时，应优先复用 `api.tool(..., capability, policy_path)` 约束，不让 extension 各自散写安全逻辑。
